"""Project and record scope for P&ID Checker V2; action RBAC is in route_guard."""
from uuid import UUID

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.planning_intelligence.access import can_access_enterprise_project


def _uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _organizer_access(user, project, *, write=False):
    if user.is_superuser or project.created_by_id == user.pk:
        return True
    return bool(project.enterprise_project_id and can_access_enterprise_project(
        user, project.enterprise_project, write=write,
    ))


def _pidv_access(user, project):
    # PIDVProject has no membership relation or enterprise identity.
    return user.is_superuser or project.created_by_id == user.pk


def accessible_organizer_projects(user):
    from apps.project_organizer.models import Project
    if user.is_superuser:
        return Project.objects.all()
    return Project.objects.filter(
        Q(created_by=user) |
        Q(enterprise_project__is_deleted=False, enterprise_project__owner=user) |
        Q(enterprise_project__is_deleted=False,
          enterprise_project__memberships__user=user,
          enterprise_project__memberships__is_active=True)
    ).distinct()


def accessible_legends(user):
    from .models import PidCheckerV2LegendSheet
    return PidCheckerV2LegendSheet.objects.filter(created_by=user).filter(
        Q(project__isnull=True) | Q(project__in=accessible_organizer_projects(user))
    )


def _record_access(user, record, *, write=False):
    if user.is_superuser:
        return True
    for field in ('project', 'owner_project'):
        project = getattr(record, field, None)
        if project is None:
            continue
        from apps.project_organizer.models import Project as OrganizerProject
        from apps.pid_verification.models import PIDVProject
        if isinstance(project, OrganizerProject):
            return _organizer_access(user, project, write=write)
        if isinstance(project, PIDVProject):
            return _pidv_access(user, project)
        return project.created_by_id == user.pk
    return getattr(record, 'created_by_id', None) == user.pk


_DIRECT_RECORDS = {
    'extraction_id': ('PidCheckerV2Extraction', 'extraction_id'),
    'legend_id': ('PidCheckerV2LegendSheet', 'legend_id'),
    'line_list_id': ('PidCheckerV2LineListUpload', 'line_list_id'),
    'equipment_list_id': ('PidCheckerV2EquipmentListUpload', 'equipment_list_id'),
    'instrument_index_id': ('PidCheckerV2InstrumentIndexUpload', 'instrument_index_id'),
}
_PIDV_PROJECT_VIEWS = {
    'ExtractLineTagsView',
    'LineListUploadView', 'LineListListView', 'CrossCheckView',
    'EquipmentListUploadView', 'EquipmentCrossCheckView',
    'InstrumentIndexUploadView', 'InstrumentCrossCheckView',
    'SymbolImagesListView', 'SymbolImageUploadView', 'SymbolImageDeleteView',
}
_READ_POST_VIEWS = {
    'ValidateLineTagsView', 'CrossCheckView', 'EquipmentCrossCheckView',
    'InstrumentCrossCheckView', 'CrossReferenceResultsView',
}


class PidCheckerScopePermission(BasePermission):
    """Check saved ownership and every request-selected project before work starts."""

    message = 'You do not have access to this P&ID project or record.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        name = view.__class__.__name__
        write = request.method not in ('GET', 'HEAD', 'OPTIONS') and name not in _READ_POST_VIEWS
        data = request.data if request.method not in ('GET', 'HEAD', 'OPTIONS') else request.query_params
        values = dict(getattr(view, 'kwargs', {}))
        for key in (*_DIRECT_RECORDS, 'pid_document_id'):
            values.setdefault(key, data.get(key))
        from . import models
        for key, (model_name, lookup) in _DIRECT_RECORDS.items():
            value = values.get(key)
            if not value or not _uuid(value):
                continue
            record = getattr(models, model_name).objects.filter(**{lookup: value}).first()
            if record is not None and not _record_access(user, record, write=write):
                raise PermissionDenied(self.message)
        document_id = values.get('pid_document_id')
        if document_id and _uuid(document_id):
            from apps.pid_verification.models import PIDVDocument
            document = PIDVDocument.objects.select_related('project').filter(document_id=document_id).first()
            if document is not None and (document.project is None or not _pidv_access(user, document.project)):
                raise PermissionDenied(self.message)

        organizer_pk = (request.data.get('project') if name in {
            'LegendSheetListCreateView', 'LegendSheetDetailView',
        } and request.method in ('POST', 'PUT', 'PATCH') else None)
        if organizer_pk:
            from apps.project_organizer.models import Project
            project = Project.objects.filter(pk=_uuid(organizer_pk)).first()
            if project is not None and not _organizer_access(user, project, write=True):
                raise PermissionDenied(self.message)
        project_id = data.get('project_id') or request.query_params.get('project_id')
        if project_id and _uuid(project_id):
            if name in _PIDV_PROJECT_VIEWS:
                from apps.pid_verification.models import PIDVProject
                project = PIDVProject.objects.filter(project_id=project_id).first()
                allowed = project is None or _pidv_access(user, project)
            else:
                from apps.project_organizer.models import Project
                project = Project.objects.filter(project_id=project_id).first()
                allowed = project is None or _organizer_access(user, project, write=write)
            if not allowed:
                raise PermissionDenied(self.message)
        return True
