"""Tenant scoping, capability checks and team -> AI project access sync."""
import ipaddress
import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .models import (EDITION_INDIVIDUAL, EDITION_RANK, TEAM_LINKER_ROLES, TEAM_MANAGER_ROLES,
                     OrganizationAdmin, OrganizationEdition, OrganizationPolicy, Team,
                     TeamMembership, TeamWorkspaceAccess, TeamWorkspaceGrant)

RBAC_ADMIN_CODES = ('super_admin', 'admin', 'ict_admin')
FULL_ACCESS_TEAM_ROLES = ('admin', 'lead', 'engineer')


# ---------------------------------------------------------------- flags & tenancy
def enterprise_enabled():
    value = getattr(settings, 'ENABLE_ENTERPRISE_TEAMS', None)
    if value is None:
        value = os.environ.get('ENABLE_ENTERPRISE_TEAMS', 'false')
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def organization_for(user):
    from apps.rbac.models import UserProfile
    profile = (UserProfile.objects.filter(user=user, is_deleted=False)
               .select_related('organization').first())
    if profile and profile.organization and profile.organization.is_active:
        return profile.organization
    return None


def require_organization(user):
    org = organization_for(user)
    if not org:
        raise PermissionDenied('Your account is not linked to an active organization.')
    return org


def active_q(prefix=''):
    now = timezone.now()
    return Q(**{f'{prefix}expires_at__isnull': True}) | Q(**{f'{prefix}expires_at__gt': now})


def _rbac_role_codes(user):
    from apps.rbac.models import UserRole
    now = timezone.now()
    return set(UserRole.objects.filter(
        user_profile__user=user, user_profile__is_deleted=False, role__is_active=True,
    ).filter(
        Q(effective_from__isnull=True) | Q(effective_from__lte=now),
        Q(expires_at__isnull=True) | Q(expires_at__gt=now),
    ).values_list('role__code', flat=True))


def is_platform_admin(user):
    """Can change an organization's edition (tier). Superusers and super_admins only."""
    return bool(user.is_superuser or 'super_admin' in _rbac_role_codes(user))


def org_roles(user, org):
    roles = set(OrganizationAdmin.objects.filter(organization=org, user=user).values_list('role', flat=True))
    if user.is_superuser or _rbac_role_codes(user) & set(RBAC_ADMIN_CODES):
        roles.add('admin')
    return roles


def capabilities(user, org):
    roles = org_roles(user, org)
    return {
        'platform_admin': is_platform_admin(user),
        'manage_org': bool(roles & {'owner', 'admin'}),
        'manage_policy': bool(roles & {'owner', 'admin', 'security'}),
        'view_audit': bool(roles & {'owner', 'admin', 'security', 'auditor'}),
        'grant_owner': bool('owner' in roles or user.is_superuser),
        'org_roles': sorted(roles),
    }


# ---------------------------------------------------------------- edition & policy
def edition_for(org):
    return OrganizationEdition.objects.filter(organization=org).first() or OrganizationEdition(organization=org)


def effective_edition(org):
    edition = edition_for(org)
    if edition.valid_until and edition.valid_until < timezone.now():
        return EDITION_INDIVIDUAL
    return edition.edition


def require_edition(org, minimum):
    if EDITION_RANK[effective_edition(org)] < EDITION_RANK[minimum]:
        label = 'Team' if minimum == 'team' else 'Enterprise'
        raise PermissionDenied(f'This feature needs the {label} edition for your organization.')


def policy_for(org):
    return OrganizationPolicy.objects.filter(organization=org).first() or OrganizationPolicy(organization=org)


def clean_policy(data):
    """Validate a PATCH body for OrganizationPolicy. Returns only known, valid fields."""
    out, errors = {}, {}
    for field in ('require_mfa', 'allow_external_guests', 'ai_outputs_require_approval', 'allow_web_search'):
        if field in data:
            if not isinstance(data[field], bool):
                errors[field] = 'Use true or false.'
            else:
                out[field] = data[field]
    for field, low, high, nullable in (('guest_max_days', 1, 365, False),
                                       ('data_retention_days', 30, 36500, True),
                                       ('session_timeout_minutes', 5, 1440, True)):
        if field in data:
            value = data[field]
            if value in (None, '') and nullable:
                out[field] = None
                continue
            try:
                value = int(value)
            except (TypeError, ValueError):
                errors[field] = 'Enter a whole number.'
                continue
            if not low <= value <= high:
                errors[field] = f'Enter a value between {low} and {high}.'
            else:
                out[field] = value
    if 'data_residency_region' in data:
        region = str(data['data_residency_region'] or '').strip().lower()
        if len(region) > 40:
            errors['data_residency_region'] = 'Keep the region under 40 characters.'
        else:
            out['data_residency_region'] = region
    if 'allowed_chat_models' in data:
        models = data['allowed_chat_models']
        if not isinstance(models, list) or not all(isinstance(m, str) and 0 < len(m.strip()) <= 120 for m in models):
            errors['allowed_chat_models'] = 'Send a list of model ids.'
        else:
            out['allowed_chat_models'] = sorted({m.strip() for m in models})[:50]
    if 'allowed_ip_ranges' in data:
        ranges = data['allowed_ip_ranges']
        if not isinstance(ranges, list) or len(ranges) > 100:
            errors['allowed_ip_ranges'] = 'Send a list of up to 100 CIDR ranges.'
        else:
            cleaned = []
            for item in ranges:
                try:
                    cleaned.append(str(ipaddress.ip_network(str(item).strip(), strict=False)))
                except ValueError:
                    errors['allowed_ip_ranges'] = f'"{str(item)[:60]}" is not a valid IP range.'
                    break
            else:
                out['allowed_ip_ranges'] = sorted(set(cleaned))
    if errors:
        raise ValidationError(errors)
    return out


# ---------------------------------------------------------------- teams
def team_role(user, team):
    return (TeamMembership.objects.filter(team=team, user=user).filter(active_q())
            .values_list('role', flat=True).first())


def visible_teams(user, org, caps):
    teams = Team.objects.filter(organization=org, is_active=True)
    if caps['manage_org'] or caps['view_audit']:
        return teams
    member_team_ids = TeamMembership.objects.filter(user=user).filter(active_q()).values('team_id')
    return teams.filter(pk__in=member_team_ids)


def team_for(user, org, caps, team_id, need=None):
    team = Team.objects.filter(pk=team_id, organization=org, is_active=True).first()
    if not team:
        raise NotFound('Team not found.')
    role = team_role(user, team)
    if not role and not (caps['manage_org'] or caps['view_audit']):
        raise NotFound('Team not found.')
    if need == 'manage' and not (caps['manage_org'] or role in TEAM_MANAGER_ROLES):
        raise PermissionDenied('Only team admins or organization admins can change this team.')
    if need == 'link' and not (caps['manage_org'] or role in TEAM_LINKER_ROLES):
        raise PermissionDenied('Only team admins or lead engineers can share AI projects with this team.')
    return team, role


def seats_in_use(org):
    return (TeamMembership.objects.filter(team__organization=org, team__is_active=True)
            .filter(active_q()).values('user_id').distinct().count())


def check_seat(org, user):
    edition = edition_for(org)
    if not edition.seat_limit:
        return
    already = (TeamMembership.objects.filter(team__organization=org, team__is_active=True, user=user)
               .filter(active_q()).exists())
    if not already and seats_in_use(org) >= edition.seat_limit:
        raise ValidationError({'email': f'All {edition.seat_limit} team seats are in use.'})


def clean_guest_expiry(org, value):
    policy = policy_for(org)
    if not policy.allow_external_guests:
        raise ValidationError({'role': 'External guests are turned off in your organization policy.'})
    if not value:
        raise ValidationError({'expires_at': 'Guests need an access end date.'})
    limit = timezone.now() + timedelta(days=policy.guest_max_days)
    if value <= timezone.now() or value > limit:
        raise ValidationError({'expires_at': f'Choose a date within the next {policy.guest_max_days} days.'})
    return value


# ---------------------------------------------------------------- team -> AI project sync
def _desired_workspace_role(workspace, user):
    if not user.is_active:
        return None, []
    team_roles = dict(TeamMembership.objects.filter(user=user, team__is_active=True)
                      .filter(active_q()).values_list('team_id', 'role'))
    best, contributing = None, []
    for access in TeamWorkspaceAccess.objects.filter(workspace=workspace, team_id__in=list(team_roles)):
        role = access.role if team_roles[access.team_id] in FULL_ACCESS_TEAM_ROLES else 'viewer'
        contributing.append(access)
        best = 'editor' if 'editor' in (best, role) else 'viewer'
    return best, contributing


def sync_member(workspace, user, actor=None):
    """Make the user's AIWorkspaceMember row match their team access.

    Returns 'owner', 'manual', 'removed', 'unchanged', 'created' or 'updated'.
    """
    from apps.core.ai_workspace_models import AIWorkspace, AIWorkspaceMember
    if user.pk == workspace.owner_id:
        return 'owner'
    with transaction.atomic():
        AIWorkspace.objects.select_for_update().filter(pk=workspace.pk).first()
        member = AIWorkspaceMember.objects.select_for_update().filter(workspace=workspace, user=user).first()
        if member and not member.team_grants.exists():
            return 'manual'  # added by hand in RADAI Intelligence - never touched
        role, contributing = _desired_workspace_role(workspace, user)
        if not role:
            if member:
                member.delete()
                return 'removed'
            return 'unchanged'
        result = 'unchanged'
        if member is None:
            member = AIWorkspaceMember.objects.create(workspace=workspace, user=user, role=role,
                                                      added_by=actor or workspace.owner)
            result = 'created'
        elif member.role != role:
            member.role = role
            member.save(update_fields=['role'])
            result = 'updated'
        TeamWorkspaceGrant.objects.filter(member=member).exclude(access__in=contributing).delete()
        for access in contributing:
            TeamWorkspaceGrant.objects.get_or_create(access=access, member=member)
        if workspace.visibility != 'shared':
            workspace.visibility = 'shared'
            workspace.save(update_fields=['visibility', 'updated_at'])
        return result


def sync_access(access, actor=None, extra_user_ids=()):
    user_ids = set(access.team.memberships.values_list('user_id', flat=True))
    user_ids |= set(access.grants.values_list('member__user_id', flat=True))
    user_ids |= set(extra_user_ids)
    counts = {}
    for user in get_user_model().objects.filter(pk__in=user_ids):
        outcome = sync_member(access.workspace, user, actor)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def sync_user_in_team(team, user, actor=None):
    for access in team.workspace_access.select_related('workspace'):
        sync_member(access.workspace, user, actor)


def revoke_access(access, actor=None):
    from apps.core.ai_workspace_models import AIWorkspaceMember
    workspace = access.workspace
    member_ids = list(access.grants.values_list('member_id', flat=True))
    with transaction.atomic():
        access.delete()
        for member in AIWorkspaceMember.objects.filter(pk__in=member_ids).select_related('user'):
            if member.team_grants.exists():
                sync_member(workspace, member.user, actor)
            else:
                member.delete()
    return len(member_ids)
