"""Explicit identity registration never changes existing project business data."""

from io import StringIO
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.core.project_models import Project, ProjectMember
from apps.file_replica.models import ReplicaEntry, ReplicaScope, ReplicaSource
from apps.file_replica.paths import path_key
from apps.file_replica.project_registration import register_server_projects
from apps.rbac.models import Module, Organization, Permission, UserPermissionOverride, UserProfile
from apps.rbac.module_actions import ensure_module_actions


class ServerProjectRegistrationTests(TestCase):
    def setUp(self):
        cache.clear()
        organization = Organization.objects.create(code='register-server-projects', name='Project registration tests')
        self.actor = get_user_model().objects.create_user(username='register-admin', email='register-admin@example.test', is_superuser=True)
        UserProfile.objects.get_or_create(user=self.actor, defaults={'organization': organization})
        module, _ = Module.objects.update_or_create(code='project_control', defaults={'name': 'Project Control', 'is_active': True})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        self.source = ReplicaSource.objects.create(name='Project folder catalogue', root_path=r'\\test-server\Projects', enabled=False)

    def folder(self, name, *, status='indexed', with_entry=True, is_directory=True, mapped=None, published=False):
        scope = ReplicaScope.objects.create(source=self.source, relative_path=name, path_key=path_key(name), project=mapped, access_enabled=published)
        if with_entry:
            ReplicaEntry.objects.create(
                source=self.source, scope=scope, relative_path=name,
                normalized_path=name.casefold(), path_key=path_key(name),
                parent_path='', name=name, is_directory=is_directory, status=status,
            )
        return scope

    def register(self, apply=False, actor=None):
        return register_server_projects(source_id=self.source.pk, actor=actor or self.actor, apply=apply)

    def test_dry_run_is_read_only_and_apply_retry_is_idempotent(self):
        scope = self.folder('5900100-New compressor project')
        with CaptureQueriesContext(connection) as queries:
            preview = self.register()
        self.assertEqual(preview['counts']['would_create'], 1)
        self.assertEqual(preview['counts']['would_map'], 1)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for query in queries))
        self.assertFalse(Project.objects.exists())
        first = self.register(apply=True)
        self.assertEqual(first['counts']['created'], 1)
        project = Project.objects.get(code='5900100')
        self.assertEqual(project.name, 'New compressor project')
        self.assertEqual(project.status, 'planning')
        self.assertEqual(project.owner, self.actor)
        self.assertFalse(project.custom_fields['control_setup']['operational_status_confirmed'])
        self.assertEqual(project.custom_fields['registration_origin']['scope_id'], str(scope.pk))
        self.assertIsNone(project.start_date)
        self.assertIsNone(project.end_date)
        self.assertIsNone(project.budget)
        self.assertFalse(ProjectMember.objects.exists())
        scope.refresh_from_db()
        self.assertEqual(scope.project_id, project.pk)
        self.assertFalse(scope.access_enabled)
        before = Project.objects.values().get(pk=project.pk)
        second = self.register(apply=True)
        self.assertEqual(second['counts']['created'], 0)
        self.assertEqual(second['counts']['already_linked'], 1)
        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(before, Project.objects.values().get(pk=project.pk))

    def test_reuses_exact_project_preserving_every_field_and_publication(self):
        project = Project.objects.create(code='5900100', name='Approved project name', status='completed', budget='1000', progress=77, custom_fields={'existing': 'value'})
        scope = self.folder('5900100-Different directory name', published=True)
        before = Project.objects.values().get(pk=project.pk)
        report = self.register(apply=True)
        self.assertEqual(report['counts']['reused'], 1)
        self.assertEqual(report['counts']['mapped'], 1)
        self.assertEqual(before, Project.objects.values().get(pk=project.pk))
        scope.refresh_from_db()
        self.assertEqual(scope.project_id, project.pk)
        self.assertTrue(scope.access_enabled)

    def test_skips_generic_missing_failed_absent_nondirectory_and_excluded(self):
        cases = [
            (self.folder('General documents'), 'no_numeric_project_code'),
            (self.folder('5900101-Missing', status='missing'), 'folder_missing_or_failed'),
            (self.folder('5900102-Failed', status='failed'), 'folder_missing_or_failed'),
            (self.folder('5900103-Old scope', with_entry=False), 'current_root_folder_not_found'),
            (self.folder('5900104-File', is_directory=False), 'current_root_folder_not_found'),
            (self.folder('5900105-Private'), 'outside_source_scope'),
        ]
        self.source.excluded_paths = ['5900105-Private']
        self.source.save(update_fields=['excluded_paths'])
        report = self.register(apply=True)
        by_id = {row['scope_id']: row for row in report['rows']}
        for scope, reason in cases:
            self.assertEqual(by_id[str(scope.pk)]['reason'], reason)
        self.assertEqual(report['counts']['skipped'], len(cases))
        self.assertFalse(Project.objects.exists())

    def test_inclusion_restrictions_apply_without_resetting_source_configuration(self):
        allowed = self.folder('5900100-Allowed')
        self.folder('5900101-Outside included paths')
        self.source.included_paths = [allowed.relative_path + '/Design']
        self.source.save(update_fields=['included_paths'])
        before = ReplicaSource.objects.values().get(pk=self.source.pk)
        report = self.register(apply=True)
        self.assertEqual(report['counts']['created'], 1)
        self.assertEqual(report['counts']['skipped'], 1)
        self.assertEqual(before, ReplicaSource.objects.values().get(pk=self.source.pk))

    def test_duplicates_deleted_codes_and_divergent_mappings_are_reported_without_edits(self):
        self.folder('5900100-First version')
        self.folder('5900100-Second version')
        deleted = Project.objects.create(code='5900101', name='Deleted project', is_deleted=True)
        self.folder('5900101-Removed')
        mapped = Project.objects.create(code='5900999', name='Explicit existing project')
        divergent = self.folder('5900102-Divergent directory', mapped=mapped, published=True)
        self.folder('5900103-Deleted mapped', mapped=deleted)
        before = list(Project.objects.order_by('pk').values())
        report = self.register(apply=True)
        reasons = [row['reason'] for row in report['rows']]
        self.assertEqual(reasons.count('duplicate_folder_project_code'), 2)
        self.assertIn('project_code_is_deleted', reasons)
        self.assertIn('conflicting_scope_mapping', reasons)
        self.assertIn('mapped_project_deleted', reasons)
        self.assertEqual(report['counts']['skipped'], 5)
        self.assertEqual(before, list(Project.objects.order_by('pk').values()))
        divergent.refresh_from_db()
        self.assertEqual(divergent.project_id, mapped.pk)
        self.assertTrue(divergent.access_enabled)

    def test_numeric_codes_and_code_only_names_are_preserved_exactly(self):
        self.folder('0000123-Project with leading zeros')
        self.folder('5900100')
        self.register(apply=True)
        self.assertEqual(Project.objects.get(code='0000123').name, 'Project with leading zeros')
        self.assertEqual(Project.objects.get(code='5900100').name, '5900100')

    def test_same_project_discovered_by_another_source_is_reused_without_duplicates(self):
        self.folder('5900100-Original directory')
        self.register(apply=True)
        existing = Project.objects.get(code='5900100')
        before = Project.objects.values().get(pk=existing.pk)
        self.source = ReplicaSource.objects.create(name='Second server', root_path=r'\\second-server\Projects')
        second_scope = self.folder('5900100-Second directory description')
        report = self.register(apply=True)
        self.assertEqual(report['counts']['reused'], 1)
        self.assertEqual(report['counts']['created'], 0)
        self.assertEqual(Project.objects.filter(code='5900100').count(), 1)
        self.assertEqual(before, Project.objects.values().get(pk=existing.pk))
        second_scope.refresh_from_db()
        self.assertEqual(second_scope.project_id, existing.pk)

    def test_actor_must_have_admin_and_effective_create_permission(self):
        self.folder('5900100-Project')
        outsider = get_user_model().objects.create_user(username='register-outsider', email='register-outsider@example.test', is_staff=True)
        with self.assertRaises(PermissionDenied):
            self.register(apply=True, actor=outsider)
        permission = Permission.objects.filter(module__code='project_control', action='create', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.actor.rbac_profile, permission=permission, allowed=False)
        with self.assertRaises(PermissionDenied):
            self.register(apply=True)
        self.assertFalse(Project.objects.exists())

    def test_command_defaults_to_dry_run_and_accepts_actor_email_or_id(self):
        self.folder('5900100-Project')
        output = StringIO()
        call_command('register_server_projects', source=str(self.source.pk), actor=self.actor.email, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['mode'], 'dry_run')
        self.assertFalse(Project.objects.exists())
        output = StringIO()
        call_command('register_server_projects', source=str(self.source.pk), actor=str(self.actor.pk), apply=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['counts']['created'], 1)
        with self.assertRaises(CommandError):
            call_command('register_server_projects', source=str(self.source.pk), actor='missing@example.test', stdout=StringIO())
