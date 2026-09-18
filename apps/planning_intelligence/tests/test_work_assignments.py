"""Real employee assignments appear only for the current responsible account."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.urls import include, path
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember, ProjectTask
from apps.dashboard.work_hub import WorkHubView
from apps.dashboard.work_hub_tasks import WorkHubTaskView
from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import Module, Organization, Permission, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.models import User

from .test_work_breakdown import WorkBreakdownFixture


urlpatterns = [
    path('api/v1/planning-intelligence/', include('apps.planning_intelligence.urls')),
    path('api/v1/dashboard/work-hub/', WorkHubView.as_view()),
    path('api/v1/dashboard/work-hub/tasks/<int:task_id>/', WorkHubTaskView.as_view()),
    path('api/v1/projects/', include('apps.core.project_urls')),
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class WorkAssignmentFixture(WorkBreakdownFixture):
    def setUp(self):
        super().setUp()
        self.enterprise = Project.objects.create(code='ASSIGN-001', name='Assignment project', owner=self.owner)
        # Attaching a canonical identity does not alter this fixture's evidence.
        type(self.project).objects.filter(pk=self.project.pk).update(enterprise_project=self.enterprise)
        self.project.refresh_from_db()
        self.organization = self.owner.rbac_profile.organization
        self.module, _ = Module.objects.get_or_create(code='project_control', defaults={'name': 'Project control', 'is_active': True})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        self.worker = self.employee('worker')
        self.reviewer = self.employee('reviewer')
        self.other_worker = self.employee('other-worker')
        self.worker_client = APIClient()
        self.worker_client.force_authenticate(self.worker.user)
        self.reviewer_client = APIClient()
        self.reviewer_client.force_authenticate(self.reviewer.user)
        self.lookup = f'/api/v1/planning-intelligence/projects/{self.project.pk}/eligible-employees/'

    def employee(self, name, *, organization=None, status='active'):
        user = User.objects.create_user(name, email=f'{name}@assignment.example.test', first_name=name.title(), last_name='Engineer')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization or self.organization})
        profile.organization = organization or self.organization
        profile.save(update_fields=['organization'])
        return EmployeeMaster.objects.create(
            user=user, employee_number=f'E-{name}', employee_code=f'E-{name}', emp_code=f'E-{name}',
            email=user.email, first_name=name.title(), last_name='Engineer', employment_status=status,
            join_date=timezone.localdate(),
        )

    def assign(self, *, task_type='task', reviewer=False):
        draft = self.read()
        draft['tasks'][0].update(assignee_id=self.worker.user_id, task_type=task_type,
                                 reviewer_id=self.reviewer.user_id if reviewer else None,
                                 due_date='2026-10-18', priority='high', effort_hours=24.1,
                                 acceptance_criteria='Checked engineering deliverable')
        response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def task_url(self, task=None):
        task = task or ProjectTask.objects.get(is_deleted=False)
        return f'/api/v1/dashboard/work-hub/tasks/{task.pk}/'

    def progress(self, client, *, status=None, progress=None, stamp=None, task=None):
        url = self.task_url(task)
        detail = client.get(url)
        self.assertEqual(detail.status_code, 200, detail.data)
        body = {'expected_updated_at': stamp or detail.data['updated_at']}
        if status is not None:
            body['status'] = status
        if progress is not None:
            body['progress_percent'] = progress
        return client.patch(url, body, format='json')

    def my_tasks(self, client):
        response = client.get('/api/v1/dashboard/work-hub/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['tasks']['rows']


class WorkAssignmentTests(WorkAssignmentFixture):
    def test_lookup_searches_canonical_active_employees_within_project_organization(self):
        foreign = Organization.objects.create(code='FOREIGN', name='Other organization')
        self.employee('foreign', organization=foreign)
        self.employee('exited', status='exited')
        locked = self.employee('locked')
        UserProfile.objects.filter(user=locked.user).update(locked_until=timezone.now() + timedelta(days=1))
        response = self.client.get(self.lookup, {'search': 'E-worker'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)
        row = response.data['results'][0]
        self.assertEqual(row['user_id'], self.worker.user_id)
        self.assertEqual(row['employee_id'], str(self.worker.pk))
        self.assertNotIn('current_base_salary', row)
        all_ids = {row['user_id'] for row in self.client.get(self.lookup).data['results']}
        self.assertEqual(all_ids, {self.worker.user_id, self.reviewer.user_id, self.other_worker.user_id})

    def test_nonmanager_project_engineer_cannot_assign_or_search_employee_directory(self):
        ProjectMember.objects.create(project=self.enterprise, user=self.outsider, role='engineer')
        self.client.force_authenticate(self.outsider)
        self.assertFalse(self.read()['permissions']['can_assign'])
        self.assertEqual(self.client.get(self.lookup).status_code, 403)
        draft = self.read()
        draft['tasks'][0]['assignee_id'] = self.worker.user_id
        self.assertEqual(self.save(draft).status_code, 403)
        self.assertFalse(ProjectTask.objects.exists())

    def test_save_immediately_publishes_once_without_granting_project_or_module_access(self):
        data = self.assign()
        first = ProjectTask.objects.get()
        stamp = first.updated_at
        self.assertEqual(data['tasks'][0]['project_task_id'], first.pk)
        self.assertEqual(data['tasks'][0]['assignee']['user_id'], self.worker.user_id)
        self.assertEqual(first.assigned_to_id, self.worker.user_id)
        self.assertEqual(first.status, 'todo')
        self.assertEqual(self.my_tasks(self.worker_client)[0]['id'], first.pk)
        self.assertFalse(ProjectMember.objects.filter(user=self.worker.user).exists())
        self.assertFalse(UserRole.objects.filter(user_profile=self.worker.user.rbac_profile).exists())
        repeated = self.save(data)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(ProjectTask.objects.count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.updated_at, stamp)

    def test_legacy_owner_name_is_preserved_without_guessing_an_employee(self):
        draft = self.read()
        draft['tasks'][0]['owner'] = self.worker.get_full_name()
        response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['tasks'][0]['owner'], self.worker.get_full_name())
        self.assertIsNone(response.data['tasks'][0]['assignee_id'])
        self.assertFalse(ProjectTask.objects.exists())

    def test_planned_reviewer_identity_survives_before_assigning_an_owner(self):
        draft = self.read()
        self.assertTrue(all(task['task_type'] == 'deliverable' for task in draft['tasks']))
        draft['tasks'][0]['reviewer_id'] = self.reviewer.user_id
        response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.data)
        task = self.read()['tasks'][0]
        self.assertEqual(task['reviewer_user']['user_id'], self.reviewer.user_id)
        self.assertEqual(task['reviewer'], self.reviewer.get_full_name())
        self.assertFalse(ProjectTask.objects.exists())

    def test_valid_long_title_and_large_effort_remain_assignable(self):
        draft = self.read()
        draft['tasks'][0].update(title='D' * 500, effort_hours=10000, assignee_id=self.worker.user_id)
        response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.data)
        task = ProjectTask.objects.get()
        self.assertEqual(task.title, 'D' * 500)
        self.assertEqual(task.estimated_hours, 10000)

    def test_invalid_inactive_foreign_and_self_review_assignments_are_atomic(self):
        foreign = Organization.objects.create(code='OTHER', name='Other organization')
        foreign_employee = self.employee('cross-org', organization=foreign)
        exited = self.employee('left-company', status='exited')
        for user_id in (9999999, foreign_employee.user_id, exited.user_id):
            draft = self.read()
            draft['tasks'][0]['assignee_id'] = user_id
            self.assertEqual(self.save(draft).status_code, 400)
        draft = self.read()
        draft['tasks'][0].update(assignee_id=self.worker.user_id, reviewer_id=self.worker.user_id)
        self.assertEqual(self.save(draft).status_code, 400)
        self.assertFalse(ProjectTask.objects.exists())
        self.assertEqual(self.read()['revision'], 0)

    def test_reassignment_reuses_task_and_revokes_old_assignee_immediately(self):
        draft = self.assign()
        task = ProjectTask.objects.get()
        draft['tasks'][0]['assignee_id'] = self.other_worker.user_id
        response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ProjectTask.objects.count(), 1)
        task.refresh_from_db()
        self.assertEqual(task.assigned_to_id, self.other_worker.user_id)
        self.assertEqual(self.worker_client.get(self.task_url(task)).status_code, 404)
        self.assertEqual(self.my_tasks(self.worker_client), [])
        self.worker_client.force_authenticate(self.other_worker.user)
        self.assertEqual(self.my_tasks(self.worker_client)[0]['id'], task.pk)

    def test_unassignment_and_removal_withdraw_without_deleting_audit_record(self):
        for remove in (False, True):
            draft = self.assign()
            task = ProjectTask.objects.get(is_deleted=False)
            if remove:
                draft['tasks'] = draft['tasks'][1:]
            else:
                draft['tasks'][0]['assignee_id'] = None
            response = self.save(draft)
            self.assertEqual(response.status_code, 200, response.data)
            task.refresh_from_db()
            self.assertTrue(task.is_deleted)
            self.assertEqual(self.my_tasks(self.worker_client), [])
            self.assertEqual(self.worker_client.get(self.task_url(task)).status_code, 404)

    def test_employee_updates_progress_and_manager_save_cannot_overwrite_it(self):
        draft = self.assign()
        response = self.progress(self.worker_client, status='in_progress', progress=35)
        self.assertEqual(response.status_code, 200, response.data)
        draft['tasks'][0].update(status='completed', progress_percent=100, priority='critical')
        response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['tasks'][0]['status'], 'in_progress')
        self.assertEqual(response.data['tasks'][0]['progress_percent'], 35)
        task = ProjectTask.objects.get()
        self.assertEqual(task.priority, 'critical')
        self.assertEqual(task.progress_percent, 35)

    def test_employee_progress_locks_workspace_before_task_like_manager_save(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row-lock ordering')
        self.assign()
        with CaptureQueriesContext(connection) as queries:
            response = self.progress(self.worker_client, status='in_progress', progress=10)
        self.assertEqual(response.status_code, 200, response.data)
        locks = [row['sql'].lower() for row in queries.captured_queries if 'for update' in row['sql'].lower()]
        self.assertGreaterEqual(len(locks), 2)
        self.assertIn(self.project._meta.db_table, locks[0])
        self.assertIn(ProjectTask._meta.db_table, locks[1])

    def test_deliverable_requires_review_and_reviewer_can_complete(self):
        self.assign(task_type='deliverable', reviewer=True)
        self.assertEqual(self.progress(self.worker_client, status='completed').status_code, 403)
        response = self.progress(self.worker_client, status='review', progress=100)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.my_tasks(self.reviewer_client)[0]['role'], 'reviewer')
        self.assertEqual(self.progress(self.reviewer_client, progress=45).status_code, 403)
        completed = self.progress(self.reviewer_client, status='completed')
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertEqual(completed.data['progress_percent'], 100)
        self.assertEqual(self.my_tasks(self.worker_client), [])
        self.assertEqual(self.my_tasks(self.reviewer_client), [])
        self.assertFalse(self.project.schedules.exists())

    def test_normal_task_assignee_can_complete_and_other_user_cannot_read(self):
        self.assign()
        other = APIClient()
        other.force_authenticate(self.other_worker.user)
        self.assertEqual(other.get(self.task_url()).status_code, 404)
        completed = self.progress(self.worker_client, status='completed')
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertEqual(completed.data['progress_percent'], 100)

    def test_project_manager_can_review_deliverable_without_named_reviewer(self):
        self.assign(task_type='deliverable')
        self.assertEqual(self.progress(self.worker_client, status='review').status_code, 200)
        self.assertEqual(self.my_tasks(self.client)[0]['role'], 'manager')
        completed = self.progress(self.client, status='completed')
        self.assertEqual(completed.status_code, 200, completed.data)

    def test_suspended_employee_loses_personal_work_and_cannot_update(self):
        self.assign()
        self.worker.employment_status = 'suspended'
        self.worker.save(update_fields=['employment_status'])
        self.assertEqual(self.worker_client.get(self.task_url()).status_code, 404)
        self.assertEqual(self.my_tasks(self.worker_client), [])

    def test_personal_endpoint_cannot_reassign_change_priority_or_apply_stale_updates(self):
        draft = self.assign()
        detail = self.worker_client.get(self.task_url()).data
        forbidden = self.worker_client.patch(self.task_url(), {
            'expected_updated_at': detail['updated_at'], 'priority': 'critical', 'status': 'in_progress',
        }, format='json')
        self.assertEqual(forbidden.status_code, 400)
        draft['tasks'][0]['due_date'] = '2026-11-20'
        self.assertEqual(self.save(draft).status_code, 200)
        stale = self.progress(self.worker_client, status='in_progress', stamp=detail['updated_at'])
        self.assertEqual(stale.status_code, 409)

    def test_explicit_deny_blocks_personal_task_access_and_updates(self):
        self.assign()
        permission = self.module.permissions.filter(action='update', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.worker.user.rbac_profile, permission=permission, allowed=False)
        self.assertEqual(self.progress(self.worker_client, status='in_progress').status_code, 403)
        read = self.module.permissions.filter(action='read', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.worker.user.rbac_profile, permission=read, allowed=False)
        self.assertEqual(self.worker_client.get(self.task_url()).status_code, 404)
        self.assertEqual(self.my_tasks(self.worker_client), [])

    def test_stale_wbs_save_and_failed_audit_do_not_publish_assignments(self):
        draft = self.read()
        changed = deepcopy(draft)
        changed['tasks'][0]['title'] = 'Changed planning task'
        self.assertEqual(self.save(changed).status_code, 200)
        draft['tasks'][0]['assignee_id'] = self.worker.user_id
        self.assertEqual(self.save(draft).status_code, 409)
        self.assertFalse(ProjectTask.objects.exists())
        draft = self.read()
        draft['tasks'][0]['assignee_id'] = self.worker.user_id
        with patch('apps.planning_intelligence.services.work_breakdown.record_event', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.save(draft)
        self.assertFalse(ProjectTask.objects.exists())
