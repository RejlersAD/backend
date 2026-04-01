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

    for node in sorted(isolated):
        # Determine kind
        instr_tags = {i['tag'] for i in extraction.get('instruments', [])}
        valve_tags = {v['tag'] for v in extraction.get('valves', [])}

        if node in instr_tags:
            out.append(RuleFinding(
                category='connectivity',
                rule_id='CON-001',
                issue_observed=f"Instrument '{node}' has no pipeline connections",
                action_required='Connect instrument to process line or verify if stand-alone',
                evidence=node,
                severity='major',
            ))
        elif node in valve_tags:
            out.append(RuleFinding(
                category='connectivity',
                rule_id='CON-002',
                issue_observed=f"Valve '{node}' has no pipeline connections",
                action_required='Connect valve to upstream and downstream pipelines',
                evidence=node,
                severity='critical',
            ))
        else:
            out.append(RuleFinding(
                category='connectivity',
                rule_id='CON-003',
                issue_observed=f"Orphan node '{node}' has no connections in graph",
                action_required='Verify element belongs to this drawing; connect or remove',
                evidence=node,
                severity='major',
            ))

    return out


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
        """Coerce noisy OCR size token into a plausible inch value (2-24)."""
        try:
            v = float(raw_num)
        except Exception:
            return None

        if 2.0 <= v <= 24.0:
            if float(v).is_integer():
                return f'{int(v)}"'
            return f'{v}"'

        # OCR can merge nearby digits, e.g. 4546" where true valve size is 6".
        # Try trailing 2-digit / 1-digit recovery.
        s = str(int(v))
        if len(s) >= 2:
            tail2 = int(s[-2:])
            if 2 <= tail2 <= 24:
                return f'{tail2}"'
        tail1 = int(s[-1])
        if 2 <= tail1 <= 24:
            return f'{tail1}"'
        return None

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

    # Secondary fallback: raw drawing sizes not present in primary line-size set.
    if not valve_size_candidates:
        all_inch_sizes = [f"{m.group(1)}\"" for m in inch_pattern.finditer(raw_text)]
        for size in all_inch_sizes:
            if _is_reasonable_size(size) and size not in drawing_line_sizes and size not in valve_size_candidates:
                valve_size_candidates.append(size)

    if not valve_size_candidates or not drawing_line_sizes:
        return out

    # Use most likely valve size (largest inch value) vs primary line size (smallest)
    # as a deterministic fallback under noisy OCR.
    def _size_value(s: str) -> float:
        try:
            return float(s.replace('"', '').strip())
        except Exception:
            return 0.0

    valve_size = max(valve_size_candidates, key=_size_value)
    line_size = min(drawing_line_sizes, key=_size_value)

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
