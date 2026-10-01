"""Minimal canonical lookups; linking does not grant access to another domain."""
from django.db.models import Q
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed


def active(user):
    return bool(user and user.is_authenticated and user.is_active)


def organization_id(user):
    if not user:
        return None
    from apps.rbac.models import UserProfile
    return UserProfile.objects.filter(user_id=user.pk, is_deleted=False).values_list('organization_id', flat=True).first()


def visible_projects(user):
    from apps.project_control.access import accessible_enterprise_projects
    queryset = accessible_enterprise_projects(user)
    if not active(user) or not module_action_allowed(user, 'project_control', 'read'):
        return queryset.none()
    if not (user.is_staff or user.is_superuser):
        org = organization_id(user)
        if not org:
            return queryset.none()
        queryset = queryset.filter(owner__rbac_profile__organization_id=org, owner__rbac_profile__is_deleted=False)
    return queryset


def visible_clients(user):
    from apps.sales.email_permissions import visible_email_clients
    from apps.sales.models import Client
    if not active(user) or not module_action_allowed(user, 'sales_clients', 'read'):
        return Client.objects.none()
    queryset = visible_email_clients(user)
    if not (user.is_staff or user.is_superuser):
        org = organization_id(user)
        if not org:
            return queryset.none()
        queryset = queryset.filter(account_manager__rbac_profile__organization_id=org,
                                   account_manager__rbac_profile__is_deleted=False)
    return queryset


def visible_employees(user, project=None):
    from apps.hr_core.models import EmployeeMaster
    from .task_assignment_policy import eligible_employees
    if not active(user) or project is None or not visible_projects(user).filter(pk=project.pk).exists():
        return EmployeeMaster.objects.none()
    return eligible_employees(project, user)


def project_client_id(project):
    return getattr(project, 'client_id', None)


def candidate_payload(row, kind):
    if kind == 'client':
        code, label = row.client_code, row.company_name
    elif kind == 'project':
        code, label = row.code, row.name
    elif kind == 'employee':
        code, label = row.employee_code or row.employee_number, row.get_full_name()
    else:
        raise ValidationError({'kind': 'Select a supported record type.'})
    return {'id': str(row.pk), 'code': code, 'label': label}


def target_queryset(user, kind, project=None):
    if kind == 'client':
        return visible_clients(user)
    if kind == 'project':
        return visible_projects(user)
    if kind == 'employee':
        return visible_employees(user, project)
    raise ValidationError({'kind': 'Select a supported record type.'})


def require_target(user, kind, identifier, project=None):
    if identifier in (None, ''):
        raise ValidationError({f'{kind}_id': 'Select a record.'})
    try:
        row = target_queryset(user, kind, project).filter(pk=identifier).first()
    except (TypeError, ValueError, ValidationError, DjangoValidationError):
        row = None
    if row is None:
        raise PermissionDenied('The selected record is unavailable or outside your access.')
    return row


def search_targets(user, kind, search='', project=None):
    queryset = target_queryset(user, kind, project)
    search = (search or '').strip()[:160]
    if kind == 'client':
        fields, order = ('client_code', 'company_name'), ('company_name', 'pk')
    elif kind == 'project':
        fields, order = ('code', 'name'), ('code', 'pk')
    else:
        fields, order = ('employee_code', 'employee_number', 'first_name', 'last_name'), ('first_name', 'last_name', 'pk')
    if search:
        condition = Q()
        for field in fields:
            condition |= Q(**{f'{field}__icontains': search})
        queryset = queryset.filter(condition)
    rows = list(queryset.order_by(*order)[:21])
    return {'results': [candidate_payload(row, kind) for row in rows[:20]], 'has_more': len(rows) > 20}


def validate_project_client(project, client):
    if project is None or client is None:
        return
    if project.client_id and project.client_id != client.pk:
        raise ValidationError({'client_id': 'The selected client differs from the project client.'})
    project_org = organization_id(project.owner)
    client_org = organization_id(client.account_manager)
    if project_org and client_org and project_org != client_org:
        raise ValidationError({'client_id': 'The project and client belong to different organization scopes.'})


def visible_payload(user, kind, row, project=None):
    if row is None or not target_queryset(user, kind, project).filter(pk=row.pk).exists():
        return None
    return candidate_payload(row, kind)
