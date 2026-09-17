"""Read a verified imported buyer sign-off without manufacturing approval artwork.

OCR locates the original block; it never establishes who approved the order.
The saved review, confirmed document association and byte digest do that. The
returned image retains the complete block, including its original printed text.
"""

from datetime import date, datetime
import hashlib
from io import BytesIO
import logging
import re
from uuid import UUID

from django.core.cache import cache
from django.core.files.storage import default_storage
import numpy as np
from PIL import Image
import pymupdf

from .po_pdf_approval import _anchor, _buyer_region, _lines, _native_words, _ocr_words
from .purchase_order_sources import is_safe_source_storage_key


logger = logging.getLogger(__name__)
MAX_SOURCE_BYTES = 15 * 1024 * 1024
MAX_RASTER_SIDE = 2400
MAX_COVER_PAGES = 2
CACHE_VERSION = 'buyer-block-v2'
CACHE_SECONDS = 60 * 60 * 24


def _name(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip().casefold()


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value or '')).isoformat()
    except ValueError:
        return ''


def _verified(value):
    return (isinstance(value, dict)
            and value.get('signature_verified') is True
            and value.get('stamp_verified') is True
            and value.get('approval_evidence_complete') is not False)


def _reference(value):
    reference, separator, fragment = str(value or '').strip().partition('#')
    if not separator:
        return reference, 1
    match = re.fullmatch(r'page=([1-9]\d*)(?:&[^#]*)?', fragment)
    return reference, int(match.group(1)) if match else None


def _source(order):
    """Resolve the recorded evidence, never an arbitrary/latest PO attachment."""
    name, approved_date = _name(getattr(order, 'approved_by_name', '')), _date(getattr(order, 'approved_date', None))
    if not name or not approved_date or not getattr(order, 'pk', None):
        return None
    rows = getattr(order, 'approval_log', None)
    if not isinstance(rows, list):
        return None
    candidates = set()
    for row in rows:
        if (not _verified(row) or _name(row.get('status')) != 'approved'
                or _name(row.get('approver')) != name):
            continue
        try:
            candidates.add(UUID(str(row.get('evidence_document_id'))))
        except (ValueError, TypeError, AttributeError):
            continue
    if not candidates:
        return None
    documents = order.source_documents.filter(
        pk__in=candidates, confirmed_po_id=order.pk,
        document_type__in=('purchase_order', 'unknown'),
    ).only('id', 's3_key', 's3_url', 'extracted_data', 'confirmed_po_id')
    matching = []
    for document in documents:
        fields = document.extracted_data
        if (not _verified(fields) or _name(fields.get('approved_by_name')) != name
                or _date(fields.get('approved_date')) != approved_date):
            continue
        digest = fields.get('source_sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', digest):
            continue
        # These are opaque saved references, not URLs to fetch. They also
        # disambiguate historical reviews carrying the same signer and date.
        expected = str(document.s3_url or '').strip()
        signature, signature_page = _reference(getattr(order, 'approval_signature', ''))
        stamp, stamp_page = _reference(getattr(order, 'approval_stamp', ''))
        if (not expected or signature != expected or stamp != expected
                or signature_page != stamp_page or signature_page not in range(1, MAX_COVER_PAGES + 1)):
            continue
        if is_safe_source_storage_key(document.s3_key):
            matching.append((document, digest.lower(), signature_page))
    return matching[0] if len(matching) == 1 else None


def _crop_page(page, words):
    region = _buyer_region(page, words)
    if not region:
        return None
    buyer_words = [word for word in words if (
        region[0] <= (word['bbox'][0] + word['bbox'][2]) / 2 <= region[2]
        and region[1] <= (word['bbox'][1] + word['bbox'][3]) / 2 <= region[3]
    )]
    date_label = _anchor(buyer_words, r'(?:Approval\s+)?Date\s*:')
    if not date_label:
        return None
    # Allow a seal below the date, while keeping the usual footer outside the
    # block. Recognized footer/contact text further constrains this limit.
    bottom = min(page.rect.height * .90, date_label[3] + 85)
    for line in _lines(words):
        if (line['bbox'][1] <= max(date_label[3] + 12, page.rect.height * .8)
                or line['bbox'][0] >= region[2]):
            continue
        if re.search(r'\b(?:Tel\s*[:;]|Fax\s*[:;]|P\.?\s*O\.?\s*Box|www\.|Millennium\s+Tower)'
                     r'|(?:REJLERS\s+){2}', line['text'], re.I):
            bottom = min(bottom, line['bbox'][1] - 8)
    if bottom <= date_label[3] + 3 or bottom <= region[1] + 30:
        return None
    # Handwriting can start just left of the printed approval label.
    rect = pymupdf.Rect(max(0, region[0] - 10), region[1], region[2], bottom)
    # Restrict the supported shape: an unknown or rotated layout is left to
    # the original-source viewer instead of guessing an approval rectangle.
    if rect.width < 60 or rect.width > page.rect.width * .58 or rect.height > page.rect.height * .5:
        return None
    scale = min(3, 1400 / max(rect.width, rect.height))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, colorspace=pymupdf.csRGB, alpha=False)
    image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    pixels = np.asarray(image)
    ink = pixels.min(axis=2) < 220
    yy, xx = np.where(ink)
    if not xx.size:
        return None
    # Only remove exterior blank margins. No ink masks, recoloring, detached
    # signatures or substituted company stamps are used in the returned image.
    pad = max(3, round(3 * scale))
    bounds = (max(0, int(xx.min()) - pad), max(0, int(yy.min()) - pad),
              min(image.width, int(xx.max()) + pad + 1), min(image.height, int(yy.max()) + pad + 1))
    stream = BytesIO()
    image.crop(bounds).save(stream, format='PNG')
    return stream.getvalue()


def _render_source(content, page_number=1):
    with pymupdf.open(stream=content, filetype='pdf') as pdf:
        if pdf.needs_pass or page_number not in range(1, MAX_COVER_PAGES + 1) or page_number > len(pdf):
            return None
        # The saved reference identifies the reviewed cover. A later attached
        # quotation/PO must never substitute for a missing block on that page.
        page = pdf[page_number - 1]
        if page.rotation or min(page.rect.width, page.rect.height) < 200 or max(page.rect.width, page.rect.height) > 2000:
            return None
        words = _native_words(page)
        artwork = _crop_page(page, words)
        if artwork is not None:
            return artwork
        # One bounded OCR attempt, only on the referenced cover page. Existing
        # helper has a 15-second timeout and makes no remote call.
        if len(words) < 20 or page.get_images():
            dpi = max(36, min(200, int(MAX_RASTER_SIDE * 72 / max(page.rect.width, page.rect.height))))
            artwork = _crop_page(page, _ocr_words(page, dpi=dpi))
            if artwork is not None:
                return artwork
    return None


def source_approval_artwork(order):
    """Return an original, verified buyer block as PNG, or no artwork.

    Missing/unreadable sources and uncertain geometry never prevent exporting
    the PO, and never cause a generic stamp/signature to replace source evidence.
    No order, approval, source document or retained PDF is modified.
    """
    source = _source(order)
    if source is None:
        return None
    document, digest, page_number = source
    try:
        with default_storage.open(document.s3_key, 'rb') as stored:
            content = stored.read(MAX_SOURCE_BYTES + 1)
        if (len(content) > MAX_SOURCE_BYTES or not content.startswith(b'%PDF')
                or hashlib.sha256(content).hexdigest() != digest):
            return None
        # Authorization and byte verification precede every cache read. The
        # immutable digest and algorithm version prevent cross-source reuse.
        key = f'procurement:source-approval-artwork:{CACHE_VERSION}:{digest}:{page_number}'
        try:
            cached = cache.get(key)
        except Exception:
            cached = None
        if isinstance(cached, bytes):
            return BytesIO(cached) if cached else None
        artwork = _render_source(content, page_number)
        try:
            cache.set(key, artwork or b'', CACHE_SECONDS if artwork else 60)
        except Exception:
            pass
        return BytesIO(artwork) if artwork else None
    except Exception:
        logger.warning('Verified PO source approval artwork could not be read (document %s).', document.pk)
        return None
