from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied

from apps.onboarding.views import (
    IT_ONBOARDING_CHECKLIST_TEMPLATE, OnboardingRecordViewSet,
    OffboardingRecordViewSet, complete_offboarding_if_ready,
    complete_onboarding_if_ready, ensure_onboarding_record,
    _resolve_exit_reporting_manager,
)
from apps.onboarding.models import (
    ONBOARDING_ACTIVE_STATUSES, OFFBOARDING_ACTIVE_STATUSES, OffboardingRecord,
)
from apps.onboarding.serializers import OffboardingRecordSerializer
from apps.onboarding.rbac import can_manage_onboarding_stage, can_start_onboarding_stage


class OnboardingRecordUserFilterTests(SimpleTestCase):
    def test_queryset_is_scoped_to_selected_user(self):
        queryset = MagicMock()
        scoped_queryset = MagicMock()
        queryset.all.return_value = queryset
        queryset.filter.return_value = scoped_queryset
        scoped_queryset.annotate.return_value = scoped_queryset
        scoped_queryset.select_related.return_value = scoped_queryset

        view = OnboardingRecordViewSet()
        view.action = 'list'
        view.request = SimpleNamespace(query_params={'user_id': '42'})
        view.queryset = queryset

        result = view.get_queryset()

        queryset.filter.assert_called_once_with(user_id=42)
        self.assertIs(result, scoped_queryset)

    def test_invalid_user_id_is_rejected(self):
        view = OnboardingRecordViewSet()
        view.action = 'list'
        view.request = SimpleNamespace(query_params={'user_id': 'invalid'})
        view.queryset = MagicMock()
        view.queryset.all.return_value = view.queryset

        with self.assertRaises(ValidationError):
            view.get_queryset()

    @patch('apps.onboarding.views.OffboardingRecord.objects.filter')
    @patch('apps.onboarding.views.OnboardingRecord.objects.filter')
    def test_command_center_pending_combines_lifecycle_requests(
        self, mock_onboarding_filter, mock_offboarding_filter
    ):
        created_at = '2026-08-14T10:00:00Z'
        mock_onboarding_filter.return_value.values.return_value = [{
            'id': 1, 'user_id': 10, 'employee_name': 'New Employee',
            'employee_email': 'new@example.com', 'employee_id': 'EMP-10',
            'department': 'Engineering', 'status': 'initiated',
            'joining_date': date(2026, 9, 1), 'initiated_date': created_at,
            'created_at': created_at,
        }]
        mock_offboarding_filter.return_value.values.return_value = [{
            'id': 2, 'user_id': 20, 'employee_name': 'Leaving Employee',
            'employee_email': 'leaving@example.com', 'employee_id': 'EMP-20',
            'department': 'Finance', 'status': 'equipment_return',
            'last_working_day': date(2026, 9, 30), 'initiated_date': created_at,
            'created_at': created_at,
        }]

        response = OnboardingRecordViewSet().command_center_pending(request=None)

        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0]['request_type'], 'Onboarding')
        self.assertEqual(response.data[0]['display_status'], 'Initiated')
        self.assertEqual(response.data[1]['request_type'], 'Offboarding')
        self.assertEqual(response.data[1]['display_status'], 'In Progress')
        mock_onboarding_filter.assert_called_once_with(status__in=ONBOARDING_ACTIVE_STATUSES)
        mock_offboarding_filter.assert_called_once_with(status__in=OFFBOARDING_ACTIVE_STATUSES)

    @patch('apps.onboarding.views.ensure_onboarding_record')
    @patch('apps.onboarding.views.OnboardingRecord.objects.filter')
    @patch('apps.onboarding.views.EmployeeMaster.objects.filter')
    def test_sync_missing_initiates_only_absent_workflows(
        self, mock_employee_filter, mock_record_filter, mock_ensure
    ):
        active_employees = MagicMock()
        active_employees.count.return_value = 2
        employees = MagicMock()
        employees.select_related.return_value = employees
        employees.iterator.return_value = [SimpleNamespace(user_id=10), SimpleNamespace(user_id=20)]
        active_employees.exclude.return_value = employees
        mock_employee_filter.return_value = active_employees
        mock_record_filter.return_value.values.return_value = [{'user_id': 99}]
        mock_ensure.side_effect = [
            (SimpleNamespace(id=101), True),
            (SimpleNamespace(id=202), False),
        ]
        request = SimpleNamespace(user=SimpleNamespace(id=7))

        response = OnboardingRecordViewSet().sync_missing(request)

        self.assertEqual(response.data['checked_count'], 2)
        self.assertEqual(response.data['created_count'], 1)
        self.assertEqual(response.data['created_record_ids'], [101])

    @patch('apps.onboarding.views.OnboardingRecord.objects.get_or_create')
    @patch('apps.onboarding.views.OnboardingRecord.objects.filter')
    def test_missing_employee_starts_in_initiated_status(self, mock_filter, mock_get_or_create):
        lookup = MagicMock()
        lookup.order_by.return_value.first.return_value = None
        mock_filter.return_value = lookup
        record = SimpleNamespace(id=303)
        mock_get_or_create.return_value = (record, True)
        employee = SimpleNamespace(
            user_id=42,
            user=SimpleNamespace(get_full_name=lambda: 'Test Employee'),
            email='TEST@example.com',
            employee_number='EMP-42',
            get_full_name=lambda: 'Test Employee',
            join_date=date(2026, 8, 1),
            job_title_uae='Engineer',
            job_title_finland='',
            designation='',
            division='Engineering',
            department='',
            manager=None,
            branch='RAD',
        )

        result, created = ensure_onboarding_record(employee, created_by=None)

        self.assertIs(result, record)
        self.assertTrue(created)
        defaults = mock_get_or_create.call_args.kwargs['defaults']
        self.assertEqual(defaults['status'], 'initiated')
        self.assertEqual(defaults['progress_percentage'], 0)
        self.assertEqual(mock_get_or_create.call_args.kwargs['employee_email'], 'test@example.com')

    @patch('apps.onboarding.views.Checklist')
    def test_start_it_checklist_creates_template_and_advances_workflow(self, mock_checklist):
        record = MagicMock()
        record.status = 'initiated'
        record.progress_percentage = 0
        record.target_completion_date = date(2026, 8, 31)
        record.joining_date = date(2026, 8, 20)
        record.checklist_items.filter.return_value.values_list.return_value = []
        view = OnboardingRecordViewSet()
        view.get_object = MagicMock(return_value=record)
        view.get_serializer = MagicMock(return_value=SimpleNamespace(data={'id': 88}))

        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_superuser=True))
        response = view.start_it_checklist(request, pk=88)

        self.assertEqual(len(mock_checklist.objects.bulk_create.call_args.args[0]), len(IT_ONBOARDING_CHECKLIST_TEMPLATE))
        self.assertEqual(record.status, 'equipment')
        self.assertEqual(record.progress_percentage, 40)
        self.assertEqual(response.data['created_checklist_count'], len(IT_ONBOARDING_CHECKLIST_TEMPLATE))

    @patch('apps.onboarding.views.Checklist')
    def test_start_it_checklist_does_not_duplicate_existing_tasks(self, mock_checklist):
        existing_task = IT_ONBOARDING_CHECKLIST_TEMPLATE[0][0]
        record = MagicMock()
        record.status = 'equipment'
        record.progress_percentage = 40
        record.target_completion_date = date(2026, 8, 31)
        record.joining_date = date(2026, 8, 20)
        record.checklist_items.filter.return_value.values_list.return_value = [existing_task]
        view = OnboardingRecordViewSet()
        view.get_object = MagicMock(return_value=record)
        view.get_serializer = MagicMock(return_value=SimpleNamespace(data={'id': 88}))

        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, is_superuser=True))
        response = view.start_it_checklist(request, pk=88)

        self.assertEqual(len(mock_checklist.objects.bulk_create.call_args.args[0]), len(IT_ONBOARDING_CHECKLIST_TEMPLATE) - 1)
        self.assertEqual(response.data['created_checklist_count'], len(IT_ONBOARDING_CHECKLIST_TEMPLATE) - 1)

    def test_it_checklist_rejects_user_without_required_rbac_role(self):
        record = MagicMock()
        view = OnboardingRecordViewSet()
        view.get_object = MagicMock(return_value=record)
        request = SimpleNamespace(user=SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
            id=51,
        ))

        with patch('apps.onboarding.rbac.get_active_role_codes', return_value={'default'}):
            with self.assertRaises(PermissionDenied):
                view.start_it_checklist(request, pk=88)


class OffboardingDuplicateValidationTests(SimpleTestCase):
    def setUp(self):
        self.today = date.today()
        self.existing = OffboardingRecord(
            id=123,
            employee_name='Test Employee',
            employee_email='employee@example.com',
            employee_id='EMP-001',
            position='Engineer',
            department='Engineering',
            exit_reason='resignation',
            last_working_day=self.today + timedelta(days=30),
            target_completion_date=self.today + timedelta(days=60),
            status='initiated',
        )

    def payload(self):
        return {
            'employee_name': 'Test Employee',
            'employee_email': 'EMPLOYEE@example.com',
            'employee_id': 'EMP-001',
            'position': 'Engineer',
            'department': 'Engineering',
            'branch': 'RAD',
            'exit_reason': 'resignation',
            'last_working_day': self.today + timedelta(days=90),
            'target_completion_date': self.today + timedelta(days=120),
            'status': 'initiated',
        }

    @patch('apps.onboarding.serializers.OffboardingRecord.objects.filter')
    def test_different_exit_date_does_not_allow_duplicate_active_request(self, mock_filter):
        mock_filter.return_value.exists.return_value = True

        serializer = OffboardingRecordSerializer(data=self.payload())

        self.assertFalse(serializer.is_valid())
        self.assertIn('already has an active offboarding', str(serializer.errors['detail'][0]))

    @patch('apps.onboarding.serializers.OffboardingRecord.objects.filter')
    def test_every_in_progress_workflow_status_blocks_a_duplicate(self, mock_filter):
        mock_filter.return_value.exists.return_value = True

        for status in OFFBOARDING_ACTIVE_STATUSES:
            with self.subTest(status=status):
                payload = self.payload()
                payload['status'] = status

                serializer = OffboardingRecordSerializer(data=payload)

                self.assertFalse(serializer.is_valid())
                self.assertIn('detail', serializer.errors)

    @patch('apps.onboarding.serializers.OffboardingRecord.objects.filter')
    def test_completed_or_cancelled_request_allows_a_new_offboarding(self, mock_filter):
        for status in ('completed', 'cancelled'):
            with self.subTest(status=status):
                payload = self.payload()
                payload['status'] = status

                serializer = OffboardingRecordSerializer(data=payload)

                self.assertTrue(serializer.is_valid(), serializer.errors)

        mock_filter.assert_not_called()

    @patch('apps.onboarding.serializers.OffboardingRecord.objects.filter')
    def test_updating_the_same_active_record_is_allowed(self, mock_filter):
        other_active_records = MagicMock()
        other_active_records.exists.return_value = False
        mock_filter.return_value.exclude.return_value = other_active_records

        serializer = OffboardingRecordSerializer(
            self.existing,
            data={'notes': 'Updated notes'},
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        mock_filter.return_value.exclude.assert_called_once_with(pk=self.existing.pk)


class OffboardingActiveEmployeeLookupTests(SimpleTestCase):
    @patch('apps.onboarding.views.OffboardingRecord.objects.filter')
    def test_lookup_returns_only_active_employee_identities(self, mock_filter):
        active_record = {
            'id': 7,
            'user_id': 42,
            'employee_email': 'employee@example.com',
            'status': 'initiated',
            'last_working_day': date.today(),
        }
        mock_filter.return_value.values.return_value = [active_record]

        response = OffboardingRecordViewSet().active_employees(request=None)

        self.assertEqual(response.data, [active_record])
        mock_filter.assert_called_once_with(status__in=OFFBOARDING_ACTIVE_STATUSES)


class OnboardingChecklistRBACTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(is_authenticated=True, is_superuser=False, id=501)

    @patch('apps.onboarding.rbac.get_active_role_codes', return_value={'hr_admin'})
    def test_hr_can_start_it_but_cannot_complete_it_tasks(self, _mock_roles):
        self.assertTrue(can_start_onboarding_stage(self.user, 'it_provisioning'))
        self.assertFalse(can_manage_onboarding_stage(self.user, 'it_provisioning'))
        self.assertTrue(can_manage_onboarding_stage(self.user, 'pre_hire'))
        self.assertTrue(can_manage_onboarding_stage(self.user, 'final_validation'))

    @patch('apps.onboarding.rbac.get_active_role_codes', return_value={'ict_admin'})
    def test_ict_role_only_manages_it_stage(self, _mock_roles):
        self.assertTrue(can_manage_onboarding_stage(self.user, 'it_provisioning'))
        self.assertFalse(can_manage_onboarding_stage(self.user, 'pre_hire'))
        self.assertFalse(can_manage_onboarding_stage(self.user, 'final_validation'))

    @patch('apps.onboarding.rbac.get_active_role_codes', return_value={'manager'})
    def test_manager_role_only_manages_first_day_stage(self, _mock_roles):
        self.assertTrue(can_manage_onboarding_stage(self.user, 'first_day'))
        self.assertFalse(can_manage_onboarding_stage(self.user, 'it_provisioning'))


class OnboardingChecklistCompletionTests(SimpleTestCase):
    def build_record(self, has_incomplete=False):
        record = MagicMock()
        record.status = 'training'
        stage_rows = record.checklist_items.filter.return_value
        stage_rows.values_list.return_value.distinct.return_value = [
            'pre_hire', 'it_provisioning', 'first_day', 'final_validation',
        ]
        stage_rows.filter.return_value.exists.return_value = has_incomplete
        return record

    def test_all_required_checklists_auto_complete_the_workflow(self):
        record = self.build_record()

        completed = complete_onboarding_if_ready(record)

        self.assertTrue(completed)
        self.assertEqual(record.status, 'completed')
        self.assertEqual(record.progress_percentage, 100)
        self.assertIsNotNone(record.actual_completion_date)
        record.save.assert_called_once_with(update_fields=[
            'status', 'progress_percentage', 'actual_completion_date', 'updated_at',
        ])

    def test_incomplete_checklist_keeps_the_workflow_open(self):
        record = self.build_record(has_incomplete=True)

        completed = complete_onboarding_if_ready(record)

        self.assertFalse(completed)
        self.assertEqual(record.status, 'training')
        record.save.assert_not_called()


class OffboardingChecklistCompletionTests(SimpleTestCase):
    def test_all_required_exit_stages_auto_complete_offboarding(self):
        record = MagicMock()
        record.status = 'final_settlement'
        stage_rows = record.checklist_items.filter.return_value
        stage_rows.values_list.return_value.distinct.return_value = [
            'exit_initiation', 'access_revocation', 'asset_return',
            'exit_clearance', 'final_settlement',
        ]
        stage_rows.filter.return_value.exists.return_value = False

        completed = complete_offboarding_if_ready(record)

        self.assertTrue(completed)
        self.assertEqual(record.status, 'completed')
        self.assertEqual(record.progress_percentage, 100)
        self.assertIsNotNone(record.actual_completion_date)
        record.save.assert_called_once()

    def test_missing_exit_stage_keeps_offboarding_open(self):
        record = MagicMock()
        record.status = 'exit_interview'
        stage_rows = record.checklist_items.filter.return_value
        stage_rows.values_list.return_value.distinct.return_value = [
            'exit_initiation', 'access_revocation', 'asset_return', 'exit_clearance',
        ]

        self.assertFalse(complete_offboarding_if_ready(record))
        record.save.assert_not_called()


class OffboardingManagementActionTests(SimpleTestCase):
    @patch('apps.onboarding.views.get_profile_project_manager')
    def test_exit_reporting_manager_prefers_active_project_pom(self, mock_project_manager):
        pom = MagicMock()
        pom.get_full_name.return_value = 'Project Manager Name'
        mock_project_manager.return_value = (pom, {'name': 'Active Project'})
        employee = MagicMock()
        employee.manager.get_full_name.return_value = 'Line Manager Name'

        self.assertEqual(
            _resolve_exit_reporting_manager(MagicMock(), employee),
            'Project Manager Name',
        )

    @patch('apps.onboarding.views.get_profile_project_manager', return_value=(None, None))
    def test_exit_reporting_manager_falls_back_to_line_manager(self, _mock_project_manager):
        employee = MagicMock()
        employee.manager.get_full_name.return_value = 'Line Manager Name'

        self.assertEqual(
            _resolve_exit_reporting_manager(MagicMock(), employee),
            'Line Manager Name',
        )

    @patch('apps.onboarding.views.NotificationService.create_notification', return_value=None)
    @patch('apps.onboarding.views.get_active_project_assignments')
    @patch('apps.onboarding.views.can_manage_offboarding', return_value=True)
    def test_rejects_active_request_when_employee_has_active_project(
        self, _mock_can_manage, mock_projects, _mock_notification,
    ):
        project = {'code': 'P-100', 'name': 'Active Project', 'id': 100, 'managers': []}
        mock_projects.return_value = [project]
        record = MagicMock()
        record.status = 'initiated'
        record.user_id = 22
        record.user = MagicMock()
        record.id = 7

        view = OffboardingRecordViewSet()
        view.get_object = MagicMock(return_value=record)
        view.get_serializer = MagicMock(return_value=SimpleNamespace(data={'status': 'rejected'}))
        request = SimpleNamespace(user=MagicMock(), data={'reason': 'Project handover is incomplete.'})

        response = view.reject(request, pk=record.id)

        self.assertEqual(response.data['status'], 'rejected')
        self.assertEqual(record.status, 'rejected')
        self.assertIn('P-100 - Active Project', record.rejection_reason)
        self.assertIsNotNone(record.rejected_at)
        record.save.assert_called_once()

    @patch('apps.onboarding.views.get_active_project_assignments', return_value=[])
    @patch('apps.onboarding.views.can_manage_offboarding', return_value=True)
    def test_reject_requires_an_active_project_assignment(self, _mock_can_manage, _mock_projects):
        record = MagicMock(status='initiated', user=MagicMock())
        view = OffboardingRecordViewSet()
        view.get_object = MagicMock(return_value=record)

        with self.assertRaisesMessage(
            ValidationError,
            'Rejection is only available while the employee is assigned to an active project.',
        ):
            view.reject(SimpleNamespace(user=MagicMock(), data={}), pk=1)

    @patch('apps.onboarding.views.can_manage_offboarding', return_value=False)
    def test_employee_cannot_delete_offboarding(self, _mock_can_manage):
        view = OffboardingRecordViewSet()
        with self.assertRaisesMessage(
            PermissionDenied,
            'Only HR or an administrator may delete an offboarding process.',
        ):
            view.destroy(SimpleNamespace(user=MagicMock()), pk=1)
