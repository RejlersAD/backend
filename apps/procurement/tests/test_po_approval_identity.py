"""PO decisions belong to the assigned employee and that employee's signature."""

from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.procurement.models import PurchaseOrder, Vendor
from apps.procurement.services.purchase_order_approval_artwork import DEFAULT_APPROVAL_STAMP_REFERENCE
from apps.procurement.services.purchase_order_approvals import (
    _active_entries, _entry_matches_user, _resolve_entry_user,
    can_approve, normalize_assignments, notify_assigned_approvers, pending_entries_for, record_decision,
)
from apps.rbac.models import Organization, UserProfile
from .approval_fixtures import grant_approval, set_position


SERVICE = 'apps.procurement.services.purchase_order_approvals'


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class PurchaseOrderApprovalIdentityTests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name='PO signer identity', code='PO-SIGNER')
        self.people = {}
        self.profiles = {}
        for index, name in enumerate(('Richa', 'ReviewerOne', 'ReviewerTwo', 'ReviewerThree', 'ReviewerFour', 'CEO', 'Administrator')):
            user = get_user_model().objects.create_user(
                username=f'po-signer-{index}', email=f'po-signer-{index}@example.test',
                first_name=name, is_superuser=index >= 5,
            )
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            grant_approval(user)
            profile.signature_image = f'data:image/png;base64,signature-{name}'
            profile.job_title = f'{name} Job Title'
            profile.save(update_fields=['signature_image', 'job_title'])
            set_position(user, 'CEO' if index == 5 else 'Engineer')
            self.people[index] = user
            self.profiles[index] = profile
        vendor = Vendor.objects.create(vendor_code='PO-SIGNER', name='PO signer vendor')
        self.order = PurchaseOrder.objects.create(
            po_number='PO-SIGNER', vendor=vendor, title='Signature identity checks',
            total_amount='100.00', created_by=self.people[0],
            approval_log=[self.stage(index) for index in (5, 0, 4, 1, 3, 2)],
        )
        notification_patch = patch(f'{SERVICE}.notify_assigned_approvers')
        self.notify_next = notification_patch.start()
        self.addCleanup(notification_patch.stop)

    def stage(self, index, **kwargs):
        return {
            'stage': f'Level {index}', 'level': index, 'user_id': str(self.people[index].pk),
            'approver_email': self.people[index].email, 'approver': self.people[index].first_name,
            'status': 'Pending', **kwargs,
            'business_position': 'ceo' if index == 5 else 'engineer',
        }

    def approve(self, index, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            self.order, entry = record_decision(
                self.order, self.people[index], 'approve', require_signature=True, **kwargs,
            )
        return entry

    def test_superuser_ceo_cannot_approve_before_levels_zero_through_four(self):
        original = deepcopy(self.order.approval_log)
        for index in (5, 6):
            with self.subTest(actor=index), self.assertRaises(PermissionDenied):
                self.approve(index)
        self.order.refresh_from_db()
        self.assertEqual(self.order.approval_log, original)
        self.assertIsNone(self.order.approved_at)
        self.notify_next.assert_not_called()

        for index in range(5):
            entry = self.approve(index)
            self.assertEqual(entry['approved_by_id'], str(self.people[index].pk))
            self.assertEqual(entry['signature_user_email'], self.people[index].email)
            self.assertEqual(self.order.approval_stamp, '')
            self.assertEqual(self.notify_next.call_count, index + 1)
            self.assertEqual(self.notify_next.call_args.kwargs['previous_level'], index)
            with self.assertRaises(PermissionDenied):
                self.approve(6)
            if index < 4:
                with self.assertRaises(PermissionDenied):
                    self.approve(5)

        final = self.approve(5)
        self.assertEqual(self.notify_next.call_count, 5)
        self.assertEqual(self.order.approved_by_id, self.people[5].pk)
        self.assertEqual(self.order.approved_by_name, 'CEO')
        self.assertEqual(self.order.approved_by_title, UserProfile.objects.get(user=self.people[5]).job_title)
        self.assertEqual(self.order.approval_signature, self.profiles[5].signature_image)
        self.assertEqual(self.order.approval_stamp, DEFAULT_APPROVAL_STAMP_REFERENCE)
        richa = next(stage for stage in self.order.approval_log if stage['level'] == 0)
        self.assertEqual(richa['approved_by_name'], 'Richa')
        self.assertEqual(richa['signature'], self.profiles[0].signature_image)
        self.assertNotEqual(richa['signature'], final['signature'])

    def test_signature_is_freshly_loaded_from_the_actual_actors_profile(self):
        # Populate the request user's relation cache before its saved signature changes.
        self.assertEqual(self.people[0].rbac_profile.signature_image, self.profiles[0].signature_image)
        actual_signature = 'data:image/png;base64,new-canonical-signature'
        UserProfile.objects.filter(pk=self.profiles[0].pk).update(signature_image=actual_signature)
        self.order.approval_log[1].update(
            signature=self.profiles[5].signature_image,
            approved_by_id=str(self.people[5].pk), approved_by_email=self.people[5].email,
        )
        self.order.save(update_fields=['approval_log'])

        entry = self.approve(0)

        self.assertEqual(entry['signature'], actual_signature)
        self.assertEqual(entry['signature_user_id'], str(self.people[0].pk))
        self.assertEqual(entry['approved_by_id'], str(self.people[0].pk))
        self.assertEqual(entry['decided_by_email'], self.people[0].email)
        self.assertEqual(entry['approver'], 'Richa')
        self.assertIsNone(self.order.approved_at)

    def test_missing_signature_or_inactive_employee_cannot_approve(self):
        for changes in ({'signature_image': ' '}, {'status': 'suspended'}, {'is_deleted': True}):
            with self.subTest(changes=changes):
                UserProfile.objects.filter(pk=self.profiles[0].pk).update(
                    signature_image=self.profiles[0].signature_image, status='active', is_deleted=False,
                )
                UserProfile.objects.filter(pk=self.profiles[0].pk).update(**changes)
                with self.assertRaises((PermissionDenied, ValidationError)):
                    self.approve(0)
        UserProfile.objects.filter(pk=self.profiles[0].pk).update(status='active', is_deleted=False)
        get_user_model().objects.filter(pk=self.people[0].pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.approve(0)
        self.order.refresh_from_db()
        self.assertTrue(all(stage['status'] == 'Pending' for stage in self.order.approval_log))
        self.notify_next.assert_not_called()

    def test_migrated_ids_use_assigned_email_and_record_the_actual_signer(self):
        self.order.approval_log[1]['user_id'] = str(self.people[5].pk)
        self.order.save(update_fields=['approval_log'])
        with self.assertRaises(PermissionDenied):
            self.approve(5, stage='Level 0')

        entry = self.approve(0)

        self.assertEqual(entry['user_id'], str(self.people[0].pk))
        self.assertEqual(entry['approved_by_email'], self.people[0].email)
        self.assertEqual(entry['signature'], self.profiles[0].signature_image)

    def test_assigned_email_never_falls_back_to_a_different_or_blank_email_user(self):
        stage = self.stage(0, approver_email='missing-person@example.test')
        self.assertIsNone(_resolve_entry_user(stage))
        self.assertFalse(_entry_matches_user(stage, self.people[0]))
        self.people[0].email = ''
        self.assertFalse(_entry_matches_user(self.stage(1, user_id=str(self.people[0].pk)), self.people[0]))

    def test_unknown_lower_status_does_not_skip_forward(self):
        for lower_status in ('not_recorded', 'Submitted', 'unknown'):
            with self.subTest(lower_status=lower_status):
                self.order.approval_log = [self.stage(0, status=lower_status), self.stage(5)]
                self.order.save(update_fields=['approval_log'])
                self.assertEqual(_active_entries(self.order.approval_log), [])
                with self.assertRaises(PermissionDenied):
                    self.approve(5)
        self.notify_next.assert_not_called()

    def test_unknown_peer_status_blocks_the_entire_active_level(self):
        for peer_status in ('not_recorded', 'Submitted', 'unknown'):
            with self.subTest(peer_status=peer_status):
                self.order.approval_log = [self.stage(0), self.stage(1, level=0, status=peer_status)]
                self.order.save(update_fields=['approval_log'])
                self.assertEqual(_active_entries(self.order.approval_log), [])
                self.assertFalse(can_approve(self.order, self.people[0]))
                with self.assertRaises(PermissionDenied):
                    self.approve(0)
                with patch('apps.notifications.services.NotificationService.create_notification') as create:
                    notify_assigned_approvers(self.order)
                create.assert_not_called()
        self.notify_next.assert_not_called()

    def test_email_only_later_assignment_prevents_early_final_approval(self):
        later = self.stage(1)
        later.pop('user_id')
        self.order.approval_log = [self.stage(0), later]
        self.order.save(update_fields=['approval_log'])

        self.approve(0)

        self.assertIsNone(self.order.approved_by_id)
        self.assertIsNone(self.order.approved_at)
        self.assertEqual(self.order.approval_signature, '')
        self.assertEqual(self.order.approved_by_name, '')
        self.assertTrue(can_approve(self.order, self.people[1]))
        self.approve(1)
        self.assertEqual(self.order.approved_by_id, self.people[1].pk)
        self.assertEqual(self.order.approval_signature, self.profiles[1].signature_image)

    def test_approved_source_evidence_does_not_require_an_internal_user_id(self):
        source = {
            'stage': 'Reviewed source approval', 'level': 0, 'status': 'Approved',
            'evidence_document_id': 'reviewed-original', 'signature_verified': True,
        }
        self.order.approval_log = [source, self.stage(1)]
        self.order.save(update_fields=['approval_log'])

        self.approve(1)

        self.assertEqual(self.order.approved_by_id, self.people[1].pk)
        self.assertEqual(self.order.approval_log[0], source)

    def test_known_wrong_signer_blocks_progression_and_notifications(self):
        self.order.approval_log = [
            self.stage(0, status='Approved', approved_by_id=str(self.people[5].pk),
                       approved_by_email=self.people[5].email, signature=self.profiles[5].signature_image),
            self.stage(5),
        ]
        self.order.save(update_fields=['approval_log'])
        with self.assertRaises(PermissionDenied):
            self.approve(5)
        # Call the imported real sender rather than the progression spy.
        with patch('apps.notifications.services.NotificationService.create_notification') as create:
            notify_assigned_approvers(self.order)
        create.assert_not_called()
        self.assertEqual(_active_entries(self.order.approval_log), [])

    def test_ordinary_assignment_save_cannot_replace_recorded_signer_provenance(self):
        approved = self.approve(0)
        payload = {**approved, 'signature': self.profiles[5].signature_image,
                   'approved_by_email': self.people[5].email, 'signature_user_email': self.people[5].email}
        normalized = normalize_assignments([payload], existing_log=[approved], require_core=False)
        self.assertEqual(normalized[0]['signature'], self.profiles[0].signature_image)
        self.assertEqual(normalized[0]['approved_by_email'], self.people[0].email)
        self.assertEqual(normalized[0]['signature_user_email'], self.people[0].email)
        self.assertEqual(normalized[0]['decided_by_id'], str(self.people[0].pk))

    def test_invalid_decision_does_not_become_a_rejection(self):
        with self.assertRaises(ValidationError):
            record_decision(self.order, self.people[0], 'anything-else')
        self.order.refresh_from_db()
        self.assertTrue(all(stage['status'] == 'Pending' for stage in self.order.approval_log))

    def test_completed_cancelled_and_unknown_orders_cannot_show_act_or_notify(self):
        for status in ('completed', 'cancelled', 'unknown'):
            with self.subTest(status=status):
                self.order.status = status
                self.order.save(update_fields=['status'])
                self.assertFalse(can_approve(self.order, self.people[0]))
                self.assertEqual(pending_entries_for(self.people[0], [self.order]), [])
                with self.assertRaises(ValidationError):
                    self.approve(0)
                with patch('apps.notifications.services.NotificationService.create_notification') as create:
                    notify_assigned_approvers(self.order)
                create.assert_not_called()

    def test_shared_eligibility_requires_active_assignee_and_profile(self):
        self.assertTrue(can_approve(self.order, self.people[0]))
        self.assertFalse(can_approve(self.order, self.people[5]))
        self.assertFalse(can_approve(self.order, self.people[6]))
        UserProfile.objects.filter(pk=self.profiles[0].pk).update(status='suspended')
        fresh_user = get_user_model().objects.get(pk=self.people[0].pk)
        self.assertFalse(can_approve(self.order, fresh_user))
        self.assertEqual(pending_entries_for(fresh_user, [self.order]), [])

    def test_final_title_and_signature_belong_to_actual_final_actor_not_stale_ceo(self):
        for title in ('Procurement Manager', ''):
            with self.subTest(title=title):
                UserProfile.objects.filter(pk=self.profiles[0].pk).update(job_title=title)
                self.order.approval_log = [self.stage(0)]
                self.order.approved_by = self.people[5]
                self.order.approved_by_name = 'CEO'
                self.order.approved_by_title = 'Chief Executive Officer'
                self.order.approval_signature = self.profiles[5].signature_image
                self.order.save(update_fields=[
                    'approval_log', 'approved_by', 'approved_by_name', 'approved_by_title', 'approval_signature',
                ])
                self.approve(0)
                self.assertEqual(self.order.approved_by, self.people[0])
                self.assertEqual(self.order.approved_by_name, 'Richa')
                self.assertEqual(self.order.approved_by_title, title)
                self.assertEqual(self.order.approval_signature, self.profiles[0].signature_image)
