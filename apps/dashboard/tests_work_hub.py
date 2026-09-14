"""Personal scope, source availability, date boundaries and read-only guarantees."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.activity.models import SystemActivity
from apps.core.project_models import Project, ProjectTask
from apps.dashboard import work_hub
from apps.payroll.models import DailyWorkLog, EmployeeLeaveRecord, LeaveRequest, LeaveType, PublicHoliday
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.ai_champion_models import ActivityEvent


class WorkHubTests(TestCase):
    now = datetime(2026, 9, 16, 20, 30, tzinfo=dt_timezone.utc)  # 17 September in Dubai.
    today = date(2026, 9, 17)
    url = '/api/v1/dashboard/work-hub/'

    def setUp(self):
        self.enterContext(timezone.override(ZoneInfo('Asia/Dubai')))
        self.enterContext(patch('apps.dashboard.work_hub.timezone.now', return_value=self.now))
        User = get_user_model()
        self.user = User.objects.create_user('work-hub-self', email='self@example.test')
        self.other = User.objects.create_user('work-hub-other', email='other@example.test')
        organization, _ = Organization.objects.get_or_create(code='work-hub-test', defaults={'name': 'Work Hub test'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization})
        self.profile.employee_id = 'SELF-001'
        self.profile.status = 'active'
        self.profile.save()
        self.profile.roles.clear()
        self.role = Role.objects.create(code='work-hub-reader', name='Work Hub reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.project = Project.objects.create(code='WORK-HUB-1', name='Own project', owner=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def grant_tasks(self):
        module, _ = Module.objects.get_or_create(code='project_control', defaults={'name': 'Project Control'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        return module

    def response(self, params=None):
        response = self.client.get(self.url, params or {'year': 2026, 'month': 9})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def task(self, **fields):
        return ProjectTask.objects.create(project=fields.pop('project', self.project),
                                          assigned_to=fields.pop('assigned_to', self.user),
                                          title=fields.pop('title', 'Assigned task'), **fields)

    def ledger(self, **fields):
        return EmployeeLeaveRecord.objects.create(employee_code=fields.pop('employee_code', self.profile.employee_id),
                                                   employee_name='Ledger person', year=fields.pop('year', 2026), **fields)

    def log(self, **fields):
        return DailyWorkLog.objects.create(user=fields.pop('user', self.user),
                                          log_date=fields.pop('log_date', self.today), task_title='Recorded activity', **fields)

    def leave_request(self, **fields):
        leave_type, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual leave'})
        return LeaveRequest.objects.create(employee=fields.pop('employee', self.user),
                                           leave_type=leave_type, employee_name='Leave requester',
                                           start_date=fields.pop('start_date', self.today),
                                           end_date=fields.pop('end_date', self.today), **fields)

    def workspace_view(self, path='/profile', **fields):
        return ActivityEvent.objects.create(user=fields.pop('user', self.user),
            timestamp=fields.pop('timestamp', self.now), action_type=fields.pop('action_type', 'view'),
            application=fields.pop('application', 'profile'), feature=fields.pop('feature', 'index'),
            metadata=fields.pop('metadata', {'source': 'frontend-route', 'path': path}), **fields)

    def test_authentication_active_account_and_get_only(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_month_bounds_and_local_defaults(self):
        for params in ({'year': 1999}, {'year': 2101}, {'year': 'invalid'}, {'month': 0}, {'month': 13}, {'month': '1.5'}):
            self.assertEqual(self.client.get(self.url, params).status_code, 400)
        data = self.client.get(self.url).data
        self.assertEqual(data['period'], {'year': 2026, 'month': 9, 'start': '2026-09-01', 'end': '2026-09-30', 'timezone': 'Asia/Dubai'})
        self.assertEqual(self.response({'year': 2024, 'month': 2})['period']['end'], '2024-02-29')

    def test_ungranted_task_source_is_unknown_not_empty(self):
        self.task(due_date=self.today)
        data = self.response()['tasks']
        self.assertEqual(data['status'], 'unavailable')
        self.assertIsNone(data['counts']['due'])
        self.assertIsNone(data['route'])
        self.assertEqual(data['rows'], [])

    def test_tasks_are_only_own_open_records_and_due_uses_local_date(self):
        self.grant_tasks()
        overdue = self.task(due_date=date(2026, 9, 16))
        due = self.task(due_date=self.today)
        future = self.task(due_date=date(2026, 9, 18))
        unknown = self.task()
        self.task(due_date=self.today, assigned_to=self.other)
        self.task(due_date=self.today, status='completed')
        self.task(due_date=self.today, is_deleted=True)
        deleted_project = Project.objects.create(code='DELETED', name='Deleted project', owner=self.user, is_deleted=True)
        self.task(due_date=self.today, project=deleted_project)
        data = self.response()['tasks']
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['counts'], {'open': 4, 'due_today': 1, 'overdue': 1, 'due': 2})
        self.assertEqual([row['id'] for row in data['rows']], [overdue.pk, due.pk, future.pk, unknown.pk])
        self.assertTrue(all(row['route'] == f'/projects?project={self.project.pk}' for row in data['rows']))

    def test_explicit_project_read_deny_wins_even_for_superuser(self):
        module = self.grant_tasks()
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.task(due_date=self.today)
        UserPermissionOverride.objects.create(user_profile=self.profile,
                                              permission=module.permissions.filter(action='read').first(), allowed=False)
        data = self.response()['tasks']
        self.assertEqual(data['status'], 'unavailable')
        self.assertIsNone(data['total_rows'])

    def test_task_cap_does_not_cap_due_or_open_counts(self):
        self.grant_tasks()
        ProjectTask.objects.bulk_create([ProjectTask(project=self.project, assigned_to=self.user, title=f'Task {i}', due_date=self.today) for i in range(103)])
        data = self.response()['tasks']
        self.assertEqual(data['counts']['due'], 103)
        self.assertEqual(data['total_rows'], 103)
        self.assertEqual(data['returned_rows'], 100)
        self.assertEqual(len(data['rows']), 100)
        self.assertTrue(data['truncated'])

    def test_leave_reads_exact_own_year_and_preserves_recorded_negative_balance(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.ledger(leave_balance=Decimal('-1.2500'))
        self.ledger(employee_code='OTHER-001', leave_balance=99)
        self.ledger(year=2025, leave_balance=30)
        data = self.response()['leave']
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['balance'], -1.25)
        self.assertEqual(data['basis'], 'recorded_annual_ledger')
        self.assertNotIn('employee_name', data)
        self.assertEqual(data['year'], 2026)

    def test_missing_or_ambiguous_employee_link_never_selects_someone_elses_balance(self):
        self.ledger(employee_code='OTHER-001', leave_balance=20)
        self.assertEqual(self.response()['leave']['status'], 'unavailable')
        self.ledger(leave_balance=0)
        self.assertEqual(self.response()['leave']['balance'], 0)
        other_profile, _ = UserProfile.objects.get_or_create(user=self.other, defaults={'organization': self.profile.organization})
        other_profile.employee_id = self.profile.employee_id
        other_profile.save()
        data = self.response()['leave']
        self.assertEqual(data['status'], 'unavailable')
        self.assertIsNone(data['balance'])

    def test_logged_hours_self_scope_month_to_date_and_approval_basis(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.log(hours_spent='2.50', approval_status='approved')
        self.log(hours_spent='1.25', approval_status='pending')
        self.log(hours_spent='0.25', approval_status='rejected')
        self.log(hours_spent='8.00', user=self.other)
        self.log(hours_spent='9.00', log_date=date(2026, 8, 31))
        self.log(hours_spent='7.00', log_date=date(2026, 9, 18))
        data = self.response({'year': 2026, 'month': 9, 'all': 'true', 'user_id': self.other.pk})['hours']
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['value'], 4)
        self.assertEqual(data['approved_hours'], 2.5)
        self.assertEqual(data['entry_count'], 3)
        self.assertEqual(data['through_date'], '2026-09-17')
        self.assertEqual(data['basis'], 'logged_work_hours')

    def test_empty_connected_sources_are_real_zero_while_missing_ledger_stays_unknown(self):
        self.grant_tasks()
        data = self.response()
        self.assertEqual(data['tasks']['counts']['due'], 0)
        self.assertEqual(data['hours']['value'], 0)
        self.assertEqual(data['hours']['entry_count'], 0)
        self.assertEqual(data['calendar']['total_rows'], 0)
        self.assertEqual(data['activity']['total_count'], 0)
        self.assertEqual(len(data['activity']['series']), 7)
        self.assertTrue(all(row['count'] == 0 for row in data['activity']['series']))
        self.assertIsNone(data['leave']['balance'])

    def test_future_period_or_invalid_hour_entry_is_not_measured_zero(self):
        self.assertEqual(self.response({'year': 2027, 'month': 1})['hours']['status'], 'unavailable')
        self.log(hours_spent='-1.00')
        data = self.response()['hours']
        self.assertEqual(data['status'], 'unavailable')
        self.assertIsNone(data['value'])

    def test_calendar_contains_only_own_approved_leave_and_active_month_holidays(self):
        own = self.leave_request(status='APPROVED', reason='PRIVATE REASON')
        self.leave_request(employee=self.other, status='APPROVED')
        self.leave_request(status='PENDING')
        self.leave_request(status='CANCELLED')
        self.leave_request(status='APPROVED', start_date=date(2026, 10, 1), end_date=date(2026, 10, 2))
        holiday = PublicHoliday.objects.create(date=self.today, name='Recorded public holiday', region='AE-AZ')
        PublicHoliday.objects.create(date=date(2026, 9, 18), name='Inactive holiday', is_active=False)
        PublicHoliday.objects.create(date=date(2026, 10, 1), name='Next month holiday')
        data = self.response()['calendar']
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['total_rows'], 2)
        self.assertEqual({row['id'] for row in data['rows']}, {f'leave:{own.pk}', f'holiday:{holiday.pk}'})
        self.assertNotIn('PRIVATE REASON', str(data))
        self.assertEqual({row['type'] for row in data['rows']}, {'leave', 'public_holiday'})

    def test_calendar_caps_are_per_source_with_full_counts(self):
        PublicHoliday.objects.bulk_create([PublicHoliday(date=date(2026, 9, 1) + timedelta(days=i // 2),
                                                         name=f'Published entry {i}', region='AE-AZ' if i % 2 else 'AE') for i in range(52)])
        data = self.response()['calendar']
        self.assertEqual(data['total_rows'], 52)
        self.assertEqual(data['returned_rows'], 50)
        self.assertTrue(data['truncated'])
        self.assertFalse(data['partial'])

    def test_activity_is_personal_even_for_staff_and_uses_exact_seven_local_calendar_days(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        start = datetime(2026, 9, 11, tzinfo=ZoneInfo('Asia/Dubai'))
        for when in (start, self.now):
            SystemActivity.objects.create(user=self.user, activity_type='document_uploaded', description='Own event', timestamp=when)
        for who, when in ((self.other, self.now), (self.user, start - timedelta(microseconds=1)), (self.user, self.now + timedelta(seconds=1))):
            SystemActivity.objects.create(user=who, activity_type='document_uploaded', description='Outside scope', timestamp=when)
        data = self.response()['activity']
        self.assertEqual(data['total_count'], 2)
        self.assertEqual(sum(row['count'] for row in data['series']), 2)
        self.assertEqual(data['series'][0], {'date': '2026-09-11', 'count': 1})
        self.assertEqual(data['series'][-1], {'date': '2026-09-17', 'count': 1})
        self.assertTrue(all(row['title'] == 'Document uploaded' for row in data['rows']))
        self.assertTrue(all(row['source_label'] == 'Documents' for row in data['rows']))
        self.assertTrue(all(row['route'] is None for row in data['rows']))

    def test_activity_cap_retains_exact_series_and_total(self):
        SystemActivity.objects.bulk_create([SystemActivity(user=self.user, activity_type='document_uploaded', description='Own event', timestamp=self.now) for _ in range(12)])
        data = self.response()['activity']
        self.assertEqual(data['total_count'], 12)
        self.assertEqual(sum(row['count'] for row in data['series']), 12)
        self.assertEqual(data['returned_rows'], 10)
        self.assertTrue(data['truncated'])

    def test_recent_excludes_get_post_tracking_and_profile_photo_telemetry_from_rows_and_totals(self):
        samples = [
            ('GET', '/api/v1/dashboard/work-hub/'),
            ('GET', '/api/v1/rbac/current-user/'),
            ('GET', '/api/v1/users/recorded-user/profile-photo/'),
            ('GET', '/api/v1/auth/session/'),
            ('GET', '/api/v1/notifications/'),
            ('POST', '/api/v1/rbac/ai-champion/track/activity/'),
            ('POST', '/api/v1/payroll/leave-requests/request/approve/'),
            ('POST', '/api/v1/projects/'),
        ]
        SystemActivity.objects.bulk_create([
            SystemActivity(user=self.user, activity_type='api_request', category='api',
                           description=f'{method} {path}', details={'method': method, 'path': path, 'status_code': 200},
                           success=True, timestamp=self.now)
            for method, path in samples
        ])
        # API categorization remains telemetry even if its type is mislabeled.
        SystemActivity.objects.create(user=self.user, activity_type='document_uploaded', category='api',
                                      description='POST /api/v1/documents/', success=True, timestamp=self.now)
        data = self.response()['activity']
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['basis'], 'recorded_user_actions')
        self.assertEqual(data['coverage'], 'work_actions_and_workspace_views')
        self.assertEqual(data['total_count'], 0)
        self.assertEqual(data['returned_rows'], 0)
        self.assertEqual(data['rows'], [])
        self.assertEqual(sum(row['count'] for row in data['series']), 0)
        self.assertFalse(data['truncated'])

    def test_recent_background_and_unknown_event_types_do_not_become_user_work(self):
        noise = ['user_login', 'user_logout', 'system_error', 'security_event', 'notification_sent',
                 'database_query', 'cache_hit', 'cache_miss', 'backup_created', 'webhook_triggered',
                 'view', 'api_call', 'unknown_business_action']
        SystemActivity.objects.bulk_create([
            SystemActivity(user=self.user, activity_type=kind, description='Background event', timestamp=self.now)
            for kind in noise
        ])
        self.assertEqual(self.response()['activity']['total_count'], 0)

    def test_recent_datasheet_uses_explicit_type_and_safe_equipment_metadata_not_paths_or_private_fields(self):
        record = SystemActivity.objects.create(user=self.user, activity_type='datasheet_generated', category='electrical_datasheet',
            description='POST /api/v1/private/?token=RAW_PRIVATE', timestamp=self.now, success=True,
            details={'datasheet_id': 'private-record', 'equipment_type': 'transformer', 'path': '/api/v1/private/'},
            metadata={'title': 'PRIVATE METADATA', 'email': 'private@example.test'}, ip_address='127.0.0.7',
            error_message='PRIVATE ERROR')
        data = self.response()['activity']
        self.assertEqual(data['total_count'], 1)
        row = data['rows'][0]
        self.assertEqual(row['id'], record.pk)
        self.assertEqual(row['title'], 'Transformer datasheet generated')
        self.assertEqual(row['source_label'], 'Electrical Engineering')
        self.assertEqual(row['basis'], 'recorded_work_action')
        self.assertIsNone(row['route'])
        self.assertEqual(set(row), {'id', 'type', 'category', 'title', 'source_label', 'basis', 'status_label', 'description', 'timestamp', 'success', 'route'})
        for secret in ['RAW_PRIVATE', '/api/', 'PRIVATE METADATA', 'private@example.test', 'PRIVATE ERROR', '127.0.0.7', 'private-record']:
            self.assertNotIn(secret, str(data))

    def test_recent_failed_action_titles_do_not_claim_successful_business_outcomes(self):
        for kind, details in [('document_uploaded', {}), ('project_created', {}),
                              ('datasheet_generated', {'equipment_type': 'transformer'})]:
            SystemActivity.objects.create(user=self.user, activity_type=kind, description='Misleading completed title',
                                          details=details, success=False, timestamp=self.now)
        rows = self.response()['activity']['rows']
        self.assertEqual({row['title'] for row in rows}, {'Document upload unsuccessful', 'Project creation unsuccessful', 'Transformer datasheet generation unsuccessful'})
        self.assertTrue(all(row['success'] is False for row in rows))
        self.assertTrue(all('recorded as unsuccessful' in row['description'] for row in rows))

    def test_recent_malformed_or_unknown_equipment_metadata_keeps_safe_generic_action(self):
        for details in [[], {'equipment_type': []}, {'equipment_type': '/api/v1/profile-photo/'}, {'equipment_type': None}]:
            SystemActivity.objects.create(user=self.user, activity_type='datasheet_generated', description='Untrusted raw description',
                                          details=details, timestamp=self.now)
        data = self.response()['activity']
        self.assertEqual(data['total_count'], 4)
        self.assertTrue(all(row['title'] == 'Electrical datasheet generated' for row in data['rows']))

    def test_recent_filtered_full_count_and_series_are_not_limited_by_newer_request_noise(self):
        start = datetime(2026, 9, 11, tzinfo=ZoneInfo('Asia/Dubai'))
        SystemActivity.objects.bulk_create([
            SystemActivity(user=self.user, activity_type='report_generated', description='Recorded report', timestamp=start + timedelta(hours=index))
            for index in range(12)
        ] + [SystemActivity(user=self.user, activity_type='api_request', category='api', description='GET /api/v1/dashboard/work-hub/', timestamp=self.now)
             for _ in range(15)])
        data = self.response()['activity']
        self.assertEqual(data['total_count'], 12)
        self.assertEqual(sum(row['count'] for row in data['series']), 12)
        self.assertEqual(data['series'][0]['count'], 12)
        self.assertEqual(data['series'][-1]['count'], 0)
        self.assertEqual(data['returned_rows'], 10)
        self.assertTrue(data['truncated'])
        self.assertTrue(all(row['title'] == 'Report generated' for row in data['rows']))

    def test_recent_recovers_recorded_workspace_views_without_claiming_completed_work(self):
        self.grant_tasks()
        event = self.workspace_view('/projects', application='projects', module='platform', feature='index',
            metadata={'source': 'frontend-route', 'path': '/projects', 'referrer': '/private/employee-name', 'token': 'PRIVATE'})
        data = self.response()['activity']
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['coverage'], 'work_actions_and_workspace_views')
        self.assertEqual(data['total_count'], 1)
        self.assertEqual(data['work_action_count'], 0)
        self.assertEqual(data['view_count'], 1)
        row = data['rows'][0]
        self.assertEqual(row['id'], f'view:{event.pk}')
        self.assertEqual(row['title'], 'Viewed Project Control')
        self.assertEqual(row['source_label'], 'Project Control')
        self.assertEqual(row['status_label'], 'Viewed')
        self.assertEqual(row['basis'], 'workspace_view')
        self.assertEqual(row['timestamp'], self.now.isoformat())
        self.assertEqual(row['route'], '/projects')
        self.assertEqual(row['description'], 'You viewed Project Control.')
        self.assertNotIn('PRIVATE', str(data))
        self.assertNotIn('employee-name', str(data))

    def test_recent_workspace_path_is_authoritative_and_background_or_unverified_views_stay_excluded(self):
        for path in ['/', '/dashboard', '/notifications', '/login', '/auth/session', '/api/v1/projects/',
                     '/profile/photo', '//other.example/projects', '/projects?user=private', '/projects/../admin',
                     '/projects\\private', '/sales/unknown', '/admin/private-area', '/unknown-workspace']:
            self.workspace_view(path, application='projects', feature='create')
        for metadata in [{}, {'path': '/projects'}, {'source': 'api', 'path': '/projects'},
                         {'source': 'frontend-route', 'path': None}, {'source': 'frontend-route', 'path': []}]:
            self.workspace_view(metadata=metadata)
        self.workspace_view('/projects', action_type='click')
        self.workspace_view('/projects', success=False)
        data = self.response()['activity']
        self.assertEqual(data['total_count'], 0)
        self.assertEqual(data['view_count'], 0)
        self.assertEqual(data['rows'], [])

    def test_recent_catalogue_uses_existing_executive_and_pid_pfd_destinations_with_exact_grants(self):
        from apps.dashboard.work_hub_views import workspace_destination
        self.assertEqual(workspace_destination('/executive'), ('/executive', 'Executive overview', 'executive_dashboard'))
        for path in ['/pid/upload', '/pid/history', '/pid/report/record-id']:
            self.assertEqual(workspace_destination(path), ('/engineering/process/pid-verification-v1', 'P&ID analysis', 'pid_analysis'))
        for path in ['/pfd/upload', '/pfd/history', '/pfd/report/record-id']:
            self.assertEqual(workspace_destination(path), ('/pfd/upload', 'PFD to P&ID', 'pfd_to_pid'))
        self.assertIsNone(workspace_destination('/pid'))
        self.assertIsNone(workspace_destination('/pfd'))
        self.workspace_view('/executive')
        self.assertIsNone(self.response()['activity']['rows'][0]['route'])
        module, _ = Module.objects.get_or_create(code='executive_dashboard', defaults={'name': 'Executive overview'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        RolePermission.objects.get_or_create(role=self.role, permission=module.permissions.get(action='read'))
        self.assertEqual(self.response()['activity']['rows'][0]['route'], '/executive')

    def test_recent_workspace_links_require_current_exact_read_even_for_superuser(self):
        self.workspace_view('/projects')
        self.assertIsNone(self.response()['activity']['rows'][0]['route'])
        module = self.grant_tasks()
        self.assertEqual(self.response()['activity']['rows'][0]['route'], '/projects')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        UserPermissionOverride.objects.create(user_profile=self.profile,
            permission=module.permissions.filter(action='read').first(), allowed=False)
        data = self.response()['activity']
        self.assertIsNone(data['rows'][0]['route'])
        self.assertEqual(data['rows'][0]['title'], 'Viewed Project Control')
        self.assertEqual(data['view_count'], 1)

    def test_recent_business_child_views_link_to_canonical_list_and_never_inherit_parent_grant(self):
        module, _ = Module.objects.get_or_create(code='sales', defaults={'name': 'Sales'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        RolePermission.objects.get_or_create(role=self.role, permission=module.permissions.get(action='read'))
        event = self.workspace_view('/sales/opportunities/private-record', application='sales', feature='opportunities')
        data = self.response()['activity']['rows'][0]
        self.assertEqual(data['title'], 'Viewed Sales opportunities')
        self.assertIsNone(data['route'])
        self.assertNotIn('private-record', str(data))
        self.assertEqual(data['id'], f'view:{event.pk}')
        source, _ = Module.objects.get_or_create(code='sales_opportunities', defaults={'name': 'Opportunities'})
        ensure_module_actions(Module, Permission, module_ids=[source.pk])
        RoleModule.objects.get_or_create(role=self.role, module=source)
        RolePermission.objects.get_or_create(role=self.role, permission=source.permissions.get(action='read'))
        self.assertEqual(self.response()['activity']['rows'][0]['route'], '/sales/opportunities')

    def test_recent_views_collapse_only_consecutive_same_destination_within_window_keep_real_timestamp(self):
        latest = self.workspace_view('/profile', timestamp=self.now)
        self.workspace_view('/profile', timestamp=self.now - timedelta(seconds=10))
        self.workspace_view('/approvals', timestamp=self.now - timedelta(seconds=15))
        self.workspace_view('/profile', timestamp=self.now - timedelta(seconds=20))
        self.workspace_view('/profile', timestamp=self.now - timedelta(seconds=60))
        data = self.response()['activity']
        self.assertEqual(data['view_count'], 4)
        self.assertEqual(data['collapsed_view_count'], 1)
        self.assertEqual(data['total_count'], 4)
        self.assertEqual(data['deduplication_window_seconds'], 30)
        self.assertEqual(sum(row['count'] for row in data['series']), 4)
        self.assertEqual(data['rows'][0]['id'], f'view:{latest.pk}')
        self.assertEqual(data['rows'][0]['timestamp'], self.now.isoformat())
        self.assertEqual(data['rows'][1]['source_label'], 'Approvals')

    def test_recent_views_preserve_actor_time_scope_and_mix_chronologically_with_action_counts(self):
        start = datetime(2026, 9, 11, tzinfo=ZoneInfo('Asia/Dubai'))
        for who, when in [(self.other, self.now), (self.user, start - timedelta(microseconds=1)),
                          (self.user, self.now + timedelta(microseconds=1))]:
            self.workspace_view('/profile', user=who, timestamp=when)
        for index in range(12):
            self.workspace_view('/profile', timestamp=start + timedelta(minutes=index))
        action = SystemActivity.objects.create(user=self.user, activity_type='document_uploaded', description='Recorded action', timestamp=self.now)
        data = self.response()['activity']
        self.assertEqual(data['work_action_count'], 1)
        self.assertEqual(data['view_count'], 12)
        self.assertEqual(data['total_count'], 13)
        self.assertEqual(data['series'][0]['count'], 12)
        self.assertEqual(data['series'][-1]['count'], 1)
        self.assertEqual(sum(row['count'] for row in data['series']), 13)
        self.assertEqual(data['returned_rows'], 10)
        self.assertTrue(data['truncated'])
        self.assertEqual(data['rows'][0]['id'], action.pk)
        self.assertEqual(data['rows'][0]['status_label'], 'Recorded')
        self.assertEqual(data['rows'][1]['status_label'], 'Viewed')

    def test_recent_view_source_failure_withholds_combined_count_without_claiming_empty_history(self):
        self.workspace_view()
        original = work_hub._model
        def fail_views(app, name):
            if name == 'ActivityEvent':
                raise DatabaseError('simulated view source failure')
            return original(app, name)
        with patch.object(work_hub, '_model', side_effect=fail_views):
            data = self.response()['activity']
        self.assertEqual(data['status'], 'error')
        self.assertIsNone(data['total_count'])
        self.assertIsNone(data['view_count'])
        self.assertEqual(data['rows'], [])
        self.assertEqual(data['series'], [])

    def test_one_source_error_does_not_turn_other_sources_into_zero_or_failure(self):
        original = work_hub._model
        def fail_activity(app, name):
            if name == 'SystemActivity':
                raise DatabaseError('simulated source failure')
            return original(app, name)
        with patch.object(work_hub, '_model', side_effect=fail_activity):
            data = self.response()
        self.assertEqual(data['activity']['status'], 'error')
        self.assertIsNone(data['activity']['total_count'])
        self.assertEqual(data['activity']['series'], [])
        self.assertEqual(data['hours']['status'], 'ready')
        self.assertEqual(data['hours']['value'], 0)

    def test_calendar_sources_fail_independently_with_unknown_combined_total(self):
        self.leave_request(status='APPROVED')
        original = work_hub._model
        def missing_holidays(app, name):
            if name == 'PublicHoliday':
                raise work_hub.SourceUnavailable('Holiday source is not enabled.')
            return original(app, name)
        with patch.object(work_hub, '_model', side_effect=missing_holidays):
            data = self.response()['calendar']
        self.assertEqual(data['status'], 'ready')
        self.assertTrue(data['partial'])
        self.assertIsNone(data['total_rows'])
        self.assertEqual(data['returned_rows'], 1)
        self.assertEqual({source['id']: source['status'] for source in data['sources']}, {'own_leave': 'ready', 'public_holidays': 'unavailable'})

    def test_get_never_creates_ledgers_or_writes_business_models(self):
        self.grant_tasks()
        self.task()
        self.log(hours_spent='1.00')
        self.leave_request(status='APPROVED')
        self.workspace_view('/profile')
        with patch('apps.payroll.services.leave_workforce_sync.ensure_canonical_leave_records', side_effect=AssertionError('GET must not sync ledgers')):
            with CaptureQueriesContext(connection) as captured:
                data = self.response()
        writes = [query['sql'] for query in captured.captured_queries if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP'))]
        self.assertEqual(writes, [])
        self.assertEqual(EmployeeLeaveRecord.objects.count(), 0)
        self.assertEqual(data['leave']['status'], 'unavailable')
