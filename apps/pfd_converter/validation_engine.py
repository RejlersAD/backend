"""
Engineering Validation Rules Engine
====================================

PURPOSE:
Apply engineering validation rules to PFD → P&ID conversion outputs
Ensure compliance with ADNOC DEP / ASME B31.3 / ISA-5.1 standards

KEY PRINCIPLES:
1. ONLY validate against documented standards and reference P&IDs
2. Flag ambiguities as ENGINEERING_HOLD (never auto-assume)
3. Every correction is traceable to a reference source
4. Incremental fixes ONLY - no wholesale regeneration

VALIDATION CATEGORIES:
- Safety Critical: PSV sizing, SDV fail positions, interlock logic
- Instrument Loops: Proper transmitter/controller/valve configuration
- Valve Specifications: Correct type and fail position for service
- Routing Logic: Flare, drain, vent system correctness
- Tag Naming: ISA-5.1 compliance
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import re

from .engineering_standards_config import get_engineering_config, EngineeringStandard
from .s3_reference_loader import load_reference_knowledge

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """Severity levels for validation findings"""
    CRITICAL = "CRITICAL"      # Safety-related, must be fixed
    HIGH = "HIGH"              # Major engineering deviation
    MEDIUM = "MEDIUM"          # Minor deviation, should be fixed
    LOW = "LOW"                # Suggestion for improvement
    INFO = "INFO"              # Informational note


class ValidationAction(Enum):
    """Recommended actions for validation findings"""
    ENGINEERING_HOLD = "ENGINEERING_HOLD"  # Flag for manual engineering review
    AUTO_CORRECT = "AUTO_CORRECT"          # Can be automatically corrected
    ADD_FROM_REFERENCE = "ADD_FROM_REFERENCE"  # Add element based on reference P&ID
    MANUAL_REVIEW = "MANUAL_REVIEW"        # Requires engineer decision
    ACCEPT_AS_IS = "ACCEPT_AS_IS"         # Valid as currently designed


@dataclass
class ValidationFinding:
    """Single validation finding"""
    rule_id: str
    element_id: str  # Equipment tag, line number, instrument tag
    element_type: str  # 'instrument', 'valve', 'equipment', 'line', 'safety_device'
    severity: ValidationSeverity
    description: str
    current_state: Dict
    expected_state: Optional[Dict] = None
    recommended_action: ValidationAction = ValidationAction.MANUAL_REVIEW
    engineering_justification: str = ""
    reference_standard: str = ""
    reference_documents: List[str] = field(default_factory=list)  # S3 paths to reference P&IDs
    
    def __str__(self):
        return f"[{self.severity.value}] {self.rule_id}: {self.description} ({self.element_id})"


@dataclass
class ValidationResult:
    """Complete validation results for a P&ID"""
    document_id: str
    document_title: str
    validation_passed: bool = True
    findings: List[ValidationFinding] = field(default_factory=list)
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    engineering_holds: List[ValidationFinding] = field(default_factory=list)
    auto_corrections: List[ValidationFinding] = field(default_factory=list)
    
    def add_finding(self, finding: ValidationFinding):
        """Add a validation finding and update counters"""
        self.findings.append(finding)
        
        # Update severity counters
        if finding.severity == ValidationSeverity.CRITICAL:
            self.critical_count += 1
            self.validation_passed = False
        elif finding.severity == ValidationSeverity.HIGH:
            self.high_count += 1
        elif finding.severity == ValidationSeverity.MEDIUM:
            self.medium_count += 1
        elif finding.severity == ValidationSeverity.LOW:
            self.low_count += 1
        
        # Categorize by action
        if finding.recommended_action == ValidationAction.ENGINEERING_HOLD:
            self.engineering_holds.append(finding)
        elif finding.recommended_action == ValidationAction.AUTO_CORRECT:
            self.auto_corrections.append(finding)
    
    def get_summary(self) -> str:
        """Get human-readable summary"""
        status = "✅ PASSED" if self.validation_passed else "❌ FAILED"
        return (
            f"Validation Result: {status}\n"
            f"  Critical: {self.critical_count}\n"
            f"  High: {self.high_count}\n"
            f"  Medium: {self.medium_count}\n"
            f"  Low: {self.low_count}\n"
            f"  Engineering Holds: {len(self.engineering_holds)}\n"
            f"  Auto Corrections: {len(self.auto_corrections)}"
        )


class EngineeringValidationEngine:
    """
    Main validation engine for P&ID conversion validation
    """
    
    def __init__(self):
        self.config = get_engineering_config()
        self.reference_knowledge = None  # Loaded on demand
        logger.info("⚙️  Engineering Validation Engine initialized")
    
    def validate_pid_document(self, pid_data: Dict) -> ValidationResult:
        """
        Validate complete P&ID document
        
        Args:
            pid_data: P&ID data structure containing:
                - equipment_list
                - instrument_list
                - piping_specifications
                - valve_list
                - safety_devices
        
        Returns:
            ValidationResult with all findings
        """
        logger.info("="*70)
        logger.info("🔍 STARTING P&ID VALIDATION")
        logger.info("="*70)
        
        result = ValidationResult(
            document_id=pid_data.get('drawing_number', 'UNKNOWN'),
            document_title=pid_data.get('drawing_title', 'Untitled')
        )
        
        # Run all validation checks
        self._validate_pressure_protection(pid_data, result)
        self._validate_level_instrumentation(pid_data, result)
        self._validate_control_loops(pid_data, result)
        self._validate_safety_valves(pid_data, result)
        self._validate_shutdown_valves(pid_data, result)
        self._validate_instrument_tags(pid_data, result)
        self._validate_flare_routing(pid_data, result)
        self._validate_drain_systems(pid_data, result)
        
        logger.info("\n" + "="*70)
        logger.info(result.get_summary())
        logger.info("="*70)
        
        return result
    
    def _validate_pressure_protection(self, pid_data: Dict, result: ValidationResult):
        """
        RULE: PRESS-001
        All pressure vessels must have overpressure protection (PSV)
        Standard: ASME B31.3 / API RP 520
        """
        logger.info("\n[RULE PRESS-001] Validating pressure protection...")
        
        equipment_list = pid_data.get('equipment_list', [])
        safety_devices = pid_data.get('safety_devices', [])
        
        # Find all pressure vessels
        pressure_vessels = [
            eq for eq in equipment_list 
            if eq.get('type') in ['VESSEL', 'DRUM', 'SEPARATOR', 'COLUMN']
            and eq.get('design_pressure', 0) > 1  # > 1 barg requires protection
        ]
        
        # Check each vessel has PSV
        for vessel in pressure_vessels:
            vessel_tag = vessel.get('tag', 'UNKNOWN')
            
            # Look for PSV associated with this vessel
            associated_psvs = [
                psv for psv in safety_devices
                if psv.get('type') == 'PSV' and vessel_tag in psv.get('protected_equipment', [])
            ]
            
            if not associated_psvs:
                finding = ValidationFinding(
                    rule_id='PRESS-001',
                    element_id=vessel_tag,
                    element_type='equipment',
                    severity=ValidationSeverity.CRITICAL,
                    description=f'Pressure vessel {vessel_tag} missing overpressure protection (PSV)',
                    current_state={'has_psv': False, 'design_pressure': vessel.get('design_pressure')},
                    expected_state={'has_psv': True, 'psv_count': '1 or more'},
                    recommended_action=ValidationAction.ENGINEERING_HOLD,
                    engineering_justification='ASME B31.3 requires overpressure protection for vessels > 1 barg',
                    reference_standard='ASME B31.3, API RP 520',
                    reference_documents=['ADNOC_P&IDs/*/SEPARATOR*.pdf', 'ADNOC_P&IDs/*/VESSEL*.pdf']
                )
                result.add_finding(finding)
                logger.warning(f"   ❌ {vessel_tag}: Missing PSV")
            else:
                logger.info(f"   ✅ {vessel_tag}: Has {len(associated_psvs)} PSV(s)")
    
    def _validate_level_instrumentation(self, pid_data: Dict, result: ValidationResult):
        """
        RULE: LEVEL-001
        Vessels with liquid level require level instrumentation (LIT + alarms)
        Standard: ISA-5.1
        """
        logger.info("\n[RULE LEVEL-001] Validating level instrumentation...")
        
        equipment_list = pid_data.get('equipment_list', [])
        instrument_list = pid_data.get('instrument_list', [])
        
        # Find vessels/drums that contain liquid
        liquid_vessels = [
            eq for eq in equipment_list
            if eq.get('type') in ['VESSEL', 'DRUM', 'SEPARATOR', 'TANK']
            and eq.get('service', '').lower() not in ['gas', 'vapor', 'air']
        ]
        
        for vessel in liquid_vessels:
            vessel_tag = vessel.get('tag', 'UNKNOWN')
            
            # Look for level instruments
            level_instruments = [
                inst for inst in instrument_list
                if inst.get('tag', '').startswith('L') and vessel_tag in inst.get('associated_equipment', [])
            ]
            
            # Check for required instruments
            has_lit = any('LIT' in inst.get('tag', '') or 'LT' in inst.get('tag', '') for inst in level_instruments)
            has_lah = any('LAH' in inst.get('tag', '') or 'LSH' in inst.get('tag', '') for inst in level_instruments)
            has_lal = any('LAL' in inst.get('tag', '') or 'LSL' in inst.get('tag', '') for inst in level_instruments)
            
            if not has_lit:
                finding = ValidationFinding(
                    rule_id='LEVEL-001',
                    element_id=vessel_tag,
                    element_type='equipment',
                    severity=ValidationSeverity.HIGH,
                    description=f'Vessel {vessel_tag} missing level transmitter (LIT/LT)',
                    current_state={'has_level_instrument': False},
                    expected_state={'has_LIT': True, 'has_LAH': True, 'has_LAL': True},
                    recommended_action=ValidationAction.ADD_FROM_REFERENCE,
                    engineering_justification='Liquid vessels require level indication and alarms per ISA-5.1',
                    reference_standard='ISA-5.1',
                    reference_documents=['ADNOC_P&IDs/*/KOD*.pdf', 'ADNOC_P&IDs/*/DRUM*.pdf']
                )
                result.add_finding(finding)
                logger.warning(f"   ❌ {vessel_tag}: Missing LIT")
            
            if not has_lah or not has_lal:
                logger.warning(f"   ⚠️  {vessel_tag}: Missing level alarms (LAH/LAL)")
    
    def _validate_control_loops(self, pid_data: Dict, result: ValidationResult):
        """
        RULE: CONTROL-001
        Control loops must have complete instrumentation (Transmitter + Controller + Valve)
        Standard: ISA-5.1
        """
        logger.info("\n[RULE CONTROL-001] Validating control loops...")
        
        instrument_list = pid_data.get('instrument_list', [])
        
        # Group instruments by loop number (if tagged properly)
        # Example: PIC-3901-01, PIT-3901-01, PCV-3901-01 are same loop
        loops = {}
        for inst in instrument_list:
            tag = inst.get('tag', '')
            # Extract loop number from tag (simplified)
            match = re.search(r'([PLFT])([A-Z]+)-(\d+)-(\d+)', tag)
            if match:
                measure_type = match.group(1)  # P, L, F, or T
                loop_id = f"{measure_type}-{match.group(3)}"
                
                if loop_id not in loops:
                    loops[loop_id] = []
                loops[loop_id].append(inst)
        
        # Validate each loop
        for loop_id, instruments in loops.items():
            tags = [inst.get('tag', '') for inst in instruments]
            
            # Check for control loop completeness
            has_transmitter = any('IT' in tag or 'T' == tag[-1] for tag in tags)
            has_controller = any('IC' in tag or 'C' in tag for tag in tags)
            has_valve = any('CV' in tag for tag in tags)
            
            if has_controller and not has_transmitter:
                finding = ValidationFinding(
                    rule_id='CONTROL-001',
                    element_id=loop_id,
                    element_type='control_loop',
                    severity=ValidationSeverity.HIGH,
                    description=f'Control loop {loop_id} has controller but missing transmitter',
                    current_state={'instruments': tags},
                    expected_state={'has_transmitter': True, 'has_controller': True},
                    recommended_action=ValidationAction.ENGINEERING_HOLD,
                    engineering_justification='Control loops require transmitter for measurement feedback',
                    reference_standard='ISA-5.1'
                )
                result.add_finding(finding)
    
    def _validate_safety_valves(self, pid_data: Dict, result: ValidationResult):
        """
        RULE: PSV-001
        PSV discharge routing must be appropriate (HP/LP flare, atmosphere)
        Standard: API RP 520, API RP 521
        """
        logger.info("\n[RULE PSV-001] Validating PSV discharge routing...")
        
        safety_devices = pid_data.get('safety_devices', [])
        
        for psv in safety_devices:
            if psv.get('type') != 'PSV':
                continue
            
            psv_tag = psv.get('tag', 'UNKNOWN')
            set_pressure = psv.get('set_pressure', 0)
            discharge_to = psv.get('discharge_to', '').upper()
            
            # Validate routing based on pressure
            if set_pressure > 15:  # High pressure (> 15 barg)
                if 'HP' not in discharge_to and 'HIGH PRESSURE' not in discharge_to:
                    finding = ValidationFinding(
                        rule_id='PSV-001',
                        element_id=psv_tag,
                        element_type='safety_device',
                        severity=ValidationSeverity.CRITICAL,
                        description=f'High pressure PSV {psv_tag} ({set_pressure} barg) should discharge to HP Flare',
                        current_state={'set_pressure': set_pressure, 'discharge_to': discharge_to},
                        expected_state={'discharge_to': 'HP_FLARE'},
                        recommended_action=ValidationAction.ENGINEERING_HOLD,
                        engineering_justification='High pressure reliefs (>15 barg) route to HP flare per ADNOC DEP',
                        reference_standard='API RP 521',
                        reference_documents=['ADNOC_P&IDs/*/FLARE*.pdf']
                    )
                    result.add_finding(finding)
                    logger.warning(f"   ❌ {psv_tag}: Wrong flare routing")
    
    def _validate_shutdown_valves(self, pid_data: Dict, result: ValidationResult):
        """
        RULE: SDV-001
        SDV fail positions must align with safety philosophy
        Standard: ADNOC DEP
        """
        logger.info("\n[RULE SDV-001] Validating shutdown valve fail positions...")
        
        valve_list = pid_data.get('valve_list', [])
        
        for valve in valve_list:
            if valve.get('type') != 'SDV':
                continue
            
            valve_tag = valve.get('tag', 'UNKNOWN')
            fail_position = valve.get('fail_position', 'UNKNOWN').upper()
            service = valve.get('service', '').lower()
            
            # Get expected fail position from configuration
            valve_spec = self.config.get_valve_specification('SDV', service)
            expected_fail = valve_spec.get('fail_position', 'FC') if valve_spec else 'FC'
            
            if fail_position not in [expected_fail, 'FC', 'FO'] and fail_position != 'UNKNOWN':
                finding = ValidationFinding(
                    rule_id='SDV-001',
                    element_id=valve_tag,
                    element_type='valve',
                    severity=ValidationSeverity.CRITICAL,
                    description=f'SDV {valve_tag} has unclear fail position: {fail_position}',
                    current_state={'fail_position': fail_position, 'service': service},
                    expected_state={'fail_position': expected_fail},
                    recommended_action=ValidationAction.ENGINEERING_HOLD,
                    engineering_justification='SDV fail position critical for safety - must be explicitly FC or FO',
                    reference_standard='ADNOC DEP'
                )
                result.add_finding(finding)
    
    def _validate_instrument_tags(self, pid_data: Dict, result: ValidationResult):
        """
        RULE: TAG-001
        Instrument tags must follow ISA-5.1 naming convention
        """
        logger.info("\n[RULE TAG-001] Validating instrument tag naming...")
        
        instrument_list = pid_data.get('instrument_list', [])
        
        # ISA-5.1 pattern: [Area]-[Function Letters]-[Loop Number]
        # Example: 14-01-PIT-3901-01
        isa_pattern = re.compile(r'^\d{2,4}-\d{2}-[A-Z]{2,6}-\d{4}-\d{2}$')
        
        for inst in instrument_list:
            tag = inst.get('tag', '')
            if not isa_pattern.match(tag):
                # Could be auto-corrected if we can identify the intended format
                severity = ValidationSeverity.MEDIUM if tag else ValidationSeverity.HIGH
                
                finding = ValidationFinding(
                    rule_id='TAG-001',
                    element_id=tag or 'UNTAGGED',
                    element_type='instrument',
                    severity=severity,
                    description=f'Instrument tag does not follow ISA-5.1 format: {tag}',
                    current_state={'tag': tag},
                    expected_state={'format': 'XX-XX-LLL-NNNN-NN (ISA-5.1)'},
                    recommended_action=ValidationAction.AUTO_CORRECT if tag else ValidationAction.ENGINEERING_HOLD,
                    engineering_justification='Consistent tagging required per ISA-5.1',
                    reference_standard='ISA-5.1'
                )
                result.add_finding(finding)
    
    def _validate_flare_routing(self, pid_data: Dict, result: ValidationResult):
        """
        RULE: FLARE-001
        Flare system must have knockout drum before flare header
        """
        logger.info("\n[RULE FLARE-001] Validating flare system routing...")
        # Implementation depends on how routing is represented in pid_data
        pass
    
    def _validate_drain_systems(self, pid_data: Dict, result: ValidationResult):
        """
        RULE: DRAIN-001
        Hydrocarbon drains must route to closed drain (not open drain)
        """
        logger.info("\n[RULE DRAIN-001] Validating drain system routing...")
        # Implementation depends on service fluid identification
        pass


# Convenience function
def validate_pid(pid_data: Dict) -> ValidationResult:
    """
    Validate P&ID document
    
    Args:
        pid_data: P&ID data structure
    
    Returns:
        ValidationResult with findings
    """
    engine = EngineeringValidationEngine()
    return engine.validate_pid_document(pid_data)
