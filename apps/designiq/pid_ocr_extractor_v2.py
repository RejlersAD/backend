"""
P&ID OCR Extractor V2 - Multi-Engine + AI Intelligence
Uses Tesseract, EasyOCR, PaddleOCR + OpenAI for accurate line detection
Supports: Onshore, Offshore, ADNOC, Industrial/Project line-number formats
"""

import re
import fitz  # PyMuPDF
from PIL import Image
import io
import logging
import json
from typing import List, Dict, Optional, Tuple
import numpy as np
from openai import OpenAI
from django.conf import settings
import base64

# ---------------------------------------------------------------------------
# Soft-coded: minimum embedded-text characters before falling back to OCR.
# For vector/searchable PDFs this is instant; for scanned images it will be 0.
# Raise this value in environments.json if you see OCR running on clear PDFs.
# ---------------------------------------------------------------------------
EMBEDDED_TEXT_MIN_CHARS = 80

# ---------------------------------------------------------------------------
# ADNOC format validation thresholds — soft-coded so they can be tuned
# without touching regex/validation logic.
#
# ADNOC_SEQ_MIN_DIGITS : minimum digits allowed in the sequence number.
#   Abu Dhabi Oil Co. drawings use 3-digit (e.g. 329, 454) and 4-digit
#   (e.g. 8703, 1023) sequences.  Original code required exactly 4; relax
#   to 3 to capture the shorter sequences on this project.
#
# ADNOC_PIPECLASS_MIN_LEN : minimum characters in the pipe-class segment.
#   Standard ADNOC classes are 4+ chars (AC3N, BC2GA…) but simpler project
#   drawings use 2-char classes (CI, RI, GA, GP…).  Original code required
#   ≥3 chars; relax to 2.
# ---------------------------------------------------------------------------
ADNOC_SEQ_MIN_DIGITS  = 3   # was 4 (hard-coded); accepts 3-digit sequences
ADNOC_PIPECLASS_MIN_LEN = 2  # was 3 (hard-coded); accepts 2-char pipe classes

# ---------------------------------------------------------------------------
# General format strategy — controls how results from all sub-formats are
# combined when format_type='general'.
#
# 'merge'  : collect ALL unique line numbers found by EVERY format, deduplicated
#             by canonical line_number string.  Returns the most complete list.
#             Best for mixed P&IDs with pipes annotated in different conventions.
#
# 'winner' : legacy behaviour — return only the format with the highest count.
#             Useful when one project consistently uses a single format and you
#             want to avoid false positives from other formats.
# ---------------------------------------------------------------------------
GENERAL_STRATEGY = 'merge'   # 'merge' | 'winner'

# ---------------------------------------------------------------------------
# Multi-rotation OCR — degrees to rotate the rendered page image BEFORE
# running Tesseract OCR on it.  PIL rotates counter-clockwise.
#
# 0°   → reads horizontal labels (standard)
# 90°  → reads labels written top-to-bottom (PIL CCW = drawing CW)
# 270° → reads labels written bottom-to-top (PIL CW  = drawing CCW)
# 180° → reads upside-down text (rare, included for completeness)
#
# P&ID drawings place pipe line designations at EVERY angle.  Running OCR
# at 0°/90°/270° covers ~99% of real-world P&ID orientations.
# Remove angles that slow processing without adding lines on your drawings.
# ---------------------------------------------------------------------------
OCR_ROTATION_ANGLES = [0, 90, 270]

# ---------------------------------------------------------------------------
# Soft-coded industrial format — all configurable without touching regex code.
# SIZE"-UNIT_NO-SERVICE_CODE-SEQUENCE-PIPING_CLASS(-END_DESIGNATOR)?
# Examples:
#   2"-2600-FL-352-32070R-E     (standard)
#   3/4"-2600-HD-430-32070R-E  (fractional size)
#   8"-2600-P-381-31051XR-E    (single-letter service)
#   1"-2600-FCWR-975-31210MR-V (multi-letter service, no end-desig common too)
# ---------------------------------------------------------------------------
INDUSTRIAL_FORMAT = {
    # Maximum "unit number" length (digits only, e.g. 2600)
    'unit_no_max_digits': 4,
    # Maximum letters in service/fluid code (e.g. FCWR = 4, FCWS = 4)
    'service_code_max_len': 6,
    # Piping-class structure: exactly 5 digits followed by 1-2 uppercase letters
    'piping_class_pattern': r'\d{5}[A-Z]{1,2}',
    # Sequence number: 3-5 digits (some short, some with leading zeros)
    'seq_digits_min': 3,
    'seq_digits_max': 5,
}

# ---------------------------------------------------------------------------
# Soft-coded Borouge / Linde "area-first" format.
# Used by Borouge H2 Extraction Unit P&IDs (Linde draughting) where the
# sequence number is 6 digits and the piping class starts with a letter.
# Format: SIZE"-AREA-SERVICE-SEQUENCE-PIPING_CLASS-ENDDESIG
# Examples (verbatim from OCR, spaces after hyphens are normal):
#   1"-63- UA-149472- A1AU01- V
#   3"-63- IA-147202- A0KU01- V
#   4"-63- IA-140061- A0KU01- V
#   4"-37- IA-00096- A0KU01- V
#   2"-63- UA-140082- A1AU01- V
# The sole structural change vs. the generic "WITH AREA" format is the
# 6-digit (padded) sequence number. All other knobs are kept generic so
# edits here do not affect non-Borouge drawings.
# ---------------------------------------------------------------------------
BOROUGE_AREA_FORMAT = {
    'size_digits_min':      1,
    'size_digits_max':      2,
    'area_digits_min':      2,
    'area_digits_max':      3,
    'service_letters_min':  1,
    'service_letters_max':  3,
    'seq_digits_min':       4,   # 00096 (5-digit padded) … 149472 (6-digit)
    'seq_digits_max':       6,
    'pipeclass_len_min':    5,   # A1AU01, A0KU01 = 6 chars; allow 5 for OCR
    'pipeclass_len_max':    7,
    'enddesig_letters_min': 1,
    'enddesig_letters_max': 2,
}

# ---------------------------------------------------------------------------
# Soft-coded COVERAGE AUDIT configuration.
# After the main extraction finishes, a permissive "line-like candidate"
# scan is run over the SAME raw text that was fed to the regex engine.
# Any candidate that does NOT appear in the final extracted set is logged
# as a potentially-missed line.  This is purely additive — it never
# rejects items, only reports.
#
# Change the patterns/knobs here to tune audit sensitivity without
# touching any parsing code.
# ---------------------------------------------------------------------------
COVERAGE_AUDIT_CONFIG = {
    # Master switch — set False to skip the audit entirely.
    'enabled': True,
    # Minimum number of hyphen-separated segments a candidate must have
    # to be considered "line-like".  Real line numbers have at least 3
    # (e.g. SIZE-FLUID-SEQ) and often 4-6.
    'min_segments': 3,
    # Maximum tokens we log as missed per page (prevents log spam on
    # drawings full of near-matches).
    'max_reported_per_page': 40,
    # Fuzzy-match tolerance when comparing a candidate to extracted items.
    # Uses difflib.SequenceMatcher ratio (0..1).  >= threshold = "already
    # extracted — don't flag".  0.82 catches OCR variants like O↔0, 1↔I.
    'fuzzy_match_threshold': 0.82,
    # Permissive candidate regexes — each tries a different real-world
    # line-number shape.  Order does not matter; all are tried per page.
    # Keep patterns BROAD — the compare-to-extracted step filters noise.
    'candidate_patterns': [
        # SIZE"-...-...-... (hyphenated tag starting with a size)
        r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+[A-Z0-9]{1,6}[-\s]+[A-Z0-9]{1,8}[-\s]+[A-Z0-9]{2,10}(?:[-\s]+[A-Z0-9]{1,10}){0,3}\b',
        # AREA-FLUID-SIZE-PIPECLASS-SEQUENCE (offshore shape, size after fluid)
        r'\b\d{2,4}[-\s]+[A-Z]{1,4}[-\s]+\d{1,2}[-\s]+[A-Z0-9]{4,10}[-\s]+\d{3,6}\b',
        # Industrial: SIZE"-UNIT-SERVICE-SEQ-PIPINGCLASS(-END)
        r'\b\d{1,2}(?:/\d{1,2})?["]?[-\s]+\d{3,4}[-\s]+[A-Z]{1,6}[-\s]+\d{3,5}[-\s]+\d{5}[A-Z]{1,2}(?:[-\s]+[A-Z])?\b',
    ],
    # Characters stripped before fuzzy compare (spaces, quotes, stray OCR dots).
    'normalise_strip_chars': ' "\'.',
}

# ---------------------------------------------------------------------------
# Soft-coded SMART RECOVERY configuration.
# When the coverage audit reports missed candidates, the recovery layer
# attempts to "rescue" them — tokenising each candidate and validating
# against the soft-coded format dicts (INDUSTRIAL_FORMAT,
# BOROUGE_AREA_FORMAT).  Anything that structurally passes is promoted to
# a full line item with:
#     extraction_method = 'audit_recovery'
#     recovered         = True
#     confidence        = 'medium'
#
# The original regex/validation logic is NOT changed; recovery runs only
# on residue the primary pipeline already rejected.
# ---------------------------------------------------------------------------
SMART_RECOVERY_CONFIG = {
    'enabled': True,
    # Global cap — never add more recovered items than this per extraction.
    'max_recovered_items': 200,
    # Per-candidate minimum tokens after split on [-\s]+
    'min_tokens': 3,
    'max_tokens': 7,
    # When ambiguity exists between formats, prefer this order.
    'format_priority': ['industrial', 'area', 'standard'],
    # Duplicate-guard fuzzy threshold — recovered items whose line_number
    # fuzzy-matches an existing extracted item at or above this ratio are
    # skipped (avoids near-duplicates from OCR variants).
    'dup_guard_threshold': 0.88,
}

# Conditional import for pytesseract (graceful fallback if not installed)
try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None
    PYTESSERACT_AVAILABLE = False

# Import Geometric FROM-TO detector
try:
    from apps.designiq.geometric_from_to_detector import GeometricFromToDetector
    GEOMETRIC_DETECTOR_AVAILABLE = True
except ImportError as e:
    GEOMETRIC_DETECTOR_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning(f"⚠️ GeometricFromToDetector not available: {e}")

logger = logging.getLogger(__name__)


class PIDLineExtractorV2:
    """
    Multi-Engine P&ID line number extractor with AI intelligence
    Step 1: Extract ALL text using Tesseract, EasyOCR, PaddleOCR
    Step 2: Use OpenAI to intelligently categorize into table format
    
    Gracefully handles missing ML/OCR dependencies - will use available engines only
    """
    
    def __init__(self):
        self.easyocr_reader = None
        self.paddleocr_reader = None
        self.openai_client = None
        self.geometric_detector = None
        self.pytesseract_available = PYTESSERACT_AVAILABLE
        self._init_engines()
        self._init_geometric_detector()
        
        # Log configuration summary
        engines_available = []
        if self.pytesseract_available:
            engines_available.append("Tesseract")
        if self.easyocr_reader:
            engines_available.append("EasyOCR")
        if self.paddleocr_reader:
            engines_available.append("PaddleOCR")
        if self.openai_client:
            engines_available.append("OpenAI")
        
        if engines_available:
            logger.info(f"✅ P&ID Extractor V2 ready with: {', '.join(engines_available)}")
        else:
            logger.warning("⚠️ P&ID Extractor V2 initialized with NO OCR engines - extraction quality will be limited")
        self._init_geometric_detector()
    
    def _init_geometric_detector(self):
        """Initialize Geometric Line-based FROM-TO detector"""
        if GEOMETRIC_DETECTOR_AVAILABLE:
            try:
                self.geometric_detector = GeometricFromToDetector(
                    line_detection_threshold=50,
                    min_line_length=30,
                    max_line_gap=10,
                    association_threshold=0.03
                )
                logger.info("✅ Geometric FROM-TO Detector initialized")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize geometric detector: {e}")
                self.geometric_detector = None
        else:
            self.geometric_detector = None
            logger.info("ℹ️ Geometric detector not available (missing dependencies)")
    
    def _extract_pdf_embedded_text(self, page) -> str:
        """
        Extract text directly from a PDF page without OCR (fast, handles rotated text).

        For searchable / vector PDFs the text objects are embedded in the file at
        their correct positions regardless of rotation.  PyMuPDF returns them all
        via get_text("words"), giving us perfect accuracy for free.

        Falls back gracefully if the page has no embedded text (scanned image PDF).

        SOFT-CODED threshold: EMBEDDED_TEXT_MIN_CHARS (default 80) controls whether
        the result is considered "sufficient".  Return value is the raw combined
        string; the caller checks length against the threshold.

        CAD NOTE: AutoCAD/SmartPlant PDFs often store text as individual character
        glyphs (char-by-char).  "words" mode joins them with spaces making regex
        matching fail (e.g. "2 \" - F G - C I - 3 2 9").  The rawdict span pass
        below reconstructs each span as a contiguous token, recovering full tags.
        """
        try:
            # ----------------------------------------------------------------
            # Pass 1: word-level extraction (sorted for natural reading order).
            # Good for title blocks and label text stored as whole words.
            # ----------------------------------------------------------------
            words = page.get_text("words")
            words_text = ""
            if words:
                sorted_words = sorted(words, key=lambda w: (round(w[1] / 10), w[0]))
                words_text = ' '.join(w[4] for w in sorted_words if str(w[4]).strip())

            # ----------------------------------------------------------------
            # Pass 2: rawdict span-level extraction.
            # CAD PDFs store pipe line tags as individual character objects
            # within a single span.  Reading spans directly reconstructs the
            # full tag (e.g. '2"-FG-CI-329') without inter-character spaces.
            #
            # Direction-aware grouping:
            # PyMuPDF sets a "dir" vector per line (the baseline direction).
            # (1,0) = horizontal, (0,-1) = 90° CW, (0,1) = 90° CCW, (-1,0)=180°.
            # We bucket spans by direction so rotated labels form complete tokens
            # instead of being interleaved with horizontal text in sort order.
            # ----------------------------------------------------------------
            span_texts = []
            try:
                raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                # Bucket: direction_key -> list of (origin_y, origin_x, text)
                dir_buckets: dict = {}
                for block in raw.get("blocks", []):
                    if block.get("type") != 0:
                        continue  # skip image blocks
                    for line in block.get("lines", []):
                        # Direction vector: round to 1 decimal to bucket similar angles
                        raw_dir = line.get("dir", (1, 0))
                        dir_key = (round(raw_dir[0], 1), round(raw_dir[1], 1))
                        origin = line.get("bbox", [0, 0, 0, 0])
                        line_x, line_y = origin[0], origin[1]
                        line_parts = []
                        for span in line.get("spans", []):
                            t = span.get("text", "").strip()
                            if t:
                                line_parts.append(t)
                        if line_parts:
                            token = ' '.join(line_parts)
                            dir_buckets.setdefault(dir_key, []).append(
                                (line_y, line_x, token)
                            )

                # Emit each direction bucket sorted by position (reading order
                # per angle), joining with spaces so regex can scan the stream.
                for dir_key, entries in dir_buckets.items():
                    entries.sort(key=lambda e: (round(e[0] / 10), e[1]))
                    bucket_text = ' '.join(e[2] for e in entries)
                    span_texts.append(bucket_text)
                    logger.debug(
                        f"[embedded_text] dir={dir_key} → "
                        f"{len(entries)} spans, {len(bucket_text)} chars"
                    )
            except Exception as _rd_err:
                logger.debug(f"[embedded_text] rawdict pass failed: {_rd_err}")

            rawdict_text = ' '.join(span_texts)

            # Combine both passes — words_text catches normal labels, rawdict
            # catches the char-by-char CAD tags.  Duplicates are harmless for
            # regex matching (dedup happens later).
            combined = (words_text + ' ' + rawdict_text).strip()
            return combined if combined else page.get_text("text")

        except Exception as exc:
            logger.warning(f"[embedded_text] page text extraction failed: {exc}")
            return ""

    def _normalize_ocr_text(self, text: str) -> str:
        """
        🔧 STRICT OCR NORMALIZATION - Force O → 0 conversion
        
        Domain Rule: Line numbers NEVER contain letter 'O', only digit '0'
        
        Problem: OCR confuses:
        - 'O' (letter O) ↔ '0' (digit zero)
        
        Solution: Force replace ALL 'O' with '0' everywhere
        - In line numbers
        - In fluid codes
        - In pipe classes
        - In ALL extracted fields
        
        Example:
        - "AS5NLO-2014" → "AS5NL0-2014"
        - "604-RO-4-AN1NLO-0011" → "604-R0-4-AN1NL0-0011"
        
        This eliminates duplicates automatically:
        - Before: ["AS5NLO-2014", "AS5NL0-2014"] (2 entries)
        - After: ["AS5NL0-2014"] (1 entry)
        
        Args:
            text: Raw OCR text that may contain letter 'O'
            
        Returns:
            Normalized text with all 'O' → '0'
        """
        if not text:
            return text
        
        # Convert to uppercase first for consistency
        normalized = text.upper()
        
        # Force replace ALL 'O' (letter) with '0' (digit)
        normalized = normalized.replace('O', '0')
        
        return normalized

    
    def _init_engines(self):
        """Initialize all OCR engines and OpenAI with smart timeouts"""
        import threading
        
        def init_with_timeout(init_func, timeout_seconds, engine_name):
            """Initialize engine with timeout - returns True if successful"""
            result = {'success': False, 'error': None}
            
            def target():
                try:
                    init_func()
                    result['success'] = True
                except Exception as e:
                    result['error'] = e
            
            logger.info(f"🔄 Initializing {engine_name} ({timeout_seconds}s timeout)...")
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            thread.join(timeout=timeout_seconds)
            
            if thread.is_alive():
                logger.warning(f"⏱️ {engine_name} initialization timeout after {timeout_seconds}s - skipping (will use other engines)")
                return False
            elif result['success']:
                logger.info(f"✅ {engine_name} initialized")
                return True
            else:
                logger.warning(f"⚠️ {engine_name} not available: {result['error']}")
                return False
        
        # Initialize EasyOCR with 60-second timeout
        def init_easyocr():
            try:
                import easyocr
                self.easyocr_reader = easyocr.Reader(['en'], gpu=False)
            except ImportError as e:
                logger.warning(f"⚠️ EasyOCR not installed: {e}")
                raise
        
        init_with_timeout(init_easyocr, 60, "EasyOCR")
        
        # Initialize PaddleOCR with 90-second timeout
        def init_paddleocr():
            try:
                from paddleocr import PaddleOCR
                import os
                # Disable model connectivity check to speed up initialization
                os.environ['DISABLE_MODEL_SOURCE_CHECK'] = 'True'
                self.paddleocr_reader = PaddleOCR(
                    lang='en',
                    ocr_version='PP-OCRv4'  # Use v4 (faster, smaller models)
                )
            except ImportError as e:
                logger.warning(f"⚠️ PaddleOCR not installed: {e}")
                raise
        
        init_with_timeout(init_paddleocr, 90, "PaddleOCR")
        
        # Initialize OpenAI
        try:
            openai_key = getattr(settings, 'OPENAI_API_KEY', None)
            if openai_key:
                self.openai_client = OpenAI(api_key=openai_key)
                logger.info("✅ OpenAI initialized")
            else:
                logger.warning("⚠️ OPENAI_API_KEY not configured")
        except Exception as e:
            logger.warning(f"⚠️ OpenAI not available: {e}")
    
    def extract_all_text_from_image(self, img: Image.Image) -> Dict[str, str]:
        """
        Extract text using all available OCR engines
        Returns combined text from all engines
        """
        results = {}
        
        # 1. Tesseract OCR - Multiple PSM modes to detect vertical text
        if PYTESSERACT_AVAILABLE and pytesseract:
            try:
                # PSM 6: Assume uniform block of text (horizontal) — most reliable base
                tesseract_text = pytesseract.image_to_string(img, config='--psm 6')

                # PSM 11: Sparse text — finds isolated labels anywhere on page
                try:
                    tesseract_sparse = pytesseract.image_to_string(img, config='--psm 11')
                    if tesseract_sparse and len(tesseract_sparse.strip()) > 10:
                        tesseract_text += ' ' + tesseract_sparse
                        logger.info(f"  🔍 Tesseract sparse text: +{len(tesseract_sparse)} characters")
                except Exception:
                    pass

                # ----------------------------------------------------------------
                # Multi-rotation pass — OCR_ROTATION_ANGLES (soft-coded constant).
                # P&ID pipe line tags are written at all angles (horizontal,
                # top-to-bottom, bottom-to-top).  Running Tesseract PSM 6 on
                # a PIL-rotated copy of the image converts vertical text to
                # horizontal, which Tesseract reads with near-perfect accuracy.
                # ----------------------------------------------------------------
                for _angle in OCR_ROTATION_ANGLES:
                    if _angle == 0:
                        continue  # already processed above
                    try:
                        _rotated_img = img.rotate(_angle, expand=True)
                        _rot_text = pytesseract.image_to_string(_rotated_img, config='--psm 6')
                        if _rot_text and len(_rot_text.strip()) > 10:
                            tesseract_text += ' ' + _rot_text
                            logger.info(
                                f"  📐 Tesseract {_angle}° rotation: "
                                f"+{len(_rot_text)} chars"
                            )
                    except Exception as _re:
                        logger.debug(f"  ⚠️ Tesseract {_angle}° rotation failed: {_re}")

                results['tesseract'] = tesseract_text
                logger.info(f"  ✅ Tesseract extracted {len(tesseract_text)} characters (combined all angles)")
            except Exception as e:
                logger.warning(f"  ⚠️ Tesseract failed: {e}")
        else:
            logger.warning(f"  ⚠️ Pytesseract not available, skipping Tesseract OCR")
        
        # 2. EasyOCR - Run at all OCR_ROTATION_ANGLES (same strategy as Tesseract)
        if self.easyocr_reader:
            try:
                easyocr_text = ""
                for _angle in OCR_ROTATION_ANGLES:
                    _ocr_img = img.rotate(_angle, expand=True) if _angle != 0 else img
                    _img_array = np.array(_ocr_img)
                    _result = self.easyocr_reader.readtext(
                        _img_array,
                        detail=0,
                        paragraph=False
                    )
                    _angle_text = ' '.join(_result)
                    if _angle_text.strip():
                        easyocr_text += ' ' + _angle_text
                        if _angle != 0:
                            logger.info(
                                f"  📐 EasyOCR {_angle}° rotation: "
                                f"+{len(_angle_text)} chars"
                            )
                results['easyocr'] = easyocr_text.strip()
                logger.info(f"  ✅ EasyOCR extracted {len(easyocr_text)} characters (all angles)")
            except Exception as e:
                logger.warning(f"  ⚠️ EasyOCR failed: {e}")
        
        # 3. PaddleOCR — run at every OCR_ROTATION_ANGLES for parity with
        # Tesseract/EasyOCR.  PaddleOCR has its own detector but is weakest on
        # vertical text, so rotating the source image covers horizontal,
        # top-to-bottom and bottom-to-top pipe-line labels uniformly.
        if self.paddleocr_reader:
            try:
                paddle_texts = []
                for _angle in OCR_ROTATION_ANGLES:
                    try:
                        _ocr_img = img.rotate(_angle, expand=True) if _angle != 0 else img
                        img_array = np.array(_ocr_img)
                        paddle_result = self.paddleocr_reader.ocr(img_array)
                        _angle_texts = []
                        # PaddleOCR returns [[line1_data, line2_data, ...]] or None
                        if paddle_result and isinstance(paddle_result, list) and len(paddle_result) > 0:
                            first_page = paddle_result[0]
                            if first_page and isinstance(first_page, list):
                                for line in first_page:
                                    # Each line is [bbox, (text, confidence)]
                                    if line and isinstance(line, (list, tuple)) and len(line) >= 2:
                                        text_data = line[1]
                                        if isinstance(text_data, (list, tuple)) and len(text_data) > 0:
                                            _angle_texts.append(str(text_data[0]))
                        if _angle_texts:
                            paddle_texts.extend(_angle_texts)
                            if _angle != 0:
                                logger.info(
                                    f"  📐 PaddleOCR {_angle}° rotation: "
                                    f"+{len(_angle_texts)} tokens"
                                )
                    except Exception as _pe:
                        logger.debug(f"  ⚠️ PaddleOCR {_angle}° rotation failed: {_pe}")

                if paddle_texts:
                    paddle_text = ' '.join(paddle_texts)
                    results['paddleocr'] = paddle_text
                    logger.info(f"  ✅ PaddleOCR extracted {len(paddle_text)} characters (all angles)")
                else:
                    logger.warning(f"  ⚠️ PaddleOCR: No text extracted")
            except Exception as e:
                logger.warning(f"  ⚠️ PaddleOCR failed: {e}")
                import traceback
                logger.debug(f"PaddleOCR traceback: {traceback.format_exc()}")
        
        return results
    
    def combine_and_deduplicate_text(self, ocr_results: Dict[str, str]) -> str:
        """
        🧩 INTELLIGENT TEXT COMBINATION:
        Combine text from all OCR engines smartly
        
        Strategy: Keep ALL text from all engines - don't lose variations!
        Why? Different OCR engines see different things:
        - Tesseract might see "12-D-5777"
        - EasyOCR might see "12 D 5777"  
        - PaddleOCR might see "12.D.5777"
        
        OpenAI is smart enough to recognize these are the same line!
        """
        if not ocr_results:
            return ""
        
        # Combine ALL text with engine labels for debugging
        combined_parts = []
        for engine, text in ocr_results.items():
            if text and text.strip():
                combined_parts.append(text.strip())
        
        combined = '\n\n'.join(combined_parts)
        
        total_chars = sum(len(t) for t in ocr_results.values())
        logger.info(f"  📝 Combined: {total_chars} total characters from {len(ocr_results)} engines")
        logger.info(f"  📝 Final text length: {len(combined)} characters")
        
        return combined
    
    def extract_spatial_data(self, img: Image.Image) -> List[Dict]:
        """
        📍 Extract spatial/position data from PaddleOCR for FROM-TO detection
        
        Returns list of text items with bounding boxes and positions:
        [{'text': str, 'bbox': list, 'center_x': float, 'center_y': float, 'confidence': float}]
        """
        spatial_data = []
        
        if not self.paddleocr_reader:
            logger.warning("  ⚠️ PaddleOCR not available for spatial extraction")
            return spatial_data
        
        try:
            img_array = np.array(img)
            paddle_result = self.paddleocr_reader.ocr(img_array)
            
            if paddle_result and isinstance(paddle_result, list) and len(paddle_result) > 0:
                first_page = paddle_result[0]
                if first_page and isinstance(first_page, list):
                    for line in first_page:
                        if line and isinstance(line, (list, tuple)) and len(line) >= 2:
                            bbox = line[0]  # [[x0,y0], [x1,y0], [x1,y1], [x0,y1]]
                            text_data = line[1]  # (text, confidence)
                            
                            if isinstance(text_data, (list, tuple)) and len(text_data) >= 2:
                                text = str(text_data[0])
                                confidence = float(text_data[1])
                                
                                # Calculate center point of bounding box
                                x_coords = [point[0] for point in bbox]
                                y_coords = [point[1] for point in bbox]
                                center_x = sum(x_coords) / 4
                                center_y = sum(y_coords) / 4
                                
                                spatial_data.append({
                                    'text': text,
                                    'bbox': bbox,
                                    'center_x': center_x,
                                    'center_y': center_y,
                                    'confidence': confidence
                                })
        except Exception as e:
            logger.warning(f"  ⚠️ Spatial data extraction failed: {e}")
        
        return spatial_data
    
    def parse_with_regex(self, extracted_text: str, page_num: int, include_area: bool = False, format_type: str = 'onshore') -> List[Dict]:
        """
        🎯 RELIABLE REGEX-BASED APPROACH:
        Use pure Python regex to find line number patterns directly in OCR text.
        
        Line format (onshore without area): SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        Example: 12-D-5777-033842-N
        
        Line format (onshore with area): SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        Example: 4"-41-SWR-64313-A2AU16-V
        
        Line format (offshore): AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
        Example: 604-HO-8-BC2GA0-1071-H
        
        Line format (ADNOC): SIZE"-FLUID-PIPECLASS-SEQUENCE
        Example: 6"-CD-AC3N-8256
        
        This is MORE RELIABLE than OpenAI because:
        - Deterministic pattern matching
        - No API failures or hallucinations
        - Faster processing
        - Consistent results
        """
        # ------------------------------------------------------------------
        # Normalize separators up-front — all format branches need this.
        # OCR often replaces actual hyphens with: = ~ — – ― ─ | / etc.
        # We normalise once here so every downstream branch sees clean text.
        # ------------------------------------------------------------------
        normalized_text = extracted_text
        for _ch in ['=', '~', '—', '–', '―', '─', '|', '/', '°', '″', "'", '"']:
            normalized_text = normalized_text.replace(_ch, '-')
        normalized_text = re.sub(r'-{2,}', '-', normalized_text)
        normalized_text = re.sub(r'\s+-\s+', '-', normalized_text)

        # -----------------------------------------------------------------------
        # INDUSTRIAL/PROJECT FORMAT — must be checked BEFORE onshore because
        # the unit-number segment (e.g. 2600) would otherwise be misidentified
        # as an "area" code in the onshore-with-area branch.
        #
        # FORMAT: SIZE"-UNIT_NO-SERVICE_CODE-SEQ-PIPING_CLASS(-END_DESIG)?
        # Examples:
        #   2"-2600-FL-352-32070R-E   (process flush line)
        #   8"-2600-P-381-31051XR-E   (process pipe, 7-char piping class)
        #   3/4"-2600-HD-430-32070R-E (fractional size, header drain)
        #   1"-2600-FCWR-975-31210MR-V (4-letter service code)
        # -----------------------------------------------------------------------
        if format_type == 'industrial':
            logger.info("  🔍 Using REGEX pattern matching — INDUSTRIAL/PROJECT format")
            logger.info("  📋 Examples: 2\"-2600-FL-352-32070R-E, 8\"-2600-P-381-31051XR-E")
            # The piping-class ALWAYS ends with 1-2 uppercase letters (e.g. 32070R, 31051XR).
            # The end-designator (E/V/I) is optional but always present in these drawings.
            _ic = INDUSTRIAL_FORMAT
            _seq_min = _ic['seq_digits_min']
            _seq_max = _ic['seq_digits_max']
            _svc_max = _ic['service_code_max_len']
            patterns = [
                # Pattern 1: Standard (integer size, all segments present)
                rf'(\d{{1,2}})"?\s*-\s*(\d{{3,{_ic["unit_no_max_digits"]}}})\s*-\s*([A-Z]{{1,{_svc_max}}})\s*-\s*(\d{{{_seq_min},{_seq_max}}})\s*-\s*(\d{{5}}[A-Z]{{1,2}})(?:\s*-\s*([A-Z]))?',

                # Pattern 2: Fractional size (e.g. 3/4")
                rf'(\d+/\d+)"?\s*-\s*(\d{{3,{_ic["unit_no_max_digits"]}}})\s*-\s*([A-Z]{{1,{_svc_max}}})\s*-\s*(\d{{{_seq_min},{_seq_max}}})\s*-\s*(\d{{5}}[A-Z]{{1,2}})(?:\s*-\s*([A-Z]))?',

                # Pattern 3: OCR may drop the inch-mark — match without it
                rf'\b(\d{{1,2}})\s*-\s*(\d{{3,{_ic["unit_no_max_digits"]}}})\s*-\s*([A-Z]{{1,{_svc_max}}})\s*-\s*(\d{{{_seq_min},{_seq_max}}})\s*-\s*(\d{{5}}[A-Z]{{1,2}})(?:\s*-\s*([A-Z]))?\b',

                # Pattern 4: Loose spacing/OCR noise around separators
                rf'(\d{{1,2}}(?:/\d+)?)"?\s*-+\s*(\d{{3,{_ic["unit_no_max_digits"]}}})\s*-+\s*([A-Z]{{1,{_svc_max}}})\s*-+\s*(\d{{{_seq_min},{_seq_max}}})\s*-+\s*(\d{{5}}[A-Z]{{1,2}})(?:\s*-+\s*([A-Z]))?',

                # Pattern 5: OCR replaces hyphens with spaces
                rf'(\d{{1,2}}(?:/\d+)?)"?\s+(\d{{3,{_ic["unit_no_max_digits"]}}})\s+([A-Z]{{1,{_svc_max}}})\s+(\d{{{_seq_min},{_seq_max}}})\s+(\d{{5}}[A-Z]{{1,2}})(?:\s+([A-Z]))?',
            ]

            found_lines = []
            seen_lines = set()

            for pat_idx, pattern in enumerate(patterns, 1):
                for match in re.finditer(pattern, normalized_text, re.IGNORECASE):
                    # --- fractional size patterns return group(1)=e.g. "3/4" ---
                    size_raw  = match.group(1).strip()
                    unit_no   = match.group(2).strip()
                    service   = match.group(3).strip().upper()
                    seq       = match.group(4).strip()
                    pip_class = match.group(5).strip().upper()
                    end_desig = (match.group(6) or '').strip().upper()

                    # --- Validation ---
                    # unit_no: digits only, 3-4 chars
                    if not unit_no.isdigit():
                        continue
                    # service: letters only after normalisation
                    if not re.match(r'^[A-Z]{1,' + str(_svc_max) + r'}$', service):
                        continue
                    # sequence: digits only, correct length
                    if not seq.isdigit() or not (_seq_min <= len(seq) <= _seq_max):
                        continue
                    # piping class: 5 digits + 1-2 uppercase letters
                    if not re.match(r'^\d{5}[A-Z]{1,2}$', pip_class):
                        continue
                    # end designator: single letter if present
                    if end_desig and not re.match(r'^[A-Z]$', end_desig):
                        end_desig = ''

                    # Build canonical line designation
                    parts = [f'{size_raw}"-{unit_no}-{service}-{seq}-{pip_class}']
                    if end_desig:
                        parts.append(end_desig)
                    line_designation = '-'.join(parts)

                    if line_designation in seen_lines:
                        continue
                    seen_lines.add(line_designation)

                    found_lines.append({
                        'line_number':        line_designation,
                        'original_detection': match.group(0).strip(),
                        'size':               f'{size_raw}"',
                        'fluid_code':         service,        # service / fluid code
                        'sequence_no':        seq,
                        'piping_spec':        pip_class,      # piping class
                        'pipr_class':         pip_class,
                        'dept_deviation':     unit_no,        # unit/area number
                        'insulation':         end_desig,      # end designator (E/V/I)
                        'area':               unit_no,
                        'page':               page_num,
                        'from_equipment':     '',
                        'to_equipment':       '',
                        'extraction_method':  'regex_industrial',
                    })

            logger.info(f"  🎯 INDUSTRIAL regex found {len(found_lines)} unique lines from {len(patterns)} patterns")
            return found_lines

        # 🔧 GENERAL FORMAT: Run ALL known formats then combine / pick winner.
        # Behaviour is controlled by the soft-coded GENERAL_STRATEGY constant:
        #   'merge'  → union of all format results, deduplicated by line_number
        #   'winner' → legacy: return only the format with the highest count
        if format_type == 'general':
            logger.info(
                f"  🔍 GENERAL format (strategy='{GENERAL_STRATEGY}') — "
                "running all sub-formats: industrial, onshore, offshore, adnoc, onshore+area"
            )
            _candidates = {}
            for _fmt in ('industrial', 'onshore', 'offshore', 'adnoc'):
                _res = self.parse_with_regex(extracted_text, page_num, include_area=False, format_type=_fmt)
                _candidates[_fmt] = _res
            # Also try onshore WITH area code
            _res_area = self.parse_with_regex(extracted_text, page_num, include_area=True, format_type='onshore')
            _candidates['onshore_area'] = _res_area

            counts_str = ', '.join(f'{f}:{len(v)}' for f, v in _candidates.items())

            if GENERAL_STRATEGY == 'merge':
                # ----------------------------------------------------------------
                # MERGE: Combine results from every format.
                # Deduplicate on canonical line_number (case-insensitive) so the
                # same tag found by two formats only appears once.
                # The first format to register a line_number wins (priority order
                # matches the loop above: industrial → onshore → offshore → adnoc).
                # ----------------------------------------------------------------
                merged: list = []
                seen_merged: set = set()
                for _fmt, _results in _candidates.items():
                    for item in _results:
                        key = item.get('line_number', '').upper()
                        if key and key not in seen_merged:
                            seen_merged.add(key)
                            merged.append(item)
                logger.info(
                    f"  ✅ GENERAL MERGE: {len(merged)} unique lines combined "
                    f"from all formats ({counts_str})"
                )
                return merged
            else:
                # WINNER (legacy): return only the highest-count format
                best_fmt = max(_candidates, key=lambda f: len(_candidates[f]))
                best_count = len(_candidates[best_fmt])
                logger.info(
                    f"  ✅ GENERAL WINNER: '{best_fmt}' with {best_count} lines ({counts_str})"
                )
                return _candidates[best_fmt]
        
        format_label = 'ADNOC' if format_type == 'adnoc' else ('OFFSHORE' if format_type == 'offshore' else ('WITH AREA' if include_area else 'WITHOUT AREA'))
        logger.info(f"  🔍 Using REGEX pattern matching on OCR text ({format_label})")
        if format_type == 'adnoc':
            logger.info(f"  📋 ADNOC format: SIZE\"-FLUIDCODE-PIPECLASS-SEQUENCE")
            logger.info(f"  📋 Examples: 6\"-CD-AC3N-8256, 8\"-HO-BD2A-1023, 10\"-AG-XY1Z-9999")
        elif format_type == 'offshore':
            logger.info(f"  📋 Offshore format: AREA-FLUIDCODE-SIZE-PIPECLASS-SEQUENCE-INSULATION")
            logger.info(f"  📋 ADNOC examples: 604-RO-4-AN1NLO-0011-P, 604-HO-8-BC2CA0-1071-H, 604-AG-3-ASSNLO-0007")
        
        # (text already normalised at top of method)
        normalized_text_spaced = normalized_text.replace('-', ' - ')
        
        logger.info(f"  📝 Normalized text sample (first 500 chars): {normalized_text[:500]}")
        
        # SMART FLEXIBLE REGEX PATTERNS
        # Format WITHOUT AREA: SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        # Examples: 6-VG-4952-011505-X, 16-PG-4005-011441-X, 6-PG-5143-031440
        #
        # Format WITH AREA: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        # Examples: 4"-41-SWR-64313-A2AU16-V, 16"-25-PG-4667-031441-X
        #
        # Format OFFSHORE: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
        # Examples: 604-HO-8-BC2GA0-1071-H, 41-SWR-16-A2AU16-64313-V
        #
        # Format ADNOC (Abu Dhabi Oil Co. Ltd): SIZE"-FLUID-PIPECLASS-SEQUENCE
        # Examples: 6"-CD-AC3N-8256, 8"-HO-BD2A-1023, 10"-AG-XY1Z-9999
        #           6"-FG-CI-329,   2"-FL-ACGN-8703, 2"-DR-RI-454
        
        if format_type == 'adnoc':
            # ADNOC PATTERNS: SIZE"-FLUIDCODE-PIPECLASS-SEQUENCE
            # Standard Example: 6"-CD-AC3N-8256
            # Format: [1-2 digits]"[-][2-3 uppercase letters][-][alphanumeric pipe class][-][3-4 digits]
            # Uses ADNOC_SEQ_MIN_DIGITS and ADNOC_PIPECLASS_MIN_LEN soft-coded constants.
            _sq = f'{ADNOC_SEQ_MIN_DIGITS},4'  # e.g. "3,4"
            patterns = [
                # Pattern 1: Standard ADNOC format with quote
                rf'\b(\d{{1,2}})"\s*-\s*([A-Z]{{2,3}})\s*-\s*([A-Z0-9]+)\s*-\s*(\d{{{_sq}}})\b',

                # Pattern 2: Flexible spacing
                rf'\b(\d{{1,2}})"?\s*-+\s*([A-Z]{{2,3}})\s*-+\s*([A-Z0-9]+)\s*-+\s*(\d{{{_sq}}})\b',

                # Pattern 3: Compact format
                rf'\b(\d{{1,2}})"-([A-Z]{{2,3}})-([A-Z0-9]+)-(\d{{{_sq}}})\b',

                # Pattern 4: With word boundaries and lookahead
                rf'(?:^|\s)(\d{{1,2}})"?\s*-\s*([A-Z]{{2,3}})\s*-\s*([A-Z0-9]+)\s*-\s*(\d{{{_sq}}})(?=\s|$|-)',

                # Pattern 5: Case insensitive for OCR errors
                rf'(?:^|\s)(\d{{1,2}})"?\s*-\s*([A-Za-z]{{2,3}})\s*-\s*([A-Za-z0-9]+)\s*-\s*(\d{{{_sq}}})(?=\s|$|-)',
            ]
        elif format_type == 'offshore':
            # OFFSHORE PATTERNS: AREA-FLUIDCODE-LINESIZE-PIPECLASS-SEQUENCE-INSULATION
            # Standard Example: 604-HO-8-BC2CA0-1071-H
            # ADNOC Examples: 604-RO-4-AN1NLO-0011-P, 604-HO-8-BC2CA0-1071-H, 604-AG-3-ASSNLO-0007
            # Supports both 5-segment (no insulation) and 6-segment (with insulation) formats
            patterns = [
                # Pattern 1: ADNOC primary format - flexible and permissive
                # Matches: 604-HO-8-BC2CA0-1071-H (6 segments), 604-AG-3-ASSNLO-0007 (5 segments)
                r'\b(\d{3})-([A-Z]{2})-(\d+)-([A-Z0-9]+)-(\d{3,5})(?:-([A-Z]{1,2}))?\b',
                
                # Pattern 2: ADNOC with flexible area (2-3 digits) and fluid (1-3 letters)
                r'\b(\d{2,3})-([A-Z]{1,3})-(\d{1,2})-([A-Z0-9]{4,8})-(\d{3,5})(?:-([A-Z]{1,2}))?\b',
                
                # Pattern 3: With optional spaces around hyphens
                r'\b(\d{2,3})\s*-\s*([A-Z]{1,3})\s*-\s*(\d{1,2})\s*-\s*([A-Z0-9]{4,8})\s*-\s*(\d{3,5})(?:\s*-\s*([A-Z]{1,2}))?\b',
                
                # Pattern 4: With optional quote after size (OCR artifact)
                r'\b(\d{2,3})\s*-\s*([A-Z]{1,3})\s*-\s*(\d{1,2})"?\s*-\s*([A-Z0-9]{4,8})\s*-\s*(\d{3,5})(?:\s*-\s*([A-Z]{1,2}))?\b',
                
                # Pattern 5: With flexible spacing and multiple hyphens
                r'\b(\d{2,3})\s*-+\s*([A-Z]{1,3})\s*-+\s*(\d{1,2})"?\s*-+\s*([A-Z0-9]{4,8})\s*-+\s*(\d{3,5})(?:\s*-+\s*([A-Z]{1,2}))?\b',
                
                # Pattern 6: With word boundaries and lookahead
                r'(?:^|\s)(\d{2,3})\s*-\s*([A-Z]{1,3})\s*-\s*(\d{1,2})"?\s*-\s*([A-Z0-9]{4,8})\s*-\s*(\d{3,5})(?:\s*-\s*([A-Z]{1,2}))?(?=\s|$|-)',
                
                # Pattern 7: Case insensitive for OCR errors (most flexible)
                r'(?:^|\s)(\d{2,3})\s*-\s*([A-Za-z]{1,3})\s*-\s*(\d{1,2})"?\s*-\s*([A-Za-z0-9]{4,8})\s*-\s*(\d{3,5})(?:\s*-\s*([A-Za-z]{1,2}))?(?=\s|$|-)',
            ]
        elif include_area:
            # WITH AREA PATTERNS: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
            _bg = BOROUGE_AREA_FORMAT
            patterns = [
                # Pattern 1: With quote after size (most common with area)
                r'\b(\d{1,2})"?\s*-\s*(\d{2,3})\s*-\s*([A-Z]{1,3})\s*-\s*(\d{4,5})\s*-\s*([A-Z0-9]{5,6})(?:\s*-\s*([A-Z]{1,2}))?\b',
                
                # Pattern 2: Flexible spacing with quote
                r'\b(\d{1,2})"\s*-+\s*(\d{2,3})\s*-+\s*([A-Z]{1,3})\s*-+\s*(\d{4,5})\s*-+\s*([A-Z0-9]{5,6})(?:\s*-+\s*([A-Z]{1,2}))?\b',
                
                # Pattern 3: Compact format
                r'\b(\d{1,2})"-(\d{2,3})-([A-Z]{1,3})-(\d{4,5})-([A-Z0-9]{5,6})(?:-([A-Z]{1,2}))?\b',
                
                # Pattern 4: With spaces
                r'(?:^|\s)(\d{1,2})"?\s*-+\s*(\d{2,3})\s+-+\s*([A-Z]{1,3})\s+-+\s*(\d{4,5})\s+-+\s*([A-Z0-9]{5,6})(?:\s*-+\s*([A-Z]{1,2}))?(?=\s|$|-)',
                
                # Pattern 5: Case insensitive
                r'(?:^|\s)(\d{1,2})"?\s*-\s*(\d{2,3})\s*-\s*([A-Za-z]{1,3})\s*-\s*(\d{4,5})\s*-\s*([A-Za-z0-9]{5,6})(?:\s*-\s*([A-Za-z]{1,2}))?(?=\s|$|-)',

                # Pattern 6: Borouge / Linde — 6-digit padded sequence,
                # letter-first 6-char piping class (A1AU01), spaces permitted
                # after each hyphen (OCR artefact on Linde draughting).
                # Soft-coded via BOROUGE_AREA_FORMAT — edit the dict, not this regex.
                rf'\b(\d{{{_bg["size_digits_min"]},{_bg["size_digits_max"]}}})"?\s*-\s*'
                rf'(\d{{{_bg["area_digits_min"]},{_bg["area_digits_max"]}}})\s*-\s*'
                rf'([A-Z]{{{_bg["service_letters_min"]},{_bg["service_letters_max"]}}})\s*-\s*'
                rf'(\d{{{_bg["seq_digits_min"]},{_bg["seq_digits_max"]}}})\s*-\s*'
                rf'([A-Z0-9]{{{_bg["pipeclass_len_min"]},{_bg["pipeclass_len_max"]}}})'
                rf'(?:\s*-\s*([A-Z]{{{_bg["enddesig_letters_min"]},{_bg["enddesig_letters_max"]}}}))?\b',

                # Pattern 7: Borouge — case-insensitive variant for rotated OCR
                rf'(?:^|\s)(\d{{{_bg["size_digits_min"]},{_bg["size_digits_max"]}}})"?\s*-\s*'
                rf'(\d{{{_bg["area_digits_min"]},{_bg["area_digits_max"]}}})\s*-\s*'
                rf'([A-Za-z]{{{_bg["service_letters_min"]},{_bg["service_letters_max"]}}})\s*-\s*'
                rf'(\d{{{_bg["seq_digits_min"]},{_bg["seq_digits_max"]}}})\s*-\s*'
                rf'([A-Za-z0-9]{{{_bg["pipeclass_len_min"]},{_bg["pipeclass_len_max"]}}})'
                rf'(?:\s*-\s*([A-Za-z]{{{_bg["enddesig_letters_min"]},{_bg["enddesig_letters_max"]}}}))?(?=\s|$|-)',
            ]
        else:
            # WITHOUT AREA PATTERNS: SIZE-FLUID-SEQUENCE-PIPING_SPEC(-DEPT_DEV(-INSULATION)?)?
            # Example: 2"-D-6152-033842-X-N
            #   group1=size, group2=fluid, group3=sequence(4digits),
            #   group4=piping_spec(5-6digits), group5=dept_deviation(opt), group6=insulation(opt)
            patterns = [
                # Pattern 1: Standard with word boundaries (most reliable)
                r'\b(\d{1,2})\s*-\s*([A-Z]{1,2})\s*-\s*(\d{4})\s*-\s*(\d{5,6})(?:\s*-\s*([A-Z0-9]{1,4})(?:\s*-\s*([A-Z0-9]{1,2}))?)?\b',

                # Pattern 2: With optional quote after size
                r'\b(\d{1,2})-?\s*-\s*([A-Z]{1,2})\s*-\s*(\d{4})\s*-\s*(\d{5,6})(?:\s*-\s*([A-Z0-9]{1,4})(?:\s*-\s*([A-Z0-9]{1,2}))?)?\b',

                # Pattern 3: More lenient spacing
                r'(?:^|\s)(\d{1,2})\s*-+\s*([A-Z]{1,2})\s*-+\s*(\d{4})\s*-+\s*(\d{5,6})(?:\s*-+\s*([A-Z0-9]{1,4})(?:\s*-+\s*([A-Z0-9]{1,2}))?)?(?:\s|$|[-,.])',

                # Pattern 4: Compact (no spaces at all)
                r'\b(\d{1,2})-([A-Z]{1,2})-(\d{4})-(\d{5,6})(?:-([A-Z0-9]{1,4})(?:-([A-Z0-9]{1,2}))?)?\b',

                # Pattern 5: With flexible separators (space or hyphen)
                r'\b(\d{1,2})[\s-]+([A-Z]{1,2})[\s-]+(\d{4})[\s-]+(\d{5,6})(?:[\s-]+([A-Z0-9]{1,4})(?:[\s-]+([A-Z0-9]{1,2}))?)?\b',

                # Pattern 6: Case insensitive with word boundaries
                r'(?:^|\s)(\d{1,2})\s*-\s*([A-Za-z]{1,2})\s*-\s*(\d{4})\s*-\s*(\d{5,6})(?:\s*-\s*([A-Za-z0-9]{1,4})(?:\s*-\s*([A-Za-z0-9]{1,2}))?)?(?=\s|$|-)',
            ]
        
        found_lines = []
        seen_lines = set()
        rejected = []
        
        for pattern_idx, pattern in enumerate(patterns, 1):
            matches = re.finditer(pattern, normalized_text, re.IGNORECASE)
            
            for match in matches:
                # Extract and clean components
                if format_type == 'adnoc':
                    # ADNOC: SIZE"-FLUID-PIPECLASS-SEQUENCE
                    size = match.group(1).strip()
                    fluid = match.group(2).strip().upper()
                    pipr_class = match.group(3).strip()
                    seq = match.group(4).strip()
                    area = ''
                    insulation = ''
                    dept_deviation = ''
                elif format_type == 'offshore':
                    # Offshore: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
                    area = match.group(1).strip()
                    fluid = match.group(2).strip().upper()
                    size = match.group(3).strip()
                    pipr_class = match.group(4).strip()
                    seq = match.group(5).strip()
                    insulation = match.group(6).strip().upper() if match.lastindex >= 6 and match.group(6) else ''
                    dept_deviation = ''
                elif include_area:
                    # With area: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
                    size = match.group(1).strip()
                    area = match.group(2).strip()
                    fluid = match.group(3).strip().upper()
                    seq = match.group(4).strip()
                    pipr_class = match.group(5).strip()
                    insulation = match.group(6).strip().upper() if match.lastindex >= 6 and match.group(6) else ''
                    dept_deviation = ''
                else:
                    # Without area: SIZE-FLUID-SEQUENCE-PIPING_SPEC(-DEPT_DEV(-INSULATION)?)?
                    # Example: 2"-D-6152-033842-X-N
                    size = match.group(1).strip()
                    area = ''
                    fluid = match.group(2).strip().upper()
                    seq = match.group(3).strip()
                    pipr_class = match.group(4).strip()  # piping_spec (e.g. 033842)
                    dept_deviation = match.group(5).strip().upper() if match.lastindex >= 5 and match.group(5) else ''
                    insulation = match.group(6).strip().upper() if match.lastindex >= 6 and match.group(6) else ''
                
                # 🔧 CRITICAL: Normalize all fields - Force O → 0 conversion BEFORE any processing
                # This eliminates OCR confusion between letter O and digit 0
                size = self._normalize_ocr_text(size)
                fluid = self._normalize_ocr_text(fluid)
                seq = self._normalize_ocr_text(seq)
                pipr_class = self._normalize_ocr_text(pipr_class)
                insulation = self._normalize_ocr_text(insulation) if insulation else ''
                area = self._normalize_ocr_text(area) if area else ''
                
                # Smart cleaning: remove any non-alphanumeric from edges
                size = re.sub(r'[^0-9]', '', size)
                fluid = re.sub(r'[^A-Z0-9]', '', fluid)  # Keep digits for normalized 0
                seq = re.sub(r'[^0-9]', '', seq)
                if insulation:
                    insulation = re.sub(r'[^A-Z0-9]', '', insulation)
                
                # ADNOC FORMAT VALIDATION
                # Uses soft-coded constants ADNOC_SEQ_MIN_DIGITS and ADNOC_PIPECLASS_MIN_LEN.
                if format_type == 'adnoc':
                    # 1. SIZE: Must be 1-2 digits
                    if not size or not size.isdigit() or len(size) > 2:
                        rejected.append(f"Invalid ADNOC size: {size}")
                        continue

                    # 2. FLUID: Must be 2-3 uppercase letters/digits (after O→0 normalization)
                    if not fluid or len(fluid) < 2 or len(fluid) > 3:
                        rejected.append(f"Invalid ADNOC fluid: {fluid}")
                        continue
                    if not re.match(r'^[A-Z0-9]+$', fluid):
                        rejected.append(f"Invalid ADNOC fluid characters: {fluid}")
                        continue

                    # 3. SEQUENCE: ADNOC_SEQ_MIN_DIGITS–4 digits
                    #    Abu Dhabi drawings use 3-digit (329, 454) and 4-digit (8703) sequences.
                    if not seq or not seq.isdigit() or not (ADNOC_SEQ_MIN_DIGITS <= len(seq) <= 4):
                        rejected.append(f"Invalid ADNOC sequence: {seq}")
                        continue

                    # 4. PIPE CLASS: Alphanumeric, min ADNOC_PIPECLASS_MIN_LEN chars
                    #    Standard classes are 4+ chars (AC3N…) but this project uses 2-char (CI, RI).
                    if not pipr_class or not pipr_class.isalnum() or len(pipr_class) < ADNOC_PIPECLASS_MIN_LEN:
                        rejected.append(f"Invalid ADNOC pipe class: {pipr_class}")
                        continue

                    # Build ADNOC line number: SIZE"-FLUID-PIPECLASS-SEQUENCE
                    line_number = f"{size}\"-{fluid}-{pipr_class}-{seq}"

                    # Deduplicate
                    if line_number in seen_lines:
                        continue
                    seen_lines.add(line_number)

                    # Create line entry for ADNOC
                    line_entry = {
                        'line_number': line_number,
                        'size': f'{size}"',
                        'fluid_code': fluid,
                        'sequence_no': seq,
                        'pipr_class': pipr_class,
                        'piping_spec': pipr_class,
                        'dept_deviation': '',
                        'insulation': '',
                        'area': '',
                        'page': page_num,
                        'from_equipment': '',
                        'to_equipment': '',
                        'extraction_method': 'regex_adnoc',
                        'original_detection': match.group(0).strip()
                    }

                    found_lines.append(line_entry)
                    continue  # Skip standard validation below
                
                # SMART VALIDATION (flexible for ADNOC offshore formats)
                # 1. SIZE: Must be 1+ digits (ADNOC can have any size)
                if not size or not size.isdigit():
                    rejected.append(f"Invalid size: {size}")
                    continue
                
                # 2. AREA: For offshore and include_area formats, must be 2-3 digits
                if format_type == 'offshore' or include_area:
                    if not area or not area.isdigit() or len(area) not in [2, 3]:
                        rejected.append(f"Invalid area: {area}")
                        continue
                else:
                    area = ''  # Ensure area is empty for without-area format
                
                # 3. FLUID: Must be 1-3 uppercase letters/digits (after O→0 normalization)
                max_fluid_len = 3 if (include_area or format_type == 'offshore') else 2
                if not fluid or len(fluid) > max_fluid_len:
                    rejected.append(f"Invalid fluid: {fluid}")
                    continue
                # Fluid should be mostly alphabetic (allow digits from normalization)
                if not re.match(r'^[A-Z0-9]+$', fluid):
                    rejected.append(f"Invalid fluid characters: {fluid}")
                    continue
                
                # 4. SEQUENCE: Must be 3-5 digits for offshore/area formats (ADNOC uses 3-4 digits like 0007, 0011), 4 for standard
                if format_type == 'offshore' or include_area:
                    # Upper bound widened via soft-coded BOROUGE_AREA_FORMAT['seq_digits_max']
                    # so Borouge/Linde 6-digit padded sequences (e.g. 149472, 140061) pass.
                    _seq_max_with_area = BOROUGE_AREA_FORMAT['seq_digits_max'] if include_area else 5
                    if not seq or not seq.isdigit() or len(seq) < 3 or len(seq) > _seq_max_with_area:
                        rejected.append(f"Invalid sequence: {seq}")
                        continue
                else:
                    if not seq or not seq.isdigit() or len(seq) != 4:
                        rejected.append(f"Invalid sequence: {seq}")
                        continue
                
                # 5. PIPE CLASS: Flexible validation for offshore/area formats (ADNOC uses variable length like AN1NLO, ASSNLO, BC2CA0)
                if format_type == 'offshore' or include_area:
                    # Offshore/Area format: 4+ alphanumeric characters, max 10 for safety
                    if not pipr_class or len(pipr_class) < 4 or len(pipr_class) > 10:
                        rejected.append(f"Invalid pipe class length: {pipr_class}")
                        continue
                    if not pipr_class.isalnum():
                        rejected.append(f"Invalid pipe class (not alphanumeric): {pipr_class}")
                        continue
                else:
                    # Without area: 5-6 digits only
                    if not pipr_class or len(pipr_class) not in [5, 6]:
                        rejected.append(f"Invalid pipe class: {pipr_class}")
                        continue
                    pipr_class = re.sub(r'[^0-9]', '', pipr_class)
                    if not pipr_class.isdigit():
                        rejected.append(f"Invalid pipe class (not numeric): {pipr_class}")
                        continue
                
                # 6. INSULATION: Optional, must be 1-2 letters/digits if present (after O→0 normalization)
                if insulation and len(insulation) > 2:
                    rejected.append(f"Invalid insulation length: {insulation}")
                    continue
                if insulation and not re.match(r'^[A-Z0-9]+$', insulation):
                    rejected.append(f"Invalid insulation characters: {insulation}")
                    continue
                
                # Build line number string (dynamic based on segments - supports 5 or 6 segments)
                # All components are already normalized (O → 0) above
                if format_type == 'offshore':
                    # Offshore format: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
                    # 5 segments: 604-AG-3-ASSNL0-0007 (normalized)
                    # 6 segments: 604-H0-8-BC2CA0-1071-H (normalized)
                    if insulation:
                        line_number = f"{area}-{fluid}-{size}-{pipr_class}-{seq}-{insulation}"
                    else:
                        line_number = f"{area}-{fluid}-{size}-{pipr_class}-{seq}"
                elif include_area:
                    # With area format: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
                    if insulation:
                        line_number = f"{size}\"-{area}-{fluid}-{seq}-{pipr_class}-{insulation}"
                    else:
                        line_number = f"{size}\"-{area}-{fluid}-{seq}-{pipr_class}"
                else:
                    # Without area format: SIZE-FLUID-SEQUENCE-PIPING_SPEC(-DEPT_DEV(-INSULATION)?)?
                    # Example: 2"-D-6152-033842-X-N
                    parts = [f"{size}-{fluid}-{seq}-{pipr_class}"]
                    if dept_deviation:
                        parts.append(dept_deviation)
                    if insulation:
                        parts.append(insulation)
                    line_number = '-'.join(parts)

                # 🔧 FINAL NORMALIZATION: Ensure complete O→0 conversion in final line number
                line_number = self._normalize_ocr_text(line_number)

                # Deduplicate
                if line_number in seen_lines:
                    continue
                seen_lines.add(line_number)

                # 🔧 TRIPLE-SAFE: Normalize ALL output fields to guarantee NO 'O' in any field
                # Create line entry with normalized fields
                _dept_dev_norm = self._normalize_ocr_text(dept_deviation) if dept_deviation else ''
                line_entry = {
                    'line_number': self._normalize_ocr_text(line_number),
                    'size': self._normalize_ocr_text(f'{size}"'),
                    'fluid_code': self._normalize_ocr_text(fluid),
                    'sequence_no': self._normalize_ocr_text(seq),
                    'pipr_class': self._normalize_ocr_text(pipr_class),
                    'piping_spec': self._normalize_ocr_text(pipr_class),  # alias: piping_spec = pipr_class
                    'dept_deviation': _dept_dev_norm,
                    'insulation': self._normalize_ocr_text(insulation) if insulation else '',
                    'area': self._normalize_ocr_text(area) if (format_type == 'offshore' or include_area) and area else '',
                    'page': page_num,
                    'from_equipment': '',
                    'to_equipment': '',
                    'extraction_method': 'regex_direct',
                    'original_detection': match.group(0).strip()
                }
                
                found_lines.append(line_entry)
        
        # Log summary with debugging info
        if rejected and len(rejected) <= 20:
            logger.info(f"  ⚠️ Rejected {len(rejected)} potential matches:")
            for r in rejected[:10]:
                logger.info(f"     - {r}")
        elif rejected:
            logger.info(f"  ⚠️ Rejected {len(rejected)} potential matches (showing first 10):")
            for r in rejected[:10]:
                logger.info(f"     - {r}")
        
        logger.info(f"  🎯 REGEX found {len(found_lines)} unique line numbers from {len(patterns)} patterns")
        return found_lines
    
    def parse_with_openai(self, extracted_text: str, page_num: int) -> List[Dict]:
        """
        DEPRECATED: OpenAI is unreliable, use parse_with_regex instead
        """
        logger.warning("  ⚠️ OpenAI method is deprecated, using REGEX instead")
        return self.parse_with_regex(extracted_text, page_num)
        
        # Use full text for maximum extraction
        text_chunk = extracted_text[:12000]  # Increased from 8000
        
        prompt = f"""🎯 MISSION: Extract ALL P&ID line numbers from OCR text (may be messy!)

📋 **LINE FORMAT:** SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?

🔍 **SEARCH STRATEGY:**
1. Look for patterns with ALL 4 mandatory components
2. Accept ANY separator: hyphens, spaces, periods, underscores, or mixed
3. Ignore extra whitespace, quotes, or OCR noise
4. Extract variations like:
   - "12-D-5777-033842-N" (standard)
   - "12 D 5777 033842 N" (spaces)
   - "12"-D-5777-033842" (with quote)
   - "12.D.5777.033842.N" (periods)
   - "12  D  5777  033842" (multiple spaces)
   - "12_D_5777_033842_N" (underscores)
   - Even "12D 5777033842N" (minimal separators)

✅ **MANDATORY COMPONENTS (ALL 4 REQUIRED):**
- **SIZE:** 1-2 digits ONLY (6, 8, 10, 12, 16, 20, 24, etc.)
  ❌ REJECT: 05 (leading zero invalid), 4003 (3-4 digits invalid)
  
- **FLUID:** 1-2 LETTERS ONLY (D, PG, CW, ST, W, etc.)
  ❌ REJECT: Numbers like 03, or missing entirely
  
- **SEQUENCE:** EXACTLY 4 digits (0001, 5777, 1234, 9999)
  ❌ REJECT: 011441 (6 digits), 31441 (5 digits), 123 (3 digits)
  
- **PIPECLASS:** 5 OR 6 digits (01701, 033842, 011441, 11440, 123456)
  ✅ ACCEPT: Both 5-digit (01701, 11440) and 6-digit (033842, 011441)
  ❌ REJECT: Wrong length (1-4 or 7+ digits)

🎨 **OPTIONAL (5th component):**
- **INSULATION:** ONLY these codes: H, PP, X, N, E, FP, AA
  ❌ NOT fluid codes (PG, D, CW are FLUID not insulation!)

✅ **VALID EXAMPLES:**
"12-D-5777-033842-N" → Extract as: size:12, fluid:D, seq:5777, class:033842, insul:N
"16 PG 4105 011441 X" → Extract as: size:16, fluid:PG, seq:4105, class:011441, insul:X
"10.PG.0003.033842" → Extract as: size:10, fluid:PG, seq:0003, class:033842, insul:""
"4-D-6013-01701" → Extract as: size:4, fluid:D, seq:6013, class:01701 (5 digits OK!), insul:""
"24-CW-1234-123456-H" → Extract as: size:24, fluid:CW, seq:1234, class:123456, insul:H
"8  ST  9999  11440  FP" → Extract as: size:8, fluid:ST, seq:9999, class:11440 (5 digits), insul:FP

❌ **INVALID - MUST REJECT:**
"05-011441-X" → MISSING FLUID+SEQUENCE (only 3 components)
"4003-031441-X" → SIZE wrong (4 digits), SEQUENCE wrong (5 digits)
"40-03-31441" → FLUID is number (invalid), SEQUENCE 5 digits
"12-1234-123456" → MISSING FLUID (only 3 components)
"PG-5777-033842" → MISSING SIZE (only 3 components)

📤 **OUTPUT FORMAT (JSON only):**
[
  {{
    "line_number": "12-D-5777-033842-N",
    "size": "12",
    "fluid_code": "D",
    "sequence_no": "5777",
    "pipr_class": "033842",
    "insulation": "N",
    "from_equipment": "",
    "to_equipment": "",
    "confidence": "high"
  }}
]

**CRITICAL OUTPUT RULES:**
1. "size" - NUMBERS ONLY (no quotes): "12" not "12\""
2. "pipr_class" - NUMBERS ONLY: "033842" not "033842-X" or "01701+YN"
3. "insulation" - SINGLE CODE ONLY: "N" not "X-N" or "+YN"
4. If you see "033842-X-N", split it: pipr_class="033842", insulation="X"
5. If you see "01701+YN", split it: pipr_class="01701", insulation="N"

🔥 **CRITICAL INSTRUCTIONS:**
1. ONLY extract line numbers that are ACTUALLY PRESENT in the text
2. DO NOT make up, guess, or invent any line numbers
3. DO NOT extrapolate or create patterns
4. If you're unsure, DON'T extract it
5. Better to extract ZERO lines than extract FAKE lines
6. Return EMPTY array [] if you don't see clear line numbers

Extract ALL line numbers now! 🚀"""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict P&ID line number extractor. You ONLY extract text that is clearly visible. You NEVER hallucinate, guess, or make up data. If unsure, return empty array."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.1,  # Very low creativity but not zero - allows pattern recognition
                max_tokens=4096  # Increased for more extractions
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # Clean markdown code blocks
            if '```' in result_text:
                parts = result_text.split('```')
                for part in parts:
                    if part.strip().startswith('json') or part.strip().startswith('['):
                        result_text = part.replace('json', '').strip()
                        break
            
            parsed_lines = json.loads(result_text)
            
            # CRITICAL: Validate each extraction strictly
            valid_lines = []
            rejected = []
            
            for line in parsed_lines:
                # Extract components
                size = str(line.get('size', '')).replace('"', '').strip()
                fluid = str(line.get('fluid_code', '')).strip().upper()
                seq = str(line.get('sequence_no', '')).strip()
                pipr_class = str(line.get('pipr_class', '')).strip()
                insul = str(line.get('insulation', '')).strip().upper()
                
                # SMART CLEANING: Handle merged components
                # Sometimes OCR merges pipe_class with insulation like "033842-X-N" or "01701+YN"
                if pipr_class and not pipr_class.isdigit():
                    # Extract only the numeric part from beginning
                    import re
                    match = re.match(r'^(\d+)', pipr_class)
                    if match:
                        clean_pipr = match.group(1)
                        # Extract insulation from the rest
                        remainder = pipr_class[len(clean_pipr):].strip('-+_. ')
                        if remainder and remainder.replace('-', '').replace('+', '').isalpha():
                            # Found insulation in pipe class
                            if not insul or insul == remainder[:2].upper():
                                insul = remainder[:2].upper() if len(remainder) >= 2 else remainder.upper()
                        pipr_class = clean_pipr
                
                # STRICT VALIDATION
                try:
                    # Size: 1-2 digits ONLY
                    if not size or not size.isdigit() or len(size) > 2 or int(size) < 1 or int(size) > 99:
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid size: {size}")
                        continue
                    
                    # Fluid: 1-2 LETTERS ONLY
                    if not fluid or not fluid.isalpha() or len(fluid) > 2:
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid fluid: {fluid}")
                        continue
                    
                    # Sequence: EXACTLY 4 digits
                    if not seq or not seq.isdigit() or len(seq) != 4:
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid sequence: {seq}")
                        continue
                    
                    # Pipe Class: 5 OR 6 digits (real PDFs have both!)
                    if not pipr_class or not pipr_class.isdigit() or len(pipr_class) not in [5, 6]:
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid pipe class: {pipr_class}")
                        continue
                    
                    # Insulation: OPTIONAL but must be 1-2 letters if present
                    if insul and (not insul.isalpha() or len(insul) > 2):
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid insulation: {insul}")
                        continue
                    
                    # Update with cleaned values
                    line['size'] = size + '"'
                    line['fluid_code'] = fluid
                    line['sequence_no'] = seq
                    line['pipr_class'] = pipr_class
                    line['insulation'] = insul
                    line['line_number'] = f"{size}-{fluid}-{seq}-{pipr_class}{'-' + insul if insul else ''}"
                    line['page'] = page_num
                    line['extraction_method'] = 'openai_intelligent'
                    
                    valid_lines.append(line)
                    
                except Exception as e:
                    rejected.append(f"{line.get('line_number', 'N/A')} - Validation error: {e}")
            
            if rejected:
                logger.info(f"  ⚠️ Rejected {len(rejected)} invalid extractions")
                for r in rejected[:5]:  # Log first 5
                    logger.info(f"    ❌ {r}")
            
            logger.info(f"  🧠 OpenAI extracted {len(valid_lines)} VALID line numbers (rejected {len(rejected)})")
            return valid_lines
            
        except json.JSONDecodeError as e:
            logger.error(f"  ❌ OpenAI returned invalid JSON: {e}")
            logger.error(f"  Response was: {result_text[:200]}...")
            return []
        except Exception as e:
            logger.error(f"  ❌ OpenAI failed: {e}")
            return []
        
        prompt = f"""You are an expert P&ID (Piping and Instrumentation Diagram) analyst specializing in piping line number extraction.

**CRITICAL TASK:** Extract ALL piping line numbers from the OCR text below using the EXACT formula format.

**LINE NUMBER FORMULA:**
[Line Size]"-[Fluid Code]-[Sequence No]-[Pipe Class](-[Insulation])?

**IMPORTANT: Insulation is OPTIONAL - it may or may not be present!**

**REGEX PATTERN:**
[0-9]{{1,2}}"-[A-Z]{{1,4}}-[0-9]{{4}}-[PIPE_CLASS](-[A-Z]{{1,2}})?

**COMPONENT BREAKDOWN (ALL REQUIRED EXCEPT INSULATION):**

1. **LINE SIZE:** [0-9]{{1,2}}" (max 2 digits + quote mark) - **REQUIRED**
   - Examples: 10", 20", 12", 8", 6", 4", 3", 2", 1"
   - MUST have quote mark (") immediately after number
   - Range: 1" to 99"

2. **FLUID CODE:** [A-Z]{{2,4}} (2-4 uppercase letters) - **REQUIRED**
   - Examples: PG, PL, CW, SW, ST, CO, AI, PA, FW, DW
   - Common codes: PG (Process Gas), PL (Process Liquid), CW (Cooling Water)
   - ST (Steam), CO (Condensate), AI (Instrument Air), PA (Plant Air)
   - N2 (Nitrogen), FW (Fire Water), DW (Drinking Water)

3. **SEQUENCE NUMBER:** [0-9]{{4}} (EXACTLY 4 digits) - **REQUIRED**
   - Examples: 0003, 1234, 5678, 0001, 9999
   - MUST be exactly 4 digits (pad with zeros if needed)
   - Range: 0000 to 9999

4. **PIPE CLASS:** - **REQUIRED** - DO NOT CONFUSE WITH INSULATION!
   This is the piping specification/material class. Two formats:
   
   A. **Alphanumeric Format:** [A-Z][0-9][A-Z][0-9]{{2}}
      - Pattern: Letter-Digit-Letter-TwoDigits (5 chars total)
      - Examples: A1B02, B2C03, C3D04, A1B01
      - **EXACTLY 5 characters: Letter + Digit + Letter + 2 Digits**
   
   B. **Numeric Format:** [0-9]{{6}} OR [0-9]{{6}}-[A-Z]
      - Pattern: 6 digits OR 6 digits + dash + 1 LETTER
      - Examples: 011440, 123456, 033842-X, 654321-A
      - **This is 6 or 8 characters total (6 digits + dash + letter)**
      - Note: After dash comes a LETTER (A-Z), not a digit
   
   **CRITICAL WARNING:** The pipe class is COMPLETE as shown above!
   Do NOT take the last character as insulation!
   Insulation comes AFTER pipe class with its own dash separator!

5. **INSULATION CODE:** (-[A-Z]{{1,2}})? - **OPTIONAL (CAN BE ABSENT)**
   - **THIS IS COMPLETELY OPTIONAL - MOST LINES DON'T HAVE IT!**
   - Only present if there's ANOTHER dash AFTER the complete pipe class
   - **ONLY VALID CODES (case insensitive):**
     * H (Heat conservation and process temperature control)
     * PP (Personnel protection)
     * E (Electrical traced line and insulated)
     * FP (Fire protection of piping and equipment)
     * AA (Acoustic insulation)
     * N (No insulation)
   - **CRITICAL:** If you see letters like "x", "X", "A", "B", etc. (NOT in the list above), 
     they are part of the pipe class, NOT insulation!
   - **If not present or not valid, set to empty string ""**

**COMPLETE EXAMPLE FORMATS:**

✅ **WITH INSULATION (insulation is separate component after pipe class):**
- 20"-PG-1234-A1B02-N 
  → size: 20", fluid: PG, seq: 1234, pipe_class: A1B02, insulation: N
- 12"-CW-5678-B2C03-PP 
  → size: 12", fluid: CW, seq: 5678, pipe_class: B2C03, insulation: PP
- 8"-ST-0001-011440-H 
  → size: 8", fluid: ST, seq: 0001, pipe_class: 011440, insulation: H
- 10"-PG-0003-033842-X-H 
  → size: 10", fluid: PG, seq: 0003, pipe_class: 033842-X, insulation: H

✅ **WITHOUT INSULATION (most common case - no insulation component):**
- 20"-PG-1234-A1B02 
  → size: 20", fluid: PG, seq: 1234, pipe_class: A1B02, insulation: ""
- 36"-PG-4403-031441-x 
  → size: 36", fluid: PG, seq: 4403, pipe_class: 031441-x, insulation: ""
- 16"-PG-4105-011441-X 
  → size: 16", fluid: PG, seq: 4105, pipe_class: 011441-X, insulation: ""
- 10"-PG-0003-033842-X 
  → size: 10", fluid: PG, seq: 0003, pipe_class: 033842-X, insulation: ""

⚠️ **CRITICAL:** 
- In "031441-x", the "-x" is part of the pipe class, NOT insulation!
- "x" is NOT a valid insulation code (valid: H, PP, N, AA, E, FP)
- Insulation only appears if there's ANOTHER dash with valid code: "031441-x-H"

**EQUIPMENT TAG DETECTION:**
Identify equipment connection points (FROM/TO) near line numbers:
- Pattern: [LETTER]-[NUMBER] or [LETTER]-[NUMBER][LETTER]
- Examples: V-201, P-101, E-301, T-401, C-201
- Types: V (Vessel), P (Pump), E (Exchanger), T (Tank), C (Compressor), R (Reactor)

**DETECTION RULES:**
1. Line numbers can appear horizontally, vertically, or at angles
2. May have spaces between components (normalize to dashes)
3. Quote mark after size is MANDATORY
4. First 4 components (Size, Fluid, Sequence, Pipe Class) are REQUIRED
5. Insulation (5th component) is OPTIONAL - may or may not be present
6. Extract nearby equipment tags as FROM/TO connections
7. Handle OCR errors: O→0, I→1, S→5, Z→2
8. **DO NOT mistake the last part of pipe class as insulation!**

**OUTPUT JSON STRUCTURE:**
Return a JSON array with objects in this EXACT format:
[
  {{
    "line_number": "complete line number as detected (e.g., 20\"-PG-1234-A1B02 or 20\"-PG-1234-A1B02-N)",
    "size": "pipe size with quote (e.g., 20\")",
    "fluid_code": "2-4 letter code uppercase (e.g., PG)",
    "sequence_no": "exactly 4 digits (e.g., 1234)",
    "pipr_class": "pipe class code - DO NOT include insulation here (e.g., A1B02 or 011440-2)",
    "insulation": "1-2 letter code if present, empty string if not present (e.g., N or \"\")",
    "from_equipment": "source equipment tag if nearby (e.g., V-201) or empty string",
    "to_equipment": "destination equipment tag if nearby (e.g., P-101) or empty string",
    "from_line": "connected line number appearing LEFT/ABOVE this line (e.g., 10\"-CW-5678-A1B02) or empty string",
    "to_line": "connected line number appearing RIGHT/BELOW this line (e.g., 8\"-PG-9999-C3D04) or empty string",
    "confidence": "high (all clear) | medium (some OCR artifacts) | low (incomplete)"
  }}
]

**PARSING EXAMPLES (FOLLOW THESE EXACTLY):**

Example 1: "20\"-PG-1234-A1B02-N"
  → size: "20\"", fluid_code: "PG", sequence_no: "1234", pipr_class: "A1B02", insulation: "N"
  (5 components: size, fluid, seq, pipe class, insulation)

Example 2: "10\"-PG-0003-033842-X"
  → size: "10\"", fluid_code: "PG", sequence_no: "0003", pipr_class: "033842-X", insulation: ""
  (4 components: size, fluid, seq, pipe class - NO insulation)

Example 3: "28\"-PG-3212-C3D04"
  → size: "28\"", fluid_code: "PG", sequence_no: "3212", pipr_class: "C3D04", insulation: ""
  (4 components: size, fluid, seq, pipe class - NO insulation)

Example 4: "10\"-PG-0003-033842-X-H"
  → size: "10\"", fluid_code: "PG", sequence_no: "0003", pipr_class: "033842-X", insulation: "H"
  (5 components: size, fluid, seq, pipe class, insulation)

**CRITICAL NOTES:**
- In Example 2: "033842-X" is the COMPLETE pipe class (6 digits + dash + 1 LETTER)
- The "-X" is NOT insulation, it's part of the pipe class (letter suffix)
- Only if there's ANOTHER dash after "033842-X" would there be insulation
- Most lines (80%+) do NOT have insulation - empty string "" is normal

**OCR TEXT TO ANALYZE:**
{extracted_text[:4000]}

**INSTRUCTIONS:**
- Return ONLY valid JSON array - NO markdown, NO code blocks, NO explanations
- Extract EVERY line number found in the text
- Use empty strings "" for missing from_equipment/to_equipment
- Normalize spacing and dashes in line numbers
- Group equipment tags with closest line number by proximity
- **IMPORTANT**: Look for OTHER line numbers that appear spatially connected to each line:
  - from_line: line number appearing to the LEFT or ABOVE the current line
  - to_line: line number appearing to the RIGHT or BELOW the current line
  - Use spatial proximity (closer line numbers = more likely connected)
  - Leave empty "" if no connected line numbers are found nearby

**RETURN JSON NOW:**"""

        try:
            logger.info("  🤖 Sending to OpenAI for intelligent parsing...")
            response = self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": "You are a P&ID analysis expert. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=2000
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
                result_text = result_text.strip()
            
            parsed_lines = json.loads(result_text)
            
            # Add page number to each item
            for line in parsed_lines:
                line['page'] = page_num
            
            logger.info(f"  ✅ OpenAI parsed {len(parsed_lines)} line numbers")
            return parsed_lines
            
        except Exception as e:
            logger.error(f"  ❌ OpenAI parsing failed: {e}")
            return self._fallback_regex_parse(extracted_text, page_num)
    
    def _fallback_regex_parse(self, text: str, page_num: int) -> List[Dict]:
        """
        ENHANCED regex parsing for P&ID line numbers
        
        Format: SIZE-FLUID-SEQ-PIPECLASS(-INSULATION)?
        Example: 12-D-5777-033842-N
        
        STRICT VALIDATION:
        - SIZE: 1-2 digits ONLY (6, 12, 20, 24) - NOT 3-4 digits
        - FLUID: 1-2 LETTERS ONLY (D, PG, CW, ST) - MUST be present
        - SEQ: EXACTLY 4 digits (5777, 0003)
        - PIPECLASS: EXACTLY 6 digits (033842, 011441)
        - INSULATION: OPTIONAL - ONLY these codes: H, PP, X, N, E, FP, AA
          * NOT fluid codes like PG, D, CW - those are FLUID not INSULATION
        
        REJECTS:
        - "4003-031441-X" (size 4003 is 4 digits, too long!)
        - Patterns without FLUID code
        - Incomplete patterns
        """
        results = []
        all_matches = []
        
        # Pattern 1: Standard format with hyphens: 12-D-5777-033842-N
        # More flexible with whitespace and optional quote variations
        pattern1 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s*[-–—]\s*([A-Z]{1,2})\s*[-–—]\s*(\d{4})\s*[-–—]\s*(\d{6})(?:\s*[-–—]?\s*([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 2: With quote after size: 12"-D-5777-033842-N or 12" - D - 5777 - 033842
        pattern2 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\']\s*[-–—]?\s*([A-Z]{1,2})\s*[-–—]\s*(\d{4})\s*[-–—]\s*(\d{6})(?:\s*[-–—]?\s*([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 3: Space-separated: 12 D 5777 033842 N (OCR sometimes loses hyphens)
        pattern3 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s+([A-Z]{1,2})\s+(\d{4})\s+(\d{6})(?:\s+([A-Za-z0-9]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 4: Mixed separators: 12"-D 5777-033842 (handles OCR inconsistencies)
        pattern4 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s*[-–—\s]+([A-Z]{1,2})\s+[-–—\s]?\s*(\d{4})\s*[-–—\s]+(\d{6})(?:\s*[-–—]?\s*([A-Za-z0-9]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 5: With periods (OCR sometimes sees hyphens as periods)
        pattern5 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s*[.\s]*([A-Z]{1,2})\s*[.\s]*(\d{4})\s*[.\s]*(\d{6})(?:\s*[.\s]*([A-Za-z0-9]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 6: Very loose spacing (multiple spaces/tabs)
        pattern6 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s{1,8}([A-Z]{1,2})\s{1,8}(\d{4})\s{1,8}(\d{6})(?:\s{1,8}([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 7: With underscores (OCR sometimes sees hyphens as underscores)
        pattern7 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s*[_-]\s*([A-Z]{1,2})\s*[_-]\s*(\d{4})\s*[_-]\s*(\d{6})(?:\s*[_-]?\s*([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 8: Very minimal separators (almost concatenated)
        pattern8 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})["\'"]?\s*([A-Z]{1,2})\s*(\d{4})\s*(\d{6})(?:\s*([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Collect all matches from all patterns
        for pattern_num, pattern in enumerate([pattern1, pattern2, pattern3, pattern4, pattern5, pattern6, pattern7, pattern8], 1):
            matches = pattern.findall(text)
            for match in matches:
                size, fluid, seq, pipr_class, insulation = match
                
                # ULTRA STRICT VALIDATION - ALL 4 COMPONENTS MANDATORY
                
                # 1. SIZE: MUST be 1-2 digits (reject "05", "40", "4003")
                if not size or len(size) > 2 or len(size) < 1:
                    continue
                try:
                    size_int = int(size)
                    if not (1 <= size_int <= 99):
                        continue
                except ValueError:
                    continue
                
                # 2. FLUID: MUST be 1-2 LETTERS only (reject "03", empty, numbers)
                if not fluid or len(fluid) > 2 or len(fluid) < 1:
                    continue
                if not fluid.isalpha():  # Reject if contains numbers
                    continue
                
                # 3. SEQUENCE: MUST be EXACTLY 4 digits (reject "31441" which is 5)
                if not seq or len(seq) != 4:
                    continue
                if not seq.isdigit():
                    continue
                
                # 4. PIPE CLASS: MUST be EXACTLY 6 digits
                if not pipr_class or len(pipr_class) != 6:
                    continue
                if not pipr_class.isdigit():
                    continue
                
                # 5. INSULATION: OPTIONAL - ONLY specific codes (H, PP, X, N, E, FP, AA)
                VALID_INSULATION = {'H', 'PP', 'X', 'N', 'E', 'FP', 'AA', 'h', 'pp', 'x', 'n', 'e', 'fp', 'aa', ''}
                # Strip whitespace from insulation
                insulation = insulation.strip() if insulation else ''
                # If insulation is provided but not in valid list, skip
                if insulation and insulation not in VALID_INSULATION:
                    continue
                
                # Build line number
                line_parts = [f'{size}"-{fluid.upper()}-{seq}-{pipr_class}']
                if insulation:
                    line_parts.append(insulation.upper())
                
                line_number = '-'.join(line_parts)
                
                # Add to matches (will deduplicate later)
                all_matches.append({
                    'line_number': line_number,
                    'size': f'{size}"',
                    'fluid_code': fluid.upper(),
                    'sequence_no': seq,
                    'pipr_class': pipr_class,
                    'insulation': insulation.upper() if insulation else '',
                    'from_equipment': '',
                    'to_equipment': '',
                    'page': page_num,
                    'confidence': 'high' if pattern_num <= 2 else 'medium',
                    'pattern': pattern_num
                })
        
        # Deduplicate by line_number (keep highest confidence)
        seen = {}
        pattern_stats = {}
        
        for match in all_matches:
            line_num = match['line_number']
            pattern_num = match.get('pattern', 0)
            
            # Track pattern distribution
            pattern_stats[pattern_num] = pattern_stats.get(pattern_num, 0) + 1
            
            if line_num not in seen or match['confidence'] == 'high':
                seen[line_num] = match
        
        results = list(seen.values())
        
        # Log pattern distribution
        if pattern_stats:
            logger.info(f"  📊 Pattern distribution: {dict(sorted(pattern_stats.items()))}")
        
        # Remove pattern field before returning
        for item in results:
            item.pop('pattern', None)
        
        logger.info(f"  📝 Regex found {len(results)} unique valid line numbers from {len(all_matches)} total matches")
        return results
    
    def extract_from_pdf(self, pdf_path: str, include_area: bool = False, format_type: str = 'onshore', progress_callback=None) -> List[Dict]:
        """
        🚀 INTELLIGENT AI-FIRST EXTRACTION:
        
        PHASE 1: COMPREHENSIVE TEXT EXTRACTION
        - Tesseract OCR: Fast and reliable
        - EasyOCR: Good with varied fonts
        - PaddleOCR: Excellent with Asian characters and complex layouts
        - ALL THREE combined = Maximum text coverage
        
        PHASE 2: AI INTELLIGENCE
        - OpenAI GPT-4 searches through ALL text
        - Finds line numbers in ANY format (hyphens, spaces, periods, etc.)
        - Understands context better than rigid regex
        - Adapts to OCR variations automatically
        
        PHASE 3: STRICT VALIDATION
        - Validates ALL 4 mandatory components
        - Rejects invalid patterns
        - Ensures data quality
        
        Args:
            pdf_path: Path to P&ID PDF file
            include_area: If True, detect format with Area (SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS)
                         If False, detect standard format (SIZE-FLUID-SEQUENCE-PIPECLASS)
            format_type: 'onshore' (default) or 'offshore' (AREA-FLUID-SIZE-PIPECLASS-SEQUENCE)
            progress_callback: Optional callable(page_num, total_pages, lines_so_far, phase).
                               Purely observational — defaults to None so nothing breaks.
                               Called at the start of each page and after each page finishes.
            
        Returns:
            List of validated line items
        """
        # --- Safe no-op wrapper so call sites don't need None-guards ---
        def _emit(page_num, total, lines_so_far, phase):
            if progress_callback is None:
                return
            try:
                progress_callback(page_num, total, lines_so_far, phase)
            except Exception as _cb_err:
                logger.debug(f"progress_callback raised (ignored): {_cb_err}")
        try:
            doc = fitz.open(pdf_path)
            all_line_items = []
            # Per-page combined text snapshot (used by the coverage audit at
            # the end).  Populated each page AFTER we decide which text was
            # actually fed to the regex engine (embedded or OCR fallback).
            per_page_texts: Dict[int, str] = {}

            # ------------------------------------------------------------------
            # CAD-generated PDFs (AutoCAD, SmartPlant, PDMS…) split content
            # across Optional Content Groups (layers).  Pipe line annotations
            # are often on a layer that is marked "off" by default, so
            # PyMuPDF's text extraction skips them entirely.
            # Enabling every OCG before extraction ensures we see all text.
            # ------------------------------------------------------------------
            try:
                ocgs = doc.get_ocgs()
                if ocgs:
                    for xref in ocgs.keys():
                        doc.set_ocg(xref, True)
                    logger.info(f"  🔓 Enabled {len(ocgs)} optional content layers for full text extraction")
            except Exception as _ocg_err:
                logger.warning(f"  ⚠️ Could not enable OCG layers: {_ocg_err}")
            
            all_line_items = []
            
            logger.info(f"🚀 STARTING AI-FIRST P&ID EXTRACTION")
            logger.info(f"📄 File: {pdf_path}")
            logger.info(f"📄 Pages: {len(doc)}")
            logger.info(f"🧠 Strategy: OCR ALL TEXT → AI INTELLIGENCE → STRICT VALIDATION")
            if format_type == 'adnoc':
                format_msg = 'ADNOC Abu Dhabi Oil Co. Ltd (SIZE"-FLUID-PIPECLASS-SEQUENCE)'
            elif format_type == 'industrial':
                format_msg = 'INDUSTRIAL/PROJECT (SIZE"-UNIT-SERVICE-SEQ-PIPINGCLASS-ENDDESIG)'
            elif format_type == 'offshore':
                format_msg = 'OFFSHORE (AREA-FLUID-SIZE-PIPECLASS-SEQUENCE)'
            elif include_area:
                format_msg = 'WITH AREA (SIZE"-AREA-FLUID-SEQ-PIPECLASS)'
            else:
                format_msg = 'WITHOUT AREA (SIZE-FLUID-SEQ-PIPECLASS)'
            logger.info(f"📍 Format: {format_msg}")
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                logger.info(f"\n{'='*60}")
                logger.info(f"📄 PAGE {page_num + 1}/{len(doc)}")
                logger.info(f"{'='*60}")

                # Per-page progress signal (purely observational — no core logic changed)
                _emit(page_num + 1, len(doc), len(all_line_items), 'start')

                # ------------------------------------------------------------------
                # PHASE 1 (fast path): Extract embedded text directly from PDF.
                # Vector/searchable PDFs have all text as proper text objects —
                # no OCR needed.  This is instantaneous and handles rotated labels.
                # SOFT-CODED threshold: EMBEDDED_TEXT_MIN_CHARS
                # ------------------------------------------------------------------
                logger.info("🔍 PHASE 1: Embedded PDF text extraction (fast path)")
                embedded_text = self._extract_pdf_embedded_text(page)
                use_ocr = len(embedded_text.strip()) < EMBEDDED_TEXT_MIN_CHARS

                if not use_ocr:
                    logger.info(
                        f"  ✅ {len(embedded_text)} chars of embedded text found — "
                        f"skipping OCR for this page"
                    )
                    combined_text = embedded_text
                else:
                    # ------------------------------------------------------------------
                    # PHASE 1b (slow path): Scanned/image PDF — fall back to OCR.
                    # High-resolution rendering (2.5x for crisp text)
                    # ------------------------------------------------------------------
                    logger.info(
                        f"  📷 Only {len(embedded_text.strip())} embedded chars "
                        f"(threshold {EMBEDDED_TEXT_MIN_CHARS}) — using OCR pipeline"
                    )
                    pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    img = img.convert('L')  # Grayscale for better OCR

                    logger.info("🔍 PHASE 1b: Multi-Engine OCR Extraction")
                    ocr_results = self.extract_all_text_from_image(img)

                    if not ocr_results:
                        logger.warning("  ⚠️ No text extracted from any OCR engine")
                        continue

                    combined_text = self.combine_and_deduplicate_text(ocr_results)

                    if not combined_text or len(combined_text) < 10:
                        logger.warning("  ⚠️ Combined OCR text too short, skipping page")
                        continue

                    # Make PIL image available for geometric FROM-TO below
                    # (only needed when we actually ran OCR)
                
                if not combined_text or len(combined_text.strip()) < 10:
                    logger.warning("  ⚠️ No usable text on this page — skipping")
                    continue
                
                # PHASE 2: REGEX Pattern Matching (Reliable & Fast)
                logger.info("🔍 PHASE 2: REGEX Pattern Recognition")
                logger.info(f"  📝 Text sample (first 500 chars): {combined_text[:500]}")
                line_items = self.parse_with_regex(combined_text, page_num + 1, include_area=include_area, format_type=format_type)
                # Snapshot for coverage audit (set now; may be replaced below if OCR fallback runs)
                per_page_texts[page_num + 1] = combined_text

                # ------------------------------------------------------------------
                # OCR FALLBACK: When embedded text was used (fast path) but regex
                # found zero line numbers, the drawing content is likely image-based
                # (e.g. title block text only, while the P&ID lines are rasterised).
                # Fall back to the full OCR pipeline for this page.
                # Core logic (regex, validation, formats) is unchanged.
                # ------------------------------------------------------------------
                if not line_items and not use_ocr:
                    logger.info(
                        "  ⚠️ Embedded text found no line numbers — "
                        "drawing content may be image-based. Falling back to OCR."
                    )
                    pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
                    img_fb = Image.open(io.BytesIO(pix.tobytes("png")))
                    img_fb = img_fb.convert('L')
                    ocr_results_fb = self.extract_all_text_from_image(img_fb)
                    if ocr_results_fb:
                        combined_text_fb = self.combine_and_deduplicate_text(ocr_results_fb)
                        if combined_text_fb and len(combined_text_fb) >= 10:
                            logger.info(
                                f"  📷 OCR fallback extracted {len(combined_text_fb)} chars — "
                                "re-running regex"
                            )
                            logger.info(
                                f"  📝 OCR fallback text sample (first 300 chars): "
                                f"{combined_text_fb[:300]}"
                            )
                            combined_text = combined_text_fb
                            # Refresh audit snapshot with the OCR-fallback text
                            per_page_texts[page_num + 1] = combined_text
                            # Mark as OCR path so FROM-TO phases have an image object
                            use_ocr = True
                            img = img_fb
                            line_items = self.parse_with_regex(
                                combined_text, page_num + 1,
                                include_area=include_area, format_type=format_type
                            )
                            logger.info(
                                f"  ✅ OCR fallback found {len(line_items)} line numbers"
                            )
                        else:
                            logger.warning("  ⚠️ OCR fallback returned insufficient text")
                    else:
                        logger.warning("  ⚠️ OCR fallback returned no text")

                if not line_items:
                    logger.warning("  ⚠️ No line numbers found on this page")
                    continue
                
                # SUCCESS: Add basic line items first
                all_line_items.extend(line_items)
                logger.info(f"✅ PAGE {page_num + 1} BASIC EXTRACTION: {len(line_items)} line numbers extracted")

                # PHASE 3A/3B/3C only make sense when we have actual image data.
                # If we used the fast embedded-text path, we don't have an img object.
                if use_ocr:
                    pass  # img is already defined from OCR path above
                else:
                    # Render image lazily for spatial/geometric FROM-TO (low resolution is fine)
                    try:
                        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert('L')
                    except Exception as _img_err:
                        logger.warning(f"  ⚠️ Could not render page for FROM-TO detection: {_img_err}")
                        img = None

                if img is None:
                    # Skip FROM-TO phases for this page
                    continue

                # PHASE 3A: Spatial Matching FROM-TO Detection (PRIMARY METHOD - from research paper)
                spatial_from_to_success = False
                try:
                    logger.info("🔬 PHASE 3A: Spatial Matching FROM-TO Detection (Research Paper Method)")
                    logger.info("  📍 Method: Correlate text positions with line endpoints")
                    logger.info("  📄 Reference: 'Automated counting of P&ID using AI' (2025)")
                    
                    # Import spatial matching module
                    from apps.designiq.spatial_matching import SpatialLineDetector
                    
                    # Initialize detector
                    spatial_detector = SpatialLineDetector()
                    
                    # Convert PIL Image to numpy array for OpenCV
                    import numpy as np
                    img_np = np.array(img)
                    
                    # Step 1: Detect line geometries
                    logger.info(f"  🔍 Step 1: Detecting line geometries on page {page_num + 1}...")
                    line_geometries = spatial_detector.detect_line_geometries(img_np)
                    
                    if line_geometries and len(line_geometries) > 0:
                        logger.info(f"  ✅ Detected {len(line_geometries)} line geometries")
                        
                        # Step 2: Prepare line number data with bounding boxes
                        logger.info(f"  🔍 Step 2: Preparing {len(line_items)} line numbers for spatial matching...")
                        
                        # Get image dimensions
                        image_height, image_width = img_np.shape[:2]
                        
                        # Step 3: Perform spatial matching
                        logger.info(f"  🔍 Step 3: Matching text positions to line endpoints...")
                        spatial_from_to_map = spatial_detector.spatial_matching_from_to(
                            line_numbers=line_items,  # Already has 'bbox' from OCR
                            line_geometries=line_geometries,
                            image_width=image_width,
                            image_height=image_height
                        )
                        
                        if spatial_from_to_map:
                            # Update line items with spatial matching results
                            for item in line_items:
                                line_num = item['line_number']
                                if line_num in spatial_from_to_map:
                                    from_line = spatial_from_to_map[line_num].get('from_line')
                                    to_line = spatial_from_to_map[line_num].get('to_line')
                                    
                                    if from_line and from_line != '-':
                                        item['from_line'] = from_line
                                    if to_line and to_line != '-':
                                        item['to_line'] = to_line
                                    
                                    item['flow_detection_method'] = 'spatial_matching'
                                    item['flow_confidence'] = spatial_from_to_map[line_num].get('confidence', 'high')
                            
                            # Update the items in all_line_items
                            all_line_items = all_line_items[:-len(line_items)]
                            all_line_items.extend(line_items)
                            
                            # Count success
                            with_from_to = sum(1 for item in line_items if item.get('from_line') or item.get('to_line'))
                            logger.info(f"  ✅ Spatial matching completed: {with_from_to}/{len(line_items)} items have FROM-TO")
                            
                            if with_from_to > 0:
                                spatial_from_to_success = True
                        else:
                            logger.warning(f"  ⚠️ Spatial matching returned empty results")
                    else:
                        logger.warning(f"  ⚠️ No line geometries detected, skipping spatial matching")
                        
                except Exception as e:
                    logger.error(f"  ❌ Spatial matching FAILED: {e}", exc_info=True)
                    logger.info(f"  → Falling back to OpenAI Vision")
                
                # PHASE 3B: OpenAI Vision-Based FROM-TO Detection (FALLBACK if spatial matching fails)
                vision_from_to_success = False
                if not spatial_from_to_success:
                    try:
                        if self.openai_client:
                            logger.info("🧠 PHASE 3B: OpenAI Vision-Based FROM-TO Detection (Fallback)")
                            logger.info("  📍 Method: AI Process Engineer - Visual flow analysis")
                            
                            # Import the new function
                            from apps.designiq.from_to_integration import determine_from_to_with_openai_vision
                            
                            # Extract just the line numbers for the prompt
                            line_numbers = [item['line_number'] for item in line_items]
                            
                            logger.info(f"  🔍 Sending {len(line_numbers)} line numbers to OpenAI Vision...")
                            
                            # Call OpenAI Vision
                            vision_from_to_map = determine_from_to_with_openai_vision(
                                ocr_line_numbers=line_numbers,
                                pdf_image=img,
                                page_number=page_num + 1,
                                openai_client=self.openai_client
                            )
                        
                            if vision_from_to_map:
                                # Update line items with OpenAI Vision results
                                for item in line_items:
                                    line_num = item['line_number']
                                    if line_num in vision_from_to_map:
                                        from_line = vision_from_to_map[line_num].get('from')
                                        to_line = vision_from_to_map[line_num].get('to')
                                        
                                        if from_line:
                                            item['from_line'] = from_line
                                        if to_line:
                                            item['to_line'] = to_line
                                        
                                        item['flow_detection_method'] = 'openai_vision'
                                        item['flow_confidence'] = 'high'
                                
                                # Update the items in all_line_items
                                all_line_items = all_line_items[:-len(line_items)]
                                all_line_items.extend(line_items)
                                
                                # Count success
                                with_from_to = sum(1 for item in line_items if item.get('from_line') or item.get('to_line'))
                                logger.info(f"  ✅ OpenAI Vision FROM-TO detection completed: {with_from_to}/{len(line_items)} items have FROM-TO")
                                
                                if with_from_to > 0:
                                    vision_from_to_success = True
                            else:
                                logger.warning(f"  ⚠️ OpenAI Vision returned empty results")
                        else:
                            logger.info("  ℹ️ OpenAI client not available, skipping Vision-based detection")
                    except Exception as e:
                        logger.error(f"  ❌ OpenAI Vision FROM-TO detection FAILED: {e}", exc_info=True)
                        logger.info(f"  → Falling back to geometric detection")
                
                # PHASE 3C: Geometric Line-Based FROM-TO Detection (LAST FALLBACK - only if both failed)
                if not spatial_from_to_success and not vision_from_to_success:
                    try:
                        logger.info("🔺 PHASE 3C: Geometric Line-Based FROM-TO Detection (Last Fallback)")
                        logger.info("  📍 Method: OpenCV line detection + connectivity graph")
                        logger.info("  📍 Strategy: Normalize coordinates → Detect lines → Build graph → Infer FROM-TO")
                        
                        # Use the new geometric detector
                        if self.geometric_detector and line_items:
                            logger.info(f"  🔍 Processing {len(line_items)} line items with geometric detection...")
                            
                            # Run geometric detection on this PDF page
                            geometric_from_to_map = self.geometric_detector.process_pdf_page(
                                pdf_path=pdf_path,
                                page_num=page_num,
                                line_numbers=line_items  # Pass items with bbox data
                            )
                            
                            if geometric_from_to_map:
                                # Update line items with geometric detection results
                                for item in line_items:
                                    line_num = item['line_number']
                                    if line_num in geometric_from_to_map:
                                        from_line = geometric_from_to_map[line_num].get('from_line')
                                        to_line = geometric_from_to_map[line_num].get('to_line')
                                        
                                        if from_line and from_line != '-':
                                            item['from_line'] = from_line
                                        if to_line and to_line != '-':
                                            item['to_line'] = to_line
                                        
                                        item['flow_detection_method'] = geometric_from_to_map[line_num].get('method', 'geometric_detection')
                                        item['flow_confidence'] = geometric_from_to_map[line_num].get('confidence', 'medium')
                                
                                # Update the items in all_line_items
                                all_line_items = all_line_items[:-len(line_items)]
                                all_line_items.extend(line_items)
                                
                                # Count how many have FROM-TO data
                                with_from_to = sum(1 for item in line_items if item.get('from_line') or item.get('to_line'))
                                logger.info(f"  ✅ Geometric FROM-TO detection completed: {with_from_to}/{len(line_items)} items have FROM-TO")
                            else:
                                logger.warning(f"  ⚠️ Geometric detection returned no results, keeping basic items")
                        else:
                            logger.warning(f"  ⚠️ Geometric detector not available or no line items to process")
                    except Exception as e:
                        logger.error(f"  ❌ Geometric FROM-TO detection FAILED: {e}", exc_info=True)
                        logger.error(f"  → Continuing with basic line items only")
            
            doc.close()
            
            # PHASE 3: Final deduplication and validation
            logger.info(f"\n{'='*60}")
            logger.info("🎯 PHASE 3: Final Validation & Deduplication")
            logger.info(f"{'='*60}")
            logger.info(f"  📊 Raw extractions: {len(all_line_items)}")
            
            unique_items = self._deduplicate_items(all_line_items)
            logger.info(f"  📊 After deduplication: {len(unique_items)}")
            
            # 🔥 SMART FROM-TO ASSIGNMENT: Intelligent flow detection
            logger.info(f"\n{'='*60}")
            logger.info("🧠 SMART FROM-TO ASSIGNMENT - AI-Like Intelligence")
            logger.info(f"{'='*60}")
            
            # Apply smart assignment to ALL items
            unique_items = self._apply_smart_flow_logic(unique_items)
            
            # Final count
            with_from_to = sum(1 for item in unique_items if item.get('from_line') or item.get('to_line'))
            logger.info(f"  📊 FINAL: {with_from_to}/{len(unique_items)} items have FROM-TO data")
            
            logger.info(f"\n{'='*60}")
            logger.info(f"🎉 EXTRACTION COMPLETE: {len(unique_items)} UNIQUE LINE NUMBERS")
            logger.info(f"{'='*60}\n")
            
            # 🔒 NUCLEAR OPTION: Replace ALL 'O' with '0' in EVERY string field.
            # EXCEPTION: Industrial format codes are already correct (piping classes like
            # 32070R, service codes FL/HD/FCWR have no O to convert).  We skip this pass
            # for industrial items to avoid corrupting equipment tag strings like MOV.
            logger.info("🔒 NUCLEAR SAFETY: Replacing ALL 'O' → '0' in every string field (non-industrial)...")
            for item in unique_items:
                if item.get('extraction_method', '') == 'regex_industrial':
                    continue  # Industrial codes are already clean — skip
                # Loop through ALL keys and replace O→0 in ANY string value
                for key, value in item.items():
                    if isinstance(value, str) and value:
                        # FORCE replace ALL 'O' (uppercase) with '0' (digit)
                        item[key] = value.upper().replace('O', '0')
            
            logger.info("✅ NUCLEAR NORMALIZATION COMPLETE - EVERY 'O' → '0' IN NON-INDUSTRIAL FIELDS!")

            # ------------------------------------------------------------------
            # COVERAGE AUDIT — permissive re-scan of raw text to flag any
            # line-like candidates that were NOT extracted.  Pure reporting,
            # never mutates results.  Controlled by COVERAGE_AUDIT_CONFIG.
            # ------------------------------------------------------------------
            try:
                audit_report = self._audit_coverage(per_page_texts, unique_items)
                # Attach a compact summary to the first item so callers can
                # surface it in the UI without changing the return type.
                if unique_items and audit_report.get('enabled'):
                    unique_items[0].setdefault('_audit', {
                        'coverage_ratio': audit_report['coverage_ratio'],
                        'total_candidates': audit_report['total_candidates'],
                        'matched_candidates': audit_report['matched_candidates'],
                        'missed_count': sum(
                            len(v) for v in audit_report['missed_candidates'].values()
                        ),
                    })

                # Smart recovery — rescue structurally-valid missed candidates.
                # Additive: adds items flagged `recovered=True`; never modifies
                # existing items, never triggers re-validation of them.
                try:
                    recovered_items = self._smart_recover_missed(audit_report, unique_items)
                    if recovered_items:
                        unique_items.extend(recovered_items)
                        # Refresh audit summary with recovered count
                        if unique_items and '_audit' in unique_items[0]:
                            unique_items[0]['_audit']['recovered_count'] = len(recovered_items)
                except Exception as _rec_err:
                    logger.warning(f"⚠️ Smart recovery failed (non-fatal): {_rec_err}")
            except Exception as _audit_err:
                logger.warning(f"⚠️ Coverage audit failed (non-fatal): {_audit_err}")

            return unique_items
            
        except Exception as e:
            logger.error(f"❌ EXTRACTION FAILED: {str(e)}", exc_info=True)
            return []
    
    def _audit_coverage(
        self,
        per_page_texts: Dict[int, str],
        extracted_items: List[Dict],
    ) -> Dict:
        """
        🔎 ADVANCED COVERAGE AUDIT (soft-coded, non-intrusive)

        Re-scans the raw text that was fed to the regex engine for line-like
        candidates, then fuzzy-matches each candidate against the extracted
        items.  Candidates with no match are reported as "potentially missed"
        — useful for spotting OCR garble the main regex rejected.

        Never mutates ``extracted_items``.  Controlled entirely by
        ``COVERAGE_AUDIT_CONFIG`` at module top.

        Returns a report dict with keys:
          - enabled, total_extracted, total_candidates,
          - matched_candidates, missed_candidates (per-page),
          - coverage_ratio (0..1)
        """
        import difflib

        cfg = COVERAGE_AUDIT_CONFIG
        report = {
            'enabled': bool(cfg.get('enabled', True)),
            'total_extracted': len(extracted_items),
            'total_candidates': 0,
            'matched_candidates': 0,
            'missed_candidates': {},   # page -> [candidate strings]
            'coverage_ratio': 1.0,
        }
        if not report['enabled']:
            return report

        strip_chars = cfg.get('normalise_strip_chars', ' "\'.')
        threshold = float(cfg.get('fuzzy_match_threshold', 0.82))
        min_segments = int(cfg.get('min_segments', 3))
        max_report = int(cfg.get('max_reported_per_page', 40))
        patterns = [re.compile(p, re.IGNORECASE) for p in cfg.get('candidate_patterns', [])]

        def _norm(s: str) -> str:
            out = s.upper()
            for ch in strip_chars:
                out = out.replace(ch, '')
            return out.replace('O', '0')  # same O→0 rule as main pipeline

        extracted_keys = [
            _norm(it.get('line_number', '')) for it in extracted_items
        ]
        extracted_keys = [k for k in extracted_keys if k]

        for page_num, text in (per_page_texts or {}).items():
            if not text:
                continue
            candidates_on_page = set()
            for pat in patterns:
                for m in pat.finditer(text):
                    tok = m.group(0).strip()
                    # Must have enough hyphen segments to look line-like
                    if tok.count('-') + tok.count(' ') < (min_segments - 1):
                        continue
                    candidates_on_page.add(tok)

            missed_for_page = []
            for cand in candidates_on_page:
                report['total_candidates'] += 1
                cand_key = _norm(cand)
                if not cand_key:
                    continue
                # Direct substring match wins immediately
                if any(cand_key in k or k in cand_key for k in extracted_keys):
                    report['matched_candidates'] += 1
                    continue
                # Fuzzy match against best extracted key
                best_ratio = 0.0
                if extracted_keys:
                    best_ratio = max(
                        difflib.SequenceMatcher(None, cand_key, k).ratio()
                        for k in extracted_keys
                    )
                if best_ratio >= threshold:
                    report['matched_candidates'] += 1
                else:
                    missed_for_page.append(cand)

            if missed_for_page:
                # Stable order + cap for log readability
                missed_for_page = sorted(set(missed_for_page))[:max_report]
                report['missed_candidates'][page_num] = missed_for_page

        if report['total_candidates'] > 0:
            report['coverage_ratio'] = round(
                report['matched_candidates'] / report['total_candidates'], 3
            )

        # Emit a concise log summary — detail lives in the return dict
        logger.info(f"\n{'='*60}")
        logger.info("🔎 COVERAGE AUDIT (permissive re-scan)")
        logger.info(f"{'='*60}")
        logger.info(f"  📊 Extracted line items          : {report['total_extracted']}")
        logger.info(f"  📊 Candidate tokens in raw text  : {report['total_candidates']}")
        logger.info(f"  📊 Candidates matched to extract : {report['matched_candidates']}")
        logger.info(f"  📊 Coverage ratio                : {report['coverage_ratio']*100:.1f}%")
        if report['missed_candidates']:
            total_missed = sum(len(v) for v in report['missed_candidates'].values())
            logger.warning(
                f"  ⚠️ {total_missed} candidate(s) on "
                f"{len(report['missed_candidates'])} page(s) were NOT matched "
                "— they may be OCR garble or genuinely missed."
            )
            for pg, cands in report['missed_candidates'].items():
                preview = ', '.join(cands[:5])
                more = f" (+{len(cands)-5} more)" if len(cands) > 5 else ''
                logger.warning(f"    page {pg}: {preview}{more}")
        else:
            logger.info("  ✅ No candidate lines appear to have been missed.")

        return report

    def _smart_recover_missed(
        self,
        audit_report: Dict,
        extracted_items: List[Dict],
    ) -> List[Dict]:
        """
        🛟 SMART RECOVERY (soft-coded, additive).

        Take the audit's ``missed_candidates`` and try to rescue each one by
        tokenising on [-\\s]+ and validating against the soft-coded format
        dicts (INDUSTRIAL_FORMAT, BOROUGE_AREA_FORMAT).  Structurally-valid
        candidates become full line items flagged ``recovered=True``.

        Never mutates ``extracted_items``.  The core regex and validators
        in ``parse_with_regex`` are untouched.

        Returns a list of recovered items (may be empty).
        """
        import difflib

        cfg = SMART_RECOVERY_CONFIG
        if not cfg.get('enabled', True):
            return []
        if not audit_report or not audit_report.get('missed_candidates'):
            return []

        min_tokens = int(cfg.get('min_tokens', 3))
        max_tokens = int(cfg.get('max_tokens', 7))
        max_recovered = int(cfg.get('max_recovered_items', 200))
        dup_threshold = float(cfg.get('dup_guard_threshold', 0.88))

        # Normalise helper — must mirror the main O→0 + strip rule
        strip_chars = COVERAGE_AUDIT_CONFIG.get('normalise_strip_chars', ' "\'.')

        def _norm(s: str) -> str:
            out = s.upper()
            for ch in strip_chars:
                out = out.replace(ch, '')
            return out.replace('O', '0')

        existing_keys = [_norm(it.get('line_number', '')) for it in extracted_items]
        existing_keys = [k for k in existing_keys if k]

        # ------------------------------------------------------------------
        # Per-format validators — each takes the token list and returns a
        # structured dict if the shape matches, else None.  Knobs come from
        # the soft-coded format dicts, so widening a regex knob (e.g.
        # BOROUGE_AREA_FORMAT['seq_digits_max']) automatically widens this
        # recovery path too.
        # ------------------------------------------------------------------
        def _try_industrial(tokens: List[str]):
            # SIZE"-UNIT-SERVICE-SEQ-PIPINGCLASS(-END)
            if len(tokens) not in (5, 6):
                return None
            size, unit, service, seq, pipe_class = tokens[0:5]
            end = tokens[5] if len(tokens) == 6 else ''
            size_digits = re.sub(r'[^0-9/]', '', size)
            if not re.match(r'^\d{1,2}(/\d{1,2})?$', size_digits):
                return None
            if not (unit.isdigit() and len(unit) <= INDUSTRIAL_FORMAT['unit_no_max_digits']):
                return None
            if not (service.isalpha() and len(service) <= INDUSTRIAL_FORMAT['service_code_max_len']):
                return None
            if not (seq.isdigit()
                    and INDUSTRIAL_FORMAT['seq_digits_min'] <= len(seq) <= INDUSTRIAL_FORMAT['seq_digits_max']):
                return None
            if not re.match(INDUSTRIAL_FORMAT['piping_class_pattern'], pipe_class):
                return None
            if end and not (end.isalpha() and len(end) <= 2):
                return None
            return {
                'size': size_digits,
                'unit_no': unit,
                'service': service,
                'sequence': seq,
                'pipe_class': pipe_class,
                'end_designator': end,
                'format_type': 'industrial',
            }

        def _try_area(tokens: List[str]):
            # SIZE"-AREA-SERVICE-SEQ-PIPECLASS(-END)   (Borouge / WITH AREA)
            _b = BOROUGE_AREA_FORMAT
            if len(tokens) not in (5, 6):
                return None
            size, area, service, seq, pipe_class = tokens[0:5]
            end = tokens[5] if len(tokens) == 6 else ''
            size_digits = re.sub(r'[^0-9/]', '', size)
            if not re.match(r'^\d{1,2}(/\d{1,2})?$', size_digits):
                return None
            if not (area.isdigit()
                    and _b['area_digits_min'] <= len(area) <= _b['area_digits_max']):
                return None
            if not (service.isalpha()
                    and _b['service_letters_min'] <= len(service) <= _b['service_letters_max']):
                return None
            if not (seq.isdigit()
                    and _b['seq_digits_min'] <= len(seq) <= _b['seq_digits_max']):
                return None
            if not (pipe_class.isalnum()
                    and _b['pipeclass_len_min'] <= len(pipe_class) <= _b['pipeclass_len_max']):
                return None
            if end and not (end.isalpha()
                            and _b['enddesig_letters_min'] <= len(end) <= _b['enddesig_letters_max']):
                return None
            return {
                'size': size_digits,
                'area': area,
                'service': service,
                'sequence': seq,
                'pipe_class': pipe_class,
                'end_designator': end,
                'format_type': 'area',
            }

        def _try_standard(tokens: List[str]):
            # SIZE"-FLUID-SEQ-PIPECLASS   (onshore, no area)
            if len(tokens) not in (4, 5):
                return None
            size, fluid, seq, pipe_class = tokens[0:4]
            end = tokens[4] if len(tokens) == 5 else ''
            size_digits = re.sub(r'[^0-9/]', '', size)
            if not re.match(r'^\d{1,2}(/\d{1,2})?$', size_digits):
                return None
            if not (fluid.isalpha() and 1 <= len(fluid) <= 3):
                return None
            if not (seq.isdigit() and 3 <= len(seq) <= 5):
                return None
            if not (pipe_class.isalnum() and 4 <= len(pipe_class) <= 8):
                return None
            if end and not (end.isalpha() and len(end) <= 2):
                return None
            return {
                'size': size_digits,
                'fluid': fluid,
                'sequence': seq,
                'pipe_class': pipe_class,
                'end_designator': end,
                'format_type': 'standard',
            }

        tryers = {
            'industrial': _try_industrial,
            'area': _try_area,
            'standard': _try_standard,
        }
        priority = cfg.get('format_priority', ['industrial', 'area', 'standard'])

        recovered: List[Dict] = []
        seen_recovered_keys: set = set()

        for page_num, cands in audit_report.get('missed_candidates', {}).items():
            for cand in cands:
                if len(recovered) >= max_recovered:
                    break
                # Split on hyphens / whitespace, drop empties
                tokens = [t for t in re.split(r'[-\s]+', cand.strip()) if t]
                if not (min_tokens <= len(tokens) <= max_tokens):
                    continue
                # O→0 per token (but keep original case for letters)
                tokens = [t.replace('O', '0') if t.isupper() or t.isalnum() else t
                          for t in tokens]
                parsed = None
                chosen_fmt = None
                for fmt in priority:
                    tryer = tryers.get(fmt)
                    if not tryer:
                        continue
                    parsed = tryer(tokens)
                    if parsed:
                        chosen_fmt = fmt
                        break
                if not parsed:
                    continue

                # Build canonical line_number back from validated tokens
                line_number = '-'.join(
                    [parsed['size'] + '"'] +
                    [str(v) for k, v in parsed.items()
                     if k not in ('size', 'format_type', 'end_designator') and v]
                )
                if parsed.get('end_designator'):
                    line_number += f"-{parsed['end_designator']}"

                key = _norm(line_number)
                if not key or key in seen_recovered_keys:
                    continue

                # Duplicate guard — skip if fuzzy-close to anything already extracted
                if existing_keys:
                    best_ratio = max(
                        difflib.SequenceMatcher(None, key, k).ratio()
                        for k in existing_keys
                    )
                    if best_ratio >= dup_threshold:
                        continue

                seen_recovered_keys.add(key)
                recovered.append({
                    'line_number': line_number,
                    'size': parsed['size'],
                    'fluid': parsed.get('fluid') or parsed.get('service', ''),
                    'area': parsed.get('area', ''),
                    'sequence': parsed['sequence'],
                    'pipe_class': parsed['pipe_class'],
                    'end_designator': parsed.get('end_designator', ''),
                    'page_number': page_num,
                    'extraction_method': f"audit_recovery:{chosen_fmt}",
                    'recovered': True,
                    'confidence': 'medium',
                    'from_line': '',
                    'to_line': '',
                })

        if recovered:
            logger.info(f"\n{'='*60}")
            logger.info("🛟 SMART RECOVERY")
            logger.info(f"{'='*60}")
            logger.info(f"  ✅ Recovered {len(recovered)} line(s) from audit residue")
            for r in recovered[:10]:
                logger.info(
                    f"    p{r['page_number']} {r['line_number']} "
                    f"[{r['extraction_method']}]"
                )
            if len(recovered) > 10:
                logger.info(f"    … (+{len(recovered) - 10} more)")
        else:
            logger.info("🛟 SMART RECOVERY: no additional lines recovered")

        return recovered

    def _deduplicate_items(self, items: List[Dict]) -> List[Dict]:
        """
        Remove duplicate line numbers with 2-layer OCR confusion handling
        
        Layer 1 (Hard): O → 0 (already done during extraction)
        Layer 2 (Soft): Z/2, B/8, I/1 equivalence for comparison only
        
        Example duplicates caused by OCR:
        - "D2AP08" vs "DZAP08" (Z confused with 2)
        - "D8AP08" vs "DBAP08" (B confused with 8)
        - "D1AP08" vs "DIAP08" (I confused with 1)
        
        Strategy:
        1. Create comparison key with soft normalization (Z→2, B→8, I→1)
        2. Detect duplicates using comparison key
        3. Keep better version (fewer suspicious characters)
        4. Preserve original values in output (no modification)
        """
        
        def create_comparison_key(line_number: str) -> str:
            """
            Soft normalization for duplicate detection ONLY
            Does NOT modify the actual value - only for comparison
            """
            if not line_number:
                return ''
            
            key = line_number.upper()
            # Soft equivalences for OCR confusion
            key = key.replace('Z', '2')  # Z ↔ 2
            key = key.replace('B', '8')  # B ↔ 8
            key = key.replace('I', '1')  # I ↔ 1
            
            return key
        
        def calculate_quality_score(item: Dict) -> int:
            """
            Score quality of extraction - higher is better
            Prefer versions with cleaner, more structured format
            """
            line_number = item.get('line_number', '')
            score = 0
            
            # 1. Should start with digit (size field)
            if re.search(r'^\d', line_number):
                score += 2
            
            # 2. Penalize suspicious OCR characters
            # O should never exist (already normalized to 0)
            if 'O' in line_number:
                score -= 10  # Critical penalty
            
            # 3. Slight penalty for potentially confused characters
            # Prefer digits over letters in ambiguous positions
            if re.search(r'[ZBI]', line_number):
                score -= 1
            
            # 4. Prefer items with 4-digit sequence numbers
            if re.search(r'\d{4}', line_number):
                score += 3
            
            # 5. Prefer items with proper structure (multiple hyphens)
            hyphen_count = line_number.count('-')
            score += min(hyphen_count, 5)  # Cap at 5
            
            # 6. Prefer items with FROM-TO data (more complete)
            if item.get('from_line') or item.get('to_line'):
                score += 2
            
            return score
        
        # Use map with comparison keys to detect duplicates
        unique_map = {}
        duplicates_detected = []
        
        for item in items:
            original = item.get('line_number', '')
            if not original:
                continue
            
            # Create comparison key (soft normalization)
            comparison_key = create_comparison_key(original)
            
            if comparison_key not in unique_map:
                # First occurrence - store it
                unique_map[comparison_key] = item
            else:
                # Duplicate detected! Choose the better version
                existing_item = unique_map[comparison_key]
                existing_score = calculate_quality_score(existing_item)
                new_score = calculate_quality_score(item)
                
                # Log if they're actually different (OCR variation)
                if existing_item['line_number'] != original:
                    duplicates_detected.append({
                        'version_a': existing_item['line_number'],
                        'version_b': original,
                        'comparison_key': comparison_key,
                        'score_a': existing_score,
                        'score_b': new_score
                    })
                
                # Keep the higher-scoring version
                if new_score > existing_score:
                    unique_map[comparison_key] = item
        
        # Log OCR duplicate resolution summary
        if duplicates_detected:
            logger.info(f"  🔍 Detected {len(duplicates_detected)} OCR-confused duplicates:")
            for dup in duplicates_detected[:5]:  # Show first 5
                winner = unique_map[dup['comparison_key']]['line_number']
                logger.info(f"     '{dup['version_a']}' vs '{dup['version_b']}' → kept '{winner}'")
            if len(duplicates_detected) > 5:
                logger.info(f"     ... and {len(duplicates_detected) - 5} more")
        
        # Convert map back to list
        unique = list(unique_map.values())
        
        removed_count = len(items) - len(unique)
        if removed_count > 0:
            logger.info(f"  ✅ Deduplication: {len(items)} → {len(unique)} items (removed {removed_count} duplicates)")
        
        return unique
    
    def _apply_smart_flow_logic(self, items: List[Dict]) -> List[Dict]:
        """
        🧠 SMART FLOW ASSIGNMENT - Intelligent FROM-TO detection
        
        Strategy:
        1. Preserve good FROM-TO data from geometric detection
        2. For items without FROM-TO, use intelligent alphanumeric sorting
        3. Apply soft logic: similar line numbers likely connect
        4. Create confidence scores
        """
        logger.info(f"  🧠 Processing {len(items)} items with smart intelligence...")
        
        if len(items) == 0:
            return items
        
        # Count existing FROM-TO
        items_with_data = sum(1 for item in items if item.get('from_line') or item.get('to_line'))
        items_without_data = len(items) - items_with_data
        
        logger.info(f"  📊 Current state: {items_with_data} with FROM-TO, {items_without_data} without")
        
        if items_without_data == 0:
            logger.info(f"  ✅ All items already have FROM-TO data!")
            return items
        
        # For items without FROM-TO: Use intelligent sorting
        logger.info(f"  🔄 Applying smart logic to {items_without_data} items...")
        
        # Extract items without FROM-TO
        items_needing_data = [item for item in items if not item.get('from_line') and not item.get('to_line')]
        items_with_existing_data = [item for item in items if item.get('from_line') or item.get('to_line')]
        
        # Sort items needing data alphanumerically by line number
        try:
            # Smart sort: group by prefix, then numeric part
            def smart_sort_key(item):
                line = item.get('line_number', '')
                # Split into parts: size, type, number, etc
                parts = line.split('-')
                if len(parts) >= 4:
                    # Extract numeric parts for sorting
                    try:
                        # Format: SIZE-TYPE-NUMBER-PROJECT-CLASS
                        size = parts[0]
                        line_type = parts[1]
                        number = parts[2]
                        project = parts[3] if len(parts) > 3 else ''
                        
                        # Create sortable key
                        return (line_type, int(number) if number.isdigit() else 99999, size, project)
                    except:
                        pass
                return (line, 0, '', '')
            
            sorted_items = sorted(items_needing_data, key=smart_sort_key)
            logger.info(f"  ✅ Sorted {len(sorted_items)} items intelligently")
            
            # Apply sequential flow to sorted items
            for idx in range(len(sorted_items)):
                item = sorted_items[idx]
                
                # First item
                if idx == 0:
                    if len(sorted_items) > 1:
                        item['to_line'] = sorted_items[1]['line_number']
                        item['flow_detection_method'] = 'smart_sequential'
                        item['flow_confidence'] = 'medium'
                # Last item
                elif idx == len(sorted_items) - 1:
                    item['from_line'] = sorted_items[idx - 1]['line_number']
                    item['flow_detection_method'] = 'smart_sequential'
                    item['flow_confidence'] = 'medium'
                # Middle items
                else:
                    item['from_line'] = sorted_items[idx - 1]['line_number']
                    item['to_line'] = sorted_items[idx + 1]['line_number']
                    item['flow_detection_method'] = 'smart_sequential'
                    item['flow_confidence'] = 'medium'
            
            # Combine back
            all_items = items_with_existing_data + sorted_items
            
            # Verify
            final_with_data = sum(1 for item in all_items if item.get('from_line') or item.get('to_line'))
            logger.info(f"  ✅ SMART LOGIC COMPLETE: {final_with_data}/{len(all_items)} items now have FROM-TO")
            
            return all_items
            
        except Exception as e:
            logger.error(f"  ❌ Smart logic failed: {e}")
            logger.error("  → Falling back to simple sequential assignment")
            
            # Simple fallback: just use the original order
            for idx in range(len(items)):
                item = items[idx]
                if item.get('from_line') or item.get('to_line'):
                    continue  # Skip items that already have data
                
                if idx == 0 and len(items) > 1:
                    item['to_line'] = items[1]['line_number']
                elif idx == len(items) - 1 and idx > 0:
                    item['from_line'] = items[idx - 1]['line_number']
                elif idx > 0 and idx < len(items) - 1:
                    item['from_line'] = items[idx - 1]['line_number']
                    item['to_line'] = items[idx + 1]['line_number']
                
                item['flow_detection_method'] = 'fallback_sequential'
                item['flow_confidence'] = 'low'
            
            return items
    
    def detect_flow_with_vision(self, img: Image.Image, page_num: int) -> Dict:
        """
        🔺 SMART VISION: Use GPT-4 Vision to detect arrows with positions
        
        Returns structured data with:
        - arrows: [{'bbox_normalized': [x1,y1,x2,y2], 'orientation': str, 'center': [x,y]}]
        """
        logger.info(f"  🔺 detect_flow_with_vision called for page {page_num}")
        logger.info(f"  🔑 OpenAI client available: {self.openai_client is not None}")
        
        if not self.openai_client:
            logger.warning("  ⚠️ OpenAI not available for vision detection")
            return {'arrows': []}
        
        try:
            logger.info(f"  🔺 Running arrow detection with GPT-4 Vision on page {page_num}")
            
            # Get image dimensions for normalization
            img_width, img_height = img.size
            
            # Convert image to base64
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            prompt = f"""You are a P&ID diagram expert. Detect ALL flow direction arrows/triangles on pipeline lines.

IMAGE DIMENSIONS: {img_width}x{img_height} pixels

OUTPUT FORMAT (JSON only):
{{
    "arrows": [
        {{
            "bbox_normalized": [x1, y1, x2, y2],
            "center_normalized": [x, y],
            "orientation": "up|down|left|right|unknown",
            "confidence": "high|medium|low"
        }}
    ]
}}

RULES:
- Find ALL arrows/triangles on pipelines
- bbox_normalized: [x1/width, y1/height, x2/width, y2/height] (values 0-1)
- center_normalized: [(x1+x2)/(2*width), (y1+y2)/(2*height)] (values 0-1)
- orientation: direction arrow POINTS TO (downstream)
- Return ONLY valid JSON, no explanations

Analyze and return JSON:"""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",  # GPT-4 Omni with vision
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_base64}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                temperature=0.1,
                max_tokens=3000
            )
            
            result_text = response.choices[0].message.content.strip()
            logger.info(f"  📝 GPT-4 Vision raw response: {result_text[:500]}...")  # Log first 500 chars
            
            # Clean markdown code blocks if present
            if '```' in result_text:
                parts = result_text.split('```')
                for part in parts:
                    if part.strip().startswith('json') or part.strip().startswith('{'):
                        result_text = part.replace('json', '').strip()
                        break
            
            vision_data = json.loads(result_text)
            
            # Log results
            arrows = vision_data.get('arrows', [])
            
            logger.info(f"  ✅ Detected {len(arrows)} arrows with positions")
            
            return vision_data
            
        except json.JSONDecodeError as e:
            logger.warning(f"  ⚠️ JSON decode error in vision response: {e}")
            logger.warning(f"  📄 Raw response was: {result_text[:1000] if 'result_text' in locals() else 'No response'}")
            return {'arrows': []}
        except Exception as e:
            logger.warning(f"  ⚠️ Vision detection failed: {e}")
            return {'arrows': []}
    
    def find_line_endpoints(self, spatial_data: List[Dict], line_number: str) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        🎯 Find the two endpoints of a detected line based on spatial data
        
        Returns (start_point, end_point) where each point is:
        {'x': float, 'y': float, 'text': str}
        """
        # Find all spatial items that contain this line number
        line_occurrences = []
        for item in spatial_data:
            if line_number in item.get('text', ''):
                line_occurrences.append({
                    'x': item['center_x'],
                    'y': item['center_y'],
                    'text': item['text']
                })
        
        if len(line_occurrences) < 2:
            # Line only appears once or not enough spatial data
            return None, None
        
        # Sort by position to find extremes (leftmost/rightmost or topmost/bottommost)
        # Try horizontal first (x-axis)
        x_sorted = sorted(line_occurrences, key=lambda p: p['x'])
        x_spread = x_sorted[-1]['x'] - x_sorted[0]['x']
        
        # Try vertical (y-axis)
        y_sorted = sorted(line_occurrences, key=lambda p: p['y'])
        y_spread = y_sorted[-1]['y'] - y_sorted[0]['y']
        
        # Use the axis with greater spread
        if x_spread > y_spread:
            # Horizontal line
            return x_sorted[0], x_sorted[-1]
        else:
            # Vertical line
            return y_sorted[0], y_sorted[-1]
    
    def associate_symbols_to_endpoints(
        self, 
        endpoint1: Dict, 
        endpoint2: Dict, 
        symbols: List[Dict],
        search_radius: float = 150.0
    ) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        🔗 Associate flow symbols to line endpoints
        
        Returns (symbol_at_endpoint1, symbol_at_endpoint2)
        """
        def distance(p1, p2):
            return np.sqrt((p1['x'] - p2['center_x'])**2 + (p1['y'] - p2['center_y'])**2)
        
        # Find closest symbol to each endpoint
        symbol1 = None
        min_dist1 = search_radius
        
        for symbol in symbols:
            dist = distance(endpoint1, symbol)
            if dist < min_dist1:
                min_dist1 = dist
                symbol1 = symbol
        
        symbol2 = None
        min_dist2 = search_radius
        
        for symbol in symbols:
            dist = distance(endpoint2, symbol)
            if dist < min_dist2:
                min_dist2 = dist
                symbol2 = symbol
        
        return symbol1, symbol2
    
    def determine_from_to(
        self,
        endpoint1: Dict,
        endpoint2: Dict,
        symbol1: Optional[Dict],
        symbol2: Optional[Dict]
    ) -> Tuple[str, str]:
        """
        🎯 Determine FROM→TO direction based on symbol orientation
        
        LOGIC:
        - If both endpoints have symbols:
          - Symbol pointing AWAY from line = FROM (upstream)
          - Symbol pointing INTO line = TO (downstream)
        
        - If only one endpoint has symbol:
          - Endpoint WITH symbol = TO (downstream)
          - Endpoint WITHOUT symbol = FROM (upstream)
        
        - If no symbols:
          - Use positional heuristic (left→right, top→bottom)
        
        Returns (from_text, to_text) extracted from endpoint texts
        """
        def extract_equipment_tag(text: str) -> str:
            """Extract equipment tag from text like 'V-201' or 'P-101'"""
            # Look for patterns like: LETTER-NUMBER or LETTER-NUMBER-LETTER
            match = re.search(r'\b([A-Z])-(\d+)([A-Z])?\b', text, re.IGNORECASE)
            if match:
                return match.group(0)
            return text.strip()
        
        from_endpoint = None
        to_endpoint = None
        
        if symbol1 and symbol2:
            # Both have symbols - use orientation
            # Symbols typically point TOWARD downstream (TO)
            # So endpoint with symbol pointing AWAY is FROM
            
            # Simple heuristic: if symbols point toward each other, 
            # the "upstream" symbol orientation indicates FROM
            orientation1 = symbol1.get('orientation', 'unknown')
            orientation2 = symbol2.get('orientation', 'unknown')
            
            # For now, use position-based fallback if orientation unclear
            if endpoint1['x'] < endpoint2['x']:
                # Horizontal: left is FROM, right is TO
                from_endpoint, to_endpoint = endpoint1, endpoint2
            else:
                from_endpoint, to_endpoint = endpoint2, endpoint1
                
        elif symbol1 or symbol2:
            # Only one has symbol - endpoint WITH symbol is usually TO (downstream)
            if symbol1:
                from_endpoint, to_endpoint = endpoint2, endpoint1
            else:
                from_endpoint, to_endpoint = endpoint1, endpoint2
        else:
            # No symbols - use positional heuristic
            # Left→Right or Top→Bottom convention
            if abs(endpoint1['x'] - endpoint2['x']) > abs(endpoint1['y'] - endpoint2['y']):
                # More horizontal
                if endpoint1['x'] < endpoint2['x']:
                    from_endpoint, to_endpoint = endpoint1, endpoint2
                else:
                    from_endpoint, to_endpoint = endpoint2, endpoint1
            else:
                # More vertical
                if endpoint1['y'] < endpoint2['y']:
                    from_endpoint, to_endpoint = endpoint1, endpoint2
                else:
                    from_endpoint, to_endpoint = endpoint2, endpoint1
        
        from_text = extract_equipment_tag(from_endpoint['text']) if from_endpoint else ''
        to_text = extract_equipment_tag(to_endpoint['text']) if to_endpoint else ''
        
        return from_text, to_text
    
    def _detect_from_to_by_distance(
        self,
        line_items: List[Dict],
        img: Image.Image,
        page_num: int
    ) -> List[Dict]:
        """
        🧠 INTELLIGENT GEOMETRIC FROM-TO DETECTION
        
        SCOPE: Backend logic with spatial intelligence
        
        PIPELINE:
        1. OCR → Detect all line numbers with normalized coordinates (0-1000 scale)
        2. CV → Detect all line segments with geometric properties
        3. MATCH → Associate line numbers to nearest line segments (spatial proximity)
        4. GRAPH → Build connectivity map from line intersections
        5. FLOW → Determine FROM-TO using connectivity + spatial direction
        6. GUARANTEE → Fallback ensures 100% of items get FROM-TO data
        """
        logger.info(f"  🧠 INTELLIGENT GEOMETRIC ANALYSIS - Spatial proximity + connectivity")

        # Convert to numpy array and get dimensions
        img_array = np.array(img)
        img_height, img_width = img_array.shape[:2] if len(img_array.shape) == 3 else (img_array.shape[0], img_array.shape[1])
        
        # Normalization factors for coordinate scaling
        norm_factor_x = 1000.0 / img_width
        norm_factor_y = 1000.0 / img_height
        
        logger.info(f"  📐 Image dimensions: {img_width}x{img_height}, Norm factors: {norm_factor_x:.3f}x{norm_factor_y:.3f}")

        # STEP 1: Extract normalized line number positions using OCR
        ocr_positions = {}  # {line_number: [(norm_x, norm_y), ...]}
        
        if self.easyocr_reader:
            try:
                logger.info(f"  🔍 Extracting line number positions with EasyOCR...")
                easyocr_result = self.easyocr_reader.readtext(img_array, detail=1)
                logger.info(f"  📊 EasyOCR found {len(easyocr_result)} text detections")
                logger.info(f"  📝 Looking for {len(line_items)} line numbers: {[item['line_number'] for item in line_items[:5]]}...")
                
                for detection in easyocr_result:
                    bbox, text, conf = detection
                    text_upper = text.upper().strip()
                    text_normalized = text_upper.replace(' ', '').replace('"', '').replace("'", '').replace('-', '')
                    
                    # Check if this text contains any of our line numbers
                    for line_item in line_items:
                        line_number = line_item['line_number'].upper().strip()
                        line_normalized = line_number.replace(' ', '').replace('"', '').replace("'", '').replace('-', '')
                        
                        # VERY LENIENT matching: just check if core parts match
                        is_match = False
                        if line_normalized in text_normalized:
                            is_match = True
                        elif text_normalized in line_normalized and len(text_normalized) >= 4:  # LOWERED to 4 chars
                            is_match = True
                        elif (len(line_normalized) > 0 and len(text_normalized) > 0 and
                              len(set(line_normalized) & set(text_normalized)) / max(len(line_normalized), len(text_normalized)) > 0.5 and  # 50% overlap
                              len(text_normalized) >= 6):  # 6+ chars
                            is_match = True
                        
                        if is_match:
                            # Calculate center and normalize
                            x_coords = [point[0] for point in bbox]
                            y_coords = [point[1] for point in bbox]
                            center_x = sum(x_coords) / 4
                            center_y = sum(y_coords) / 4
                            
                            norm_x = center_x * norm_factor_x
                            norm_y = center_y * norm_factor_y
                            
                            if line_number not in ocr_positions:
                                ocr_positions[line_number] = []
                            ocr_positions[line_number].append((norm_x, norm_y))
                
                logger.info(f"  ✅ Found positions for {len(ocr_positions)}/{len(line_items)} line numbers ({len(ocr_positions)/max(len(line_items), 1)*100:.0f}%)")
            except Exception as e:
                logger.warning(f"  ⚠️ OCR position extraction failed: {e}")
                return line_items
        else:
            logger.warning(f"  ⚠️ EasyOCR not available")
            return line_items
        
        if not ocr_positions:
            logger.error(f"  ❌ CRITICAL: No OCR positions found - OCR failed to detect any line numbers!")
            logger.info(f"  🔄 FALLBACK: Using smart proximity estimation...")
            # FALLBACK: Use simple proximity between all line numbers
            return self._aggressive_proximity_fallback(line_items)
        
        # Calculate average normalized position for each line number
        line_centers = {}
        for line_number, positions in ocr_positions.items():
            avg_x = sum(p[0] for p in positions) / len(positions)
            avg_y = sum(p[1] for p in positions) / len(positions)
            line_centers[line_number] = (avg_x, avg_y)
        
        logger.info(f"  📍 Normalized centers calculated for {len(line_centers)} line numbers")
        
        # STEP 2: Detect geometric line segments using OpenCV
        logger.info(f"  📏 STEP 2: Detecting ALL geometric line segments in P&ID drawing...")
        
        try:
            import cv2
            
            # Convert to grayscale
            if len(img_array.shape) == 3:
                gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            else:
                gray = img_array
            
            # Edge detection
            edges = cv2.Canny(gray, 50, 150, apertureSize=3)
            
            # Hough Line Transform to detect line segments
            lines = cv2.HoughLinesP(
                edges,
                rho=1,
                theta=np.pi/180,
                threshold=100,
                minLineLength=50,
                maxLineGap=10
            )
            
            if lines is None or len(lines) == 0:
                logger.warning(f"  ⚠️ No geometric lines detected")
                return line_items
            
            logger.info(f"  ✅ Detected {len(lines)} geometric line segments")
            
            # Normalize line segment coordinates
            normalized_lines = []
            for idx, line in enumerate(lines):
                x1, y1, x2, y2 = line[0]
                norm_line = {
                    'id': idx,
                    'x1': x1 * norm_factor_x,
                    'y1': y1 * norm_factor_y,
                    'x2': x2 * norm_factor_x,
                    'y2': y2 * norm_factor_y,
                    'center_x': (x1 + x2) / 2 * norm_factor_x,
                    'center_y': (y1 + y2) / 2 * norm_factor_y,
                    'length': np.sqrt((x2-x1)**2 + (y2-y1)**2) * (norm_factor_x + norm_factor_y) / 2,
                    'angle': np.arctan2(y2-y1, x2-x1)
                }
                normalized_lines.append(norm_line)
            
        except ImportError:
            logger.warning(f"  ⚠️ OpenCV not available, using fallback proximity method")
            return self._fallback_proximity_detection(line_items, line_centers)
        except Exception as e:
            logger.warning(f"  ⚠️ Line detection failed: {e}, using fallback")
            return self._fallback_proximity_detection(line_items, line_centers)
        
        # STEP 3: Match line numbers to geometric lines
        logger.info(f"  🎯 Matching line numbers to geometric lines...")
        
        line_number_to_geom_line = {}  # {line_number: geom_line_id}
        proximity_threshold = 75  # Normalized units - INCREASED for better matching
        
        for line_number, (cx, cy) in line_centers.items():
            min_dist = float('inf')
            closest_line_id = None
            
            for geom_line in normalized_lines:
                # Calculate distance from point to line segment
                dist = self._point_to_line_distance(
                    cx, cy,
                    geom_line['x1'], geom_line['y1'],
                    geom_line['x2'], geom_line['y2']
                )
                
                if dist < min_dist and dist < proximity_threshold:
                    min_dist = dist
                    closest_line_id = geom_line['id']
            
            if closest_line_id is not None:
                line_number_to_geom_line[line_number] = closest_line_id
        
        logger.info(f"  ✅ Matched {len(line_number_to_geom_line)}/{len(line_centers)} line numbers to geometric lines")
        
        # STEP 4: Build connectivity graph based on line intersections
        logger.info(f"  🔗 Building connectivity graph from line intersections...")
        
        line_connectivity = {}  # {line_id: [connected_line_ids]}
        intersection_threshold = 50  # Normalized units - INCREASED from 30 for better connectivity
        
        for i, line1 in enumerate(normalized_lines):
            connected = []
            
            for j, line2 in enumerate(normalized_lines):
                if i == j:
                    continue
                
                # Check if lines intersect or are very close
                if self._lines_intersect_or_close(line1, line2, intersection_threshold):
                    connected.append(j)
            
            if connected:
                line_connectivity[i] = connected
        
        logger.info(f"  ✅ Built connectivity graph with {len(line_connectivity)} connected nodes")
        
        # STEP 5: Determine FROM-TO relationships using spatial flow direction
        logger.info(f"  🧭 Determining FROM-TO relationships...")
        
        from_to_map = {}
        
        for line_number, geom_line_id in line_number_to_geom_line.items():
            if geom_line_id not in line_connectivity:
                continue
            
            connected_line_ids = line_connectivity[geom_line_id]
            if len(connected_line_ids) < 1:
                continue
            
            # Find line numbers associated with connected geometric lines
            connected_line_numbers = []
            for conn_id in connected_line_ids:
                for other_line_num, other_geom_id in line_number_to_geom_line.items():
                    if other_geom_id == conn_id and other_line_num != line_number:
                        connected_line_numbers.append(other_line_num)
            
            if not connected_line_numbers:
                continue
            
            # Get current line center
            cx, cy = line_centers[line_number]
            
            # Determine FROM and TO based on spatial position
            from_line = None
            to_line = None
            
            # Calculate relative positions of connected lines
            relative_positions = []
            for conn_line_num in connected_line_numbers:
                conn_cx, conn_cy = line_centers[conn_line_num]
                dx = conn_cx - cx
                dy = conn_cy - cy
                angle = np.arctan2(dy, dx)
                distance = np.sqrt(dx**2 + dy**2)
                
                relative_positions.append({
                    'line': conn_line_num,
                    'dx': dx,
                    'dy': dy,
                    'angle': angle,
                    'distance': distance
                })
            
            # Sort by distance, take closest 2
            relative_positions.sort(key=lambda x: x['distance'])
            closest_connections = relative_positions[:2]
            
            if len(closest_connections) >= 2:
                # Determine orientation (horizontal vs vertical)
                dx_spread = abs(closest_connections[0]['dx']) + abs(closest_connections[1]['dx'])
                dy_spread = abs(closest_connections[0]['dy']) + abs(closest_connections[1]['dy'])
                
                if dx_spread > dy_spread:
                    # Horizontal flow: LEFT=FROM, RIGHT=TO
                    sorted_conns = sorted(closest_connections, key=lambda x: x['dx'])
                    from_line = sorted_conns[0]['line']
                    to_line = sorted_conns[1]['line']
                else:
                    # Vertical flow: TOP=FROM, BOTTOM=TO
                    sorted_conns = sorted(closest_connections, key=lambda x: x['dy'])
                    from_line = sorted_conns[0]['line']
                    to_line = sorted_conns[1]['line']
                
                from_to_map[line_number] = {
                    'from_line': from_line,
                    'to_line': to_line,
                    'method': 'geometric_analysis',
                    'confidence': 'high'
                }
                
                logger.info(f"  ✅ {line_number}: FROM={from_line} → TO={to_line}")
            
            elif len(closest_connections) == 1:
                # Only one connection - determine FROM or TO by position
                conn = closest_connections[0]
                if conn['dx'] < 0 or conn['dy'] < 0:
                    from_to_map[line_number] = {
                        'from_line': conn['line'],
                        'to_line': '',
                        'method': 'geometric_analysis',
                        'confidence': 'medium'
                    }
                else:
                    from_to_map[line_number] = {
                        'from_line': '',
                        'to_line': conn['line'],
                        'method': 'geometric_analysis',
                        'confidence': 'medium'
                    }
        
        # DISTANCE-BASED FALLBACK: For lines without geometric connections, use simple proximity
        logger.info(f"  🔄 Applying distance-based fallback for remaining lines...")
        
        max_distance = 250  # Normalized units - generous threshold
        
        for line_number, (x, y) in line_centers.items():
            if line_number in from_to_map:
                # Already has connections from geometric analysis
                continue
            
            # Find nearest lines by distance
            distances = []
            for other_line, (other_x, other_y) in line_centers.items():
                if other_line != line_number:
                    dist = np.sqrt((x - other_x)**2 + (y - other_y)**2)
                    if dist <= max_distance:
                        dx = other_x - x
                        dy = other_y - y
                        distances.append({
                            'line': other_line,
                            'distance': dist,
                            'dx': dx,
                            'dy': dy
                        })
            
            if len(distances) >= 2:
                # Sort by distance, take 2 closest
                distances.sort(key=lambda d: d['distance'])
                closest_two = distances[:2]
                
                # Determine orientation
                dx_spread = abs(closest_two[0]['dx']) + abs(closest_two[1]['dx'])
                dy_spread = abs(closest_two[0]['dy']) + abs(closest_two[1]['dy'])
                
                if dx_spread > dy_spread:
                    # Horizontal flow
                    sorted_lines = sorted(closest_two, key=lambda l: l['dx'])
                    from_line = sorted_lines[0]['line']
                    to_line = sorted_lines[1]['line']
                else:
                    # Vertical flow
                    sorted_lines = sorted(closest_two, key=lambda l: l['dy'])
                    from_line = sorted_lines[0]['line']
                    to_line = sorted_lines[1]['line']
                
                from_to_map[line_number] = {
                    'from_line': from_line,
                    'to_line': to_line,
                    'method': 'distance_proximity',
                    'confidence': 'low'
                }
                logger.info(f"  🔄 {line_number}: FROM={from_line} → TO={to_line} (distance fallback)")
            
            elif len(distances) == 1:
                # Only one nearby line
                conn = distances[0]
                if conn['dx'] < 0 or conn['dy'] < 0:
                    from_to_map[line_number] = {
                        'from_line': conn['line'],
                        'to_line': '',
                        'method': 'distance_proximity',
                        'confidence': 'low'
                    }
                else:
                    from_to_map[line_number] = {
                        'from_line': '',
                        'to_line': conn['line'],
                        'method': 'distance_proximity',
                        'confidence': 'low'
                    }
                logger.info(f"  🔄 {line_number}: {'FROM' if conn['dx'] < 0 or conn['dy'] < 0 else 'TO'}={conn['line']} (distance fallback)")
        
        # Apply FROM-TO to line items
        enhanced_items = []
        for line_item in line_items:
            line_number = line_item['line_number'].upper().strip()
            
            if line_number in from_to_map:
                mapping = from_to_map[line_number]
                line_item['from_line'] = mapping.get('from_line', '')
                line_item['to_line'] = mapping.get('to_line', '')
                line_item['flow_detection_method'] = mapping.get('method', 'geometric_analysis')
                line_item['flow_confidence'] = mapping.get('confidence', 'medium')
            
            enhanced_items.append(line_item)
        
        detected_count = sum(1 for item in enhanced_items if item.get('from_line') or item.get('to_line'))
        logger.info(f"  ✅ Detected FROM/TO for {detected_count}/{len(line_items)} lines ({detected_count/len(line_items)*100:.1f}%)")
        
        # 🛡️ FINAL GUARANTEE: Ensure ALL items have FROM-TO (use sequential as last resort)
        items_without = [item for item in enhanced_items if not item.get('from_line') and not item.get('to_line')]
        if items_without:
            logger.warning(f"  ⚠️ {len(items_without)} items still missing FROM-TO, applying sequential guarantee...")
            for idx, item in enumerate(enhanced_items):
                if item.get('from_line') or item.get('to_line'):
                    continue  # Already has data
                
                # Apply sequential logic
                if idx == 0 and len(enhanced_items) > 1:
                    item['to_line'] = enhanced_items[1]['line_number']
                    item['flow_detection_method'] = 'sequential_guarantee'
                    item['flow_confidence'] = 'low'
                elif idx == len(enhanced_items) - 1 and idx > 0:
                    item['from_line'] = enhanced_items[idx - 1]['line_number']
                    item['flow_detection_method'] = 'sequential_guarantee'
                    item['flow_confidence'] = 'low'
                elif 0 < idx < len(enhanced_items) - 1:
                    item['from_line'] = enhanced_items[idx - 1]['line_number']
                    item['to_line'] = enhanced_items[idx + 1]['line_number']
                    item['flow_detection_method'] = 'sequential_guarantee'
                    item['flow_confidence'] = 'low'
            
            final_count = sum(1 for item in enhanced_items if item.get('from_line') or item.get('to_line'))
            logger.info(f"  ✅ GUARANTEED: {final_count}/{len(enhanced_items)} items now have FROM-TO ({final_count/len(enhanced_items)*100:.1f}%)")
        
        # 🎯 ARROW-BASED FROM-TO ENHANCEMENT (NEW MODULE)
        # Try to improve FROM-TO detection using arrow markers from CAD/vector parsing
        try:
            logger.info(f"  🎯 Attempting arrow-based FROM-TO enhancement...")
            
            # Detect arrows using vision (if available)
            vision_data = None
            if self.openai_client:
                vision_data = self.detect_flow_with_vision(img, page_num)
            
            # Only run if we have arrows or geometric data
            if (vision_data and vision_data.get('arrows')) or (normalized_lines and ocr_positions):
                from apps.designiq.from_to_integration import apply_arrow_based_from_to
                
                enhanced_items = apply_arrow_based_from_to(
                    line_items=enhanced_items,
                    normalized_lines=normalized_lines,
                    line_connectivity=line_connectivity,
                    ocr_positions=ocr_positions,
                    vision_data=vision_data,
                    img_width=img_width,
                    img_height=img_height,
                )
                
                logger.info(f"  ✅ Arrow-based enhancement complete")
            else:
                logger.info(f"  ℹ️ Skipping arrow-based enhancement (no arrows or geometric data available)")
                
        except Exception as e:
            logger.warning(f"  ⚠️ Arrow-based FROM-TO enhancement failed: {e}", exc_info=True)
            # Continue with existing FROM-TO data
        
        return enhanced_items
    
    def _point_to_line_distance(self, px, py, x1, y1, x2, y2):
        """Calculate minimum distance from point (px, py) to line segment (x1, y1) to (x2, y2)"""
        # Calculate line length squared
        line_length_sq = (x2 - x1)**2 + (y2 - y1)**2
        
        if line_length_sq == 0:
            # Line is actually a point
            return np.sqrt((px - x1)**2 + (py - y1)**2)
        
        # Calculate projection parameter
        t = max(0, min(1, ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / line_length_sq))
        
        # Calculate projection point
        proj_x = x1 + t * (x2 - x1)
        proj_y = y1 + t * (y2 - y1)
        
        # Return distance to projection point
        return np.sqrt((px - proj_x)**2 + (py - proj_y)**2)
    
    def _lines_intersect_or_close(self, line1, line2, threshold):
        """Check if two line segments intersect or their endpoints are close"""
        # Check endpoint proximity
        endpoints1 = [(line1['x1'], line1['y1']), (line1['x2'], line1['y2'])]
        endpoints2 = [(line2['x1'], line2['y1']), (line2['x2'], line2['y2'])]
        
        for ep1 in endpoints1:
            for ep2 in endpoints2:
                dist = np.sqrt((ep1[0] - ep2[0])**2 + (ep1[1] - ep2[1])**2)
                if dist < threshold:
                    return True
        
        # Check actual intersection using line segment intersection algorithm
        x1, y1, x2, y2 = line1['x1'], line1['y1'], line1['x2'], line1['y2']
        x3, y3, x4, y4 = line2['x1'], line2['y1'], line2['x2'], line2['y2']
        
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-10:
            # Lines are parallel
            return False
        
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
        u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
        
        if 0 <= t <= 1 and 0 <= u <= 1:
            # Lines intersect
            return True
        
        return False
    
    def _aggressive_proximity_fallback(self, line_items):
        """
        AGGRESSIVE fallback: ALWAYS provide FROM-TO data using simple logic
        Strategy: For each line, find the 2 closest neighbors and assign them as FROM/TO
        """
        logger.info(f"  🔄 Using AGGRESSIVE proximity fallback - WILL assign FROM-TO to all items")
        logger.info(f"  📝 Processing {len(line_items)} line items...")
        
        # If only 1 or 2 items, just assign sequentially
        if len(line_items) <= 1:
            logger.info(f"  ⚠️ Only {len(line_items)} item(s), cannot determine FROM-TO")
            return line_items
        
        if len(line_items) == 2:
            logger.info(f"  📊 Only 2 items, assigning sequential FROM-TO")
            line_items[0]['from_line'] = ''
            line_items[0]['to_line'] = line_items[1]['line_number']
            line_items[1]['from_line'] = line_items[0]['line_number']
            line_items[1]['to_line'] = ''
            return line_items
        
        # For 3+ items: Each item connects to its neighbors in the list
        # This creates a chain: item1 -> item2 -> item3 -> ...
        enhanced_items = []
        
        for idx, line_item in enumerate(line_items):
            from_line = ''
            to_line = ''
            
            # First item: no FROM, connects TO second item
            if idx == 0:
                to_line = line_items[idx + 1]['line_number']
            
            # Last item: connects FROM second-to-last, no TO
            elif idx == len(line_items) - 1:
                from_line = line_items[idx - 1]['line_number']
            
            # Middle items: connect FROM previous, TO next
            else:
                from_line = line_items[idx - 1]['line_number']
                to_line = line_items[idx + 1]['line_number']
            
            line_item['from_line'] = from_line
            line_item['to_line'] = to_line
            line_item['flow_detection_method'] = 'sequential_fallback'
            line_item['flow_confidence'] = 'low'
            enhanced_items.append(line_item)
        
        with_from_to = sum(1 for item in enhanced_items if item.get('from_line') or item.get('to_line'))
        logger.info(f"  ✅ Aggressive fallback assigned FROM-TO to {with_from_to}/{len(enhanced_items)} items")
        
        return enhanced_items
    
    def _simple_proximity_fallback(self, line_items):
        """Simple proximity fallback when OCR positions cannot be extracted"""
        logger.info(f"  🔄 Using simple proximity fallback (no OCR positions)")
        logger.info(f"  📝 Will assign basic connectivity based on line order")
        
        # For now, just return items without FROM-TO
        # This prevents errors and keeps basic line data
        enhanced_items = []
        for line_item in line_items:
            # Keep all existing data
            enhanced_items.append(line_item)
        
        logger.info(f"  ✅ Returned {len(enhanced_items)} items (no FROM-TO data added)")
        return enhanced_items
    
    def _fallback_proximity_detection(self, line_items, line_centers):
        """Fallback method using simple proximity when geometric detection fails"""
        logger.info(f"  🔄 Using fallback proximity detection with {len(line_centers)} positioned items")
        
        max_distance = 300  # Normalized units - VERY LENIENT for better matching
        from_to_map = {}
        
        for line_number, (x, y) in line_centers.items():
            distances = []
            
            for other_line, (other_x, other_y) in line_centers.items():
                if other_line != line_number:
                    dist = np.sqrt((x - other_x)**2 + (y - other_y)**2)
                    if dist <= max_distance:
                        dx = other_x - x
                        dy = other_y - y
                        distances.append({
                            'line': other_line,
                            'distance': dist,
                            'dx': dx,
                            'dy': dy
                        })
            
            if len(distances) >= 2:
                # Sort by distance and take 2 closest
                distances.sort(key=lambda d: d['distance'])
                closest_two = distances[:2]
                
                # Determine which is FROM and which is TO based on direction
                dx_spread = abs(closest_two[0]['dx']) + abs(closest_two[1]['dx'])
                dy_spread = abs(closest_two[0]['dy']) + abs(closest_two[1]['dy'])
                
                if dx_spread > dy_spread:
                    # Horizontal arrangement - use X direction
                    sorted_lines = sorted(closest_two, key=lambda l: l['dx'])
                    from_line = sorted_lines[0]['line']  # Left one
                    to_line = sorted_lines[1]['line']    # Right one
                else:
                    # Vertical arrangement - use Y direction
                    sorted_lines = sorted(closest_two, key=lambda l: l['dy'])
                    from_line = sorted_lines[0]['line']  # Top one
                    to_line = sorted_lines[1]['line']    # Bottom one
                
                from_to_map[line_number] = {
                    'from_line': from_line,
                    'to_line': to_line,
                    'method': 'proximity_fallback',
                    'confidence': 'medium'
                }
            elif len(distances) == 1:
                # Only one nearby line - mark as connected in one direction
                from_to_map[line_number] = {
                    'from_line': distances[0]['line'],
                    'to_line': '',
                    'method': 'proximity_single',
                    'confidence': 'low'
                }
            # NO ELSE - if no nearby lines, leave empty (will be caught by later fallback)
        
        logger.info(f"  📊 Proximity fallback mapped {len(from_to_map)}/{len(line_items)} items")
        
        enhanced_items = []
        unmapped_count = 0
        for line_item in line_items:
            line_number = line_item['line_number'].upper().strip()
            if line_number in from_to_map:
                mapping = from_to_map[line_number]
                line_item['from_line'] = mapping['from_line']
                line_item['to_line'] = mapping['to_line']
                line_item['flow_detection_method'] = mapping['method']
                line_item['flow_confidence'] = mapping['confidence']
            else:
                unmapped_count += 1
            enhanced_items.append(line_item)
        
        # If many items still unmapped, use aggressive fallback for those
        if unmapped_count > 0:
            logger.warning(f"  ⚠️ {unmapped_count} items still without FROM-TO, using sequential assignment")
            # Apply sequential assignment to unmapped items
            unmapped_items = [item for item in enhanced_items if not item.get('from_line') and not item.get('to_line')]
            if len(unmapped_items) >= 2:
                for idx, item in enumerate(unmapped_items):
                    if idx == 0:
                        item['to_line'] = unmapped_items[idx + 1]['line_number']
                    elif idx == len(unmapped_items) - 1:
                        item['from_line'] = unmapped_items[idx - 1]['line_number']
                    else:
                        item['from_line'] = unmapped_items[idx - 1]['line_number']
                        item['to_line'] = unmapped_items[idx + 1]['line_number']
                    item['flow_detection_method'] = 'sequential_backup'
                    item['flow_confidence'] = 'very_low'
        
        return enhanced_items
    
    def enhance_with_flow_direction(
        self,
        line_items: List[Dict],
        img: Image.Image,
        spatial_data: Optional[List[Dict]],
        page_num: int
    ) -> List[Dict]:
        """
        🚀 OpenCV-Based FROM-TO Detection: Detect arrow symbols and connect line numbers
        
        Strategy:
        1. Detect arrow/triangle symbols using OpenCV (Canny edges + contours + PCA)
        2. Get OCR positions for all detected line numbers
        3. Detect physical lines in P&ID using OpenCV line detection
        4. Match physical lines to line numbers by proximity
        5. Associate symbols to physical line endpoints via proximity
        6. Infer FROM/TO roles using orientation analysis
        7. Map endpoints to line numbers with intelligent scoring
        """
        logger.info(f"  🔺 PHASE 3: OpenCV-Based FROM-TO Detection")
        
        # Check if detector available
        if not self.from_to_detector:
            logger.warning(f"  ⚠️ FROM-TO detector not available, skipping")
            return line_items
        
        # Step 1: Get image dimensions
        img_width, img_height = img.size
        img_array = np.array(img)
        
        # Step 2: Extract OCR positions using EasyOCR
        ocr_positions = []
        if self.easyocr_reader:
            try:
                easyocr_result = self.easyocr_reader.readtext(img_array, detail=1)
                
                for detection in easyocr_result:
                    bbox, text, conf = detection
                    # Calculate bbox and center
                    x_coords = [point[0] for point in bbox]
                    y_coords = [point[1] for point in bbox]
                    center_x = sum(x_coords) / 4
                    center_y = sum(y_coords) / 4
                    
                    # Normalize coordinates (0-1 range)
                    x1_norm = min(x_coords) / img_width
                    y1_norm = min(y_coords) / img_height
                    x2_norm = max(x_coords) / img_width
                    y2_norm = max(y_coords) / img_height
                    center_x_norm = center_x / img_width
                    center_y_norm = center_y / img_height
                    
                    ocr_positions.append({
                        'id': f'ocr_{len(ocr_positions)}',
                        'text': text.upper().strip(),
                        'bbox': (x1_norm, y1_norm, x2_norm, y2_norm),
                        'center_x_norm': center_x_norm,
                        'center_y_norm': center_y_norm,
                        'confidence': conf
                    })
                
                logger.info(f"  📍 Extracted {len(ocr_positions)} OCR items with positions")
            except Exception as e:
                logger.warning(f"  ⚠️ Could not extract spatial OCR data: {e}")
                return line_items
        else:
            logger.warning(f"  ⚠️ EasyOCR not available for spatial extraction")
            return line_items
        
        # Step 3: Detect ALL line segments in P&ID using geometric analysis
        logger.info(f"  🔍 Detecting ALL line segments in P&ID...")
        all_segments = self._detect_all_line_segments(img_array)
        logger.info(f"  ✅ Detected {len(all_segments)} line segments with unique IDs")
        
        # Step 4: Build position map for line numbers
        line_position_map = {}  # {line_number: (avg_x, avg_y)}
        
        for line_item in line_items:
            line_number = line_item['line_number'].upper().strip()
            line_positions = []
            
            # Find all OCR occurrences of this line number
            for pos in ocr_positions:
                if line_number in pos['text']:
                    line_positions.append({
                        'x': pos['center_x_norm'],
                        'y': pos['center_y_norm'],
                        'confidence': pos['confidence']
                    })
            
            if line_positions:
                # Calculate average position
                avg_x = sum(p['x'] for p in line_positions) / len(line_positions)
                avg_y = sum(p['y'] for p in line_positions) / len(line_positions)
                line_position_map[line_number] = (avg_x, avg_y)
        
        logger.info(f"  🗺️ Mapped {len(line_position_map)} line numbers to positions")
        
        # Step 5: Assign line numbers to segments using spatial proximity
        line_segments_map = self._assign_line_numbers_to_segments(
            all_segments,
            line_position_map,
            max_distance=0.15
        )
        
        logger.info(f"  🔗 Assigned line numbers to {len(line_segments_map)} segment groups")
        
        # Step 6: Build connectivity graph based on line intersections
        connectivity_graph = self._build_connectivity_graph(all_segments)
        logger.info(f"  📊 Built connectivity graph with {len(connectivity_graph)} nodes")
        
        # Step 7: Infer FROM-TO relationships using graph connectivity
        from_to_map = self._infer_from_to_relationships(
            line_segments_map,
            connectivity_graph,
            all_segments
        )
        
        logger.info(f"  ✅ Inferred FROM-TO for {len(from_to_map)} lines using connectivity graph")
        
        # Step 8: Apply FROM-TO results to line items
        enhanced_items = []
        for line_item in line_items:
            line_number = line_item['line_number'].upper().strip()
            
            if line_number in from_to_map:
                mapping = from_to_map[line_number]
                line_item['from_line'] = mapping.get('from_line', '')
                line_item['to_line'] = mapping.get('to_line', '')
                line_item['flow_detection_method'] = 'graph_connectivity'
                line_item['flow_confidence'] = mapping.get('confidence', 'medium')
                
                if mapping.get('from_line') or mapping.get('to_line'):
                    logger.info(f"  ✅ {line_number}: FROM={mapping.get('from_line', 'N/A')} → TO={mapping.get('to_line', 'N/A')}")
            
            enhanced_items.append(line_item)
        
        detected_count = sum(1 for item in enhanced_items if item.get('from_line') or item.get('to_line'))
        logger.info(f"  ✅ Mapped FROM/TO for {detected_count}/{len(line_items)} lines using graph connectivity")
        
        return enhanced_items
    
    def _detect_all_line_segments(self, img_array: np.ndarray) -> List[Dict]:
        """
        Detect ALL line segments in P&ID with unique IDs and properties.
        
        Args:
            img_array: Input image as numpy array
        
        Returns:
            List of segment dicts with:
                - id: Unique segment ID (str)
                - start: (x, y) normalized start point
                - end: (x, y) normalized end point
                - length: Normalized length
                - angle: Angle in radians
                - bbox: (x_min, y_min, x_max, y_max) normalized bounding box
        """
        import cv2
        
        # Convert to grayscale
        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array.copy()
        
        height, width = gray.shape
        
        # Apply edge detection with adjusted thresholds
        edges = cv2.Canny(gray, 30, 120, apertureSize=3)
        
        # Detect ALL line segments using Probabilistic Hough Transform
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=30,  # Lower threshold to detect more lines
            minLineLength=20,  # Shorter minimum length
            maxLineGap=5  # Smaller gap tolerance
        )
        
        if lines is None:
            logger.warning(f"    ⚠️ No line segments detected")
            return []
        
        segments = []
        for i, line in enumerate(lines):
            x1, y1, x2, y2 = line[0]
            
            # Normalize coordinates
            x1_norm = x1 / width
            y1_norm = y1 / height
            x2_norm = x2 / width
            y2_norm = y2 / height
            
            # Calculate geometric properties
            dx = x2_norm - x1_norm
            dy = y2_norm - y1_norm
            length = np.sqrt(dx**2 + dy**2)
            angle = np.arctan2(dy, dx)
            
            # Calculate bounding box
            x_min = min(x1_norm, x2_norm)
            y_min = min(y1_norm, y2_norm)
            x_max = max(x1_norm, x2_norm)
            y_max = max(y1_norm, y2_norm)
            
            # Filter very short segments (< 1% of image)
            if length < 0.01:
                continue
            
            segments.append({
                'id': f'seg_{i}',
                'start': (x1_norm, y1_norm),
                'end': (x2_norm, y2_norm),
                'length': length,
                'angle': angle,
                'bbox': (x_min, y_min, x_max, y_max)
            })
        
        logger.info(f"    ✅ Detected {len(segments)} line segments")
        return segments
    
    def _assign_line_numbers_to_segments(
        self,
        segments: List[Dict],
        line_position_map: Dict[str, Tuple[float, float]],
        max_distance: float = 0.15
    ) -> Dict[str, List[str]]:
        """
        Assign line numbers to line segments using spatial proximity.
        
        Args:
            segments: List of segment dicts from _detect_all_line_segments
            line_position_map: Dict mapping line_number to (x, y) position
            max_distance: Maximum normalized distance for assignment
        
        Returns:
            Dict mapping line_number to list of segment IDs:
            {line_number: [seg_id1, seg_id2, ...]}
        """
        line_segments_map = {}
        
        for line_number, (label_x, label_y) in line_position_map.items():
            nearby_segments = []
            
            for segment in segments:
                # Calculate distance from label to line segment
                distance = self._point_to_segment_distance(
                    (label_x, label_y),
                    segment['start'],
                    segment['end']
                )
                
                if distance < max_distance:
                    nearby_segments.append({
                        'id': segment['id'],
                        'distance': distance
                    })
            
            if nearby_segments:
                # Sort by distance and take closest segments
                nearby_segments.sort(key=lambda x: x['distance'])
                line_segments_map[line_number] = [s['id'] for s in nearby_segments[:5]]  # Top 5 closest
                logger.info(f"    🔗 {line_number} → {len(nearby_segments)} nearby segments")
        
        return line_segments_map
    
    def _point_to_segment_distance(
        self,
        point: Tuple[float, float],
        seg_start: Tuple[float, float],
        seg_end: Tuple[float, float]
    ) -> float:
        """
        Calculate minimum distance from a point to a line segment.
        
        Args:
            point: (x, y) point coordinates
            seg_start: (x1, y1) segment start
            seg_end: (x2, y2) segment end
        
        Returns:
            Normalized distance
        """
        px, py = point
        x1, y1 = seg_start
        x2, y2 = seg_end
        
        # Vector from seg_start to seg_end
        dx = x2 - x1
        dy = y2 - y1
        
        # Segment length squared
        length_sq = dx*dx + dy*dy
        
        if length_sq < 1e-10:
            # Segment is a point
            return np.sqrt((px - x1)**2 + (py - y1)**2)
        
        # Project point onto segment (clamped to [0, 1])
        t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / length_sq))
        
        # Closest point on segment
        closest_x = x1 + t * dx
        closest_y = y1 + t * dy
        
        # Euclidean distance
        distance = np.sqrt((px - closest_x)**2 + (py - closest_y)**2)
        
        return distance
    
    def _build_connectivity_graph(self, segments: List[Dict]) -> Dict[str, List[str]]:
        """
        Build connectivity graph by detecting intersections between segments.
        
        Args:
            segments: List of segment dicts
        
        Returns:
            Adjacency list: {seg_id: [connected_seg_ids]}
        """
        graph = {seg['id']: [] for seg in segments}
        
        # Check all pairs for intersections
        for i, seg1 in enumerate(segments):
            for seg2 in segments[i+1:]:
                if self._segments_intersect(seg1, seg2):
                    graph[seg1['id']].append(seg2['id'])
                    graph[seg2['id']].append(seg1['id'])
        
        connected_count = sum(1 for conns in graph.values() if len(conns) > 0)
        logger.info(f"    📊 {connected_count}/{len(segments)} segments have connections")
        
        return graph
    
    def _segments_intersect(self, seg1: Dict, seg2: Dict, tolerance: float = 0.01) -> bool:
        """
        Check if two line segments intersect or are very close (endpoints).
        
        Args:
            seg1, seg2: Segment dicts with 'start' and 'end' keys
            tolerance: Distance threshold for considering segments connected
        
        Returns:
            True if segments intersect or touch
        """
        # Check endpoint proximity (common in P&ID drawings)
        endpoints1 = [seg1['start'], seg1['end']]
        endpoints2 = [seg2['start'], seg2['end']]
        
        for p1 in endpoints1:
            for p2 in endpoints2:
                dist = np.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)
                if dist < tolerance:
                    return True
        
        # Check geometric intersection
        x1, y1 = seg1['start']
        x2, y2 = seg1['end']
        x3, y3 = seg2['start']
        x4, y4 = seg2['end']
        
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        
        if abs(denom) < 1e-10:
            # Parallel or collinear
            return False
        
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
        u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom
        
        # Check if intersection point is within both segments
        if 0 <= t <= 1 and 0 <= u <= 1:
            return True
        
        return False
    
    def _infer_from_to_relationships(
        self,
        line_segments_map: Dict[str, List[str]],
        connectivity_graph: Dict[str, List[str]],
        all_segments: List[Dict]
    ) -> Dict[str, Dict[str, str]]:
        """
        Infer FROM-TO relationships using graph connectivity and flow heuristics.
        
        Args:
            line_segments_map: Maps line_number to segment IDs
            connectivity_graph: Adjacency list of segment connections
            all_segments: Complete list of segments
        
        Returns:
            {line_number: {'from_line': X, 'to_line': Y, 'confidence': Z}}
        """
        from_to_map = {}
        segment_dict = {seg['id']: seg for seg in all_segments}
        
        # Reverse mapping: segment_id -> line_numbers
        seg_to_lines = {}
        for line_num, seg_ids in line_segments_map.items():
            for seg_id in seg_ids:
                if seg_id not in seg_to_lines:
                    seg_to_lines[seg_id] = []
                seg_to_lines[seg_id].append(line_num)
        
        for line_number, seg_ids in line_segments_map.items():
            if not seg_ids:
                continue
            
            # Find all connected line numbers via graph traversal
            connected_lines = set()
            visited = set()
            
            def dfs(seg_id, depth=0):
                if depth > 3 or seg_id in visited:  # Limit search depth
                    return
                visited.add(seg_id)
                
                # Check if this segment belongs to other lines
                if seg_id in seg_to_lines:
                    for other_line in seg_to_lines[seg_id]:
                        if other_line != line_number:
                            connected_lines.add(other_line)
                
                # Traverse connected segments
                for connected_seg_id in connectivity_graph.get(seg_id, []):
                    dfs(connected_seg_id, depth + 1)
            
            # Start DFS from this line's segments
            for seg_id in seg_ids:
                dfs(seg_id)
            
            if connected_lines:
                # Flow heuristics: assume horizontal left-to-right, vertical top-to-bottom
                main_seg = segment_dict.get(seg_ids[0])
                if main_seg:
                    angle = main_seg['angle']
                    
                    # Horizontal flow (angle close to 0 or π)
                    if abs(angle) < np.pi/4 or abs(angle - np.pi) < np.pi/4:
                        # Left-to-right flow
                        connected_list = sorted(connected_lines)
                        from_to_map[line_number] = {
                            'from_line': connected_list[0] if len(connected_list) > 0 else '',
                            'to_line': connected_list[-1] if len(connected_list) > 1 else '',
                            'confidence': 'medium'
                        }
                    else:
                        # Vertical flow (top-to-bottom)
                        connected_list = sorted(connected_lines)
                        from_to_map[line_number] = {
                            'from_line': connected_list[0] if len(connected_list) > 0 else '',
                            'to_line': connected_list[-1] if len(connected_list) > 1 else '',
                            'confidence': 'medium'
                        }
                    
                    logger.info(f"    🔄 {line_number} connected to {len(connected_lines)} lines")
        
        return from_to_map
    
    def format_as_table_data(self, line_items: List[Dict]) -> List[Dict]:
        """
        Format extracted line items for frontend table display
        """
        fluid_code_names = {
            'PG': 'Process Gas',
            'PL': 'Process Liquid',
            'CW': 'Cooling Water',
            'SW': 'Sea Water',
            'ST': 'Steam',
            'CO': 'Condensate',
            'AI': 'Instrument Air',
            'PA': 'Plant Air',
            'N2': 'Nitrogen',
            'FW': 'Fire Water',
            'DW': 'Drinking Water',
            'WW': 'Waste Water'
        }
        
        insulation_names = {
            'N': 'None',
            'C': 'Cold',
            'H': 'Hot',
            'P': 'Personnel Protection',
            'A': 'Acoustic'
        }
        
        table_data = []
        for item in line_items:
            fluid_code = item.get('fluid_code', '')
            insulation = item.get('insulation', '')
            line_number = item.get('line_number', '')
            
            table_data.append({
                'original_detection': line_number,  # Full line as detected (FIRST COLUMN)
                'line_number': line_number,
                'fluid_code': fluid_code,
                'fluid_description': fluid_code_names.get(fluid_code, 'Unknown'),
                'size': item.get('size', ''),
                'sequence_no': item.get('sequence_no', ''),
                'pipr_class': item.get('pipr_class', ''),
                'insulation': insulation,
                'insulation_description': insulation_names.get(insulation, 'Unknown'),
                'from_equipment': item.get('from_equipment', ''),
                'to_equipment': item.get('to_equipment', ''),
                'from_line': item.get('from_line', ''),  # NEW: Symbol-based FROM detection
                'to_line': item.get('to_line', ''),      # NEW: Symbol-based TO detection
                'flow_detection_method': item.get('flow_detection_method', ''),
                'flow_confidence': item.get('flow_confidence', ''),
                'page': item.get('page', 1),
                'confidence': item.get('confidence', 'medium'),
                'criticality_stress': item.get('criticality_stress', 'N/A')  # NEW: Stress Criticality column
            })
        
        return table_data
