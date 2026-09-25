"""Guarded saved-record source annotations, without reimporting commercial data."""

from copy import deepcopy

from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from ..models import PurchaseRequisition
from .pr_source_approval_review import REVIEW_KEY, prepare_source_approval_review
from .requisition_concurrency import RequisitionTimestampField, check_requisition_precondition
from .requisition_source_approvals import _check_original, SourceApprovalConflict
from .requisition_workflow import RequisitionWorkflowService


@transaction.atomic
def edit_requisition_source_review(requisition_id, actor, payload):
    allowed = {'document_sha256', 'expected_updated_at', 'source_approval_review', 'expected_source_approval_review'}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ValidationError({'detail': 'Supply the original document, current version and expected source review.'})
    try:
        RequisitionTimestampField().run_validation(payload['expected_updated_at'])
    except ValidationError as exc:
        raise ValidationError({'expected_updated_at': exc.detail}) from exc
    pr = PurchaseRequisition.objects.select_for_update().get(pk=requisition_id)
    if str(pr.issued_by_id) != str(actor.pk) and not RequisitionWorkflowService._is_super_admin(actor):
        raise PermissionDenied('Only the requisition issuer may modify this source review.')
    metadata = deepcopy(pr.price_remarks_data or {})
    digest = (metadata.get('signed_document_verification') or {}).get('document_sha256')
    if not digest or digest != payload['document_sha256']:
        raise SourceApprovalConflict('The original PDF selection changed. Refresh the recommendation.')
    _, envelope = prepare_source_approval_review(metadata, digest, actor, payload['source_approval_review'],
                                                payload['expected_source_approval_review'])
    evidence = metadata.get('signed_approval_evidence') or {}
    if envelope == evidence.get(REVIEW_KEY):
        return pr
    check_requisition_precondition(pr, payload['expected_updated_at'])
    _check_original(pr, digest)
    if envelope is not None:
        evidence[REVIEW_KEY] = envelope
    metadata['signed_approval_evidence'] = evidence
    pr.price_remarks_data = metadata
    pr.save(update_fields=['price_remarks_data', 'updated_at'])
    return pr
