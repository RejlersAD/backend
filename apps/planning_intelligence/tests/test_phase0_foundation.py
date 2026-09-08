import json
from datetime import date
from unittest.mock import patch
import tempfile

from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.users.models import User

from ..access import accessible_projects
from ..models import (
    DocumentIntelligenceRun, PlanningAuditEvent, PlanningFile, PlanningGeneration,
    PlanningJob, PlanningProject, Schedule, ScheduleBaseline, ScheduleBasis, ScheduleVersion,
)
from ..serializers import PlanningFileSerializer, PlanningGenerationSerializer
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

    def test_project_list_can_be_scoped_to_enterprise_project(self):
        other_enterprise_project = Project.objects.create(
            code='P-002', name='Project Two', owner=self.owner,
        )
        PlanningProject.objects.create(
            enterprise_project=other_enterprise_project,
            name='Planning Two',
            created_by=self.owner,
        )
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.get(
            '/api/v1/planning-intelligence/projects/',
            {'enterprise_project': self.enterprise_project.id},
        )

        self.assertEqual(response.status_code, 200)
        rows = response.data.get('results', response.data)
        self.assertEqual([row['id'] for row in rows], [self.workspace.id])

    def test_enterprise_contract_reports_and_synchronizes_master_data(self):
        self.enterprise_project.name = 'Authoritative Project Name'
        self.enterprise_project.client_name = 'Authoritative Client'
        self.enterprise_project.location = 'Abu Dhabi'
        self.enterprise_project.start_date = date(2026, 1, 15)
        self.enterprise_project.end_date = date(2026, 12, 15)
        self.enterprise_project.save()
        client = APIClient()
        client.force_authenticate(self.owner)

        contract_response = client.get(
            f'/api/v1/planning-intelligence/projects/{self.workspace.id}/enterprise-contract/',
        )
        self.assertEqual(contract_response.status_code, 200)
        self.assertFalse(contract_response.data['in_sync'])
        self.assertEqual(contract_response.data['enterprise_code'], 'P-001')

        sync_response = client.post(
            f'/api/v1/planning-intelligence/projects/{self.workspace.id}/sync-from-enterprise/',
            {'expected_enterprise_updated_at': contract_response.data['enterprise_updated_at']},
            format='json',
        )
        self.assertEqual(sync_response.status_code, 200)
        self.assertTrue(sync_response.data['contract']['in_sync'])
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.name, 'Authoritative Project Name')
        self.assertEqual(self.workspace.client, 'Authoritative Client')
        self.assertEqual(str(self.workspace.effective_date), '2026-01-15')
        self.assertTrue(PlanningAuditEvent.objects.filter(
            project=self.workspace, action='project.enterprise_synced',
        ).exists())

    def test_enterprise_contract_does_not_sync_dates_after_baseline(self):
        self.enterprise_project.start_date = date(2026, 2, 1)
        self.enterprise_project.end_date = date(2026, 11, 30)
        self.enterprise_project.save()
        self.workspace.effective_date = date(2026, 1, 1)
        self.workspace.planned_end_date = date(2026, 12, 31)
        self.workspace.save()
        schedule = Schedule.objects.create(
            project=self.workspace, name='Control Schedule', code='CS-01',
            planned_start=date(2026, 1, 1), created_by=self.owner,
        )
        version = ScheduleVersion.objects.create(
            schedule=schedule, version=1, status='baselined', created_by=self.owner,
        )
        ScheduleBaseline.objects.create(
            schedule=schedule, source_version=version, name='Approved Baseline',
            approved_by=self.owner,
        )
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.post(
            f'/api/v1/planning-intelligence/projects/{self.workspace.id}/sync-from-enterprise/',
            {'fields': ['effective_date', 'planned_end_date']}, format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['synced_fields'], [])
        self.assertEqual(response.data['skipped_fields'], ['effective_date', 'planned_end_date'])
        self.workspace.refresh_from_db()
        self.assertEqual(str(self.workspace.effective_date), '2026-01-01')
        self.assertEqual(str(self.workspace.planned_end_date), '2026-12-31')


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

    def test_generation_detail_normalizes_non_finite_legacy_numbers(self):
        legacy_generation = PlanningGeneration(
            project=self.workspace,
            version=99,
            generated_by=self.owner,
            intelligence={'confidence': float('nan')},
            activities=[
                {'id': 'A-1', 'total_float_days': float('inf')},
                {'id': 'A-2', 'total_float_days': float('-inf')},
            ],
        )

        rendered = JSONRenderer().render(
            PlanningGenerationSerializer(legacy_generation).data,
        )
        payload = json.loads(rendered)

        self.assertIsNone(payload['intelligence']['confidence'])
        self.assertIsNone(payload['activities'][0]['total_float_days'])
        self.assertIsNone(payload['activities'][1]['total_float_days'])

    @patch(
        'rest_framework.mixins.RetrieveModelMixin.retrieve',
        side_effect=DatabaseError('legacy production column is missing'),
    )
    def test_generation_detail_falls_back_to_legacy_schema_query(self, _retrieve):
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.get(
            f'/api/v1/planning-intelligence/generations/{self.generation.id}/'
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['id'], self.generation.id)
        self.assertEqual(response.data['project'], self.workspace.id)
        self.assertEqual(response.data['narrative'], 'Original narrative')

    @patch(
        'rest_framework.mixins.RetrieveModelMixin.retrieve',
        side_effect=DatabaseError('legacy production column is missing'),
    )
    def test_legacy_schema_fallback_preserves_project_access(self, _retrieve):
        client = APIClient()
        client.force_authenticate(self.outsider)

        response = client.get(
            f'/api/v1/planning-intelligence/generations/{self.generation.id}/'
        )

        self.assertEqual(response.status_code, 404)

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
    @patch('apps.planning_intelligence.services.pipeline.analyze_documents')
    def test_analysis_job_persists_terminal_result(self, analyze):
        intelligence_run = DocumentIntelligenceRun.objects.create(
            project=self.workspace, status='succeeded', started_at=timezone.now(),
            finished_at=timezone.now(), requested_by=self.owner,
        )
        basis = ScheduleBasis.objects.create(
            project=self.workspace, source_run=intelligence_run, version=1,
            readiness={'ready': True},
        )
        analyze.return_value = {
            'scope': 'FEED',
            'document_intelligence_run_id': intelligence_run.id,
        }
        job = PlanningJob.objects.create(project=self.workspace, job_type='analyze', requested_by=self.owner)
        run_planning_job.apply(args=[job.id]).get()
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')
        self.assertEqual(job.progress, 100)
        self.assertEqual(job.result_data, {
            'intelligence': {
                'scope': 'FEED',
                'document_intelligence_run_id': intelligence_run.id,
            },
            'schedule_basis_id': basis.id,
            'schedule_basis_version': 1,
            'schedule_basis_readiness': {'ready': True},
        })

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
