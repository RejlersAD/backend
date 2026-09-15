"""Replica API integration checks with real RBAC and isolated local storage."""

import hashlib
import tempfile
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.project_models import Project, ProjectMember
from apps.file_replica.models import (
    ReplicaEntry, ReplicaExtraction, ReplicaScan, ReplicaScope, ReplicaSource, ReplicaVersion,
)
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions


BASE = '/api/v1/file-replica/'
FOLDER = '5900738 Sample'
OTHER_FOLDER = '5900739 Other'
STAMP = '2026-09-15T08:00:00Z'
LATER = '2026-09-15T09:00:00Z'
CONTENT = b'Project Code: 5900738\nProject Name: Source suggestion\nProgress: 77%\nStatus: Issued\n'


class ReplicaAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name='Replica API tests', code='replica-api-tests')
        cls.operator_role = Role.objects.create(name='Replica operator tests', code='replica-operator-test', level=4)
        cls.admin_role = Role.objects.create(name='Replica administrator tests', code='admin', level=2)
        cls.module, _ = Module.objects.get_or_create(code='project_control', defaults={'name': 'Project Control'})
        cls.module.is_active = True
        cls.module.save(update_fields=['is_active'])
        ensure_module_actions(Module, Permission, module_ids=[cls.module.pk])
        RoleModule.objects.get_or_create(role=cls.operator_role, module=cls.module)
        for permission in cls.module.permissions.filter(action__in=['read', 'export', 'create', 'update'], is_active=True):
            RolePermission.objects.get_or_create(role=cls.operator_role, permission=permission)
        cls.superuser = cls.make_user('replica-root', superuser=True)
        cls.admin = cls.make_user('replica-admin', role=cls.admin_role)
        cls.staff = cls.make_user('replica-staff', staff=True)
        cls.member = cls.make_user('replica-member', role=cls.operator_role)
        cls.viewer = cls.make_user('replica-viewer', role=cls.operator_role)
        cls.outsider = cls.make_user('replica-outsider', role=cls.operator_role)
        cls.project = Project.objects.create(code='5900738', name='Existing project name', owner=cls.member, progress=12)
        cls.other_project = Project.objects.create(code='5900739', name='Unrelated project', owner=cls.outsider)
        ProjectMember.objects.create(project=cls.project, user=cls.viewer, role='viewer', is_active=True)

    @classmethod
    def make_user(cls, name, *, role=None, staff=False, superuser=False):
        user = get_user_model().objects.create_user(
            username=name, email=name + '@example.test', is_staff=staff or superuser, is_superuser=superuser,
        )
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': cls.organization})
        UserRole.objects.filter(user_profile=profile).delete()
        if role:
            UserRole.objects.create(user_profile=profile, role=role)
        return user

    def setUp(self):
        cache.clear()
        temporary = tempfile.TemporaryDirectory(prefix='radai-replica-api-')
        self.addCleanup(temporary.cleanup)
        settings_override = override_settings(FILE_REPLICA_ROOT=temporary.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        # The model's callable storage is resolved when Django imports it.
        storage_patch = patch.object(ReplicaVersion._meta.get_field('file'), 'storage', FileSystemStorage(location=temporary.name))
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.admin_client = self.user_client(self.admin)
        self.member_client = self.user_client(self.member)
        self.token = 'synthetic-connector-token-for-tests'
        self.source = ReplicaSource.objects.create(
            name='Synthetic file server', root_path=r'\\replica-test-server\Projects',
            included_paths=[FOLDER], mode='mirror',
            token_hash=hashlib.sha256(self.token.encode()).hexdigest(),
        )
        self.agent = self.agent_client(self.source, self.token)

    @staticmethod
    def user_client(user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(AccessToken.for_user(user)))
        return client

    @staticmethod
    def agent_client(source, token):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token, HTTP_X_REPLICA_SOURCE=str(source.pk))
        return client

    @staticmethod
    def folder_row(path=FOLDER):
        return {'relative_path': path, 'is_directory': True}

    @staticmethod
    def file_row(content=CONTENT, path=FOLDER + '/register.txt', modified_at=STAMP, **overrides):
        row = {
            'relative_path': path, 'is_directory': False, 'size_bytes': len(content),
            'modified_at': modified_at, 'checksum': hashlib.sha256(content).hexdigest(),
        }
        row.update(overrides)
        return row

    def start_scan(self, *, client=None):
        response = (client or self.agent).post(BASE + 'agent/scans/', {'run_id': str(uuid.uuid4())}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return str(response.data['id'])

    def test_active_scan_heartbeat_prevents_false_offline_and_stale_takeover(self):
        scan = self.start_scan()
        stale = timezone.now() - timedelta(hours=1)
        ReplicaSource.objects.filter(pk=self.source.pk).update(last_heartbeat=stale)
        ReplicaScan.objects.filter(pk=scan).update(updated_at=stale)
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, 'offline')
        response = self.agent.post(BASE + f'agent/scans/{scan}/heartbeat/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, 'syncing')
        self.assertGreater(ReplicaScan.objects.get(pk=scan).updated_at, stale)
        self.assertGreater(self.source.last_heartbeat, stale)
        replacement = self.agent.post(BASE + 'agent/scans/', {'run_id': str(uuid.uuid4())}, format='json')
        self.assertEqual(replacement.status_code, 409)

    def test_heartbeat_rejects_wrong_source_disabled_and_completed_scans(self):
        scan = self.start_scan()
        other = ReplicaSource.objects.create(name='Other heartbeat source', root_path=r'\\other\Projects', token_hash=self.source.token_hash)
        wrong = self.agent_client(other, self.token)
        endpoint = BASE + f'agent/scans/{scan}/heartbeat/'
        self.assertEqual(wrong.post(endpoint, {}, format='json').status_code, 404)
        self.assertEqual(APIClient().post(endpoint, {}, format='json').status_code, 403)
        self.source.enabled = False
        self.source.save(update_fields=['enabled'])
        self.assertEqual(self.agent.post(endpoint, {}, format='json').status_code, 403)
        self.source.enabled = True
        self.source.save(update_fields=['enabled'])
        self.complete_scan(scan)
        self.assertEqual(self.agent.post(endpoint, {}, format='json').status_code, 400)

    def test_catalogue_accepts_all_extensions_and_large_metadata_without_upload(self):
        self.source.mode = 'catalogue'
        self.source.save(update_fields=['mode'])
        scan = self.start_scan()
        names = ['drawing.PDF', 'model.dwg', 'package.zip', 'installer.exe', 'README', 'vendor.xyz']
        rows = [self.folder_row()] + [self.file_row(path=f'{FOLDER}/Design/Issued/{name}', checksum='', size_bytes=2 * 1024**3) for name in names]
        response = self.inventory(scan, rows)
        self.assertTrue(all(not item['upload_required'] and item['status'] == 'indexed' for item in response))
        self.complete_scan(scan)
        self.assertFalse(ReplicaVersion.objects.exists())
        listing = self.admin_client.get(BASE + 'entries/', {'source': str(self.source.pk)}).data['results']
        self.assertEqual(len(listing), 7)
        by_name = {row['name']: row for row in listing}
        self.assertEqual(by_name['drawing.PDF']['file_extension'], 'pdf')
        self.assertEqual(by_name['drawing.PDF']['type_label'], 'PDF file')
        self.assertEqual(by_name['model.dwg']['type_label'], 'DWG file')
        self.assertEqual(by_name['vendor.xyz']['type_label'], 'XYZ file')
        self.assertEqual(by_name['README']['type_label'], 'File (no extension)')
        self.assertEqual(by_name[FOLDER]['content_type'], 'inode/directory')

    def test_inventory_batch_uses_bounded_queries_for_new_and_existing_files(self):
        self.source.mode = 'catalogue'
        self.source.save(update_fields=['mode'])
        rows = [self.folder_row()] + [self.file_row(path=f'{FOLDER}/file-{index}.dat', checksum='') for index in range(100)]
        scan = self.start_scan()
        with CaptureQueriesContext(connection) as queries:
            self.inventory(scan, rows)
        self.assertLess(len(queries), 35, [query['sql'] for query in queries])
        self.complete_scan(scan)
        second = self.start_scan()
        with CaptureQueriesContext(connection) as queries:
            self.inventory(second, rows)
        self.assertLess(len(queries), 25, [query['sql'] for query in queries])
        self.complete_scan(second)
        self.assertEqual(self.source.entries.count(), 101)

    def test_inventory_rejects_duplicate_case_insensitive_paths_atomically(self):
        scan = self.start_scan()
        response = self.agent.post(BASE + f'agent/scans/{scan}/entries/', {'entries': [self.folder_row(), self.folder_row(FOLDER.lower())]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.source.entries.exists())

    def test_source_and_scope_scan_state_distinguish_unscanned_and_current_catalogue(self):
        self.source.mode = 'catalogue'
        self.source.save(update_fields=['mode'])
        source_url = BASE + f'sources/{self.source.pk}/'
        self.assertEqual(self.admin_client.get(source_url).data['scan_state'], 'unscanned')
        scan = self.start_scan()
        self.inventory(scan, [self.folder_row(), self.file_row(checksum='')])
        detail = self.admin_client.get(source_url).data
        self.assertEqual(detail['scan_state'], 'syncing')
        self.assertEqual(str(detail['active_run']), scan)
        self.assertEqual(detail['latest_scan']['entry_count'], 2)
        self.complete_scan(scan)
        self.assertEqual(self.admin_client.get(source_url).data['scan_state'], 'ready')
        scope = self.source.scopes.get()
        detail = self.admin_client.get(BASE + f'scopes/{scope.pk}/').data
        self.assertEqual(detail['source_mode'], 'catalogue')
        self.assertEqual(detail['source_scan_state'], 'ready')
        self.assertTrue(detail['in_inventory_scope'])
        self.source.included_paths = [OTHER_FOLDER]
        self.source.save(update_fields=['included_paths'])
        detail = self.admin_client.get(BASE + f'scopes/{scope.pk}/').data
        self.assertEqual(detail['source_scan_state'], 'unscanned')
        self.assertFalse(detail['in_inventory_scope'])
        self.source.included_paths = []
        self.source.save(update_fields=['included_paths'])
        self.assertEqual(self.admin_client.get(source_url).data['scan_state'], 'discovery_only')

    def inventory(self, scan, rows, *, client=None):
        response = (client or self.agent).post(BASE + f'agent/scans/{scan}/entries/', {'entries': rows}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['entries']

    def complete_scan(self, scan, success=True, *, expected='completed'):
        response = self.agent.post(BASE + f'agent/scans/{scan}/complete/', {'success': success}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], expected, response.data)
        return response

    def upload(self, scan, entry, content=CONTENT, *, checksum=None, modified_at=STAMP, client=None):
        return (client or self.agent).post(BASE + f'agent/entries/{entry.pk}/content/', {
            'scan_id': scan, 'checksum': checksum or hashlib.sha256(content).hexdigest(),
            'modified_at': modified_at,
            'file': SimpleUploadedFile(entry.name, content, content_type='text/plain'),
        }, format='multipart')

    def publish_scope(self, scope):
        response = self.admin_client.patch(BASE + f'scopes/{scope.pk}/', {'access_enabled': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        scope.refresh_from_db()

    def make_replica(self, content=CONTENT, *, publish=True, path=FOLDER + '/register.txt', complete=True):
        scan = self.start_scan()
        self.inventory(scan, [self.folder_row(path.split('/')[0]), self.file_row(content, path)])
        entry = ReplicaEntry.objects.get(source=self.source, relative_path=path)
        response = self.upload(scan, entry, content)
        self.assertEqual(response.status_code, 201, response.data)
        entry.refresh_from_db()
        if publish:
            self.publish_scope(entry.scope)
        if complete:
            self.complete_scan(scan)
        return entry, scan

    def replace_content(self, entry, content, *, upload=True):
        scan = self.start_scan()
        self.inventory(scan, [self.folder_row(), self.file_row(content, entry.relative_path, LATER)])
        if upload:
            response = self.upload(scan, entry, content, modified_at=LATER)
            self.assertEqual(response.status_code, 201, response.data)
            self.complete_scan(scan)
        entry.refresh_from_db()
        return scan

    def extract_entry(self, entry, *, client=None):
        response = (client or self.member_client).post(BASE + f'entries/{entry.pk}/extract/', {}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'pending_review', response.data)
        return response.data

    def download(self, entry, *, client=None, version=None):
        params = {'version': str(version)} if version else {}
        response = (client or self.member_client).get(BASE + f'entries/{entry.pk}/download/', params)
        self.addCleanup(response.close)
        return response

    @staticmethod
    def results(response):
        return response.data['results'] if isinstance(response.data, dict) and 'results' in response.data else response.data

    def deny_action(self, action):
        for permission in self.module.permissions.filter(action=action, is_active=True):
            UserPermissionOverride.objects.update_or_create(
                user_profile=self.member.rbac_profile, permission=permission, defaults={'allowed': False},
            )

    def test_sources_require_real_administrator_role_not_staff(self):
        for user in (self.superuser, self.admin):
            with self.subTest(allowed=user.email):
                response = self.user_client(user).get(BASE + 'sources/')
                self.assertEqual(response.status_code, 200, response.data)
        for user in (self.staff, self.member):
            with self.subTest(denied=user.email):
                self.assertEqual(self.user_client(user).get(BASE + 'sources/').status_code, 403)
        self.assertIn(APIClient().get(BASE + 'sources/').status_code, (401, 403))
        payload = {'name': 'Created through API', 'root_path': r'\\server\Projects', 'included_paths': [FOLDER]}
        response = self.admin_client.post(BASE + 'sources/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(ReplicaSource.objects.get(pk=response.data['id']).included_paths, [FOLDER])

    def test_inactive_locked_deleted_admin_profiles_and_inactive_roles_are_denied(self):
        for field, value in (
            ('status', 'inactive'), ('is_deleted', True), ('locked_until', timezone.now() + timedelta(hours=1)),
        ):
            with self.subTest(field=field):
                UserProfile.objects.filter(user=self.admin).update(**{field: value})
                self.assertEqual(self.admin_client.get(BASE + 'sources/').status_code, 403)
                UserProfile.objects.filter(user=self.admin).update(status='active', is_deleted=False, locked_until=None)
        Role.objects.filter(pk=self.admin_role.pk).update(is_active=False)
        self.assertEqual(self.admin_client.get(BASE + 'sources/').status_code, 403)
        Role.objects.filter(pk=self.admin_role.pk).update(is_active=True)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        self.assertIn(self.admin_client.get(BASE + 'sources/').status_code, (401, 403))

    def test_rotated_token_is_hashed_returned_once_and_old_token_stops_working(self):
        response = self.admin_client.post(BASE + f'sources/{self.source.pk}/rotate-token/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        token = response.data['token']
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.source.refresh_from_db()
        self.assertNotEqual(self.source.token_hash, token)
        self.assertEqual(self.source.token_hash, hashlib.sha256(token.encode()).hexdigest())
        detail = self.admin_client.get(BASE + f'sources/{self.source.pk}/')
        self.assertNotIn('token', detail.data)
        self.assertNotIn('token_hash', detail.data)
        self.assertNotIn(token.encode(), detail.content)
        self.assertIn(self.agent.get(BASE + 'agent/config/').status_code, (401, 403))
        self.assertEqual(self.agent_client(self.source, token).get(BASE + 'agent/config/').status_code, 200)

    def test_connector_requires_its_own_source_token_not_jwt_or_anonymous(self):
        self.assertIn(APIClient().get(BASE + 'agent/config/').status_code, (401, 403))
        jwt_client = self.user_client(self.superuser)
        response = jwt_client.get(BASE + 'agent/config/', HTTP_X_REPLICA_SOURCE=str(self.source.pk))
        self.assertIn(response.status_code, (401, 403))
        wrong_source = ReplicaSource.objects.create(name='Other source', root_path=r'\\other\Projects', token_hash='f' * 64)
        self.assertIn(self.agent_client(wrong_source, self.token).get(BASE + 'agent/config/').status_code, (401, 403))
        self.assertIn(self.agent.get(BASE + 'sources/').status_code, (401, 403))
        self.assertEqual(self.agent.get(BASE + 'agent/config/').status_code, 200)

    def test_connector_cannot_inventory_another_sources_scan(self):
        scan = self.start_scan()
        token = 'second-source-token'
        other = ReplicaSource.objects.create(
            name='Other source', root_path=r'\\other\Projects', included_paths=[FOLDER],
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        response = self.agent_client(other, token).post(
            BASE + f'agent/scans/{scan}/entries/', {'entries': [self.folder_row()]}, format='json',
        )
        self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(ReplicaEntry.objects.exists())

    def test_inventory_rejects_traversal_excluded_and_outside_paths_atomically(self):
        self.source.excluded_paths = [FOLDER + '/Private']
        self.source.save(update_fields=['excluded_paths'])
        scan = self.start_scan()
        paths = [FOLDER + '/../secret.txt', r'C:\secret.txt', '/absolute/file.txt',
                 FOLDER + '/Private/secret.txt', OTHER_FOLDER + '/secret.txt']
        for path in paths:
            with self.subTest(path=path):
                response = self.agent.post(BASE + f'agent/scans/{scan}/entries/', {
                    'entries': [self.folder_row(), self.file_row(path=path)],
                }, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(ReplicaEntry.objects.exists())

    def test_discovery_without_selected_folders_accepts_only_top_level_directories(self):
        self.source.included_paths = []
        self.source.save(update_fields=['included_paths'])
        scan = self.start_scan()
        self.inventory(scan, [self.folder_row()])
        for row in (self.folder_row(FOLDER + '/Nested'), self.file_row()):
            response = self.agent.post(BASE + f'agent/scans/{scan}/entries/', {'entries': [row]}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(ReplicaEntry.objects.count(), 1)

    def test_folder_mapping_preserves_integer_project_id_and_requires_publication(self):
        entry, _ = self.make_replica(publish=False)
        scope = entry.scope
        self.assertEqual(scope.project_id, self.project.pk)
        self.assertIsInstance(scope.project_id, int)
        self.assertFalse(scope.access_enabled)
        self.assertEqual(self.member_client.get(BASE + f'entries/{entry.pk}/').status_code, 404)
        response = self.admin_client.get(BASE + f'scopes/{scope.pk}/')
        self.assertEqual(response.data['project'], self.project.pk)
        self.publish_scope(scope)
        self.assertEqual(self.member_client.get(BASE + f'entries/{entry.pk}/').status_code, 200)
        filtered = self.member_client.get(BASE + 'entries/', {'project': self.project.pk})
        self.assertEqual(filtered.status_code, 200, filtered.data)
        self.assertIn(str(entry.pk), [row['id'] for row in self.results(filtered)])
        self.assertEqual(self.member_client.patch(BASE + f'scopes/{scope.pk}/', {'access_enabled': False}, format='json').status_code, 403)

    def test_module_access_does_not_reveal_an_unrelated_project_scope(self):
        self.source.included_paths = [FOLDER, OTHER_FOLDER]
        self.source.save(update_fields=['included_paths'])
        own, _ = self.make_replica()
        other, _ = self.make_replica(path=OTHER_FOLDER + '/register.txt')
        response = self.member_client.get(BASE + 'entries/')
        self.assertEqual(response.status_code, 200, response.data)
        ids = {row['id'] for row in self.results(response)}
        self.assertIn(str(own.pk), ids)
        self.assertNotIn(str(other.pk), ids)
        self.assertEqual(self.download(other).status_code, 404)
        self.assertEqual(self.member_client.get(BASE + f'scopes/{other.scope_id}/').status_code, 404)

    def test_each_action_obeys_explicit_user_denies(self):
        entry, _ = self.make_replica()
        extraction = self.extract_entry(entry)
        actions = {
            'read': lambda: self.member_client.get(BASE + f'entries/{entry.pk}/'),
            'export': lambda: self.download(entry),
            'create': lambda: self.member_client.post(BASE + f'entries/{entry.pk}/extract/', {}, format='json'),
            'update': lambda: self.member_client.post(BASE + f'extractions/{extraction["id"]}/review/', {'status': 'accepted'}, format='json'),
        }
        for action, request in actions.items():
            with self.subTest(action=action):
                self.deny_action(action)
                self.assertEqual(request().status_code, 403)
                UserPermissionOverride.objects.filter(user_profile=self.member.rbac_profile).delete()
        self.assertEqual(ReplicaExtraction.objects.get(pk=extraction['id']).status, 'pending_review')

    def test_inactive_module_and_removed_role_module_deny_reads(self):
        entry, _ = self.make_replica()
        Module.objects.filter(pk=self.module.pk).update(is_active=False)
        self.assertEqual(self.member_client.get(BASE + f'entries/{entry.pk}/').status_code, 403)
        Module.objects.filter(pk=self.module.pk).update(is_active=True)
        RoleModule.objects.filter(role=self.operator_role, module=self.module).delete()
        self.assertEqual(self.member_client.get(BASE + f'entries/{entry.pk}/').status_code, 403)

    def test_project_viewer_can_read_export_but_cannot_extract_or_review(self):
        entry, _ = self.make_replica()
        extraction = self.extract_entry(entry)
        viewer = self.user_client(self.viewer)
        self.assertEqual(viewer.get(BASE + f'entries/{entry.pk}/').status_code, 200)
        self.assertEqual(self.download(entry, client=viewer).status_code, 200)
        self.assertEqual(viewer.post(BASE + f'entries/{entry.pk}/extract/', {}, format='json').status_code, 403)
        self.assertEqual(viewer.post(BASE + f'extractions/{extraction["id"]}/review/', {'status': 'accepted'}, format='json').status_code, 403)

    def test_upload_rejects_checksum_mtime_and_inventory_size_mismatches(self):
        scan = self.start_scan()
        self.inventory(scan, [self.folder_row(), self.file_row()])
        entry = ReplicaEntry.objects.get(relative_path=FOLDER + '/register.txt')
        self.assertEqual(self.upload(scan, entry, checksum='0' * 64).status_code, 400)
        self.assertEqual(self.upload(scan, entry, modified_at=LATER).status_code, 400)
        self.inventory(scan, [self.file_row(size_bytes=len(CONTENT) + 1)])
        response = self.upload(scan, entry)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(ReplicaVersion.objects.exists())
        entry.refresh_from_db()
        self.assertIsNone(entry.current_version_id)

    def test_retry_upload_is_idempotent_and_new_content_gets_a_new_version(self):
        entry, scan = self.make_replica(complete=False)
        initial_version = entry.current_version_id
        response = self.upload(scan, entry)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(str(response.data['version']), str(initial_version))
        self.assertEqual(entry.versions.count(), 1)
        self.complete_scan(scan)
        replacement = b'Project Code: 5900738\nStatus: Revised\n'
        self.replace_content(entry, replacement)
        self.assertEqual(entry.versions.count(), 2)
        self.assertNotEqual(entry.current_version_id, initial_version)
        self.assertEqual(entry.current_version.number, 2)
        response = self.download(entry, version=initial_version)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), CONTENT)
        response.close()

    def test_failed_and_incomplete_scans_do_not_mark_unseen_files_missing(self):
        entry, _ = self.make_replica()
        failed = self.start_scan()
        self.inventory(failed, [self.folder_row()])
        self.complete_scan(failed, success=False, expected='failed')
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'available')
        incomplete = self.start_scan()
        self.inventory(incomplete, [self.folder_row(), self.file_row(path=FOLDER + '/pending.txt')])
        self.complete_scan(incomplete, expected='failed')
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'available')
        self.assertEqual(entry.versions.count(), 1)

    def test_completed_scan_marks_only_unseen_entries_in_selected_scope_missing(self):
        self.source.mode = 'catalogue'
        self.source.included_paths = [FOLDER, OTHER_FOLDER]
        self.source.save(update_fields=['mode', 'included_paths'])
        scan = self.start_scan()
        self.inventory(scan, [self.folder_row(), self.folder_row(OTHER_FOLDER), self.file_row(),
                              self.file_row(path=OTHER_FOLDER + '/outside.txt')])
        self.complete_scan(scan)
        response = self.admin_client.patch(BASE + f'sources/{self.source.pk}/', {'included_paths': [FOLDER]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        scan = self.start_scan()
        self.inventory(scan, [self.folder_row()])
        self.complete_scan(scan)
        self.assertEqual(ReplicaEntry.objects.get(relative_path=FOLDER + '/register.txt').status, 'missing')
        self.assertEqual(ReplicaEntry.objects.get(relative_path=OTHER_FOLDER + '/outside.txt').status, 'indexed')

    def test_configuration_edit_invalidates_active_scan_and_preserves_existing_files(self):
        entry, _ = self.make_replica()
        scan = self.start_scan()
        self.inventory(scan, [self.folder_row()])
        response = self.admin_client.patch(BASE + f'sources/{self.source.pk}/', {'max_file_size_mb': 20}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.active_run)
        self.assertEqual(ReplicaScan.objects.get(pk=scan).status, 'failed')
        response = self.agent.post(BASE + f'agent/scans/{scan}/entries/', {'entries': [self.file_row()]}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.complete_scan(scan, expected='failed')
        entry.refresh_from_db()
        self.assertEqual(entry.status, 'available')

    def test_extraction_has_version_provenance_and_accepting_never_updates_project(self):
        entry, _ = self.make_replica()
        result = self.extract_entry(entry)
        self.assertEqual(str(result['version']), str(entry.current_version_id))
        self.assertEqual(result['version_number'], 1)
        self.assertFalse(result['stale'])
        suggestion = next(item for item in result['suggestions'] if item['label'] == 'Progress')
        self.assertEqual(suggestion['value'], '77%')
        self.assertEqual(suggestion['location'], 'Line 3')
        response = self.member_client.post(BASE + f'extractions/{result["id"]}/review/', {
            'status': 'accepted', 'notes': 'Checked against the source.',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        row = ReplicaExtraction.objects.get(pk=result['id'])
        self.assertEqual(row.status, 'accepted')
        self.assertEqual(row.reviewed_by_id, self.member.pk)
        self.assertIsNotNone(row.reviewed_at)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Existing project name')
        self.assertEqual(self.project.progress, 12)

    def test_stale_extraction_cannot_be_accepted_but_can_be_rejected(self):
        entry, _ = self.make_replica()
        result = self.extract_entry(entry)
        self.replace_content(entry, b'Status: Revised\n')
        rows = self.member_client.get(BASE + f'entries/{entry.pk}/extractions/')
        self.assertTrue(rows.data[0]['stale'])
        review_url = BASE + f'extractions/{result["id"]}/review/'
        response = self.member_client.post(review_url, {'status': 'accepted'}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(ReplicaExtraction.objects.get(pk=result['id']).status, 'pending_review')
        response = self.member_client.post(review_url, {'status': 'rejected'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_search_uses_only_current_available_extracted_content(self):
        entry, _ = self.make_replica(b'Project Name: AquamarineUnicorn\n')
        self.extract_entry(entry)
        response = self.member_client.get(BASE + 'entries/', {'search': 'AquamarineUnicorn'})
        self.assertEqual([row['id'] for row in self.results(response)], [str(entry.pk)])
        scan = self.replace_content(entry, b'Project Name: VermilionFalcon\n', upload=False)
        response = self.member_client.get(BASE + 'entries/', {'search': 'AquamarineUnicorn'})
        self.assertEqual(self.results(response), [])
        response = self.upload(scan, entry, b'Project Name: VermilionFalcon\n', modified_at=LATER)
        self.assertEqual(response.status_code, 201, response.data)
        self.complete_scan(scan)
        self.extract_entry(entry)
        old = self.member_client.get(BASE + 'entries/', {'search': 'AquamarineUnicorn'})
        current = self.member_client.get(BASE + 'entries/', {'search': 'VermilionFalcon'})
        self.assertEqual(self.results(old), [])
        self.assertEqual([row['id'] for row in self.results(current)], [str(entry.pk)])

    def test_download_streams_privately_without_storage_paths_or_url_fields(self):
        entry, _ = self.make_replica()
        detail = self.member_client.get(BASE + f'entries/{entry.pk}/')
        versions = self.member_client.get(BASE + f'entries/{entry.pk}/versions/')
        for payload in (detail.data, *versions.data):
            self.assertFalse({'file', 'url', 'download_url', 'root_path', 'token', 'token_hash'} & payload.keys())
        self.assertNotIn(entry.current_version.file.name.encode(), detail.content)
        response = self.download(entry)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), CONTENT)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('sandbox', response['Content-Security-Policy'])
        response.close()

    def test_access_revocation_immediately_blocks_versions_download_and_extracted_text(self):
        entry, _ = self.make_replica()
        self.extract_entry(entry)
        self.assertEqual(self.download(entry).status_code, 200)
        response = self.admin_client.patch(BASE + f'scopes/{entry.scope_id}/', {'access_enabled': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for suffix in ('', 'versions/', 'extractions/'):
            self.assertEqual(self.member_client.get(BASE + f'entries/{entry.pk}/' + suffix).status_code, 404)
        self.assertEqual(self.download(entry, version=entry.current_version_id).status_code, 404)
        response = self.member_client.get(BASE + 'entries/', {'search': 'Source suggestion'})
        self.assertEqual(self.results(response), [])

    def test_membership_revocation_and_source_exclusions_apply_immediately(self):
        entry, _ = self.make_replica()
        viewer = self.user_client(self.viewer)
        self.assertEqual(viewer.get(BASE + f'entries/{entry.pk}/').status_code, 200)
        ProjectMember.objects.filter(project=self.project, user=self.viewer).update(is_active=False)
        self.assertEqual(viewer.get(BASE + f'entries/{entry.pk}/').status_code, 404)
        response = self.admin_client.patch(BASE + f'sources/{self.source.pk}/', {
            'excluded_paths': [entry.relative_path],
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.member_client.get(BASE + f'entries/{entry.pk}/').status_code, 404)
        self.assertEqual(self.download(entry).status_code, 404)
