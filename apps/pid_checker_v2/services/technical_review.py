"""Deterministic technical review of linked P&ID extraction evidence."""
import hashlib
import json

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.rbac.approval_eligibility import (approval_access, has_business_position,
                                            require_approval)
from apps.rbac.models import AuditLog
from apps.rbac.action_policy import module_action_allowed
from apps.pid_checker_v2.legend_defaults import SECTION_LINE_LIST, get_default_template
from apps.pid_checker_v2.models import (PidCheckerV2LineListUpload,
                                        PidEngineeringEvidenceLink, PidEvidenceReviewEvent)
from apps.pid_checker_v2.services.legend_engine import compile_legend
from apps.pid_checker_v2.services.legend_validator import validate_tags
from apps.pid_checker_v2.services.line_list_cross_check import cross_check
from apps.pid_checker_v2.services.line_list_provenance import (line_list_lookup,
                                                               require_project_master)
from apps.pid_checker_v2.services.engineering_evidence import project_access


REVIEW_POSITIONS = ('hod_process', 'engineering_manager', 'manager_engineering')


class ReviewConflict(APIException):
    status_code = 409
    default_detail = 'Review evidence or state changed. Validate again.'


def _locked_link(extraction_id):
    link = PidEngineeringEvidenceLink.objects.select_for_update(of=('self',)).select_related(
        'project', 'extraction', 'work_item', 'revision__document', 'revision__chain',
        'line_list', 'reviewer',
    ).filter(extraction__extraction_id=extraction_id).first()
    if link is None:
        from rest_framework.exceptions import NotFound
        raise NotFound('Engineering evidence link not found.')
    return link


def _write_actor(user, link):
    if not project_access(user, link.project, write=True) or link.extraction.created_by_id != user.pk:
        raise PermissionDenied('Only the extraction creator with project write access may submit evidence.')
    if not module_action_allowed(user, 'crs_documents', 'read'):
        raise PermissionDenied('CRS document read access is required.')


def _reviewer_eligible(user, link):
    return bool(user and user.is_active and user.pk == link.work_item.reviewer_id
                and project_access(user, link.project)
                and module_action_allowed(user, 'crs_documents', 'read')
                and approval_access(user, 'pid_analysis')
                and has_business_position(user, REVIEW_POSITIONS))


def _require_reviewer(user, link):
    require_approval(user, 'pid_analysis', assigned=user.pk == link.reviewer_id
                     and user.pk == link.work_item.reviewer_id
                     and project_access(user, link.project)
                     and module_action_allowed(user, 'crs_documents', 'read')
                     and has_business_position(user, REVIEW_POSITIONS),
                     current=link.review_state == 'under_review')
    if user.pk in (link.submitted_by_id, link.extraction.created_by_id, link.work_item.owner_id):
        raise PermissionDenied('Technical reviewers must be independent of the creator and work owner.')


def _fingerprint(link):
    # Lock parents and evidence rows until the transition commits. The same
    # source facts are hashed at validation, submission, and decision time.
    from apps.crs.models import CRSDocument
    from apps.crs.revision_models import CRSRevision, CRSRevisionChain
    from apps.project_control.execution_models import EPCWorkItem
    from apps.pid_checker_v2.models import (PidCheckerV2Extraction, PidCheckerV2LineTag,
                                            PidCheckerV2LineListRow, PidLineListProvenance)
    extraction = PidCheckerV2Extraction.objects.select_for_update().get(pk=link.extraction_id)
    work = EPCWorkItem.objects.select_for_update().get(pk=link.work_item_id)
    revision = CRSRevision.objects.select_for_update().get(pk=link.revision_id)
    document = CRSDocument.objects.select_for_update().get(pk=revision.document_id)
    chain = CRSRevisionChain.objects.select_for_update().get(pk=revision.chain_id)
    line_list = PidCheckerV2LineListUpload.objects.select_for_update().get(pk=link.line_list_id)
    tags = list(PidCheckerV2LineTag.objects.select_for_update().filter(
        extraction=extraction).order_by('pk').values('tag', 'size', 'service', 'spec', 'serial'))
    rows = list(PidCheckerV2LineListRow.objects.select_for_update().filter(
        upload=line_list).order_by('pk').values('tag', 'size', 'service_code', 'spec', 'serial', 'excel_row'))
    master_facts = None
    provenance = PidLineListProvenance.objects.select_for_update().filter(line_list=line_list).first()
    if provenance:
        master_revision = CRSRevision.objects.select_for_update().get(pk=provenance.revision_id)
        master_document = CRSDocument.objects.select_for_update().get(pk=master_revision.document_id)
        master_chain = CRSRevisionChain.objects.select_for_update().get(pk=master_revision.chain_id)
        master_facts = {
            'project_id': provenance.project_id,
            'provenance_updated_at': provenance.updated_at.isoformat(),
            'revision_id': master_revision.pk, 'revision_status': master_revision.status,
            'revision_number': master_revision.revision_number,
            'revision_updated_at': master_revision.updated_at.isoformat(),
            'document_id': master_document.pk,
            'document_updated_at': master_document.updated_at.isoformat(),
            'chain_id': master_chain.pk, 'chain_status': master_chain.status,
            'chain_current_revision': master_chain.current_revision_number,
            'chain_updated_at': master_chain.updated_at.isoformat(),
        }
    source_file_hash = None
    if document.pdf_file:
        try:
            hasher = hashlib.sha256()
            with document.pdf_file.open('rb') as source:
                for chunk in source.chunks():
                    hasher.update(chunk)
            source_file_hash = hasher.hexdigest()
        except Exception:
            source_file_hash = None
    facts = {
        'project_id': link.project_id, 'work_item_id': work.pk, 'work_updated_at': work.updated_at.isoformat(),
        'work_status': work.status, 'reviewer_id': work.reviewer_id,
        'extraction_id': str(extraction.extraction_id), 'source_sha256': extraction.file_sha256,
        'tag_count': extraction.tag_count, 'tags': tags,
        'line_list_id': line_list.pk, 'line_list_updated_at': line_list.updated_at.isoformat(),
        'line_list_active': line_list.is_active, 'line_list_rows': rows,
        'revision_id': revision.pk, 'revision_updated_at': revision.updated_at.isoformat(),
        'revision_label': revision.revision_label, 'revision_status': revision.status,
        'document_id': document.pk, 'document_updated_at': document.updated_at.isoformat(),
        'document_file': document.pdf_file.name,
        'document_file_sha256': source_file_hash,
        'chain_id': chain.pk, 'chain_updated_at': chain.updated_at.isoformat(), 'chain_status': chain.status,
        'line_list_provenance': master_facts,
    }
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode('utf-8')).hexdigest()
    return digest, tags, rows, line_list, source_file_hash


def _event(link, actor, action, note='', details=None):
    PidEvidenceReviewEvent.objects.create(
        link=link, actor=actor, action=action, review_round=link.review_round,
        note=note, fingerprint=link.evidence_fingerprint, details=details or {},
    )
    AuditLog.objects.create(
        user=actor, user_email=actor.email, action='update',
        resource_type='PidEvidenceTechnicalReview', resource_id=link.extraction.extraction_id,
        resource_repr=f'P&ID extraction {link.extraction.extraction_id}',
        changes={'review_state': link.review_state, 'action': action},
        metadata={'link_id': link.pk, 'review_round': link.review_round,
                  'fingerprint': link.evidence_fingerprint, 'revision_id': link.revision_id},
    )


@transaction.atomic
def review_payload(link):
    current = False
    if link.line_list_id and link.evidence_fingerprint:
        current = _fingerprint(link)[0] == link.evidence_fingerprint
    return {
        'extraction_id': str(link.extraction.extraction_id), 'project_id': link.project_id,
        'work_item_id': link.work_item_id, 'revision_id': link.revision_id,
        'crs_document_id': link.revision.document_id, 'review_state': link.review_state,
        'review_round': link.review_round, 'reviewer_id': link.reviewer_id,
        'submitted_by_id': link.submitted_by_id, 'submitted_at': link.submitted_at,
        'decided_by_id': link.decided_by_id, 'decided_at': link.decided_at,
        'evidence_fingerprint': link.evidence_fingerprint,
        'evidence_current': current, 'validation': link.validation_snapshot,
        'comments': [{'action': e.action, 'actor_id': e.actor_id, 'note': e.note,
                      'review_round': e.review_round, 'created_at': e.created_at}
                     for e in link.review_events.order_by('pk')],
    }


@transaction.atomic
def validate(link_id, *, user, line_list_id):
    link = _locked_link(link_id)
    _write_actor(user, link)
    if link.review_state not in ('draft', 'validated', 'returned'):
        raise ReviewConflict('Only draft or returned evidence can be validated.')
    line_list = PidCheckerV2LineListUpload.objects.select_for_update(of=('self',)).select_related('owner_project').filter(
        created_by=user, is_active=True, **line_list_lookup(line_list_id),
    ).first()
    if line_list is None or (line_list.owner_project_id and line_list.owner_project.created_by_id != user.pk):
        raise ValidationError({'line_list_id': 'An active, owned line list is required.'})
    provenance = require_project_master(line_list, link.project)
    link.line_list = line_list
    digest, tags, rows, _, source_file_hash = _fingerprint(link)
    legend = compile_legend(get_default_template(SECTION_LINE_LIST)['definition'])
    legend_result = validate_tags(tags, legend, use_ai=False)
    cross_result = cross_check(tags, rows, use_ai=False)
    cross_result.pop('token_usage', None)  # No AI calls; avoid Decimal in JSONField.
    summary = cross_result['summary']
    passed = bool(tags and rows and line_list.is_active and source_file_hash
                  and legend_result['summary']['errors'] == 0
                  and legend_result['summary']['warnings'] == 0
                  and summary['missing_on_pid'] == 0 and summary['extra_on_pid'] == 0
                  and summary['mismatch'] == 0 and summary['match'] == len(tags) == len(rows))
    snapshot = {'passed': passed, 'legend': legend_result, 'line_list_cross_check': cross_result,
                'line_list_id': line_list.pk, 'revision_id': link.revision_id,
                'crs_document_id': link.revision.document_id,
                'source_file_sha256': source_file_hash,
                'line_list_provenance': {
                    'project_id': provenance.project_id,
                    'revision_id': provenance.revision_id,
                    'crs_document_id': provenance.revision.document_id,
                    'crs_chain_id': provenance.revision.chain_id,
                    'revision_status': provenance.revision.status,
                    'chain_status': provenance.revision.chain.status,
                }}
    if (link.review_state == 'validated' and link.evidence_fingerprint == digest
            and link.validation_snapshot == snapshot):
        return link
    link.evidence_fingerprint = digest
    link.validation_snapshot = snapshot
    link.review_state = 'validated' if passed else 'draft'
    link.save(update_fields=['line_list', 'evidence_fingerprint', 'validation_snapshot',
                             'review_state', 'updated_at'])
    _event(link, user, 'validated' if passed else 'validation_failed',
           details={'legend_summary': legend_result['summary'], 'cross_check_summary': summary})
    return link


@transaction.atomic
def submit(link_id, *, user):
    link = _locked_link(link_id)
    _write_actor(user, link)
    if link.review_state == 'under_review' and link.submitted_by_id == user.pk:
        if _fingerprint(link)[0] != link.evidence_fingerprint:
            raise ReviewConflict()
        return link
    if link.review_state != 'validated' or not link.validation_snapshot.get('passed'):
        raise ValidationError({'validation': 'Complete passing deterministic validation before review.'})
    if _fingerprint(link)[0] != link.evidence_fingerprint:
        raise ReviewConflict()
    reviewer = link.work_item.reviewer
    if reviewer.pk in (user.pk, link.work_item.owner_id) or not _reviewer_eligible(reviewer, link):
        raise PermissionDenied('The EPC reviewer lacks independent process-engineering approval authority.')
    link.review_state = 'under_review'
    link.reviewer = reviewer
    link.submitted_by = user
    link.submitted_at = timezone.now()
    link.decided_by = None
    link.decided_at = None
    link.review_round += 1
    link.save(update_fields=['review_state', 'reviewer', 'submitted_by', 'submitted_at',
                             'decided_by', 'decided_at', 'review_round', 'updated_at'])
    _event(link, user, 'submitted', details={'reviewer_id': reviewer.pk})
    return link


@transaction.atomic
def comment(link_id, *, user, note):
    link = _locked_link(link_id)
    _require_reviewer(user, link)
    note = str(note or '').strip()
    if not note:
        raise ValidationError({'note': 'A review comment is required.'})
    _event(link, user, 'commented', note)
    return link


@transaction.atomic
def decide(link_id, *, user, decision, note):
    link = _locked_link(link_id)
    decision = str(decision or '').strip().lower()
    note = str(note or '').strip()
    if decision not in ('approve', 'return', 'reject'):
        raise ValidationError({'decision': 'Choose approve, return, or reject.'})
    if not note:
        raise ValidationError({'note': 'Record the review findings or decision reason.'})
    target = {'approve': 'approved', 'return': 'returned', 'reject': 'rejected'}[decision]
    last = link.review_events.order_by('-pk').first()
    if link.review_state == target and last and last.action == decision and last.actor_id == user.pk and last.note == note:
        if (user.pk != link.reviewer_id or not _reviewer_eligible(user, link)
                or user.pk in (link.submitted_by_id, link.extraction.created_by_id,
                               link.work_item.owner_id)):
            raise PermissionDenied('Current reviewer authority and project access are required.')
        return link
    if link.review_state != 'under_review':
        raise ReviewConflict('This review round has already been decided or is not pending.')
    _require_reviewer(user, link)
    if decision == 'approve' and (not link.validation_snapshot.get('passed')
                                  or _fingerprint(link)[0] != link.evidence_fingerprint):
        raise ReviewConflict('Evidence changed after validation or a required check failed.')
    link.review_state = target
    link.decided_by = user
    link.decided_at = timezone.now()
    link.save(update_fields=['review_state', 'decided_by', 'decided_at', 'updated_at'])
    _event(link, user, decision, note)
    return link
