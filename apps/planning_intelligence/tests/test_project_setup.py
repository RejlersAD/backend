"""One-click setup creates only the reviewed draft and preserves authorization."""
from copy import deepcopy
from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectTask
from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import Organization, RoleModule, RolePermission
from ..models import DocumentIntelligenceRun, GovernanceItem, PlanningProject, ScheduleBaseline, ScheduleVersion
from ..services.project_setup import PREVIEW_SALT, SetupAIUnavailable
from .test_work_assignments import WorkAssignmentFixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_work_assignments')
class ProjectSetupTests(WorkAssignmentFixture):
    def setUp(self):
        # Fixture user IDs repeat after rollback; throttle history must not leak
        # between independent tests or consume another test's request budget.
        cache.clear()
        super().setUp()
        RoleModule.objects.get_or_create(role=self.role, module=self.module)
        for permission in self.module.permissions.filter(action__in=['read', 'create', 'update'], is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.brief = {
            'code': 'SOFTWARE-PHASE1', 'name': 'Internal application launch',
            'description': 'Test and launch developed application workflows, fix defects, complete UAT and support handover.',
            'project_type': 'software', 'department': 'ICT', 'phase': 'Phase 1',
            'start_date': '2026-09-21', 'end_date': '2026-12-20',
            'project_manager_id': self.reviewer.user_id,
            'team_member_ids': [self.worker.user_id], 'generation_mode': 'template',
        }
        self.base = '/api/v1/planning-intelligence/project-setup/'

    def preview(self, **changes):
        response = self.client.post(self.base + 'preview/', {**self.brief, **changes}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def create(self, preview):
        return self.client.post(self.base + 'create/', preview, format='json')

    def test_template_preview_does_not_create_a_project_tasks_or_document_evidence(self):
        before = (Project.objects.count(), PlanningProject.objects.count(), ProjectTask.objects.count(), DocumentIntelligenceRun.objects.count())
        result = self.preview()
        self.assertEqual(result['plan']['source'], 'template')
        self.assertTrue(result['plan']['warnings'])
        self.assertEqual(result['plan']['project']['planning_mode'], 'manual')
        self.assertEqual(before, (Project.objects.count(), PlanningProject.objects.count(), ProjectTask.objects.count(), DocumentIntelligenceRun.objects.count()))
        first = result['plan']['tasks'][0]
        self.assertEqual(first['planned_start_date'], '2026-09-21')
        self.assertEqual(first['due_date'], '2026-09-22')

    def test_create_materializes_assignments_milestones_risks_and_calculated_draft_once(self):
        preview = self.preview()
        preview['plan']['tasks'][0]['title'] = 'Reviewed scope acceptance'
        response = self.create(preview)
        self.assertEqual(response.status_code, 201, response.data)
        enterprise = Project.objects.get(code=self.brief['code'])
        workspace = enterprise.planning_workspace
        self.assertEqual(workspace.planning_mode, 'manual')
        self.assertEqual(workspace.manual_work_breakdown['tasks'][0]['title'], 'Reviewed scope acceptance')
        self.assertFalse(workspace.files.exists())
        self.assertFalse(DocumentIntelligenceRun.objects.filter(project=workspace).exists())
        self.assertEqual(enterprise.memberships.get(user=self.reviewer.user).role, 'project_manager')
        self.assertEqual(enterprise.memberships.get(user=self.worker.user).role, 'viewer')
        self.assertEqual(enterprise.tasks.filter(is_deleted=False).count(), len(preview['plan']['tasks']))
        self.assertTrue(any(row['title'] == preview['plan']['tasks'][1]['title'] for row in self.my_tasks(self.worker_client)))
        version = ScheduleVersion.objects.get(pk=response.data['schedule_version_id'])
        self.assertEqual(version.status, 'calculated')
        self.assertTrue(all(item.planned_start and item.planned_finish and item.duration_days > 0 for item in version.activities.all()))
        reviewed = {task['id']: task for task in preview['plan']['tasks']}
        for activity in version.activities.all():
            self.assertEqual(activity.planned_start.isoformat(), reviewed[activity.external_id]['planned_start_date'])
            self.assertEqual(activity.planned_finish.isoformat(), reviewed[activity.external_id]['due_date'])
        self.assertEqual(workspace.work_calendars.count(), 1)
        self.assertEqual(enterprise.milestones.count(), len(preview['plan']['milestones']))
        self.assertEqual(GovernanceItem.objects.filter(version=version, item_type='risk').count(), len(preview['plan']['risks']))
        self.assertFalse(ScheduleBaseline.objects.filter(schedule__project=workspace).exists())
        repeat = self.create(preview)
        self.assertEqual(repeat.status_code, 200, repeat.data)
        self.assertTrue(repeat.data['repeated'])
        self.assertEqual(repeat.data['enterprise_project']['id'], enterprise.pk)
        self.assertEqual(workspace.schedules.count(), 1)
        self.assertEqual(version.schedule.versions.count(), 1)

    def test_options_exposes_only_current_organization_employees_and_no_credentials(self):
        foreign = Organization.objects.create(code='SETUP-FOREIGN', name='Another organization')
        outsider = self.employee('setup-foreign', organization=foreign)
        response = self.client.get(self.base + 'options/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(outsider.user_id, {person['user_id'] for person in response.data['employees']})
        self.assertNotIn('api_key', str(response.data))

    def test_creation_rechecks_employee_eligibility(self):
        preview = self.preview()
        self.worker.employment_status = 'exited'
        self.worker.save(update_fields=['employment_status'])
        response = self.create(preview)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Project.objects.filter(code=self.brief['code']).exists())

    def test_repeat_cannot_disclose_project_after_creator_access_is_revoked(self):
        preview = self.preview()
        self.assertEqual(self.create(preview).status_code, 201)
        enterprise = Project.objects.get(code=self.brief['code'])
        enterprise.owner = self.outsider
        enterprise.save(update_fields=['owner'])
        enterprise.memberships.filter(user=self.owner).delete()
        self.assertEqual(self.create(preview).status_code, 403)

    def test_creator_can_manage_as_owner_without_self_assigning_manager_role(self):
        EmployeeMaster.objects.create(user=self.owner, employee_number='SETUP-OWNER', employee_code='SETUP-OWNER',
                                      emp_code='SETUP-OWNER', email=self.owner.email, first_name='Setup', last_name='Owner',
                                      employment_status='active', join_date=timezone.localdate())
        preview = self.preview(project_manager_id=self.owner.pk)
        response = self.create(preview)
        self.assertEqual(response.status_code, 201, response.data)
        enterprise = Project.objects.get(code=self.brief['code'])
        self.assertEqual(enterprise.owner_id, self.owner.pk)
        self.assertFalse(enterprise.memberships.filter(user=self.owner, role='project_manager').exists())

    def test_preview_token_tampering_and_other_actor_are_rejected(self):
        preview = self.preview()
        altered = {**preview, 'preview_token': preview['preview_token'] + 'x'}
        self.assertEqual(self.create(altered).status_code, 400)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.create(preview).status_code, 403)

    def test_expired_preview_requires_regeneration(self):
        preview = self.preview()
        with patch('apps.planning_intelligence.services.project_setup.signing.loads', side_effect=signing.SignatureExpired()):
            self.assertEqual(self.create(preview).status_code, 400)
        self.assertFalse(Project.objects.filter(code=self.brief['code']).exists())

    def test_edited_plan_cannot_change_identity_or_selected_manager(self):
        preview = self.preview()
        preview['plan']['project'].update(code='FORGED', project_manager_id=self.outsider.pk)
        response = self.create(preview)
        self.assertEqual(response.status_code, 201, response.data)
        enterprise = Project.objects.get(code=self.brief['code'])
        self.assertEqual(enterprise.memberships.get(role='project_manager').user_id, self.reviewer.user_id)
        self.assertFalse(Project.objects.filter(code='FORGED').exists())

    def test_cross_team_assignment_and_cyclic_dependencies_fail_before_writes(self):
        preview = self.preview()
        malicious = deepcopy(preview)
        malicious['plan']['tasks'][0]['assignee_id'] = self.other_worker.user_id
        self.assertEqual(self.create(malicious).status_code, 400)
        cyclic = deepcopy(preview)
        cyclic['plan']['tasks'][0]['depends_on'] = [cyclic['plan']['tasks'][-1]['id']]
        self.assertEqual(self.create(cyclic).status_code, 400)
        self.assertFalse(Project.objects.filter(code=self.brief['code']).exists())

    def test_downstream_schedule_failure_rolls_back_everything(self):
        preview = self.preview()
        before = (Project.objects.count(), PlanningProject.objects.count(), ProjectTask.objects.count())
        with patch('apps.planning_intelligence.services.cpm.calculate_schedule_version', side_effect=ValueError('test schedule failure')):
            with self.assertRaises(ValueError):
                self.create(preview)
        self.assertEqual(before, (Project.objects.count(), PlanningProject.objects.count(), ProjectTask.objects.count()))

    def test_ai_unavailable_does_not_silently_substitute_template(self):
        with override_settings(OPENAI_API_KEY=''):
            response = self.client.post(self.base + 'preview/', {**self.brief, 'generation_mode': 'ai'}, format='json')
        self.assertEqual(response.status_code, 503, response.data)
        self.assertFalse(Project.objects.filter(code=self.brief['code']).exists())

    def test_invalid_provider_credentials_return_actionable_message_without_secret(self):
        from ..services.project_setup import generate_ai
        authentication_error = type('AuthenticationError', (Exception,), {})('private-provider-error-content')
        with override_settings(OPENAI_API_KEY='test-not-a-real-key'), patch('openai.OpenAI') as client, patch('apps.rbac.ai_telemetry.record_usage'):
            client.return_value.chat.completions.create.side_effect = authentication_error
            with self.assertRaises(SetupAIUnavailable) as caught:
                generate_ai(self.brief, {}, self.owner)
        self.assertIn('server credentials', str(caught.exception.detail))
        self.assertNotIn('private-provider-error-content', str(caught.exception.detail))

    def test_placeholder_key_disables_ai_and_never_contacts_provider(self):
        from ..services.project_setup import generate_ai
        before = (Project.objects.count(), PlanningProject.objects.count(), ProjectTask.objects.count())
        for key in ['sk-local-placeholder-only', 'your-openai-api-key', 'sk-dummy', 'replace_with_key']:
            with self.subTest(key=key), override_settings(OPENAI_API_KEY=key), patch('openai.OpenAI') as client:
                options = self.client.get(self.base + 'options/')
                self.assertEqual(options.status_code, 200)
                self.assertFalse(options.data['ai_available'])
                self.assertIn('placeholder AI key', options.data['ai_message'])
                self.assertNotIn(key, str(options.data))
                with self.assertRaises(SetupAIUnavailable):
                    generate_ai(self.brief, {}, self.owner)
                client.assert_not_called()
        self.assertEqual(before, (Project.objects.count(), PlanningProject.objects.count(), ProjectTask.objects.count()))

    def test_whitespace_key_is_unconfigured_and_template_preview_remains_available(self):
        with override_settings(OPENAI_API_KEY='   '):
            options = self.client.get(self.base + 'options/')
            self.assertFalse(options.data['ai_available'])
            self.assertIn('not configured', options.data['ai_message'])
            self.assertEqual(self.preview()['plan']['source'], 'template')
        self.assertFalse(Project.objects.filter(code=self.brief['code']).exists())

    def test_configured_key_is_trimmed_without_exposing_or_prevalidating_it(self):
        from ..services.project_setup import generate_ai
        with override_settings(OPENAI_API_KEY='  opaque-configured-credential  '), patch('openai.OpenAI') as client, patch('apps.rbac.ai_telemetry.record_usage'):
            options = self.client.get(self.base + 'options/')
            self.assertTrue(options.data['ai_available'])
            self.assertEqual(options.data['ai_message'], '')
            client.assert_not_called()
            client.return_value.chat.completions.create.side_effect = RuntimeError('provider offline')
            with self.assertRaises(SetupAIUnavailable):
                generate_ai(self.brief, {}, self.owner)
            client.assert_called_once_with(api_key='opaque-configured-credential', timeout=50, max_retries=0)

    def test_ai_output_is_validated_and_scheduled_before_returning_a_signed_preview(self):
        from ..services.project_setup import generate_template
        from ..project_setup_serializers import ProjectSetupBriefSerializer
        serializer = ProjectSetupBriefSerializer(data=self.brief)
        serializer.is_valid(raise_exception=True)
        raw = generate_template(serializer.validated_data)
        with patch('apps.planning_intelligence.services.project_setup.generate_ai', return_value=raw):
            result = self.preview(generation_mode='ai')
        self.assertEqual(result['plan']['source'], 'ai')
        raw['tasks'][0]['depends_on'] = ['missing-task']
        with patch('apps.planning_intelligence.services.project_setup.generate_ai', return_value=raw):
            response = self.client.post(self.base + 'preview/', {**self.brief, 'generation_mode': 'ai'}, format='json')
        self.assertEqual(response.status_code, 503)

    def test_forecast_overrun_is_visible_without_moving_target(self):
        result = self.preview(end_date='2026-10-01')
        self.assertEqual(result['plan']['project']['end_date'], '2026-10-01')
        self.assertTrue(any('after the target' in warning for warning in result['plan']['warnings']))

    def test_unauthorized_user_and_invalid_dates_cannot_start_setup(self):
        unauthenticated = APIClient()
        self.assertIn(unauthenticated.get(self.base + 'options/').status_code, [401, 403])
        self.client.force_authenticate(self.worker.user)
        self.assertEqual(self.client.get(self.base + 'options/').status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.base + 'preview/', {**self.brief, 'end_date': '2026-09-01'}, format='json').status_code, 400)
