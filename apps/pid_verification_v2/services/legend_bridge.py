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
from apps.core.ai_consumer_clients import provider_api_key

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


# ── Legend text-match persistence (soft-coded) ────────────────────────────
# 29 Oct 2026: the old free-text matching produced bogus "comparison
# findings" like "PP-150 → CRUDE OIL" (the tag merely STARTED WITH the
# lookup code 'P' = 'CRUDE OIL' — 55 bogus findings on a clean AFC sheet).
# Matching is now FIELD-POSITION based (see match_text_against_legend), and
# the low-confidence single-source 'text_only' findings are suppressed
# entirely — only 'linked' matches (text AND vision symbol agree) are
# persisted by the orchestrator's LegendSymbolBridgeStage.
PERSIST_TEXT_ONLY_MATCHES = False

# Column-name hints used to detect the code / description / position
# columns of an uploaded Excel legend register (data_type='legend_pid').
_REGISTER_CODE_HINTS    = ('code', 'symbol', 'abbr', 'abbreviation', 'letter', 'field id')
_REGISTER_DESC_HINTS    = ('description', 'meaning', 'full name', 'definition', 'desc', 'name')
_REGISTER_POS_HINTS     = ('position', 'pos', 'field position', 'seq', 'order')
# A legend-register code is a short uppercase-ish token (LD-08 style field
# ids allowed: letters/digits/dash, <= 8 chars).
_REGISTER_CODE_RE = re.compile(r'^[A-Za-z0-9]{1,8}(-[A-Za-z0-9]{1,4})?$')


def get_legend_lookup_fields(user, project=None) -> list[dict]:
    """Collect legend lookup tables to text-match extracted P&ID tags
    against: pid_checker_v2's built-in default templates (always
    available), overlaid per-section with the user's own ACTIVE
    PidCheckerV2LegendSheet definition where one exists, and — when
    `project` is given — extended with the project's uploaded Excel legend
    register (PIDVReferenceData, data_type='legend_pid', e.g.
    Borouge_PID_Legend_Register.xlsx).

    Each field dict carries its 'position' — the field's index within its
    section template (e.g. Line List: size=0, service=1, spec=2, ...) — so
    match_text_against_legend() can match codes by FIELD POSITION within a
    composite line number instead of free-text token matching. Register-
    sourced fields use the register's own position column when present,
    otherwise position=None (exact-token match only).

    Returns a list of field dicts: {key, label, lookup, section, position}.
    """
    from apps.pid_checker_v2.legend_defaults import DEFAULT_TEMPLATES

    fields: dict[tuple, dict] = {}
    for section, template in DEFAULT_TEMPLATES.items():
        for pos, f in enumerate(template.get('definition', {}).get('fields', [])):
            if f.get('lookup'):
                fields[(section, f['key'])] = {**f, 'section': section, 'position': pos}

    if user is not None and getattr(user, 'is_authenticated', False):
        try:
            from apps.pid_checker_v2.models import PidCheckerV2LegendSheet
            active_sheets = PidCheckerV2LegendSheet.objects.filter(created_by=user, is_active=True)
            for sheet in active_sheets:
                for pos, f in enumerate((sheet.definition or {}).get('fields', [])):
                    if f.get('lookup'):
                        fields[(sheet.section, f['key'])] = {**f, 'section': sheet.section, 'position': pos}
        except Exception:
            logger.debug('[LegendBridge] Could not load user legend sheets', exc_info=True)

    if project is not None:
        for reg_field in _legend_register_lookup_fields(project):
            # Register fields extend (never overwrite) template fields.
            fields.setdefault(
                (reg_field['section'], reg_field['key']), reg_field
            )

    return list(fields.values())


def _legend_register_lookup_fields(project) -> list[dict]:
    """Build lookup fields from the project's most recent COMPLETED Excel
    legend register (PIDVReferenceData, data_type='legend_pid').

    The parsed_data rows (pandas records from parse_reference_data_task)
    are scanned for a code column (short code-like values), a description
    column (free text), and an optional position column (field position
    within the line number, e.g. LD-08 / LD-04 per the legend register).
    Each code column becomes one lookup field keyed by that position.

    Returns [] when no register is uploaded or it has no recognizable
    code/description columns — legend checks then run on the built-in
    templates only.
    """
    try:
        from apps.pid_verification_v2.models import PIDVReferenceData
        ref = (
            PIDVReferenceData.objects
            .filter(
                project=project,
                data_type=PIDVReferenceData.DataType.LEGEND_PID,
                status=PIDVReferenceData.Status.COMPLETED,
            )
            .exclude(parsed_data__isnull=True)
            .order_by('-created_at')
            .first()
        )
    except Exception:
        logger.debug('[LegendBridge] Could not query legend register', exc_info=True)
        return []

    if not ref or not ref.parsed_data:
        return []

    # Multi-sheet registers (parsed after 29 Oct 2026): build one lookup
    # field per category GROUP of the 'Codes' sheet (e.g. 'Equipment
    # designation code', 'Line class', 'Insulation code').
    if isinstance(ref.parsed_data, dict) and 'sheets' in ref.parsed_data:
        from .legend_register import get_register_codes
        fields = []
        for category, codes in get_register_codes(project).items():
            if codes:
                fields.append({
                    'key': f'register_{re.sub(r"[^A-Za-z0-9]+", "_", category).strip("_").lower()}',
                    'label': f'Legend Register: {category}',
                    'lookup': codes,
                    'code_positions': {},
                    'section': 'legend_register',
                    'position': None,
                })
        logger.info(
            '[LegendBridge] Loaded %d legend-register category lookups from %s (%s)',
            len(fields), ref.file_name, ref.reference_id,
        )
        return fields

    rows = ref.parsed_data
    columns = list(rows[0].keys()) if rows else []
    lowered = {str(c).lower().strip(): c for c in columns}

    def _find_col(hints):
        for hint in hints:
            for low, orig in lowered.items():
                if hint in low:
                    return orig
        return None

    desc_col = _find_col(_REGISTER_DESC_HINTS)
    pos_col  = _find_col(_REGISTER_POS_HINTS)

    def _looks_like_code_col(col) -> bool:
        values = [str(r.get(col, '')).strip() for r in rows[:50]]
        values = [v for v in values if v]
        if not values:
            return False
        code_like = sum(1 for v in values if _REGISTER_CODE_RE.match(v))
        return code_like / len(values) >= 0.8

    code_cols = [
        c for c in columns
        if c != desc_col and _looks_like_code_col(c)
    ]
    if not code_cols or not desc_col:
        logger.info(
            '[LegendBridge] Legend register %s has no recognizable code/'
            'description columns (columns=%s) — skipped',
            ref.reference_id, columns,
        )
        return []

    fields: list[dict] = []
    for code_col in code_cols:
        lookup: dict[str, str] = {}
        positions: dict[str, int] = {}
        for row in rows:
            code = str(row.get(code_col, '')).strip().upper()
            desc = str(row.get(desc_col, '')).strip()
            if not code or not desc or not _REGISTER_CODE_RE.match(code):
                continue
            lookup.setdefault(code, desc)
            if pos_col is not None:
                try:
                    positions[code] = int(float(str(row.get(pos_col, '')).strip()))
                except (ValueError, TypeError):
                    pass
        if not lookup:
            continue
        fields.append({
            'key': f'register_{code_col}',
            'label': f'Legend Register: {code_col}',
            'lookup': lookup,
            # Per-code positions from the register's position column
            # (empty dict → exact-token match fallback).
            'code_positions': positions,
            'section': 'legend_register',
            'position': None,
        })

    logger.info(
        '[LegendBridge] Loaded %d legend-register lookup field(s) from %s '
        '(%s)', len(fields), ref.file_name, ref.reference_id,
    )
    return fields


def match_text_against_legend(extraction: dict, fields: list[dict]) -> list[dict]:
    """Match extracted P&ID text (tags/instruments/valves/equipment/
    line_tags) against legend lookup tables, e.g. tag '6"-FL-8183-AC3N'
    has code 'FL' at FIELD POSITION 1 (after the size), which the Line
    List section's 'service' field maps to 'FLARE GAS'.

    Matching is by FIELD POSITION within the composite tag, NOT free text:
      - Template fields (position=N): the code must equal the tag's N-th
        token (tokens split on non-alphanumeric separators). E.g. the
        'service' field (position 1) only matches the second token of a
        line designation.
      - Fields whose lookup carries per-code positions ('code_positions'
        from an Excel legend register, e.g. LD-08 / LD-04): the code must
        sit at its register-defined token position.
      - Fields with no position information at all: exact-token match only
        (code == one whole token).

    The previous "tag starts with the code" fallback was REMOVED — it
    produced bogus matches like 'PP-150 → CRUDE OIL' (code 'P' prefix-
    matching tag 'PP-150'), 55 false findings on one clean AFC sheet
    (29 Oct 2026).

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

    def _code_matches(code_u: str, tag_tokens: list[str], field: dict) -> bool:
        # Per-code register position (e.g. LD-08 / LD-04) wins when present.
        code_pos = (field.get('code_positions') or {}).get(code_u)
        if code_pos is not None:
            return 0 <= code_pos < len(tag_tokens) and tag_tokens[code_pos] == code_u
        # Template field position (size=0, service=1, ...).
        field_pos = field.get('position')
        if field_pos is not None:
            return field_pos < len(tag_tokens) and tag_tokens[field_pos] == code_u
        # No position information — exact whole-token match only.
        return code_u in tag_tokens

    matches: list[dict] = []
    seen: set[tuple] = set()
    for tag in candidates:
        tag_tokens = _TOKEN_RE.findall(tag)  # ordered
        for field in fields:
            lookup = field.get('lookup') or {}
            for code, description in lookup.items():
                code_u = str(code).upper()
                if not _code_matches(code_u, tag_tokens, field):
                    continue
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
    api_key = provider_api_key('anthropic', fallback=lambda: (api_key))
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

        # 2026-09-08: this call carries the page's FINDINGS (the "more
        # findings = more accurate" ask) — double_pass=True (the default)
        # applies here explicitly.
        first_result = run_claude_analysis(
            drawing_data, api_key, page_image_b64,
            symbol_images=batches[0], model=model, include_findings=True,
            double_pass=True,
        )
        all_symbols = list(first_result['symbols'])

        overflow_batches = batches[1:]
        if overflow_batches:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(SYMBOL_BATCH_CONCURRENT_CALLS, len(overflow_batches))) as pool:
                # double_pass=False here, deliberately: these are symbol-
                # only calls (include_findings=False, no findings ever
                # come out of them) — doubling every overflow batch would
                # multiply cost per extra 40-symbol chunk in the library
                # for a benefit outside what was actually asked for
                # (findings accuracy). The single findings call above
                # already gets the double-pass treatment.
                futures = [
                    pool.submit(
                        run_claude_analysis, drawing_data, api_key, page_image_b64,
                        symbol_images=batch, model=model, include_findings=False,
                        double_pass=False,
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
