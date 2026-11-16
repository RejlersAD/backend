"""Explicit, transactional P&ID evidence linkage to the live CRS revision store."""
from django.db import transaction
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.core.project_models import Project
from apps.crs.revision_models import CRSRevision, CRSRevisionChain
from apps.planning_intelligence.access import can_access_enterprise_project
from apps.project_control.access import can_write_enterprise_project
from apps.project_control.execution_models import EPCWorkItem
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import AuditLog
from apps.pid_checker_v2.models import (CRSChainProjectBinding, PidCheckerV2Extraction,
                                        PidEngineeringEvidenceLink)


class LinkConflict(APIException):
    status_code = 409
    default_detail = 'The extraction or CRS chain is already linked to another scope.'


def project_access(user, project, *, write=False):
    return bool(user.is_authenticated and user.is_active and
                can_access_enterprise_project(user, project, write=write) and
                (not write or can_write_enterprise_project(user, project)))


def _integer(value, field):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError({field: 'A positive integer ID is required.'})
    if number < 1:
        raise ValidationError({field: 'A positive integer ID is required.'})
    return number


def payload(link):
    return {
        'id': link.pk,
        'extraction_id': str(link.extraction.extraction_id),
        'project_id': link.project_id,
        'work_item_id': link.work_item_id,
        'revision_id': link.revision_id,
        'crs_document_id': link.revision.document_id,
        'crs_chain_id': link.revision.chain_id,
        'revision_number': link.revision.revision_number,
        'revision_label': link.revision.revision_label,
        'linked_by_id': link.linked_by_id,
        'updated_by_id': link.updated_by_id,
        'created_at': link.created_at.isoformat(),
        'updated_at': link.updated_at.isoformat(),
    }


def _audit(user, link, action, before=None):
    AuditLog.objects.create(
        user=user, user_email=user.email, action=action,
        resource_type='PidEngineeringEvidenceLink',
        resource_id=link.extraction.extraction_id,
        resource_repr=f'P&ID extraction {link.extraction.extraction_id}',
        changes={'before': before, 'after': {
            'project_id': link.project_id, 'work_item_id': link.work_item_id,
            'revision_id': link.revision_id, 'crs_document_id': link.revision.document_id,
        }},
        metadata={'link_id': link.pk, 'crs_chain_id': link.revision.chain_id},
    )


@transaction.atomic
def associate(*, user, extraction_id, project_id, work_item_id, revision_id,
              allow_update=False):
    project = Project.objects.select_for_update().filter(
        pk=_integer(project_id, 'project_id'), is_deleted=False,
    ).first()
    if project is None:
        raise NotFound('Project not found.')
    if not project_access(user, project, write=True):
        raise PermissionDenied('Project write access is required.')
    if not module_action_allowed(user, 'crs_documents', 'read'):
        raise PermissionDenied('CRS document read access is required.')
    extraction = PidCheckerV2Extraction.objects.select_for_update(of=('self',)).select_related('project').filter(
        extraction_id=extraction_id,
    ).first()
    if extraction is None:
        raise NotFound('Extraction not found.')
    if extraction.created_by_id != user.pk or (extraction.project_id and
                                                extraction.project.created_by_id != user.pk):
        raise PermissionDenied('Extraction ownership is required.')
    work = EPCWorkItem.objects.filter(pk=_integer(work_item_id, 'work_item_id'),
                                      project=project, is_deleted=False, phase='engineering').first()
    if work is None:
        raise ValidationError({'work_item_id': 'Engineering work item must belong to this project.'})
    revision = CRSRevision.objects.select_related('chain', 'document').filter(
        pk=_integer(revision_id, 'revision_id'),
    ).first()
    if revision is None:
        raise NotFound('CRS revision not found.')
    CRSRevisionChain.objects.select_for_update().get(pk=revision.chain_id)
    binding = CRSChainProjectBinding.objects.select_for_update().filter(chain_id=revision.chain_id).first()
    if binding and binding.project_id != project.pk:
        raise LinkConflict('CRS revision chain is bound to another project.')
    if binding is None:
        # The legacy CRS chain has only free-text project_name. Bind it only
        # through an explicit request by its creator or source uploader.
        if user.pk not in (revision.chain.created_by_id, revision.document.uploaded_by_id):
            raise PermissionDenied('CRS source ownership is required for first project binding.')
        binding = CRSChainProjectBinding.objects.create(
            chain=revision.chain, project=project, linked_by=user,
        )
    link = PidEngineeringEvidenceLink.objects.select_for_update(of=('self',)).select_related(
        'extraction', 'revision__document', 'revision__chain', 'project',
    ).filter(extraction=extraction).first()
    if link:
        if link.project_id != project.pk:
            raise LinkConflict('Extraction is bound to another project.')
        if link.work_item_id == work.pk and link.revision_id == revision.pk:
            return link, False
        if not allow_update:
            raise LinkConflict('Extraction already has a different evidence association.')
        if link.review_state in ('under_review', 'approved', 'rejected'):
            raise LinkConflict('Submitted or decided technical evidence cannot be relinked.')
        before = {'project_id': link.project_id, 'work_item_id': link.work_item_id,
                  'revision_id': link.revision_id, 'crs_document_id': link.revision.document_id}
        link.work_item = work
        link.revision = revision
        link.updated_by = user
        link.review_state = 'draft'
        link.validation_snapshot = {}
        link.evidence_fingerprint = ''
        link.save(update_fields=['work_item', 'revision', 'updated_by', 'review_state',
                                 'validation_snapshot', 'evidence_fingerprint', 'updated_at'])
        _audit(user, link, 'update', before)
        return link, False
    link = PidEngineeringEvidenceLink.objects.create(
        extraction=extraction, project=project, work_item=work, revision=revision,
        linked_by=user, updated_by=user,
    )
    _audit(user, link, 'create')
    return link, True
