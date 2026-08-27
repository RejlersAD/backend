"""
Legend Sheets / Symbol Images bridge — connects P&ID Verification V2's
automatic analysis pipeline to P&ID Checker V2's (apps.pid_checker_v2)
legend lookup tables and manually-uploaded reference symbol pictures.

These are two independent Django apps with separate databases (see the
architecture notes elsewhere in this codebase) — this module is the
explicit, additive connector: it reads from pid_checker_v2 and writes
findings back into pid_verification_v2's own PIDVComparisonFinding table.
It does not modify pid_checker_v2 in any way, so V1 and pid_checker_v2's
own UI are unaffected.

Two independent capabilities, combined by cross_reference():
  1. Text matching — extracted P&ID tags (e.g. "FL-CS-1001") are matched
     against pid_checker_v2's per-section lookup tables (e.g. Line List's
     "FL" -> "FLARE GAS"). Always available — no API key needed.
  2. Symbol vision — pid_checker_v2's identify_symbols_via_vision() is
     reused to compare the P&ID against LegendSymbolImage reference
     pictures. Requires a Claude BYOK key (same as V2's existing
     deep_claude/hybrid AI analysis modes) — skipped otherwise.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r'[A-Za-z0-9]+')

# Cap how many text matches we bother cross-referencing/persisting per
# document — a dense P&ID can extract hundreds of tags, and every lookup
# table entry is a candidate; this keeps worst-case findings volume sane
# without needing real pagination in PIDVComparisonFinding.
MAX_TEXT_MATCHES_PER_DOCUMENT = 200

# How many reference symbol pictures go in a single Vision call. Increased
# from an earlier 20 to 40 — deliberately soft-coded and NEVER read as a
# fixed count anywhere: get_symbol_images_for_project() always queries
# LegendSymbolImage fresh, so whether the library has 184 pictures today or
# 500+ next year, run_page_vision_analysis() below derives the number of
# batches from len(symbol_images) at call time (ceil division) — adding a
# new symbol is just an upload, no code or config change needed here.
SYMBOL_BATCH_SIZE = 40

# Vision calls for overflow batches (2nd+) are I/O-bound and independent —
# run them concurrently so a large symbol library doesn't multiply wall-
# clock latency by its batch count. Mirrors pid_checker_v2's
# symbol_shape_extractor.VISION_CONCURRENT_CALLS.
SYMBOL_BATCH_CONCURRENT_CALLS = 4


def get_legend_lookup_fields(user) -> list[dict]:
    """Collect legend lookup tables to text-match extracted P&ID tags
    against: pid_checker_v2's built-in default templates (always
    available), overlaid per-section with the user's own ACTIVE
    PidCheckerV2LegendSheet definition where one exists.

    Returns a list of field dicts: {key, label, lookup, section}.
    """
    from apps.pid_checker_v2.legend_defaults import DEFAULT_TEMPLATES

    fields: dict[tuple, dict] = {}
    for section, template in DEFAULT_TEMPLATES.items():
        for f in template.get('definition', {}).get('fields', []):
            if f.get('lookup'):
                fields[(section, f['key'])] = {**f, 'section': section}

    if user is not None and getattr(user, 'is_authenticated', False):
        try:
            from apps.pid_checker_v2.models import PidCheckerV2LegendSheet
            active_sheets = PidCheckerV2LegendSheet.objects.filter(created_by=user, is_active=True)
            for sheet in active_sheets:
                for f in (sheet.definition or {}).get('fields', []):
                    if f.get('lookup'):
                        fields[(sheet.section, f['key'])] = {**f, 'section': sheet.section}
        except Exception:
            logger.debug('[LegendBridge] Could not load user legend sheets', exc_info=True)

    return list(fields.values())


def match_text_against_legend(extraction: dict, fields: list[dict]) -> list[dict]:
    """Match extracted P&ID text (tags/instruments/valves/equipment/
    line_tags) against legend lookup tables, e.g. tag 'FL-CS-1001' contains
    code 'FL', which the Line List section's 'service' field maps to
    'FLARE GAS'.

    A code matches when it appears as its own token in the tag (split on
    non-alphanumeric separators) or the tag starts with it — avoids
    matching a code that's merely a substring of an unrelated word.

    Returns a list of {tag, code, description, section, field_key, field_label}.
    """
    candidates: set[str] = set()
    for key in ('tags', 'instruments', 'valves', 'equipment'):
        for item in extraction.get(key, []) or []:
            tag = item.get('tag') if isinstance(item, dict) else item
            if tag:
                candidates.add(str(tag).upper())
    for lt in extraction.get('line_tags', []) or []:
        tag = lt.get('tag') if isinstance(lt, dict) else lt
        if tag:
            candidates.add(str(tag).upper())

    matches: list[dict] = []
    seen: set[tuple] = set()
    for tag in candidates:
        tokens = set(_TOKEN_RE.findall(tag))
        for field in fields:
            lookup = field.get('lookup') or {}
            for code, description in lookup.items():
                code_u = str(code).upper()
                if code_u in tokens or tag.startswith(code_u):
                    key = (tag, field['section'], field['key'], code_u)
                    if key in seen:
                        continue
                    seen.add(key)
                    matches.append({
                        'tag': tag,
                        'code': code_u,
                        'description': description,
                        'section': field['section'],
                        'field_key': field['key'],
                        'field_label': field.get('label', field['key']),
                    })
                    if len(matches) >= MAX_TEXT_MATCHES_PER_DOCUMENT:
                        return matches
    return matches


def get_symbol_images_for_project(v2_project) -> list[dict]:
    """Resolve LegendSymbolImage reference pictures for a V2 project, as
    {'symbol_type': str, 'b64': str} dicts ready for identify_symbols_via_vision().

    LegendSymbolImage.project is a FK into V1's PIDVProject, not V2's —
    the two apps have no shared project model. Bridge them by NAME: if a
    V1 project exists with the same project_name as this V2 project, its
    uploads are used (plus pid_checker_v2's own cross-project fallback for
    symbols it hasn't uploaded itself); otherwise we go straight to that
    same fallback (any project's images), so a V2-only project still gets
    coverage instead of none.
    """
    try:
        import base64
        from apps.pid_checker_v2.views import _get_symbol_images_with_fallback

        v1_project_id = None
        if v2_project is not None and v2_project.project_name:
            try:
                from apps.pid_verification.models import PIDVProject as V1Project
                v1_match = V1Project.objects.filter(project_name__iexact=v2_project.project_name).first()
                if v1_match:
                    v1_project_id = str(v1_match.project_id)
            except Exception:
                logger.debug('[LegendBridge] V1 project lookup failed', exc_info=True)

        rows = _get_symbol_images_with_fallback(v1_project_id)
        images = []
        for row in rows:
            if not row.image_file:
                continue
            row.image_file.open('rb')
            try:
                data = row.image_file.read()
            finally:
                row.image_file.close()
            images.append({
                'symbol_type': row.symbol_name,
                'b64': base64.b64encode(data).decode('ascii'),
            })
        return images
    except Exception:
        logger.warning('[LegendBridge] Could not load symbol images', exc_info=True)
        return []


def run_page_vision_analysis(drawing_data: dict, api_key: str, page_image_b64: str,
                              symbol_images: list[dict] | None = None,
                              model: str | None = None) -> dict | None:
    """Run ONE page's real-Vision analysis (findings + symbol recognition)
    against the full symbol-image library, batching automatically so this
    scales to any library size without a code change.

    Batch strategy (auto-adjusts to len(symbol_images) — never hardcoded):
      - Batch 1 (<= SYMBOL_BATCH_SIZE images): one call asking for BOTH
        findings AND symbols (apps.pid_verification_v2.services.ai_analysis
        .run_claude_analysis, include_findings=True) — findings only need
        to be requested once per page.
      - Batches 2..N (only exist once the library exceeds SYMBOL_BATCH_SIZE,
        e.g. batch 2 appears once >40 symbols exist, batch 3 once >80,
        etc.): symbol-only calls (include_findings=False), run concurrently
        since they're independent I/O-bound requests.
      - All batches' symbols are merged and deduped (pid_checker_v2's
        _dedupe_symbols — same dedup used by its own Identify Symbols
        feature) before returning.

    Non-fatal by design: returns None (never raises) on failure or missing
    inputs — this is a best-effort pipeline enhancement, not a required
    step (see LegendSymbolBridgeStage / AIAnalysisStage, both critical=False).
    """
    if not api_key or not page_image_b64:
        return None
    try:
        from apps.pid_verification_v2.services.ai_analysis import run_claude_analysis
        from apps.pid_checker_v2.services.symbol_shape_extractor import _dedupe_symbols

        symbol_images = symbol_images or []
        batches = [
            symbol_images[i:i + SYMBOL_BATCH_SIZE]
            for i in range(0, len(symbol_images), SYMBOL_BATCH_SIZE)
        ] or [[]]  # always at least one (findings-only) call, even with 0 symbols

        first_result = run_claude_analysis(
            drawing_data, api_key, page_image_b64,
            symbol_images=batches[0], model=model, include_findings=True,
        )
        all_symbols = list(first_result['symbols'])

        overflow_batches = batches[1:]
        if overflow_batches:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(SYMBOL_BATCH_CONCURRENT_CALLS, len(overflow_batches))) as pool:
                futures = [
                    pool.submit(
                        run_claude_analysis, drawing_data, api_key, page_image_b64,
                        symbol_images=batch, model=model, include_findings=False,
                    )
                    for batch in overflow_batches
                ]
                for future in futures:
                    try:
                        all_symbols.extend(future.result()['symbols'])
                    except Exception:
                        logger.warning('[LegendBridge] Symbol overflow-batch call failed (non-fatal)', exc_info=True)

        return {'findings': first_result['findings'], 'symbols': _dedupe_symbols(all_symbols)}
    except Exception:
        logger.warning('[LegendBridge] Page vision analysis failed (non-fatal)', exc_info=True)
        return None


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(str(text).upper()))


def cross_reference(text_matches: list[dict], symbol_result: dict | None) -> dict:
    """Link text-matched legend entries to visually-identified symbols by
    name similarity (token overlap between the legend description, e.g.
    "PRESSURE SAFETY VALVE", and the vision model's symbol_type string).

    Called once PER PAGE (both text_matches and symbol_result should be for
    the same page — see LegendSymbolBridgeStage) so results are naturally
    page-attributed. True pixel-level spatial proximity still isn't
    available (symbol vision only reports coarse location strings like
    "top-left"), so this remains a name-based link rather than a
    pixel-distance one.

    Returns {'linked': [...], 'text_only': [...], 'symbol_only': [...]}.
    Each 'linked' entry is HIGH confidence (text + visual symbol agree);
    'text_only'/'symbol_only' are single-source, lower-confidence hints.
    """
    symbols = (symbol_result or {}).get('symbols', []) if symbol_result else []

    linked = []
    text_only = []
    used_symbol_idx: set = set()

    for match in text_matches:
        desc_tokens = _tokens(match['description'])
        best_idx, best_overlap = None, 0
        for idx, sym in enumerate(symbols):
            if idx in used_symbol_idx:
                continue
            overlap = len(desc_tokens & _tokens(sym.get('symbol_type', '')))
            if overlap > best_overlap:
                best_idx, best_overlap = idx, overlap
        if best_idx is not None and best_overlap >= 1:
            used_symbol_idx.add(best_idx)
            linked.append({**match, 'symbol': symbols[best_idx]})
        else:
            text_only.append(match)

    symbol_only = [s for idx, s in enumerate(symbols) if idx not in used_symbol_idx]

    return {'linked': linked, 'text_only': text_only, 'symbol_only': symbol_only}
