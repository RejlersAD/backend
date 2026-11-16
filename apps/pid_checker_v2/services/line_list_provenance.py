"""Explicit canonical project and exact CRS master-source provenance for Line Lists."""
from uuid import UUID

from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.project_models import Project
from apps.crs.revision_models import CRSRevision, CRSRevisionChain
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import AuditLog
from apps.pid_checker_v2.models import (CRSChainProjectBinding, PidCheckerV2LineListUpload,
                                        PidEngineeringEvidenceLink, PidLineListProvenance)
from apps.pid_checker_v2.services.engineering_evidence import (LinkConflict, _integer,
                                                               project_access)

# Approved-master gates reuse the live CRS revision controls: the chain decision
# status, the per-revision completion status, and the chain's current revision.
APPROVED_CHAIN_STATUS = 'approved'
COMPLETED_REVISION_STATUS = 'completed'
LOCKED_REVIEW_STATES = ('under_review', 'approved')


def line_list_lookup(value, field='line_list_id'):
    """Resolve the public upload UUID or a legacy integer primary key."""
    if isinstance(value, bool):
        raise ValidationError({field: 'A Line List UUID or positive integer ID is required.'})
    if isinstance(value, UUID):
        return {'line_list_id': value}
    if isinstance(value, int) or (isinstance(value, str) and value.isdecimal()):
        number = int(value)
        if number > 0:
            return {'pk': number}
    elif isinstance(value, str):
        try:
            return {'line_list_id': UUID(value)}
        except ValueError:
            pass
    raise ValidationError({field: 'A Line List UUID or positive integer ID is required.'})


def master_state_error(revision):
    """Why this CRS revision is not an approved, current master source."""
    if revision.chain.status != APPROVED_CHAIN_STATUS:
        return 'The master Line List CRS revision chain is not approved.'
    if revision.status != COMPLETED_REVISION_STATUS:
        return 'The master Line List CRS revision is not completed.'
    if revision.chain.current_revision_number != revision.revision_number:
        return 'The master Line List CRS revision is stale or superseded.'
    return ''


def provenance_payload(provenance):
    revision = provenance.revision
    return {
        'id': provenance.pk,
        'line_list_id': str(provenance.line_list.line_list_id),
        'line_list_pk': provenance.line_list_id,
        'project_id': provenance.project_id,
        'revision_id': provenance.revision_id,
        'crs_document_id': revision.document_id,
        'crs_chain_id': revision.chain_id,
        'revision_number': revision.revision_number,
        'revision_label': revision.revision_label,
        'revision_status': revision.status,
        'chain_status': revision.chain.status,
        'linked_by_id': provenance.linked_by_id,
        'updated_by_id': provenance.updated_by_id,
        'created_at': provenance.created_at.isoformat(),
        'updated_at': provenance.updated_at.isoformat(),
    }


def _audit(user, provenance, action, before=None):
    AuditLog.objects.create(
        user=user, user_email=user.email, action=action,
        resource_type='PidLineListProvenance',
        resource_id=provenance.line_list.line_list_id,
        resource_repr=f'Master line list {provenance.line_list.line_list_id}',
        changes={'before': before, 'after': {
            'project_id': provenance.project_id, 'revision_id': provenance.revision_id,
            'crs_document_id': provenance.revision.document_id,
        }},
        metadata={'provenance_id': provenance.pk, 'line_list_pk': provenance.line_list_id,
                  'crs_chain_id': provenance.revision.chain_id},
    )


def locked_provenance(line_list):
    return PidLineListProvenance.objects.select_for_update(of=('self',)).select_related(
        'line_list', 'project', 'revision__document', 'revision__chain',
    ).filter(line_list=line_list).first()


def require_project_master(line_list, project):
    """Server-side provenance gate before project-linked technical approval."""
    provenance = locked_provenance(line_list)
    if provenance is None:
        raise ValidationError({'line_list_id': 'The line list has no canonical project association.'})
    if provenance.project_id != project.pk:
        raise ValidationError({'line_list_id': 'The line list is associated with another canonical project.'})
    binding = CRSChainProjectBinding.objects.filter(chain_id=provenance.revision.chain_id).first()
    if binding is None or binding.project_id != project.pk:
        raise LinkConflict('The master Line List CRS chain is bound to another project.')
    problem = master_state_error(provenance.revision)
    if problem:
        raise ValidationError({'line_list_id': problem})
    return provenance


@transaction.atomic
def associate_line_list(*, user, line_list_ref, project_id, revision_id):
    project = Project.objects.select_for_update().filter(
        pk=_integer(project_id, 'project_id'), is_deleted=False,
    ).first()
    if project is None:
        raise NotFound('Project not found.')
    if not project_access(user, project, write=True):
        raise PermissionDenied('Project write access is required.')
    if not module_action_allowed(user, 'crs_documents', 'read'):
        raise PermissionDenied('CRS document read access is required.')
    line_list = PidCheckerV2LineListUpload.objects.select_for_update(of=('self',)).select_related(
        'owner_project',
    ).filter(**line_list_lookup(line_list_ref)).first()
    if line_list is None:
        raise NotFound('Line list not found.')
    if line_list.created_by_id != user.pk or (line_list.owner_project_id and
                                              line_list.owner_project.created_by_id != user.pk):
        raise PermissionDenied('Line list ownership is required.')
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
        CRSChainProjectBinding.objects.create(chain=revision.chain, project=project, linked_by=user)
    problem = master_state_error(revision)
    if problem:
        raise ValidationError({'revision_id': problem})
    provenance = locked_provenance(line_list)
    if provenance:
        if provenance.project_id != project.pk:
            raise LinkConflict('Line list is already associated with another project.')
        if provenance.revision_id == revision.pk:
            return provenance, False
        if PidEngineeringEvidenceLink.objects.filter(
                line_list=line_list, review_state__in=LOCKED_REVIEW_STATES).exists():
            raise LinkConflict('A submitted or approved technical review uses this master Line List.')
        before = {'project_id': provenance.project_id, 'revision_id': provenance.revision_id,
                  'crs_document_id': provenance.revision.document_id}
        provenance.revision = revision
        provenance.updated_by = user
        provenance.save(update_fields=['revision', 'updated_by', 'updated_at'])
        _audit(user, provenance, 'update', before)
        return provenance, False
    provenance = PidLineListProvenance.objects.create(
        line_list=line_list, project=project, revision=revision, linked_by=user, updated_by=user,
    )
    _audit(user, provenance, 'create')
    return provenance, True
