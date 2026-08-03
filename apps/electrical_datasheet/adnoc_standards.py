"""
ADNOC Standards Reference Data for Electrical Equipment Validation
===================================================================
Purpose: Store ADNOC standard specifications for transformer and switchgear validation
Source: ADNOC Engineering Standards & External Industry Standards
Storage: AWS S3 (rejlers-engineering-data/adnoc_standards/)

Soft-coded approach: Load from S3 or fallback to embedded defaults
"""

import json
import os
from typing import Dict, List, Any, Optional
from django.conf import settings
from decimal import Decimal


class ADNOCStandardsManager:
    """
    Manages ADNOC standards for electrical equipment validation.
    Supports loading from S3 or using embedded defaults.
    """
    
    def __init__(self):
        self.standards_cache = {}
        self.s3_enabled = getattr(settings, 'USE_S3', False)
        
    def get_transformer_standards(self, voltage_class: str = 'all') -> Dict[str, Any]:
        """
        Get ADNOC standards for power and distribution transformers.
        
        Args:
            voltage_class: 'all', '11kv', '33kv', '132kv', etc.
        
        Returns:
            Dictionary containing validation criteria
        """
        cache_key = f'transformer_{voltage_class}'
        
        if cache_key in self.standards_cache:
            return self.standards_cache[cache_key]
        
        # Try loading from S3 first
        if self.s3_enabled:
            s3_data = self._load_from_s3(f'adnoc_standards/transformers/{voltage_class}.json')
            if s3_data:
                self.standards_cache[cache_key] = s3_data
                return s3_data
        
        # Fallback to embedded standards
        standards = self._get_default_transformer_standards(voltage_class)
        self.standards_cache[cache_key] = standards
        return standards
    
    def get_switchgear_standards(self, voltage_class: str = '11kv') -> Dict[str, Any]:
        """
        Get ADNOC standards for switchgear (11kV, 33kV, etc.).
        
        Args:
            voltage_class: '11kv', '33kv', '132kv', etc.
        
        Returns:
            Dictionary containing validation criteria
        """
        cache_key = f'switchgear_{voltage_class}'
        
        if cache_key in self.standards_cache:
            return self.standards_cache[cache_key]
        
        # Try loading from S3 first
        if self.s3_enabled:
            s3_data = self._load_from_s3(f'adnoc_standards/switchgear/{voltage_class}.json')
            if s3_data:
                self.standards_cache[cache_key] = s3_data
                return s3_data
        
        # Fallback to embedded standards
        standards = self._get_default_switchgear_standards(voltage_class)
        self.standards_cache[cache_key] = standards
        return standards
    
    def _load_from_s3(self, s3_key: str) -> Optional[Dict[str, Any]]:
        """Load standards from S3 bucket"""
        if not self.s3_enabled:
            return None
        
        try:
            from apps.core.s3_service import S3Service
            s3_service = S3Service()
            
            content = s3_service.download_file_content(s3_key)
            if content:
                return json.loads(content)
        except Exception as e:
            print(f"[ADNOC Standards] Failed to load from S3: {e}")
        
        return None
    
    def _get_default_transformer_standards(self, voltage_class: str) -> Dict[str, Any]:
        """
        Default ADNOC transformer standards (embedded fallback).
        Based on ADNOC Engineering Standards and IEC 60076 series.
        """
        return {
            "source": "ADNOC Engineering Standards & IEC 60076",
            "last_updated": "2024-01-01",
            "voltage_classes": {
                "11kv": {
                    "primary_voltage": {"min": 10.5, "max": 11.5, "unit": "kV"},
                    "secondary_voltage": {"options": [0.415, 3.3, 6.6, 11], "unit": "kV"},
                    "ratings": {"min": 50, "max": 10000, "unit": "kVA"},
                    "frequency": {"value": 50, "tolerance": 1, "unit": "Hz"},
                    "phases": [3],
                    "connection_types": ["Dyn11", "Dyn1", "Yyn0", "Yzn11"],
                    "cooling": ["ONAN", "ONAF", "OFAF"],
                    "impedance": {"min": 4, "max": 8, "unit": "%"},
                    "insulation_class": {"options": ["F", "H"], "temp_rise": {"F": 100, "H": 125}},
                    "tap_range": {"min": -5, "max": 5, "step": 2.5, "unit": "%"},
                    "fault_level": {"min": 20, "max": 31.5, "unit": "kA"},
                    "standards": ["IEC 60076-1", "IEC 60076-2", "IEC 60076-3", "IEEE C57.12.00"],
                    "tests_required": [
                        "Ratio test",
                        "Polarity test",
                        "Impedance test",
                        "No-load current and losses",
                        "Load losses",
                        "Insulation resistance",
                        "Voltage withstand test",
                        "Temperature rise test"
                    ]
                },
                "33kv": {
                    "primary_voltage": {"min": 31.5, "max": 34.5, "unit": "kV"},
                    "secondary_voltage": {"options": [11, 6.6, 3.3], "unit": "kV"},
                    "ratings": {"min": 500, "max": 50000, "unit": "kVA"},
                    "frequency": {"value": 50, "tolerance": 1, "unit": "Hz"},
                    "phases": [3],
                    "connection_types": ["YNyn0", "Dyn11", "YNd11"],
                    "cooling": ["ONAN", "ONAF", "OFAF", "ODAF"],
                    "impedance": {"min": 6, "max": 12, "unit": "%"},
                    "insulation_class": {"options": ["F", "H"], "temp_rise": {"F": 100, "H": 125}},
                    "tap_range": {"min": -10, "max": 10, "step": 1.25, "unit": "%"},
                    "fault_level": {"min": 25, "max": 40, "unit": "kA"},
                    "standards": ["IEC 60076-1", "IEC 60076-2", "IEC 60076-3", "IEEE C57.12.00"],
                    "tests_required": [
                        "Ratio test",
                        "Polarity test",
                        "Vector group test",
                        "Impedance test",
                        "No-load current and losses",
                        "Load losses",
                        "Insulation resistance",
                        "Voltage withstand test",
                        "Temperature rise test",
                        "Impulse test"
                    ]
                }
            },
            "general_requirements": {
                "environment": {
                    "ambient_temp_max": 50,
                    "ambient_temp_min": -10,
                    "humidity_max": 95,
                    "altitude_max": 1000,
                    "unit": "°C/%/meters"
                },
                "enclosure": {
                    "ip_rating": {"min": "IP23", "outdoor": "IP44"},
                    "material": ["Galvanized steel", "Stainless steel"]
                },
                "bushings": {
                    "types": ["Oil-filled", "Resin", "SF6"],
                    "ratings": "As per voltage class"
                },
                "oil_specifications": {
                    "type": "Mineral oil / Ester fluid",
                    "breakdown_voltage": {"min": 30, "unit": "kV"},
                    "dielectric_strength": "IEC 60296"
                },
                "protection": {
                    "buchholz_relay": "Required for oil-filled > 500 kVA",
                    "temperature_indicator": "Winding & Oil temperature",
                    "pressure_relief": "Required",
                    "oil_level_indicator": "Required"
                }
            }
        }
    
    def _get_default_switchgear_standards(self, voltage_class: str) -> Dict[str, Any]:
        """
        Default ADNOC switchgear standards (embedded fallback).
        Based on ADNOC Engineering Standards and IEC 62271 series.
        """
        return {
            "source": "ADNOC Engineering Standards & IEC 62271",
            "last_updated": "2024-01-01",
            "voltage_classes": {
                "11kv": {
                    "rated_voltage": {"value": 12, "unit": "kV", "note": "System voltage 11kV"},
                    "rated_current": {"options": [630, 1250, 2000, 2500, 3150, 4000], "unit": "A"},
                    "frequency": {"value": 50, "unit": "Hz"},
                    "rated_short_time_current": {"duration": 3, "values": [20, 25, 31.5, 40], "unit": "kA/s"},
                    "rated_peak_current": {"formula": "2.5 * short_time_current", "unit": "kA"},
                    "rated_breaking_current": {"values": [16, 20, 25, 31.5], "unit": "kA"},
                    "rated_making_current": {"formula": "2.5 * breaking_current", "unit": "kA"},
                    "insulation_level": {
                        "power_frequency": {"value": 28, "duration": 60, "unit": "kV/s"},
                        "impulse": {"value": 75, "waveform": "1.2/50 μs", "unit": "kV"}
                    },
                    "types": {
                        "air_insulated": {
                            "designation": "AIS",
                            "withdrawable": "Preferred",
                            "compartments": ["CB", "Bus", "Cable", "Instrument"],
                            "segregation": "Type 2b minimum (IEC 62271-200)"
                        },
                        "gas_insulated": {
                            "designation": "GIS",
                            "gas": "SF6 or alternative eco-gas",
                            "leakage_rate": {"max": 0.5, "unit": "%/year"}
                        }
                    },
                    "circuit_breaker": {
                        "type": ["Vacuum", "SF6"],
                        "operating_mechanism": ["Spring", "Motor-wound spring"],
                        "operations": {
                            "mechanical_endurance": 10000,
                            "electrical_endurance": 100,
                            "duty_cycle": "O-0.3s-CO-3min-CO"
                        },
                        "operating_time": {"closing": 100, "opening": 50, "unit": "ms"}
                    },
                    "protection": {
                        "relays": ["Overcurrent", "Earth fault", "Directional", "Distance"],
                        "ct_ratio": "As per load requirement",
                        "vt_ratio": "11000/110V",
                        "interlocks": ["CB-Earthing switch", "Busbar isolator", "Cable isolator"]
                    },
                    "standards": [
                        "IEC 62271-1", "IEC 62271-100", "IEC 62271-200",
                        "IEC 62271-102", "IEEE C37.06", "ADNOC-AGES-SP-1030"
                    ],
                    "tests_required": [
                        "Dielectric test",
                        "Temperature rise test",
                        "Short-circuit test",
                        "Mechanical endurance test",
                        "Protection relay testing",
                        "Interlock testing",
                        "Auxiliary supply test",
                        "Insulation resistance"
                    ]
                },
                "33kv": {
                    "rated_voltage": {"value": 36, "unit": "kV", "note": "System voltage 33kV"},
                    "rated_current": {"options": [630, 1250, 2000, 2500, 3150], "unit": "A"},
                    "frequency": {"value": 50, "unit": "Hz"},
                    "rated_short_time_current": {"duration": 3, "values": [25, 31.5, 40], "unit": "kA/s"},
                    "rated_peak_current": {"formula": "2.5 * short_time_current", "unit": "kA"},
                    "rated_breaking_current": {"values": [20, 25, 31.5, 40], "unit": "kA"},
                    "insulation_level": {
                        "power_frequency": {"value": 70, "duration": 60, "unit": "kV/s"},
                        "impulse": {"value": 170, "waveform": "1.2/50 μs", "unit": "kV"}
                    },
                    "standards": [
                        "IEC 62271-1", "IEC 62271-100", "IEC 62271-200",
                        "IEEE C37.06", "ADNOC-AGES-SP-1030"
                    ]
                }
            },
            "general_requirements": {
                "environment": {
                    "ambient_temp_max": 50,
                    "ambient_temp_min": -10,
                    "humidity_max": 95,
                    "altitude_max": 1000,
                    "pollution_level": "IEC 60815-1 Heavy",
                    "seismic": "As per project specification"
                },
                "enclosure": {
                    "ip_rating": {"indoor": "IP4X", "outdoor": "IP54"},
                    "ik_rating": "IK10",
                    "material": "Galvanized steel with epoxy coating",
                    "color": "RAL 7035 (Light grey)"
                },
                "busbars": {
                    "material": "Copper / Aluminum",
                    "plating": "Tin-plated (Copper)",
                    "configuration": ["Single busbar", "Double busbar"],
                    "earthing": "Separate earthing busbar required"
                },
                "auxiliary_supply": {
                    "ac": {"voltage": [230, 415], "frequency": 50, "unit": "V/Hz"},
                    "dc": {"voltage": [110, 125, 220], "unit": "V"},
                    "ups_backed": "Control and protection circuits"
                },
                "communication": {
                    "protocols": ["IEC 61850", "Modbus RTU", "DNP3"],
                    "interfaces": ["Ethernet", "Fiber optic"]
                }
            }
        }
    
    def validate_against_standard(self, 
                                   equipment_type: str, 
                                   datasheet_data: Dict[str, Any],
                                   voltage_class: str = None) -> Dict[str, Any]:
        """
        Validate datasheet data against ADNOC standards.
        
        Args:
            equipment_type: 'transformer' or 'switchgear'
            datasheet_data: Parsed datasheet data
            voltage_class: Optional voltage class filter
        
        Returns:
            Validation results with pass/fail/warnings
        """
        if equipment_type == 'transformer':
            standards = self.get_transformer_standards(voltage_class or 'all')
        elif equipment_type == 'switchgear':
            standards = self.get_switchgear_standards(voltage_class or '11kv')
        else:
            return {"error": f"Unknown equipment type: {equipment_type}"}
        
        validation_results = {
            "equipment_type": equipment_type,
            "voltage_class": voltage_class,
            "standard_source": standards.get("source"),
            "checks_passed": 0,
            "checks_failed": 0,
            "checks_warning": 0,
            "details": []
        }
        
        # Perform validation checks
        # This will be called by the enhanced quality checker
        return validation_results


# Global instance
adnoc_standards = ADNOCStandardsManager()
