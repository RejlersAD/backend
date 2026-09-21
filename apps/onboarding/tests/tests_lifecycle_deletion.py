"""Delete lifecycle cases without deleting employees or recreating cases on sync."""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from apps.hr_core.models import EmployeeMaster
from apps.onboarding.models import (
    AccessProvisioning, Checklist, Document, Equipment, ExitApproval,
    LifecycleCaseDeletion, OffboardingRecord, OnboardingRecord,
)
from apps.onboarding.views import ensure_onboarding_record
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints
from apps.users.views import EmployeeProfileViewSet


urlpatterns = [
    path('api/v1/onboarding/', include('apps.onboarding.urls')),
    path('api/v1/users/employees/active_employees/', EmployeeProfileViewSet.as_view({'get': 'active_employees'})),
]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class LifecycleCaseDeletionTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Deletion tests', code='delete-tests')
        self.modules = {}
        for code in ('hr_onboarding', 'hr_management'):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            self.modules[code] = module
        self.actor = get_user_model().objects.create_user('case-operator', email='operator@example.test')
        self.profile = UserProfile.objects.create(user=self.actor, organization=self.organization, job_title='Engineer')
        self.role = Role.objects.create(name='Custom lifecycle access', code='custom_lifecycle_delete', level=4)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.employee_user = get_user_model().objects.create_user('retained-employee', email='retained@example.test')
        self.employee = EmployeeMaster.objects.create(
            user=self.employee_user, first_name='Retained', last_name='Employee',
            email=self.employee_user.email, employee_number='DELETE-EMP-1',
            join_date=date.today(), employment_status='probation',
        )

    def grant(self, module_code='hr_onboarding', actions=('read', 'create', 'update', 'delete')):
        module = self.modules[module_code]
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action__in=actions):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def record(self, workflow='onboarding', **overrides):
        values = {
            'employee_name': self.employee.get_full_name(), 'employee_email': self.employee.email,
            'employee_id': self.employee.employee_number, 'user': self.employee_user,
            'canonical_employee': self.employee, 'position': 'Engineer', 'department': 'Engineering',
            'target_completion_date': date.today() + timedelta(days=30),
        }
        if workflow == 'onboarding':
            values['joining_date'] = date.today()
            model = OnboardingRecord
        else:
            values.update(last_working_day=date.today() + timedelta(days=20), exit_reason='resignation')
            model = OffboardingRecord
        values.update(overrides)
        return model.objects.create(**values)

    def url(self, record):
        workflow = 'onboarding' if isinstance(record, OnboardingRecord) else 'offboarding'
        return f'/api/v1/onboarding/{workflow}/{record.pk}/'

    def rows(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        return response.data.get('results', []) if isinstance(response.data, dict) else response.data

    def test_delete_allowed_through_either_hr_module_preserves_employee_and_removes_case_children(self):
        for module_code in self.modules:
            for workflow in ('onboarding', 'offboarding'):
                with self.subTest(module=module_code, workflow=workflow):
                    RoleModule.objects.filter(role=self.role).delete()
                    RolePermission.objects.filter(role=self.role).delete()
                    self.grant(module_code)
                    record = self.record(workflow)
                    relation = {f'{workflow}_record': record}
                    children = [
                        Checklist.objects.create(**relation, task_name='Case task'),
                        Document.objects.create(**relation, document_type='contract', document_name='Case document'),
                        Equipment.objects.create(**relation, equipment_type='laptop', item_name='Case equipment'),
                        AccessProvisioning.objects.create(**relation, access_type='email', access_name='Case account record'),
                    ]
                    if workflow == 'offboarding':
                        children.append(ExitApproval.objects.create(offboarding_record=record, approver=self.actor, approval_step='hr_approver'))
                    case_id = record.pk
                    response = self.client.delete(self.url(record))
                    self.assertEqual(response.status_code, 204, response.data)
                    self.assertFalse(type(record).objects.filter(pk=case_id).exists())
                    for child in children:
                        self.assertFalse(type(child).objects.filter(pk=child.pk).exists())
                    self.employee_user.refresh_from_db()
                    self.employee.refresh_from_db()
                    self.assertTrue(self.employee_user.is_active)
                    self.assertEqual(self.employee.employment_status, 'probation')
                    marker = LifecycleCaseDeletion.objects.get(workflow=workflow, record_id=case_id)
                    self.assertEqual(marker.canonical_employee, self.employee)
                    self.assertEqual(marker.user, self.employee_user)
                    self.assertEqual(marker.deleted_by, self.actor)

    def test_hr_role_and_edit_permission_do_not_imply_delete(self):
        self.role.code = 'hr_admin'
        self.role.save(update_fields=['code'])
        self.grant(actions=('read', 'create', 'update'))
        for workflow in ('onboarding', 'offboarding'):
            record = self.record(workflow)
            response = self.client.get(self.url(record))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.data['can_delete'])
            self.assertEqual(self.client.delete(self.url(record)).status_code, 403)
            self.assertTrue(type(record).objects.filter(pk=record.pk).exists())
        self.assertFalse(LifecycleCaseDeletion.objects.exists())

    def test_explicit_delete_deny_on_either_hr_alias_wins(self):
        self.grant('hr_onboarding')
        self.grant('hr_management')
        records = [self.record(), self.record('offboarding')]
        for module in self.modules.values():
            permission = module.permissions.filter(action='delete').first()
            override = UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
            for record in records:
                self.assertEqual(self.client.delete(self.url(record)).status_code, 403)
                self.assertFalse(self.client.get(self.url(record)).data['can_delete'])
            override.delete()
        self.assertFalse(LifecycleCaseDeletion.objects.exists())

    def test_inactive_deleted_locked_profiles_and_inactive_users_cannot_delete(self):
        self.grant()
        records = [self.record(), self.record('offboarding')]
        for field, value in (('status', 'suspended'), ('is_deleted', True), ('locked_until', timezone.now() + timedelta(hours=1))):
            original = getattr(self.profile, field)
            setattr(self.profile, field, value)
            self.profile.save(update_fields=[field])
            for record in records:
                self.assertEqual(self.client.delete(self.url(record)).status_code, 403)
            setattr(self.profile, field, original)
            self.profile.save(update_fields=[field])
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        for record in records:
            self.assertEqual(self.client.delete(self.url(record)).status_code, 403)
        self.assertFalse(LifecycleCaseDeletion.objects.exists())

    def test_list_and_detail_advertise_delete_and_deleted_case_disappears(self):
        self.grant()
        for workflow in ('onboarding', 'offboarding'):
            record = self.record(workflow)
            url = self.url(record)
            self.assertTrue(self.client.get(url).data['can_delete'])
            self.assertTrue(self.rows(self.client.get(f'/api/v1/onboarding/{workflow}/'))[0]['can_delete'])
            self.assertEqual(self.client.delete(url).status_code, 204)
            self.assertEqual(self.rows(self.client.get(f'/api/v1/onboarding/{workflow}/')), [])
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.delete(url).status_code, 404)

    def test_deleted_case_is_not_recreated_by_ensure_or_sync(self):
        self.grant()
        record = self.record()
        self.assertEqual(self.client.delete(self.url(record)).status_code, 204)
        self.assertEqual(ensure_onboarding_record(self.employee, self.actor), (None, False))
        response = self.client.post('/api/v1/onboarding/onboarding/ensure-employee-workflow/', {'user_id': self.employee_user.pk}, format='json')
        self.assertEqual(response.status_code, 410)
        response = self.client.post('/api/v1/onboarding/onboarding/sync-missing/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['created_count'], 0)
        self.assertFalse(OnboardingRecord.objects.exists())

    def test_legacy_deleted_email_and_employee_number_prevent_automatic_recreation(self):
        self.grant()
        for identity in ({'employee_email': self.employee.email.upper(), 'employee_id': ''}, {'employee_email': 'old-address@example.test', 'employee_id': self.employee.employee_number}):
            record = self.record(canonical_employee=None, user=None, **identity)
            # Canonical-link signals may attach identity at creation; legacy
            # imported rows can still have neither link at deletion time.
            OnboardingRecord.objects.filter(pk=record.pk).update(canonical_employee=None, user=None)
            self.assertEqual(self.client.delete(self.url(record)).status_code, 204)
            self.assertIsNone(LifecycleCaseDeletion.objects.get(record_id=record.pk).canonical_employee_id)
            self.assertEqual(ensure_onboarding_record(self.employee, self.actor), (None, False))
            LifecycleCaseDeletion.objects.all().delete()

    def test_explicit_user_delete_grant_works_without_role_delete_grant(self):
        self.grant(actions=('read',))
        for permission in self.modules['hr_onboarding'].permissions.filter(action='delete'):
            UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=True)
        for workflow in ('onboarding', 'offboarding'):
            record = self.record(workflow)
            self.assertTrue(self.client.get(self.url(record)).data['can_delete'])
            self.assertEqual(self.client.delete(self.url(record)).status_code, 204)

    def test_deleting_one_case_does_not_remove_another_case_for_the_same_employee(self):
        self.grant()
        onboarding = self.record()
        offboarding = self.record('offboarding')
        other_task = Checklist.objects.create(offboarding_record=offboarding, task_name='Keep exit task')
        other_approval = ExitApproval.objects.create(offboarding_record=offboarding, approver=self.actor, approval_step='hr_approver')
        self.assertEqual(self.client.delete(self.url(onboarding)).status_code, 204)
        self.assertTrue(OffboardingRecord.objects.filter(pk=offboarding.pk).exists())
        self.assertTrue(Checklist.objects.filter(pk=other_task.pk).exists())
        self.assertTrue(ExitApproval.objects.filter(pk=other_approval.pk).exists())

    def test_explicit_case_creation_after_deletion_remains_allowed(self):
        self.grant()
        record = self.record()
        self.assertEqual(self.client.delete(self.url(record)).status_code, 204)
        response = self.client.post('/api/v1/onboarding/onboarding/', {
            'employee_name': self.employee.get_full_name(), 'employee_email': self.employee.email,
            'user': self.employee_user.pk, 'employee_id': self.employee.employee_number,
            'position': 'Engineer', 'department': 'Engineering',
            'joining_date': date.today().isoformat(), 'target_completion_date': date.today().isoformat(),
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        existing, created = ensure_onboarding_record(self.employee, self.actor)
        self.assertFalse(created)
        self.assertEqual(existing.pk, response.data['id'])

    def test_case_and_tombstone_changes_roll_back_together(self):
        self.grant()
        record = self.record()
        with patch.object(OnboardingRecord, 'delete', side_effect=RuntimeError('simulated database deletion failure')):
            with self.assertRaises(RuntimeError):
                self.client.delete(self.url(record))
        self.assertTrue(OnboardingRecord.objects.filter(pk=record.pk).exists())
        self.assertFalse(LifecycleCaseDeletion.objects.exists())

    def test_concurrent_case_deletion_returns_not_found(self):
        self.grant()
        record = self.record()
        with patch.object(OnboardingRecord.objects, 'select_for_update') as lock:
            lock.return_value.get.side_effect = OnboardingRecord.DoesNotExist
            response = self.client.delete(self.url(record))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(LifecycleCaseDeletion.objects.exists())

    def test_legacy_active_employee_list_exposes_same_delete_permission_and_removes_deleted_case(self):
        self.grant('hr_management', actions=('read',))
        self.grant('hr_onboarding', actions=('read',))
        record = self.record()
        url = '/api/v1/users/employees/active_employees/?onboarding_active=true&minimal=true'
        rows = self.rows(self.client.get(url))
        self.assertEqual(rows[0]['onboarding_record_id'], record.pk)
        self.assertFalse(rows[0]['onboarding_can_delete'])
        self.grant('hr_management', actions=('delete',))
        self.assertTrue(self.rows(self.client.get(url))[0]['onboarding_can_delete'])
        self.assertEqual(self.client.delete(self.url(record)).status_code, 204)
        self.assertEqual(self.rows(self.client.get(url)), [])
        self.assertEqual(len(self.rows(self.client.get('/api/v1/users/employees/active_employees/?minimal=true'))), 1)
