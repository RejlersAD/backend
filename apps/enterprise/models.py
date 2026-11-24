"""Team & Enterprise tier models.

Every record hangs off ``rbac.Organization`` so data is tenant-scoped by
construction. Querysets in views always filter by the caller's organization.
"""
import uuid

from django.conf import settings
from django.db import models

EDITION_INDIVIDUAL = 'individual'
EDITION_TEAM = 'team'
EDITION_ENTERPRISE = 'enterprise'
EDITION_CHOICES = [
    (EDITION_INDIVIDUAL, 'Individual'),
    (EDITION_TEAM, 'Team'),
    (EDITION_ENTERPRISE, 'Enterprise'),
]
EDITION_RANK = {EDITION_INDIVIDUAL: 0, EDITION_TEAM: 1, EDITION_ENTERPRISE: 2}

ORG_ADMIN_ROLES = [
    ('owner', 'Organization owner'),
    ('admin', 'Organization admin'),
    ('security', 'Security officer'),
    ('auditor', 'Auditor (read-only)'),
]

TEAM_ROLES = [
    ('admin', 'Team admin'),
    ('lead', 'Lead engineer (approver)'),
    ('engineer', 'Engineer'),
    ('reviewer', 'Reviewer (read & comment)'),
    ('guest', 'External guest (time-limited)'),
]
TEAM_MANAGER_ROLES = ('admin',)
TEAM_LINKER_ROLES = ('admin', 'lead')

WORKSPACE_ROLES = [('editor', 'Editor'), ('viewer', 'Viewer')]


class OrganizationEdition(models.Model):
    """Which product tier an organization is on, plus its limits."""
    organization = models.OneToOneField('rbac.Organization', on_delete=models.CASCADE,
                                        related_name='enterprise_edition')
    edition = models.CharField(max_length=16, choices=EDITION_CHOICES, default=EDITION_INDIVIDUAL)
    seat_limit = models.PositiveIntegerField(null=True, blank=True, help_text='Blank = unlimited team seats.')
    max_teams = models.PositiveIntegerField(null=True, blank=True, help_text='Blank = unlimited teams.')
    valid_until = models.DateTimeField(null=True, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'enterprise_org_editions'

    def __str__(self):
        return f'{self.organization} – {self.get_edition_display()}'


class OrganizationPolicy(models.Model):
    """Security & governance policy for one organization."""
    organization = models.OneToOneField('rbac.Organization', on_delete=models.CASCADE,
                                        related_name='enterprise_policy')
    require_mfa = models.BooleanField(default=False)
    allow_external_guests = models.BooleanField(default=False)
    guest_max_days = models.PositiveSmallIntegerField(default=30)
    ai_outputs_require_approval = models.BooleanField(
        default=True, help_text='AI findings stay draft until a named engineer approves them.')
    allowed_chat_models = models.JSONField(default=list, blank=True,
                                           help_text='Approved model ids. Empty = any configured model.')
    allow_web_search = models.BooleanField(
        default=False, help_text='Let RADAI Intelligence search the public web (Claude only). Off by default.')
    data_retention_days = models.PositiveIntegerField(null=True, blank=True, help_text='Blank = keep.')
    data_residency_region = models.CharField(max_length=40, blank=True, help_text='e.g. uae')
    allowed_ip_ranges = models.JSONField(default=list, blank=True, help_text='CIDR list. Empty = any.')
    session_timeout_minutes = models.PositiveIntegerField(null=True, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'enterprise_org_policies'


class OrganizationAdmin(models.Model):
    organization = models.ForeignKey('rbac.Organization', on_delete=models.CASCADE,
                                     related_name='enterprise_admins')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='enterprise_admin_roles')
    role = models.CharField(max_length=16, choices=ORG_ADMIN_ROLES)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enterprise_org_admins'
        constraints = [models.UniqueConstraint(fields=['organization', 'user', 'role'],
                                               name='enterprise_org_admin_unique')]


class Team(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('rbac.Organization', on_delete=models.CASCADE, related_name='teams')
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'enterprise_teams'
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['organization', 'slug'],
                                               name='enterprise_team_slug_unique')]
        indexes = [models.Index(fields=['organization', 'is_active'], name='enterprise__organiz_7c1e2a_idx')]

    def __str__(self):
        return self.name


class TeamMembership(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='enterprise_team_memberships')
    role = models.CharField(max_length=12, choices=TEAM_ROLES, default='engineer')
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enterprise_team_memberships'
        constraints = [models.UniqueConstraint(fields=['team', 'user'], name='enterprise_team_member_unique')]
        indexes = [models.Index(fields=['user', 'expires_at'], name='enterprise__user_id_3f9b1d_idx')]


class TeamWorkspaceAccess(models.Model):
    """A team's access to an existing RADAI Intelligence AI project."""
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='workspace_access')
    workspace = models.ForeignKey('core.AIWorkspace', on_delete=models.CASCADE, related_name='team_access')
    role = models.CharField(max_length=12, choices=WORKSPACE_ROLES, default='viewer')
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enterprise_team_workspace_access'
        constraints = [models.UniqueConstraint(fields=['team', 'workspace'],
                                               name='enterprise_team_workspace_unique')]


class TeamWorkspaceGrant(models.Model):
    """Marks an ``AIWorkspaceMember`` row as created by team access.

    Rows without a grant were added by hand in RADAI Intelligence and are never
    modified or removed by team sync.
    """
    access = models.ForeignKey(TeamWorkspaceAccess, on_delete=models.CASCADE, related_name='grants')
    member = models.ForeignKey('core.AIWorkspaceMember', on_delete=models.CASCADE, related_name='team_grants')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enterprise_team_workspace_grants'
        constraints = [models.UniqueConstraint(fields=['access', 'member'],
                                               name='enterprise_team_grant_unique')]
