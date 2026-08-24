from unittest.mock import patch
import tempfile

from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.users.models import User

from ..access import accessible_projects
from ..models import PlanningFile, PlanningGeneration, PlanningJob, PlanningProject
from ..serializers import PlanningFileSerializer
from ..services import byok_crypto
from ..services.pipeline import generate_schedule
from ..tasks import parse_uploaded_planning_file, run_planning_job


class Phase0Fixture(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='test')
        self.viewer = User.objects.create_user(username='viewer', email='viewer@example.com', password='test')
        self.outsider = User.objects.create_user(username='outsider', email='outsider@example.com', password='test')
        self.enterprise_project = Project.objects.create(code='P-001', name='Project One', owner=self.owner)
        ProjectMember.objects.create(project=self.enterprise_project, user=self.viewer, role='viewer')
        self.workspace = PlanningProject.objects.create(
            enterprise_project=self.enterprise_project, name='Planning One', created_by=self.owner,
        )


class PlanningAccessTests(Phase0Fixture):
    def test_enterprise_owner_and_member_can_read_but_outsider_cannot(self):
        self.assertTrue(accessible_projects(self.owner).filter(pk=self.workspace.pk).exists())
        self.assertTrue(accessible_projects(self.viewer).filter(pk=self.workspace.pk).exists())
        self.assertFalse(accessible_projects(self.outsider).filter(pk=self.workspace.pk).exists())

    def test_viewer_cannot_modify_workspace(self):
        client = APIClient()
        client.force_authenticate(self.viewer)
        response = client.patch(
            f'/api/v1/planning-intelligence/projects/{self.workspace.id}/',
            {'name': 'Unauthorized change'}, format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.name, 'Planning One')

    def test_outsider_cannot_see_workspace(self):
        client = APIClient()
        client.force_authenticate(self.outsider)
        response = client.get('/api/v1/planning-intelligence/projects/')
        rows = response.data.get('results', response.data)
        self.assertEqual(rows, [])


class PlanningValidationTests(Phase0Fixture):
    def test_unsupported_upload_extension_is_rejected(self):
        upload = SimpleUploadedFile('schedule.exe', b'not a schedule')
        serializer = PlanningFileSerializer(
            data={'project': self.workspace.id, 'category': 'other', 'file': upload},
            context={'request': type('Request', (), {'user': self.owner})()},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('file', serializer.errors)

    @override_settings(BYOK_ENCRYPTION_KEY=None, SECRET_KEY='stable-legacy-secret')
    def test_byok_legacy_secret_key_compatibility(self):
        encrypted = byok_crypto.encrypt_api_key('sk-ant-test-key-with-enough-characters')
        self.assertEqual(
            byok_crypto.decrypt_api_key(encrypted),
            'sk-ant-test-key-with-enough-characters',
        )

    @override_settings(BYOK_ENCRYPTION_KEY=None, SECRET_KEY='django-insecure-change-this-in-production')
    def test_byok_encryption_fails_closed_without_a_safe_key(self):
        with self.assertRaises(ImproperlyConfigured):
            byok_crypto.encrypt_api_key('sk-ant-test-key-with-enough-characters')

    @patch('apps.planning_intelligence.services.document_intelligence.profile_document')
    @patch('apps.planning_intelligence.services.parsers.extract_text', return_value=('parsed scope', 0.95))
    def test_successful_parse_clears_a_previous_error(self, _extract, _profile):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            planning_file = PlanningFile.objects.create(
                project=self.workspace, category='sow', original_filename='scope.pdf',
                file=SimpleUploadedFile('scope.pdf', b'%PDF-test'), parse_status='failed',
                parse_error='previous worker error', uploaded_by=self.owner,
            )
            parse_uploaded_planning_file.run(planning_file.id)
            planning_file.refresh_from_db()
            self.assertEqual(planning_file.parse_status, 'done')
            self.assertEqual(planning_file.parse_error, '')
            self.assertEqual(planning_file.extracted_text, 'parsed scope')


class GenerationRevisionTests(Phase0Fixture):
    def setUp(self):
        super().setUp()
        self.generation = PlanningGeneration.objects.create(
            project=self.workspace, version=1, generated_by=self.owner,
            wbs=[{'code': '1', 'name': 'Project'}],
            activities=[{'id': 'A-1', 'name': 'Start', 'predecessors': [], 'is_milestone': True}],
            narrative='Original narrative',
        )

    def test_edit_creates_immutable_child_revision(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        response = client.patch(
            f'/api/v1/planning-intelligence/generations/{self.generation.id}/edit/',
            {'narrative': 'Corrected narrative', 'change_summary': 'Corrected scope note'}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.generation.refresh_from_db()
        revision = PlanningGeneration.objects.get(pk=response.data['id'])
        self.assertEqual(self.generation.narrative, 'Original narrative')
        self.assertEqual(revision.version, 2)
        self.assertEqual(revision.parent_generation_id, self.generation.id)
        self.assertEqual(revision.narrative, 'Corrected narrative')

    @patch('apps.planning_intelligence.services.pipeline.build_narrative', return_value='Narrative')
    @patch('apps.planning_intelligence.services.pipeline.validate', return_value=[])
    @patch('apps.planning_intelligence.services.pipeline.build_manhours', return_value={})
    @patch('apps.planning_intelligence.services.pipeline.build_eddr', return_value=[])
    @patch('apps.planning_intelligence.services.pipeline.build_activities', return_value={'activities': [], 'logic_matrix': []})
    @patch('apps.planning_intelligence.services.pipeline.build_wbs', return_value=[])
    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={})
    def test_generation_versions_are_allocated_from_locked_project(self, *_mocks):
        second = generate_schedule(self.workspace, user=self.owner)
        third = generate_schedule(self.workspace, user=self.owner)
        self.assertEqual((second.version, third.version), (2, 3))


class PlanningJobAndExportTests(Phase0Fixture):
    @patch('apps.planning_intelligence.services.pipeline.analyze_documents', return_value={'scope': 'FEED'})
    def test_analysis_job_persists_terminal_result(self, _analyze):
        job = PlanningJob.objects.create(project=self.workspace, job_type='analyze', requested_by=self.owner)
        run_planning_job.apply(args=[job.id]).get()
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')
        self.assertEqual(job.progress, 100)
        self.assertEqual(job.result_data, {'intelligence': {'scope': 'FEED'}})

    def test_generation_export_is_project_scoped(self):
        generation = PlanningGeneration.objects.create(
            project=self.workspace, version=1, generated_by=self.owner,
            activities=[{'id': 'A-1', 'name': 'Start', 'predecessors': []}],
        )
        owner_client = APIClient()
        owner_client.force_authenticate(self.owner)
        response = owner_client.get(
            f'/api/v1/planning-intelligence/generations/{generation.id}/export/?export_format=json'
        )
        self.assertEqual(response.status_code, 200)

        outsider_client = APIClient()
        outsider_client.force_authenticate(self.outsider)
        denied = outsider_client.get(
            f'/api/v1/planning-intelligence/generations/{generation.id}/export/?export_format=json'
        )
        self.assertEqual(denied.status_code, 404)
