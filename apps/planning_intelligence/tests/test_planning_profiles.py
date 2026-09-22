"""Planning policy approvals are versioned, scoped and distinct from source facts."""
from copy import deepcopy
from datetime import date

from django.core.exceptions import ValidationError as ModelValidationError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.rbac.models import Module, Permission
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User
from ..models import PlanningProject, PlanningAuditEvent, ScheduleVersion, ScheduleBaseline, WorkCalendar, CalendarException
from ..planning_profile_models import PlanningProfile, ProjectPlanningProfileSelection
from ..planning_profile_views import PlanningProfileView
from ..services.planning_profiles import planning_profile_selection, selected_approved_rule
from ..workflow_models import WorkflowTemplate, WorkflowStage, EngineeringDependencyTemplate, EngineeringDependencyRule, ProjectScheduleConfiguration
from .test_business_approval_gates import grant_test_approval


PREFIX = 'api/v1/planning-intelligence/projects/<int:project_id>/planning-profiles/'
urlpatterns = [
    path(PREFIX, PlanningProfileView.as_view()),
    path(PREFIX + 'select/', PlanningProfileView.as_view(operation='select')),
    path(PREFIX + '<int:profile_id>/', PlanningProfileView.as_view()),
    *[path(PREFIX + '<int:profile_id>/' + operation + '/', PlanningProfileView.as_view(operation=operation))
      for operation in ('propose', 'approve', 'reject', 'revise')],
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class PlanningProfileTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='profile-owner', email='profile-owner@example.test')
        self.engineer = User.objects.create_user(username='profile-engineer', email='profile-engineer@example.test')
        self.reviewer = User.objects.create_user(username='profile-reviewer', email='profile-reviewer@example.test')
        self.outsider = User.objects.create_user(username='profile-outsider', email='profile-outsider@example.test')
        self.admin = User.objects.create_user(username='profile-admin', email='profile-admin@example.test', is_staff=True, is_superuser=True)
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        grant_test_approval((self.owner, self.engineer, self.reviewer, self.outsider, self.admin))
        self.enterprise = Project.objects.create(code='PROFILE-001', name='Project policy review', owner=self.owner)
        ProjectMember.objects.create(project=self.enterprise, user=self.engineer, role='engineer')
        ProjectMember.objects.create(project=self.enterprise, user=self.reviewer, role='reviewer')
        self.project = PlanningProject.objects.create(name='Project policy review', enterprise_project=self.enterprise,
                                                      created_by=self.owner, simple_planning_state={'revision': 7, 'tasks': []})
        self.other = PlanningProject.objects.create(name='Other project', created_by=self.outsider)
        self.workflow = WorkflowTemplate.objects.create(project=self.project, code='PROJECT_RELEASE', name='Project release policy',
                                                        version=2, status='active', created_by=self.owner)
        self.codes = ['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE']
        for index, code in enumerate(self.codes):
            WorkflowStage.objects.create(template=self.workflow, sequence=index + 1, code=code, name=code.replace('_', ' '),
                duration_days=[2, 3, 1, 2, 1][index], responsible_party='Company' if index in (1, 3) else 'Engineer',
                relationship_to_previous='' if index == 0 else 'FS', lag_days=0, progress_weight=20)
        self.dependencies = EngineeringDependencyTemplate.objects.create(project=self.project, code='EXPLICIT_RELEASE',
            name='Reviewed release dependency', status='active', version=3)
        self.link = EngineeringDependencyRule.objects.create(template=self.dependencies, predecessor_code='PKG-A',
            predecessor_name='Procurement inspection', predecessor_stage_code='FINAL_ISSUE', successor_code='PKG-B',
            successor_name='Commissioning handover', successor_stage_code='IFR', relationship_type='FS', lag_days=2,
            rationale='Project-specific release policy selected for approval.', source_reference='Policy revision B')
        self.calendar = WorkCalendar.objects.create(project=self.project, name='Project contract calendar',
                                                    working_weekdays=[0, 1, 2, 3, 4], hours_per_day=8, timezone='Asia/Dubai')
        CalendarException.objects.create(calendar=self.calendar, date=date(2026, 12, 2), is_working=False, name='Holiday')
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/planning-profiles/'
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def payload(self, **extra):
        return {'code': 'RELEASE-POLICY', 'name': 'Release policy', 'workflow_template_id': self.workflow.pk,
                'final_gate_label': 'IFT / IFM', **extra}

    def create(self, **extra):
        response = self.client.post(self.url, self.payload(**extra), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['profile']

    def decide(self, profile, operation, status=200):
        response = self.client.post(f"{self.url}{profile['id']}/{operation}/", {
            'revision': profile['revision'], 'reason': f'Explicit {operation} after policy review.',
        }, format='json')
        self.assertEqual(response.status_code, status, response.data)
        return response.data.get('profile', response.data)

    def approve(self, **extra):
        return self.decide(self.decide(self.create(**extra), 'propose'), 'approve')

    def select(self, profile, revision=0, status=200):
        response = self.client.post(self.url + 'select/', {'profile_id': profile['id'], 'selection_revision': revision,
                                                         'reason': 'Use this reviewed version for future planning.'}, format='json')
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    def test_collection_is_read_only_and_does_not_choose_or_seed_defaults(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['profiles'], [])
        self.assertEqual(response.data['selection']['revision'], 0)
        self.assertFalse(response.data['selection']['valid'])
        self.assertFalse(response.data['capabilities']['schedule_generation'])
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
        self.assertIn(self.workflow.pk, [row['id'] for row in response.data['options']['workflow_templates']])
        self.assertFalse(ProjectPlanningProfileSelection.objects.exists())

    def test_approval_and_selection_freeze_policy_with_trace_but_never_generate_schedule(self):
        profile = self.approve(dependency_template_id=self.dependencies.pk, approved_dependency_rule_ids=[self.link.pk],
            wbs_convention={'levels': ['project', 'discipline', 'deliverable', 'workflow_stage'], 'code_separator': '.'},
            calendar_policy={'mode': 'project_calendar', 'calendar_id': self.calendar.pk},
            progress_policy={'mode': 'workflow_weights'}, resource_policy={'mode': 'workflow_roles'})
        self.assertEqual(profile['definition']['workflow']['stages'][-1]['name'], 'IFT / IFM')
        snapshot = deepcopy(profile['approved_snapshot'])
        self.assertEqual(snapshot['approval']['actor_id'], self.owner.pk)
        self.assertEqual(snapshot['approval']['profile_revision'], 3)
        self.assertEqual(snapshot['content_fingerprint'], profile['content_fingerprint'])
        self.assertFalse(snapshot['source_fact'])
        self.assertTrue(all(rule['source_fact'] is False for rule in snapshot['definition']['rules']))
        self.assertEqual(snapshot['definition']['calendar_policy']['snapshot']['exceptions'], [{'date': '2026-12-02', 'is_working': False}])
        selected = self.select(profile)['selection']
        self.assertTrue(selected['valid'])
        self.assertEqual(selected['snapshot'], snapshot)
        self.assertEqual(selected['revision'], 1)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {'revision': 7, 'tasks': []})
        self.assertFalse(ScheduleVersion.objects.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertFalse(ProjectScheduleConfiguration.objects.exists())
        self.assertEqual(list(PlanningAuditEvent.objects.filter(project=self.project).order_by('created_at').values_list('action', flat=True)),
                         ['planning_profile.created', 'planning_profile.propose', 'planning_profile.approve', 'planning_profile.selected'])

    def test_unspecified_policies_stay_unspecified_and_dependency_library_is_not_auto_applied(self):
        profile = self.create(dependency_template_id=self.dependencies.pk)
        definition = profile['definition']
        self.assertEqual(definition['calendar_policy'], {'mode': 'not_specified', 'snapshot': None})
        self.assertIsNone(definition['progress_policy']['weights'])
        self.assertIsNone(definition['resource_policy']['roles'])
        self.assertEqual(definition['wbs_convention']['levels'], [])
        self.assertFalse(any(row['kind'] == 'dependency' for row in definition['rules']))
        self.assertEqual([row['value']['value'] for row in definition['rules'] if row['kind'] == 'stage_duration'], [2, 3, 1, 2, 1])
        self.assertFalse(PlanningProfile.objects.get(pk=profile['id']).approved_snapshot)

    def test_project_engineer_can_propose_but_only_business_authority_can_approve_select(self):
        self.client.force_authenticate(self.engineer)
        profile = self.decide(self.create(), 'propose')
        self.assertFalse(profile['permissions']['can_approve'])
        self.decide(profile, 'approve', status=403)
        self.client.force_authenticate(self.admin)
        self.decide(profile, 'approve', status=403)
        self.client.force_authenticate(self.owner)
        profile = self.decide(profile, 'approve')
        self.client.force_authenticate(self.engineer)
        self.select(profile, status=403)
        self.client.force_authenticate(self.admin)
        self.select(profile, status=403)

    def test_read_only_member_and_outsider_cannot_write_or_see_other_project_profiles(self):
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 403)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, self.payload(), format='json').status_code, 404)

    def test_client_cannot_forge_approval_snapshot_revision_or_status(self):
        for values in ({'status': 'approved'}, {'approved_snapshot': {}}, {'revision': 1}, {'approved_by_id': self.owner.pk}):
            response = self.client.post(self.url, self.payload(**values), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PlanningProfile.objects.exists())

    def test_draft_revision_and_selection_revision_conflicts_are_atomic(self):
        profile = self.create()
        changed = self.client.patch(f"{self.url}{profile['id']}/", {'revision': 1, 'name': 'Reviewed name'}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        stale = self.client.patch(f"{self.url}{profile['id']}/", {'revision': 1, 'name': 'Overwrite'}, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        self.decide(profile, 'propose', status=409)
        profile = self.decide(self.decide(changed.data['profile'], 'propose'), 'approve')
        self.select(profile)
        before = planning_profile_selection(self.project)
        self.select(profile, status=409)
        self.assertEqual(planning_profile_selection(self.project), before)

    def test_approved_version_cannot_be_changed_and_new_revision_does_not_change_selection(self):
        approved = self.approve()
        self.select(approved)
        before = planning_profile_selection(self.project)
        blocked = self.client.patch(f"{self.url}{approved['id']}/", {'revision': approved['revision'], 'name': 'Changed'}, format='json')
        self.assertEqual(blocked.status_code, 409, blocked.data)
        revised = self.decide(approved, 'revise', status=201)
        self.assertEqual(revised['status'], 'draft')
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['supersedes_id'], approved['id'])
        self.assertEqual(revised['revision'], 1)
        self.assertEqual(revised['approved_snapshot'], {})
        self.assertNotEqual(revised['content_fingerprint'], approved['content_fingerprint'])
        self.assertEqual(planning_profile_selection(self.project), before)
        row = PlanningProfile.objects.get(pk=approved['id'])
        row.name = 'Model bypass'
        with self.assertRaises(ModelValidationError):
            row.save()
        if connection.vendor == 'postgresql':
            with self.assertRaises(DatabaseError), transaction.atomic():
                PlanningProfile.objects.filter(pk=row.pk).update(name='Bulk bypass')
            with self.assertRaises(DatabaseError), transaction.atomic():
                PlanningProfile.objects.filter(pk=row.pk).delete()

    def test_unapproved_versions_cannot_be_selected_and_rejection_keeps_decision_history(self):
        profile = self.create()
        self.select(profile, status=409)
        profile = self.decide(profile, 'propose')
        self.select(profile, status=409)
        rejected = self.decide(profile, 'reject')
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(rejected['decided_by_id'], self.owner.pk)
        self.assertIsNone(rejected['approved_by_id'])
        self.select(rejected, status=409)
        revised = self.decide(rejected, 'revise', status=201)
        self.assertEqual(revised['supersedes_id'], rejected['id'])

    def test_changed_template_or_calendar_requires_new_review_not_silent_approval(self):
        profile = self.decide(self.create(calendar_policy={'mode': 'project_calendar', 'calendar_id': self.calendar.pk}), 'propose')
        self.calendar.hours_per_day = 7
        self.calendar.save(update_fields=['hours_per_day'])
        self.decide(profile, 'approve', status=409)
        row = PlanningProfile.objects.get(pk=profile['id'])
        self.assertEqual(row.status, 'proposed')
        self.assertFalse(row.approved_snapshot)
        profile = self.decide(self.decide(profile, 'reject'), 'revise', status=201)
        refreshed = self.client.patch(f"{self.url}{profile['id']}/", {'revision': 1}, format='json')
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.workflow.stages.filter(code='IFR').update(duration_days=4)
        self.decide(refreshed.data['profile'], 'propose', status=409)

    def test_live_library_changes_do_not_mutate_selected_approved_snapshot(self):
        profile = self.approve()
        self.select(profile)
        before = planning_profile_selection(self.project)
        self.workflow.stages.filter(code='IFR').update(duration_days=17)
        self.assertEqual(planning_profile_selection(self.project), before)
        rule = next(rule for rule in profile['definition']['rules'] if rule['kind'] == 'stage_duration')
        resolved = selected_approved_rule(self.project, rule['id'], content_fingerprint=profile['content_fingerprint'])
        self.assertEqual(resolved['rule']['value'], {'value': 2.0, 'unit': 'working_days'})
        self.assertEqual(resolved['approval']['actor_id'], self.owner.pk)
        self.assertFalse(resolved['source_fact'])
        self.assertIsNone(selected_approved_rule(self.project, rule['id'].upper()))
        self.assertIsNone(selected_approved_rule(self.project, rule['id'], content_fingerprint='wrong'))
        self.assertIsNone(selected_approved_rule(self.other, rule['id']))

    def test_corrupted_selection_snapshot_fails_closed(self):
        profile = self.approve()
        self.select(profile)
        ProjectPlanningProfileSelection.objects.filter(project=self.project).update(approved_snapshot={'forged': True})
        selection = planning_profile_selection(self.project)
        self.assertFalse(selection['valid'])
        self.assertIsNone(selection['snapshot'])
        self.assertIsNone(selected_approved_rule(self.project, profile['definition']['rules'][0]['id']))

    def test_cross_project_template_rule_calendar_and_profile_are_rejected(self):
        foreign_template = WorkflowTemplate.objects.create(project=self.other, code='OTHER', name='Other', status='active')
        foreign_calendar = WorkCalendar.objects.create(project=self.other, name='Other', working_weekdays=[0], hours_per_day=7)
        for extra in ({'workflow_template_id': foreign_template.pk},
                      {'calendar_policy': {'mode': 'project_calendar', 'calendar_id': foreign_calendar.pk}},
                      {'approved_dependency_rule_ids': [self.link.pk]},
                      {'dependency_template_id': self.dependencies.pk, 'approved_dependency_rule_ids': [self.link.pk + 1000]}):
            response = self.client.post(self.url, self.payload(**extra), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PlanningProfile.objects.exists())
        profile = self.approve()
        self.client.force_authenticate(self.admin)
        other_url = f'/api/v1/planning-intelligence/projects/{self.other.pk}/planning-profiles/'
        self.assertEqual(self.client.get(f"{other_url}{profile['id']}/").status_code, 404)

    def test_invalid_or_conflicting_policy_input_is_not_silently_ignored(self):
        bad_values = [
            {'wbs_convention': {'levels': ['project', 'project']}},
            {'wbs_convention': {'levels': ['filename_guess']}},
            {'stage_duration_overrides': {'IFR': {'value': 2, 'unit': 'unknown'}}},
            {'stage_duration_overrides': {'IFR': {'value': '2', 'unit': 'working_days'}}},
            {'stage_duration_overrides': {'UNKNOWN': {'value': 2, 'unit': 'working_days'}}},
            {'progress_policy': {'mode': 'explicit_weights', 'weights': {code: 10 for code in self.codes}}},
            {'progress_policy': {'mode': 'workflow_weights', 'weights': {code: 20 for code in self.codes}}},
            {'resource_policy': {'mode': 'workflow_roles', 'roles': {code: 'Other' for code in self.codes}}},
            {'resource_policy': {'mode': 'explicit_roles', 'roles': {code: '' for code in self.codes}}},
            {'calendar_policy': {'mode': 'not_specified', 'calendar_id': self.calendar.pk}},
            {'calendar_policy': {'mode': 'project_calendar', 'calendar_id': self.calendar.pk, 'calendar': {}}},
            {'calendar_policy': {'mode': 'project_calendar', 'calendar_id': 'not-an-id'}},
            {'calendar_policy': {'mode': 'project_calendar', 'calendar_id': True}},
        ]
        for extra in bad_values:
            with self.subTest(extra=extra):
                response = self.client.post(self.url, self.payload(**extra), format='json')
                self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PlanningProfile.objects.exists())

    def test_unordered_selected_rule_ids_have_stable_canonical_approval(self):
        second = EngineeringDependencyRule.objects.create(template=self.dependencies, predecessor_code='PKG-B',
            predecessor_name='Commissioning handover', predecessor_stage_code='FINAL_ISSUE', successor_code='PKG-C',
            successor_name='Operations acceptance', successor_stage_code='IFR', relationship_type='FS', lag_days=0)
        profile = self.approve(dependency_template_id=self.dependencies.pk,
                               approved_dependency_rule_ids=[second.pk, self.link.pk])
        selected = profile['definition']['configuration']['approved_dependency_rule_ids']
        self.assertEqual(selected, sorted([self.link.pk, second.pk]))
        self.assertEqual([rule['value']['id'] for rule in profile['definition']['rules'] if rule['kind'] == 'dependency'], selected)
        self.assertEqual(profile['status'], 'approved')

    def test_explicit_duration_units_are_retained_as_policy_not_extracted_dates(self):
        profile = self.approve(stage_duration_overrides={'IFR': {'value': 12, 'unit': 'hours'}},
            calendar_policy={'mode': 'explicit', 'calendar': {'working_weekdays': [0, 1, 2, 3],
                'hours_per_day': 6, 'timezone': 'Asia/Dubai', 'exceptions': []}})
        rule = next(rule for rule in profile['definition']['rules'] if rule['kind'] == 'stage_duration' and rule['stage_code'] == 'IFR')
        self.assertEqual(rule['value'], {'value': 12, 'unit': 'hours'})
        self.assertEqual(rule['basis'], 'project_override')
        self.assertEqual(rule['provenance_type'], 'planning_rule')
        self.assertFalse(rule['source_fact'])
        self.assertFalse(ScheduleVersion.objects.exists())
