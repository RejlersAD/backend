"""
Project Organizer — CRUD + activity endpoints
================================================

Endpoints (all under /api/v1/project-organizer/)
-------------------------------------------------
GET    projects/                        list_projects   (RBAC-filtered)
POST   projects/                        create_project
GET    projects/<project_id>/           get_project
PATCH  projects/<project_id>/           update_project
DELETE projects/<project_id>/           delete_project
GET    projects/<project_id>/activity/  list_project_activity (optional ?tool_code=)
POST   projects/<project_id>/activity/  create_project_activity (append-only)

Uses the central module/action policy and mirrors
``apps.spec_customization.project_views`` owner/admin write conventions. Admins
(``is_staff``/``is_superuser``/admin-ish role) see and modify every
project; team sharing adds read access only. Regular users modify their own projects.
"""
from __future__ import annotations

import logging
import os

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Project, ProjectActivity

User = get_user_model()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded configuration
# ---------------------------------------------------------------------------
PROJECT_RBAC = {
    'list_limit':     500,
    'activity_limit': 200,
}

# Project visibility policy (soft-coded). Mirrors the platform's RBAC
# VisibilityStrategy pattern. Sharing requires effective read permission in a
# configured collaboration module for both the viewer and project owner.
PROJECT_VISIBILITY = {
    # 'owner'       → non-admins see only their own projects (legacy).
    # 'module_team' → non-admins see their own projects PLUS projects whose
    #                 creator shares one of `team_module_codes` with them.
    'strategy': os.getenv('PROJECT_VISIBILITY_STRATEGY', 'module_team').strip().lower(),
    # Modules that confer team-wide project visibility when strategy=module_team.
    'team_module_codes': [
        m.strip() for m in os.getenv(
            'PROJECT_VISIBILITY_TEAM_MODULES',
            'process_datasheet,spec_customization,piping_pms,pid_analysis',
        ).split(',') if m.strip()
    ],
}


def _user_team_module_codes(user) -> set:
    """Configured team modules with a current effective read grant."""
    try:
        from apps.rbac.action_policy import module_action_allowed
        return {
            code for code in PROJECT_VISIBILITY['team_module_codes']
            if module_action_allowed(user, code, 'read')
        }
    except Exception:
        return set()


def _shares_team_module(viewer, owner) -> bool:
    """True when viewer and project owner share at least one team module."""
    team = set(PROJECT_VISIBILITY['team_module_codes'])
    if not team or viewer is None or owner is None:
        return False
    viewer_codes = _user_team_module_codes(viewer)
    if not viewer_codes.intersection(team):
        return False
    owner_codes = _user_team_module_codes(owner)
    return bool(viewer_codes.intersection(owner_codes).intersection(team))


ALLOWED_CREATE_FIELDS = {
    'name', 'code', 'client', 'plant', 'discipline',
    'description', 'status', 'tags', 'metadata',
}
ALLOWED_UPDATE_FIELDS = ALLOWED_CREATE_FIELDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_admin(user) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False) or getattr(user, 'is_staff', False):
        return True
    role = (getattr(user, 'role', '') or '').lower()
    if role in {'admin', 'super_admin', 'tenant_admin'}:
        return True
    return False


def _user_role(user) -> str:
    if _is_admin(user):
        return 'admin'
    return (getattr(user, 'role', '') or 'user').lower()


def _user_can_modify(user, project: Project) -> bool:
    if _is_admin(user):
        return True
    return project.created_by_id == getattr(user, 'id', None)


def _serialize_project(p: Project, *, activity_count: int | None = None) -> dict:
    return {
        'project_id':     str(p.project_id),
        'name':           p.name,
        'code':           p.code,
        'client':         p.client,
        'plant':          p.plant,
        'discipline':     p.discipline,
        'description':    p.description,
        'status':         p.status,
        'tags':           p.tags or [],
        'metadata':       p.metadata or {},
        'created_at':     p.created_at.isoformat() if p.created_at else None,
        'updated_at':     p.updated_at.isoformat() if p.updated_at else None,
        'created_by_id':  p.created_by_id,
        'created_by':     getattr(p.created_by, 'username', '') if p.created_by_id else '',
        'activity_count': activity_count if activity_count is not None else p.activity.count(),
    }


def _serialize_activity(a: ProjectActivity) -> dict:
    return {
        'id':         a.id,
        'project_id': str(a.project_id),
        'tool_code':  a.tool_code,
        'summary':    a.summary,
        'metadata':   a.metadata or {},
        'created_at': a.created_at.isoformat() if a.created_at else None,
        'created_by': getattr(a.created_by, 'username', '') if a.created_by_id else '',
    }


def _filtered_queryset(user):
    qs = Project.objects.select_related('created_by').all()
    if _is_admin(user):
        return qs
    if PROJECT_VISIBILITY['strategy'] == 'module_team':
        team = set(PROJECT_VISIBILITY['team_module_codes'])
        if team and _user_team_module_codes(user).intersection(team):
            # Share only projects whose creator has a common effective read grant.
            teammate_ids = [
                uid for uid in Project.objects.values_list('created_by_id', flat=True).distinct()
                if uid and _shares_team_module(user, User.objects.filter(pk=uid).first())
            ]
            return qs.filter(Q(created_by=user) | Q(created_by_id__in=teammate_ids))
    return qs.filter(created_by=user)


def _sanitize_payload(data: dict, whitelist: set) -> dict:
    out = {}
    for k in whitelist:
        if k in data:
            out[k] = data[k]
    if 'status' in out:
        valid = {c[0] for c in Project.STATUS_CHOICES}
        if out['status'] not in valid:
            out['status'] = Project.STATUS_ACTIVE
    if 'tags' in out and not isinstance(out['tags'], list):
        out['tags'] = []
    if 'metadata' in out and not isinstance(out['metadata'], dict):
        out['metadata'] = {}
    return out


def _get_accessible_project(user, project_id):
    """Returns (project, error_response). error_response is None on success."""
    try:
        p = Project.objects.select_related('created_by').get(project_id=project_id)
    except Project.DoesNotExist:
        return None, Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)
    if _is_admin(user) or p.created_by_id == getattr(user, 'id', None):
        return p, None
    if PROJECT_VISIBILITY['strategy'] == 'module_team' and _shares_team_module(user, p.created_by):
        return p, None
    return None, Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def projects_collection(request):
    if request.method == 'POST':
        return _create_project(request)
    return _list_projects(request)


def _list_projects(request):
    qs = _filtered_queryset(request.user)
    status_filter = request.query_params.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)
    search = (request.query_params.get('q') or '').strip()
    if search:
        qs = qs.filter(
            Q(name__icontains=search) |
            Q(code__icontains=search) |
            Q(client__icontains=search) |
            Q(plant__icontains=search)
        )

    qs = qs[:PROJECT_RBAC['list_limit']]
    items = [_serialize_project(p) for p in qs]
    return Response({
        'role':  _user_role(request.user),
        'total': len(items),
        'items': items,
    })


def _create_project(request):
    payload = _sanitize_payload(request.data or {}, ALLOWED_CREATE_FIELDS)
    if not (payload.get('name') or '').strip():
        return Response({'error': 'Project name is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        p = Project.objects.create(created_by=request.user, **payload)
    except Exception as exc:
        logger.exception('Project create failed')
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize_project(p, activity_count=0), status=status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def project_detail(request, project_id):
    p, err = _get_accessible_project(request.user, project_id)
    if err:
        return err

    if request.method == 'GET':
        return Response(_serialize_project(p))

    if not _user_can_modify(request.user, p):
        return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'PATCH':
        updates = _sanitize_payload(request.data or {}, ALLOWED_UPDATE_FIELDS)
        for k, v in updates.items():
            setattr(p, k, v)
        p.save()
        return Response(_serialize_project(p))

    # DELETE
    p.delete()
    return Response({'deleted': True, 'project_id': str(project_id)})


# ---------------------------------------------------------------------------
# Cross-tool activity log
# ---------------------------------------------------------------------------
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def activity_collection(request, project_id):
    p, err = _get_accessible_project(request.user, project_id)
    if err:
        return err

    if request.method == 'POST':
        if not _user_can_modify(request.user, p):
            return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)
        tool_code = (request.data.get('tool_code') or '').strip()
        summary   = (request.data.get('summary') or '').strip()
        if not tool_code or not summary:
            return Response({'error': 'tool_code and summary are required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        metadata = request.data.get('metadata')
        if not isinstance(metadata, dict):
            metadata = {}
        a = ProjectActivity.objects.create(
            project=p, tool_code=tool_code, summary=summary,
            metadata=metadata, created_by=request.user,
        )
        return Response(_serialize_activity(a), status=status.HTTP_201_CREATED)

    qs = p.activity.all()
    tool_code_filter = request.query_params.get('tool_code')
    if tool_code_filter:
        qs = qs.filter(tool_code=tool_code_filter)
    qs = qs[:PROJECT_RBAC['activity_limit']]
    return Response({'items': [_serialize_activity(a) for a in qs]})
