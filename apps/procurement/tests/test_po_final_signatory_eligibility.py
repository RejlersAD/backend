"""The PO final-signatory directory and assignment save share eligibility."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.hr_core.models import EmployeeMaster
from apps.procurement.models import PurchaseOrder, Vendor
from apps.procurement.services.purchase_order_approvals import MANAGEMENT_STAGE, normalize_assignments
from apps.procurement.views import PurchaseOrderViewSet, PurchaseRequisitionViewSet
from apps.rbac.approval_eligibility import has_business_position
from apps.rbac.models import (
    Module, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

from .approval_fixtures import grant_approval, set_position


router = DefaultRouter()
router.register('requisitions', PurchaseRequisitionViewSet, basename='final-signatory-requisitions')
router.register('orders', PurchaseOrderViewSet, basename='final-signatory-orders')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/'
COMBINED_TITLE = 'Sr. Vice President, Middle East\nCEO, Rejlers Abu Dhabi'


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class POFinalSignatoryEligibilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for code in ('procurement_requisitions', 'procurement_orders'):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
        self.editor = self.person('editor', 'Procurement Manager')
        role = Role.objects.create(code='final-signatory-editor', name='PO editor', level=3)
        UserRole.objects.create(user_profile=self.editor.rbac_profile, role=role)
        for module in Module.objects.filter(code__in=['procurement_requisitions', 'procurement_orders']):
            RoleModule.objects.create(role=role, module=module)
            for permission in module.permissions.filter(action__in=['read', 'update'], is_active=True):
                RolePermission.objects.create(role=role, permission=permission)
        self.ceo = self.person('ceo', COMBINED_TITLE)
        self.client = APIClient()
        self.client.force_authenticate(self.editor)
        vendor = Vendor.objects.create(vendor_code='FINAL-SIGNATORY', name='Signatory test supplier')
        self.order = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-0750_2026', vendor=vendor, title='Unassigned draft',
            total_amount='100.00', created_by=self.editor, approval_log=[],
        )

    def person(self, name, title):
        user = get_user_model().objects.create_user(
            f'final-signatory-{name}', email=f'{name}@final-signatory.example.test',
            first_name=name, last_name='Test',
        )
        grant_approval(user, 'procurement_orders')
        set_position(user, title)
        return user

    def candidates(self, role='po_final_signoff'):
        response = self.client.get(f'{BASE}requisitions/get_approvers/', {'role': role})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['role'], role)
        self.assertEqual(response.data['count'], len(response.data['users']))
        return response.data['users']

    def assignment(self, user=None):
        return {'stage': MANAGEMENT_STAGE, 'level': 0, 'user_id': str((user or self.ceo).pk)}

    def assert_not_eligible(self, user=None):
        user = user or self.ceo
        self.assertNotIn(str(user.pk), {row['id'] for row in self.candidates()})
        with self.assertRaises(ValidationError):
            normalize_assignments([self.assignment(user)], require_core=False)

    def save_assignment(self):
        with self.captureOnCommitCallbacks(execute=True), \
                patch('apps.procurement.serializers.notify_assigned_approvers'), \
                patch('apps.procurement.serializers.notify_purchase_order_created'):
            return self.client.patch(
                f'{BASE}orders/{self.order.pk}/',
                {'approval_log': [self.assignment()], 'notes': 'Keep draft input'}, format='json',
            )

    def test_full_official_ceo_titles_are_listed_and_save_without_rewriting_hr(self):
        for title in (
            'CEO', 'Chief Executive Officer', 'CEO, Rejlers Abu Dhabi',
            'CEO, Rejlers Abu Dhabi / Senior VP, Middle East Region',
            'CEO, Rejlers Abu Dhabi / Senior VP, Middle East Region. 5950 Abu Dhabi',
            COMBINED_TITLE,
            'Senior Vice President, Middle East / CEO, Rejlers Abu Dhabi',
            '  SR. VICE PRESIDENT, MIDDLE EAST - CEO, REJLERS ABU DHABI  ',
        ):
            with self.subTest(title=title):
                EmployeeMaster.objects.filter(user=self.ceo).update(designation=title)
                candidates = self.candidates()
                self.assertEqual([row['id'] for row in candidates], [str(self.ceo.pk)])
                self.assertEqual(candidates[0]['job_title'], title)
                response = self.save_assignment()
                self.assertEqual(response.status_code, 200, response.data)
                self.order.refresh_from_db()
                self.assertEqual(self.order.approval_log[0]['user_id'], str(self.ceo.pk))
                self.assertEqual(self.order.approval_log[0]['status'], 'Pending')
                self.assertIsNone(self.order.approved_at)
                self.assertEqual(EmployeeMaster.objects.get(user=self.ceo).designation, title)

    def test_related_titles_and_prefixed_ceo_titles_do_not_confer_authority(self):
        for title in (
            'Senior VP, Middle East Region', 'Vice President', 'Chief Operating Officer',
            f'Assistant to the {COMBINED_TITLE}', f'Former {COMBINED_TITLE}',
            'Deputy CEO, Rejlers Abu Dhabi / Senior VP, Middle East Region',
            'CEO, Another Company / Senior VP, Middle East Region',
            'CEO, Rejlers Abu Dhabi / Senior VP, Middle East Region. 5951 Abu Dhabi',
            'Former CEO, Rejlers Abu Dhabi / Senior VP, Middle East Region. 5950 Abu Dhabi',
            'Assistant to the CEO, Rejlers Abu Dhabi / Senior VP, Middle East Region. 5950 Abu Dhabi',
        ):
            with self.subTest(title=title):
                EmployeeMaster.objects.filter(user=self.ceo).update(designation=title)
                self.assert_not_eligible()

    def test_full_ceo_title_does_not_supply_finance_or_operations_authority(self):
        for position in ('finance', 'operations'):
            with self.subTest(position=position):
                self.assertFalse(has_business_position(self.ceo, position))

    def test_missing_hr_record_or_profile_title_cannot_supply_the_ceo_position(self):
        UserProfile.objects.filter(user=self.ceo).update(job_title=COMBINED_TITLE)
        EmployeeMaster.objects.filter(user=self.ceo).delete()
        self.assert_not_eligible()
        response = self.save_assignment()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('active HR record and CEO designation', str(response.data))

    def test_secondary_ceo_title_does_not_override_primary_position(self):
        EmployeeMaster.objects.filter(user=self.ceo).update(designation='Engineer', job_title_uae=COMBINED_TITLE)
        self.assert_not_eligible()
        EmployeeMaster.objects.filter(user=self.ceo).update(designation='')
        self.assertEqual([row['id'] for row in self.candidates()], [str(self.ceo.pk)])
        self.assertEqual(normalize_assignments([self.assignment()], require_core=False)[0]['user_id'], str(self.ceo.pk))

    def test_missing_permission_and_explicit_denial_exclude_the_signatory(self):
        UserRole.objects.filter(user_profile=self.ceo.rbac_profile).delete()
        self.assert_not_eligible()
        grant_approval(self.ceo, 'procurement_orders')
        permission = Permission.objects.filter(module__code='procurement_orders', action='approve', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.ceo.rbac_profile, permission=permission, allowed=False)
        self.assert_not_eligible()
        response = self.save_assignment()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('Purchase Order Approve permission', str(response.data))

    def test_disabled_module_excludes_the_signatory(self):
        Module.objects.filter(code='procurement_orders').update(is_active=False)
        self.assert_not_eligible()

    def test_inactive_locked_deleted_and_terminated_employees_are_excluded(self):
        for fields in ({'status': 'inactive'}, {'is_deleted': True},
                       {'locked_until': timezone.now() + timedelta(hours=1)}):
            with self.subTest(fields=fields):
                UserProfile.objects.filter(user=self.ceo).update(status='active', is_deleted=False, locked_until=None)
                UserProfile.objects.filter(user=self.ceo).update(**fields)
                self.assert_not_eligible()
        UserProfile.objects.filter(user=self.ceo).update(status='active', is_deleted=False, locked_until=None)
        EmployeeMaster.objects.filter(user=self.ceo).update(employment_status='terminated')
        self.assert_not_eligible()
        EmployeeMaster.objects.filter(user=self.ceo).update(employment_status='active')
        get_user_model().objects.filter(pk=self.ceo.pk).update(is_active=False)
        self.assert_not_eligible()

    def test_directory_selection_cannot_bypass_revocation_before_save(self):
        self.assertEqual([row['id'] for row in self.candidates()], [str(self.ceo.pk)])
        UserRole.objects.filter(user_profile=self.ceo.rbac_profile).delete()
        response = self.save_assignment()
        self.assertEqual(response.status_code, 400, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.approval_log, [])
        self.assertNotEqual(self.order.notes, 'Keep draft input')
        self.assertIsNone(self.order.approved_at)

    def test_any_active_directory_retains_employees_without_final_approval_access(self):
        ordinary = self.person('buyer', 'Engineer')
        UserRole.objects.filter(user_profile=ordinary.rbac_profile).delete()
        EmployeeMaster.objects.filter(user=ordinary).delete()
        self.assertIn(str(ordinary.pk), {row['id'] for row in self.candidates('any_active')})
        self.assert_not_eligible(ordinary)

    def test_final_signatory_directory_still_requires_existing_requester_read_access(self):
        UserRole.objects.filter(user_profile=self.editor.rbac_profile).delete()
        response = self.client.get(f'{BASE}requisitions/get_approvers/', {'role': 'po_final_signoff'})
        self.assertEqual(response.status_code, 403, response.data)
