"""An exact completed analysis opens read-only evidence, never a new schedule."""
from copy import deepcopy
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

from ..models import (
    DocumentIntelligenceRun, IntelligenceFact, PlanningFile, PlanningGeneration,
    PlanningJob, Schedule, ScheduleBaseline, ScheduleVersion, WorkCalendar,
)
from ..services.document_intelligence import run_document_intelligence
from ..services.pipeline import _document_payload
from ..intelligence_views import DocumentIntelligenceRunViewSet
from .test_document_intelligence import DocumentIntelligenceFixture


# Use a private router: guarding the shared application's URLPattern instances
# would also alter unrelated release-harness fixtures in a combined test run.
router = DefaultRouter()
router.register('intelligence-runs', DocumentIntelligenceRunViewSet, basename='analysis-workspace-test')
urlpatterns = [path('api/v1/planning-intelligence/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class AnalysisWorkspaceAPITests(DocumentIntelligenceFixture):
    def setUp(self):
        super().setUp()
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        organization = Organization.objects.create(name='Analysis workspace', code='analysis-workspace')
        self.role = Role.objects.create(name='Analysis evidence reader', code='analysis-evidence-reader')
        RoleModule.objects.create(role=self.role, module=module)
        for permission in module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        for user in (self.owner, self.outsider):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            UserRole.objects.filter(user_profile=profile).delete()
            UserRole.objects.create(user_profile=profile, role=self.role)
        self.source_file = self.source(
            'activity-evidence.csv', 'sow',
            'Task|Duration (working days)\nReview field survey|\nPrepare mapping report|\n',
        )
        self.run, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = self.endpoint(self.run)
        # Any accidental reanalysis/provider call on GET fails the test.
        for target in (
            'apps.planning_intelligence.services.pipeline.analyze_documents',
            'apps.planning_intelligence.services.project_ai.call_project_ai',
            'apps.planning_intelligence.services.schedule_materializer.materialize_generation',
        ):
            guard = patch(target, side_effect=AssertionError('Reading evidence must not start work.'))
            self.addCleanup(guard.stop)
            guard.start()

    @staticmethod
    def endpoint(run):
        return f'/api/v1/planning-intelligence/intelligence-runs/{run.pk}/schedule-workspace/'

    def test_completed_analysis_is_readable_with_only_read_access_and_no_database_writes(self):
        summary = deepcopy(self.run.summary)
        statuses = list(self.run.facts.order_by('pk').values_list('pk', 'status', 'reviewed_at'))
        with CaptureQueriesContext(connection) as queries:
            first = self.client.get(self.url)
            second = self.client.get(self.url)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.data, first.data)
        writes = [row['sql'] for row in queries if row['sql'].lstrip().split(' ', 1)[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}]
        self.assertEqual(writes, [])
        self.assertEqual(first.data['state'], 'analysis_evidence')
        self.assertEqual(first.data['analysis_run_id'], self.run.pk)
        self.assertEqual(first.data['project'], self.project.pk)
        self.assertNotIn('id', first.data)
        self.assertNotIn('version', first.data)
        self.assertEqual(first.data['intelligence']['document_intelligence_run_id'], self.run.pk)
        self.assertEqual([row['name'] for row in first.data['activities']], ['Review field survey', 'Prepare mapping report'])
        self.assertTrue(all(row['duration_days'] is None and row['start_date'] is None and row['finish_date'] is None
                            for row in first.data['activities']))
        self.assertTrue(all(row['source_references'][0]['file_id'] == self.source_file.pk for row in first.data['activities']))
        self.assertEqual(first.data['logic_matrix'], [])
        self.assertTrue(first.data['validation'])
        self.assertFalse(first.data['intelligence']['schedule_engine']['ready_for_calculation'])
        self.run.refresh_from_db()
        self.assertEqual(self.run.summary, summary)
        self.assertEqual(list(self.run.facts.order_by('pk').values_list('pk', 'status', 'reviewed_at')), statuses)
        self.assertEqual(DocumentIntelligenceRun.objects.count(), 1)
        for model in (PlanningGeneration, PlanningJob, Schedule, ScheduleVersion, ScheduleBaseline, WorkCalendar):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_exact_older_run_does_not_substitute_newer_facts_or_existing_generation(self):
        self.source_file.extracted_text = 'Prepare permit dossier. Prepare handover dossier.'
        self.source_file.save(update_fields=['extracted_text', 'updated_at'])
        first, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        latest, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        for run, title in ((first, 'permit dossier'), (latest, 'handover dossier')):
            IntelligenceFact.objects.create(
                run=run, source_file=self.source_file, fact_type='deliverable', key=title,
                value={'original_title': title}, extraction_method='ai', status='detected',
                source_excerpt=f'Prepare {title}.', source_locator={'line': 1},
            )
        generation = PlanningGeneration.objects.create(
            project=self.project, version=1, generated_by=self.owner,
            activities=[{'name': 'Unrelated saved generation activity'}],
        )
        response = self.client.get(self.endpoint(first))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['analysis_run_id'], first.pk)
        self.assertEqual([row['name'] for row in response.data['activities']], ['permit dossier'])
        self.assertEqual(response.data['intelligence']['document_intelligence_run_id'], first.pk)
        self.assertEqual(PlanningGeneration.objects.get().pk, generation.pk)

    def test_partial_ai_failure_preserves_completed_source_evidence_and_coverage(self):
        coverage = {'status': 'partial', 'chunks_total': 2, 'chunks_processed': 0, 'chunks_failed': 2}
        self.run.summary['base_intelligence']['ai_processing_coverage'] = coverage
        self.run.summary['processing_coverage']['ai_processing'] = coverage
        self.run.save(update_fields=['summary'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['activities']), 2)
        self.assertEqual(response.data['state'], 'analysis_evidence')
        self.assertEqual(response.data['intelligence']['ai_processing_coverage'], coverage)
        self.assertFalse(response.data['intelligence']['schedule_engine']['ready_for_calculation'])

    def test_saved_older_engine_evidence_opens_without_restarting_or_upgrading_run(self):
        from ..services.document_intelligence import validate_resume_source, ResumeSourceChanged
        self.run.engine_version = 'historical-extraction-engine'
        self.run.save(update_fields=['engine_version'])
        before = deepcopy(self.run.summary)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['analysis_run_id'], self.run.pk)
        self.assertEqual(len(response.data['activities']), 2)
        writes = [row['sql'] for row in queries
                  if row['sql'].lstrip().split(' ', 1)[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}]
        self.assertEqual(writes, [])
        self.run.refresh_from_db()
        self.assertEqual(self.run.engine_version, 'historical-extraction-engine')
        self.assertEqual(self.run.summary, before)
        with self.assertRaises(ResumeSourceChanged):
            validate_resume_source(self.project, self.run)

    def test_older_engine_does_not_bypass_changed_source_guard(self):
        self.run.engine_version = 'historical-extraction-engine'
        self.run.save(update_fields=['engine_version'])
        PlanningFile.objects.filter(pk=self.source_file.pk).update(extracted_text='Changed historical source')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'intelligence_workspace_sources_changed')

    def test_failed_or_running_analysis_is_not_an_empty_completed_workspace(self):
        for run_status in ('running', 'failed'):
            with self.subTest(status=run_status):
                DocumentIntelligenceRun.objects.filter(pk=self.run.pk).update(status=run_status)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data['code'], 'intelligence_workspace_run_unavailable')
                self.assertNotIn('activities', response.data)

    def test_changed_source_text_without_timestamp_change_is_rejected(self):
        PlanningFile.objects.filter(pk=self.source_file.pk).update(extracted_text='Changed source bytes')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'intelligence_workspace_sources_changed')

    def test_added_pending_source_is_rejected(self):
        PlanningFile.objects.create(project=self.project, file='pending.pdf', original_filename='pending.pdf', parse_status='pending')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'intelligence_workspace_sources_changed')
        self.assertIn('parsing', response.data['error'].lower())

    def test_changed_project_inputs_are_rejected(self):
        self.project.name = 'Changed planning inputs'
        self.project.save(update_fields=['name', 'updated_at'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'intelligence_workspace_sources_changed')

    def test_run_without_verifiable_source_manifest_is_rejected(self):
        self.run.summary.pop('extraction_source_manifest')
        self.run.save(update_fields=['summary'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'intelligence_workspace_sources_changed')

    def test_project_scoping_and_authentication_remain_required(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404, response.data)
        self.client.force_authenticate(None)
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (401, 403))

    def test_owner_without_module_read_cannot_open_workspace(self):
        RolePermission.objects.filter(role=self.role).delete()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403, response.data)

    def test_source_change_during_projection_does_not_return_mixed_evidence(self):
        def change_source(project, intelligence):
            payload = _document_payload(project, intelligence)
            PlanningFile.objects.filter(pk=self.source_file.pk).update(extracted_text='Concurrent replacement')
            return payload
        with patch('apps.planning_intelligence.services.analysis_workspace._document_payload', side_effect=change_source):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'intelligence_workspace_sources_changed')

    def test_fact_review_change_during_projection_requires_refresh(self):
        def change_review(project, intelligence):
            payload = _document_payload(project, intelligence)
            self.run.facts.update(status='rejected')
            return payload
        with patch('apps.planning_intelligence.services.analysis_workspace._document_payload', side_effect=change_review):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'intelligence_workspace_review_changed')

    def test_project_input_change_during_projection_is_rechecked(self):
        def change_project(project, intelligence):
            payload = _document_payload(project, intelligence)
            self.project.name = 'Concurrent input change'
            self.project.save(update_fields=['name', 'updated_at'])
            return payload
        with patch('apps.planning_intelligence.services.analysis_workspace._document_payload', side_effect=change_project):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'intelligence_workspace_sources_changed')

    def test_read_endpoint_does_not_accept_post(self):
        response = self.client.post(self.url, {}, format='json')
        self.assertIn(response.status_code, (403, 405))
        self.assertFalse(PlanningGeneration.objects.exists())
