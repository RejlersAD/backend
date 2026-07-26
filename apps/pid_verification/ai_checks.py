"""
AI-Powered P&ID Check Executors
Implements AUTO, ASSIST, and HUMAN check categories.
"""
import re
from typing import Dict, List, Optional, Set, Tuple
from fuzzywuzzy import fuzz
import pandas as pd

from .ai_config import RECONCILIATION_CONFIG, PATTERN_RULES, CHECK_CATEGORIES


class AutoCheckExecutor:
    """Executes fully automated checks (AUTO category)."""
    
    def __init__(self):
        self.config = RECONCILIATION_CONFIG
    
    def _normalize_tag(self, tag: str) -> str:
        """Normalize tag for comparison (uppercase, strip spaces)."""
        if not tag:
            return ""
        return str(tag).strip().upper()
    
    def _fuzzy_match(self, text1: str, text2: str, threshold: float = 0.85) -> bool:
        """Check if two strings match with fuzzy matching."""
        if not text1 or not text2:
            return False
        score = fuzz.ratio(text1.lower(), text2.lower()) / 100.0
        return score >= threshold
    
    def check_line_list_reconciliation(
        self,
        pid_lines: List[Dict],
        line_list_data: List[Dict]
    ) -> Dict:
        """
        AUTO_001: Line List Two-Way Reconciliation
        
        Args:
            pid_lines: Lines extracted from P&IDs [{line_number, size, spec, insulation, sheet, ...}]
            line_list_data: Lines from uploaded line list [{Line Number, Size, Spec, Insulation, ...}]
            
        Returns:
            Check result with result, confidence, finding, details
        """
        config = self.config['line_list']
        
        # Build sets of line numbers
        pid_line_numbers = {
            self._normalize_tag(line.get('line_number', ''))
            for line in pid_lines if line.get('line_number')
        }
        
        # Handle different possible column names in line list
        line_list_key = None
        for possible_key in ['Line Number', 'line_number', 'LINE_NUMBER', 'Line_Number']:
            if possible_key in line_list_data[0] if line_list_data else {}:
                line_list_key = possible_key
                break
        
        if not line_list_key:
            return {
                'check_id': config['check_id'],
                'name': config['name'],
                'result': 'Error',
                'confidence': '',
                'finding': 'Line list data has no recognizable line number column',
                'severity': config['severity'],
                'details': {}
            }
        
        list_line_numbers = {
            self._normalize_tag(line.get(line_list_key, ''))
            for line in line_list_data if line.get(line_list_key)
        }
        
        # Two-way comparison
        orphans_in_pid = pid_line_numbers - list_line_numbers  # In P&ID but not in list
        missing_from_pid = list_line_numbers - pid_line_numbers  # In list but not in P&ID
        
        # Determine result
        orphan_count = len(orphans_in_pid)
        missing_count = len(missing_from_pid)
        threshold = config.get('orphan_threshold', 5)
        
        if orphan_count == 0 and missing_count == 0:
            result = 'Pass'
            finding = f"All {len(pid_line_numbers)} line numbers reconciled two-way vs line list"
        elif orphan_count <= threshold and missing_count <= threshold:
            result = 'Warning'
            finding = f"{orphan_count} orphan(s) in P&ID not in list; {missing_count} missing from P&ID (within threshold)"
        else:
            result = 'Fail'
            finding = f"{orphan_count} orphan(s) in P&ID not in list; {missing_count} missing from P&ID (exceeds threshold of {threshold})"
        
        return {
            'check_id': config['check_id'],
            'name': config['name'],
            'result': result,
            'confidence': 'High',
            'finding': finding,
            'severity': config['severity'],
            'details': {
                'pid_line_count': len(pid_line_numbers),
                'list_line_count': len(list_line_numbers),
                'orphans': list(orphans_in_pid)[:20],  # First 20 for display
                'missing': list(missing_from_pid)[:20],
                'orphan_count': orphan_count,
                'missing_count': missing_count,
            }
        }
    
    def check_equipment_list_reconciliation(
        self,
        pid_equipment: List[Dict],
        equipment_list_data: List[Dict]
    ) -> Dict:
        """
        AUTO_002: Equipment List Two-Way Reconciliation
        
        Args:
            pid_equipment: Equipment extracted from P&IDs [{tag, type, service, sheet, ...}]
            equipment_list_data: Equipment from uploaded list [{Equipment Tag, Service, Type, ...}]
            
        Returns:
            Check result with result, confidence, finding, details
        """
        config = self.config['equipment_list']
        
        # Build sets of equipment tags
        pid_equipment_tags = {
            self._normalize_tag(eq.get('tag', ''))
            for eq in pid_equipment if eq.get('tag')
        }
        
        # Handle different possible column names
        equipment_key = None
        for possible_key in ['Equipment Tag', 'equipment_tag', 'EQUIPMENT_TAG', 'Tag', 'tag']:
            if possible_key in equipment_list_data[0] if equipment_list_data else {}:
                equipment_key = possible_key
                break
        
        if not equipment_key:
            return {
                'check_id': config['check_id'],
                'name': config['name'],
                'result': 'Error',
                'confidence': '',
                'finding': 'Equipment list data has no recognizable tag column',
                'severity': config['severity'],
                'details': {}
            }
        
        list_equipment_tags = {
            self._normalize_tag(eq.get(equipment_key, ''))
            for eq in equipment_list_data if eq.get(equipment_key)
        }
        
        # Two-way comparison
        orphans_in_pid = pid_equipment_tags - list_equipment_tags
        missing_from_pid = list_equipment_tags - pid_equipment_tags
        
        # Determine result
        orphan_count = len(orphans_in_pid)
        missing_count = len(missing_from_pid)
        threshold = config.get('orphan_threshold', 3)
        
        if orphan_count == 0 and missing_count == 0:
            result = 'Pass'
            finding = f"All {len(pid_equipment_tags)} equipment tags reconciled two-way vs equipment list"
        elif orphan_count <= threshold and missing_count <= threshold:
            result = 'Warning'
            finding = f"{orphan_count} orphan(s) in P&ID not in list; {missing_count} missing from P&ID (within threshold)"
        else:
            result = 'Fail'
            finding = f"{orphan_count} orphan(s) in P&ID not in list; {missing_count} missing from P&ID (exceeds threshold of {threshold})"
        
        return {
            'check_id': config['check_id'],
            'name': config['name'],
            'result': result,
            'confidence': 'High',
            'finding': finding,
            'severity': config['severity'],
            'details': {
                'pid_equipment_count': len(pid_equipment_tags),
                'list_equipment_count': len(list_equipment_tags),
                'orphans': list(orphans_in_pid)[:20],
                'missing': list(missing_from_pid)[:20],
                'orphan_count': orphan_count,
                'missing_count': missing_count,
            }
        }
    
    def check_instrument_index_reconciliation(
        self,
        pid_instruments: List[Dict],
        instrument_index_data: List[Dict]
    ) -> Dict:
        """
        AUTO_003: Instrument Index Two-Way Reconciliation
        
        Args:
            pid_instruments: Instruments extracted from P&IDs [{tag, type, location, sheet, ...}]
            instrument_index_data: Instruments from uploaded index [{Instrument Tag, Type, Location, ...}]
            
        Returns:
            Check result with result, confidence, finding, details
        """
        config = self.config['instrument_index']
        
        # Build sets of instrument tags (with sub-tag support)
        pid_instrument_tags = {
            self._normalize_tag(inst.get('tag', ''))
            for inst in pid_instruments if inst.get('tag')
        }
        
        # Handle different possible column names
        instrument_key = None
        for possible_key in ['Instrument Tag', 'instrument_tag', 'INSTRUMENT_TAG', 'Tag', 'tag']:
            if possible_key in instrument_index_data[0] if instrument_index_data else {}:
                instrument_key = possible_key
                break
        
        if not instrument_key:
            return {
                'check_id': config['check_id'],
                'name': config['name'],
                'result': 'Error',
                'confidence': '',
                'finding': 'Instrument index data has no recognizable tag column',
                'severity': config['severity'],
                'details': {}
            }
        
        list_instrument_tags = {
            self._normalize_tag(inst.get(instrument_key, ''))
            for inst in instrument_index_data if inst.get(instrument_key)
        }
        
        # Support sub-tags (PI-001 matches PI-001-16)
        if config.get('allow_sub_tags', False):
            # Create mapping of base tags to full tags
            pid_base_tags = {}
            for tag in pid_instrument_tags:
                base_tag = re.sub(r'[-]\d+$', '', tag)  # Remove trailing -16
                if base_tag not in pid_base_tags:
                    pid_base_tags[base_tag] = []
                pid_base_tags[base_tag].append(tag)
            
            list_base_tags = {}
            for tag in list_instrument_tags:
                base_tag = re.sub(r'[-]\d+$', '', tag)
                if base_tag not in list_base_tags:
                    list_base_tags[base_tag] = []
                list_base_tags[base_tag].append(tag)
            
            # Compare at base tag level
            orphans_in_pid = set(pid_base_tags.keys()) - set(list_base_tags.keys())
            missing_from_pid = set(list_base_tags.keys()) - set(pid_base_tags.keys())
        else:
            # Exact tag comparison
            orphans_in_pid = pid_instrument_tags - list_instrument_tags
            missing_from_pid = list_instrument_tags - pid_instrument_tags
        
        # Determine result
        orphan_count = len(orphans_in_pid)
        missing_count = len(missing_from_pid)
        threshold = config.get('orphan_threshold', 5)
        
        if orphan_count == 0 and missing_count == 0:
            result = 'Pass'
            finding = f"All {len(pid_instrument_tags)} instrument tags reconciled two-way vs instrument index"
        elif orphan_count <= threshold and missing_count <= threshold:
            result = 'Warning'
            finding = f"{orphan_count} orphan(s) in P&ID not in index; {missing_count} missing from P&ID (within threshold)"
        else:
            result = 'Fail'
            finding = f"{orphan_count} orphan(s) in P&ID not in index; {missing_count} missing from P&ID (exceeds threshold of {threshold})"
        
        return {
            'check_id': config['check_id'],
            'name': config['name'],
            'result': result,
            'confidence': 'High',
            'finding': finding,
            'severity': config['severity'],
            'details': {
                'pid_instrument_count': len(pid_instrument_tags),
                'index_instrument_count': len(list_instrument_tags),
                'orphans': list(orphans_in_pid)[:20],
                'missing': list(missing_from_pid)[:20],
                'orphan_count': orphan_count,
                'missing_count': missing_count,
            }
        }
    
    def check_legend_reconciliation(
        self,
        pid_equipment: List[Dict],
        pid_instruments: List[Dict],
        legend_data: Dict
    ) -> Dict:
        """
        AUTO_004: Legend Symbol Verification
        
        Verify that all equipment and instrument prefixes found in P&ID match the project legend.
        
        Args:
            pid_equipment: Equipment extracted from P&IDs [{tag, type, ...}]
            pid_instruments: Instruments extracted from P&IDs [{tag, type, ...}]
            legend_data: Legend knowledge from project {instrument_prefixes, valve_prefixes, ...}
            
        Returns:
            Check result with result, confidence, finding, details
        """
        config = self.config.get('legend', {})
        
        if not legend_data:
            return {
                'check_id': config.get('check_id', 'AUTO_004'),
                'name': config.get('name', 'Legend Symbol Verification'),
                'result': 'Warning',
                'confidence': '',
                'finding': 'No legend data uploaded for this project',
                'severity': config.get('severity', 'critical'),
                'details': {}
            }
        
        # Extract prefixes from legend
        instrument_prefixes = set(legend_data.get('instrument_prefixes', []))
        valve_prefixes = set(legend_data.get('valve_prefixes', []))
        
        # Combine all known prefixes from legend
        known_prefixes = instrument_prefixes.union(valve_prefixes)
        
        # Extract prefixes from P&ID equipment and instruments
        pid_instrument_prefixes = set()
        for inst in pid_instruments:
            tag = inst.get('tag', '')
            if tag:
                # Extract prefix (e.g., "PI" from "PI-001", "FT" from "FT-3610")
                match = re.match(r'^([A-Z]{1,4})', self._normalize_tag(tag))
                if match:
                    pid_instrument_prefixes.add(match.group(1))
        
        pid_equipment_prefixes = set()
        for eq in pid_equipment:
            tag = eq.get('tag', '')
            eq_type = eq.get('type', '').upper()
            if tag:
                # Extract prefix (e.g., "V" from "V-001", "P" from "P-102A")
                match = re.match(r'^([A-Z]{1,2})', self._normalize_tag(tag))
                if match:
                    pid_equipment_prefixes.add(match.group(1))
        
        all_pid_prefixes = pid_instrument_prefixes.union(pid_equipment_prefixes)
        
        # Find unknown prefixes (in P&ID but not in legend)
        unknown_prefixes = all_pid_prefixes - known_prefixes
        
        # Find unused legend entries (in legend but not used in P&ID)
        unused_legend_prefixes = known_prefixes - all_pid_prefixes
        
        # Determine result
        unknown_count = len(unknown_prefixes)
        threshold = config.get('orphan_threshold', 0)
        
        if unknown_count == 0:
            result = 'Pass'
            finding = f"All {len(all_pid_prefixes)} P&ID symbol prefixes match project legend"
        elif unknown_count <= threshold:
            result = 'Warning'
            finding = f"{unknown_count} unknown prefix(es) found in P&ID: {', '.join(sorted(unknown_prefixes))}"
        else:
            result = 'Fail'
            finding = f"{unknown_count} unknown prefix(es) found in P&ID not in legend: {', '.join(sorted(unknown_prefixes))}"
        
        return {
            'check_id': config.get('check_id', 'AUTO_004'),
            'name': config.get('name', 'Legend Symbol Verification'),
            'result': result,
            'confidence': 'High',
            'finding': finding,
            'severity': config.get('severity', 'critical'),
            'details': {
                'pid_prefix_count': len(all_pid_prefixes),
                'legend_prefix_count': len(known_prefixes),
                'unknown_prefixes': list(unknown_prefixes),
                'unused_legend_prefixes': list(unused_legend_prefixes),
                'unknown_count': unknown_count,
                'instrument_prefixes_used': list(pid_instrument_prefixes),
                'equipment_prefixes_used': list(pid_equipment_prefixes),
            }
        }
    
    def run_all_checks(
        self,
        pid_extractions: Dict,
        reference_data: Dict
    ) -> List[Dict]:
        """
        Run all AUTO checks.
        
        Args:
            pid_extractions: All extracted data from P&IDs {equipment, lines, instruments, ...}
            reference_data: All uploaded reference data {line_list, equipment_list, instrument_index}
            
        Returns:
            List of check results
        """
        results = []
        
        # AUTO_001: Line List Reconciliation
        if reference_data.get('line_list'):
            results.append(
                self.check_line_list_reconciliation(
                    pid_extractions.get('lines', []),
                    reference_data['line_list']
                )
            )
        
        # AUTO_002: Equipment List Reconciliation
        if reference_data.get('equipment_list'):
            results.append(
                self.check_equipment_list_reconciliation(
                    pid_extractions.get('equipment', []),
                    reference_data['equipment_list']
                )
            )
        
        # AUTO_003: Instrument Index Reconciliation
        if reference_data.get('instrument_index'):
            results.append(
                self.check_instrument_index_reconciliation(
                    pid_extractions.get('instruments', []),
                    reference_data['instrument_index']
                )
            )
        
        # AUTO_004: Legend Symbol Verification
        if reference_data.get('legend_knowledge'):
            results.append(
                self.check_legend_reconciliation(
                    pid_extractions.get('equipment', []),
                    pid_extractions.get('instruments', []),
                    reference_data['legend_knowledge']
                )
            )
        
        return results
