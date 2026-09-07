"""
Legend-based tag-format validation for I/O List rows/comments.

Self-contained: does not import from apps.pid_checker_v2 or
apps.pid_verification. The legend *definition* JSON shape
({separator, fields: [{key,label,regex,suffix?,optional?,lookup?}]}) matches
apps.pid_checker_v2.PidCheckerV2LegendSheet's on purpose — that's what let the
one-time data migration copy existing legends across unchanged — but the
compile/compare logic here is a fresh, lean implementation covering only
what I/O List actually needs (no AI-prompt-block generation, no symbol
images).

Usage (called from services/orchestrator.py, after comment/row extraction —
read-only w.r.t. io_rows/comments, never mutates them):

    from .legend_comparison import compile_legend, compare_io_with_legend
    findings = compare_io_with_legend(io_rows, comments, legend.definition)

Or, for the multi-section check (models.IO_LEGEND_FORMAT_SECTIONS /
IO_LEGEND_LOOKUP_SECTIONS — everything actually wired into automated
validation, not just instrument_index):

    from .legend_comparison import compare_io_with_legends
    findings = compare_io_with_legends(io_rows, comments, format_legends, lookup_legends)
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

DEFAULT_SEPARATOR = '-'

# Best-effort reconstruction for a hyphen-less tag, shaped for the current
# instrument_index convention: UNIT(2-4 digits) FUNCTION(1-6 letters)
# SEQUENCE(2-5 digits + optional trailing letter), e.g. '113XHSC9502B'.
# This is deliberately specific to that 3-segment shape rather than a
# generic any-legend reconstruction — for any other definition shape it
# just no-ops (falls through to _NO_SEPARATOR_SIMPLE_TAG_RE below, or
# returns the string unchanged).
# SEQUENCE floor was '\d{3,5}[A-Z]?' (minimum 3 digits) — the same
# overly-strict floor found and fixed in io_table_extractor.py's
# _OCR_TAG_RE/_LOOP_ROW_TAG_RE (confirmed via a real '10-PSV-15B' test
# case, sequence '15' = 2 digits). A hyphen-less tag with a genuine
# 2-digit sequence (e.g. 'V15' typed without a hyphen) fell all the way
# through this best-effort reconstruction unchanged, then failed the
# legend's own hyphen-requiring pattern — a false "not recognised"
# purely because the SEPARATOR was missing, nothing wrong with the tag
# itself. Widened to match the other two fixes: '\d{2,5}[A-Z]?'.
_NO_SEPARATOR_TAG_RE = re.compile(r'^(\d{2,4})([A-Z]{1,6})(\d{2,5}[A-Z]?)$')

# Same idea, for the shorter 2-segment FUNCTION+SEQUENCE shape a bare P&ID
# instrument balloon typically carries (no unit/area prefix) — e.g. a
# drawing reading 'XV1004' or 'PSV1521A' with the hyphen omitted. Checked
# only after the 3-segment pattern above fails to match (that one requires
# a LEADING digit run, so there's no ambiguity between the two — a string
# starting with letters can only ever match this one). Same 2-digit floor
# widening as _NO_SEPARATOR_TAG_RE above, same reason.
_NO_SEPARATOR_SIMPLE_TAG_RE = re.compile(r'^([A-Z]{1,6})(\d{2,5}[A-Z]?)$')


def _strip_leading_zeros(segment: str) -> str:
    """'0113' -> '113'; leaves non-numeric segments (e.g. 'XHSC') untouched."""
    if segment.isdigit():
        return segment.lstrip('0') or '0'
    return segment


def normalize_tag(tag: str) -> str:
    """Canonicalize a tag string so equivalent-but-differently-typed variants
    compare equal: case, stray whitespace around hyphens, missing hyphens
    (best-effort, see `_NO_SEPARATOR_TAG_RE`), and leading zeros on numeric
    segments. Does NOT strip a trailing suffix letter (e.g. '9502' vs
    '9502B') — both are independently valid per the sequence field's own
    regex, so format-matching doesn't need to treat them as equal; that
    fuzzier cross-referencing concern is `family_key`'s job.

        normalize_tag('xhsc')                  -> 'XHSC'
        normalize_tag('113 - PZT -3191')        -> '113-PZT-3191'
        normalize_tag('0113-XHSC-9502')         -> '113-XHSC-9502'
        normalize_tag('113XHSC9502')            -> '113-XHSC-9502'
        normalize_tag('113-PZT-9,502')          -> '113-PZT-9502'
        normalize_tag('113-PZT-9.502')          -> '113-PZT-9502'
        normalize_tag('XV1004')                 -> 'XV-1004'
        normalize_tag('psv1521a')               -> 'PSV-1521A'
    """
    if not tag:
        return ''
    t = tag.strip().upper()
    t = re.sub(r'\s*-\s*', '-', t)   # '113 - PZT -3191' -> '113-PZT-3191'
    t = re.sub(r'\s+', '', t)        # any other stray internal whitespace
    t = re.sub(r'(?<=\d)[.,](?=\d)', '', t)  # '9,502' / '9.502' -> '9502'

    if '-' in t:
        return '-'.join(_strip_leading_zeros(part) for part in t.split('-'))

    m = _NO_SEPARATOR_TAG_RE.match(t)
    if m:
        unit, func, seq = m.groups()
        return f'{_strip_leading_zeros(unit)}-{func}-{seq}'

    m = _NO_SEPARATOR_SIMPLE_TAG_RE.match(t)
    if m:
        func, seq = m.groups()
        return f'{func}-{seq}'

    return t


def family_key(normalized_tag: str) -> str:
    """Coarser key than `normalize_tag`, for fuzzy cross-referencing (e.g.
    matching a tag mentioned in free-text comments against the IO table)
    where a trailing suffix letter on the sequence should be ignored:
    '113-XHSC-9502' and '113-XHSC-9502B' both key to '113-XHSC-9502'.
    Not used for legend format validation — only for equality matching
    between two tag strings.
    """
    if not normalized_tag:
        return ''
    parts = normalized_tag.split('-')
    if parts and re.match(r'^\d{3,5}[A-Z]$', parts[-1]):
        parts[-1] = parts[-1][:-1]
    return '-'.join(parts)


def normalize_lookup_value(value: str) -> str:
    """Case/whitespace/hyphen-insensitive canonical form for a plain coded
    lookup value (a field checked against a flat lookup table, not a
    composite tag): 'ai', 'AI', ' AI ' all normalize the same; runs of
    spaces and/or hyphens collapse to a single space, so 'GATE-VALVE' and
    'GATE VALVE' also normalize the same. Deliberately does NOT do partial/
    substring matching, abbreviation expansion, or typo-tolerance — those
    trade real precision for leniency (e.g. collapsing meaningfully
    distinct lookup entries like 'CENTRIFUGAL PUMP (MOTOR DRIVEN)' vs
    'CENTRIFUGAL PUMP (VERTICAL TYPE) ELECTRIC MOTOR DRIVEN' together), so
    they're intentionally left out rather than risk masking real
    mismatches as passes.
    """
    if not value:
        return ''
    return re.sub(r'[\s\-]+', ' ', value.strip().upper()).strip()


@dataclass(frozen=True)
class CompiledLegend:
    pattern: re.Pattern
    field_keys: tuple[str, ...]
    field_labels: dict[str, str]
    lookups: dict[str, dict[str, str]]
    separator: str

    def describe(self) -> str:
        """Human-readable expected format, e.g. 'SIZE-SERVICE-SERIAL'."""
        return self.separator.join(k.upper() for k in self.field_keys)


class LegendDefinitionError(ValueError):
    """The legend's `definition` JSON is structurally invalid."""


def compile_legend(definition: dict) -> CompiledLegend:
    """Compile a legend `definition` JSON into a matchable composite regex.

    Mirrors the shape (not the code) of
    apps.pid_checker_v2.services.legend_engine.compile_legend — same input
    JSON, independently implemented.
    """
    if not isinstance(definition, dict):
        raise LegendDefinitionError('legend definition must be a JSON object')
    fields = definition.get('fields')
    if not isinstance(fields, list) or not fields:
        raise LegendDefinitionError('legend definition must include at least one field')
    separator = definition.get('separator') or DEFAULT_SEPARATOR

    field_keys: list[str] = []
    field_labels: dict[str, str] = {}
    lookups: dict[str, dict[str, str]] = {}
    regex_parts: list[str] = []
    sep = re.escape(separator)
    # True until the first required field is seen. While true, an optional
    # field's separator is bundled as a TRAILING part of its own group
    # (rather than a leading separator belonging to whatever comes next) —
    # any of a run of leading optional fields could turn out to be the
    # first token actually present in a given tag, and a skipped one must
    # never leave a dangling separator requirement for whichever field
    # ends up first. Once a required field is hit, behavior reverts to a
    # plain leading separator (safe from then on, since something before
    # it is now guaranteed present).
    seen_required_field = False

    for i, field in enumerate(fields):
        if not isinstance(field, dict):
            raise LegendDefinitionError(f'field #{i} must be an object')
        key = str(field.get('key') or f'field_{i}').strip()
        if not key:
            raise LegendDefinitionError(f'field #{i} is missing a key')
        regex = str(field.get('regex') or '').strip()
        if not regex:
            raise LegendDefinitionError(f'field {key!r} is missing a regex')
        try:
            re.compile(regex)
        except re.error as exc:
            raise LegendDefinitionError(f'field {key!r} has invalid regex: {exc}') from exc

        suffix = str(field.get('suffix') or '')
        optional = bool(field.get('optional'))
        group = f'({regex})'
        if suffix:
            # Optional, not mandatory — a decorative unit marker like the
            # inch mark on a line size (e.g. line_numbering's '6"') is
            # commonly typed/extracted without it (e.g. '6-FL-AC6N-8112'
            # instead of '6"-FL-AC6N-8112'); the tag is exactly as valid
            # either way, so the suffix shouldn't be able to fail an
            # otherwise-correct tag on its own. Wrapped in a non-capturing
            # group so a multi-character suffix is optional as a whole,
            # not just its last character (a bare trailing '?' would only
            # apply to that one character).
            group = f'{group}(?:{re.escape(suffix)})?'

        is_last_field = (i == len(fields) - 1)
        if not seen_required_field and optional:
            group = f'(?:{group})?' if is_last_field else f'(?:{group}{sep})?'
        elif not seen_required_field:
            seen_required_field = True
        else:
            group = f'(?:{sep}{group})?' if optional else f'{sep}{group}'
        regex_parts.append(group)

        field_keys.append(key)
        field_labels[key] = str(field.get('label') or key)
        lookup = field.get('lookup')
        if isinstance(lookup, dict) and lookup:
            lookups[key] = {normalize_lookup_value(str(k)): str(v) for k, v in lookup.items()}

    full_pattern = r'^' + ''.join(regex_parts) + r'$'
    try:
        pattern = re.compile(full_pattern)
    except re.error as exc:
        raise LegendDefinitionError(f'compiled pattern is invalid: {exc}') from exc

    return CompiledLegend(
        pattern=pattern,
        field_keys=tuple(field_keys),
        field_labels=field_labels,
        lookups=lookups,
        separator=separator,
    )


def _closest_lookup_match(normalized_value: str, lookup: dict[str, str]) -> str | None:
    """Best-effort typo tolerance for a coded lookup value (e.g.
    'PRESURE' vs 'PRESSURE'): if `normalized_value` isn't an exact key in
    `lookup` but is very close to exactly one key, return that key as a
    suggested correction — surfaced as a 'warning'-severity finding rather
    than a hard 'error', since it's a guess, not a confirmed mismatch.

    Skips anything under 4 characters — short 2-3 letter codes (e.g. 'PT'
    vs 'PI', both legitimately valid/distinct ISA codes) are only one edit
    apart yet mean completely different things, so a distance-ratio match
    there is more likely to produce a false, misleading suggestion than to
    catch a real typo.
    """
    if not normalized_value or len(normalized_value) < 4:
        return None
    matches = difflib.get_close_matches(normalized_value, lookup.keys(), n=1, cutoff=0.75)
    return matches[0] if matches else None


def _lookup_code_finding(
    value: str, lookup: dict[str, str], field_key: str, field_label: str,
    section: str, row_id: Any, source: str,
) -> dict | None:
    """Shared by every lookup-membership check in this module (a code
    embedded inside a composite tag, or a standalone lookup-field value) —
    one place implementing 'exact match -> pass (None), close match ->
    warning with a suggestion, no match -> error', so typo tolerance
    covers every legend/tab uniformly rather than needing to be
    re-implemented per call site.
    """
    code = normalize_lookup_value(value)
    if code in lookup:
        return None
    suggestion = _closest_lookup_match(code, lookup)
    if suggestion:
        return {
            'row_id': row_id, 'source': source, 'field': field_key, 'section': section,
            'value': value, 'severity': 'warning',
            'issue': (
                f'"{value}" is not a recognised {field_label} code in the {section} legend — '
                f'possibly meant "{suggestion}" ({lookup[suggestion]})?'
            ),
            'expected': ', '.join(sorted(lookup.keys())),
        }
    return {
        'row_id': row_id, 'source': source, 'field': field_key, 'section': section,
        'value': value, 'severity': 'error',
        'issue': f'"{value}" is not a recognised {field_label} code in the {section} legend',
        'expected': ', '.join(sorted(lookup.keys())),
    }


def _check_one_tag(compiled: CompiledLegend, tag: str, row_id: Any, source: str) -> list[dict]:
    """Validate a single tag string against the compiled legend.

    Returns a list of 0-2 findings: a format mismatch (if the whole tag
    doesn't match the composite pattern), and/or one per unrecognised
    lookup code (if it does match but a coded field's value isn't in that
    field's lookup table).
    """
    findings: list[dict] = []
    match = compiled.pattern.match(normalize_tag(tag))
    if not match:
        findings.append({
            'row_id':  row_id,
            'source':  source,
            'field':   'tag_number',
            'value':   tag,
            'severity': 'error',
            'issue':   'Does not match the active legend\'s tag format',
            'expected': compiled.describe(),
        })
        return findings

    parts = match.groups()
    for key, value in zip(compiled.field_keys, parts):
        lookup = compiled.lookups.get(key)
        if not lookup or not value:
            continue
        finding = _lookup_code_finding(
            value, lookup, key, compiled.field_labels.get(key, key), 'legend', row_id, source,
        )
        if finding:
            findings.append(finding)
    return findings


def compare_io_with_legend(
    io_rows: list[dict],
    comments: list[dict],
    definition: dict,
) -> list[dict]:
    """Validate extracted I/O rows' tag_number (and comments' linked_tags)
    against a legend definition. Returns [] on any structural problem with
    the definition or if there's nothing to check — this must never raise,
    since it runs as an additive step inside document extraction.
    """
    if not definition:
        return []
    try:
        compiled = compile_legend(definition)
    except LegendDefinitionError:
        return []

    findings: list[dict] = []
    for row in io_rows:
        tag = (row.get('tag_number') or '').strip()
        if not tag:
            continue
        findings.extend(_check_one_tag(compiled, tag, row.get('tag_number'), 'io_row'))

    for comment in comments:
        for tag in comment.get('linked_tags') or []:
            tag = (tag or '').strip()
            if not tag:
                continue
            findings.extend(_check_one_tag(compiled, tag, comment.get('s_no'), 'comment'))

    return findings


def _check_lookup_value(
    compiled: CompiledLegend, value: str, row_id: Any, field_name: str, section: str,
) -> list[dict]:
    """Validate a single row field's value (e.g. a 'signal_type' column
    value) against a one-field lookup-shaped legend — format regex first,
    then lookup membership, both normalised via `normalize_lookup_value`
    (case/space/hyphen-insensitive).
    """
    if not compiled.field_keys:
        return []
    key = compiled.field_keys[0]
    normalized = normalize_lookup_value(value)
    match = compiled.pattern.match(normalized)
    if not match:
        return [{
            'row_id': row_id, 'source': 'io_row', 'field': field_name, 'section': section,
            'value': value, 'severity': 'error',
            'issue': f'"{value}" does not match the expected format for {compiled.field_labels.get(key, key)}',
            'expected': compiled.describe(),
        }]
    lookup = compiled.lookups.get(key)
    if lookup:
        finding = _lookup_code_finding(
            match.group(1), lookup, field_name, compiled.field_labels.get(key, key),
            section, row_id, 'io_row',
        )
        if finding:
            return [finding]
    return []


def compare_io_with_legends(
    io_rows: list[dict],
    comments: list[dict],
    format_legends: dict[str, dict],
    lookup_legends: dict[str, tuple[dict, str]],
) -> list[dict]:
    """Multi-section version of `compare_io_with_legend`.

    format_legends: {section: definition} — every currently-active
        *format*-kind legend (see models.IO_LEGEND_FORMAT_SECTIONS), all
        checked against the same 'tag_number' field and OR'd together — a
        tag only needs to match ONE active format legend to pass. This
        matters because I/O List rows carry one generic tag_number field,
        not a separate column per tag convention (instrument/equipment/
        well/line); OR-composition lets all five coexist without a row
        that legitimately follows one convention getting flagged against
        every other one too. A tag is only flagged if it matches NONE of
        the active format legends. A tag that DOES match one is still
        checked for unrecognised lookup codes within that match, same as
        the single-section compare_io_with_legend always has.

    lookup_legends: {section: (definition, field_name)} — every currently-
        active *lookup*-kind legend (see models.IO_LEGEND_LOOKUP_SECTIONS),
        each checked independently against its own named row field (e.g.
        'signal_types' -> the row's 'signal_type' value).

    Same never-raises contract as compare_io_with_legend — a structurally
    invalid definition is skipped, not fatal, since this runs as an
    additive step inside document extraction.
    """
    findings: list[dict] = []

    compiled_formats: dict[str, CompiledLegend] = {}
    for section, definition in format_legends.items():
        if not definition:
            continue
        try:
            compiled_formats[section] = compile_legend(definition)
        except LegendDefinitionError:
            continue

    if compiled_formats:
        def check_tag_against_any(tag: str, row_id: Any, source: str) -> list[dict]:
            normalized = normalize_tag(tag)
            matched_any = False
            per_match_findings: list[dict] = []
            for section, compiled in compiled_formats.items():
                match = compiled.pattern.match(normalized)
                if not match:
                    continue
                matched_any = True
                parts = match.groups()
                for key, value in zip(compiled.field_keys, parts):
                    lookup = compiled.lookups.get(key)
                    if not lookup or not value:
                        continue
                    finding = _lookup_code_finding(
                        value, lookup, key, compiled.field_labels.get(key, key),
                        section, row_id, source,
                    )
                    if finding:
                        per_match_findings.append(finding)
            if not matched_any:
                expected = '; '.join(f'{s}: {c.describe()}' for s, c in compiled_formats.items())
                return [{
                    'row_id': row_id, 'source': source, 'field': 'tag_number',
                    'value': tag, 'severity': 'error',
                    'issue': 'Does not match any active tag format',
                    'expected': expected,
                }]
            return per_match_findings

        for row in io_rows:
            tag = (row.get('tag_number') or '').strip()
            if not tag:
                continue
            findings.extend(check_tag_against_any(tag, row.get('tag_number'), 'io_row'))

        for comment in comments:
            for tag in comment.get('linked_tags') or []:
                tag = (tag or '').strip()
                if not tag:
                    continue
                findings.extend(check_tag_against_any(tag, comment.get('s_no'), 'comment'))

    for section, (definition, field_name) in lookup_legends.items():
        if not definition:
            continue
        try:
            compiled = compile_legend(definition)
        except LegendDefinitionError:
            continue
        for row in io_rows:
            raw_value = (row.get(field_name) or '').strip()
            if not raw_value:
                continue
            row_id = row.get('tag_number') or f'row with {field_name}={raw_value!r}'
            findings.extend(_check_lookup_value(compiled, raw_value, row_id, field_name, section))

    return findings


def check_field_against_legend(
    io_rows: list[dict], field_name: str, definition: dict, section: str,
) -> list[dict]:
    """Single-legend format+lookup check against ONE named row field —
    NOT OR'd against the other format legends the way tag_number is (see
    compare_io_with_legends' own docstring for why tag_number needs OR
    composition). Used for a field that unambiguously belongs to exactly
    one convention: 'equipment_tag'/'line_tag' are only ever populated by
    P&ID Vision extraction (services/pid_vision_extractor.py) reading an
    equipment symbol or a line-number callout respectively, so there's no
    ambiguity to OR away — a value in 'equipment_tag' should always be
    checked against 'equipment_register' specifically, nothing else.
    Never raises — same additive, best-effort contract as every other
    function in this module.
    """
    if not definition:
        return []
    try:
        compiled = compile_legend(definition)
    except LegendDefinitionError:
        return []

    findings: list[dict] = []
    for row in io_rows:
        value = (row.get(field_name) or '').strip()
        if not value:
            continue
        row_id = row.get('tag_number') or row.get(field_name)
        match = compiled.pattern.match(normalize_tag(value))
        if not match:
            findings.append({
                'row_id': row_id, 'source': 'io_row', 'field': field_name, 'section': section,
                'value': value, 'severity': 'error',
                'issue': f'"{value}" does not match the {section} legend\'s expected format',
                'expected': compiled.describe(),
            })
            continue
        for key, part_value in zip(compiled.field_keys, match.groups()):
            lookup = compiled.lookups.get(key)
            if not lookup or not part_value:
                continue
            finding = _lookup_code_finding(
                part_value, lookup, key, compiled.field_labels.get(key, key),
                section, row_id, 'io_row',
            )
            if finding:
                findings.append(finding)
    return findings


def check_symbol_types_against_legends(
    io_rows: list[dict], symbol_legends: dict[str, dict],
) -> list[dict]:
    """Validates every row's 'symbol_type' value (populated only by P&ID
    Vision extraction — see services/pid_vision_extractor.py, both for
    untagged symbols like valves/actuators/piping fittings AND as an
    enrichment on tagged instrument/equipment rows) against the OR'd
    union of every active symbol-shaped lookup section (models.
    IO_LEGEND_SYMBOL_LOOKUP_SECTIONS — valve_types, equipment_symbols,
    instrument_symbols, actuator_types, signal_line_types, ...).

    OR'd for the same reason compare_io_with_legends OR's the five
    tag_number format sections: a free-text symbol_type can't be
    pre-sorted into "definitely a valve vs definitely an actuator" before
    checking it, so matching ANY one active section's lookup is a pass.
    Typo-tolerant via the same _lookup_code_finding/_closest_lookup_match
    machinery every other lookup check in this module uses.

    Never raises — same additive, best-effort contract as everything else
    here; returns [] if no symbol-shaped legend is active or a definition
    is structurally invalid.
    """
    compiled_lookups: dict[str, dict[str, str]] = {}
    for section, (definition, field_key) in symbol_legends.items():
        if not definition:
            continue
        try:
            compiled = compile_legend(definition)
        except LegendDefinitionError:
            continue
        lookup = compiled.lookups.get(field_key)
        if lookup:
            compiled_lookups[section] = lookup

    if not compiled_lookups:
        return []

    combined_lookup: dict[str, str] = {}
    for lookup in compiled_lookups.values():
        combined_lookup.update(lookup)

    findings: list[dict] = []
    for row in io_rows:
        value = (row.get('symbol_type') or '').strip()
        if not value:
            continue
        code = normalize_lookup_value(value)
        if any(code in lookup for lookup in compiled_lookups.values()):
            continue
        # Prefix match: Vision reports the plain type ('GATE VALVE'); this
        # project's own legend often qualifies it further ('GATE VALVE
        # (NORMAL CLOSE)'/'(NORMAL OPEN)') — confirmed directly against a
        # real 26-entry valve_types legend where every gate/globe/ball/
        # needle valve is qualified by fail-state, no bare entry exists.
        # A plain, unqualified type name is a legitimate abbreviated form
        # of a qualified one, not a wrong answer — treat it as a pass
        # (silently, not even a warning) whenever the code is a
        # WORD-BOUNDARY prefix of at least one active entry (the entry
        # either ends right there, or continues with a space/punctuation,
        # never mid-word — 'GATE VALVE' must match cleanly against 'GATE
        # VALVE (NORMAL CLOSE)', but a genuine typo like 'GATE VALV' must
        # NOT, since it's also technically a raw string prefix of the same
        # entry). Deliberately passes even when several qualified variants
        # exist (e.g. both '...(NORMAL CLOSE)' and '...(NORMAL OPEN)') —
        # which specific fail-state a valve is in is often not reliably
        # readable from the symbol alone, so treating that ambiguity as a
        # legend violation would be a false positive, not a real catch.
        if any(
            key.startswith(code) and (len(key) == len(code) or not key[len(code)].isalnum())
            for key in combined_lookup
        ):
            continue
        row_id = (
            row.get('tag_number') or row.get('equipment_tag') or row.get('line_tag')
            or row.get('location') or value
        )
        finding = _lookup_code_finding(
            value, combined_lookup, 'symbol_type', 'symbol type',
            'symbol_types', row_id, 'io_row',
        )
        if finding:
            findings.append(finding)
    return findings
