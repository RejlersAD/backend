import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

EDITIONS = [('individual', 'Individual'), ('team', 'Team'), ('enterprise', 'Enterprise')]
ADMIN_ROLES = [('owner', 'Organization owner'), ('admin', 'Organization admin'),
               ('security', 'Security officer'), ('auditor', 'Auditor (read-only)')]
TEAM_ROLES = [('admin', 'Team admin'), ('lead', 'Lead engineer (approver)'), ('engineer', 'Engineer'),
              ('reviewer', 'Reviewer (read & comment)'), ('guest', 'External guest (time-limited)')]
WS_ROLES = [('editor', 'Editor'), ('viewer', 'Viewer')]


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('rbac', '0070_executive_all_tab_permissions'),
        ('core', '0023_ai_workspace_attachment_evidence'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrganizationEdition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('edition', models.CharField(choices=EDITIONS, default='individual', max_length=16)),
                ('seat_limit', models.PositiveIntegerField(blank=True, null=True, help_text='Blank = unlimited team seats.')),
                ('max_teams', models.PositiveIntegerField(blank=True, null=True, help_text='Blank = unlimited teams.')),
                ('valid_until', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='enterprise_edition', to='rbac.organization')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'enterprise_org_editions'},
        ),
        migrations.CreateModel(
            name='OrganizationPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('require_mfa', models.BooleanField(default=False)),
                ('allow_external_guests', models.BooleanField(default=False)),
                ('guest_max_days', models.PositiveSmallIntegerField(default=30)),
                ('ai_outputs_require_approval', models.BooleanField(default=True, help_text='AI findings stay draft until a named engineer approves them.')),
                ('allowed_chat_models', models.JSONField(blank=True, default=list, help_text='Approved model ids. Empty = any configured model.')),
                ('data_retention_days', models.PositiveIntegerField(blank=True, null=True, help_text='Blank = keep.')),
                ('data_residency_region', models.CharField(blank=True, max_length=40, help_text='e.g. uae')),
                ('allowed_ip_ranges', models.JSONField(blank=True, default=list, help_text='CIDR list. Empty = any.')),
                ('session_timeout_minutes', models.PositiveIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='enterprise_policy', to='rbac.organization')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'enterprise_org_policies'},
        ),
        migrations.CreateModel(
            name='OrganizationAdmin',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=ADMIN_ROLES, max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='enterprise_admins', to='rbac.organization')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='enterprise_admin_roles', to=settings.AUTH_USER_MODEL)),
                ('granted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'enterprise_org_admins'},
        ),
        migrations.AddConstraint(
            model_name='organizationadmin',
            constraint=models.UniqueConstraint(fields=('organization', 'user', 'role'), name='enterprise_org_admin_unique'),
        ),
        migrations.CreateModel(
            name='Team',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=120)),
                ('slug', models.SlugField(max_length=140)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='teams', to='rbac.organization')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'enterprise_teams', 'ordering': ['name']},
        ),
        migrations.AddConstraint(
            model_name='team',
            constraint=models.UniqueConstraint(fields=('organization', 'slug'), name='enterprise_team_slug_unique'),
        ),
        migrations.AddIndex(
            model_name='team',
            index=models.Index(fields=['organization', 'is_active'], name='enterprise__organiz_7c1e2a_idx'),
        ),
        migrations.CreateModel(
            name='TeamMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=TEAM_ROLES, default='engineer', max_length=12)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='enterprise.team')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='enterprise_team_memberships', to=settings.AUTH_USER_MODEL)),
                ('invited_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'enterprise_team_memberships'},
        ),
        migrations.AddConstraint(
            model_name='teammembership',
            constraint=models.UniqueConstraint(fields=('team', 'user'), name='enterprise_team_member_unique'),
        ),
        migrations.AddIndex(
            model_name='teammembership',
            index=models.Index(fields=['user', 'expires_at'], name='enterprise__user_id_3f9b1d_idx'),
        ),
        migrations.CreateModel(
            name='TeamWorkspaceAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=WS_ROLES, default='viewer', max_length=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_access', to='enterprise.team')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='team_access', to='core.aiworkspace')),
                ('linked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'enterprise_team_workspace_access'},
        ),
        migrations.AddConstraint(
            model_name='teamworkspaceaccess',
            constraint=models.UniqueConstraint(fields=('team', 'workspace'), name='enterprise_team_workspace_unique'),
        ),
        migrations.CreateModel(
            name='TeamWorkspaceGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('access', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='grants', to='enterprise.teamworkspaceaccess')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='team_grants', to='core.aiworkspacemember')),
            ],
            options={'db_table': 'enterprise_team_workspace_grants'},
        ),
        migrations.AddConstraint(
            model_name='teamworkspacegrant',
            constraint=models.UniqueConstraint(fields=('access', 'member'), name='enterprise_team_grant_unique'),
        ),
    ]
