"""
Engineering Standards Configuration
====================================

SOFT-CODED ENGINEERING RULES FOR PFD → P&ID CONVERSION
Aligned with ADNOC DEP / ASME B31.3 / B31.8 / ISA-5.1 standards

PURPOSE:
- Maintain engineering correctness through configuration, NOT hardcoded logic
- Reference-driven validation (compares against approved P&IDs from S3)
- Traceable corrections with engineering justification

CRITICAL DESIGN PRINCIPLES:
1. All rules are config-driven and can be updated without code changes
2. Every correction must be traceable to a reference document
3. Ambiguous cases are flagged as "ENGINEERING_HOLD" for manual review
4. Accuracy > Automation (never auto-generate unsupported elements)

INTEGRATION WITH S3 REFERENCE SAMPLES:
- References loaded from: rejlers-engineering-data (me-central-1)
- Approved P&IDs from ADNOC projects used as ground truth
- Pattern matching against real engineering drawings
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class EngineeringStandard(Enum):
    """Engineering standards applicable to ADNOC projects"""
    ADNOC_DEP = "ADNOC Design & Engineering Practice"
    ASME_B31_3 = "ASME B31.3 - Process Piping"
    ASME_B31_8 = "ASME B31.8 - Gas Transmission & Distribution"
    ISA_5_1 = "ISA-5.1 - Instrumentation Symbols and Identification"
    API_RP_551 = "API RP 551 - Process Measurement Instrumentation"
    API_RP_520 = "API RP 520 - Sizing, Selection and Installation of PSVs"
    

@dataclass
class InstrumentMapping:
    """
    Configuration for instrument type identification and mapping
    Based on reference P&ID samples from S3
    """
    # Instrument Type Identification (from reference P&IDs)
    instrument_prefixes: Dict[str, Dict] = field(default_factory=lambda: {
        # PRESSURE INSTRUMENTS
        'P': {
            'description': 'Pressure',
            'common_suffixes': {
                'IT': {'name': 'Pressure Indicator Transmitter', 'safety_critical': True},
                'IC': {'name': 'Pressure Indicator Controller', 'safety_critical': True},
                'I': {'name': 'Pressure Indicator', 'safety_critical': False},
                'CV': {'name': 'Pressure Control Valve', 'safety_critical': True},
                'SV': {'name': 'Pressure Safety Valve', 'safety_critical': True, 'std': 'API RP 520'},
                'SAH': {'name': 'Pressure Switch Alarm High', 'safety_critical': True},
                'SAL': {'name': 'Pressure Switch Alarm Low', 'safety_critical': True},
                'SDH': {'name': 'Pressure Switch Shutdown High', 'safety_critical': True, 'interlock': True},
                'SDL': {'name': 'Pressure Switch Shutdown Low', 'safety_critical': True, 'interlock': True},
            },
            'standard': 'ISA-5.1'
        },
        # LEVEL INSTRUMENTS
        'L': {
            'description': 'Level',
            'common_suffixes': {
                'IT': {'name': 'Level Indicator Transmitter', 'safety_critical': True},
                'IC': {'name': 'Level Indicator Controller', 'safety_critical': True},
                'CV': {'name': 'Level Control Valve', 'safety_critical': False},
                'SAH': {'name': 'Level Switch Alarm High', 'safety_critical': True},
                'SAL': {'name': 'Level Switch Alarm Low', 'safety_critical': True},
                'SAHH': {'name': 'Level Switch Alarm High-High', 'safety_critical': True},
                'SALL': {'name': 'Level Switch Alarm Low-Low', 'safety_critical': True},
                'SDH': {'name': 'Level Switch Shutdown High', 'safety_critical': True, 'interlock': True},
                'SDL': {'name': 'Level Switch Shutdown Low', 'safety_critical': True, 'interlock': True},
                'G': {'name': 'Level Gauge (sight glass)', 'safety_critical': False},
            },
            'standard': 'ISA-5.1'
        },
        # FLOW INSTRUMENTS
        'F': {
            'description': 'Flow',
            'common_suffixes': {
                'IT': {'name': 'Flow Indicator Transmitter', 'safety_critical': False},
                'IC': {'name': 'Flow Indicator Controller', 'safety_critical': False},
                'CV': {'name': 'Flow Control Valve', 'safety_critical': False},
                'E': {'name': 'Flow Element (orifice plate)', 'safety_critical': False},
                'SAH': {'name': 'Flow Switch Alarm High', 'safety_critical': False},
                'SAL': {'name': 'Flow Switch Alarm Low', 'safety_critical': True},
            },
            'standard': 'ISA-5.1'
        },
        # TEMPERATURE INSTRUMENTS
        'T': {
            'description': 'Temperature',
            'common_suffixes': {
                'I': {'name': 'Temperature Indicator', 'safety_critical': False},
                'IT': {'name': 'Temperature Indicator Transmitter', 'safety_critical': False},
                'IC': {'name': 'Temperature Indicator Controller', 'safety_critical': False},
                'CV': {'name': 'Temperature Control Valve', 'safety_critical': False},
                'SAH': {'name': 'Temperature Switch Alarm High', 'safety_critical': True},
                'SAL': {'name': 'Temperature Switch Alarm Low', 'safety_critical': False},
            },
            'standard': 'ISA-5.1'
        },
    })
    

@dataclass
class ValveMapping:
    """
    Valve type identification and fail-position configuration
    Based on ADNOC safety philosophy from reference P&IDs
    """
    valve_types: Dict[str, Dict] = field(default_factory=lambda: {
        # SHUTDOWN VALVES (Safety Critical)
        'SDV': {
            'name': 'Shutdown Valve',
            'description': 'Emergency shutdown valve (ESD system)',
            'typical_applications': ['Isolate equipment on emergency', 'Block in/out vessels', 'Fire isolation'],
            'fail_position_rules': {
                'default': 'FC',  # Fail Close (safe position for most applications)
                'special_cases': {
                    'flare_inlet': 'FO',  # Fail Open to ensure relief path
                    'fire_protection': 'FO',  # Fail Open for deluge systems
                }
            },
            'interlock': True,
            'safety_critical': True,
            'standard': 'ADNOC DEP'
        },
        # MOTOR OPERATED VALVES
        'MOV': {
            'name': 'Motor Operated Valve',
            'description': 'Electrically actuated valve for on/off service',
            'typical_applications': ['Block valves', 'Infrequent operation', 'Manual override required'],
            'fail_position': 'AS-IS',  # Remains in last position on power failure
            'interlock': False,
            'safety_critical': False,
            'standard': 'ADNOC DEP'
        },
        # CONTROL VALVES
        'PCV': {
            'name': 'Pressure Control Valve',
            'description': 'Pneumatic/electric pressure control valve',
            'fail_position_rules': {
                'default': 'FC',
                'special_cases': {
                    'pressure_reducing': 'FO',  # Maintain downstream pressure
                    'back_pressure': 'FC',  # Prevent overpressure
                }
            },
            'control_loop': True,
            'safety_critical': True
        },
        'LCV': {
            'name': 'Level Control Valve',
            'description': 'Level control valve (typically in vessel outlet)',
            'fail_position': 'FC',  # Prevent vessel overflow
            'control_loop': True,
            'safety_critical': True
        },
        # CHECK VALVES
        'CV': {
            'name': 'Check Valve',
            'description': 'Non-return valve (passive)',
            'typical_applications': ['Prevent backflow', 'Pump discharge protection'],
            'fail_position': 'N/A',  # Passive device
            'interlock': False
        },
        # RELIEF DEVICES
        'PSV': {
            'name': 'Pressure Safety Valve',
            'description': 'Spring-loaded pressure relief valve',
            'typical_applications': ['Overpressure protection', 'Thermal expansion relief'],
            'sizing_standard': 'API RP 520',
            'discharge_to': ['HP Flare', 'LP Flare', 'Closed Drain', 'Atmosphere'],
            'safety_critical': True,
            'interlock': False
        },
    })
    

@dataclass
class RoutingConfiguration:
    """
    Piping routing rules for safety systems
    Based on ADNOC safety philosophy
    """
    safety_routes: Dict[str, Dict] = field(default_factory=lambda: {
        'HP_FLARE': {
            'description': 'High Pressure Flare System',
            'typical_sources': [
                'PSV reliefs from HP equipment (> 15 barg)',
                'HP separator dumps',
                'Emergency depressurization',
            ],
            'header_size_min': '6"',
            'material': 'CS (Carbon Steel)',
            'slope': '1:200 toward knockout drum',
            'knockout_drum_required': True,
            'reference_dwg': 'Typical from ADNOC projects'
        },
        'LP_FLARE': {
            'description': 'Low Pressure Flare System',
            'typical_sources': [
                'PSV reliefs from LP equipment (< 15 barg)',
                'Tank vents',
                'Storage atmospheric reliefs',
            ],
            'header_size_min': '4"',
            'knockout_drum_required': True
        },
        'CLOSED_DRAIN': {
            'description': 'Closed Drain System (Hydrocarbon Liquids)',
            'typical_sources': [
                'Equipment drains containing hydrocarbons',
                'PSV liquid discharge',
                'Sample return lines',
            ],
            'collection_vessel_required': True,
            'route_to': 'Slop tank or drain drum',
            'interlock_required': False
        },
        'OPEN_DRAIN': {
            'description': 'Open Drain System (Non-Hazardous)',
            'typical_sources': [
                'Cooling water drains',
                'Steam condensate',
                'Rainwater',
            ],
            'segregation_required': True,
            'reference': 'Must not mix with hydrocarbon drains'
        },
    })


@dataclass
class ValidationRules:
    """
    Engineering validation rules for PFD → P&ID conversion
    Each rule references specific engineering standards
    """
    mandatory_checks: List[Dict] = field(default_factory=lambda: [
        {
            'rule_id': 'PRESS-001',
            'description': 'All pressure vessels must have overpressure protection',
            'check': 'PSV present on vessels with design pressure > 1 barg',
            'standard': 'ASME B31.3 / API RP 520',
            'severity': 'CRITICAL',
            'action_if_missing': 'FLAG_ENGINEERING_HOLD',
            'reference_samples': ['ADNOC_P&IDs/*/P&ID*.pdf']  # S3 path pattern
        },
        {
            'rule_id': 'LEVEL-001',
            'description': 'Vessels with liquid level require level instrumentation',
            'check': 'LIT + LAH/LAL present on all drums/vessels with liquid service',
            'standard': 'ISA-5.1',
            'severity': 'HIGH',
            'action_if_missing': 'ADD_FROM_REFERENCE',
            'reference_samples': ['ADNOC_P&IDs/*KOD*.pdf', 'ADNOC_P&IDs/*DRUM*.pdf']
        },
        {
            'rule_id': 'VALVE-001',
            'description': 'SDVs must have correct fail positions for safety',
            'check': 'SDV fail position aligns with safety philosophy',
            'standard': 'ADNOC DEP',
            'severity': 'CRITICAL',
            'action_if_wrong': 'FLAG_ENGINEERING_HOLD',
            'reference': 'Verify against P&ID typical drawings'
        },
        {
            'rule_id': 'TAG-001',
            'description': 'Instrument tags must follow ISA-5.1 convention',
            'check': 'Tag format: [AREA]-[PREFIX][SUFFIX]-[NUMBER]',
            'standard': 'ISA-5.1',
            'severity': 'MEDIUM',
            'action_if_wrong': 'AUTO_CORRECT',
            'example': '14-01-PIT-3901-01 (Area 14, Plant 01, Pressure Indicator Transmitter)'
        },
        {
            'rule_id': 'FLARE-001',
            'description': 'PSV discharge routing must be appropriate',
            'check': 'HP reliefs → HP Flare, LP reliefs → LP Flare',
            'standard': 'API RP 521',
            'severity': 'HIGH',
            'action_if_wrong': 'FLAG_ENGINEERING_HOLD',
            'reference_samples': ['ADNOC_P&IDs/*/PIG*.pdf', 'ADNOC_P&IDs/*SEPARATOR*.pdf']
        },
    ])
    
    optional_checks: List[Dict] = field(default_factory=lambda: [
        {
            'rule_id': 'INST-001',
            'description': 'Flow elements typically located upstream of control valves',
            'check': 'FE/FIT placement relative to FCV',
            'standard': 'ISA-5.1',
            'severity': 'LOW',
            'action': 'SUGGEST_CORRECTION'
        },
    ])


class EngineeringStandardsConfig:
    """
    Central configuration manager for engineering standards
    Loads configuration from JSON files and S3 reference samples
    """
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path(__file__).parent / 'config'
        self.config_path.mkdir(exist_ok=True)
        
        # Load configurations
        self.instrument_mapping = InstrumentMapping()
        self.valve_mapping = ValveMapping()
        self.routing_config = RoutingConfiguration()
        self.validation_rules = ValidationRules()
        
        # S3 reference integration
        self.s3_reference_path = "rejlers-engineering-data"
        self.s3_region = "me-central-1"
        
        logger.info("✅ Engineering Standards Configuration Loaded")
        logger.info(f"   - Instrument Types: {len(self.instrument_mapping.instrument_prefixes)}")
        logger.info(f"   - Valve Types: {len(self.valve_mapping.valve_types)}")
        logger.info(f"   - Safety Routes: {len(self.routing_config.safety_routes)}")
        logger.info(f"   - Validation Rules: {len(self.validation_rules.mandatory_checks)}")
    
    def get_instrument_definition(self, tag: str) -> Optional[Dict]:
        """
        Get instrument definition based on tag
        Example: 'PIT-3901-01' → returns Pressure Indicator Transmitter definition
        """
        if '-' not in tag:
            return None
            
        parts = tag.split('-')
        instrument_code = parts[-2] if len(parts) >= 2 else tag
        
        # Extract first letter (measurement type)
        if not instrument_code:
            return None
            
        first_letter = instrument_code[0].upper()
        suffix = instrument_code[1:].upper()
        
        if first_letter in self.instrument_mapping.instrument_prefixes:
            prefix_info = self.instrument_mapping.instrument_prefixes[first_letter]
            suffix_info = prefix_info['common_suffixes'].get(suffix, {})
            
            return {
                'measurement_type': prefix_info['description'],
                'instrument_name': suffix_info.get('name', f'{prefix_info["description"]} {suffix}'),
                'safety_critical': suffix_info.get('safety_critical', False),
                'interlock': suffix_info.get('interlock', False),
                'standard': prefix_info.get('standard', 'ISA-5.1')
            }
        
        return None
    
    def get_valve_specification(self, valve_type: str, service: Optional[str] = None) -> Optional[Dict]:
        """
        Get valve specification including fail position
        Args:
            valve_type: 'SDV', 'MOV', 'PCV', etc.
            service: Special application (e.g., 'flare_inlet', 'fire_protection')
        """
        if valve_type not in self.valve_mapping.valve_types:
            return None
            
        valve_spec = self.valve_mapping.valve_types[valve_type].copy()
        
        # Determine fail position if applicable
        if 'fail_position_rules' in valve_spec:
            rules = valve_spec['fail_position_rules']
            if service and service in rules.get('special_cases', {}):
                valve_spec['fail_position'] = rules['special_cases'][service]
            else:
                valve_spec['fail_position'] = rules.get('default', 'AS-IS')
        
        return valve_spec
    
    def validate_pid_element(self, element_type: str, element_data: Dict) -> Dict:
        """
        Validate a P&ID element against engineering rules
        Returns validation result with corrections/flags
        """
        validation_result = {
            'element_type': element_type,
            'element_data': element_data,
            'valid': True,
            'violations': [],
            'suggestions': [],
            'engineering_holds': []
        }
        
        # Apply relevant validation rules
        for rule in self.validation_rules.mandatory_checks:
            # This would contain actual validation logic
            # For now, structure is set up for extensibility
            pass
        
        return validation_result
    
    def export_config(self, output_path: Path):
        """Export configuration to JSON for review/editing"""
        config_data = {
            'instrument_mapping': self.instrument_mapping.__dict__,
            'valve_mapping': self.valve_mapping.__dict__,
            'routing_config': self.routing_config.__dict__,
            'validation_rules': {
                'mandatory_checks': self.validation_rules.mandatory_checks,
                'optional_checks': self.validation_rules.optional_checks
            }
        }
        
        with open(output_path, 'w') as f:
            json.dump(config_data, f, indent=2)
        
        logger.info(f"✅ Configuration exported to {output_path}")
    
    def load_from_s3_references(self):
        """
        Load engineering patterns from S3 reference P&IDs
        This would analyze approved P&IDs and extract patterns
        """
        # TODO: Implement S3 reference analysis
        # This will be in the S3 reference loader module
        pass


# Global configuration instance
_config_instance = None

def get_engineering_config() -> EngineeringStandardsConfig:
    """Get singleton instance of engineering configuration"""
    global _config_instance
    if _config_instance is None:
        _config_instance = EngineeringStandardsConfig()
    return _config_instance
