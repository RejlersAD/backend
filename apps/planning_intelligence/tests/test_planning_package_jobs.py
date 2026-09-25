"""Exercise the durable saved-analysis command through its real materializer."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from . import tests_planning_package_builder as fixtures
from ..models import ScheduleVersion, PlanningGeneration
from ..services.operational_jobs import get_or_create_job
from ..tasks import run_planning_job
from ..serializers import PlanningGenerationSerializer
from ..schedule_views import ScheduleVersionViewSet


class PlanningPackageJobTests(TestCase):
    fact = fixtures.PlanningPackageBuilderTests.fact

    def setUp(self):
        fixtures.PlanningPackageBuilderTests.setUp(self)
        self.user = get_user_model().objects.create_superuser(
            username='package-job-planner', email='package@example.test', password='test')
        self.project.created_by = self.user
        self.project.save(update_fields=['created_by'])
        self.payload = {'generation_options': {'mode': 'planning_package', 'intelligence_run_id': self.run.pk}}

    def test_job_opens_exact_editable_calculated_workspace_and_replays(self):
        job, _ = get_or_create_job(self.project, 'generate', self.payload, self.user)
        with patch('apps.planning_intelligence.services.pipeline.analyze_documents', side_effect=AssertionError('AI rerun')):
            run_planning_job.run(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded', job.error_message)
        result = job.result_data
        version = ScheduleVersion.objects.get(pk=result['schedule_version_id'])
        self.assertEqual(version.status, 'calculated')
        self.assertEqual(result['intelligence_run_id'], self.run.pk)
        generation = PlanningGeneration.objects.get(pk=result['generation_id'])
        detail = PlanningGenerationSerializer(generation).data
        for key in ('generation_mode', 'intelligence_run_id', 'schedule_id', 'schedule_version_id'):
            self.assertEqual(detail[key], result[key])
        request = APIRequestFactory().get('/workspace/')
        force_authenticate(request, self.user)
        response = ScheduleVersionViewSet.as_view({'get': 'workspace'})(request, pk=version.pk)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['can_edit'])
        self.assertGreater(len(response.data['relationships']), 0)
        self.assertEqual(response.data['planning_package']['intelligence_run_id'], self.run.pk)
        replay, created = get_or_create_job(self.project, 'generate', self.payload, self.user)
        self.assertFalse(created)
        self.assertEqual(replay.pk, job.pk)
        run_planning_job.run(replay.pk)
        self.assertEqual(ScheduleVersion.objects.count(), 1)

    def test_queued_template_change_fails_before_any_generation(self):
        job, _ = get_or_create_job(self.project, 'generate', self.payload, self.user)
        self.template.stages.filter(sequence=1).update(duration_days=3)
        run_planning_job.run(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertIn('changed', job.error_message.lower())
        self.assertEqual(PlanningGeneration.objects.count(), 0)

    def test_queued_deleted_source_fails_without_provider_call(self):
        job, _ = get_or_create_job(self.project, 'generate', self.payload, self.user)
        self.source.is_deleted = True
        self.source.save(update_fields=['is_deleted'])
        with patch('apps.planning_intelligence.services.pipeline.analyze_documents', side_effect=AssertionError('AI rerun')):
            run_planning_job.run(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(ScheduleVersion.objects.count(), 0)

    def test_preview_terminal_replay_preserves_fingerprint(self):
        job, _ = get_or_create_job(self.project, 'preview', self.payload, self.user)
        run_planning_job.run(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded', job.error_message)
        self.assertTrue(job.result_data['planning_input_fingerprint'])
        replay, created = get_or_create_job(self.project, 'preview', self.payload, self.user)
        self.assertFalse(created)
        self.assertEqual(replay.pk, job.pk)
        self.assertEqual(ScheduleVersion.objects.count(), 0)

    def test_changed_confirmed_selection_invalidates_queue_and_creates_fresh_scope(self):
        from django.utils import timezone
        from ..services.preview_confirmation import review_fingerprint
        old_job, _ = get_or_create_job(self.project, 'generate', self.payload, self.user)
        self.run.summary['preview_confirmation'] = {
            'confirmed_at': timezone.now().isoformat(), 'review_fingerprint': review_fingerprint(self.run),
            'preview': {'disciplines': {'general': {'in_scope': True, 'deliverables': ['Design Basis']}}},
        }
        self.run.save(update_fields=['summary'])
        run_planning_job.run(old_job.pk)
        old_job.refresh_from_db()
        self.assertEqual(old_job.status, 'failed')
        self.assertEqual(old_job.error_code, 'planning_request_conflict')
        fresh, created = get_or_create_job(self.project, 'generate', self.payload, self.user)
        self.assertTrue(created)
        self.assertNotEqual(fresh.pk, old_job.pk)
        run_planning_job.run(fresh.pk)
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, 'succeeded', fresh.error_message)
        self.assertEqual(fresh.result_generation.eddr[0]['deliverable_name'], 'Design Basis')
        self.assertEqual(len(fresh.result_generation.eddr), 1)
        from ..services.planning_boundaries import accepted_input_validation
        version = ScheduleVersion.objects.get(pk=fresh.result_data['schedule_version_id'])
        self.run.summary['preview_confirmation']['preview']['disciplines']['general']['deliverables'] = ['Equipment Layout']
        self.run.save(update_fields=['summary'])
        readiness = accepted_input_validation(version)
        self.assertTrue(readiness['ready_for_calculation'])
        self.assertFalse(readiness['ready_for_approval'])
        self.assertTrue(any(row['code'] == 'planning_package_source_changed' for row in readiness['issues']))
