"""
P&ID Verification V2 — Comparison Engine

Cross-document comparison service for identifying discrepancies between:
  1. P&ID and Legend sheets
  2. P&ID and Line List
  3. P&ID and Equipment Register
  4. P&ID and Instrument Index

Returns structured comparison results with discrepancy categories:
  - missing: Items in reference but not in P&ID
  - extra: Items in P&ID but not in reference
  - mismatch: Items present in both but with different attributes

All comparison logic is soft-coded for easy tuning.
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# SOFT-CODED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Fuzzy matching threshold (0.0-1.0). Items with similarity >= threshold are considered matches.
COMPARISON_MATCH_THRESHOLD = 0.85

# Attribute weight configuration for computing overall similarity
# Higher weight = attribute has more influence on match decision
ATTRIBUTE_WEIGHTS = {
    'line_list': {
        'tag': 1.0,           # Line tag is critical
        'size': 0.8,          # Size is important
        'fluid_code': 0.6,    # Fluid code matters
        'spec': 0.5,          # Material spec
        'from_to': 0.4,       # From-To location
    },
    'equipment': {
        'tag': 1.0,           # Equipment tag is critical
        'type': 0.7,          # Equipment type
        'description': 0.5,   # Description
        'service': 0.6,       # Service/duty
    },
    'instrument': {
        'tag': 1.0,           # Instrument tag is critical
        'type': 0.8,          # Instrument type (PT, FT, etc.)
        'service': 0.6,       # Service description
        'range': 0.4,         # Operating range
    },
}

# Legend symbol matching — exact match required (no fuzzy matching for symbols)
LEGEND_EXACT_MATCH = True


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ComparisonFinding:
    """Single discrepancy found during comparison"""
    category: str           # 'missing', 'extra', 'mismatch'
    comparison_type: str    # 'legend', 'linelist', 'equipment', 'instrument'
    item_id: str           # Identifier (tag, symbol name, etc.)
    severity: str          # 'critical', 'major', 'minor'
    issue_observed: str    # Human-readable description
    pid_value: Any         # Value found in P&ID (None if missing)
    ref_value: Any         # Value found in reference doc (None if extra)
    similarity: float      # Match score 0.0-1.0 (1.0 = exact match)
    evidence: str          # Supporting data for review


@dataclass
class ComparisonResult:
    """Complete comparison results for one comparison type"""
    comparison_type: str        # 'legend', 'linelist', 'equipment', 'instrument'
    total_pid_items: int       # Items extracted from P&ID
    total_ref_items: int       # Items in reference document
    matched_count: int         # Items that matched successfully
    missing_count: int         # Items in ref but not in P&ID
    extra_count: int           # Items in P&ID but not in ref
    mismatch_count: int        # Items with attribute differences
    findings: List[ComparisonFinding]
    summary: str               # One-line summary


# ═══════════════════════════════════════════════════════════════════════════
# FUZZY MATCHING UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def fuzzy_match(str1: str, str2: str, threshold: float = COMPARISON_MATCH_THRESHOLD) -> float:
    """
    Compute fuzzy similarity between two strings.
    
    Returns:
        Float 0.0-1.0 where 1.0 = exact match
    """
    if not str1 or not str2:
        return 0.0
    
    str1_clean = str1.strip().lower()
    str2_clean = str2.strip().lower()
    
    if str1_clean == str2_clean:
        return 1.0
    
    # Use SequenceMatcher for fuzzy comparison
    return SequenceMatcher(None, str1_clean, str2_clean).ratio()


def weighted_similarity(pid_item: Dict, ref_item: Dict, weights: Dict[str, float]) -> float:
    """
    Compute weighted similarity between P&ID item and reference item.
    
    Args:
        pid_item: Item extracted from P&ID
        ref_item: Item from reference document
        weights: Attribute weights configuration
    
    Returns:
        Overall similarity score 0.0-1.0
    """
    total_weight = 0.0
    weighted_sum = 0.0
    
    for attr, weight in weights.items():
        pid_val = str(pid_item.get(attr, '')).strip()
        ref_val = str(ref_item.get(attr, '')).strip()
        
        if pid_val or ref_val:
            similarity = fuzzy_match(pid_val, ref_val)
            weighted_sum += similarity * weight
            total_weight += weight
    
    if total_weight == 0:
        return 0.0
    
    return weighted_sum / total_weight


# ═══════════════════════════════════════════════════════════════════════════
# COMPARISON FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def compare_with_legend(
    pid_symbols: List[Dict[str, Any]],
    legend_data: Optional[Dict[str, Any]]
) -> ComparisonResult:
    """
    Compare P&ID extracted symbols with Legend sheet.
    
    Args:
        pid_symbols: Symbols/tags extracted from P&ID
        legend_data: Legend knowledge data (symbol definitions)
    
    Returns:
        ComparisonResult with legend-specific findings
    """
    findings = []
    
    # Handle case where legend is not available
    if not legend_data:
        return ComparisonResult(
            comparison_type='legend',
            total_pid_items=len(pid_symbols),
            total_ref_items=0,
            matched_count=0,
            missing_count=0,
            extra_count=0,
            mismatch_count=0,
            findings=[],
            summary='Legend sheet not available for comparison'
        )
    
    # Extract legend symbols from legend_data
    legend_symbols = legend_data.get('symbols', [])
    
    # Convert to sets for comparison
    pid_symbol_set = {s.get('symbol_type', '').strip().upper() for s in pid_symbols if s.get('symbol_type')}
    legend_symbol_set = {s.get('type', '').strip().upper() for s in legend_symbols if s.get('type')}
    
    matched = pid_symbol_set & legend_symbol_set
    missing = legend_symbol_set - pid_symbol_set
    extra = pid_symbol_set - legend_symbol_set
    
    # Generate findings for missing symbols
    for symbol in missing:
        findings.append(ComparisonFinding(
            category='missing',
            comparison_type='legend',
            item_id=symbol,
            severity='major',
            issue_observed=f'Symbol "{symbol}" is defined in Legend but not found on P&ID',
            pid_value=None,
            ref_value=symbol,
            similarity=0.0,
            evidence=f'Legend defines {symbol} but P&ID does not use it'
        ))
    
    # Generate findings for extra symbols (not in legend)
    for symbol in extra:
        findings.append(ComparisonFinding(
            category='extra',
            comparison_type='legend',
            item_id=symbol,
            severity='critical',
            issue_observed=f'Symbol "{symbol}" found on P&ID but not defined in Legend sheet',
            pid_value=symbol,
            ref_value=None,
            similarity=0.0,
            evidence=f'P&ID uses {symbol} which is not in approved Legend'
        ))
    
    return ComparisonResult(
        comparison_type='legend',
        total_pid_items=len(pid_symbol_set),
        total_ref_items=len(legend_symbol_set),
        matched_count=len(matched),
        missing_count=len(missing),
        extra_count=len(extra),
        mismatch_count=0,
        findings=findings,
        summary=f'{len(extra)} unapproved symbols, {len(missing)} legend symbols unused'
    )


def compare_with_line_list(
    pid_lines: List[Dict[str, Any]],
    line_list_data: List[Dict[str, Any]]
) -> ComparisonResult:
    """
    Compare P&ID pipeline designations with Line List register.
    
    Args:
        pid_lines: Line tags extracted from P&ID
        line_list_data: Line list from database/Excel
    
    Returns:
        ComparisonResult with line list comparison findings
    """
    findings = []
    matched_count = 0
    mismatch_count = 0
    
    # Build lookup dictionaries
    pid_line_dict = {line.get('text', ''): line for line in pid_lines if line.get('text')}
    ref_line_dict = {line.get('line_tag', ''): line for line in line_list_data if line.get('line_tag')}
    
    pid_tags = set(pid_line_dict.keys())
    ref_tags = set(ref_line_dict.keys())
    
    # Find exact matches
    exact_matches = pid_tags & ref_tags
    
    # Check for attribute mismatches in exact matches
    for tag in exact_matches:
        pid_line = pid_line_dict[tag]
        ref_line = ref_line_dict[tag]
        
        # Compare size
        pid_size = pid_line.get('size', '')
        ref_size = ref_line.get('size', '')
        
        if pid_size and ref_size and pid_size != ref_size:
            mismatch_count += 1
            findings.append(ComparisonFinding(
                category='mismatch',
                comparison_type='linelist',
                item_id=tag,
                severity='major',
                issue_observed=f'Line {tag}: Size mismatch between P&ID ({pid_size}) and Line List ({ref_size})',
                pid_value=pid_size,
                ref_value=ref_size,
                similarity=fuzzy_match(pid_size, ref_size),
                evidence=f'P&ID shows {pid_size}, Line List shows {ref_size}'
            ))
        else:
            matched_count += 1
    
    # Find missing lines (in Line List but not on P&ID)
    missing = ref_tags - pid_tags
    for tag in missing:
        ref_line = ref_line_dict[tag]
        findings.append(ComparisonFinding(
            category='missing',
            comparison_type='linelist',
            item_id=tag,
            severity='major',
            issue_observed=f'Line {tag} is registered in Line List but not found on P&ID',
            pid_value=None,
            ref_value=ref_line,
            similarity=0.0,
            evidence=f'Line List entry: {tag} ({ref_line.get("size", "")}) - {ref_line.get("service", "")}'
        ))
    
    # Find extra lines (on P&ID but not in Line List)
    extra = pid_tags - ref_tags
    for tag in extra:
        pid_line = pid_line_dict[tag]
        findings.append(ComparisonFinding(
            category='extra',
            comparison_type='linelist',
            item_id=tag,
            severity='critical',
            issue_observed=f'Line {tag} found on P&ID but not registered in Line List',
            pid_value=pid_line,
            ref_value=None,
            similarity=0.0,
            evidence=f'P&ID shows {tag} but Line List has no entry'
        ))
    
    return ComparisonResult(
        comparison_type='linelist',
        total_pid_items=len(pid_tags),
        total_ref_items=len(ref_tags),
        matched_count=matched_count,
        missing_count=len(missing),
        extra_count=len(extra),
        mismatch_count=mismatch_count,
        findings=findings,
        summary=f'{len(extra)} unregistered lines, {len(missing)} lines not on P&ID, {mismatch_count} mismatches'
    )


def compare_with_equipment_list(
    pid_equipment: List[Dict[str, Any]],
    equipment_list_data: List[Dict[str, Any]]
) -> ComparisonResult:
    """
    Compare P&ID equipment tags with Equipment Register.
    
    Args:
        pid_equipment: Equipment/tags extracted from P&ID
        equipment_list_data: Equipment register from database
    
    Returns:
        ComparisonResult with equipment comparison findings
    """
    findings = []
    matched_count = 0
    mismatch_count = 0
    
    # Build lookup dictionaries
    pid_equip_dict = {eq.get('tag', ''): eq for eq in pid_equipment if eq.get('tag')}
    ref_equip_dict = {eq.get('tag', ''): eq for eq in equipment_list_data if eq.get('tag')}
    
    pid_tags = set(pid_equip_dict.keys())
    ref_tags = set(ref_equip_dict.keys())
    
    # Find exact matches
    exact_matches = pid_tags & ref_tags
    
    # Check for attribute mismatches
    for tag in exact_matches:
        pid_eq = pid_equip_dict[tag]
        ref_eq = ref_equip_dict[tag]
        
        # Compare type/description
        pid_type = pid_eq.get('type', '')
        ref_type = ref_eq.get('type', '')
        
        if pid_type and ref_type:
            similarity = fuzzy_match(pid_type, ref_type)
            if similarity < COMPARISON_MATCH_THRESHOLD:
                mismatch_count += 1
                findings.append(ComparisonFinding(
                    category='mismatch',
                    comparison_type='equipment',
                    item_id=tag,
                    severity='major',
                    issue_observed=f'Equipment {tag}: Type mismatch between P&ID and Equipment List',
                    pid_value=pid_type,
                    ref_value=ref_type,
                    similarity=similarity,
                    evidence=f'P&ID: {pid_type}, Equipment List: {ref_type}'
                ))
            else:
                matched_count += 1
        else:
            matched_count += 1
    
    # Find missing equipment
    missing = ref_tags - pid_tags
    for tag in missing:
        ref_eq = ref_equip_dict[tag]
        findings.append(ComparisonFinding(
            category='missing',
            comparison_type='equipment',
            item_id=tag,
            severity='major',
            issue_observed=f'Equipment {tag} is in Equipment Register but not shown on P&ID',
            pid_value=None,
            ref_value=ref_eq,
            similarity=0.0,
            evidence=f'Equipment List: {tag} - {ref_eq.get("description", "")}'
        ))
    
    # Find extra equipment
    extra = pid_tags - ref_tags
    for tag in extra:
        pid_eq = pid_equip_dict[tag]
        findings.append(ComparisonFinding(
            category='extra',
            comparison_type='equipment',
            item_id=tag,
            severity='critical',
            issue_observed=f'Equipment {tag} found on P&ID but not in Equipment Register',
            pid_value=pid_eq,
            ref_value=None,
            similarity=0.0,
            evidence=f'P&ID shows {tag} ({pid_eq.get("type", "")}) but not registered'
        ))
    
    return ComparisonResult(
        comparison_type='equipment',
        total_pid_items=len(pid_tags),
        total_ref_items=len(ref_tags),
        matched_count=matched_count,
        missing_count=len(missing),
        extra_count=len(extra),
        mismatch_count=mismatch_count,
        findings=findings,
        summary=f'{len(extra)} unregistered equipment, {len(missing)} not on P&ID, {mismatch_count} mismatches'
    )


def compare_with_instrument_index(
    pid_instruments: List[Dict[str, Any]],
    instrument_index_data: List[Dict[str, Any]]
) -> ComparisonResult:
    """
    Compare P&ID instrument tags with Instrument Index.
    
    Args:
        pid_instruments: Instruments extracted from P&ID
        instrument_index_data: Instrument index from database
    
    Returns:
        ComparisonResult with instrument comparison findings
    """
    findings = []
    matched_count = 0
    mismatch_count = 0
    
    # Build lookup dictionaries
    pid_instr_dict = {ins.get('tag', ''): ins for ins in pid_instruments if ins.get('tag')}
    ref_instr_dict = {ins.get('tag', ''): ins for ins in instrument_index_data if ins.get('tag')}
    
    pid_tags = set(pid_instr_dict.keys())
    ref_tags = set(ref_instr_dict.keys())
    
    # Find exact matches
    exact_matches = pid_tags & ref_tags
    
    # Check for attribute mismatches
    for tag in exact_matches:
        pid_ins = pid_instr_dict[tag]
        ref_ins = ref_instr_dict[tag]
        
        # Compare instrument type
        pid_type = pid_ins.get('type', '')
        ref_type = ref_ins.get('type', '')
        
        if pid_type and ref_type:
            similarity = fuzzy_match(pid_type, ref_type)
            if similarity < COMPARISON_MATCH_THRESHOLD:
                mismatch_count += 1
                findings.append(ComparisonFinding(
                    category='mismatch',
                    comparison_type='instrument',
                    item_id=tag,
                    severity='major',
                    issue_observed=f'Instrument {tag}: Type mismatch between P&ID and Instrument Index',
                    pid_value=pid_type,
                    ref_value=ref_type,
                    similarity=similarity,
                    evidence=f'P&ID: {pid_type}, Index: {ref_type}'
                ))
            else:
                matched_count += 1
        else:
            matched_count += 1
    
    # Find missing instruments
    missing = ref_tags - pid_tags
    for tag in missing:
        ref_ins = ref_instr_dict[tag]
        findings.append(ComparisonFinding(
            category='missing',
            comparison_type='instrument',
            item_id=tag,
            severity='major',
            issue_observed=f'Instrument {tag} is in Instrument Index but not shown on P&ID',
            pid_value=None,
            ref_value=ref_ins,
            similarity=0.0,
            evidence=f'Index: {tag} - {ref_ins.get("service", "")}'
        ))
    
    # Find extra instruments
    extra = pid_tags - ref_tags
    for tag in extra:
        pid_ins = pid_instr_dict[tag]
        findings.append(ComparisonFinding(
            category='extra',
            comparison_type='instrument',
            item_id=tag,
            severity='critical',
            issue_observed=f'Instrument {tag} found on P&ID but not in Instrument Index',
            pid_value=pid_ins,
            ref_value=None,
            similarity=0.0,
            evidence=f'P&ID shows {tag} ({pid_ins.get("type", "")}) but not in Index'
        ))
    
    return ComparisonResult(
        comparison_type='instrument',
        total_pid_items=len(pid_tags),
        total_ref_items=len(ref_tags),
        matched_count=matched_count,
        missing_count=len(missing),
        extra_count=len(extra),
        mismatch_count=mismatch_count,
        findings=findings,
        summary=f'{len(extra)} unregistered instruments, {len(missing)} not on P&ID, {mismatch_count} mismatches'
    )


# ═══════════════════════════════════════════════════════════════════════════
# MAIN COMPARISON ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def run_all_comparisons(
    extraction: Dict[str, Any],
    legend_data: Optional[Dict[str, Any]] = None,
    line_list_data: Optional[List[Dict[str, Any]]] = None,
    equipment_list_data: Optional[List[Dict[str, Any]]] = None,
    instrument_index_data: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, ComparisonResult]:
    """
    Run all 4 comparison types and return consolidated results.
    
    Args:
        extraction: P&ID extraction result (tags, instruments, lines, etc.)
        legend_data: Legend knowledge data
        line_list_data: Line list reference data
        equipment_list_data: Equipment register data
        instrument_index_data: Instrument index data
    
    Returns:
        Dictionary with comparison results for each type:
        {
            'legend': ComparisonResult,
            'linelist': ComparisonResult,
            'equipment': ComparisonResult,
            'instrument': ComparisonResult
        }
    """
    results = {}
    
    # Extract P&ID elements
    pid_symbols = extraction.get('symbols', [])
    pid_lines = extraction.get('line_tags', [])
    pid_equipment = extraction.get('equipment', [])
    pid_instruments = extraction.get('instruments', [])
    
    # Run each comparison
    logger.info('[ComparisonEngine] Running legend comparison...')
    results['legend'] = compare_with_legend(pid_symbols, legend_data)
    
    logger.info('[ComparisonEngine] Running line list comparison...')
    results['linelist'] = compare_with_line_list(pid_lines, line_list_data or [])
    
    logger.info('[ComparisonEngine] Running equipment comparison...')
    results['equipment'] = compare_with_equipment_list(pid_equipment, equipment_list_data or [])
    
    logger.info('[ComparisonEngine] Running instrument comparison...')
    results['instrument'] = compare_with_instrument_index(pid_instruments, instrument_index_data or [])
    
    # Log summary
    total_findings = sum(len(r.findings) for r in results.values())
    logger.info(
        '[ComparisonEngine] Comparison complete: %d total discrepancies found across 4 comparison types',
        total_findings
    )
    
    return results
