"""Imported approval artwork is copied only from this PO's verified source."""

import hashlib
from datetime import date
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from docx import Document
from PIL import Image

from apps.procurement.models import PODocument, PurchaseOrder, Vendor
from apps.procurement.services.purchase_order_exports import build_purchase_order_docx, build_purchase_order_pdf
from apps.procurement.services.purchase_order_source_artwork import source_approval_artwork


SIGNER = 'Synthetic Buyer Approver'
APPROVAL_DATE = date(2026, 9, 12)


def source_pdf(*, scanned=False):
    """Realistic two-column approval cover with obviously distinct decoys."""
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=595, height=842)
        page.insert_text((48, 70), 'PURCHASE ORDER', fontsize=18)
        page.insert_text((48, 100), 'RAD-PRJ-PUR-0901_2026', fontsize=12)
        page.insert_text((48, 480), 'Approved by:', fontsize=12)
        page.insert_text((320, 480), 'Confirmation:', fontsize=12)
        page.draw_circle((172, 555), 38, color=(0, 0, 1), fill=(0, 0, 1))
        page.draw_rect(pymupdf.Rect(48, 520, 113, 535), color=(0, .65, 0), fill=(0, .65, 0))
        page.insert_text((48, 625), SIGNER, fontsize=12)
        page.insert_text((48, 645), 'Chief Executive Officer', fontsize=11)
        page.insert_text((48, 668), 'Date: 12.09.2026', fontsize=12)
        page.draw_rect(pymupdf.Rect(335, 515, 550, 695), color=(1, 0, 0), fill=(1, 0, 0))
        page.insert_text((345, 650), 'SELLER ONLY', fontsize=14)
        page.draw_rect(pymupdf.Rect(0, 792, 595, 842), color=(1, 0, 0), fill=(1, 0, 0))
        page.insert_text((48, 815), 'FOOTER MUST NOT APPEAR', fontsize=14)
        if not scanned:
            return pdf.tobytes()
        image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes('png')
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=image)
        return pdf.tobytes()


def source_pdf_with_second_page_approval():
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=595, height=842)
        page.insert_text((48, 70), 'PURCHASE ORDER - introductory page')
        with pymupdf.open(stream=source_pdf(), filetype='pdf') as signed:
            pdf.insert_pdf(signed)
        return pdf.tobytes()


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PurchaseOrderSourceArtworkTests(TestCase):
    def setUp(self):
        cache.clear()
        media = TemporaryDirectory(prefix='source-approval-artwork-')
        self.addCleanup(media.cleanup)
        storage = override_settings(MEDIA_ROOT=media.name, STORAGES={
            'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
        })
        storage.enable()
        self.addCleanup(storage.disable)
        self.vendor = Vendor.objects.create(vendor_code='SOURCE-ARTWORK', name='Synthetic supplier')
        self.order = self.make_order('0901')

    def make_order(self, number):
        return PurchaseOrder.objects.create(
            po_number=f'RAD-PRJ-PUR-{number}_2026', vendor=self.vendor,
            title='Source artwork regression', total_amount=100, status='completed',
            approved_by_name=SIGNER, approved_by_title='Chief Executive Officer',
            approved_date=APPROVAL_DATE,
        )

    def retain_source(self, content=None):
        content = content if content is not None else source_pdf()
        digest = hashlib.sha256(content).hexdigest()
        key = default_storage.save(f'procurement/signed_documents/2026/{self.order.pk}.pdf', ContentFile(content))
        url = default_storage.url(key)
        document = PODocument.objects.create(
            original_filename='Synthetic signed PO.pdf', s3_key=key, s3_url=url,
            document_type='purchase_order', extraction_status='completed', confirmed_po=self.order,
            extracted_data={
                'source_sha256': digest, 'signature_verified': True, 'stamp_verified': True,
                'approved_by_name': SIGNER, 'approved_date': APPROVAL_DATE.isoformat(),
                'approval_evidence_complete': True,
            },
        )
        self.order.approval_signature = f'{url}#page=1'
        self.order.approval_stamp = f'{url}#page=1'
        self.order.approval_log = [{
            'stage': 'Signed PO document approval', 'status': 'Approved',
            'evidence_document_id': str(document.pk), 'approver': SIGNER,
            'date': APPROVAL_DATE.isoformat(), 'signature_verified': True,
            'stamp_verified': True, 'approval_evidence_complete': True,
        }]
        self.order.attachments = [{
            'type': 'signed_purchase_order_pdf', 'document_id': str(document.pk),
            'sha256': digest, 'signature_verified': True, 'stamp_verified': True,
            's3_key': key, 'filename': 'Synthetic signed PO.pdf', 'content_type': 'application/pdf',
        }]
        self.order.save(update_fields=['approval_signature', 'approval_stamp', 'approval_log', 'attachments'])
        return document, content

    def assert_buyer_pixels_only(self, artwork):
        self.assertIsNotNone(artwork)
        with Image.open(artwork) as image:
            pixels = list(image.convert('RGB').getdata())
            blue = sum(1 for red, green, blue in pixels if blue > 170 and red < 60 and green < 60)
            green = sum(1 for red, green, blue in pixels if green > 100 and red < 60 and blue < 60)
            red = sum(1 for red, green, blue in pixels if red > 170 and green < 60 and blue < 60)
            self.assertGreater(blue, 500, 'Source company seal pixels must remain visible.')
            self.assertGreater(green, 100, 'Source signature pixels must remain visible.')
            self.assertEqual(red, 0, 'Seller approval and page footer must not enter the buyer block.')
            self.assertLess(image.width, 1500)
            self.assertLess(image.height, 1500)

    def test_native_source_preserves_buyer_artwork_without_changing_original_or_saved_evidence(self):
        document, original = self.retain_source()
        before = (PurchaseOrder.objects.values().get(pk=self.order.pk), PODocument.objects.values().get(pk=document.pk))
        self.assert_buyer_pixels_only(source_approval_artwork(self.order))
        with default_storage.open(document.s3_key, 'rb') as saved:
            self.assertEqual(saved.read(), original)
        self.assertEqual(before, (PurchaseOrder.objects.values().get(pk=self.order.pk), PODocument.objects.values().get(pk=document.pk)))

    def test_scanned_cover_uses_one_bounded_ocr_then_reuses_unchanged_source_crop(self):
        with pymupdf.open(stream=source_pdf(), filetype='pdf') as native:
            words = [{'text': item[4], 'bbox': list(item[:4]), 'method': 'ocr', 'confidence': 99}
                     for item in native[0].get_text('words')]
        with pymupdf.open(stream=source_pdf(scanned=True), filetype='pdf') as scanned:
            # Additional pages must never be OCRed to find another signature.
            for _ in range(4):
                scanned.new_page()
            self.retain_source(scanned.tobytes())

        def read_words(page, *args, **kwargs):
            self.assertEqual(page.number, 0)
            self.assertLessEqual(kwargs.get('dpi', 200), 220)
            return words

        with patch('apps.procurement.services.purchase_order_source_artwork._ocr_words', side_effect=read_words) as ocr:
            first = source_approval_artwork(self.order)
            self.assert_buyer_pixels_only(first)
            second = source_approval_artwork(self.order)
            self.assertEqual(first.getvalue(), second.getvalue())
        ocr.assert_called_once()

    def test_matching_page_two_references_use_only_the_recorded_second_page(self):
        document, _ = self.retain_source(source_pdf_with_second_page_approval())
        self.order.approval_signature = f'{document.s3_url}#page=2'
        self.order.approval_stamp = f'{document.s3_url}#page=2'
        with patch('apps.procurement.services.purchase_order_source_artwork._ocr_words') as ocr:
            self.assert_buyer_pixels_only(source_approval_artwork(self.order))
        ocr.assert_not_called()

    def test_page_one_reference_never_borrows_approval_artwork_from_a_later_page(self):
        self.retain_source(source_pdf_with_second_page_approval())

        def no_cover_words(page, **kwargs):
            self.assertEqual(page.number, 0)
            return []

        with patch('apps.procurement.services.purchase_order_source_artwork._ocr_words', side_effect=no_cover_words) as ocr:
            self.assertIsNone(source_approval_artwork(self.order))
        ocr.assert_called_once()

    def test_signature_and_stamp_must_reference_the_same_supported_source_page(self):
        document, _ = self.retain_source(source_pdf_with_second_page_approval())
        for signature_page, stamp_page in ((1, 2), (2, 1), (3, 3)):
            with self.subTest(signature_page=signature_page, stamp_page=stamp_page):
                self.order.approval_signature = f'{document.s3_url}#page={signature_page}'
                self.order.approval_stamp = f'{document.s3_url}#page={stamp_page}'
                with patch('apps.procurement.services.purchase_order_source_artwork.default_storage.open') as stored:
                    self.assertIsNone(source_approval_artwork(self.order))
                stored.assert_not_called()

    def test_document_id_and_url_cannot_borrow_another_orders_approval_artwork(self):
        document, _ = self.retain_source()
        document.confirmed_po = self.make_order('0902')
        document.save(update_fields=['confirmed_po'])
        with patch('apps.procurement.services.purchase_order_source_artwork.default_storage.open') as source_open:
            self.assertIsNone(source_approval_artwork(self.order))
        source_open.assert_not_called()

    def test_changed_source_bytes_fail_digest_verification(self):
        document, _ = self.retain_source()
        with default_storage.open(document.s3_key, 'wb') as stored:
            stored.write(source_pdf(scanned=True))
        self.assertIsNone(source_approval_artwork(self.order))

    def test_signature_and_stamp_require_verified_source_flags(self):
        document, _ = self.retain_source()
        for field in ('signature_verified', 'stamp_verified'):
            with self.subTest(field=field):
                document.extracted_data[field] = False
                document.save(update_fields=['extracted_data'])
                self.assertIsNone(source_approval_artwork(self.order))
                document.extracted_data[field] = True

    def test_source_log_also_requires_both_verification_flags(self):
        self.retain_source()
        for field in ('signature_verified', 'stamp_verified'):
            with self.subTest(field=field):
                self.order.approval_log[0][field] = False
                self.assertIsNone(source_approval_artwork(self.order))
                self.order.approval_log[0][field] = True

    def test_source_name_and_date_must_match_recorded_final_approval(self):
        document, _ = self.retain_source()
        for field, mismatch in (('approved_by_name', 'Another Buyer'), ('approved_date', '2026-09-13')):
            with self.subTest(field=field):
                original = document.extracted_data[field]
                document.extracted_data[field] = mismatch
                document.save(update_fields=['extracted_data'])
                self.assertIsNone(source_approval_artwork(self.order))
                document.extracted_data[field] = original

    def test_cached_crop_does_not_bypass_current_source_verification_or_ownership(self):
        document, _ = self.retain_source()
        self.assertIsNotNone(source_approval_artwork(self.order))
        document.extracted_data['stamp_verified'] = False
        document.save(update_fields=['extracted_data'])
        self.assertIsNone(source_approval_artwork(self.order))
        document.extracted_data['stamp_verified'] = True
        document.confirmed_po = self.make_order('0902')
        document.save(update_fields=['extracted_data', 'confirmed_po'])
        self.assertIsNone(source_approval_artwork(self.order))

    def test_unapproved_source_log_does_not_authorize_stamp_artwork(self):
        self.retain_source()
        self.order.approval_log[0]['status'] = 'Evidence review required'
        self.assertIsNone(source_approval_artwork(self.order))

    def test_invalid_storage_reference_is_not_opened_or_fetched(self):
        document, _ = self.retain_source()
        document.s3_key = 'https://unrelated.example.invalid/signed.pdf'
        document.save(update_fields=['s3_key'])
        with patch('apps.procurement.services.purchase_order_source_artwork.default_storage.open') as source_open, \
                patch('urllib.request.urlopen') as remote_fetch:
            self.assertIsNone(source_approval_artwork(self.order))
        source_open.assert_not_called()
        remote_fetch.assert_not_called()

    def test_unrecognized_cover_does_not_guess_a_source_approval_rectangle(self):
        with pymupdf.open() as unrelated:
            unrelated.new_page().insert_text((48, 70), 'Supplier quotation')
            self.retain_source(unrelated.tobytes())
        with patch('apps.procurement.services.purchase_order_source_artwork._ocr_words', return_value=[]):
            self.assertIsNone(source_approval_artwork(self.order))

    def test_pdf_and_word_include_identical_source_crop_without_repeating_source_name_or_date(self):
        document, original = self.retain_source()
        artwork = source_approval_artwork(self.order)
        self.assertIsNotNone(artwork)
        crop = artwork.getvalue()
        with Image.open(BytesIO(crop)) as image:
            expected_pixels = image.convert('RGB').tobytes()
            expected_size = image.size
        pdf_bytes, warnings = build_purchase_order_pdf(self.order)
        self.assertFalse(warnings)
        with pymupdf.open(stream=pdf_bytes, filetype='pdf') as pdf:
            self.assertNotIn(SIGNER, pdf[0].get_text())
            self.assertNotIn(APPROVAL_DATE.isoformat(), pdf[0].get_text())
            images = [entry for entry in pdf[0].get_images() if entry[2:4] == expected_size]
            self.assertEqual(len(images), 1)
            pixmap = pymupdf.Pixmap(pdf, images[0][0])
            self.assertEqual(pixmap.samples, expected_pixels)
            # The original is still separately attached in full, including
            # source text that was deliberately excluded from the cover crop.
            self.assertIn(SIGNER, pdf[-1].get_text())
            self.assertIn('FOOTER MUST NOT APPEAR', pdf[-1].get_text())
        word = Document(BytesIO(build_purchase_order_docx(self.order)))
        self.assertIn(crop, [part.blob for part in word.part.package.parts])
        text = ''.join(word.element.itertext())
        self.assertNotIn(SIGNER, text)
        self.assertNotIn(APPROVAL_DATE.isoformat(), text)
        with default_storage.open(document.s3_key, 'rb') as saved:
            self.assertEqual(saved.read(), original)

    def test_export_resolves_missing_attachment_key_through_confirmed_source_document(self):
        document, original = self.retain_source()
        self.order.attachments[0].pop('s3_key')
        self.order.save(update_fields=['attachments'])
        content, warnings = build_purchase_order_pdf(self.order)
        self.assertFalse(warnings)
        with pymupdf.open(stream=content, filetype='pdf') as exported, \
                pymupdf.open(stream=original, filetype='pdf') as source:
            self.assertEqual(exported[-1].get_text(), source[0].get_text())
            self.assertIn('FOOTER MUST NOT APPEAR', exported[-1].get_text())
        with default_storage.open(document.s3_key, 'rb') as saved:
            self.assertEqual(saved.read(), original)

    def test_export_does_not_read_foreign_document_even_with_its_storage_key_in_attachment(self):
        document, _ = self.retain_source()
        document.confirmed_po = self.make_order('0902')
        document.save(update_fields=['confirmed_po'])
        # The stale/forged attachment still supplies this now-foreign source
        # document ID and its valid private storage key.
        self.assertEqual(self.order.attachments[0]['s3_key'], document.s3_key)
        with patch('django.core.files.storage.default_storage.open') as stored:
            content, warnings = build_purchase_order_pdf(self.order)
        stored.assert_not_called()
        self.assertEqual(len(warnings), 1)
        self.assertIn('file could not be downloaded', warnings[0])
        with pymupdf.open(stream=content, filetype='pdf') as exported:
            text = '\n'.join(page.get_text() for page in exported)
        self.assertNotIn('FOOTER MUST NOT APPEAR', text)
        self.assertNotIn('SELLER ONLY', text)
