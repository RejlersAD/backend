"""
AGPS Drafting Standard check for SLD drawings — verifies a drawing
against AGPS-SP-EDS-002 (Engineering Drawings and Documents Drafting,
Rev.0 Sept 2025).

Two tiers:
  1. Programmatic (free, no AI) — anything decidable from the PDF
     itself, e.g. sheet size vs the ISO A-series table (Table 11.4).
  2. AI Vision (needs the user's API key, same one used for tag
     extraction) — one call per drawing page checking title block,
     border elements, revision info, and SLD presentation conventions
     against a curated rule digest of the standard.

Results are stored on the job (ElectricalComparisonJob.drafting_results)
and rendered by the frontend as a pass/fail/review checklist. This is a
visual/compliance aid — it never blocks or fails the comparison job.
"""
import logging

from apps.pid_checker_v2.services.vision_extractor import (
    _with_retries,
    VISION_OVERVIEW_MAX_DIMENSION_PX,
)
from .electrical_vision import _render_single_page, _prepare_image_b64
from .tag_extractor import (
    _call_claude_electrical,
    _call_openai_electrical,
    _classify_page,
    PAGE_TYPE_DRAWING,
)
logger = logging.getLogger(__name__)


# Table 11.4 DRAWING SIZES (mm, width x height, portrait or landscape).
_ISO_A_SIZES = {
    'A0': (1189, 841),
    'A1': (841, 594),
    'A2': (594, 420),
    'A3': (420, 297),
    'A4': (210, 297),
}
_SIZE_TOLERANCE_MM = 5  # allow for PDF crop-box rounding


def check_sheet_size(page_width_pt, page_height_pt):
    """Programmatic — Table 11.4: sheet size shall conform to the ISO
    'A' series (A0–A4). Returns a check dict."""
    w_mm = page_width_pt / 72.0 * 25.4
    h_mm = page_height_pt / 72.0 * 25.4
    long_side, short_side = max(w_mm, h_mm), min(w_mm, h_mm)
    for name, (long_, short) in _ISO_A_SIZES.items():
        if abs(long_side - long_) <= _SIZE_TOLERANCE_MM and abs(short_side - short) <= _SIZE_TOLERANCE_MM:
            return {
                'rule_id': 'sheet_size',
                'rule': 'Sheet size conforms to ISO A-series (AGPS Table 11.4)',
                'status': 'pass',
                'evidence': f'{w_mm:.0f} x {h_mm:.0f} mm ≈ {name} ({long_} x {short} mm)',
            }
    return {
        'rule_id': 'sheet_size',
        'rule': 'Sheet size conforms to ISO A-series (AGPS Table 11.4)',
        'status': 'fail',
        'evidence': f'{w_mm:.0f} x {h_mm:.0f} mm matches none of A0/A1/A2/A3/A4',
    }


# Curated, vision-checkable digest of AGPS-SP-EDS-002 for SLD drawings.
# CAD-only rules (layer names, .dwt templates, dimension styles, batch
# standard checker) are deliberately excluded — they are not verifiable
# from a PDF. Each rule cites its section in the standard.
DRAFTING_VISION_RULES = [
    ('title_block_complete',
     'Section 11.2.2 — Title block present and filled: drawing title, drawing/document number, '
     'revision number, date, scale, sheet number.'),
    ('title_block_caps',
     'Section 11.2.2 — All text in the title block is in CAPITAL LETTERS.'),
    ('border_elements',
     'Section 11.2.1 — Border carries the required elements: general notes section, legend, '
     'and reference list (as applicable to an SLD).'),
    ('revision_info',
     'Section 11.2.1/11.2.2 — Revision number and revision description are shown '
     '(title block or revision table).'),
    ('tag_text_prominence',
     'Table 11.5 — Tag numbers are lettered more prominently than general notes '
     '(tag-number style larger than general-text style), and text is uniform in style.'),
    ('sld_presentation',
     'Appendix 1 Table A1.5 — SLD conventions: busbars drawn as prominent (heavier) lines, '
     'different voltage levels visually distinguishable, equipment symbols from the standard '
     'symbol set, each equipment labelled with its tag number near the symbol.'),
]

DRAFTING_CHECK_SYSTEM_PROMPT = """You are an ADNOC drafting-standards reviewer checking a Single Line Diagram (SLD) page against AGPS-SP-EDS-002 (Engineering Drawings and Documents Drafting).

For EACH rule you are given, judge the drawing page and respond with ONLY a JSON object, no markdown fences:
{"checks": [{"rule_id": "<id>", "status": "pass" | "fail" | "review", "evidence": "<one short sentence: what you see that justifies the verdict>"}]}

Rules:
- "pass" only when the requirement is clearly met.
- "fail" only when the requirement is clearly violated or the element is clearly absent.
- "review" when the scan quality or angle makes it impossible to judge confidently — do NOT guess.
- Judge only what is visible on THIS page.
- Report every rule_id you were given, exactly once."""


def _parse_drafting_response(raw, rule_ids):
    import json
    import re
    fallback = [{'rule_id': rid, 'status': 'review', 'evidence': 'Could not evaluate (unparseable AI response)'} for rid in rule_ids]
    if not raw:
        return fallback
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, flags=re.DOTALL)
        if not m:
            return fallback
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return fallback
    checks = parsed.get('checks') if isinstance(parsed, dict) else None
    if not isinstance(checks, list):
        return fallback
    results = []
    for c in checks:
        rid = str(c.get('rule_id') or '')
        if rid not in rule_ids:
            continue
        status_val = str(c.get('status') or '').lower()
        if status_val not in ('pass', 'fail', 'review'):
            status_val = 'review'
        results.append({'rule_id': rid, 'status': status_val, 'evidence': str(c.get('evidence') or '')[:300]})
    for rid in rule_ids:
        if not any(r['rule_id'] == rid for r in results):
            results.append({'rule_id': rid, 'status': 'review', 'evidence': 'Not evaluated by the AI response'})
    return results


def run_drafting_check(pdf_bytes, api_key, provider='claude', model=None):
    """Run the AGPS drafting check over every drawing page of the SLD.

    Returns {'pages': [{'page': 1-based, 'checks': [{'rule_id', 'rule',
    'status', 'evidence'}]}], 'summary': {'pass': n, 'fail': n,
    'review': n}}. Never raises — per-page Vision failures degrade to
    'review' entries, and a total failure returns an empty summary so a
    drafting-check hiccup can never fail the comparison job.
    """
    import pymupdf

    rule_text_by_id = dict(DRAFTING_VISION_RULES)
    pages_out = []
    totals = {'pass': 0, 'fail': 0, 'review': 0}

    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    try:
        page_dims = [(p.rect.width, p.rect.height) for p in doc]
    finally:
        doc.close()

    rule_ids = [rid for rid, _ in DRAFTING_VISION_RULES]
    rules_block = '\n'.join(f'- rule_id "{rid}": {text}' for rid, text in DRAFTING_VISION_RULES)
    user_prompt = (
        'Check this SLD drawing page against each of these AGPS-SP-EDS-002 rules:\n'
        f'{rules_block}\n\n'
        'Remember: respond with ONLY the JSON object described.'
    )

    for page_index, (page_w, page_h) in enumerate(page_dims):
        checks = [check_sheet_size(page_w, page_h)]

        try:
            page_type, _ = _classify_page(pdf_bytes, page_index)
        except Exception:
            page_type = PAGE_TYPE_DRAWING  # classify failure must not skip the check

        vision_checks = []
        if page_type == PAGE_TYPE_DRAWING and api_key and api_key.strip():
            try:
                image_b64 = _prepare_image_b64(
                    _render_single_page(pdf_bytes, page_index),
                    VISION_OVERVIEW_MAX_DIMENSION_PX,
                )
                if provider == 'openai':
                    fn = lambda k, b64, p: _call_openai_electrical(k, b64, p, system_prompt=DRAFTING_CHECK_SYSTEM_PROMPT)  # noqa: E731
                else:
                    fn = lambda k, b64, p: _call_claude_electrical(k, b64, p, model=model, system_prompt=DRAFTING_CHECK_SYSTEM_PROMPT)  # noqa: E731
                raw, _, _ = _with_retries(provider, fn, api_key, image_b64, user_prompt)
                vision_checks = _parse_drafting_response(raw, set(rule_ids))
            except Exception as exc:  # noqa: BLE001
                logger.warning('[ElecCompare][Drafting] page %d Vision check failed: %s', page_index + 1, exc)
                vision_checks = [
                    {'rule_id': rid, 'status': 'review', 'evidence': 'AI check failed for this page'}
                    for rid in rule_ids
                ]

        for vc in vision_checks:
            vc['rule'] = rule_text_by_id.get(vc['rule_id'], vc['rule_id'])
            checks.append(vc)

        for c in checks:
            totals[c['status']] = totals.get(c['status'], 0) + 1
        pages_out.append({'page': page_index + 1, 'checks': checks})

    return {'pages': pages_out, 'summary': totals}
