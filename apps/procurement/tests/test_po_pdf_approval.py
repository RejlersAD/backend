"""Buyer-side approval candidates never infer authorization or seller values."""

import hashlib
from unittest.mock import patch

import pymupdf
from django.test import SimpleTestCase

from apps.procurement.services.po_pdf_approval import (
    POApprovalPreviewError, _native_words, preview_signed_po_approval,
)


SERVICE = 'apps.procurement.services.po_pdf_approval'


def cover(page, *, buyer_name='Avery Stone', buyer_date='03.07.2026', signed=False, stamped=False,
          seller_date='14.08.2026', title='Senior Vice President, Middle East', quotation=False):
    page.insert_text((45, 55), 'QUOTATION' if quotation else 'PURCHASE ORDER', fontsize=16)
    page.insert_text((45, 75), 'RAD-PRJ-PUR-0085_JUL2026', fontsize=11)
    page.insert_text((45, 98), '1 July 2026', fontsize=11)
    page.insert_text((45, 180), 'Seller: Other Company', fontsize=11)
    page.insert_text((45, 430), 'Approved by:', fontsize=12)
    page.insert_text((335, 430), 'Order Confirmation:', fontsize=12)
    page.insert_text((335, 455), 'Seller Signature:', fontsize=10)
    page.insert_text((335, 565), 'Seller Alicia Different', fontsize=10)
    page.insert_text((335, 580), 'Seller Director', fontsize=10)
    page.insert_text((335, 615), f'Date: {seller_date}', fontsize=10)
    if buyer_name:
        page.insert_text((45, 565), buyer_name, fontsize=11)
    if title:
        page.insert_text((45, 580), title, fontsize=10)
        page.insert_text((45, 595), 'CEO, Rejlers Abu Dhabi', fontsize=10)
    page.insert_text((45, 615), f'Date: {buyer_date}', fontsize=10)
    if signed:
        page.draw_bezier((50, 510), (110, 468), (125, 540), (190, 500), color=(0.1, 0.15, 0.6), width=2)
    if stamped:
        page.draw_circle((238, 673), 29, color=(0.1, 0.15, 0.6), width=2)


def native_pdf(*, pages=1, **kwargs):
    with pymupdf.open() as document:
        cover(document.new_page(width=620, height=850), **kwargs)
        for _index in range(1, pages):
            document.new_page(width=620, height=850)
        return document.tobytes()


class POPDFApprovalTests(SimpleTestCase):
    def test_native_buyer_identity_date_and_ink_are_source_candidates_only(self):
        original = native_pdf(pages=27, signed=True, stamped=True)
        with patch(f'{SERVICE}._ocr_words') as ocr:
            result = preview_signed_po_approval(original)
        ocr.assert_not_called()
        self.assertEqual(result['source_sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(result['page_count'], 27)
        self.assertEqual(result['pages_inspected'], [1])
        evidence = result['approval_evidence']
        self.assertEqual(evidence['approved_by_name'], 'Avery Stone')
        self.assertEqual(evidence['approved_by_title'], 'Senior Vice President, Middle East / CEO, Rejlers Abu Dhabi')
        self.assertEqual(evidence['approved_date'], '2026-07-03')
        self.assertTrue(evidence['signature_detected'])
        self.assertTrue(evidence['stamp_detected'])
        self.assertTrue(evidence['requires_review'])
        self.assertEqual(evidence['detection_meaning'], 'candidate_only')
        self.assertNotIn('signature_verified', evidence)
        self.assertNotIn('status', evidence)
        self.assertEqual(evidence['provenance']['approved_by_name']['method'], 'native')

    def test_printed_name_title_and_placeholder_do_not_count_as_signature_or_stamp(self):
        result = preview_signed_po_approval(native_pdf())
        self.assertFalse(result['approval_evidence']['signature_detected'])
        self.assertFalse(result['approval_evidence']['stamp_detected'])

    def test_blank_buyer_date_does_not_use_po_header_or_seller_confirmation_date(self):
        with patch(f'{SERVICE}._ocr_words', side_effect=RuntimeError('OCR unavailable')):
            result = preview_signed_po_approval(native_pdf(buyer_date='', seller_date='14.08.2026'))
        self.assertEqual(result['approval_evidence']['approved_date'], '')
        self.assertTrue(any('Approval date' in issue for issue in result['approval_evidence']['issues']))

    def test_seller_signer_never_fills_missing_buyer_name(self):
        with patch(f'{SERVICE}._ocr_words', side_effect=RuntimeError('OCR unavailable')):
            result = preview_signed_po_approval(native_pdf(buyer_name='', title=''))
        self.assertEqual(result['approval_evidence']['approved_by_name'], '')
        self.assertEqual(result['approval_evidence']['approved_by_title'], '')

    def test_supplier_quotation_is_not_a_buyer_approval_cover(self):
        with patch(f'{SERVICE}._ocr_words') as ocr:
            result = preview_signed_po_approval(native_pdf(quotation=True, signed=True, stamped=True))
        self.assertIsNone(result['approval_evidence']['page'])
        self.assertEqual(result['approval_evidence']['approved_by_name'], '')
        self.assertFalse(result['approval_evidence']['signature_detected'])
        ocr.assert_not_called()

    def test_po_quotation_reference_does_not_turn_its_buyer_block_into_supplier_evidence(self):
        with pymupdf.open(stream=native_pdf(), filetype='pdf') as document:
            document[0].insert_text((45, 200), 'Quotation reference: Supplier offer 123', fontsize=10)
            result = preview_signed_po_approval(document.tobytes())
        self.assertEqual(result['approval_evidence']['approved_by_name'], 'Avery Stone')

    def test_only_first_two_pages_are_inspected_and_attached_later_order_is_ignored(self):
        with pymupdf.open() as document:
            for index in range(27):
                page = document.new_page(width=620, height=850)
                if index == 2:
                    cover(page, signed=True, stamped=True)
            pdf = document.tobytes()
        with patch(f'{SERVICE}._ocr_words', return_value=[]) as ocr:
            result = preview_signed_po_approval(pdf)
        self.assertEqual(result['pages_inspected'], [1, 2])
        self.assertEqual(ocr.call_count, 2)
        self.assertEqual(result['approval_evidence']['approved_by_name'], '')

    def test_scanned_source_uses_bounded_ocr_and_name_crop_without_directory_inference(self):
        with pymupdf.open(stream=native_pdf(signed=True, stamped=True), filetype='pdf') as template:
            words = [{**word, 'method': 'ocr', 'confidence': 90} for word in _native_words(template[0])]
            raster = template[0].get_pixmap(dpi=120).tobytes('png')
        with pymupdf.open() as scan:
            page = scan.new_page(width=620, height=850)
            page.insert_image(page.rect, stream=raster)
            pdf = scan.tobytes()
        def ocr(_page, clip=None, **_kwargs):
            if clip is None:
                return words
            return [word for word in words if word['text'] in ('Avery', 'Stone')]
        with patch(f'{SERVICE}._ocr_words', side_effect=ocr) as reader:
            evidence = preview_signed_po_approval(pdf)['approval_evidence']
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(evidence['approved_by_name'], 'Avery Stone')
        self.assertEqual(evidence['approved_date'], '2026-07-03')
        self.assertEqual(evidence['provenance']['approved_by_name']['refinement'], 'source_line_crop')
        self.assertTrue(evidence['requires_review'])

    def test_uncertain_ocr_date_is_left_blank(self):
        with pymupdf.open(stream=native_pdf(), filetype='pdf') as document:
            words = [{**word, 'method': 'ocr', 'confidence': 45 if word['text'] == '03.07.2026' else 95}
                     for word in _native_words(document[0])]
            pdf = document.tobytes()
        with patch(f'{SERVICE}._native_words', return_value=[]), patch(f'{SERVICE}._ocr_words', return_value=words):
            evidence = preview_signed_po_approval(pdf)['approval_evidence']
        self.assertEqual(evidence['approved_date'], '')

    def test_invalid_and_password_protected_pdfs_are_rejected(self):
        for content in (b'not-pdf', b'%PDF-corrupt'):
            with self.subTest(content=content), self.assertRaises(POApprovalPreviewError):
                preview_signed_po_approval(content)
        with pymupdf.open() as document:
            document.new_page()
            encrypted = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='secret')
        with self.assertRaisesMessage(POApprovalPreviewError, 'password protected'):
            preview_signed_po_approval(encrypted)
