"""Read-only command preflight and replay identity for Planning Package mode."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from django.utils import timezone
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User
from ..models import (
    CalendarException, DocumentIntelligenceRun, IntelligenceFact, PlanningFile, PlanningProject,
    PlanningJob, ProjectScheduleConfiguration, WorkCalendar, WorkflowStage, WorkflowTemplate, WorkflowTemplateOverride,
)
from ..views import PlanningProjectViewSet
from ..services.document_intelligence import _extraction_source_manifest
from ..services.operational_jobs import generation_fingerprint, get_or_create_job
from ..services.planning_package_request import PlanningPackageRequestError, resolve_generation_options


router = DefaultRouter()
router.register('projects', PlanningProjectViewSet, basename='planning-package-request-test')
urlpatterns = [path('api/v1/planning-intelligence/', include(router.urls))]
secure_module_endpoints(urlpatterns)


class PlanningPackageRequestTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='package-preflight', email='package-preflight@example.test')
        self.project = PlanningProject.objects.create(name='Original name', created_by=self.owner,
            effective_date=date(2026, 10, 1), planned_end_date=date(2027, 5, 30))
        self.file = PlanningFile.objects.create(project=self.project, uploaded_by=self.owner,
            original_filename='scope.txt', file='tests/package-scope.txt', category='sow', parse_status='done',
            extracted_text='Item|Discipline|Deliverable\n1|Process|Design basis\n')
        self.run = self.completed_run()
        self.payload = {'generation_options': {'mode': 'planning_package', 'intelligence_run_id': self.run.pk}}
        for target in ('apps.planning_intelligence.services.pipeline.analyze_documents',
                       'apps.planning_intelligence.services.document_intelligence.get_or_run_document_intelligence',
                       'apps.planning_intelligence.services.project_ai.call_project_ai'):
            guard = patch(target, side_effect=AssertionError('Preflight must not start another analysis.'))
            guard.start()
            self.addCleanup(guard.stop)

    def completed_run(self, project=None, **changes):
        return DocumentIntelligenceRun.objects.create(project=project or self.project, status='succeeded',
            source_file_ids=[self.file.pk], started_at=timezone.now(), finished_at=timezone.now(),
            summary={'extraction_source_manifest': _extraction_source_manifest([self.file])}, **changes)

    def configuration(self):
        template = WorkflowTemplate.objects.create(project=self.project, code='PROJECT_WORKFLOW',
                                                    name='Recorded workflow', status='active')
        stage = WorkflowStage.objects.create(template=template, sequence=1, code='PREPARE',
            name='Prepare', duration_days=Decimal('3'), relationship_to_previous='')
        configuration = ProjectScheduleConfiguration.objects.create(project=self.project,
            workflow_template=template, standard_task_count=1, configuration_version=1,
            settings={'final_issue_mode': 'task'})
        return configuration, template, stage

    def fingerprint(self):
        # Commands load project afresh; cached reverse relations from test setup
        # must not stand in for the current database state.
        return generation_fingerprint(PlanningProject.objects.get(pk=self.project.pk), self.payload)

    def assert_preflight_error(self, status, code, payload=None, project=None):
        with self.assertRaises(PlanningPackageRequestError) as raised:
            resolve_generation_options(project or self.project, payload or self.payload)
        self.assertEqual(raised.exception.status_code, status)
        self.assertEqual(raised.exception.code, code)

    def test_exact_saved_run_is_selected_and_preflight_is_read_only(self):
        newer = self.completed_run()
        with CaptureQueriesContext(connection) as captured:
            result = resolve_generation_options(self.project, self.payload)
        self.assertEqual(result['mode'], 'planning_package')
        self.assertEqual(result['intelligence_run'].pk, self.run.pk)
        self.assertNotEqual(result['intelligence_run'].pk, newer.pk)
        self.assertFalse([row for row in captured if row['sql'].lstrip().split(' ', 1)[0].upper()
                          in {'INSERT', 'UPDATE', 'DELETE'}])

    def test_foreign_run_cannot_supply_scope(self):
        other = PlanningProject.objects.create(name='Other project', created_by=self.owner,
                                               effective_date=self.project.effective_date)
        self.assert_preflight_error(404, 'planning_analysis_unavailable', project=other)

    @override_settings(ROOT_URLCONF=__name__)
    def test_read_only_module_user_cannot_enqueue_generation(self):
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        organization = Organization.objects.create(name='Planning reader', code='package-reader')
        role = Role.objects.create(name='Planning package reader', code='package-reader')
        RoleModule.objects.create(role=role, module=module)
        for permission in module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.create(role=role, permission=permission)
        profile, _ = UserProfile.objects.get_or_create(user=self.owner, defaults={'organization': organization})
        UserRole.objects.filter(user_profile=profile).delete()
        UserRole.objects.create(user_profile=profile, role=role)
        client = APIClient()
        client.force_authenticate(self.owner)
        with patch.object(PlanningProjectViewSet, '_enqueue_job', side_effect=AssertionError('Denied request must not enqueue work.')):
            response = client.post(f'/api/v1/planning-intelligence/projects/{self.project.pk}/generate/', self.payload, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(PlanningJob.objects.filter(project=self.project).exists())

    def test_failed_running_or_deleted_run_is_unavailable(self):
        for changes in ({'status': 'failed'}, {'status': 'running'}, {'status': 'succeeded', 'is_deleted': True}):
            with self.subTest(changes=changes):
                DocumentIntelligenceRun.objects.filter(pk=self.run.pk).update(**changes)
                self.assert_preflight_error(404, 'planning_analysis_unavailable')

    def test_deleted_source_blocks_generation_with_actionable_stale_response(self):
        PlanningFile.objects.filter(pk=self.file.pk).update(is_deleted=True)
        self.assert_preflight_error(409, 'planning_sources_changed')

    def test_changed_text_or_new_unparsed_file_requires_current_analysis(self):
        original = self.file.extracted_text
        PlanningFile.objects.filter(pk=self.file.pk).update(extracted_text=original + 'Changed source')
        self.assert_preflight_error(409, 'planning_sources_changed')
        PlanningFile.objects.filter(pk=self.file.pk).update(extracted_text=original)
        PlanningFile.objects.create(project=self.project, uploaded_by=self.owner, original_filename='later.txt',
            file='tests/later-package-source.txt', category='sow', parse_status='pending')
        self.assert_preflight_error(409, 'planning_sources_changed')

    def test_missing_registered_start_is_not_replaced_with_today(self):
        self.project.effective_date = None
        self.project.save(update_fields=['effective_date'])
        self.assert_preflight_error(400, 'planning_start_required')

    def test_corrected_project_metadata_uses_existing_analysis_without_mutation(self):
        old_summary = deepcopy(self.run.summary)
        self.project.name = 'Corrected project name'
        self.project.effective_date = date(2026, 11, 1)
        self.project.save(update_fields=['name', 'effective_date', 'updated_at'])
        with CaptureQueriesContext(connection) as captured:
            result = resolve_generation_options(self.project, self.payload)
        self.assertEqual(result['intelligence_run'].pk, self.run.pk)
        self.run.refresh_from_db()
        self.assertEqual(self.run.summary, old_summary)
        self.assertEqual(DocumentIntelligenceRun.objects.filter(project=self.project).count(), 1)
        self.assertFalse([row for row in captured if row['sql'].lstrip().split(' ', 1)[0].upper()
                          in {'INSERT', 'UPDATE', 'DELETE'}])

    def test_mode_and_analysis_identity_validation_preserves_literal_default(self):
        self.assertEqual(resolve_generation_options(self.project, {}), {'mode': 'document_driven', 'intelligence_run': None})
        for value in (None, True, -1, 1.5, 'not-a-run'):
            with self.subTest(value=value):
                self.assert_preflight_error(400, 'invalid_generation_options',
                    {'generation_options': {'mode': 'planning_package', 'intelligence_run_id': value}})
        self.assert_preflight_error(400, 'invalid_generation_options', {'generation_options': {'mode': 'unknown'}})

    def test_preview_configuration_revision_change_is_stale(self):
        configuration, _, _ = self.configuration()
        payload = deepcopy(self.payload)
        payload['generation_options']['expected_configuration_version'] = 1
        resolve_generation_options(self.project, payload)
        configuration.configuration_version = 2
        configuration.save(update_fields=['configuration_version', 'updated_at'])
        self.assert_preflight_error(409, 'configuration_conflict', payload)

    def test_generated_nondefault_calendar_does_not_break_identical_command_replay(self):
        before = self.fingerprint()
        calendar = WorkCalendar.objects.create(project=self.project, name='Materialized proposal calendar',
            is_default=False, working_weekdays=[0, 1, 2, 3, 4], hours_per_day=8, timezone='UTC')
        CalendarException.objects.create(calendar=calendar, date=date(2026, 12, 1), is_working=False)
        self.assertEqual(self.fingerprint(), before)

    def test_default_calendar_inputs_change_fingerprint(self):
        calendar = WorkCalendar.objects.create(project=self.project, name='Registered planning calendar',
            is_default=True, working_weekdays=[0, 1, 2, 3, 4], hours_per_day=8, timezone='UTC')
        before = self.fingerprint()
        CalendarException.objects.create(calendar=calendar, date=date(2026, 12, 1), is_working=False)
        self.assertNotEqual(self.fingerprint(), before)

    def test_configuration_stage_template_and_override_changes_invalidate_identity(self):
        configuration, template, stage = self.configuration()
        before = self.fingerprint()
        configuration.settings = {'final_issue_mode': 'milestone'}
        configuration.save(update_fields=['settings', 'updated_at'])
        after_settings = self.fingerprint()
        self.assertNotEqual(after_settings, before)
        stage.duration_days = Decimal('4')
        stage.save(update_fields=['duration_days', 'updated_at'])
        after_stage = self.fingerprint()
        self.assertNotEqual(after_stage, after_settings)
        template.name = 'Revised workflow label'
        template.save(update_fields=['name', 'updated_at'])
        after_template = self.fingerprint()
        self.assertNotEqual(after_template, after_stage)
        alternative = WorkflowTemplate.objects.create(project=self.project, code='ALTERNATIVE', name='Alternative', status='active')
        WorkflowStage.objects.create(template=alternative, sequence=1, code='REVIEW', name='Review', duration_days=2)
        WorkflowTemplateOverride.objects.create(configuration=configuration, scope_type='deliverable',
                                                scope_key='Design basis', workflow_template=alternative)
        self.assertNotEqual(self.fingerprint(), after_template)

    def test_fact_review_and_selected_run_change_fingerprint(self):
        assertion = IntelligenceFact.objects.create(run=self.run, source_file=self.file,
            fact_type='deliverable', key='design-basis', value={'name': 'Design basis'}, status='detected')
        before = self.fingerprint()
        assertion.status = 'rejected'
        assertion.save(update_fields=['status', 'updated_at'])
        self.assertNotEqual(self.fingerprint(), before)
        second = self.completed_run()
        first_run_fingerprint = self.fingerprint()
        self.payload['generation_options']['intelligence_run_id'] = second.pk
        self.assertNotEqual(self.fingerprint(), first_run_fingerprint)

    def test_same_command_reuses_job_and_retains_queued_input_fingerprint(self):
        first, created = get_or_create_job(self.project, 'generate', self.payload, self.owner,
                                           idempotency_key='same-reviewed-plan')
        second, repeated = get_or_create_job(self.project, 'generate', self.payload, self.owner,
                                             idempotency_key='same-reviewed-plan')
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.result_data['planning_input_fingerprint'], self.fingerprint())
        self.assertEqual(PlanningJob.objects.filter(project=self.project).count(), 1)

    def test_same_custom_key_rejects_changed_workflow_stage(self):
        _, _, stage = self.configuration()
        get_or_create_job(self.project, 'generate', self.payload, self.owner, idempotency_key='reviewed-plan')
        stage.duration_days = Decimal('9')
        stage.save(update_fields=['duration_days', 'updated_at'])
        with self.assertRaises(PlanningPackageRequestError) as raised:
            get_or_create_job(PlanningProject.objects.get(pk=self.project.pk), 'generate', self.payload,
                              self.owner, idempotency_key='reviewed-plan')
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.code, 'planning_request_conflict')
        self.assertEqual(PlanningJob.objects.filter(project=self.project).count(), 1)

    def test_completed_job_replay_survives_created_nondefault_calendar(self):
        first, _ = get_or_create_job(self.project, 'generate', self.payload, self.owner,
                                     idempotency_key='materialized-plan')
        first.status = 'succeeded'
        first.result_data = {**first.result_data, 'generation_id': 17}
        first.save(update_fields=['status', 'result_data', 'updated_at'])
        WorkCalendar.objects.create(project=self.project, name='Planning Package 17 Calendar',
            is_default=False, working_weekdays=[0, 1, 2, 3, 4], hours_per_day=8, timezone='UTC')
        replay, created = get_or_create_job(PlanningProject.objects.get(pk=self.project.pk), 'generate',
            self.payload, self.owner, idempotency_key='materialized-plan')
        self.assertFalse(created)
        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(replay.result_data['generation_id'], 17)
