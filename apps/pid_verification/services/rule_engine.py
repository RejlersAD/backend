"""
Deterministic Rule Engine
==========================
ALL validation logic is pure Python with no AI/ML calls.
Same extraction input ALWAYS produces identical findings.

Rule catalogue:
  TAG-001  Missing tags on instruments / valves
  TAG-002  Duplicate tag within drawing
  TAG-003  Tag format inconsistency  
  TAG-004  Tag referenced in notes but absent from drawing

  CON-001  Isolated instrument (no pipeline connection)
  CON-002  Isolated valve     (no pipeline connection)
  CON-003  Orphan node        (no connections at all in graph)

  VLV-001  Valve without a tag
  EQP-001  Equipment tag present but not in master list pattern

  LSZ-001  Missing line size text for known pipelines
  LSZ-002  Conflicting line sizes on the same line segment
  LSZ-003  Valve bore size does not match connected line size
  LSZ-004  Conflicting inline size annotations on the same OCR line reference
           (multiple distinct NPS sizes on a line containing a line-designation token)
  LSZ-005  3+ distinct nominal sizes on the drawing — possible undocumented spec-breaks
  LSZ-006  Same pipeline base with conflicting NPS sizes
  LSZ-007  Same pipeline designation 3+ times in one orientation
  LSZ-008  Pipeline designation confirmed in both H and V orientations
  LSZ-009  Cloud-truncated duplicate pipeline designation
  LSZ-010  Shared sequence-number / pipe-class / insulation suffix across
           different pipeline identities (area codes) on the same drawing
           -- strong indicator of a copy-paste error in line numbering

  NTS-001  NOTES section present but no tag references found
  NTS-002  HOLD item detected – requires action
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Expected tag format:  PREFIX-NUMBER(optional letter)
_TAG_FORMAT_RE = re.compile(r'^[A-Z]{1,4}-[0-9]{3,5}[A-Z]?$')
# Prefixes that MUST have a tag
_TAGGED_VALVE_PREFIXES = {'HV', 'FV', 'XV', 'PV', 'SDV', 'BDV', 'CV', 'LV', 'TV'}
_INSTRUMENT_PREFIXES   = {'FT', 'FI', 'FIC', 'PT', 'PI', 'PIC', 'LT', 'LI', 'LIC',
                          'TT', 'TI', 'TIC', 'AT', 'AI', 'FY', 'PY', 'LY'}

# Soft-coding policy for orphan connectivity findings.
# Instead of hard yes/no only, we score confidence and map to severity.
_ORPHAN_CONFIDENCE_HIGH = 0.75
_ORPHAN_CONFIDENCE_MEDIUM = 0.45
_ORPHAN_LOW_TEXT_CUTOFF = 60

# ── LSZ-004 soft-coded knobs ─────────────────────────────────────────────────
# Regex to extract well-formed pipeline designation tokens from a noisy OCR line.
# Matches patterns like  4"-D-5749-013842-X-N  or  2"-BD-6003-033842-X
_LSZ004_LINE_TAG_RE = re.compile(
    r'\d{1,3}(?:\.\d+)?["\u201c\u201d]\s*-[A-Z]{1,4}-\d{3,6}-\d{4,10}(?:-[A-Z0-9]+)*',
    re.IGNORECASE,
)
# Maximum LSZ-004 findings emitted per drawing (avoids flooding the report).
_LSZ004_MAX_FINDINGS = 5
# Minimum distinct NPS sizes on a single OCR line to raise LSZ-004.
_LSZ004_MIN_SIZES = 2

# ── LSZ-005 soft-coded knobs ─────────────────────────────────────────────────
# Minimum number of *distinct* nominal sizes on a drawing to trigger LSZ-005.
# Increase to suppress; decrease to be more sensitive.
_LSZ005_MIN_DISTINCT_SIZES = 3
# Valid pipe-size range (inches) — filters out OCR artefacts.
_LSZ005_SIZE_MIN_INCH = 0.5   # ½" is the smallest common instrument line
_LSZ005_SIZE_MAX_INCH = 48.0  # 48" is the largest standard pipe
# ── LSZ-010 soft-coded knobs ─────────────────────────────────────────────────
# Fields from line_tag that form the "shared suffix" used for grouping.
# Reorder or remove fields to tune sensitivity:
#   ('sequence_no', 'pipe_class', 'insulation') catches  013842-X-N  matches
#   ('sequence_no',)                             catches  013842      matches (wider net)
_LSZ010_SUFFIX_FIELDS = ('sequence_no', 'pipe_class', 'insulation')
# When True: only flag shared-suffix conflicts where fluid codes also match.
# Recommended True -- different fluid systems legitimately reuse sequence numbers.
_LSZ010_SAME_FLUID_ONLY = True
# When True: only flag when NPS sizes also match (tighter, fewer false positives).
_LSZ010_REQUIRE_SAME_SIZE = False
# Maximum LSZ-010 findings per drawing (avoids flooding the report on dense sheets).
_LSZ010_MAX_FINDINGS = 10
# Minimum non-empty suffix fields required to consider an entry checkable.
# Prevents empty-field entries from creating spurious cross-matches.
_LSZ010_MIN_SUFFIX_PARTS = 1


@dataclass
class RuleFinding:
    category:        str
    rule_id:         str
    issue_observed:  str
    action_required: str
    evidence:        str  = ''
    direction:       str  = 'N/A'
    severity:        str  = 'major'


def run_rules(extraction: Dict[str, Any], graph) -> List[RuleFinding]:
    """
    Execute all rules against a single drawing's extraction + graph.
    Returns a sorted, deterministic list of RuleFinding objects.
    """
    findings: List[RuleFinding] = []

    findings.extend(_check_tag_issues(extraction))
    findings.extend(_check_connectivity(extraction, graph))
    findings.extend(_check_valve_equipment(extraction))
    findings.extend(_check_line_sizes(extraction))
    findings.extend(_check_notes_holds(extraction))
    findings.extend(_check_pipeline_tag_duplicates(extraction))
    findings.extend(_check_shared_suffix_across_identities(extraction))

    # Sort deterministically: rule_id → issue_observed
    findings.sort(key=lambda f: (f.rule_id, f.issue_observed))
    return findings


# ---------------------------------------------------------------------------
# TAG RULES
# ---------------------------------------------------------------------------

def _check_tag_issues(extraction: Dict[str, Any]) -> List[RuleFinding]:
    out = []
    tags = extraction.get('tags', [])

    # TAG-001: Instrument/valve without a tag in the tag list
    for item in extraction.get('instruments', []) + extraction.get('valves', []):
        if item.get('tag') and item['tag'] not in tags:
            out.append(RuleFinding(
                category='tag',
                rule_id='TAG-001',
                issue_observed=f"Element '{item['tag']}' detected via OCR but not in consolidated tag list",
                action_required='Verify tag label on drawing; re-issue if missing',
                evidence=item['tag'],
                severity='major',
            ))

    # TAG-002: Duplicate tags
    seen = set()
    for tag in tags:
        if tag in seen:
            out.append(RuleFinding(
                category='tag',
                rule_id='TAG-002',
                issue_observed=f"Duplicate tag '{tag}' found on drawing",
                action_required='Remove or renumber duplicate tag to maintain uniqueness',
                evidence=tag,
                severity='critical',
            ))
        seen.add(tag)

    # TAG-003: Non-standard tag format
    for tag in tags:
        if not _TAG_FORMAT_RE.match(tag):
            out.append(RuleFinding(
                category='tag',
                rule_id='TAG-003',
                issue_observed=f"Tag '{tag}' does not match standard format PREFIX-NNNN",
                action_required='Rename tag to conform to instrument tag naming convention',
                evidence=tag,
                severity='minor',
            ))

    # TAG-004: Tags in notes not present on drawing
    all_note_text = ' '.join(extraction.get('notes', []) + extraction.get('holds', []))
    note_tags = set(re.findall(r'\b[A-Z]{1,4}-[0-9]{3,5}[A-Z]?\b', all_note_text))
    drawing_tags = set(tags)
    for ntag in sorted(note_tags - drawing_tags):
        out.append(RuleFinding(
            category='tag',
            rule_id='TAG-004',
            issue_observed=f"Tag '{ntag}' referenced in notes/HOLDs but not found on drawing",
            action_required='Add missing tag to drawing or update note reference',
            evidence=ntag,
            severity='major',
        ))

    return out


# ---------------------------------------------------------------------------
# CONNECTIVITY RULES
# ---------------------------------------------------------------------------

def _check_connectivity(extraction: Dict[str, Any], graph) -> List[RuleFinding]:
    out = []

    try:
        from apps.pid_verification.services.graph_builder import get_isolated_nodes
        isolated = get_isolated_nodes(graph)
    except Exception:
        isolated = []

    instr_tags = {i.get('tag') for i in extraction.get('instruments', []) if i.get('tag')}
    valve_tags = {v.get('tag') for v in extraction.get('valves', []) if v.get('tag')}
    raw_text_len = len(extraction.get('raw_text', '') or '')

    for node in sorted(isolated):
        if node in instr_tags:
            kind = 'instrument'
            rule_id = 'CON-001'
            noun = 'Instrument'
            action = 'Connect instrument to process line or verify if stand-alone'
        elif node in valve_tags:
            kind = 'valve'
            rule_id = 'CON-002'
            noun = 'Valve'
            action = 'Connect valve to upstream and downstream pipelines'
        else:
            kind = 'other'
            rule_id = 'CON-003'
            noun = 'Node'
            action = 'Verify element belongs to this drawing; connect or remove'

        confidence = _orphan_confidence(node, extraction, kind)
        band = _confidence_band(confidence, raw_text_len)
        severity = _orphan_severity_for_band(kind, band)

        out.append(RuleFinding(
            category='connectivity',
            rule_id=rule_id,
            issue_observed=(
                f"Possible orphan {noun.lower()} '{node}' has no connections in graph "
                f"(confidence: {band}, score: {confidence:.2f})"
            ),
            action_required=(
                f"{action}. Perform a quick visual check on drawing before closing issue "
                "(soft rule)."
            ),
            evidence=node,
            severity=severity,
        ))

    return out


def _orphan_confidence(node: str, extraction: Dict[str, Any], kind: str) -> float:
    """Return 0..1 confidence that orphan finding is real and not extraction noise."""
    score = 0.0

    tags = set(extraction.get('tags', []) or [])
    raw_text = extraction.get('raw_text', '') or ''

    # Evidence 1: canonical tag list contains this exact node.
    if node in tags:
        score += 0.45

    # Evidence 2: appears in OCR text one or more times.
    if raw_text:
        count = len(re.findall(rf'\b{re.escape(node)}\b', raw_text, flags=re.IGNORECASE))
        if count >= 2:
            score += 0.30
        elif count == 1:
            score += 0.18

    # Evidence 3: type-specific weight.
    if kind in {'instrument', 'valve'}:
        score += 0.18
    else:
        score += 0.10

    # Evidence 4: tag format looks valid.
    if _TAG_FORMAT_RE.match(node):
        score += 0.07

    return min(score, 1.0)


def _confidence_band(score: float, raw_text_len: int) -> str:
    """Map confidence score into low/medium/high and degrade if OCR text is sparse."""
    if score >= _ORPHAN_CONFIDENCE_HIGH:
        band = 'high'
    elif score >= _ORPHAN_CONFIDENCE_MEDIUM:
        band = 'medium'
    else:
        band = 'low'

    # If OCR extracted very little text, avoid aggressive confidence.
    if raw_text_len < _ORPHAN_LOW_TEXT_CUTOFF:
        if band == 'high':
            return 'medium'
        if band == 'medium':
            return 'low'
    return band


def _orphan_severity_for_band(kind: str, band: str) -> str:
    """Soft severity policy by type + confidence band."""
    if band == 'high':
        if kind == 'valve':
            return 'major'
        if kind == 'instrument':
            return 'major'
        return 'major'
    if band == 'medium':
        return 'minor'
    return 'info'


# ---------------------------------------------------------------------------
# VALVE & EQUIPMENT RULES
# ---------------------------------------------------------------------------

def _check_valve_equipment(extraction: Dict[str, Any]) -> List[RuleFinding]:
    out = []

    for valve in extraction.get('valves', []):
        tag = valve.get('tag', '')
        prefix = tag.split('-')[0] if '-' in tag else ''
        if not tag:
            out.append(RuleFinding(
                category='valve',
                rule_id='VLV-001',
                issue_observed='Valve symbol detected without a tag label',
                action_required='Add tag to valve per instrument tag numbering system',
                evidence='',
                severity='critical',
            ))
        elif prefix in _TAGGED_VALVE_PREFIXES and tag not in extraction.get('tags', []):
            out.append(RuleFinding(
                category='valve',
                rule_id='VLV-001',
                issue_observed=f"Valve '{tag}' not found in consolidated tag list",
                action_required='Add valve tag to tag list or correct label',
                evidence=tag,
                severity='major',
            ))

    return out


# ---------------------------------------------------------------------------
# LINE SIZE RULES
# ---------------------------------------------------------------------------

def _check_line_sizes(extraction: Dict[str, Any]) -> List[RuleFinding]:
    out = []
    pipelines  = extraction.get('pipelines', [])
    line_sizes = extraction.get('line_sizes', [])
    raw_text = extraction.get('raw_text', '')

    # LSZ-001: Pipelines with no recorded size
    for pipeline in pipelines:
        if not pipeline.get('size'):
            out.append(RuleFinding(
                category='line_size',
                rule_id='LSZ-001',
                issue_observed=f"Pipeline '{pipeline.get('line_id', 'unknown')}' has no line size annotation",
                action_required='Add nominal pipe size to line designation',
                evidence=pipeline.get('line_id', ''),
                severity='major',
            ))

    # LSZ-002: Conflicting sizes on same pipeline (requires pipeline.size list)
    pipeline_sizes: dict = {}
    for pipeline in pipelines:
        lid  = pipeline.get('line_id', '')
        size = pipeline.get('size', '')
        if lid and size:
            if lid in pipeline_sizes and pipeline_sizes[lid] != size:
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-002',
                    issue_observed=f"Conflicting line sizes on pipeline '{lid}': "
                                   f"'{pipeline_sizes[lid]}' vs '{size}'",
                    action_required='Resolve conflicting sizes; verify pipeline continuity',
                    evidence=lid,
                    severity='critical',
                ))
            else:
                pipeline_sizes[lid] = size

    # Flag line size texts that could not be attributed to any pipeline.
    # If no pipelines are extracted, this becomes noisy and misleading.
    if pipelines:
        attributed_sizes = {p.get('size') for p in pipelines if p.get('size')}
        for ls in line_sizes:
            if ls['text'] not in attributed_sizes:
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-001',
                    issue_observed=f"Line size '{ls['text']}' found on drawing but not mapped to any pipeline",
                    action_required='Associate line size annotation with its pipeline designation',
                    evidence=ls['text'],
                    direction=ls.get('direction', 'unknown'),
                    severity='minor',
                ))

    # LSZ-003: Explicit valve-size vs line-size mismatch found in text
    out.extend(_check_valve_line_size_mismatch(raw_text, line_sizes))

    # LSZ-005: Drawing-specific multi-size transition observation.
    out.extend(_check_multi_size_transition_observation(raw_text, line_sizes))

    return out


def _normalize_size_token(token: str) -> str:
    """Normalize size token to canonical display (e.g., 6 -> 6\")."""
    t = token.strip().lower().replace(' ', '')
    t = t.replace("''", '"')
    if t.endswith('mm'):
        return t
    if t.endswith('"'):
        return t
    return f'{t}"'


def _check_valve_line_size_mismatch(raw_text: str, line_sizes: List[Dict[str, Any]] | None = None) -> List[RuleFinding]:
    """
    Detect mismatch patterns like:
      6" valve ... 4" line
    and return a critical, actionable finding.
    """
    out: List[RuleFinding] = []
    if not raw_text:
        return out

    for line in raw_text.splitlines():
        line_lower = line.lower()
        if 'valve' not in line_lower:
            continue
        if 'line' not in line_lower and 'pipe' not in line_lower:
            continue

        size_tokens = re.findall(r'(\d+(?:\.\d+)?(?:\s*(?:"|\'\'|mm))?)', line, flags=re.IGNORECASE)
        normalized = [_normalize_size_token(s) for s in size_tokens if s.strip()]

        unique_sizes = []
        for s in normalized:
            if s not in unique_sizes:
                unique_sizes.append(s)

        if len(unique_sizes) >= 2:
            valve_size = unique_sizes[0]
            line_size = unique_sizes[1]
            if valve_size != line_size:
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-003',
                    issue_observed=f"Valve size '{valve_size}' does not match connected line size '{line_size}'",
                    action_required='Verify valve bore size against line size and correct drawing/specification mismatch',
                    evidence=line.strip()[:240],
                    direction='N/A',
                    severity='critical',
                ))

    # Fallback heuristic for noisy OCR where "valve" word is not detected but
    # valve callouts often end with "-V" and include inch-size text.
    if not out:
        fallback = _check_valve_line_size_mismatch_fallback(raw_text, line_sizes or [])
        out.extend(fallback)

    # Secondary deterministic fallback: if a single OCR line contains
    # multiple distinct inch sizes plus a pipeline-like token, flag it.
    # Example caught: "... 6\" 4\"-BD-4860-033842-X-N ..."
    if not out:
        out.extend(_check_inline_size_conflict_with_line_token(raw_text))

    return out


def _check_inline_size_conflict_with_line_token(raw_text: str) -> List[RuleFinding]:
    """
    LSZ-004  An OCR text-line contains a pipeline-designation token AND two or
    more distinct NPS inch-sizes.  Classic symptom: a valve callout sitting next
    to a line designation where the valve bore differs from the pipe nominal size,
    or two different line designations with different sizes on the same text line.

    Soft-coded via module-level constants:
      _LSZ004_LINE_TAG_RE  -- pattern that recognises a line-designation token
      _LSZ004_MAX_FINDINGS -- cap to avoid flooding the report with noise
      _LSZ004_MIN_SIZES    -- minimum distinct sizes required to raise the finding
    """
    out: List[RuleFinding] = []
    if not raw_text:
        return out

    size_token_re = re.compile(r'\b(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')')

    # Track which size-conflict frozensets we have already reported so that
    # repeated OCR lines do not generate identical duplicate findings.
    seen_conflict_keys: set = set()

    for ocr_line in raw_text.splitlines():
        # Only examine lines that contain at least one line-designation token.
        if not _LSZ004_LINE_TAG_RE.search(ocr_line):
            continue

        # Collect distinct NPS sizes found on this OCR line.
        sizes: list = []
        for m in size_token_re.finditer(ocr_line):
            s = f'{m.group(1)}"'
            if s not in sizes:
                sizes.append(s)

        if len(sizes) < _LSZ004_MIN_SIZES:
            continue

        conflict_key = frozenset(sizes)
        if conflict_key in seen_conflict_keys:
            continue
        seen_conflict_keys.add(conflict_key)

        # Build clean evidence: prefer extracted line-tag tokens over raw OCR.
        tag_tokens = _LSZ004_LINE_TAG_RE.findall(ocr_line)
        if tag_tokens:
            sizes_str = ", ".join(sizes)
            tags_str  = "  ·  ".join(list(dict.fromkeys(tag_tokens))[:4])
            evidence  = f"Sizes [{sizes_str}] on: {tags_str}"
        else:
            evidence = re.sub(r'[^\w\s"./%-]', " ", ocr_line).strip()[:160]

        if len(sizes) == 2:
            sizes_label = f'{sizes[0]} and {sizes[1]}'
        else:
            sizes_label = ", ".join(sizes[:-1]) + f' and {sizes[-1]}'

        out.append(RuleFinding(
            category="line_size",
            rule_id="LSZ-004",
            issue_observed=(
                f"Conflicting inline size annotations {sizes_label} "
                "detected on the same line reference"
            ),
            action_required=(
                "Verify valve/line nominal sizes and add a reducer or correct "
                "the line designation as required."
            ),
            evidence=evidence,
            direction="N/A",
            severity="critical",
        ))

        if len(out) >= _LSZ004_MAX_FINDINGS:
            break

    return out
def _check_valve_line_size_mismatch_fallback(raw_text: str, line_sizes: List[Dict[str, Any]]) -> List[RuleFinding]:
    out: List[RuleFinding] = []
    if not raw_text:
        return out

    inch_pattern = re.compile(r'\b(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\')')
    valve_inch_pattern = re.compile(r'\b(\d{1,4}(?:\.\d+)?)\s*(?:"|\'\')')
    valve_like_line = re.compile(r'\b\S*-V\b', flags=re.IGNORECASE)

    def _is_reasonable_size(s: str) -> bool:
        try:
            v = float(s.replace('"', '').strip())
            return 2.0 <= v <= 24.0
        except Exception:
            return False

    def _coerce_ocr_size(raw_num: str) -> str | None:
        """Accept only direct, reasonable OCR sizes (no trailing-digit recovery)."""
        try:
            v = float(raw_num)
        except Exception:
            return None

        if not (2.0 <= v <= 24.0):
            return None
        if float(v).is_integer():
            return f'{int(v)}"'
        return f'{v}"'

    # Prefer extracted line-size annotations for line side of the comparison.
    drawing_line_sizes = []
    for ls in line_sizes:
        text = str(ls.get('text', '')).strip()
        if text.endswith('"') and _is_reasonable_size(text) and text not in drawing_line_sizes:
            drawing_line_sizes.append(text)

    # Fallback if extractor could not map line_sizes list.
    if not drawing_line_sizes:
        all_inch_sizes = [f"{m.group(1)}\"" for m in inch_pattern.finditer(raw_text)]
        for s in all_inch_sizes:
            if _is_reasonable_size(s) and s not in drawing_line_sizes:
                drawing_line_sizes.append(s)

    # Candidate valve sizes from lines that look like valve callouts
    valve_size_candidates = []
    valve_evidence_line = ''
    for line in raw_text.splitlines():
        if not valve_like_line.search(line):
            continue
        matches = [m.group(1) for m in valve_inch_pattern.finditer(line)]
        for raw_num in matches:
            size = _coerce_ocr_size(raw_num)
            if size and _is_reasonable_size(size) and size not in valve_size_candidates:
                valve_size_candidates.append(size)
                valve_evidence_line = line.strip()[:240]

        # Prefer local comparison on the same OCR line to avoid blended data
        # from unrelated parts of the diagram.
        local_sizes = []
        for m in inch_pattern.finditer(line):
            s = f"{m.group(1)}\""
            if _is_reasonable_size(s) and s not in local_sizes:
                local_sizes.append(s)

        if len(local_sizes) >= 2:
            local_sorted = sorted(local_sizes, key=_size_value)
            valve_size_local = local_sorted[-1]
            line_size_local = local_sorted[0]
            if valve_size_local != line_size_local:
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-003',
                    issue_observed=f"Valve size '{valve_size_local}' does not match connected line size '{line_size_local}'",
                    action_required='Verify valve bore size against line size and correct drawing/specification mismatch',
                    evidence=line.strip()[:240],
                    direction='N/A',
                    severity='critical',
                ))
                return out

    # Secondary fallback: raw drawing sizes not present in primary line-size set.
    if not valve_size_candidates:
        all_inch_sizes = [f"{m.group(1)}\"" for m in inch_pattern.finditer(raw_text)]
        for size in all_inch_sizes:
            if _is_reasonable_size(size) and size not in drawing_line_sizes and size not in valve_size_candidates:
                valve_size_candidates.append(size)

    if not valve_size_candidates or not drawing_line_sizes:
        return out

    # If no local valve line comparison was possible, use a conservative
    # fallback that only compares dominant valve candidate vs dominant line size.
    # This remains deterministic but avoids aggressive global min/max blending.
    def _size_value(s: str) -> float:
        try:
            return float(s.replace('"', '').strip())
        except Exception:
            return 0.0

    valve_size = max(valve_size_candidates, key=_size_value)
    # Guard against synthetic OCR candidates that are not present in extracted
    # diagram line-size annotations (prevents blended false positives).
    if valve_size not in drawing_line_sizes:
        return out
    # Use most frequent extracted line size first, then larger value tie-breaker.
    freq = {}
    for s in drawing_line_sizes:
        freq[s] = freq.get(s, 0) + 1
    line_size = sorted(drawing_line_sizes, key=lambda s: (freq.get(s, 0), _size_value(s)), reverse=True)[0]

    if valve_size != line_size:
        out.append(RuleFinding(
            category='line_size',
            rule_id='LSZ-003',
            issue_observed=f"Valve size '{valve_size}' does not match connected line size '{line_size}'",
            action_required='Verify valve bore size against line size and correct drawing/specification mismatch',
            evidence=valve_evidence_line or 'OCR fallback: valve-like callout vs line-size annotation',
            direction='N/A',
            severity='critical',
        ))

    return out


def _check_multi_size_transition_observation(raw_text: str, line_sizes: List[dict]) -> List[RuleFinding]:
    """
    LSZ-005  Three or more *distinct* nominal pipe sizes are present on the drawing.

    This observation flags drawings with many size transitions so the engineer
    can confirm every spec-break / reducer is documented.  The exact set of
    sizes detected is reported dynamically rather than hard-coding a specific triplet.

    Soft-coded via module constants:
      _LSZ005_MIN_DISTINCT_SIZES  -- number of distinct sizes required to fire
      _LSZ005_SIZE_MIN_INCH       -- lower bound for a valid pipe size (inches)
      _LSZ005_SIZE_MAX_INCH       -- upper bound for a valid pipe size (inches)
    """
    out: List[RuleFinding] = []
    if not line_sizes:
        return out

    def _inch_value(txt: str):
        """Return float inch value from '4"'  '25mm', or None if unparseable."""
        txt = txt.strip().replace("\u201c", "\"").replace("\u201d", "\"").replace("''", "\"")
        if txt.endswith("\""):
            try:
                return float(txt.rstrip("\"").strip())
            except ValueError:
                return None
        if txt.lower().endswith("mm"):
            try:
                return float(txt[:-2].strip()) / 25.4
            except ValueError:
                return None
        return None

    # Collect distinct validated sizes from the extraction line_sizes list.
    valid_sizes: dict = {}   # canonical_text -> inch_value
    for ls in line_sizes:
        raw = str(ls.get("text", "")).strip()
        # Normalise curly / smart quotes to straight double-quote.
        canonical = raw.replace("\u201c", "\"").replace("\u201d", "\"").replace("''", "\"")
        if not canonical.endswith("\""):
            continue
        val = _inch_value(canonical)
        if val is None:
            continue
        if _LSZ005_SIZE_MIN_INCH <= val <= _LSZ005_SIZE_MAX_INCH:
            if canonical not in valid_sizes:
                valid_sizes[canonical] = val

    if len(valid_sizes) < _LSZ005_MIN_DISTINCT_SIZES:
        return out

    # Sort sizes smallest to largest for a readable display.
    sorted_sizes = sorted(valid_sizes.keys(), key=lambda s: valid_sizes[s])

    # Human-friendly label: '2", 4", and 8"'
    if len(sorted_sizes) == _LSZ005_MIN_DISTINCT_SIZES:
        sizes_label = f'{sorted_sizes[0]}, {sorted_sizes[1]}, and {sorted_sizes[2]}'
    else:
        sizes_label = ", ".join(sorted_sizes[:-1]) + f', and {sorted_sizes[-1]}'

    out.append(RuleFinding(
        category="line_size",
        rule_id="LSZ-005",
        issue_observed=(
            f"Multiple nominal sizes {sizes_label} detected on this drawing segment"
        ),
        action_required=(
            "Verify intended reducers / spec-breaks and confirm each size "
            "transition is documented on the line route with a reducer symbol "
            "and updated line designations."
        ),
        evidence=f"Detected sizes: {sizes_label} on same diagram context",
        direction="N/A",
        severity="major",
    ))

    return out
# ---------------------------------------------------------------------------
# NOTES & HOLDs RULES
# ---------------------------------------------------------------------------

def _check_notes_holds(extraction: Dict[str, Any]) -> List[RuleFinding]:
    out = []
    notes  = extraction.get('notes', [])
    holds  = extraction.get('holds', [])
    tags   = set(extraction.get('tags', []))

    # NTS-001: Notes present but no tag references
    if notes:
        note_text = ' '.join(notes)
        note_tags = set(re.findall(r'\b[A-Z]{1,4}-[0-9]{3,5}[A-Z]?\b', note_text))
        if not note_tags:
            out.append(RuleFinding(
                category='notes',
                rule_id='NTS-001',
                issue_observed='drawing notes present but contain no tag references',
                action_required='Review notes and associate each note with the relevant tag(s) or equipment',
                evidence=notes[0][:120] if notes else '',
                severity='minor',
            ))

    # NTS-002: Every HOLD item is flagged as requiring action
    for hold in holds:
        out.append(RuleFinding(
            category='notes',
            rule_id='NTS-002',
            issue_observed=f"HOLD detected: {hold[:120]}",
            action_required='Resolve HOLD item and update drawing revision',
            evidence=hold[:200],
            severity='major',
        ))

    return out


# ---------------------------------------------------------------------------
# PIPELINE LINE DESIGNATION RULES  (LSZ-006, LSZ-007)
# ---------------------------------------------------------------------------

def _check_pipeline_tag_duplicates(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    LSZ-006  Same pipeline base (fluid + area + seq + class + insulation) detected
             with conflicting NPS sizes → likely labelling error or missing reducer.
    LSZ-007  Same designation detected 3+ times in a single orientation → possible
             label-copy error (warning only; legitimate on multi-sheet drawings).
    """
    out: List[RuleFinding] = []
    line_tags = extraction.get('line_tags', [])
    if not line_tags:
        return out

    # LSZ-006 ────────────────────────────────────────────────────────────
    # Group by base (everything except NPS size)
    base_groups: dict = {}
    for lt in line_tags:
        base_key = '-'.join([
            lt.get('fluid_code', ''),
            lt.get('area_code',   ''),
            lt.get('sequence_no', ''),
            lt.get('pipe_class',  ''),
            lt.get('insulation',  ''),
        ]).upper().strip('-')
        if not base_key:
            continue
        base_groups.setdefault(base_key, []).append(lt)

    for base_key, entries in base_groups.items():
        sizes = list({e.get('size', '') for e in entries if e.get('size')})
        if len(sizes) > 1:
            texts = [e.get('text', '') for e in entries]
            out.append(RuleFinding(
                category='line_size',
                rule_id='LSZ-006',
                issue_observed=(
                    f"Pipeline base '{base_key}' found with conflicting NPS sizes: "
                    f"{', '.join(sorted(sizes))} — possible reducer or labelling error"
                ),
                action_required=(
                    'Confirm whether a size transition (reducer) is intended. '
                    'If so, add a reducer symbol and update line designations. '
                    'Otherwise correct the mislabelled tag.'
                ),
                evidence='; '.join(texts[:3]),
                severity='major',
            ))

    # LSZ-007 ────────────────────────────────────────────────────────────
    # Same full designation appearing ≥3 times in the same orientation
    for lt in line_tags:
        for direction in ('H', 'V'):
            same_dir = [o for o in lt.get('occurrences', []) if o['direction'] == direction]
            if len(same_dir) >= 3:
                dir_label = 'horizontal' if direction == 'H' else 'vertical'
                coords = '; '.join(
                    f"({o['x_pct']:.1f}%, {o['y_pct']:.1f}%)" for o in same_dir[:3]
                )
                out.append(RuleFinding(
                    category='line_size',
                    rule_id='LSZ-007',
                    issue_observed=(
                        f"Pipeline tag '{lt.get('text', '')}' appears {len(same_dir)} times "
                        f"in {dir_label} orientation — possible label-copy error"
                    ),
                    action_required=(
                        'Verify intended multiplicity. Remove duplicate labels if the line '
                        'does not re-enter this drawing area. Multiple occurrences are '
                        'normal on multi-sheet or continuation drawings.'
                    ),
                    evidence=f"{lt.get('text','')} @ {coords}",
                    severity='minor',
                ))

    # LSZ-008 ────────────────────────────────────────────────────────────
    # Same designation confirmed in BOTH H and V orientations (multi-angle duplicate)
    # This is the most common duplicate type: the label runs along the pipe in one
    # direction and also appears as a cross-reference note in the perpendicular axis.
    for lt in line_tags:
        if not lt.get('multi_angle'):
            continue
        occs = lt.get('occurrences', [])
        h_occs = [o for o in occs if o['direction'] == 'H' and o.get('x_pct') is not None]
        v_occs = [o for o in occs if o['direction'] == 'V' and o.get('x_pct') is not None]
        if not h_occs or not v_occs:
            continue
        h_coord = f"({h_occs[0]['x_pct']:.1f}%, {h_occs[0]['y_pct']:.1f}%)"
        v_coord = f"({v_occs[0]['x_pct']:.1f}%, {v_occs[0]['y_pct']:.1f}%)"
        tag_text = lt.get('text', '')
        # Soft-coded: evidence STARTS with the full tag text so that the frontend
        # overlay can extract the NPS size prefix (e.g. '4"') and map it to a
        # diagram-anchored position via tag_positions.  The coordinate detail
        # follows for audit traceability.
        out.append(RuleFinding(
            category='line_size',
            rule_id='LSZ-008',
            issue_observed=(
                f"Pipeline tag '{tag_text}' detected in both horizontal "
                f"and vertical orientations — confirmed duplicate label on this drawing"
            ),
            action_required=(
                'Verify the line physically re-enters this drawing area in a different '
                'direction. If the label is a continuation reference, ensure arrows and '
                'sheet cross-references are present per engineering drafting standard.'
            ),
            evidence=f"{tag_text}  H @ {h_coord}  ·  V @ {v_coord}",
            severity='minor',
        ))

    # LSZ-009 ────────────────────────────────────────────────────────────
    # Cloud-truncated duplicate pipeline designation.
    # Fired when extraction finds the same line identity (size + fluid + area +
    # sequence) twice: once with a full pipe_class/insulation suffix and once
    # without — the truncated form is almost certainly the same physical label
    # partially covered by a revision cloud.
    # The full entry's occurrences include both the original and the merged
    # truncated occurrence (set by the cloud-truncation resolution pass in
    # extraction.py), so the exact drawing positions are already available.
    for lt in line_tags:
        if not lt.get('cloud_truncation_detected'):
            continue
        tag_text = lt.get('text', '')
        occ_count = len(lt.get('occurrences', []))
        out.append(RuleFinding(
            category='line_size',
            rule_id='LSZ-009',
            issue_observed=(
                f"Cloud-truncated duplicate detected: pipeline tag '{tag_text}' (full designation) "
                f"appears alongside a second truncated occurrence missing the pipe-class / "
                f"insulation suffix. A revision cloud is likely obscuring the trailing suffix "
                f"on one label. Tag found at {occ_count} location(s) on this drawing."
            ),
            action_required=(
                'Visually inspect all occurrences of this line tag on the drawing. '
                'Confirm whether the truncated label is the same physical line with its '
                'suffix obscured by a revision cloud, or a genuinely separate line. '
                'If it is the same line, update the truncated label to show the complete '
                'designation including the full pipe-class and insulation/tracing suffix.'
            ),
            evidence=tag_text,
            severity='critical',
        ))

    return out

# ---------------------------------------------------------------------------
# LSZ-010  Shared sequence-number / suffix across different pipeline identities
# ---------------------------------------------------------------------------

def _check_shared_suffix_across_identities(extraction: dict) -> list:
    """
    LSZ-010  Two or more pipeline tags on the same drawing share an identical
    trailing suffix (sequence_no + pipe_class + insulation, configurable via
    _LSZ010_SUFFIX_FIELDS) but belong to DIFFERENT pipeline identities
    (different area codes, and optionally different fluid codes).

    This pattern is the most common copy-paste error in P&ID line numbering:
    an engineer copies a line designation, updates the fluid/area segment, but
    forgets to change the sequence number and pipe-class suffix.

    Example (the case that motivated this rule):
      4\"-D-5749-013842-X-N   area 5749
      4\"-D-5690-013842-X-N   area 5690
      Shared suffix: 013842-X-N  |  Different areas: 5749 vs 5690

    Soft-coded via module-level constants:
      _LSZ010_SUFFIX_FIELDS       -- tuple of line_tag keys forming the "shared suffix"
      _LSZ010_SAME_FLUID_ONLY     -- only flag when fluid codes also match
      _LSZ010_REQUIRE_SAME_SIZE   -- only flag when NPS sizes also match
      _LSZ010_MAX_FINDINGS        -- cap on findings per drawing
      _LSZ010_MIN_SUFFIX_PARTS    -- minimum populated suffix parts to consider
    """
    out: list = []
    line_tags = extraction.get("line_tags", [])
    if not line_tags:
        return out

    # ── Build suffix_key -> list[line_tag] map ────────────────────────────
    suffix_groups: dict = {}
    for lt in line_tags:
        parts = [
            str(lt.get(f) or "").upper().strip()
            for f in _LSZ010_SUFFIX_FIELDS
        ]
        # Skip entries where too few suffix fields are populated.
        populated = sum(1 for p in parts if p)
        if populated < _LSZ010_MIN_SUFFIX_PARTS:
            continue
        # Use only populated parts in the key so partial entries don't dilute.
        suffix_key = "-".join(p for p in parts if p)
        suffix_groups.setdefault(suffix_key, []).append(lt)

    seen_groups: set = set()

    for suffix_key, entries in suffix_groups.items():
        if len(entries) < 2:
            continue

        # ── Build distinct (fluid_code, area_code) identity pairs ─────────
        identities = []
        for e in entries:
            idn = (
                str(e.get("fluid_code") or "").upper().strip(),
                str(e.get("area_code")  or "").upper().strip(),
            )
            if idn not in identities:
                identities.append(idn)

        # Must have at least two DIFFERENT identities to be a conflict.
        if len(identities) < 2:
            continue

        # ── Apply optional filters ─────────────────────────────────────────
        if _LSZ010_SAME_FLUID_ONLY:
            # Only flag when all conflicting tags share the same fluid code.
            fluids = {i[0] for i in identities if i[0]}
            if len(fluids) > 1:
                # Different fluid systems legitimately share sequence numbers.
                continue

        if _LSZ010_REQUIRE_SAME_SIZE:
            sizes = {str(e.get("size") or "").upper().strip() for e in entries if e.get("size")}
            if len(sizes) > 1:
                continue

        # ── Deduplication: same set of identities ─────────────────────────
        group_key = frozenset(f"{i[0]}-{i[1]}" for i in identities)
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)

        # ── Build human-readable display strings ──────────────────────────
        # Collect unique texts in insertion order, capped at 4 for readability.
        texts = list(dict.fromkeys(e.get("text", "") for e in entries if e.get("text")))

        areas_display  = ", ".join(sorted({i[1] for i in identities if i[1]}))
        fluids_display = ", ".join(sorted({i[0] for i in identities if i[0]}))

        # Build the human-readable suffix from the first entry's actual values.
        suffix_display = "-".join(
            str(entries[0].get(f) or "").strip()
            for f in _LSZ010_SUFFIX_FIELDS
            if entries[0].get(f)
        )

        evidence = "  ·  ".join(texts[:4])

        out.append(RuleFinding(
            category="line_size",
            rule_id="LSZ-010",
            issue_observed=(
                f"Pipeline suffix '{suffix_display}' shared across different area codes "
                f"({areas_display}) on the same drawing -- "
                "possible copy-paste error in line numbering"
            ),
            action_required=(
                "Verify that each pipeline has a unique sequence number within its "
                "area / fluid combination. If these are separate physical lines, "
                "assign distinct sequence numbers per the project line-numbering "
                "convention. If intentional (e.g. shared-service line), add a note "
                "or cross-reference to justify the identical suffix."
            ),
            evidence=evidence,
            severity="major",
        ))

        if len(out) >= _LSZ010_MAX_FINDINGS:
            break

    return out
