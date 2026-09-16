"""Exercise employee-selected PR decisions through the central API guard."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path, resolve
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.views import PurchaseOrderViewSet, PurchaseRequisitionViewSet
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import ModuleActionGuardMixin, secure_module_endpoints

from .approval_fixtures import grant_approval, set_position


router = DefaultRouter()
router.register('requisitions', PurchaseRequisitionViewSet, basename='requisition')
router.register('orders', PurchaseOrderViewSet, basename='order')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class EmployeeSelectedLevelOneGuardedAPITests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name='Level one API', code='level-one-api')
        self.module, _ = Module.objects.get_or_create(
            code='procurement_requisitions', defaults={'name': 'Purchase Recommendations'},
        )
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        self.users = []
        for index in range(3):
            user = get_user_model().objects.create_user(
                username=f'level-one-api-{index}', email=f'level-one-api-{index}@example.test',
            )
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            UserRole.objects.filter(user_profile=profile).delete()
            profile.signature_image = f'level-one-signature-{index}'
            profile.save(update_fields=['signature_image'])
            set_position(user, 'Full Stack Developer')
            self.users.append(user)
        self.issuer, self.employee, self.other = self.users
        self.client = APIClient()
        self.client.force_authenticate(self.employee)
        self.pr = PurchaseRequisition.objects.create(
            pr_number='PR-LEVEL-ONE-API', issued_by=self.issuer, status='submitted',
            approval_workflow_config=[self.stage(self.employee)],
        )
        self.detail_url = f'/api/v1/procurement/requisitions/{self.pr.pk}/'
        self.decision_url = self.detail_url + 'process_dynamic_approval/'
        self.assertTrue(issubclass(resolve(self.decision_url).func.cls, ModuleActionGuardMixin))
        self.assertFalse(module_action_allowed(self.employee, self.module.code, 'approve'))

    def stage(self, user, level=1, role='Level 1 Approver'):
        return {
            'level': level, 'role': role, 'stage': role,
            'user_id': str(user.pk), 'user_email': user.email,
            'status': 'pending', 'assignment_id': f'level-one-api-{user.pk}-{level}',
        }

    def set_route(self, stages):
        self.pr.approval_workflow_config = stages
        self.pr.save(update_fields=['approval_workflow_config'])

    def deny(self, action):
        for permission in self.module.permissions.filter(action=action, is_active=True):
            UserPermissionOverride.objects.create(
                user_profile=self.employee.rbac_profile, permission=permission, allowed=False,
            )

    def assert_pending(self):
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertTrue(all(stage['status'] == 'pending' for stage in self.pr.approval_workflow_config))

    def test_assigned_employee_can_read_and_approve_without_module_wide_access(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.post(self.decision_url, {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        stage = self.pr.approval_workflow_config[0]
        self.assertEqual(stage['status'], 'approved')
        self.assertEqual(stage['approved_by_id'], str(self.employee.pk))
        self.assertEqual(stage['signature_user_id'], str(self.employee.pk))
        self.assertFalse(module_action_allowed(self.employee, self.module.code, 'approve'))
        self.assertFalse(module_action_allowed(self.employee, self.module.code, 'read'))
        self.assertFalse(self.employee.rbac_profile.roles.exists())
        self.assertEqual(self.client.get('/api/v1/procurement/requisitions/').status_code, 403)
        self.assertEqual(self.client.post('/api/v1/procurement/requisitions/', {}, format='json').status_code, 403)

    def test_assigned_employee_can_reject_with_reason(self):
        response = self.client.post(
            self.detail_url + 'process_dynamic_rejection/',
            {'reason': 'Please correct the supplied vendor details.'}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'rejected')
        self.assertEqual(self.pr.approval_workflow_config[0]['rejected_by_id'], str(self.employee.pk))

    def test_legacy_pm_action_uses_the_same_assigned_level_one_scope(self):
        response = self.client.post(self.detail_url + 'pm_approve/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_unassigned_employee_cannot_read_or_decide(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.detail_url).status_code, 403)
        self.assertEqual(self.client.post(self.decision_url, {}, format='json').status_code, 403)
        self.assert_pending()

    def test_future_level_one_assignment_cannot_skip_procurement(self):
        self.set_route([
            self.stage(self.other, level=0, role='Procurement Department'),
            self.stage(self.employee),
        ])
        self.assertEqual(self.client.post(self.decision_url, {}, format='json').status_code, 403)
        self.assert_pending()

    def test_explicit_approve_deny_overrides_assignment(self):
        self.deny('approve')
        self.assertEqual(self.client.post(self.decision_url, {}, format='json').status_code, 403)
        self.assert_pending()

    def test_explicit_read_deny_overrides_assigned_record_visibility(self):
        self.deny('read')
        self.assertEqual(self.client.get(self.detail_url).status_code, 403)
        self.assert_pending()

    def test_disabled_module_blocks_assignment(self):
        Module.objects.filter(pk=self.module.pk).update(is_active=False)
        self.assertEqual(self.client.post(self.decision_url, {}, format='json').status_code, 403)
        self.assert_pending()

    def test_named_business_role_at_level_one_keeps_position_and_grant_checks(self):
        self.set_route([self.stage(self.employee, role='CEO')])
        self.assertEqual(self.client.post(self.decision_url, {}, format='json').status_code, 403)
        grant_approval(self.employee, self.module.code)
        self.assertEqual(self.client.post(self.decision_url, {}, format='json').status_code, 403)
        self.assert_pending()

    def test_generic_higher_stage_does_not_acquire_level_one_authority(self):
        self.set_route([self.stage(self.employee, level=2, role='Level 2 Approver')])
        self.assertEqual(self.client.post(self.decision_url, {}, format='json').status_code, 403)
        self.assert_pending()

    def test_assignment_does_not_authorize_editing_the_request(self):
        response = self.client.patch(self.detail_url, {'title': 'Unauthorized edit'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assert_pending()

    def test_read_only_module_access_does_not_authorize_another_employees_decision(self):
        role = Role.objects.create(code='level-one-api-reader', name='PR reader', level=4)
        RoleModule.objects.create(role=role, module=self.module)
        for permission in self.module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(user_profile=self.employee.rbac_profile, role=role)
        self.set_route([self.stage(self.other)])
        self.assertEqual(self.client.get(self.detail_url).status_code, 200)
        self.assertFalse(module_action_allowed(self.employee, self.module.code, 'approve'))
        self.assertEqual(self.client.post(self.decision_url, {}, format='json').status_code, 403)
        self.assert_pending()

    def test_purchase_order_assignment_does_not_acquire_pr_level_one_authority(self):
        module, _ = Module.objects.get_or_create(code='procurement_orders', defaults={'name': 'Purchase Orders'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        vendor = Vendor.objects.create(vendor_code='LEVEL-ONE-API', name='Level one vendor')
        order = PurchaseOrder.objects.create(
            po_number='PO-LEVEL-ONE-API', vendor=vendor, created_by=self.issuer,
            total_amount=10, approval_log=[self.stage(self.employee)],
        )
        response = self.client.post(f'/api/v1/procurement/orders/{order.pk}/approve/', {}, format='json')
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.approval_log[0]['status'], 'pending')
