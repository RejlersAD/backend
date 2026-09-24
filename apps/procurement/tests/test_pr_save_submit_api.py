"""Saving PR drafts never requests approval; explicit submission does."""

from copy import deepcopy
from decimal import Decimal
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.notifications.models import Notification
from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.procurement.views import PurchaseRequisitionViewSet
from apps.rbac.models import Module, Permission, Role, RoleModule, RolePermission, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

from .approval_fixtures import grant_approval, set_position


router = DefaultRouter()
router.register('requisitions', PurchaseRequisitionViewSet, basename='save-submit-requisitions')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/requisitions/'


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='https://teams.example.test/test-only',
                   WEB_PUSH_VAPID_PRIVATE_KEY='')
class PurchaseRequisitionSaveSubmitAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.tasks = {}
        for key, target in {
            'teams': 'apps.notifications.teams.send_teams_approval_assignment.delay',
            'email': 'apps.notifications.services.send_notification_email.delay',
            'push': 'apps.notifications.services.send_web_push_notification.delay',
        }.items():
            patcher = patch(target)
            self.tasks[key] = patcher.start()
            self.addCleanup(patcher.stop)
        users = get_user_model()
        self.issuer = users.objects.create_user('save-issuer', email='issuer@save.example.test')
        self.procurement = users.objects.create_user('save-procurement', email='procurement@save.example.test')
        self.alternate = users.objects.create_user('save-alternate', email='alternate@save.example.test')
        self.engineer = users.objects.create_user('save-engineer', email='engineer@save.example.test')
        for user, title in ((self.issuer, 'Engineer'), (self.procurement, 'Procurement Manager'),
                            (self.alternate, 'Procurement Manager'), (self.engineer, 'Engineer')):
            grant_approval(user, 'procurement_requisitions')
            set_position(user, title)
            profile = user.rbac_profile
            profile.signature_image = 'test-only-save-submit-signature'
            profile.save(update_fields=['signature_image'])
        module = Module.objects.get(code='procurement_requisitions')
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        self.editor_role = Role.objects.create(code='save-submit-editor', name='Save-submit editor', level=3)
        RoleModule.objects.create(role=self.editor_role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'create', 'update']):
            RolePermission.objects.create(role=self.editor_role, permission=permission)
        UserRole.objects.create(user_profile=self.issuer.rbac_profile, role=self.editor_role)
        self.client = APIClient()
        self.client.force_authenticate(self.issuer)
        self.workflow = [
            {'level': 0, 'role': 'Procurement Department', 'user_id': str(self.procurement.pk)},
            {'level': 1, 'role': 'Level 1 Approver', 'user_id': str(self.engineer.pk)},
        ]
        self.original_notification_ids = set(Notification.objects.values_list('pk', flat=True))
        for task in self.tasks.values():
            task.reset_mock()

    def save_new(self, **values):
        payload = {'pr_number': 'RAD-PRJ-PR-0530_2026', 'po_applicable': True,
                   'requisition_type': 'general', 'product_service': 'Saved engineering scope',
                   'total_price': '100.00', 'net_total_excl_vat': '100.00',
                   'approval_workflow_config': self.workflow}
        payload.update(values)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(BASE, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return PurchaseRequisition.objects.get(pk=response.data['id'])

    def request(self, method, pr, payload=None, action=''):
        with self.captureOnCommitCallbacks(execute=True):
            return getattr(self.client, method)(f'{BASE}{pr.pk}/{action}', payload or {}, format='json')

    def assert_no_new_notifications(self):
        # Include generic created/updated events, not only approval metadata.
        self.assertEqual(set(Notification.objects.values_list('pk', flat=True)), self.original_notification_ids)
        for task in self.tasks.values():
            task.assert_not_called()

    def notices(self, pr):
        return Notification.objects.filter(metadata__pr_id=str(pr.pk), metadata__event_type='approval_assignment')

    def test_create_and_repeated_draft_saves_store_route_without_notifying_anyone(self):
        pr = self.save_new()
        self.assertEqual(pr.status, 'draft')
        self.assert_no_new_notifications()
        for index in range(2):
            response = self.request('patch', pr, {'notes': f'Draft edit {index}', 'approval_workflow_config': self.workflow})
            self.assertEqual(response.status_code, 200, response.data)
            pr.refresh_from_db()
            self.assertEqual(pr.status, 'draft')
            self.assertFalse(RequisitionWorkflowService.can_approve(pr, self.procurement))
            self.assertFalse(RequisitionWorkflowService.can_approve(pr, self.engineer))
            self.assert_no_new_notifications()

    def test_save_cannot_implicitly_submit_with_status_payload(self):
        pr = self.save_new()
        response = self.request('patch', pr, {'status': 'submitted'})
        self.assertEqual(response.status_code, 400, response.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'draft')
        self.assert_no_new_notifications()

    def test_explicit_submission_notifies_level_zero_once_and_saves_do_not_resubmit(self):
        pr = self.save_new()
        submitted = self.request('post', pr, action='submit/')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'submitted')
        notice = self.notices(pr).get()
        self.assertEqual(notice.recipient_id, self.procurement.pk)
        self.assertEqual(notice.metadata['approval_level'], 0)
        self.assertTrue(notice.metadata['requires_action'])
        self.tasks['teams'].assert_called_once()
        for task in self.tasks.values():
            task.reset_mock()
        for _ in range(2):
            response = self.request('patch', pr, {'notes': 'Saved after submission', 'approval_workflow_config': self.workflow})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(self.notices(pr).get().pk, notice.pk)
        repeated = self.request('post', pr, action='submit/')
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(self.notices(pr).get().pk, notice.pk)
        for task in self.tasks.values():
            task.assert_not_called()

    def test_draft_reassignment_stays_silent_until_explicit_submit(self):
        pr = self.save_new()
        changed = deepcopy(self.workflow)
        changed[0]['user_id'] = str(self.alternate.pk)
        response = self.request('patch', pr, {'approval_workflow_config': changed})
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_no_new_notifications()
        response = self.request('post', pr, action='submit/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.notices(pr).get().recipient_id, self.alternate.pk)
        self.tasks['teams'].assert_called_once()

    def test_decision_still_notifies_next_level_after_explicit_submission(self):
        pr = self.save_new()
        self.assertEqual(self.request('post', pr, action='submit/').status_code, 200)
        self.tasks['teams'].reset_mock()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.procurement.pk))
        approved = self.request('post', pr, action='process_dynamic_approval/')
        self.assertEqual(approved.status_code, 200, approved.data)
        notice = self.notices(pr).get(recipient=self.engineer)
        self.assertEqual(notice.metadata['approval_level'], 1)
        self.assertEqual(self.notices(pr).count(), 2)
        self.tasks['teams'].assert_called_once()

    def test_failed_explicit_submit_rolls_back_optional_route_change(self):
        pr = self.save_new()
        original = deepcopy(pr.approval_workflow_config)
        # A stored header/line mismatch must reject submission after its
        # optional route payload has validated, without saving half the action.
        pr.items = [{'description': 'Two items', 'quantity': '2', 'unit_price': '100', 'total': '200'}]
        pr.save(update_fields=['items'])
        changed = deepcopy(self.workflow)
        changed[0]['user_id'] = str(self.alternate.pk)
        response = self.request('post', pr, {'approval_workflow_config': changed}, action='submit/')
        self.assertEqual(response.status_code, 400, response.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'draft')
        self.assertEqual(pr.approval_workflow_config, original)
        self.assert_no_new_notifications()

    def test_other_editor_cannot_explicitly_submit_issuers_draft(self):
        pr = self.save_new()
        UserRole.objects.create(user_profile=self.engineer.rbac_profile, role=self.editor_role)
        self.client.force_authenticate(self.engineer)
        response = self.request('post', pr, action='submit/')
        self.assertEqual(response.status_code, 403, response.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'draft')
        self.assert_no_new_notifications()

    def test_new_and_saved_unconfirmed_pr_prices_need_no_vat_choice_or_approval_notice(self):
        pr = self.save_new()
        response = self.request('patch', pr, {'total_price': '125.50', 'net_total_excl_vat': '125.50'})
        self.assertEqual(response.status_code, 200, response.data)
        pr.refresh_from_db()
        self.assertEqual((pr.total_price, pr.net_total_excl_vat), (Decimal('125.50'), Decimal('125.50')))
        self.assertEqual(pr.vat_basis, 'unconfirmed')
        self.assertEqual(pr.status, 'draft')
        self.assert_no_new_notifications()

    def test_json_and_multipart_line_prices_save_and_submit_without_a_vat_choice(self):
        for index, request_format in enumerate(('json', 'multipart')):
            with self.subTest(request_format=request_format):
                items = [{'description': 'Quoted scope', 'quantity': '2', 'unit_price': '50', 'total': '100'}]
                payload = {
                    'pr_number': f'RAD-PRJ-PR-053{index + 1}_2026',
                    'po_applicable': True, 'requisition_type': 'general',
                    'product_service': 'Untaxed quoted scope',
                    'total_price': '100.00', 'net_total_excl_vat': '100.00',
                    'items': items, 'approval_workflow_config': self.workflow,
                }
                if request_format == 'multipart':
                    payload['items'] = json.dumps(items)
                    payload['approval_workflow_config'] = json.dumps(self.workflow)
                with self.captureOnCommitCallbacks(execute=True):
                    created = self.client.post(BASE, payload, format=request_format)
                self.assertEqual(created.status_code, 201, created.data)
                pr = PurchaseRequisition.objects.get(pk=created.data['id'])
                self.assertEqual((pr.total_price, pr.net_total_excl_vat), (Decimal('100'), Decimal('100')))
                self.assertEqual(pr.vat_basis, 'unconfirmed')

                items[0].update(unit_price='62.75', total='125.50')
                updated_payload = {
                    'items': json.dumps(items) if request_format == 'multipart' else items,
                    'total_price': '125.50', 'net_total_excl_vat': '125.50',
                }
                with self.captureOnCommitCallbacks(execute=True):
                    saved = self.client.patch(f'{BASE}{pr.pk}/', updated_payload, format=request_format)
                self.assertEqual(saved.status_code, 200, saved.data)
                pr.refresh_from_db()
                self.assertEqual((pr.total_price, pr.net_total_excl_vat), (Decimal('125.50'), Decimal('125.50')))
                self.assertEqual(pr.vat_basis, 'unconfirmed')
                self.assertEqual(pr.status, 'draft')
                self.assertFalse(self.notices(pr).exists())

                submitted = self.request('post', pr, action='submit/')
                self.assertEqual(submitted.status_code, 200, submitted.data)
                pr.refresh_from_db()
                self.assertEqual(pr.status, 'submitted')
                self.assertEqual(pr.vat_basis, 'unconfirmed')
                self.assertEqual((pr.total_price, pr.net_total_excl_vat), (Decimal('125.50'), Decimal('125.50')))
                self.assertEqual(self.notices(pr).count(), 1)

    def test_timestamp_precondition_allows_current_edits_and_rejects_stale_json_and_multipart(self):
        pr = self.save_new()
        for request_format in ('json', 'multipart'):
            with self.subTest(request_format=request_format):
                pr.refresh_from_db()
                token = pr.updated_at.isoformat()
                response = self.client.patch(f'{BASE}{pr.pk}/', {
                    'notes': f'Current {request_format} edit', 'expected_updated_at': token,
                }, format=request_format)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertNotIn('expected_updated_at', response.data)
                pr.refresh_from_db()
                saved_timestamp = pr.updated_at
                self.assertNotEqual(saved_timestamp.isoformat(), token)

                stale = self.client.patch(f'{BASE}{pr.pk}/', {
                    'notes': 'Stale edit must not win', 'expected_updated_at': token,
                }, format=request_format)
                self.assertEqual(stale.status_code, 409, stale.data)
                self.assertEqual(stale.data['code'], 'stale_requisition')
                pr.refresh_from_db()
                self.assertEqual(pr.notes, f'Current {request_format} edit')
                self.assertEqual(pr.updated_at, saved_timestamp)
        self.assert_no_new_notifications()

    def test_precondition_is_checked_against_locked_record_after_serializer_validation(self):
        from rest_framework.test import APIRequestFactory
        from apps.procurement.serializers import PurchaseRequisitionSerializer
        from apps.procurement.services.requisition_concurrency import StaleRequisition

        pr = self.save_new()
        request = APIRequestFactory().patch('/')
        request.user = self.issuer
        serializer = PurchaseRequisitionSerializer(pr, data={
            'notes': 'Old validated form', 'expected_updated_at': pr.updated_at.isoformat(),
        }, partial=True, context={'request': request})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        newer = PurchaseRequisition.objects.get(pk=pr.pk)
        newer.notes = 'Concurrent saved edit'
        newer.save()
        with self.assertRaises(StaleRequisition):
            serializer.save()
        newer.refresh_from_db()
        self.assertEqual(newer.notes, 'Concurrent saved edit')
        self.assert_no_new_notifications()

    def test_matching_timestamp_can_submit_and_returns_the_next_token(self):
        pr = self.save_new()
        token = pr.updated_at.isoformat()
        submitted = self.request('post', pr, {'expected_updated_at': token}, action='submit/')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(submitted.data['status'], 'submitted')
        self.assertNotIn('expected_updated_at', submitted.data)
        pr.refresh_from_db()
        self.assertNotEqual(pr.updated_at.isoformat(), token)
        self.assertEqual(self.notices(pr).count(), 1)
        self.tasks['teams'].assert_called_once()

    def test_stale_submission_cannot_change_route_or_start_notifications(self):
        pr = self.save_new()
        token = pr.updated_at.isoformat()
        pr.notes = 'New draft content'
        pr.save()
        original_workflow = deepcopy(pr.approval_workflow_config)
        current_timestamp = pr.updated_at
        changed = deepcopy(self.workflow)
        changed[0]['user_id'] = str(self.alternate.pk)
        response = self.request('post', pr, {
            'expected_updated_at': token, 'approval_workflow_config': changed,
        }, action='submit/')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'stale_requisition')
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'draft')
        self.assertEqual(pr.notes, 'New draft content')
        self.assertEqual(pr.approval_workflow_config, original_workflow)
        self.assertEqual(pr.updated_at, current_timestamp)
        self.assert_no_new_notifications()

    def test_invalid_preconditions_fail_without_edits_or_submission(self):
        pr = self.save_new()
        timestamp = pr.updated_at
        for request_format in ('json', 'multipart'):
            tokens = ('not-a-timestamp', '') if request_format == 'multipart' else ('not-a-timestamp', '', None)
            for token in tokens:
                for method, suffix in (('patch', ''), ('post', 'submit/')):
                    with self.subTest(request_format=request_format, token=token, method=method):
                        response = getattr(self.client, method)(f'{BASE}{pr.pk}/{suffix}', {
                            'notes': 'Invalid precondition', 'expected_updated_at': token,
                        }, format=request_format)
                        self.assertEqual(response.status_code, 400, response.data)
                        self.assertIn('expected_updated_at', response.data)
                        pr.refresh_from_db()
                        self.assertEqual(pr.status, 'draft')
                        self.assertEqual(pr.notes, '')
                        self.assertEqual(pr.updated_at, timestamp)
        self.assert_no_new_notifications()

    def test_stale_token_does_not_bypass_submission_issuer_check(self):
        pr = self.save_new()
        token = pr.updated_at.isoformat()
        pr.save()
        UserRole.objects.create(user_profile=self.engineer.rbac_profile, role=self.editor_role)
        self.client.force_authenticate(self.engineer)
        response = self.request('post', pr, {'expected_updated_at': token}, action='submit/')
        self.assertEqual(response.status_code, 403, response.data)
        self.assert_no_new_notifications()

    def test_creation_rejects_a_precondition_for_a_nonexistent_record(self):
        response = self.client.post(BASE, {
            'pr_number': 'RAD-PRJ-PR-0599_2026',
            'expected_updated_at': '2026-09-24T10:00:00Z',
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('expected_updated_at', response.data)
        self.assertFalse(PurchaseRequisition.objects.filter(pr_number='RAD-PRJ-PR-0599_2026').exists())
