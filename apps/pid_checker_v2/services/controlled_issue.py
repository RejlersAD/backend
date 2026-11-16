"""Issue exact approved P&ID evidence and register it with EPC Project Control."""
import hashlib
from django.db import transaction
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.pid_checker_v2.models import (PidControlledIssue, PidIssueProgressEvidence)
from apps.pid_checker_v2.services.engineering_evidence import _integer, project_access
from apps.pid_checker_v2.services.technical_review import (_fingerprint, _locked_link,
                                                             REVIEW_POSITIONS)
from apps.project_control.execution_models import EPCWorkEvent
from apps.project_control.services.execution import _locked as lock_work, review_evidence
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.approval_eligibility import has_business_position, require_approval
from apps.rbac.models import AuditLog


class IssueConflict(APIException):
    status_code = 409
    default_detail = 'Controlled issue prerequisites or source evidence changed.'


def _scope(user, link, *, write=False):
    if (not project_access(user, link.project, write=write)
            or not module_action_allowed(user, 'crs_documents', 'read')):
        raise PermissionDenied('Project and CRS document access are required.')


def _issue_authority(user, link):
    _scope(user, link, write=True)
    assigned = (has_business_position(user, REVIEW_POSITIONS)
                and module_action_allowed(user, 'crs_documents', 'approve'))
    require_approval(user, 'pid_analysis', assigned=assigned,
                     current=link.review_state == 'approved')
    if user.pk in (link.extraction.created_by_id, link.submitted_by_id,
                   link.decided_by_id, link.work_item.owner_id):
        raise PermissionDenied('The issue authority must be independent of author, reviewer, and work owner.')


def _crs_ready(link):
    revision = link.revision
    document = revision.document
    chain = revision.chain
    return bool(chain.status == 'approved' and revision.status == 'completed'
                and document.status == 'completed'
                and chain.current_revision_number == revision.revision_number
                and document.pending_comments == 0
                and revision.total_comments == revision.total_resolved_comments)


def issue_payload(issue):
    progress = PidIssueProgressEvidence.objects.filter(issue=issue).first()
    return {
        'id': issue.pk, 'extraction_id': str(issue.link.extraction.extraction_id),
        'project_id': issue.project_id, 'work_item_id': issue.work_item_id,
        'revision_id': issue.revision_id, 'crs_document_id': issue.revision.document_id,
        'issue_number': issue.issue_number, 'issue_revision': issue.issue_revision,
        'evidence_fingerprint': issue.evidence_fingerprint,
        'source_manifest': issue.source_manifest,
        'issued_by_id': issue.issued_by_id, 'issued_at': issue.issued_at,
        'progress_evidence': None if progress is None else {
            'id': progress.pk, 'reported_by_id': progress.reported_by_id,
            'reported_at': progress.reported_at, 'note': progress.note,
            'snapshot': progress.snapshot,
        },
    }


@transaction.atomic
def controlled_issue(extraction_id, *, user, revision_id, evidence_fingerprint):
    link = _locked_link(extraction_id)
    _issue_authority(user, link)
    if (_integer(revision_id, 'revision_id') != link.revision_id
            or evidence_fingerprint != link.evidence_fingerprint):
        raise IssueConflict('The exact reviewed CRS revision and evidence fingerprint are required.')
    existing = PidControlledIssue.objects.select_for_update().defer('source_bytes').filter(link=link).first()
    if existing:
        if _fingerprint(link)[0] != link.evidence_fingerprint or not _crs_ready(link):
            raise IssueConflict('The issued record is preserved, but its current source changed.')
        return existing, False
    approved_event = link.review_events.filter(
        action='approve', review_round=link.review_round,
        actor_id=link.decided_by_id, fingerprint=link.evidence_fingerprint,
    ).exists()
    if (not link.validation_snapshot.get('passed') or not link.decided_by_id
            or not link.decided_at or not approved_event):
        raise IssueConflict('Technical approval and passing validation are required.')
    digest, _, _, _, source_hash = _fingerprint(link)
    if digest != link.evidence_fingerprint or not source_hash:
        raise IssueConflict('Reviewed evidence or CRS source file changed.')
    if not _crs_ready(link):
        raise IssueConflict('The exact CRS chain, revision, and document must be approved/completed.')
    try:
        with link.revision.document.pdf_file.open('rb') as source:
            source_bytes = b''.join(source.chunks())
    except Exception:
        raise IssueConflict('The exact CRS source file is unavailable.')
    if hashlib.sha256(source_bytes).hexdigest() != source_hash:
        raise IssueConflict('The CRS source file changed while issuing.')
    issue_number = f'PID-{link.project_id}-{link.pk:06d}'
    manifest = {
        'extraction_id': str(link.extraction.extraction_id),
        'project_id': link.project_id, 'work_item_id': link.work_item_id,
        'revision_id': link.revision_id, 'crs_document_id': link.revision.document_id,
        'crs_chain_id': link.revision.chain_id,
        'review_round': link.review_round, 'technical_decided_by_id': link.decided_by_id,
        'technical_decided_at': link.decided_at.isoformat(),
        'evidence_fingerprint': digest, 'source_file_sha256': source_hash,
        'validation': link.validation_snapshot,
        'crs_chain_status': link.revision.chain.status,
        'crs_revision_status': link.revision.status,
        'crs_document_status': link.revision.document.status,
    }
    event = EPCWorkEvent.objects.create(
        work_item=link.work_item, action='pid_issued', actor=user,
        note=f'Controlled P&ID issue {issue_number}', payload=manifest,
    )
    issued = PidControlledIssue.objects.create(
        link=link, project=link.project, work_item=link.work_item,
        revision=link.revision, work_event=event,
        issue_number=issue_number, issue_revision=link.revision.revision_label,
        evidence_fingerprint=digest, source_bytes=source_bytes,
        source_manifest=manifest, issued_by=user,
    )
    AuditLog.objects.create(
        user=user, user_email=user.email, action='create',
        resource_type='PidControlledIssue', resource_id=issued.pk,
        resource_repr=issue_number, changes={'issue': manifest},
        metadata={'link_id': link.pk, 'work_event_id': event.pk},
    )
    return issued, True


@transaction.atomic
def register_progress_evidence(extraction_id, *, user, note):
    link = _locked_link(extraction_id)
    _scope(user, link, write=True)
    if not module_action_allowed(user, 'project_control', 'update'):
        raise PermissionDenied('Project Control update permission is required.')
    note = str(note or '').strip()
    if not note:
        raise ValidationError({'note': 'Record how the issue supports work progress.'})
    issue = PidControlledIssue.objects.select_for_update().defer('source_bytes').filter(link=link).first()
    if issue is None:
        raise IssueConflict('Controlled issue is required before progress evidence.')
    existing = PidIssueProgressEvidence.objects.select_for_update().filter(issue=issue).first()
    if existing:
        if existing.reported_by_id == user.pk and existing.note == note:
            return issue, False
        raise IssueConflict('Progress evidence was already registered for this issue.')
    work = lock_work(link.work_item)
    if work.project_id != link.project_id or work.pk != issue.work_item_id or work.status == 'accepted':
        raise IssueConflict('The linked EPC work item is no longer open in this project.')
    progress = None
    if work.activity_id:
        from apps.planning_intelligence.models import ActivityProgressUpdate
        progress = ActivityProgressUpdate.objects.filter(
            activity_id=work.activity_id, is_deleted=False,
        ).order_by('-data_date', '-pk').first()
    snapshot = {
        'issue_id': issue.pk, 'issue_number': issue.issue_number,
        'issue_revision': issue.issue_revision, 'work_item_id': work.pk,
        'work_status': work.status, 'activity_id': work.activity_id,
        'baseline_id': work.baseline_id,
        'baseline_checksum': work.baseline.checksum if work.baseline_id else None,
        'existing_progress_update_id': progress.pk if progress else None,
        'existing_physical_progress_pct': str(progress.physical_progress_pct) if progress else None,
        'epc_review_evidence': review_evidence(work),
        'progress_effect': 'evidence_only; earned progress remains with EPC acceptance',
    }
    event = EPCWorkEvent.objects.create(
        work_item=work, action='pid_progress', actor=user, note=note, payload=snapshot,
    )
    PidIssueProgressEvidence.objects.create(
        issue=issue, work_event=event, reported_by=user, note=note, snapshot=snapshot,
    )
    AuditLog.objects.create(
        user=user, user_email=user.email, action='create',
        resource_type='PidIssueProgressEvidence', resource_id=issue.pk,
        resource_repr=issue.issue_number, changes={'progress_evidence': snapshot},
        metadata={'work_item_id': work.pk, 'work_event_id': event.pk},
    )
    return issue, True
