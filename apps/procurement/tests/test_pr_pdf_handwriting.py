"""Date recognition tests: literal evidence, independent variants, no network."""

from datetime import date
import io
import os
import shutil
from unittest import TestCase, skipUnless
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
import pymupdf

from apps.procurement.services.pr_pdf_handwriting import (
    _vision_date_candidates, agreed_date, parse_source_date, read_approval_date,
)


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf"


def _date_pdf(value="17.03.2024"):
    image = Image.new("RGB", (500, 130), "white")
    font = ImageFont.truetype(FONT, 38) if os.path.exists(FONT) else ImageFont.load_default(size=38)
    ImageDraw.Draw(image).text((35, 30), value, fill=(35, 45, 150), font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    document = pymupdf.open()
    page = document.new_page(width=500, height=130)
    page.insert_image(page.rect, stream=buffer.getvalue())
    return document


def _cell():
    return {"text": "uncertain", "evidence": {"page": 1, "bbox": [15, 15, 475, 115],
                                               "coordinate_space": "points", "recognition_confidence": 0}}


class ApprovalDateAgreementTests(TestCase):
    def test_accepts_other_valid_dates_and_leap_year_without_repairing_digits(self):
        self.assertEqual(parse_source_date("17.03.2024"), date(2024, 3, 17))
        self.assertEqual(parse_source_date("29-02-2024"), date(2024, 2, 29))
        for value in ("0.0.2028", "31.02.2026", "07.0l.2026", "07.01.202?", "07 012026"):
            self.assertIsNone(parse_source_date(value))

    def test_repeating_one_preprocessing_variant_is_not_independent_agreement(self):
        values = [{"text": "17.03.2024", "variant": "threshold", "psm": psm} for psm in (7, 13)]
        self.assertIsNone(agreed_date(values))
        values.append({"text": "17.03.2024", "variant": "original"})
        self.assertEqual(agreed_date(values), date(2024, 3, 17))

    def test_conflicting_valid_dates_remain_ambiguous_even_with_a_majority(self):
        values = [{"text": value, "variant": str(index)} for index, value in enumerate(
            ("07.01.2026", "07.01.2026", "07.01.2028"))]
        self.assertIsNone(agreed_date(values))

    def test_vision_normalized_date_must_match_literal_transcription(self):
        values = [{"text": "07.01.2028", "date_iso": "2026-01-07", "variant": variant,
                   "ambiguous": False} for variant in ("original", "mask")]
        self.assertIsNone(agreed_date(values))
        values[0]["text"] = "07.01.2026"
        values[1].update({"text": "07.01.2026", "ambiguous": True})
        self.assertIsNone(agreed_date(values))

    def test_placeholder_credentials_never_make_a_network_request(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "your-openai-api-key-here"}):
            with patch("openai.OpenAI") as client:
                candidates, status = _vision_date_candidates({})
        client.assert_not_called()
        self.assertEqual((candidates, status), ([], "not_configured"))

    def test_failed_local_and_vision_readings_return_evidence_without_guessing(self):
        with _date_pdf() as document:
            with patch("apps.procurement.services.pr_pdf_handwriting._local_date_candidates", return_value=[
                {"text": "0.0.2028", "variant": "mask"}, {"text": "20206", "variant": "threshold"},
            ]), patch("apps.procurement.services.pr_pdf_handwriting._vision_date_candidates", return_value=([], "not_configured")):
                result = read_approval_date(document, _cell(), {"width": 500, "height": 130})
        self.assertIsNone(result["date"])
        self.assertTrue(result["ink_present"])
        self.assertTrue(result["evidence"]["review_required"])
        self.assertEqual(result["evidence"]["vision_status"], "not_configured")
        self.assertEqual(result["evidence"]["candidates"][0]["text"], "0.0.2028")

    def test_configured_vision_requires_matching_two_crops_and_preserves_provenance(self):
        candidates = [{"text": "11.12.2023", "date_iso": "2023-12-11", "ambiguous": False,
                       "variant": variant, "engine": "vision"} for variant in ("original", "mask")]
        with _date_pdf("11.12.2023") as document:
            with patch("apps.procurement.services.pr_pdf_handwriting._local_date_candidates", return_value=[]), \
                 patch("apps.procurement.services.pr_pdf_handwriting._vision_date_candidates", return_value=(candidates, "completed")):
                result = read_approval_date(document, _cell(), {"width": 500, "height": 130})
        self.assertEqual(result["date"], date(2023, 12, 11))
        self.assertEqual(result["evidence"]["source"], "date_cell_vision_consensus")
        self.assertFalse(result["evidence"]["review_required"])
        self.assertEqual(len(result["evidence"]["crop_sha256"]), 64)


@skipUnless(shutil.which("tesseract") and os.path.exists(FONT), "Real date OCR needs local Tesseract and a font")
class RealApprovalDateOCRTests(TestCase):
    def test_different_blue_italic_dates_are_read_without_vision_or_hardcoded_values(self):
        for literal, expected in (("17.03.2024", date(2024, 3, 17)), ("29.02.2024", date(2024, 2, 29)),
                                  ("11.12.2023", date(2023, 12, 11))):
            with self.subTest(date=literal), _date_pdf(literal) as document:
                with patch("apps.procurement.services.pr_pdf_handwriting._vision_date_candidates", return_value=([], "disabled")) as vision:
                    result = read_approval_date(document, _cell(), {"width": 500, "height": 130})
                vision.assert_not_called()
                self.assertEqual(result["date"], expected)
                self.assertTrue(result["evidence"]["colored_ink"])
                self.assertEqual(result["evidence"]["source"], "date_cell_ocr_consensus")
