"""Completed Jarmo approvals use his profile artwork without changing evidence."""

import base64
from datetime import date, datetime, timezone
from io import BytesIO
from unittest.mock import patch

import pymupdf
from django.contrib.auth import get_user_model
from django.test import TestCase
from docx import Document
from PIL import Image

from apps.procurement.models import PurchaseOrder, Vendor
from apps.procurement.services.purchase_order_approval_artwork import (
    APPROVAL_STAMP_PATH, completed_jarmo_profile_artwork,
)
from apps.procurement.services.purchase_order_exports import build_purchase_order_docx, build_purchase_order_pdf
from apps.rbac.models import Organization, UserProfile


PROFILE_SIZE = (193, 61)
SNAPSHOT_SIZE = (177, 49)


def signature_image(size, color):
    output = BytesIO()
    Image.new('RGB', size, color).save(output, format='PNG')
    content = output.getvalue()
    return content, 'data:image/png;base64,' + base64.b64encode(content).decode()


class PurchaseOrderProfileArtworkTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(code='PROFILE-ARTWORK', name='Profile artwork tests')
        self.signature_bytes, self.signature = signature_image(PROFILE_SIZE, (20, 45, 160))
        self.jarmo, self.profile = self.make_profile('profile-artwork-jarmo', email='jarmo.suominen@rejlers.ae')
        vendor = Vendor.objects.create(vendor_code='PROFILE-ARTWORK', name='Synthetic supplier')
        self.order = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-0911_2026', vendor=vendor, title='Profile artwork regression',
            total_amount=100, status='completed', approved_by_name='Jarmo Suominen',
            approved_by_title='Chief Executive Officer', approved_date=date(2026, 9, 12),
        )
        self.stamp_bytes = APPROVAL_STAMP_PATH.read_bytes()
        with Image.open(BytesIO(self.stamp_bytes)) as image:
            self.stamp_size = image.size

    def make_profile(self, username, *, first_name='Jarmo', last_name='Suominen', email=None):
        user = get_user_model().objects.create_user(
            username=username, email=email or f'{username}@example.test', first_name=first_name, last_name=last_name,
        )
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': self.organization})
        profile.signature_image = self.signature
        profile.save(update_fields=['signature_image'])
        return user, profile

    def assert_profile_artwork_absent(self):
        self.assertEqual(completed_jarmo_profile_artwork(self.order), (None, None))

    def pdf_images(self, content):
        with pymupdf.open(stream=content, filetype='pdf') as pdf:
            return {entry[2:4]: pymupdf.Pixmap(pdf, entry[0]).samples for entry in pdf[0].get_images()}

    def word_images(self, content):
        document = Document(BytesIO(content))
        return [part.blob for part in document.part.package.parts if part.content_type.startswith('image/')]

    def assert_exported_profile_artwork(self):
        content, warnings = build_purchase_order_pdf(self.order)
        self.assertFalse(warnings)
        images = self.pdf_images(content)
        self.assertIn(PROFILE_SIZE, images)
        with Image.open(BytesIO(self.signature_bytes)) as signature:
            self.assertEqual(images[PROFILE_SIZE], signature.convert('RGB').tobytes())
        self.assertIn(self.stamp_size, images)
        word = self.word_images(build_purchase_order_docx(self.order))
        self.assertIn(self.signature_bytes, word)
        self.assertIn(self.stamp_bytes, word)

    def test_completed_recorded_jarmo_approval_resolves_actual_active_profile_without_writes(self):
        before = (PurchaseOrder.objects.values().get(pk=self.order.pk), UserProfile.objects.values().get(pk=self.profile.pk))
        # Imported legacy approvals may have the reviewed name/date without a
        # user FK or a saved signature snapshot.
        self.assertIsNone(self.order.approved_by_id)
        self.assertEqual(self.order.approval_signature, '')
        signature, stamp = completed_jarmo_profile_artwork(self.order)
        self.assertEqual(signature.getvalue(), self.signature_bytes)
        self.assertEqual(stamp.getvalue(), self.stamp_bytes)
        self.assert_exported_profile_artwork()
        self.assertEqual(before, (PurchaseOrder.objects.values().get(pk=self.order.pk), UserProfile.objects.values().get(pk=self.profile.pk)))

    def test_completed_jarmo_pdf_references_render_profile_signature_and_canonical_seal(self):
        self.order.approval_signature = 'https://source.example.invalid/signed-po.pdf#page=1'
        self.order.approval_stamp = self.order.approval_signature
        self.order.approval_log = [{
            'stage': 'Signed PO document approval', 'evidence_document_id': 'source-document',
            'approver': 'Jarmo Suominen', 'status': 'Approved',
            'signature_verified': True, 'approval_evidence_complete': True,
            'stamp_verified': False,
        }]
        with patch('apps.procurement.services.purchase_order_exports.source_approval_artwork') as source_crop:
            self.assert_exported_profile_artwork()
        source_crop.assert_not_called()

    def test_profile_artwork_takes_priority_over_an_older_matching_recorded_snapshot(self):
        _old_bytes, old_signature = signature_image(SNAPSHOT_SIZE, (100, 25, 50))
        self.order.approved_by = self.jarmo
        self.order.approval_signature = old_signature
        self.order.approval_log = [{
            'level': 5, 'status': 'Approved', 'user_id': str(self.jarmo.pk),
            'approved_by_id': str(self.jarmo.pk), 'signature_user_id': str(self.jarmo.pk),
            'signature': old_signature,
        }]
        self.assert_exported_profile_artwork()
        content, _ = build_purchase_order_pdf(self.order)
        self.assertNotIn(SNAPSHOT_SIZE, self.pdf_images(content))
        self.assertEqual(self.order.approval_signature, old_signature)

    def test_other_signers_and_noncompleted_orders_do_not_receive_jarmo_profile_artwork(self):
        for status, name in (('completed', 'Another Approver'), ('draft', 'Jarmo Suominen'),
                             ('pending_approval', 'Jarmo Suominen'), ('sent', 'Jarmo Suominen')):
            with self.subTest(status=status, name=name):
                self.order.status, self.order.approved_by_name = status, name
                self.assert_profile_artwork_absent()

    def test_missing_recorded_name_or_approval_date_does_not_create_a_signed_approval(self):
        for name, approved_date in (('', date(2026, 9, 12)), ('Jarmo Suominen', None)):
            with self.subTest(name=name, date=approved_date):
                self.order.approved_by_name, self.order.approved_date = name, approved_date
                self.assert_profile_artwork_absent()

    def test_inactive_deleted_or_suspended_profiles_are_not_used(self):
        for changes in ({'status': 'inactive'}, {'status': 'suspended'}, {'is_deleted': True}):
            with self.subTest(changes=changes):
                UserProfile.objects.filter(pk=self.profile.pk).update(**{'status': 'active', 'is_deleted': False, **changes})
                self.assert_profile_artwork_absent()
        UserProfile.objects.filter(pk=self.profile.pk).update(status='active', is_deleted=False)
        get_user_model().objects.filter(pk=self.jarmo.pk).update(is_active=False)
        self.assert_profile_artwork_absent()

    def test_ambiguous_canonical_email_accounts_are_not_selected_arbitrarily(self):
        self.make_profile('profile-artwork-other-jarmo', email='JARMO.SUOMINEN@REJLERS.AE')
        self.assert_profile_artwork_absent()

    def test_namesake_account_does_not_replace_the_exact_corporate_profile(self):
        self.make_profile('profile-artwork-namesake')
        signature, _stamp = completed_jarmo_profile_artwork(self.order)
        self.assertEqual(signature.getvalue(), self.signature_bytes)
        get_user_model().objects.filter(pk=self.jarmo.pk).update(email='former-account@example.test')
        self.assert_profile_artwork_absent()

    def test_recorded_timestamp_and_normalized_name_are_supported_without_inventing_a_date(self):
        self.order.approved_by_name = '  JARMO   Suominen  '
        self.order.approved_date = None
        self.order.approved_at = datetime(2026, 9, 12, 10, 30, tzinfo=timezone.utc)
        signature, stamp = completed_jarmo_profile_artwork(self.order)
        self.assertEqual(signature.getvalue(), self.signature_bytes)
        self.assertEqual(stamp.getvalue(), self.stamp_bytes)
        self.assertIsNone(self.order.approved_date)

    def test_conflicting_recorded_approver_id_does_not_receive_jarmo_signature(self):
        other, _ = self.make_profile('profile-artwork-other', first_name='Other', last_name='Approver')
        self.order.approved_by = other
        self.assert_profile_artwork_absent()

    def test_conflicting_internal_signer_metadata_does_not_receive_profile_artwork(self):
        other, _ = self.make_profile('profile-artwork-other', first_name='Other', last_name='Approver')
        self.order.approved_by = self.jarmo
        self.order.approval_log = [{
            'level': 5, 'status': 'Approved', 'user_id': str(self.jarmo.pk),
            'approved_by_id': str(other.pk), 'signature_user_id': str(other.pk),
        }]
        self.assert_profile_artwork_absent()

    def test_incomplete_internal_approval_sequence_is_not_hidden_by_a_missing_snapshot(self):
        self.order.approved_by = self.jarmo
        self.order.approval_log = [{
            'level': 5, 'status': 'Pending', 'user_id': str(self.jarmo.pk),
        }]
        self.assertEqual(self.order.approval_signature, '')
        self.assert_profile_artwork_absent()

    def test_explicit_unverified_source_approval_does_not_receive_profile_artwork(self):
        self.order.approval_signature = 'https://source.example.invalid/signed-po.pdf#page=1'
        self.order.approval_stamp = self.order.approval_signature
        self.order.approval_log = [{
            'stage': 'Signed PO document approval', 'evidence_document_id': 'source-document',
            'approver': 'Jarmo Suominen', 'status': 'Evidence review required',
            'signature_verified': False, 'stamp_verified': True,
        }]
        self.assert_profile_artwork_absent()

    def test_absent_or_invalid_profile_image_preserves_valid_recorded_signature_fallback(self):
        saved_bytes, saved_signature = signature_image(SNAPSHOT_SIZE, (100, 25, 50))
        self.order.approved_by = self.jarmo
        self.order.approval_signature = saved_signature
        self.order.approval_log = [{
            'level': 5, 'status': 'Approved', 'user_id': str(self.jarmo.pk),
            'approved_by_id': str(self.jarmo.pk), 'signature_user_id': str(self.jarmo.pk),
            'signature': saved_signature,
        }]
        for value in ('', 'data:image/png;base64,not-an-image', 'https://external.example.invalid/signature.png'):
            with self.subTest(value=value):
                UserProfile.objects.filter(pk=self.profile.pk).update(signature_image=value)
                self.assert_profile_artwork_absent()
                with patch('urllib.request.urlopen') as remote_fetch:
                    content, _ = build_purchase_order_pdf(self.order)
                    images = self.pdf_images(content)
                    self.assertIn(SNAPSHOT_SIZE, images)
                    self.assertNotIn(PROFILE_SIZE, images)
                    self.assertIn(saved_bytes, self.word_images(build_purchase_order_docx(self.order)))
                remote_fetch.assert_not_called()

    def test_current_profile_image_is_loaded_without_using_a_stale_related_profile_cache(self):
        self.assertEqual(self.jarmo.rbac_profile.signature_image, self.signature)
        fresh_bytes, fresh_signature = signature_image((197, 63), (40, 80, 120))
        UserProfile.objects.filter(pk=self.profile.pk).update(signature_image=fresh_signature)
        self.order.approved_by = self.jarmo
        signature, stamp = completed_jarmo_profile_artwork(self.order)
        self.assertEqual(signature.getvalue(), fresh_bytes)
        self.assertEqual(stamp.getvalue(), self.stamp_bytes)
