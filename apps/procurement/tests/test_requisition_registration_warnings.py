"""Registration advisories must not prevent saving or submitting a recommendation."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.procurement.models import PurchaseRequisition
from apps.procurement.views import PurchaseRequisitionViewSet
from apps.rbac.models import Module, Organization, Permission, UserProfile
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

from .approval_fixtures import grant_approval, set_position


router = DefaultRouter()
router.register('requisitions', PurchaseRequisitionViewSet, basename='registration-warning-pr')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/requisitions/'


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class RequisitionRegistrationWarningsAPITests(TestCase):
    def setUp(self):
        cache.clear()
        organization = Organization.objects.create(code='registration-warnings', name='Registration warnings')
        module, _ = Module.objects.get_or_create(
            code='procurement_requisitions', defaults={'name': 'Purchase Recommendations'},
        )
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        users = get_user_model()
        self.issuer = users.objects.create_superuser(
            'registration-issuer', email='registration-issuer@example.test', password='test-only',
        )
        self.employee = users.objects.create_user(
            'registration-employee', email='registration-employee@example.test',
        )
        for user in (self.issuer, self.employee):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            profile.status = 'active'
            profile.signature_image = f'signature-{user.pk}'
            profile.save()
            set_position(user, 'AI Team Lead')
        self.client = APIClient()
        self.client.force_authenticate(self.issuer)

    def stage(self, **changes):
        stage = {'level': 3, 'role': 'Manager of Projects (MoP)', 'user_id': str(self.employee.pk)}
        stage.update(changes)
        return stage

    def create(self, **changes):
        payload = {
            'pr_number': 'PR-REGISTRATION-WARNING', 'po_applicable': True,
            'approval_workflow_config': [self.stage()],
        }
        payload.update(changes)
        response = self.client.post(BASE, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def assert_warning(self, response, text):
        warnings = response.data['registration_warnings']
        self.assertIsInstance(warnings, list)
        self.assertTrue(all(isinstance(warning, str) for warning in warnings))
        self.assertTrue(any(text.lower() in warning.lower() for warning in warnings), warnings)

    def test_create_with_mop_position_mismatch_returns_warning_and_resets_client_decision(self):
        response = self.create(approval_workflow_config=[self.stage(
            status='approved', signature='forged', approved_by_id=str(self.issuer.pk),
        )], registration_warnings=['Forged warning'])

        self.assert_warning(response, 'Level 3')
        self.assert_warning(response, 'business position')
        self.assertNotIn('Forged warning', response.data['registration_warnings'])
        requisition = PurchaseRequisition.objects.get(pk=response.data['id'])
        stage = requisition.approval_workflow_config[0]
        self.assertEqual(stage['user_id'], str(self.employee.pk))
        self.assertEqual(stage['status'], 'pending')
        self.assertNotIn('signature', stage)
        self.assertNotIn('approved_by_id', stage)
        detail = self.client.get(f'{BASE}{requisition.pk}/')
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['registration_warnings'], response.data['registration_warnings'])

    def test_draft_update_keeps_ineligible_assignment_and_returns_warning(self):
        response = self.create(approval_workflow_config=[])
        updated = self.client.patch(f"{BASE}{response.data['id']}/", {
            'approval_workflow_config': [self.stage()],
        }, format='json')

        self.assertEqual(updated.status_code, 200, updated.data)
        self.assert_warning(updated, 'Level 3')
        self.assertEqual(updated.data['approval_workflow_config'][0]['user_id'], str(self.employee.pk))

    def test_empty_workflow_can_be_submitted_without_notifications_or_approval(self):
        response = self.create(requisition_type='project', po_applicable=False, approval_workflow_config=[])
        with patch('apps.notifications.services.NotificationService.create_notification') as notify:
            with self.captureOnCommitCallbacks(execute=True):
                submitted = self.client.post(f"{BASE}{response.data['id']}/submit/", {}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(submitted.data['status'], 'submitted')
        self.assertEqual(submitted.data['approval_workflow_config'], [])
        self.assertFalse(submitted.data['can_approve'])
        self.assertFalse(submitted.data['convert_to_po_enabled'])
        self.assert_warning(submitted, 'workflow')
        self.assert_warning(submitted, 'Level 4')
        self.assert_warning(submitted, 'Level 5')
        notify.assert_not_called()

    def test_missing_role_and_assignee_can_be_saved_and_submitted(self):
        response = self.create(approval_workflow_config=[self.stage(role='', user_id='')])
        self.assert_warning(response, 'role')
        self.assert_warning(response, 'approver')
        submitted = self.client.post(f"{BASE}{response.data['id']}/submit/", {}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(submitted.data['status'], 'submitted')
        self.assertEqual(submitted.data['approval_workflow_config'][0]['user_id'], '')
        self.assertFalse(submitted.data['can_approve'])

    def test_duplicate_employee_assignments_are_advisory(self):
        response = self.create(approval_workflow_config=[
            self.stage(level=1, role='Level 1 Approver'), self.stage(),
        ])
        self.assert_warning(response, 'duplicates an approver')
        submitted = self.client.post(f"{BASE}{response.data['id']}/submit/", {}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(len(submitted.data['approval_workflow_config']), 2)

    def test_inactive_employee_assignment_warns_without_blocking_registration(self):
        self.employee.is_active = False
        self.employee.save(update_fields=['is_active'])
        response = self.create()
        self.assert_warning(response, 'active')
        self.assertEqual(response.data['approval_workflow_config'][0]['user_id'], str(self.employee.pk))

    def test_position_warning_never_authorizes_the_actual_approval(self):
        grant_approval(self.employee, 'procurement_requisitions')
        response = self.create()
        url = f"{BASE}{response.data['id']}/"
        submitted = self.client.post(url + 'submit/', {}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.client.force_authenticate(self.employee)

        decision = self.client.post(url + 'process_dynamic_approval/', {}, format='json')

        self.assertEqual(decision.status_code, 403, decision.data)
        requisition = PurchaseRequisition.objects.get(pk=response.data['id'])
        self.assertEqual(requisition.status, 'submitted')
        self.assertEqual(requisition.approval_workflow_config[0]['status'], 'pending')

    def test_missing_referenced_user_remains_invalid_input(self):
        response = self.client.post(BASE, {
            'pr_number': 'PR-REGISTRATION-BAD-USER',
            'approval_workflow_config': [self.stage(user_id='not-a-user')],
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('approval_workflow_config', response.data)
        self.assertFalse(PurchaseRequisition.objects.filter(pr_number='PR-REGISTRATION-BAD-USER').exists())

    def test_legacy_uuid_assignment_without_email_is_recognized_in_warnings(self):
        requisition = PurchaseRequisition.objects.create(
            pr_number='PR-REGISTRATION-UUID', issued_by=self.issuer, po_applicable=True,
            approval_workflow_config=[self.stage(level=1, role='Level 1 Approver')],
        )
        response = self.client.get(f'{BASE}{requisition.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(any('Level 1 (' in warning for warning in response.data['registration_warnings']))

    def test_eligibility_warning_clears_after_employee_position_and_permission_are_corrected(self):
        response = self.create()
        self.assert_warning(response, 'business position')
        set_position(self.employee, 'Manager of Projects')
        grant_approval(self.employee, 'procurement_requisitions')

        detail = self.client.get(f"{BASE}{response.data['id']}/")

        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertFalse(any('business position' in warning for warning in detail.data['registration_warnings']))

    def test_submitted_incomplete_route_can_be_completed_before_any_decision(self):
        for index, route in enumerate(([], [self.stage(role='', user_id='')])):
            with self.subTest(route=route):
                response = self.create(pr_number=f'PR-REGISTRATION-REPAIR-{index}', approval_workflow_config=route)
                url = f"{BASE}{response.data['id']}/"
                submitted = self.client.post(url + 'submit/', {}, format='json')
                self.assertEqual(submitted.status_code, 200, submitted.data)
                repaired = self.client.patch(url, {'approval_workflow_config': [
                    self.stage(level=1, role='Level 1 Approver'),
                ]}, format='json')
                self.assertEqual(repaired.status_code, 200, repaired.data)
                self.assertEqual(repaired.data['status'], 'submitted')
                self.assertEqual(repaired.data['approval_workflow_config'][0]['level'], 1)
                self.assertEqual(repaired.data['approval_workflow_config'][0]['user_id'], str(self.employee.pk))

    def test_editing_duplicate_assignments_never_copies_one_decision_into_both_stages(self):
        duplicate = self.stage(level=1, role='Level 1 Approver')
        response = self.create(approval_workflow_config=[duplicate, duplicate])
        url = f"{BASE}{response.data['id']}/"
        submitted = self.client.post(url + 'submit/', {}, format='json')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.client.force_authenticate(self.employee)
        decision = self.client.post(url + 'process_dynamic_approval/', {}, format='json')
        self.assertEqual(decision.status_code, 200, decision.data)
        self.assertEqual(
            [stage['status'] for stage in decision.data['approval_workflow_config']],
            ['approved', 'pending'],
        )

        self.client.force_authenticate(self.issuer)
        edited = self.client.patch(url, {'approval_workflow_config': [duplicate, duplicate]}, format='json')

        self.assertEqual(edited.status_code, 200, edited.data)
        self.assertEqual(edited.data['status'], 'in_review')
        stages = edited.data['approval_workflow_config']
        self.assertEqual([stage['status'] for stage in stages], ['approved', 'pending'])
        self.assertEqual(stages[0]['approved_by_id'], str(self.employee.pk))
        self.assertNotIn('approved_by_id', stages[1])
