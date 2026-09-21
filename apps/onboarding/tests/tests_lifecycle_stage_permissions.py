"""Lifecycle duty roles authorize checklist work independently of job titles."""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from apps.hr_core.models import EmployeeMaster
from apps.onboarding.models import Checklist, OffboardingRecord, OnboardingRecord
from apps.onboarding.rbac import (
    OFFBOARDING_STAGE_RBAC, ONBOARDING_STAGE_RBAC,
    can_manage_onboarding_stage, offboarding_stage_permissions,
    onboarding_stage_permissions,
)
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/onboarding/', include('apps.onboarding.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class LifecycleStagePermissionTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Lifecycle tests', code='lifecycle-tests')
        self.module, _ = Module.objects.get_or_create(code='hr_onboarding', defaults={'name': 'Employee Lifecycle'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        self.user = self.person('hr-operator', 'hr_admin')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def person(self, username, role_code, actions=('read', 'create', 'update', 'approve')):
        user = get_user_model().objects.create_user(username, email=f'{username}@example.test')
        profile = UserProfile.objects.create(user=user, organization=self.organization, job_title='Software Engineer')
        role, _ = Role.objects.get_or_create(code=role_code, defaults={
            'name': 'HR & Payroll Administrator' if role_code == 'hr_admin' else role_code,
            'level': 4,
        })
        UserRole.objects.create(user_profile=profile, role=role)
        RoleModule.objects.get_or_create(role=role, module=self.module)
        for permission in self.module.permissions.filter(action__in=actions):
            RolePermission.objects.get_or_create(role=role, permission=permission)
        return user

    def record(self, offboarding=False, **overrides):
        values = {
            'employee_name': 'Lifecycle Employee', 'employee_email': 'lifecycle@example.test',
            'position': 'Engineer', 'department': 'Engineering',
            'target_completion_date': date.today() + timedelta(days=30),
        }
        if offboarding:
            values.update(last_working_day=date.today() + timedelta(days=20), exit_reason='resignation')
        else:
            values['joining_date'] = date.today() + timedelta(days=10)
        values.update(overrides)
        return (OffboardingRecord if offboarding else OnboardingRecord).objects.create(**values)

    def ready_stage(self, record, stage):
        offboarding = isinstance(record, OffboardingRecord)
        policies = OFFBOARDING_STAGE_RBAC if offboarding else ONBOARDING_STAGE_RBAC
        field = 'offboarding_record' if offboarding else 'onboarding_record'
        for prior in list(policies)[:list(policies).index(stage)]:
            Checklist.objects.create(**{field: record}, stage=prior, task_name=f'Completed {prior}', completed=True)

    def start(self, record, stage):
        kind = 'offboarding' if isinstance(record, OffboardingRecord) else 'onboarding'
        return self.client.post(
            f'/api/v1/onboarding/{kind}/{record.pk}/start-checklist-stage/', {'stage': stage}, format='json',
        )

    def complete(self, item):
        return self.client.patch(f'/api/v1/onboarding/checklist/{item.pk}/', {'completed': True}, format='json')

    def grant_only_hr_actions(self, user, module_code, *, overrides=False):
        role = user.rbac_profile.roles.get()
        RoleModule.objects.filter(role=role).delete()
        RolePermission.objects.filter(role=role).delete()
        module, _ = Module.objects.get_or_create(code=module_code, defaults={'name': module_code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.create(role=role, module=module)
        for permission in module.permissions.filter(action__in=('read', 'create', 'update')):
            if overrides:
                UserPermissionOverride.objects.create(user_profile=user.rbac_profile, permission=permission, allowed=True)
            else:
                RolePermission.objects.create(role=role, permission=permission)
        return module

    def employee_payload(self, suffix='test'):
        return {
            'first_name': 'Lifecycle', 'surname': suffix, 'email': f'lifecycle.{suffix}@rejlers.ae',
            'mobile_phone': '+971501234567', 'joining_date': '2026-10-01', 'branch': 'RAD',
            'division': 'Engineering', 'job_title_uae': 'Process Engineer',
            'job_title_finland': 'Project Engineer',
        }

    def test_hr_create_and_edit_complete_entire_onboarding_without_approval_grant(self):
        cases = (
            ('hr_admin', 'hr_onboarding', False),
            ('custom_hr', 'hr_management', False),
            ('admin', 'hr_management', False),
            ('employee_with_grants', 'hr_onboarding', True),
        )
        for role, module_code, overrides in cases:
            with self.subTest(role=role, module=module_code, overrides=overrides):
                actor = self.user if role == 'hr_admin' else self.person(f'operator-{role}', role)
                self.grant_only_hr_actions(actor, module_code, overrides=overrides)
                self.assertFalse(module_action_allowed(actor, module_code, 'approve'))
                self.client.force_authenticate(actor)
                response = self.client.post('/api/v1/onboarding/onboarding/create_employee/',
                                            self.employee_payload(role), format='json')
                self.assertEqual(response.status_code, 201, response.data)
                record = OnboardingRecord.objects.get(pk=response.data['onboarding_id'])
                employee = record.canonical_employee
                self.assertEqual(employee.job_title_uae, 'Process Engineer')
                self.assertEqual(employee.job_title_finland, 'Project Engineer')
                self.assertEqual(employee.probation_end_date, date(2027, 4, 1))
                self.assertEqual(employee.employment_status, 'probation')
                self.assertEqual(self.client.get(f'/api/v1/onboarding/onboarding/{record.pk}/').status_code, 200)
                for stage in ONBOARDING_STAGE_RBAC:
                    response = self.start(record, stage)
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertTrue(response.data['checklist_stage_permissions'][stage]['can_manage'])
                    items = list(record.checklist_items.filter(stage=stage))
                    self.assertTrue(items)
                    for item in items:
                        response = self.complete(item)
                        self.assertEqual(response.status_code, 200, response.data)
                record.refresh_from_db()
                self.assertEqual(record.status, 'completed')
                self.assertEqual(record.progress_percentage, 100)
                self.assertEqual(record.checklist_items.filter(completed_by=actor).count(), record.checklist_items.count())
                response = self.client.patch(f'/api/v1/onboarding/checklist/{items[-1].pk}/',
                                             {'completed': False}, format='json')
                self.assertEqual(response.status_code, 403)

    def test_final_completion_endpoint_uses_edit_not_approve(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        record = self.record()
        for stage in ONBOARDING_STAGE_RBAC:
            Checklist.objects.create(onboarding_record=record, stage=stage, task_name=stage, completed=True)
        response = self.client.post(f'/api/v1/onboarding/onboarding/{record.pk}/mark_completed/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        record.refresh_from_db()
        self.assertEqual(record.status, 'completed')

    def test_explicit_deny_cannot_be_bypassed_with_other_hr_module_or_superuser(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        record = self.record()
        permission = self.module.permissions.get(action='update')
        UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile, permission=permission, allowed=False)
        for superuser in (False, True):
            with self.subTest(superuser=superuser):
                self.user.is_superuser = superuser
                self.user.save(update_fields=['is_superuser'])
                self.assertEqual(self.start(record, 'pre_hire').status_code, 403)
        self.assertFalse(record.checklist_items.exists())

    def test_hr_management_access_does_not_grant_offboarding_actions(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        record = self.record(offboarding=True)
        self.assertEqual(self.start(record, 'exit_initiation').status_code, 403)
        item = Checklist.objects.create(offboarding_record=record, stage='exit_initiation', task_name='Exit approval')
        self.assertEqual(self.complete(item).status_code, 403)
        self.assertEqual(self.client.patch(f'/api/v1/onboarding/checklist/{item.pk}/',
            {'completed': True, 'onboarding_record': self.record().pk, 'offboarding_record': None}, format='json').status_code, 403)

    def test_identity_preview_is_read_only_and_requires_create_permission(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        before = (get_user_model().objects.count(), EmployeeMaster.objects.count(), OnboardingRecord.objects.count())
        url = '/api/v1/onboarding/onboarding/employee_identity_preview/'
        response = self.client.get(url, {'first_name': 'Preview', 'surname': 'Employee'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['email'], 'preview.employee@rejlers.ae')
        self.assertTrue(response.data['available'])
        self.assertEqual(before, (get_user_model().objects.count(), EmployeeMaster.objects.count(), OnboardingRecord.objects.count()))
        RolePermission.objects.filter(role__code='hr_admin', permission__action='create').delete()
        self.assertEqual(self.client.get(url, {'first_name': 'Preview', 'surname': 'Employee'}).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/onboarding/onboarding/create_employee/', self.employee_payload(), format='json').status_code, 403)

    def test_duplicate_email_is_case_insensitive_and_never_creates_partial_identity(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        existing = self.person('existing-email', 'default')
        existing.email = 'Lifecycle.Test@Rejlers.ae'
        existing.save(update_fields=['email'])
        before = (get_user_model().objects.count(), EmployeeMaster.objects.count(), OnboardingRecord.objects.count())
        response = self.client.post('/api/v1/onboarding/onboarding/create_employee/', self.employee_payload(), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('email', response.data['errors'])
        self.assertEqual(before, (get_user_model().objects.count(), EmployeeMaster.objects.count(), OnboardingRecord.objects.count()))

    def test_invalid_photo_and_create_failure_do_not_leave_partial_employee(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        before = (get_user_model().objects.count(), EmployeeMaster.objects.count(), OnboardingRecord.objects.count())
        payload = self.employee_payload()
        payload['photo'] = SimpleUploadedFile('wrong.pdf', b'not an image', content_type='application/pdf')
        response = self.client.post('/api/v1/onboarding/onboarding/create_employee/', payload, format='multipart')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(before, (get_user_model().objects.count(), EmployeeMaster.objects.count(), OnboardingRecord.objects.count()))
        with patch('apps.onboarding.views.EmployeeService.create_employee', side_effect=ValueError('Creation failed')):
            response = self.client.post('/api/v1/onboarding/onboarding/create_employee/', self.employee_payload(), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(before, (get_user_model().objects.count(), EmployeeMaster.objects.count(), OnboardingRecord.objects.count()))

    def test_creation_uses_preview_email_and_reports_optional_photo_failure(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        payload = self.employee_payload()
        payload.pop('email')
        preview = self.client.get('/api/v1/onboarding/onboarding/employee_identity_preview/',
                                  {'first_name': payload['first_name'], 'surname': payload['surname']})
        self.assertEqual(preview.status_code, 200, preview.data)
        payload['photo'] = SimpleUploadedFile('photo.jpg', b'photo fixture', content_type='image/jpeg')
        with patch('apps.onboarding.views.S3Service', side_effect=RuntimeError('Storage unavailable')):
            response = self.client.post('/api/v1/onboarding/onboarding/create_employee/', payload, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['email'], preview.data['email'])
        self.assertFalse(response.data['photo_uploaded'])
        self.assertTrue(response.data['warnings'])
        self.assertTrue(OnboardingRecord.objects.filter(pk=response.data['onboarding_id']).exists())

    def manager_fixture(self, username, *, employment_status='active', active=True):
        user = self.person(username, f'manager-fixture-{username}', actions=())
        employee = EmployeeMaster.objects.create(
            user=user, email=user.email, employee_number=username, employee_code=username, emp_code=username,
            first_name=username, last_name='Manager', join_date=date.today(), employment_status=employment_status,
        )
        get_user_model().objects.filter(pk=user.pk).update(is_active=active)
        return employee

    def test_manager_options_allow_lifecycle_create_without_general_hr_read(self):
        self.grant_only_hr_actions(self.user, 'hr_onboarding')
        RolePermission.objects.filter(role__code='hr_admin').exclude(permission__action='create').delete()
        self.assertFalse(module_action_allowed(self.user, 'hr_management', 'read'))
        self.assertFalse(module_action_allowed(self.user, 'hr_onboarding', 'read'))
        manager = self.manager_fixture('available-manager')
        self.manager_fixture('inactive-manager', active=False)
        self.manager_fixture('exited-manager', employment_status='exited')
        self.manager_fixture('suspended-manager', employment_status='suspended')
        url = '/api/v1/onboarding/onboarding/employee_manager_options/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['user_id'], manager.user_id)
        self.assertEqual(set(response.data['results'][0]), {'user_id', 'first_name', 'last_name', 'email', 'employee_number'})
        RolePermission.objects.filter(role__code='hr_admin').delete()
        RolePermission.objects.create(role=self.user.rbac_profile.roles.get(), permission=self.module.permissions.get(action='read'))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_invalid_and_inactive_managers_are_rejected_before_any_employee_writes(self):
        self.grant_only_hr_actions(self.user, 'hr_onboarding')
        inactive = self.manager_fixture('inactive-selection', active=False)
        exited = self.manager_fixture('exited-selection', employment_status='exited')
        before = (get_user_model().objects.count(), EmployeeMaster.objects.count(), OnboardingRecord.objects.count())
        for manager_id in ('invalid', '999999', inactive.user_id, exited.user_id):
            with self.subTest(manager_id=manager_id):
                payload = {**self.employee_payload(), 'manager_id': manager_id}
                response = self.client.post('/api/v1/onboarding/onboarding/create_employee/', payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('manager_id', response.data['errors'])
                self.assertEqual(before, (get_user_model().objects.count(), EmployeeMaster.objects.count(), OnboardingRecord.objects.count()))

    def test_selected_active_manager_is_preserved_on_created_employee(self):
        self.grant_only_hr_actions(self.user, 'hr_onboarding')
        manager = self.manager_fixture('selected-manager')
        payload = {**self.employee_payload(), 'manager_id': str(manager.user_id)}
        response = self.client.post('/api/v1/onboarding/onboarding/create_employee/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        employee = EmployeeMaster.objects.get(pk=response.data['employee_master_id'])
        self.assertEqual(employee.manager_id, manager.pk)

    def test_hr_management_read_can_fetch_only_explicitly_scoped_onboarding_checklists(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        onboarding = self.record()
        offboarding = self.record(offboarding=True)
        item = Checklist.objects.create(onboarding_record=onboarding, stage='pre_hire', task_name='Onboarding task')
        Checklist.objects.create(offboarding_record=offboarding, stage='exit_initiation', task_name='Offboarding task')
        url = '/api/v1/onboarding/checklist/'
        response = self.client.get(url, {'workflow': 'onboarding'})
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual([row['id'] for row in rows], [item.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, {'workflow': 'onboarding', 'offboarding_record': offboarding.pk}).status_code, 403)

    def test_owner_options_require_edit_and_return_only_eligible_active_operators(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        RolePermission.objects.filter(role__code='hr_admin', permission__action='create').delete()
        owner = self.person('eligible-owner', 'case-owner', actions=('read', 'update'))
        self.person('create-only-owner', 'case-creator', actions=('read', 'create'))
        inactive = self.person('inactive-owner', 'inactive-case-owner', actions=('read', 'update'))
        get_user_model().objects.filter(pk=inactive.pk).update(is_active=False)
        denied = self.person('denied-owner', 'denied-case-owner', actions=('read', 'update'))
        UserPermissionOverride.objects.create(user_profile=denied.rbac_profile,
            permission=self.module.permissions.get(action='update'), allowed=False)
        url = '/api/v1/onboarding/onboarding/owner_options/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({row['user_id'] for row in response.data['results']}, {self.user.pk, owner.pk})
        self.assertEqual(set(response.data['results'][0]), {'user_id', 'first_name', 'last_name', 'email', 'employee_number'})
        RolePermission.objects.filter(role__code='hr_admin', permission__action='update').delete()
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_case_owner_update_validates_eligibility_and_allows_clearing_owner(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        record = self.record()
        owner = self.person('selected-owner', 'selected-case-owner', actions=('read', 'update'))
        url = f'/api/v1/onboarding/onboarding/{record.pk}/'
        response = self.client.patch(url, {'assigned_to': owner.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        record.refresh_from_db()
        self.assertEqual(record.assigned_to_id, owner.pk)
        get_user_model().objects.filter(pk=owner.pk).update(is_active=False)
        response = self.client.patch(url, {'assigned_to': owner.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('assigned_to', response.data)
        response = self.client.patch(url, {'assigned_to': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        record.refresh_from_db()
        self.assertIsNone(record.assigned_to_id)

    def test_joining_date_syncs_canonical_employee_and_default_probation_without_moving_deadlines(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        employee = self.manager_fixture('date-synced-employee')
        employee.join_date = date(2026, 10, 1)
        employee.probation_end_date = date(2027, 4, 1)
        employee.save(update_fields=['join_date', 'probation_end_date'])
        record = self.record(canonical_employee=employee, user=employee.user, joining_date=employee.join_date)
        deadline = record.target_completion_date
        task = Checklist.objects.create(onboarding_record=record, stage='pre_hire', task_name='Existing deadline', due_date=date(2026, 10, 5))
        response = self.client.patch(f'/api/v1/onboarding/onboarding/{record.pk}/', {'joining_date': '2026-11-02'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        employee.refresh_from_db()
        record.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(employee.join_date, date(2026, 11, 2))
        self.assertEqual(employee.probation_end_date, date(2027, 5, 2))
        self.assertEqual(record.target_completion_date, deadline)
        self.assertEqual(task.due_date, date(2026, 10, 5))
        employee.probation_end_date = date(2027, 8, 1)
        employee.save(update_fields=['probation_end_date'])
        response = self.client.patch(f'/api/v1/onboarding/onboarding/{record.pk}/', {'joining_date': '2026-12-02'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        employee.refresh_from_db()
        self.assertEqual(employee.probation_end_date, date(2027, 8, 1))

    def test_closed_cases_cannot_change_owner_or_joining_date(self):
        for status in ('completed', 'cancelled', 'rejected'):
            with self.subTest(status=status):
                record = self.record(status=status, employee_email=f'closed-{status}@example.test')
                before = record.joining_date
                response = self.client.patch(f'/api/v1/onboarding/onboarding/{record.pk}/',
                    {'assigned_to': self.user.pk, 'joining_date': '2026-12-01'}, format='json')
                self.assertEqual(response.status_code, 403, response.data)
                record.refresh_from_db()
                self.assertIsNone(record.assigned_to_id)
                self.assertEqual(record.joining_date, before)

    def test_task_evidence_notes_are_saved_without_completing_task(self):
        self.grant_only_hr_actions(self.user, 'hr_management')
        record = self.record()
        self.assertEqual(self.start(record, 'pre_hire').status_code, 200)
        item = record.checklist_items.first()
        response = self.client.patch(f'/api/v1/onboarding/checklist/{item.pk}/',
            {'description': 'Verified signed contract in employee documents.'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        item.refresh_from_db()
        self.assertEqual(item.description, 'Verified signed contract in employee documents.')
        self.assertFalse(item.completed)
        self.assertIsNone(item.completed_by_id)

    def test_hr_admin_without_canonical_employee_can_start_and_complete_pre_hire(self):
        self.assertFalse(EmployeeMaster.objects.filter(user=self.user).exists())
        record = self.record()
        response = self.start(record, 'pre_hire')
        self.assertEqual(response.status_code, 200, response.data)
        permission = response.data['checklist_stage_permissions']['pre_hire']
        self.assertTrue(permission['can_start'])
        self.assertTrue(permission['can_manage'])
        self.assertIsNone(permission['disabled_reason'])
        item = record.checklist_items.first()
        response = self.complete(item)
        self.assertEqual(response.status_code, 200, response.data)
        item.refresh_from_db()
        self.assertTrue(item.completed)
        self.assertEqual(item.completed_by, self.user)

    def test_hr_admin_with_unrelated_business_designation_can_manage_hr_stages(self):
        EmployeeMaster.objects.create(
            user=self.user, email=self.user.email, employee_number='HR-ENGINEER', employee_code='HR-ENGINEER', emp_code='HR-ENGINEER',
            first_name='HR', last_name='Operator', designation='Software Engineer', join_date=date.today(),
        )
        for stage in ('pre_hire', 'first_day', 'final_validation'):
            with self.subTest(stage=stage):
                record = self.record(employee_email=f'{stage}@example.test')
                self.ready_stage(record, stage)
                response = self.start(record, stage)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(self.complete(record.checklist_items.filter(stage=stage).first()).status_code, 200)

    def test_hr_admin_can_manage_offboarding_hr_and_payroll_stages(self):
        for stage in ('exit_initiation', 'asset_return', 'exit_clearance', 'final_settlement'):
            with self.subTest(stage=stage):
                record = self.record(offboarding=True)
                self.ready_stage(record, stage)
                response = self.start(record, stage)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertTrue(response.data['checklist_stage_permissions'][stage]['can_manage'])
                self.assertEqual(self.complete(record.checklist_items.filter(stage=stage).first()).status_code, 200)

    def test_hr_can_complete_onboarding_it_but_offboarding_revocation_stays_ict_owned(self):
        ict_user = self.person('ict-operator', 'ict_admin')
        for offboarding, stage in ((False, 'it_provisioning'), (True, 'access_revocation')):
            with self.subTest(stage=stage):
                self.client.force_authenticate(self.user)
                record = self.record(offboarding=offboarding)
                self.ready_stage(record, stage)
                response = self.start(record, stage)
                self.assertEqual(response.status_code, 200, response.data)
                permission = response.data['checklist_stage_permissions'][stage]
                self.assertTrue(permission['can_start'])
                self.assertEqual(permission['can_manage'], not offboarding)
                item = record.checklist_items.filter(stage=stage).first()
                self.assertEqual(self.complete(item).status_code, 403 if offboarding else 200)
                item.refresh_from_db()
                self.assertEqual(item.completed, not offboarding)
                self.client.force_authenticate(ict_user)
                self.assertEqual(self.complete(item).status_code, 200)

    def test_stage_sequence_cannot_be_skipped_and_reason_names_prerequisite(self):
        record = self.record()
        permission = onboarding_stage_permissions(self.user, record)['final_validation']
        self.assertFalse(permission['can_start'])
        self.assertIn('Complete Pre-Hire Initiation', permission['disabled_reason'])
        self.assertEqual(self.start(record, 'final_validation').status_code, 403)
        self.assertFalse(record.checklist_items.exists())
        self.start(record, 'pre_hire')
        self.assertEqual(self.start(record, 'it_provisioning').status_code, 403)

    def test_pending_exit_project_approval_still_blocks_hr_handoff(self):
        record = self.record(offboarding=True, project_manager_approval_status='pending')
        self.ready_stage(record, 'access_revocation')
        permission = offboarding_stage_permissions(self.user, record)['access_revocation']
        self.assertFalse(permission['can_start'])
        self.assertIn('Project manager approval', permission['disabled_reason'])
        self.assertEqual(self.start(record, 'access_revocation').status_code, 403)

    def test_pending_exit_approvals_still_block_hr_clearance(self):
        record = self.record(offboarding=True)
        self.ready_stage(record, 'exit_clearance')
        record.exit_approvals.create(approval_step='hr_coordinator', approver=self.user, status='pending')
        permission = offboarding_stage_permissions(self.user, record)['exit_clearance']
        self.assertFalse(permission['can_manage'])
        self.assertIn('pending exit approvals', permission['disabled_reason'])
        self.assertEqual(self.start(record, 'exit_clearance').status_code, 403)

    def test_effective_hr_edit_grant_authorizes_onboarding_regardless_of_role_name(self):
        record = self.record()
        for role in ('default', 'ict_admin', 'finance_admin', 'manager', 'admin', 'super_admin'):
            with self.subTest(role=role):
                actor = self.person(f'actor-{role}', role)
                self.client.force_authenticate(actor)
                self.assertEqual(self.start(record, 'pre_hire').status_code, 200)
                permission = onboarding_stage_permissions(actor, record)['pre_hire']
                self.assertTrue(permission['can_manage'])
                self.assertIsNone(permission['disabled_reason'])

    def test_read_only_hr_module_grant_does_not_authorize_mutation(self):
        RolePermission.objects.filter(role__code='hr_admin').exclude(permission__action='read').delete()
        record = self.record()
        self.assertEqual(self.start(record, 'pre_hire').status_code, 403)
        self.assertFalse(onboarding_stage_permissions(self.user, record)['pre_hire']['can_manage'])

    def test_explicit_edit_deny_wins_over_hr_duty_role(self):
        for permission in self.module.permissions.filter(action='update'):
            UserPermissionOverride.objects.create(user_profile=self.user.rbac_profile, permission=permission, allowed=False)
        record = self.record()
        self.assertEqual(self.start(record, 'pre_hire').status_code, 403)
        self.assertFalse(record.checklist_items.exists())

    def test_custom_effective_grant_survives_hr_role_change_until_all_grants_are_revoked(self):
        record = self.record()
        # Keep the effective module grant so these assertions specifically test ownership.
        actor = self.person('generic-permissions', 'custom_lifecycle_access')
        UserRole.objects.create(user_profile=self.user.rbac_profile, role=actor.rbac_profile.roles.get())
        Role.objects.filter(code='hr_admin').update(is_active=False)
        self.assertTrue(can_manage_onboarding_stage(self.user, 'pre_hire', record))
        Role.objects.filter(code='hr_admin').update(is_active=True)
        self.assertTrue(can_manage_onboarding_stage(self.user, 'pre_hire', record))
        UserRole.objects.filter(user_profile=self.user.rbac_profile, role__code='hr_admin').delete()
        self.assertTrue(can_manage_onboarding_stage(self.user, 'pre_hire', record))
        UserRole.objects.filter(user_profile=self.user.rbac_profile).delete()
        self.assertFalse(can_manage_onboarding_stage(self.user, 'pre_hire', record))

    def test_inactive_deleted_and_locked_profiles_cannot_manage_stages(self):
        record = self.record()
        for changes in ({'status': 'inactive'}, {'is_deleted': True}, {'locked_until': timezone.now() + timedelta(hours=1)}):
            with self.subTest(changes=changes):
                UserProfile.objects.filter(user=self.user).update(**changes)
                self.assertFalse(can_manage_onboarding_stage(self.user, 'pre_hire', record))
                UserProfile.objects.filter(user=self.user).update(status='active', is_deleted=False, locked_until=None)

    def test_closed_workflows_remain_read_only(self):
        for offboarding in (False, True):
            for status in ('completed', 'cancelled', 'rejected'):
                with self.subTest(offboarding=offboarding, status=status):
                    record = self.record(offboarding=offboarding, status=status, employee_email=f'{status}@example.test')
                    permissions = (offboarding_stage_permissions if offboarding else onboarding_stage_permissions)(self.user, record)
                    for permission in permissions.values():
                        self.assertFalse(permission['can_start'])
                        self.assertFalse(permission['can_manage'])
                        self.assertIn('workflow is closed', permission['disabled_reason'])

    def test_manager_with_effective_hr_edit_can_manage_onboarding_without_direct_report_assignment(self):
        manager = self.person('line-manager', 'manager')
        master = EmployeeMaster.objects.create(
            user=manager, email=manager.email, employee_number='MANAGER', employee_code='MANAGER', emp_code='MANAGER',
            first_name='Line', last_name='Manager', designation='Engineer', join_date=date.today(),
        )
        report_user = self.person('direct-report', 'default')
        employee = EmployeeMaster.objects.create(
            user=report_user, email=report_user.email, manager=master, employee_number='REPORT', employee_code='REPORT', emp_code='REPORT',
            first_name='Direct', last_name='Report', designation='Engineer', join_date=date.today(),
        )
        record = self.record(user=report_user)
        self.ready_stage(record, 'first_day')
        self.assertTrue(can_manage_onboarding_stage(manager, 'first_day', record))
        employee.manager = None
        employee.save(update_fields=['manager'])
        self.assertTrue(can_manage_onboarding_stage(manager, 'first_day', record))

    def test_existing_hr_business_position_remains_eligible_with_effective_grant(self):
        actor = self.person('hr-position-holder', 'custom_hr_workflow')
        EmployeeMaster.objects.create(
            user=actor, email=actor.email, employee_number='HR-POSITION', employee_code='HR-POSITION', emp_code='HR-POSITION',
            first_name='HR', last_name='Manager', designation='HR Manager', join_date=date.today(),
        )
        self.assertTrue(can_manage_onboarding_stage(actor, 'pre_hire', self.record()))
