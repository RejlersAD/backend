"""Explicit permanent project deletion with existing authority and evidence guards."""
from collections import Counter

from django.apps import apps
from django.db import connection, OperationalError, transaction
from django.db.models import Q
from django.db.models.deletion import Collector, ProtectedError, RestrictedError
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from apps.rbac.approval_eligibility import active_approval_user, project_approval_assignment
from apps.rbac.models import AuditLog
from .project_models import Project


class PermanentProjectDeleteSerializer(serializers.Serializer):
    permanent = serializers.BooleanField()
    expected_updated_at = serializers.DateTimeField()

    def validate_permanent(self, value):
        if value is not True:
            raise serializers.ValidationError('Explicit permanent deletion confirmation is required.')
        return value


class ProjectDeleteConflict(APIException):
    status_code = 409
    default_code = 'project_delete_conflict'

    def __init__(self, message, *, code='project_delete_conflict', blockers=None):
        super().__init__({'error': message, 'code': code, **({'blockers': blockers} if blockers else {})})


def _blockers(objects):
    counts = Counter(str(row._meta.verbose_name_plural) for row in objects)
    return [{'record_type': str(label), 'count': count} for label, count in sorted(counts.items())]


def _workspace_guards(workspaces):
    from apps.planning_intelligence.models import (
        DocumentIntelligenceRun, IntegrationDelivery, PlanningFile, PlanningJob,
        EvidenceDecision, EvidenceDocumentVersion, EvidenceNode,
        PlanningRetentionPolicy, ScheduleLogicReview,
    )
    if PlanningRetentionPolicy.objects.filter(project__in=workspaces, legal_hold=True).exists():
        raise ProjectDeleteConflict('This project has an active legal hold. Remove it through the authorized retention workflow before deleting.',
                                    code='project_delete_legal_hold')
    active_checks = (
        PlanningJob.objects.filter(project__in=workspaces, status__in=['queued', 'running']),
        PlanningFile.objects.filter(project__in=workspaces, parse_status__in=['pending', 'processing']),
        DocumentIntelligenceRun.objects.filter(project__in=workspaces, status='running'),
        IntegrationDelivery.objects.filter(endpoint__project__in=workspaces, status__in=['queued', 'delivering']),
    )
    if any(query.exists() for query in active_checks):
        raise ProjectDeleteConflict('Project processing or delivery is still active. Wait for it to finish before deleting.',
                                    code='project_delete_work_active')
    # The ORM cascade bypasses this model's append-only delete override.
    immutable = (
        ScheduleLogicReview.objects.filter(project__in=workspaces),
        EvidenceDocumentVersion.objects.filter(graph__project__in=workspaces),
        EvidenceNode.objects.filter(graph__project__in=workspaces),
        EvidenceDecision.objects.filter(graph__project__in=workspaces),
    )
    for records in immutable:
        if records.exists():
            raise ProjectDeleteConflict('This project has protected planning evidence and cannot be permanently deleted.',
                                        code='project_delete_protected', blockers=_blockers(records))


def _commercial_guards(project):
    from apps.project_control.models import ProjectDocument
    if ProjectDocument.objects.filter(project=project, parse_status__in=['pending', 'processing']).exists():
        raise ProjectDeleteConflict('Project document processing is still active. Wait for it to finish before deleting.',
                                    code='project_delete_work_active')
    # These links use SET_NULL, but removing project identity from existing
    # procurement evidence would silently break cross-department reporting.
    for label in ('PurchaseRequisition', 'PurchaseOrder'):
        if not apps.is_installed('apps.procurement'):
            break
        model = apps.get_model('procurement', label)
        scope = Q(enterprise_project=project)
        if label == 'PurchaseOrder':
            scope |= Q(project__enterprise_project=project)
        records = model.objects.filter(scope)
        if records.exists():
            raise ProjectDeleteConflict('This project is referenced by procurement records. Resolve those project links before deleting.',
                                        code='project_delete_commercial_records', blockers=_blockers(records))


def delete_project(project_id, actor, *, expected_updated_at):
    """Delete only an unprotected project, never substitute an archive operation."""
    from apps.planning_intelligence.models import PlanningProject
    from .project_deletion_storage import collect_project_files, cleanup_project_files

    outcome = {'cleanup_pending': False}
    with transaction.atomic():
        # Keep workspace-before-enterprise order used by existing planning work.
        # Archived child rows belong to this deletion too.
        workspaces = list(PlanningProject.objects.select_for_update().filter(
            enterprise_project_id=project_id).order_by('pk'))
        try:
            # Agreement intake can hold the enterprise row before its workspace.
            # Do not wait on that opposite order while holding workspace locks.
            with transaction.atomic():
                project = Project.objects.select_for_update(
                    nowait=connection.features.has_select_for_update_nowait,
                ).filter(pk=project_id, is_deleted=False).first()
        except OperationalError as exc:
            cause = exc.__cause__
            if (getattr(cause, 'sqlstate', None) or getattr(cause, 'pgcode', None)) != '55P03':
                raise
            raise ProjectDeleteConflict('The project is being changed in another session. Retry deletion after that work finishes.',
                                        code='project_delete_busy') from exc
        if project is None:
            raise NotFound('This project is no longer available.')
        if not active_approval_user(actor) or not (
            actor.is_staff or actor.is_superuser or project_approval_assignment(actor, project)
        ):
            raise PermissionDenied('Only the project owner, project manager, or authorized administrator may permanently delete this project.')
        if expected_updated_at != project.updated_at:
            raise ProjectDeleteConflict('The project changed after you opened it. Reload before permanently deleting.',
                                        code='project_delete_stale')
        if set(PlanningProject.objects.filter(enterprise_project_id=project_id).values_list('pk', flat=True)) != {
            row.pk for row in workspaces
        }:
            raise ProjectDeleteConflict('The project gained a planning workspace while deletion was being checked. Reload before deleting.',
                                        code='project_delete_stale')
        _workspace_guards(workspaces)
        _commercial_guards(project)
        collector = Collector(using=project._state.db)
        try:
            collector.collect([project])
        except (ProtectedError, RestrictedError) as exc:
            protected = getattr(exc, 'protected_objects', None) or getattr(exc, 'restricted_objects', ())
            raise ProjectDeleteConflict('This project has protected business or planning records and cannot be permanently deleted.',
                                        code='project_delete_protected', blockers=_blockers(protected)) from exc
        # Cascades bypass model-level workflow guards as well as delete overrides.
        # Submitted/approved business evidence remains protected until its domain
        # explicitly permits removal; a project delete cannot manufacture that permission.
        protected_states = {'submitted', 'approved', 'baselined', 'published', 'issued',
                            'internal_review', 'approval_review', 'posted'}
        collected = [*collector.data.items(), *((query.model, query) for query in collector.fast_deletes)]
        protected = [row for model, rows in collected
                     if model._meta.app_label in {'planning_intelligence', 'project_control'}
                     for row in rows if getattr(row, 'status', None) in protected_states
                     or getattr(row, 'approved_at', None) or getattr(row, 'published_at', None)]
        if protected:
            raise ProjectDeleteConflict('This project has submitted or approved business evidence and cannot be permanently deleted.',
                                        code='project_delete_protected', blockers=_blockers(protected))
        files = collect_project_files(collector)
        counts = {model._meta.label: len(rows) for model, rows in collector.data.items()}
        for query in collector.fast_deletes:
            label = query.model._meta.label
            counts[label] = counts.get(label, 0) + query.count()
        audit = AuditLog.objects.create(
            user=actor, user_email=actor.email, action='delete', resource_type='Project',
            resource_repr=project.code, success=True,
            changes={'before': {'project_id': project.pk, 'code': project.code, 'name': project.name},
                     'after': {'permanently_deleted': True}},
            metadata={'command': 'permanently_delete_project', 'project_id': project.pk,
                      'planning_workspace_ids': [row.pk for row in workspaces],
                      'deleted_record_counts': counts, 'files': files},
        )
        collector.delete()
        outcome.update(cleanup_pending=bool(files), deletion_id=str(audit.pk))
        if files:
            def cleanup():
                outcome['cleanup_pending'] = not cleanup_project_files(audit.pk)['completed']
            transaction.on_commit(cleanup, robust=True)
    return outcome
