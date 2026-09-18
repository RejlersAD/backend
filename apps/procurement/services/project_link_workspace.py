"""Server project names and explicit, audited links to purchase orders."""

import re

from django.core.paginator import EmptyPage, Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.core.project_models import Project
from apps.procurement.models import Project as ProcurementProject
from apps.procurement.models import PurchaseOrder
from apps.rbac.action_policy import module_action_allowed


def folder_identity(folder_name):
    match = re.fullmatch(r'([0-9]{1,50})(?:[ _-]+(.*))?', folder_name)
    if not match:
        return '', folder_name
    return match.group(1), (match.group(2) or folder_name).strip()


def visible_folder_scopes(user):
    from apps.file_replica.permissions import action_allowed, visible_scopes

    if not action_allowed(user, 'read'):
        raise PermissionDenied('Project Control read permission is required to list server project names.')
    return visible_scopes(user)


def can_create_folder_project(user):
    from apps.file_replica.permissions import is_replica_admin

    return is_replica_admin(user) and module_action_allowed(user, 'project_control', 'create')


def canonical_project_payload(project):
    master_id = ProcurementProject.objects.filter(enterprise_project=project).values_list('pk', flat=True).first()
    return {'id': str(project.pk), 'code': project.code, 'name': project.name,
            'procurement_project_id': str(master_id) if master_id else None}


class WorkspaceQuerySerializer(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=200, default='')
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    link_status = serializers.ChoiceField(required=False, choices=['all', 'linked', 'unlinked'], default='all')
    project_id = serializers.IntegerField(required=False, min_value=1)
    scope_id = serializers.UUIDField(required=False)
    source_id = serializers.UUIDField(required=False)


def build_project_link_workspace(params, user):
    query = WorkspaceQuerySerializer(data=params)
    query.is_valid(raise_exception=True)
    values = query.validated_data
    canonical = list(Project.objects.annotate(
        order_count=Count('purchase_orders'),
    ).order_by('code', 'id').values(
        'id', 'code', 'name', 'status', 'client_name', 'currency',
        'order_count', 'procurement_project__id', 'is_deleted',
    ))
    by_id = {project['id']: project for project in canonical}
    by_code = {project['code']: project for project in canonical}
    scopes = visible_folder_scopes(user)
    if values.get('source_id'):
        scopes = scopes.filter(source_id=values['source_id'])
    scopes = list(scopes.order_by('relative_path', 'id'))
    code_counts = {}
    for scope in scopes:
        code, _ = folder_identity(scope.relative_path)
        key = (scope.source_id, code)
        code_counts[key] = code_counts.get(key, 0) + 1
    may_create = can_create_folder_project(user)
    projects = []
    for scope in scopes:
        code, name = folder_identity(scope.relative_path)
        project = by_id.get(scope.project_id) if scope.project_id else by_code.get(code)
        error = ''
        if project and project['is_deleted']:
            error = 'This folder refers to a removed project. Ask an administrator to review its mapping.'
            project = None
        elif not project and not code:
            error = 'This folder needs a confirmed numeric project code before it can be connected.'
        elif not project and code_counts[(scope.source_id, code)] > 1:
            error = 'More than one folder uses this project code. Ask an administrator to confirm the folder mapping.'
        elif not project and len(name) > 255:
            error = 'The folder name is too long for a project. Ask an administrator to confirm a shorter project name.'
        projects.append({
            'id': str(scope.pk), 'scope_id': str(scope.pk), 'source_id': str(scope.source_id),
            'folder_name': scope.relative_path,
            'project_id': str(project['id']) if project else None,
            'code': project['code'] if project else code, 'name': name,
            'status': project['status'] if project else '',
            'client_name': project['client_name'] if project else '',
            'currency': project['currency'] if project else '',
            'purchase_order_count': project['order_count'] if project else 0,
            'procurement_project_id': str(project['procurement_project__id'])
            if project and project['procurement_project__id'] else None,
            'can_prepare': not error and bool(project or may_create),
            'preparation_error': error,
        })
    project_id = values.get('project_id')
    if project_id and not any(project['id'] == project_id and not project['is_deleted'] for project in canonical):
        raise serializers.ValidationError({'project_id': 'Select an existing project.'})
    selected_scope = None
    if values.get('scope_id'):
        selected_scope = next((scope for scope in projects if scope['scope_id'] == str(values['scope_id'])), None)
        if selected_scope is None:
            raise NotFound('The selected project folder is unavailable.')
        if project_id and str(project_id) != selected_scope['project_id']:
            raise serializers.ValidationError({'project_id': 'The selected folder is mapped to a different project. Refresh the list.'})
        project_id = selected_scope['project_id']

    orders = PurchaseOrder.objects.select_related('vendor', 'enterprise_project').only(
        'id', 'po_number', 'title', 'po_date', 'status', 'total_amount', 'currency',
        'vendor_id', 'vendor__name', 'enterprise_project_id',
        'enterprise_project__code', 'enterprise_project__name',
    ).order_by('-created_at', '-id')
    if project_id:
        orders = orders.filter(enterprise_project_id=project_id)
    elif selected_scope:
        orders = orders.none()
    if values['link_status'] != 'all':
        orders = orders.filter(enterprise_project__isnull=values['link_status'] == 'unlinked')
    search = values['search'].strip()
    if search:
        orders = orders.filter(
            Q(po_number__icontains=search) | Q(title__icontains=search)
            | Q(vendor__name__icontains=search)
            | Q(enterprise_project__code__icontains=search)
            | Q(enterprise_project__name__icontains=search)
        )
    paginator = Paginator(orders, values['page_size'])
    try:
        page = paginator.page(values['page'])
    except EmptyPage as exc:
        raise NotFound('This purchase order page no longer exists. Refresh the list.') from exc
    results = []
    for order in page:
        project = order.enterprise_project
        results.append({
            'id': str(order.pk), 'po_number': order.po_number, 'title': order.title,
            'vendor_name': order.vendor.name, 'po_date': order.po_date.isoformat(),
            'status': order.status, 'total_amount': str(order.total_amount), 'currency': order.currency,
            'current_project': {'id': str(project.pk), 'code': project.code, 'name': project.name}
            if project else None,
        })
    return {
        'projects': projects,
        'purchase_orders': {
            'count': paginator.count, 'page': page.number,
            'page_size': paginator.per_page, 'total_pages': paginator.num_pages,
            'results': results,
        },
    }


class FolderProjectSerializer(serializers.Serializer):
    scope_id = serializers.UUIDField()
    expected_folder_project_id = serializers.IntegerField(allow_null=True, min_value=1)


class FolderOrderSerializer(FolderProjectSerializer):
    order_id = serializers.UUIDField()
    expected_project_id = serializers.IntegerField(allow_null=True, min_value=1)


@transaction.atomic
def prepare_folder_project(*, user, scope_id, expected_folder_project_id):
    """Resolve a selected folder only after an explicit Create PO/Connect click."""
    from apps.file_replica.models import ReplicaScope, ReplicaSource
    from apps.file_replica.permissions import is_replica_admin

    scope = get_object_or_404(visible_folder_scopes(user), pk=scope_id)
    # This order agrees with the connector and serializes duplicate-code folders
    # within the same source, without reading or changing any server content.
    ReplicaSource.objects.select_for_update().get(pk=scope.source_id)
    scope = ReplicaScope.objects.select_for_update().get(pk=scope.pk)
    if not visible_folder_scopes(user).filter(pk=scope.pk).exists():
        raise PermissionDenied('The selected project folder is no longer available.')
    code, name = folder_identity(scope.relative_path)
    project = Project.objects.filter(pk=scope.project_id).first() if scope.project_id else Project.objects.filter(code=code).first()
    if project and project.is_deleted:
        raise serializers.ValidationError({'scope_id': 'This folder refers to a removed project. Review the folder mapping first.'})
    current_id = project.pk if project else None
    if current_id != expected_folder_project_id:
        raise serializers.ValidationError({'expected_folder_project_id': 'This folder was mapped to a different project while you were reviewing it. Refresh before continuing.'})
    if scope.project_id:
        return project
    if not is_replica_admin(user):
        raise PermissionDenied('A file server administrator must confirm this folder mapping.')
    if project is None:
        if not can_create_folder_project(user):
            raise PermissionDenied('Project Control create permission is required to create this project.')
        if not code:
            raise serializers.ValidationError({'scope_id': 'This folder needs a confirmed numeric project code before it can be connected.'})
        if len(name) > 255:
            raise serializers.ValidationError({'scope_id': 'The folder name is too long for a project. Confirm a shorter project name first.'})
        duplicate = ReplicaScope.objects.filter(source=scope.source).exclude(pk=scope.pk).filter(
            relative_path__regex=rf'^{code}([ _-]|$)',
        ).exists()
        if duplicate:
            raise serializers.ValidationError({'scope_id': 'More than one folder uses this project code. Confirm the folder mapping first.'})
        project, _ = Project.objects.get_or_create(code=code, defaults={'name': name, 'owner': user})
        if project.is_deleted:
            raise serializers.ValidationError({'scope_id': 'This project code belongs to a removed project. Review it before continuing.'})
    scope.project = project
    # Publication remains exactly as configured; mapping is not an access grant.
    scope.save(update_fields=['project'])
    return project


@transaction.atomic
def connect_folder_order(*, user, scope_id, order_id, expected_project_id, expected_folder_project_id):
    from .project_relationships import resolve_project_relationship

    order = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=order_id)
    project = prepare_folder_project(user=user, scope_id=scope_id, expected_folder_project_id=expected_folder_project_id)
    return resolve_project_relationship(
        record_type='purchase_order', record_id=order.pk,
        enterprise_project_id=project.pk, expected_project_id=expected_project_id,
        user=user, reason=f'Linked from Project Links: {project.code} — {project.name}',
    )
