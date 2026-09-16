"""Correct incomplete approval evidence against an unchanged original PR PDF."""

from copy import deepcopy
from datetime import date, datetime, time
import hashlib
import re

from botocore.exceptions import ClientError
from django.core.files.storage import default_storage
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from ..models import PurchaseRequisition
from .pr_pdf_semantics import approval_role
from .requisition_source_documents import SIGNED_PR_TYPE, requisition_source_key
from .requisition_workflow import RequisitionWorkflowService
from .signed_pr_pdf_import import _find_unique_active_issuer


class SourceApprovalConflict(APIException):
    status_code = 409
    default_detail = 'The recorded source approval changed. Refresh the recommendation and review it again.'


class SourceApprovalUnavailable(APIException):
    status_code = 503
    default_detail = 'The original PR PDF could not be checked. Please retry.'


def source_row_verified(row):
    return row.get('signature_verified') is True or (
        str(row.get('status') or '').strip().lower() == 'approved'
        and row.get('signature_verified') is not False
    )


def _source_row(row):
    return isinstance(row, dict) and row.get('external') is True and row.get('source') == SIGNED_PR_TYPE


def _name(row):
    return str(row.get('user_name') or '').strip()


def _validate_payload(payload):
    allowed = {'document_sha256', 'row_index', 'expected_row', 'approver_name', 'signature_verified', 'approval_date'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValidationError({'detail': 'Only the source approver name, signature confirmation, and date may be edited.'})
    digest = payload.get('document_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
        raise ValidationError({'document_sha256': 'A valid original PDF digest is required.'})
    index = payload.get('row_index')
    if type(index) is not int or index < 0:
        raise ValidationError({'row_index': 'Select a recorded source approval row.'})
    if not isinstance(payload.get('expected_row'), dict):
        raise ValidationError({'expected_row': 'The saved source row is required. Refresh the recommendation.'})
    name = payload.get('approver_name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200 or any(ord(char) < 32 for char in name):
        raise ValidationError({'approver_name': 'Enter the source approver name using at most 200 characters.'})
    confirmed = payload.get('signature_verified')
    if type(confirmed) is not bool:
        raise ValidationError({'signature_verified': 'Explicitly confirm whether the source PDF signature was verified.'})
    date_text = payload.get('approval_date', '')
    if not isinstance(date_text, str):
        raise ValidationError({'approval_date': 'Use YYYY-MM-DD for the source approval date.'})
    approved_on = None
    if date_text:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_text):
                raise ValueError
            approved_on = date.fromisoformat(date_text)
        except ValueError:
            raise ValidationError({'approval_date': 'Use a valid YYYY-MM-DD source approval date.'})
        if approved_on > timezone.localdate():
            raise ValidationError({'approval_date': 'The source approval date cannot be in the future.'})
    if confirmed and not approved_on:
        raise ValidationError({'approval_date': 'Enter the date shown with the verified source approval.'})
    if not confirmed and approved_on:
        raise ValidationError({'approval_date': 'Confirm the source signature before entering an approval date.'})
    return digest.lower(), index, name.strip(), confirmed, approved_on


def _check_original(pr, digest):
    """Resolve a record-bound attachment and verify its stored bytes, without OCR."""
    candidates = [
        (index, attachment, requisition_source_key(pr, attachment))
        for index, attachment in enumerate(pr.attachments or [])
        if isinstance(attachment, dict) and str(attachment.get('sha256') or '').lower() == digest
    ]
    candidate = next((item for item in candidates if item[2]), None)
    if candidate is None:
        raise NotFound('The original signed PR attachment is unavailable.')
    index, _attachment, key = candidate
    try:
        hasher, size, prefix = hashlib.sha256(), 0, b''
        with default_storage.open(key, 'rb') as original:
            while chunk := original.read(65536):
                prefix = (prefix + chunk)[:4] if len(prefix) < 4 else prefix
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise ValidationError({'document_sha256': 'The original PR PDF exceeds the supported review size.'})
                hasher.update(chunk)
    except FileNotFoundError:
        raise NotFound('The original signed PR PDF could not be found.')
    except ClientError as exc:
        if str((exc.response.get('Error') or {}).get('Code')) in {'NoSuchKey', 'NotFound', '404'}:
            raise NotFound('The original signed PR PDF could not be found.')
        raise SourceApprovalUnavailable() from exc
    except APIException:
        raise
    except Exception as exc:
        raise SourceApprovalUnavailable() from exc
    if prefix != b'%PDF' or hasher.hexdigest() != digest:
        raise SourceApprovalConflict('The original PDF no longer matches this approval evidence. Refresh the recommendation and review the source again.')
    return index


def _approved_at(row):
    value = row.get('approved_at')
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
    except (ValueError, TypeError):
        parsed = None
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


@transaction.atomic
def edit_requisition_source_approval(requisition_id, actor, payload):
    digest, index, name, confirmed, approved_on = _validate_payload(payload)
    pr = PurchaseRequisition.objects.select_for_update().get(pk=requisition_id)
    if str(pr.issued_by_id) != str(actor.pk) and not RequisitionWorkflowService._is_super_admin(actor):
        raise PermissionDenied('Only the requisition issuer may modify this requisition.')
    metadata = deepcopy(pr.price_remarks_data or {})
    verification = metadata.get('signed_document_verification') or {}
    if str(verification.get('document_sha256') or '').lower() != digest:
        raise SourceApprovalConflict('The original PDF selection changed. Refresh the recommendation before editing its approvals.')
    rows = verification.get('source_approval_rows')
    if not isinstance(rows, list) or index >= len(rows):
        raise SourceApprovalConflict()
    if not rows or not all(_source_row(row) for row in rows):
        raise ValidationError({'row_index': 'Only existing original PDF approval rows may be edited.'})
    if any(str(item.get('status') or '').strip().lower() in {'rejected', 'not_approved', 'declined', 'denied', 'cancelled'} for item in rows):
        raise ValidationError({'row_index': 'A rejected source decision must be resolved through the approval review process.'})
    row = rows[index]
    if row != payload['expected_row']:
        raise SourceApprovalConflict()
    if _name(row) and source_row_verified(row):
        raise SourceApprovalConflict('This source approval is already complete and cannot be changed. Refresh the recommendation.')
    role = approval_role(row.get('role_key') or row.get('role', ''))
    if not role:
        raise ValidationError({'row_index': 'This source role must be reconciled through the signed PDF review.'})
    if confirmed and source_row_verified(row):
        raise ValidationError({'signature_verified': 'This signature is already recorded. Save only its missing approver name.'})

    attachment_index = _check_original(pr, digest)
    before = deepcopy(row)
    signer = _find_unique_active_issuer(name)
    row.update(user_name=name, user_id=str(signer.pk) if signer else None)
    if confirmed:
        approved_at = timezone.make_aware(datetime.combine(approved_on, time(12)))
        row.update(status='approved', signature_verified=True, signature_source='manual', approved_at=approved_at.isoformat())
    complete = all(_name(item) and source_row_verified(item) for item in rows)
    if complete and pr.approval_workflow_config and not all(_source_row(item) for item in pr.approval_workflow_config):
        raise SourceApprovalConflict('An internal approval workflow already exists. Refresh the recommendation and reconcile the source evidence before replacing it.')

    evidence = metadata.setdefault('signed_approval_evidence', {})
    evidence.setdefault('reviewed_approver_names', {})[role] = name
    if confirmed:
        for key in ('signatures', 'manual_signature_overrides'):
            evidence.setdefault(key, {})[role] = True
        evidence.setdefault('signature_sources', {})[role] = 'manual'
    verification['source_approval_rows'] = rows
    verification['signed_off'] = complete
    metadata['signed_document_verification'] = verification
    now = timezone.now()
    metadata.setdefault('source_approval_reviews', []).append({
        'document_sha256': digest, 'row_index': index, 'role': row.get('role'),
        'reviewed_by_id': str(actor.pk), 'reviewed_by_name': actor.get_full_name() or actor.email,
        'reviewed_at': now.isoformat(), 'signature_verified': confirmed,
        'before': before, 'after': deepcopy(row),
    })
    fields = ['price_remarks_data', 'updated_at']
    existing_workflow = pr.approval_workflow_config or []
    if not complete and existing_workflow and all(_source_row(item) for item in existing_workflow):
        # Some historical imports already have a partial external history.
        # Update its exact corresponding source row without creating a new
        # route, changing another document's row, or touching internal stages.
        matches = [position for position, item in enumerate(existing_workflow) if item == before]
        if len(matches) == 1:
            pr.approval_workflow_config = deepcopy(existing_workflow)
            pr.approval_workflow_config[matches[0]] = deepcopy(row)
            fields.append('approval_workflow_config')
    if complete:
        if pr.status == 'draft':
            pr.status = 'approved'
            fields.append('status')
        pr.approval_workflow_config = deepcopy(rows)
        pr.current_approval_step = len(rows)
        pr.review_due_at = None
        pr.approved_by = _find_unique_active_issuer(_name(rows[-1]))
        recorded_dates = [value for item in rows if (value := _approved_at(item))]
        if confirmed or pr.approved_at is None:
            pr.approved_at = max(recorded_dates) if recorded_dates else pr.approved_at
        fields.extend(['approval_workflow_config', 'current_approval_step', 'review_due_at', 'approved_by', 'approved_at'])
        approval_fields = {
            'pm': ('pm_name', 'pm_signature', 'pm_approval_status', 'pm_approved_at'),
            'moe': ('eng_manager_name', 'eng_manager_signature', 'eng_manager_approval_status', 'eng_manager_approved_at'),
            'mop': ('manager_projects_name', 'manager_projects_signature', 'manager_projects_approval_status', 'manager_projects_approved_at'),
            'vp': ('vp_op_name', 'vp_op_signature', 'vp_op_approval_status', 'vp_op_approved_at'),
        }
        source_url = reverse('requisition-uploaded-document-content', kwargs={'pk': pr.pk, 'document_id': attachment_index})
        for item in rows:
            source_role = approval_role(item.get('role_key') or item.get('role', ''))
            if source_role not in approval_fields:
                continue
            user_field, signature_field, status_field, date_field = approval_fields[source_role]
            if source_role != role and getattr(pr, status_field) == 'approved':
                continue
            setattr(pr, user_field, _find_unique_active_issuer(_name(item)))
            # Existing signature/date evidence survives a missing-name edit.
            if not getattr(pr, signature_field):
                setattr(pr, signature_field, source_url)
            setattr(pr, status_field, 'approved')
            if confirmed or getattr(pr, date_field) is None:
                setattr(pr, date_field, _approved_at(item))
            fields.extend(approval_fields[source_role])
    pr.price_remarks_data = metadata
    pr.save(update_fields=list(dict.fromkeys(fields)))
    return pr
