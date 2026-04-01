"""
PFD Quality Rule Engine
========================
12 deterministic rules applied to the extracted drawing data.
Returns a list of RuleFinding dataclasses.
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class RuleFinding:
    category:        str   # matches PFDQFinding.Category choices
    rule_id:         str
    issue_observed:  str
    action_required: str
    evidence:        str
    direction:       str   # 'UP' | 'DOWN' | ''
    severity:        str   # matches PFDQFinding.Severity choices


# ---------------------------------------------------------------------------
# Tag format validator (EQP-001)
# ---------------------------------------------------------------------------
_RE_VALID_TAG = re.compile(r'^[VEPKTRFC]-\d{3,4}[A-Z]?$')


def run_rules(extraction: Dict[str, Any]) -> List[RuleFinding]:
    """
    Apply all 12 PFD rules to the extracted data dict and return findings.
    """
    findings: List[RuleFinding] = []

    eq_tags    = extraction.get('equipment_tags',  [])
    streams    = extraction.get('stream_numbers',  [])
    ctrl_valves = extraction.get('control_valves', [])
    relief     = extraction.get('relief_devices',  [])
    vessels_hx = extraction.get('vessels_hx',      [])
    title_blk  = extraction.get('title_block',     {})
    holds      = extraction.get('holds',            [])
    utilities  = extraction.get('utility_headers',  [])

    findings += _rule_eqp001(eq_tags)
    findings += _rule_eqp002(eq_tags)
    findings += _rule_eqp003(eq_tags)
    findings += _rule_str001(streams)
    findings += _rule_str002(streams)
    findings += _rule_str003(streams)
    findings += _rule_ctl001(ctrl_valves)
    findings += _rule_ttl001(title_blk)
    findings += _rule_ttl002(title_blk)
    findings += _rule_sft001(vessels_hx, relief)
    findings += _rule_nts001(holds)
    findings += _rule_utl001(utilities)

    return findings


# ---------------------------------------------------------------------------
# Equipment rules
# ---------------------------------------------------------------------------

def _rule_eqp001(tags: list) -> List[RuleFinding]:
    """EQP-001: Equipment tag format invalid (not V/E/P/K/T/R/C/F-NNN)."""
    bad = [t for t in tags if not _RE_VALID_TAG.match(t)]
    if not bad:
        return []
    return [RuleFinding(
        category        = 'equipment',
        rule_id         = 'EQP-001',
        issue_observed  = f'Invalid equipment tag format detected: {", ".join(bad[:5])}',
        action_required = (
            'Rename tags to follow convention <TYPE>-<NNN> '
            '(e.g. V-101, E-201, P-301).'
        ),
        evidence        = ', '.join(bad),
        direction       = 'UP',
        severity        = 'major',
    )]


def _rule_eqp002(tags: list) -> List[RuleFinding]:
    """EQP-002: Duplicate equipment tag."""
    seen, dupes = set(), set()
    for t in tags:
        key = t.upper()
        if key in seen:
            dupes.add(t)
        seen.add(key)
    if not dupes:
        return []
    return [RuleFinding(
        category        = 'equipment',
        rule_id         = 'EQP-002',
        issue_observed  = f'Duplicate equipment tags found: {", ".join(sorted(dupes))}',
        action_required = 'Assign unique tag numbers to each equipment item.',
        evidence        = ', '.join(sorted(dupes)),
        direction       = 'UP',
        severity        = 'critical',
    )]


def _rule_eqp003(tags: list) -> List[RuleFinding]:
    """EQP-003: No equipment tags found on drawing."""
    if tags:
        return []
    return [RuleFinding(
        category        = 'equipment',
        rule_id         = 'EQP-003',
        issue_observed  = 'No equipment tags detected on this drawing.',
        action_required = (
            'Verify drawing contains equipment and that tags conform to '
            'the <TYPE>-<NNN> format for OCR recognition.'
        ),
        evidence        = 'No tags matched V/E/P/K/T/R/C/F-NNN pattern.',
        direction       = 'DOWN',
        severity        = 'major',
    )]


# ---------------------------------------------------------------------------
# Stream rules
# ---------------------------------------------------------------------------

def _rule_str001(streams: list) -> List[RuleFinding]:
    """STR-001: No stream numbers found."""
    if streams:
        return []
    return [RuleFinding(
        category        = 'stream',
        rule_id         = 'STR-001',
        issue_observed  = 'No stream numbers identified on the drawing.',
        action_required = (
            'Add stream identification numbers to all process streams on the PFD.'
        ),
        evidence        = 'No numeric stream identifiers detected.',
        direction       = 'DOWN',
        severity        = 'major',
    )]


def _rule_str002(streams: list) -> List[RuleFinding]:
    """STR-002: Duplicate stream number."""
    seen, dupes = set(), set()
    for s in streams:
        if s in seen:
            dupes.add(s)
        seen.add(s)
    if not dupes:
        return []
    return [RuleFinding(
        category        = 'stream',
        rule_id         = 'STR-002',
        issue_observed  = f'Duplicate stream numbers detected: {", ".join(str(s) for s in sorted(dupes))}',
        action_required = 'Each stream must have a unique number. Renumber duplicate streams.',
        evidence        = ', '.join(str(s) for s in sorted(dupes)),
        direction       = 'UP',
        severity        = 'critical',
    )]


def _rule_str003(streams: list) -> List[RuleFinding]:
    """STR-003: Non-sequential gap in stream numbers (minor advisory)."""
    if len(streams) < 2:
        return []
    sorted_s = sorted(set(streams))
    gaps = []
    for i in range(1, len(sorted_s)):
        if sorted_s[i] - sorted_s[i-1] > 10:
            gaps.append(f'{sorted_s[i-1]}→{sorted_s[i]}')
    if not gaps:
        return []
    return [RuleFinding(
        category        = 'stream',
        rule_id         = 'STR-003',
        issue_observed  = f'Large gap(s) in stream number sequence: {", ".join(gaps)}',
        action_required = 'Review stream numbering for missing streams or numbering errors.',
        evidence        = ', '.join(gaps),
        direction       = '',
        severity        = 'minor',
    )]


# ---------------------------------------------------------------------------
# Control rules
# ---------------------------------------------------------------------------

def _rule_ctl001(ctrl_valves: list) -> List[RuleFinding]:
    """CTL-001: No control valves detected (advisory)."""
    if ctrl_valves:
        return []
    return [RuleFinding(
        category        = 'control',
        rule_id         = 'CTL-001',
        issue_observed  = 'No control valves (FCV/PCV/LCV/TCV) detected on the drawing.',
        action_required = (
            'Verify whether the absence of control valves is intentional. '
            'Ensure valve tags conform to FCV/PCV/LCV/TCV-NNN convention.'
        ),
        evidence        = 'No control valve tags matched known patterns.',
        direction       = '',
        severity        = 'minor',
    )]


# ---------------------------------------------------------------------------
# Title block rules
# ---------------------------------------------------------------------------

def _rule_ttl001(title_blk: dict) -> List[RuleFinding]:
    """TTL-001: Drawing number missing from title block."""
    if title_blk.get('drawing_number'):
        return []
    return [RuleFinding(
        category        = 'title_block',
        rule_id         = 'TTL-001',
        issue_observed  = 'Drawing number not found in the title block.',
        action_required = 'Add a drawing number with prefix DWG NO / DWG# to the title block.',
        evidence        = 'No DWG NO pattern detected in text.',
        direction       = 'UP',
        severity        = 'major',
    )]


def _rule_ttl002(title_blk: dict) -> List[RuleFinding]:
    """TTL-002: Revision indicator missing."""
    if title_blk.get('revision'):
        return []
    return [RuleFinding(
        category        = 'title_block',
        rule_id         = 'TTL-002',
        issue_observed  = 'Revision indicator not found in the title block.',
        action_required = 'Add REV / REVISION indicator (e.g. REV A, REV 0) to the title block.',
        evidence        = 'No REV pattern detected in text.',
        direction       = 'UP',
        severity        = 'major',
    )]


# ---------------------------------------------------------------------------
# Safety rules
# ---------------------------------------------------------------------------

def _rule_sft001(vessels_hx: list, relief_devices: list) -> List[RuleFinding]:
    """
    SFT-001: Pressurised vessel or heat exchanger present without a
    corresponding relief device (PSV/PRV/SRV).
    """
    if not vessels_hx:
        return []
    if relief_devices:
        return []
    return [RuleFinding(
        category        = 'safety',
        rule_id         = 'SFT-001',
        issue_observed  = (
            f'Vessel/HX ({", ".join(vessels_hx[:5])}) present without an identified '
            'relief device (PSV/PRV/SRV/BDV/TSV).'
        ),
        action_required = (
            'Confirm relief protection is shown. Tag relief devices per PSV/PRV-NNN convention.'
        ),
        evidence        = f'Vessels/HX: {", ".join(vessels_hx)} | Relief found: none',
        direction       = 'UP',
        severity        = 'critical',
    )]


# ---------------------------------------------------------------------------
# Notes rules
# ---------------------------------------------------------------------------

def _rule_nts001(holds: list) -> List[RuleFinding]:
    """NTS-001: HOLD item detected — engineer action required."""
    if not holds:
        return []
    return [RuleFinding(
        category        = 'notes',
        rule_id         = 'NTS-001',
        issue_observed  = f'Open HOLD item(s) detected: {", ".join(holds[:5])}',
        action_required = 'Resolve all HOLD items before IFC (Issued For Construction).',
        evidence        = ', '.join(holds),
        direction       = 'UP',
        severity        = 'major',
    )]


# ---------------------------------------------------------------------------
# Utility rules
# ---------------------------------------------------------------------------

def _rule_utl001(utilities: list) -> List[RuleFinding]:
    """UTL-001: Utility connection without a recognised designation label."""
    # Flag if we found generic keywords but they have no associated tag number
    unlabelled = [u for u in utilities if not re.search(r'\d', u)]
    if not unlabelled:
        return []
    return [RuleFinding(
        category        = 'utility',
        rule_id         = 'UTL-001',
        issue_observed  = f'Utility connection(s) without a specific designation: {", ".join(unlabelled[:5])}',
        action_required = (
            'Label each utility connection with type + specification '
            '(e.g. CW-10, LP STEAM-25, IA-100).'
        ),
        evidence        = ', '.join(unlabelled),
        direction       = '',
        severity        = 'minor',
    )]
