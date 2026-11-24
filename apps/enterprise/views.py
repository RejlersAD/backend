"""REST API for Team & Enterprise tiers, mounted at /api/v1/enterprise/."""
import csv
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access as acl
from .audit import audit, client_ip
from .models import (EDITION_CHOICES, ORG_ADMIN_ROLES, TEAM_ROLES, WORKSPACE_ROLES,
                     OrganizationAdmin, OrganizationPolicy, Team, TeamMembership,
                     TeamWorkspaceAccess)

User = get_user_model()
TEAM_ROLE_CODES = {code for code, _ in TEAM_ROLES}
ORG_ROLE_CODES = {code for code, _ in ORG_ADMIN_ROLES}
WS_ROLE_CODES = {code for code, _ in WORKSPACE_ROLES}
EDITION_CODES = {code for code, _ in EDITION_CHOICES}
MAX_EXPORT_ROWS = 10000


def iso(value):
    return value.isoformat() if value else None


def parse_when(value, field):
    if value in (None, ''):
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        day = parse_date(str(value))
        if day is None:
            raise ValidationError({field: 'Use an ISO date, e.g. 2026-12-31.'})
        parsed = timezone.datetime(day.year, day.month, day.day, 23, 59, 59)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def text(data, field, limit, required=False):
    value = str(data.get(field) or '').strip()
    if required and not value:
        raise ValidationError({field: 'This field is required.'})
    if len(value) > limit:
        raise ValidationError({field: f'Keep this under {limit} characters.'})
    return value


def user_label(user):
    if not user:
        return None
    return {'id': user.pk, 'email': user.email, 'name': (user.get_full_name() or user.email or '').strip()}


def find_user(email):
    email = str(email or '').strip()
    users = User.objects.filter(is_active=True, email__iexact=email) if email else User.objects.none()
    if users.count() != 1:
        raise ValidationError({'email': 'Enter the exact email of one active user.'})
    return users.first()


def edition_payload(org):
    edition = acl.edition_for(org)
    return {'edition': edition.edition, 'effective_edition': acl.effective_edition(org),
            'seat_limit': edition.seat_limit, 'max_teams': edition.max_teams,
            'seats_in_use': acl.seats_in_use(org), 'valid_until': iso(edition.valid_until),
            'team_count': Team.objects.filter(organization=org, is_active=True).count()}


def policy_payload(policy, full):
    data = {'require_mfa': policy.require_mfa, 'allow_external_guests': policy.allow_external_guests,
            'guest_max_days': policy.guest_max_days,
            'ai_outputs_require_approval': policy.ai_outputs_require_approval,
            'allow_web_search': policy.allow_web_search,
            'data_residency_region': policy.data_residency_region,
            'updated_at': iso(policy.updated_at) if policy.pk else None}
    if full:
        data.update({'allowed_chat_models': policy.allowed_chat_models,
                     'data_retention_days': policy.data_retention_days,
                     'allowed_ip_ranges': policy.allowed_ip_ranges,
                     'session_timeout_minutes': policy.session_timeout_minutes,
                     'updated_by': user_label(policy.updated_by) if policy.pk else None})
    return data


def member_payload(m):
    now = timezone.now()
    return {'user': user_label(m.user), 'role': m.role, 'expires_at': iso(m.expires_at),
            'is_expired': bool(m.expires_at and m.expires_at <= now),
            'invited_by': user_label(m.invited_by), 'created_at': iso(m.created_at)}


def access_payload(a):
    return {'workspace_id': a.workspace_id, 'workspace_name': a.workspace.name, 'role': a.role,
            'owner': user_label(a.workspace.owner), 'linked_by': user_label(a.linked_by),
            'synced_members': a.grants.count(), 'created_at': iso(a.created_at)}


def team_payload(team, role, caps, detail=False):
    data = {'id': str(team.pk), 'name': team.name, 'slug': team.slug, 'description': team.description,
            'my_role': role, 'can_manage': bool(caps['manage_org'] or role in acl.TEAM_MANAGER_ROLES),
            'can_link': bool(caps['manage_org'] or role in acl.TEAM_LINKER_ROLES),
            'member_count': getattr(team, 'member_count', None), 'created_at': iso(team.created_at)}
    if data['member_count'] is None:
        data['member_count'] = team.memberships.filter(acl.active_q()).count()
    data['project_count'] = getattr(team, 'project_count', None)
    if data['project_count'] is None:
        data['project_count'] = team.workspace_access.count()
    if detail:
        data['members'] = [member_payload(m) for m in team.memberships.select_related('user', 'invited_by')
                           .order_by('role', 'user__email')]
        data['workspaces'] = [access_payload(a) for a in team.workspace_access
                              .select_related('workspace', 'workspace__owner', 'linked_by')
                              .order_by('workspace__name')]
    return data


def annotated(teams):
    # distinct=True: two counts over different joins would otherwise multiply each other.
    return teams.annotate(member_count=Count('memberships', filter=acl.active_q('memberships__'), distinct=True),
                          project_count=Count('workspace_access', distinct=True))


def my_team_roles(user, org):
    return dict(TeamMembership.objects.filter(user=user, team__organization=org)
                .filter(acl.active_q()).values_list('team_id', 'role'))


class EnterpriseAPIView(APIView):
    permission_classes = [IsAuthenticated]
    require_enabled = True

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.enabled = acl.enterprise_enabled()
        if self.require_enabled and not self.enabled:
            raise NotFound('Team & Enterprise features are not enabled on this server.')
        if self.enabled:
            self.org = acl.require_organization(request.user)
            self.caps = acl.capabilities(request.user, self.org)


# ---------------------------------------------------------------- context
class ContextView(EnterpriseAPIView):
    require_enabled = False

    def get(self, request):
        if not self.enabled:
            return Response({'enabled': False})
        org = self.org
        roles = my_team_roles(request.user, org)
        teams = annotated(acl.visible_teams(request.user, org, self.caps))
        return Response({
            'enabled': True,
            'me': request.user.pk,
            'organization': {'id': str(org.pk), 'name': org.name, 'code': org.code},
            'edition': edition_payload(org),
            'policy': policy_payload(acl.policy_for(org),
                                     full=self.caps['manage_policy'] or self.caps['view_audit']),
            'capabilities': self.caps,
            'your_ip': client_ip(request),
            'teams': [team_payload(t, roles.get(t.pk), self.caps) for t in teams],
            'choices': {'team_roles': TEAM_ROLES, 'org_roles': ORG_ADMIN_ROLES,
                        'workspace_roles': WORKSPACE_ROLES, 'editions': EDITION_CHOICES},
        })


# ---------------------------------------------------------------- teams
class TeamListView(EnterpriseAPIView):
    def get(self, request):
        roles = my_team_roles(request.user, self.org)
        teams = annotated(acl.visible_teams(request.user, self.org, self.caps))
        return Response({'results': [team_payload(t, roles.get(t.pk), self.caps) for t in teams]})

    def post(self, request):
        acl.require_edition(self.org, 'team')
        if not self.caps['manage_org']:
            raise PermissionDenied('Only organization admins can create teams.')
        name = text(request.data, 'name', 120, required=True)
        description = text(request.data, 'description', 2000)
        edition = acl.edition_for(self.org)
        with transaction.atomic():
            active = list(Team.objects.select_for_update()
                          .filter(organization=self.org, is_active=True).values_list('pk', flat=True))
            if edition.max_teams and len(active) >= edition.max_teams:
                raise ValidationError({'name': f'Your edition allows {edition.max_teams} teams.'})
            base = slugify(name)[:120] or 'team'
            slug, n = base, 1
            while Team.objects.filter(organization=self.org, slug=slug).exists():
                n += 1
                slug = f'{base}-{n}'
            team = Team.objects.create(organization=self.org, name=name, slug=slug,
                                       description=description, created_by=request.user)
            TeamMembership.objects.create(team=team, user=request.user, role='admin', invited_by=request.user)
            audit(request, 'create', 'EnterpriseTeam', self.org, f'Team {team.name}', {'team_id': str(team.pk)})
        return Response(team_payload(team, 'admin', self.caps, detail=True), status=201)


class TeamDetailView(EnterpriseAPIView):
    def get(self, request, team_id):
        team, role = acl.team_for(request.user, self.org, self.caps, team_id)
        return Response(team_payload(team, role, self.caps, detail=True))

    def patch(self, request, team_id):
        team, role = acl.team_for(request.user, self.org, self.caps, team_id, need='manage')
        changes = {}
        if 'name' in request.data:
            changes['name'] = text(request.data, 'name', 120, required=True)
        if 'description' in request.data:
            changes['description'] = text(request.data, 'description', 2000)
        for field, value in changes.items():
            setattr(team, field, value)
        if changes:
            team.save(update_fields=[*changes, 'updated_at'])
            audit(request, 'update', 'EnterpriseTeam', self.org, f'Team {team.name}',
                  {'team_id': str(team.pk), 'fields': sorted(changes)})
        return Response(team_payload(team, role, self.caps, detail=True))

    def delete(self, request, team_id):
        """Archives the team and removes the AI project access it granted."""
        team, _ = acl.team_for(request.user, self.org, self.caps, team_id, need='manage')
        with transaction.atomic():
            revoked = 0
            for access in list(team.workspace_access.select_related('workspace')):
                revoked += acl.revoke_access(access, request.user)
            team.is_active = False
            team.save(update_fields=['is_active', 'updated_at'])
            audit(request, 'delete', 'EnterpriseTeam', self.org, f'Team {team.name}',
                  {'team_id': str(team.pk), 'revoked_workspace_members': revoked})
        return Response(status=204)


class TeamMembersView(EnterpriseAPIView):
    def post(self, request, team_id):
        acl.require_edition(self.org, 'team')
        team, _ = acl.team_for(request.user, self.org, self.caps, team_id, need='manage')
        role = request.data.get('role') or 'engineer'
        if role not in TEAM_ROLE_CODES:
            raise ValidationError({'role': 'Choose a valid team role.'})
        user = find_user(request.data.get('email'))
        same_org = acl.organization_for(user) == self.org
        expires_at = parse_when(request.data.get('expires_at'), 'expires_at')
        if role == 'guest':
            expires_at = acl.clean_guest_expiry(self.org, expires_at)
        elif not same_org:
            raise ValidationError({'email': 'Only members of your organization can join with this role. '
                                            'Add external people as time-limited guests.'})
        with transaction.atomic():
            Team.objects.select_for_update().filter(pk=team.pk).first()
            acl.check_seat(self.org, user)
            membership, created = TeamMembership.objects.update_or_create(
                team=team, user=user,
                defaults={'role': role, 'expires_at': expires_at, 'invited_by': request.user})
            audit(request, 'role_assign', 'EnterpriseTeamMember', self.org, f'{user.email} in {team.name}',
                  {'team_id': str(team.pk), 'member_user_id': user.pk, 'role': role,
                   'expires_at': iso(expires_at), 'external': not same_org})
        acl.sync_user_in_team(team, user, request.user)
        return Response(member_payload(membership), status=201 if created else 200)


class TeamMemberDetailView(EnterpriseAPIView):
    def _membership(self, team, user_id):
        membership = team.memberships.select_related('user', 'invited_by').filter(user_id=user_id).first()
        if not membership:
            raise NotFound('Member not found.')
        return membership

    def _guard_last_admin(self, team, membership, new_role=None):
        if membership.role == 'admin' and new_role != 'admin':
            if not team.memberships.filter(role='admin').exclude(pk=membership.pk).exists():
                raise ValidationError({'role': 'A team needs at least one team admin.'})

    def patch(self, request, team_id, user_id):
        team, _ = acl.team_for(request.user, self.org, self.caps, team_id, need='manage')
        membership = self._membership(team, user_id)
        role = request.data.get('role', membership.role)
        if role not in TEAM_ROLE_CODES:
            raise ValidationError({'role': 'Choose a valid team role.'})
        expires_at = (parse_when(request.data.get('expires_at'), 'expires_at')
                      if 'expires_at' in request.data else membership.expires_at)
        if role == 'guest':
            expires_at = acl.clean_guest_expiry(self.org, expires_at)
        elif acl.organization_for(membership.user) != self.org:
            raise ValidationError({'role': 'External people can only be guests.'})
        self._guard_last_admin(team, membership, role)
        before = membership.role
        membership.role, membership.expires_at = role, expires_at
        membership.save(update_fields=['role', 'expires_at'])
        audit(request, 'update', 'EnterpriseTeamMember', self.org, f'{membership.user.email} in {team.name}',
              {'team_id': str(team.pk), 'member_user_id': membership.user_id, 'from_role': before,
               'to_role': role, 'expires_at': iso(expires_at)})
        acl.sync_user_in_team(team, membership.user, request.user)
        return Response(member_payload(membership))

    def delete(self, request, team_id, user_id):
        leaving = str(user_id) == str(request.user.pk)
        team, _ = acl.team_for(request.user, self.org, self.caps, team_id,
                               need=None if leaving else 'manage')
        membership = self._membership(team, user_id)
        self._guard_last_admin(team, membership)
        user = membership.user
        membership.delete()
        audit(request, 'role_revoke', 'EnterpriseTeamMember', self.org, f'{user.email} in {team.name}',
              {'team_id': str(team.pk), 'member_user_id': user.pk, 'self_removal': leaving})
        acl.sync_user_in_team(team, user, request.user)
        return Response(status=204)


# ---------------------------------------------------------------- team <-> AI projects
class MyWorkspacesView(EnterpriseAPIView):
    """AI projects the caller owns, i.e. the ones they may share with a team."""

    def get(self, request):
        from apps.core.ai_workspace_models import AIWorkspace
        rows = AIWorkspace.objects.filter(owner=request.user, archived_at__isnull=True).order_by('name')
        return Response({'results': [{'id': w.pk, 'name': w.name, 'visibility': w.visibility} for w in rows]})


class TeamWorkspacesView(EnterpriseAPIView):
    def post(self, request, team_id):
        from apps.core.ai_workspace_models import AIWorkspace
        acl.require_edition(self.org, 'team')
        team, _ = acl.team_for(request.user, self.org, self.caps, team_id, need='link')
        role = request.data.get('role') or 'viewer'
        if role not in WS_ROLE_CODES:
            raise ValidationError({'role': 'Choose editor or viewer.'})
        workspace = AIWorkspace.objects.filter(pk=request.data.get('workspace_id'),
                                               archived_at__isnull=True).first()
        # Only the owner may share an AI project: org admins cannot expose someone else's private work.
        if not workspace or workspace.owner_id != request.user.pk:
            raise ValidationError({'workspace_id': 'Choose an AI project that you own.'})
        try:
            with transaction.atomic():
                access = TeamWorkspaceAccess.objects.create(team=team, workspace=workspace, role=role,
                                                            linked_by=request.user)
        except IntegrityError:
            raise ValidationError({'workspace_id': 'This AI project is already shared with the team.'})
        counts = acl.sync_access(access, request.user)
        audit(request, 'permission_grant', 'EnterpriseTeamWorkspace', self.org,
              f'{workspace.name} -> {team.name}',
              {'team_id': str(team.pk), 'ai_workspace_id': workspace.pk, 'role': role, 'sync': counts})
        access = (TeamWorkspaceAccess.objects.select_related('workspace', 'workspace__owner', 'linked_by')
                  .get(pk=access.pk))
        return Response({**access_payload(access), 'sync': counts}, status=201)


class TeamWorkspaceDetailView(EnterpriseAPIView):
    def _access(self, request, team_id, workspace_id):
        team, role = acl.team_for(request.user, self.org, self.caps, team_id)
        access = (team.workspace_access.select_related('workspace', 'workspace__owner', 'linked_by')
                  .filter(workspace_id=workspace_id).first())
        if not access:
            raise NotFound('This AI project is not shared with the team.')
        allowed = (self.caps['manage_org'] or role in acl.TEAM_LINKER_ROLES
                   or access.workspace.owner_id == request.user.pk)
        if not allowed:
            raise PermissionDenied('Only team admins, lead engineers or the project owner can change this.')
        return team, access

    def patch(self, request, team_id, workspace_id):
        team, access = self._access(request, team_id, workspace_id)
        role = request.data.get('role')
        if role not in WS_ROLE_CODES:
            raise ValidationError({'role': 'Choose editor or viewer.'})
        if role == 'editor' and access.workspace.owner_id != request.user.pk:
            raise PermissionDenied('Only the project owner can give a team editor access.')
        access.role = role
        access.save(update_fields=['role'])
        counts = acl.sync_access(access, request.user)
        audit(request, 'update', 'EnterpriseTeamWorkspace', self.org, f'{access.workspace.name} -> {team.name}',
              {'team_id': str(team.pk), 'ai_workspace_id': access.workspace_id, 'role': role, 'sync': counts})
        return Response({**access_payload(access), 'sync': counts})

    def delete(self, request, team_id, workspace_id):
        team, access = self._access(request, team_id, workspace_id)
        name, ws_id = access.workspace.name, access.workspace_id
        removed = acl.revoke_access(access, request.user)
        audit(request, 'permission_revoke', 'EnterpriseTeamWorkspace', self.org, f'{name} -> {team.name}',
              {'team_id': str(team.pk), 'ai_workspace_id': ws_id, 'members_revoked': removed})
        return Response(status=204)


# ---------------------------------------------------------------- organization governance
class EditionView(EnterpriseAPIView):
    def get(self, request):
        return Response(edition_payload(self.org))

    def patch(self, request):
        if not self.caps['platform_admin']:
            raise PermissionDenied('Only platform administrators can change the edition.')
        edition = acl.edition_for(self.org)
        changes = {}
        if 'edition' in request.data:
            if request.data['edition'] not in EDITION_CODES:
                raise ValidationError({'edition': 'Choose individual, team or enterprise.'})
            changes['edition'] = request.data['edition']
        for field in ('seat_limit', 'max_teams'):
            if field in request.data:
                value = request.data[field]
                if value in (None, ''):
                    changes[field] = None
                    continue
                try:
                    value = int(value)
                    if not 1 <= value <= 100000:
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValidationError({field: 'Enter a number from 1 to 100000, or leave blank.'})
                changes[field] = value
        if 'valid_until' in request.data:
            changes['valid_until'] = parse_when(request.data['valid_until'], 'valid_until')
        before = {k: getattr(edition, k) for k in changes}
        for field, value in changes.items():
            setattr(edition, field, value)
        edition.updated_by = request.user
        edition.save()
        audit(request, 'update', 'EnterpriseEdition', self.org, f'{self.org.name} edition',
              {'from': {k: None if v is None else str(v) for k, v in before.items()},
               'to': {k: None if v is None else str(v) for k, v in changes.items()}})
        return Response(edition_payload(self.org))


class PolicyView(EnterpriseAPIView):
    def get(self, request):
        full = self.caps['manage_policy'] or self.caps['view_audit']
        return Response(policy_payload(acl.policy_for(self.org), full=full))

    def patch(self, request):
        acl.require_edition(self.org, 'enterprise')
        if not self.caps['manage_policy']:
            raise PermissionDenied('Only organization owners, admins or security officers can change policy.')
        changes = acl.clean_policy(request.data)
        if changes.get('allowed_ip_ranges'):
            # Lock-out guard: whoever saves the list must be inside it.
            from .audit import client_ip
            from .enforcement import ip_in_ranges
            here = client_ip(request)
            if not ip_in_ranges(here, changes['allowed_ip_ranges']):
                raise ValidationError({'allowed_ip_ranges':
                                       f"Your current address ({here or 'unknown'}) isn't in these ranges, so saving "
                                       'would lock you out. Add it to the list first.'})
        with transaction.atomic():
            policy, _ = OrganizationPolicy.objects.select_for_update().get_or_create(organization=self.org)
            before = {k: getattr(policy, k) for k in changes}
            for field, value in changes.items():
                setattr(policy, field, value)
            policy.updated_by = request.user
            policy.save()
            audit(request, 'update', 'EnterprisePolicy', self.org, f'{self.org.name} policy',
                  {'changes': {k: {'from': before[k], 'to': v} for k, v in changes.items() if before[k] != v}})
        return Response(policy_payload(policy, full=True))


class RetentionPreviewView(EnterpriseAPIView):
    """What the retention policy would delete now. Read-only; deletion runs only from the command."""

    def get(self, request):
        if not (self.caps['manage_policy'] or self.caps['view_audit']):
            raise PermissionDenied('Only organization admins, security officers or auditors can see this.')
        from . import retention
        return Response(retention.preview(self.org, acl.policy_for(self.org).data_retention_days))


class OrgAdminsView(EnterpriseAPIView):
    def get(self, request):
        if not (self.caps['manage_org'] or self.caps['view_audit']):
            raise PermissionDenied('Organization admin access is required.')
        rows = OrganizationAdmin.objects.filter(organization=self.org).select_related('user', 'granted_by')
        return Response({'results': [{'id': r.pk, 'user': user_label(r.user), 'role': r.role,
                                      'granted_by': user_label(r.granted_by), 'created_at': iso(r.created_at)}
                                     for r in rows.order_by('role', 'user__email')]})

    def post(self, request):
        if not self.caps['manage_org']:
            raise PermissionDenied('Organization admin access is required.')
        role = request.data.get('role')
        if role not in ORG_ROLE_CODES:
            raise ValidationError({'role': 'Choose owner, admin, security or auditor.'})
        if role == 'owner' and not self.caps['grant_owner']:
            raise PermissionDenied('Only an organization owner can add another owner.')
        user = find_user(request.data.get('email'))
        if acl.organization_for(user) != self.org:
            raise ValidationError({'email': 'This user is not in your organization.'})
        row, created = OrganizationAdmin.objects.get_or_create(
            organization=self.org, user=user, role=role, defaults={'granted_by': request.user})
        if created:
            audit(request, 'role_assign', 'EnterpriseOrgAdmin', self.org, f'{user.email} as {role}',
                  {'member_user_id': user.pk, 'role': role})
        return Response({'id': row.pk, 'user': user_label(user), 'role': role}, status=201 if created else 200)


class OrgAdminDetailView(EnterpriseAPIView):
    def delete(self, request, admin_id):
        if not self.caps['manage_org']:
            raise PermissionDenied('Organization admin access is required.')
        row = OrganizationAdmin.objects.filter(organization=self.org, pk=admin_id).select_related('user').first()
        if not row:
            raise NotFound('Admin role not found.')
        if row.role == 'owner':
            if not self.caps['grant_owner']:
                raise PermissionDenied('Only an organization owner can remove an owner.')
            if not OrganizationAdmin.objects.filter(organization=self.org, role='owner').exclude(pk=row.pk).exists():
                raise ValidationError({'role': 'An organization needs at least one owner.'})
        email, role, user_id = row.user.email, row.role, row.user_id
        row.delete()
        audit(request, 'role_revoke', 'EnterpriseOrgAdmin', self.org, f'{email} as {role}',
              {'member_user_id': user_id, 'role': role})
        return Response(status=204)


# ---------------------------------------------------------------- audit log
# Audit categories used by the filters and the summary.
SECURITY_ACTIONS = ('login', 'logout', 'role_assign', 'role_revoke', 'role_set_primary', 'permission_grant',
                    'permission_revoke', 'password_change', 'password_reset', 'reset_password', 'mfa_enable',
                    'mfa_disable', 'file_download')
ROUTINE_ACTIONS = ('data_access', 'read')
TRUE_VALUES = ('1', 'true', 'yes', 'on')


def org_audit_rows(org):
    """Every audit event of the organization: its members' events plus Team & Enterprise events about it.
    A sub-query (not a join) so rows are never duplicated and aggregates stay correct."""
    from apps.rbac.models import AuditLog, UserProfile
    members = UserProfile.objects.filter(organization=org).values('user_id')
    return AuditLog.objects.filter(Q(user_id__in=members) | Q(metadata__organization_id=str(org.pk)))


def security_q():
    return Q(action__in=SECURITY_ACTIONS) | Q(metadata__module='enterprise')


def audit_queryset(org, params, apply_period=True):
    rows = org_audit_rows(org)
    if params.get('action'):
        rows = rows.filter(action=params['action'])
    if params.get('resource_type'):
        rows = rows.filter(resource_type__iexact=params['resource_type'])
    if params.get('user'):
        rows = rows.filter(user_email__icontains=params['user'][:120])
    if params.get('q'):
        term = params['q'][:120]
        rows = rows.filter(Q(user_email__icontains=term) | Q(resource_repr__icontains=term)
                           | Q(resource_type__icontains=term) | Q(action__icontains=term))
    if str(params.get('failed', '')).lower() in TRUE_VALUES:
        rows = rows.filter(success=False)
    if str(params.get('security', '')).lower() in TRUE_VALUES:
        rows = rows.filter(security_q())
    if str(params.get('hide_routine', '')).lower() in TRUE_VALUES:
        rows = rows.exclude(action__in=ROUTINE_ACTIONS)
    if apply_period and params.get('since'):
        rows = rows.filter(timestamp__gte=parse_when(params['since'], 'since'))
    if apply_period and params.get('until'):
        rows = rows.filter(timestamp__lte=parse_when(params['until'], 'until'))
    return rows.order_by('-timestamp')


def audit_row(r):
    return {'id': str(r.id), 'timestamp': iso(r.timestamp), 'user_email': r.user_email, 'action': r.action,
            'resource_type': r.resource_type, 'resource_repr': r.resource_repr, 'ip_address': r.ip_address,
            'success': r.success, 'metadata': r.metadata, 'changes': r.changes,
            'user_agent': (r.user_agent or '')[:300], 'error_message': (r.error_message or '')[:1000],
            'is_security': r.action in SECURITY_ACTIONS or (r.metadata or {}).get('module') == 'enterprise'}


def safe_cell(value):
    """Stops spreadsheet formula injection when the CSV is opened in Excel."""
    value = '' if value is None else str(value)
    return "'" + value if value[:1] in ('=', '+', '-', '@', '\t', '\r') else value


class AuditLogView(EnterpriseAPIView):
    def get(self, request):
        acl.require_edition(self.org, 'enterprise')
        if not self.caps['view_audit']:
            raise PermissionDenied('Audit access is required.')
        rows = audit_queryset(self.org, request.query_params)
        try:
            page = max(1, int(request.query_params.get('page', 1)))
            size = min(200, max(1, int(request.query_params.get('page_size', 50))))
        except ValueError:
            raise ValidationError({'page': 'Use whole numbers for page and page_size.'})
        total = rows.count()
        chunk = rows[(page - 1) * size: page * size]
        return Response({'count': total, 'page': page, 'page_size': size,
                         'results': [audit_row(r) for r in chunk]})


class AuditSummaryView(EnterpriseAPIView):
    """Totals, a daily series and top lists for the filtered period (default: last 30 days)."""

    def get(self, request):
        from django.db.models.functions import TruncDate
        acl.require_edition(self.org, 'enterprise')
        if not self.caps['view_audit']:
            raise PermissionDenied('Audit access is required.')
        params = request.query_params.copy()
        if not params.get('since'):
            params['since'] = (timezone.now() - timedelta(days=30)).isoformat()
        rows = audit_queryset(self.org, params).order_by()
        period = audit_queryset(self.org, {k: params.get(k) for k in ('since', 'until')}).order_by()

        def top(field, n):
            return [{'value': r[field] or '', 'count': r['n']}
                    for r in rows.values(field).annotate(n=Count('id')).order_by('-n')[:n]]

        return Response({
            'since': params['since'], 'until': params.get('until') or None,
            'total': rows.count(),
            'failed': rows.filter(success=False).count(),
            'security': rows.filter(security_q()).count(),
            'people': rows.exclude(user_email='').values('user_email').distinct().count(),
            'by_day': [{'date': r['day'].isoformat(), 'count': r['n']} for r in
                       rows.annotate(day=TruncDate('timestamp')).values('day').annotate(n=Count('id')).order_by('day')
                       if r['day']],
            'top_actions': top('action', 6),
            'top_users': top('user_email', 5),
            'top_resources': top('resource_type', 5),
            # Options for the filter dropdowns: everything seen in the period, not just the filtered rows.
            'actions': sorted({a for a in period.values_list('action', flat=True).distinct()[:100] if a}),
            'resource_types': sorted({r for r in period.values_list('resource_type', flat=True).distinct()[:200] if r}),
        })


class OrgModelsView(EnterpriseAPIView):
    """Every chat model configured on this server, so admins can build the approved-model list."""

    def get(self, request):
        if not (self.caps['manage_policy'] or self.caps['view_audit']):
            raise PermissionDenied('Only organization admins, security officers or auditors can see this.')
        from apps.core.ai_workspace_views import chat_models
        return Response({'models': chat_models()})


class AuditLogExportView(EnterpriseAPIView):
    def get(self, request):
        acl.require_edition(self.org, 'enterprise')
        if not self.caps['view_audit']:
            raise PermissionDenied('Audit access is required.')
        rows = audit_queryset(self.org, request.query_params)[:MAX_EXPORT_ROWS]
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        stamp = timezone.now().strftime('%Y%m%d-%H%M')
        response['Content-Disposition'] = f'attachment; filename="radai-audit-{slugify(self.org.code)}-{stamp}.csv"'
        writer = csv.writer(response)
        writer.writerow(['timestamp', 'user_email', 'action', 'resource_type', 'resource', 'ip_address', 'success'])
        count = 0
        for r in rows:
            writer.writerow([safe_cell(v) for v in (iso(r.timestamp), r.user_email, r.action, r.resource_type,
                                                    r.resource_repr, r.ip_address, r.success)])
            count += 1
        audit(request, 'file_download', 'EnterpriseAuditExport', self.org, 'Audit log CSV export',
              {'rows': count, 'filters': {k: v[:120] for k, v in request.query_params.items()}})
        return response
