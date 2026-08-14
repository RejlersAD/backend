"""Unified active project assignments used by employee lifecycle workflows."""

from django.contrib.auth import get_user_model

from apps.core.project_models import Project
from apps.rbac.models import EngineerProfile, UserProfile as RBACUserProfile


User = get_user_model()


def get_active_project_assignments(user):
    if not user:
        return []

    assignments = []
    core_projects = Project.objects.filter(
        status='active',
        memberships__user=user,
        memberships__is_active=True,
    ).select_related('owner').distinct()
    for project in core_projects:
        managers = [project.owner] if project.owner_id else []
        managers.extend(
            membership.user
            for membership in project.memberships.select_related('user').filter(
                role='project_manager', is_active=True
            )
        )
        assignments.append({
            'id': project.id,
            'code': project.code,
            'name': project.name,
            'managers': [manager for manager in managers if manager],
            'source': 'core_project',
        })

    profile = RBACUserProfile.objects.filter(
        user=user,
        is_deleted=False,
    ).select_related('engineer_profile').first()
    profile_projects = []
    if profile:
        try:
            profile_projects = profile.engineer_profile.current_projects or []
        except EngineerProfile.DoesNotExist:
            profile_projects = []

    active_profile_projects = [
        project for project in profile_projects
        if isinstance(project, dict) and project.get('status', 'active') == 'active'
    ]
    manager_ids = {
        str(project.get('project_manager_id'))
        for project in active_profile_projects
        if project.get('project_manager_id') not in (None, '')
    }
    manager_users = {
        str(manager.id): manager
        for manager in User.objects.filter(id__in=manager_ids, is_active=True)
    }

    for project in active_profile_projects:
        manager = manager_users.get(str(project.get('project_manager_id')))
        project_id = project.get('id') or project.get('name')
        assignments.append({
            'id': f'profile-{project_id}',
            'code': project.get('code') or 'Profile',
            'name': project.get('name') or 'Unnamed Project',
            'managers': [manager] if manager else [],
            'source': 'engineer_profile',
        })

    return assignments


def get_profile_project_manager(user):
    """Return the PoM from the most recently added active profile project."""
    assignments = get_active_project_assignments(user)
    for assignment in reversed(assignments):
        if assignment['source'] == 'engineer_profile' and assignment['managers']:
            return assignment['managers'][0], assignment
    return None, None
