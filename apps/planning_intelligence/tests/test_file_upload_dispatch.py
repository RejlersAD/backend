"""Document parsing is dispatched only for committed reference uploads."""
import tempfile
from unittest.mock import patch

from celery.exceptions import Retry
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.users.models import User

from ..models import PlanningFile, PlanningProject
from ..tasks import parse_uploaded_planning_file


class PlanningFileUploadDispatchTests(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='upload-owner', email='upload-owner@example.com', password='test',
        )
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        role = Role.objects.create(code='reference_upload_test', name='Reference upload test')
        RoleModule.objects.create(role=role, module=module)
        for permission in module.permissions.filter(action='create', is_active=True):
            RolePermission.objects.create(role=role, permission=permission)
        organization, _ = Organization.objects.get_or_create(
            code='UPLOAD-TEST', defaults={'name': 'Upload test organization'},
        )
        profile, _ = UserProfile.objects.get_or_create(
            user=self.owner, defaults={'organization': organization},
        )
        UserRole.objects.create(user_profile=profile, role=role)
        enterprise = Project.objects.create(code='UPLOAD-001', name='Upload project', owner=self.owner)
        self.workspace = PlanningProject.objects.create(
            enterprise_project=enterprise, name='Upload planning', created_by=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def upload(self):
        response = self.client.post('/api/v1/planning-intelligence/files/', {
            'project': self.workspace.pk,
            'category': 'mdr',
            'file': SimpleUploadedFile('deliverables.csv', b'Code,Title\nD-001,Foundation drawings\n'),
        }, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['id']

    @patch('apps.planning_intelligence.views.parse_uploaded_planning_file')
    def test_parser_waits_until_outer_transaction_commits(self, parser):
        def inspect_committed_upload(file_id):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(PlanningFile.objects.filter(pk=file_id, parse_status='pending').exists())

        parser.delay.side_effect = inspect_committed_upload
        with transaction.atomic():
            with transaction.atomic():
                file_id = self.upload()
                parser.delay.assert_not_called()
            parser.delay.assert_not_called()
            parser.assert_not_called()

        parser.delay.assert_called_once_with(file_id)
        parser.assert_not_called()

    @patch('apps.planning_intelligence.views.parse_uploaded_planning_file')
    def test_rolled_back_upload_is_not_dispatched(self, parser):
        with self.assertRaisesMessage(RuntimeError, 'cancel upload'):
            with transaction.atomic():
                file_id = self.upload()
                parser.delay.assert_not_called()
                raise RuntimeError('cancel upload')

        self.assertFalse(PlanningFile.objects.filter(pk=file_id).exists())
        parser.delay.assert_not_called()
        parser.assert_not_called()

    @patch('apps.planning_intelligence.views.parse_uploaded_planning_file')
    def test_broker_failure_fallback_also_waits_for_commit(self, parser):
        parser.delay.side_effect = RuntimeError('broker unavailable')

        def inspect_committed_upload(file_id):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(PlanningFile.objects.filter(pk=file_id).exists())

        parser.side_effect = inspect_committed_upload
        with transaction.atomic():
            file_id = self.upload()
            parser.delay.assert_not_called()
            parser.assert_not_called()

        parser.delay.assert_called_once_with(file_id)
        parser.assert_called_once_with(file_id)

    @patch('apps.planning_intelligence.views.parse_uploaded_planning_file')
    def test_autocommit_upload_dispatches_immediately(self, parser):
        self.assertFalse(connection.in_atomic_block)
        file_id = self.upload()
        parser.delay.assert_called_once_with(file_id)
        parser.assert_not_called()


class PlanningFileVisibilityRetryTests(TestCase):
    def test_missing_upload_schedules_a_short_retry(self):
        task = parse_uploaded_planning_file
        task.push_request(called_directly=False, is_eager=True, retries=0, args=[-1], kwargs={})
        try:
            with self.assertRaises(Retry) as raised:
                task.run(-1)
        finally:
            task.pop_request()

        self.assertEqual(raised.exception.when, 1)
        self.assertIsInstance(raised.exception.exc, PlanningFile.DoesNotExist)
        self.assertEqual(raised.exception.sig.args, (-1,))
        self.assertEqual(raised.exception.sig.options['retries'], 1)

    def test_missing_upload_fails_after_three_retries(self):
        task = parse_uploaded_planning_file
        self.assertEqual(task.max_retries, 3)
        task.push_request(called_directly=False, is_eager=True, retries=3, args=[-1], kwargs={})
        try:
            with self.assertRaises(PlanningFile.DoesNotExist):
                task.run(-1)
        finally:
            task.pop_request()
