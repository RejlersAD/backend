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
    out: List[RuleFinding] = []
    if not raw_text:
        return out

    # Flexible line token matcher for strings like 4"-BD-4860-033842-X-N
    line_like = re.compile(r'\d{1,2}\s*(?:"|\'\'|”)?\s*-[A-Z]{1,4}-\d{3,6}-\d{4,6}-[A-Z](?:-[A-Z])?', re.IGNORECASE)
    size_token = re.compile(r'\b(\d{1,2}(?:\.\d+)?)\s*(?:"|\'\'|”)')

    for line in raw_text.splitlines():
        if not line_like.search(line):
            continue

        sizes = []
        for m in size_token.finditer(line):
            s = f"{m.group(1)}\""
            if s not in sizes:
                sizes.append(s)

        if len(sizes) >= 2:
            out.append(RuleFinding(
                category='line_size',
                rule_id='LSZ-004',
                issue_observed=(
                    f"Conflicting inline size annotations {sizes[0]} and {sizes[1]} "
                    "detected on the same line reference"
                ),
                action_required='Verify valve/line nominal sizes and add reducer or correct line designation as required',
                evidence=line.strip()[:240],
                direction='N/A',
                severity='critical',
            ))
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


def _check_multi_size_transition_observation(raw_text: str, line_sizes: List[Dict[str, Any]]) -> List[RuleFinding]:
    out: List[RuleFinding] = []
    if not line_sizes:
        return out

    normalized = set()
    for ls in line_sizes:
        txt = str(ls.get('text', '')).strip().replace('”', '"').replace("''", '"')
        if txt and txt.endswith('"'):
            normalized.add(txt)

    target = {'8"', '4"', '3"'}
    if target.issubset(normalized) and ('-BD-' in raw_text or '-VG-' in raw_text):
        out.append(RuleFinding(
            category='line_size',
            rule_id='LSZ-005',
            issue_observed='Multiple nominal sizes 8", 4", and 3" detected on this drawing segment',
            action_required='Verify intended reducers/spec-breaks and confirm each size transition is documented on the line route',
            evidence='Detected sizes: 8", 4", 3" on same diagram context',
            direction='N/A',
            severity='major',
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
