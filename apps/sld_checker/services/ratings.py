"""
Additive ratings extractor (source='checker')
==============================================

For a located tag, reads the data block around it: PDF text-layer words
below/right of the tag (pymupdf page.get_text('words')), falling back to
pytesseract OCR on the cached 150-dpi PNG region. Returns parsed ratings
with a confidence; values below SldCheckConfig.conf_min are stored but
flagged needs_review and skipped by rules.

Never invents values: every parsed number comes from a regex hit on text
actually read.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── value patterns (order = priority within a field) ─────────────────────
KA_RE = re.compile(r'(\d+(?:\.\d+)?)\s*kA', re.I)
KVA_RE = re.compile(r'(\d+(?:\.\d+)?)\s*kVA', re.I)
KW_RE = re.compile(r'(\d+(?:\.\d+)?)\s*kW\b', re.I)
AMP_RE = re.compile(r'(\d+(?:\.\d+)?)\s*A\b')
VOLT_RE = re.compile(r'(\d{3,4}(?:\.\d+)?)\s*V\b', re.I)
RATIO_RE = re.compile(r'(\d{3,6})\s*/\s*(\d{3,4})\s*V', re.I)
Z_RE = re.compile(r'Z\s*=?(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*(?:Z|imp)', re.I)
XD_RE = re.compile(r'X"?d\s*=?(\d+(?:\.\d+)?)\s*%', re.I)
VG_RE = re.compile(r'\b(Dyn\d+|Yyn\d+|Yd\d+|Dd\d+|YNd\d+)\b', re.I)
ANSI_RE = re.compile(r'\b(50|51|50N|51N|50G|51G|27|59|59N|64|46|49|81|87|87T|32|40)\b')
DEVICE_RE = re.compile(r'\b(ACB|MCCB|MCB|SFU|FUSE|CONTACTOR|ATS)\b', re.I)
STARTER_RE = re.compile(r'\b(DOL|SD|S\/D|STAR[- ]?DELTA|SOFT[- ]?START(?:ER)?|SS|VFD|VSD)\b', re.I)
FDR_RE = re.compile(r'\b(?:FDR|FEEDER|FEED)\s*[.:]?\s*(\d{1,3})\b', re.I)
INTERLOCK_RE = re.compile(r'interlock|mech(?:anical)?\s*lock|kirk\s*key|castell', re.I)

CONF_TEXT = 0.9
CONF_OCR = 0.65


def _words_near(page, x, y, xw=0.35, yw=0.22):
    """Text-layer words in a box below/right of (x, y) PDF points."""
    rect = page.rect
    clip = page.rect.__class__(x, y, min(rect.width, x + rect.width * xw),
                               min(rect.height, y + rect.height * yw))
    try:
        words = page.get_text('words', clip=clip) or []
    except Exception:
        words = []
    words.sort(key=lambda w: (round(w[1], 1), w[0]))
    return ' '.join(w[4] for w in words)


def _ocr_near(png_path, bbox_pct):
    """OCR fallback on the cached PNG region below/right of the tag."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(png_path)
        w, h = img.size
        x0 = int(bbox_pct[0] / 100 * w)
        y0 = int(bbox_pct[1] / 100 * h)
        crop = img.crop((x0, y0, min(w, x0 + int(0.35 * w)), min(h, y0 + int(0.25 * h))))
        return pytesseract.image_to_string(crop, config='--psm 6') or ''
    except Exception:
        logger.debug('[SLDC] OCR fallback failed', exc_info=True)
        return ''


def parse_block(text, kind):
    """Parse one data-block text into rating fields for the node kind."""
    out = {}
    if not text:
        return out
    t = ' '.join(text.split())
    if kind in ('board', 'subpanel'):
        m = KA_RE.search(t)
        if m:
            out['isc_ka'] = float(m.group(1))
        amps = [float(a) for a in AMP_RE.findall(t) if float(a) >= 100]
        if amps:
            out['rated_a'] = max(amps)
        m = VOLT_RE.search(t)
        if m:
            out['voltage_v'] = float(m.group(1))
    elif kind == 'transformer':
        m = KVA_RE.search(t)
        if m:
            out['kva'] = float(m.group(1))
        m = RATIO_RE.search(t)
        if m:
            out['voltage_v'] = float(m.group(2))
            out['ratio'] = f'{m.group(1)}/{m.group(2)}'
        m = VG_RE.search(t)
        if m:
            out['vector_group'] = m.group(1)
        m = Z_RE.search(t)
        if m:
            out['z_pct'] = float(m.group(1) or m.group(2))
    elif kind == 'generator':
        m = KVA_RE.search(t)
        if m:
            out['kva'] = float(m.group(1))
        m = KW_RE.search(t)
        if m:
            out['kw'] = float(m.group(1))
        m = VOLT_RE.search(t)
        if m:
            out['voltage_v'] = float(m.group(1))
        m = XD_RE.search(t)
        if m:
            out['xd_pct'] = float(m.group(1))
    elif kind in ('motor', 'load'):
        m = KW_RE.search(t)
        if m:
            out['kw'] = float(m.group(1))
    return out


def parse_feeder(text):
    """Feeder-edge data: number, device+rating, starter, ANSI relays."""
    out = {}
    if not text:
        return out
    t = ' '.join(text.split())
    m = FDR_RE.search(t)
    if m:
        out['feeder_no'] = m.group(1)
    m = DEVICE_RE.search(t)
    if m:
        out['device'] = m.group(1).upper()
        after = t[m.end():m.end() + 20]
        a = AMP_RE.search(after)
        if a:
            out['device_a'] = float(a.group(1))
        k = KA_RE.search(after)
        if k:
            out['device_ka'] = float(k.group(1))
    m = STARTER_RE.search(t)
    if m:
        s = m.group(1).upper().replace(' ', '-')
        out['starter'] = {'S/D': 'SD', 'STAR-DELTA': 'SD',
                          'SOFT-START': 'SS', 'SOFT-STARTER': 'SS',
                          'VSD': 'VFD'}.get(s, s if s in ('DOL', 'SD', 'SS', 'VFD') else None)
    relays = sorted(set(ANSI_RE.findall(t)))
    if relays:
        out['relays'] = relays
    return out


def extract_node_ratings(page, tag_hit, kind, png_path=None):
    """(fields, confidence, block_text) for one located tag."""
    text = _words_near(page, tag_hit['x'], tag_hit['y'])
    conf = CONF_TEXT
    if len(text.strip()) < 6 and png_path and tag_hit.get('bbox_pct'):
        text = _ocr_near(png_path, tag_hit['bbox_pct'])
        conf = CONF_OCR
    fields = parse_block(text, kind)
    if not fields:
        conf = min(conf, CONF_OCR)
    return fields, conf, text
