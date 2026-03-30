"""
MOV (Motor Operated Valve) Datasheet Configuration
SOFT-CODED: Comprehensive field definitions for intelligent P&ID extraction
All fields are configurable and mapped for easy maintenance
"""

# ============================================================================
# MOV DATASHEET FIELD CONFIGURATION
# ============================================================================

MOV_DATASHEET_FIELDS = {
    # ========================================================================
    # SECTION 1: GENERAL DATA
    # ========================================================================
    'general_data': {
        'section_name': 'General Data',
        'fields': {
            'tag_number': {
                'label': '1. Tag Number',
                'type': 'text',
                'required': True,
                'extraction_keywords': ['tag', 'tag number', 'equipment no', 'valve no'],
                'pattern': r'[A-Z]{2,4}[-_]\d{1,5}(?:[-_]\d{1,5})?[A-Z]?',
                'example': 'MOV-101A',
                'excel_cell': 'B3'
            },
            'service': {
                'label': '2. Service',
                'type': 'text',
                'required': True,
                'extraction_keywords': ['service', 'description', 'application'],
                'example': 'Process Isolation / Emergency Shutdown',
                'excel_cell': 'B4'
            },
            'pid_no': {
                'label': '3. DWG. No.',
                'type': 'text',
                'required': False,
                'extraction_keywords': ['dwg no', 'drawing no', 'drawing number', 'p&id', 'pid', 'document no'],
                'pattern': r'[A-Z0-9]{2,6}[-_][A-Z0-9]{3,6}[-_][A-Z0-9]{2,6}[-_][A-Z0-9]{3,8}[-_]\d{3,5}|[A-Z0-9]{2,6}[-_][A-Z0-9]{3,6}[-_]\d{3,5}',
                'example': 'PJ6-EXD-MRI-BQDA-0022',
                'excel_cell': 'B5'
            },
            'line_number': {
                'label': '4.1 Line Number',
                'type': 'text',
                'required': False,
                'extraction_keywords': ['line no', 'line number', 'line id'],
                'pattern': r'\d+"[-_]?[A-Z]{2,4}[-_]?\d{3,5}',
                'example': '2"-HC-1001-A1',
                'excel_cell': 'B6'
            },
            'piping_class': {
                'label': '4.2 Piping Class',
                'type': 'text',
                'required': False,
                'extraction_keywords': ['piping class', 'pipe class', 'rating'],
                'options': ['150#', '300#', '600#', '900#', '1500#', '2500#'],
                'example': '300# RF',
                'excel_cell': 'D6'
            },
            'fluid': {
                'label': '5.1 Fluid',
                'type': 'text',
                'required': True,
                'extraction_keywords': ['fluid', 'medium', 'process fluid', 'service fluid'],
                'example': 'Natural Gas / Process Water / Crude Oil',
                'excel_cell': 'B7'
            },
            'state': {
                'label': '5.2 State',
                'type': 'select',
                'required': False,
                'options': ['Liquid', 'Gas', 'Vapor', 'Two-Phase', 'Condensate'],
                'extraction_keywords': ['state', 'phase state'],
                'example': 'Liquid',
                'excel_cell': 'D7'
            },
            'phase': {
                'label': '5.3 Phase',
                'type': 'select',
                'required': False,
                'options': ['Single Phase', 'Two Phase', 'Multi Phase'],
                'extraction_keywords': ['phase', 'flow phase'],
                'example': 'Single Phase',
                'excel_cell': 'F7'
            }
        }
    },
    
    # ========================================================================
    # SECTION 2: OPERATING CONDITIONS
    # ========================================================================
    'operating_conditions': {
        'section_name': 'Operating Conditions',
        'fields': {
            'operating_pressure_min': {
                'label': '6. Operating Pressure - Min',
                'type': 'number',
                'unit': 'bar(g)',
                'required': False,
                'extraction_keywords': ['operating pressure', 'op pressure', 'min pressure'],
                'example': '0',
                'excel_cell': 'B10'
            },
            'operating_pressure_normal': {
                'label': '6. Operating Pressure - Normal',
                'type': 'number',
                'unit': 'bar(g)',
                'required': True,
                'extraction_keywords': ['operating pressure', 'normal pressure', 'operating'],
                'example': '10',
                'excel_cell': 'D10'
            },
            'operating_pressure_max': {
                'label': '6. Operating Pressure - Maximum',
                'type': 'number',
                'unit': 'bar(g)',
                'required': False,
                'extraction_keywords': ['operating pressure', 'max pressure', 'maximum pressure'],
                'example': '15',
                'excel_cell': 'F10'
            },
            'operating_temperature_min': {
                'label': '7. Operating Temperature - Min',
                'type': 'number',
                'unit': '°C',
                'required': False,
                'extraction_keywords': ['operating temp', 'op temp', 'min temp'],
                'example': '-20',
                'excel_cell': 'B11'
            },
            'operating_temperature_normal': {
                'label': '7. Operating Temperature - Normal',
                'type': 'number',
                'unit': '°C',
                'required': True,
                'extraction_keywords': ['operating temp', 'normal temp', 'operating'],
                'example': '50',
                'excel_cell': 'D11'
            },
            'operating_temperature_max': {
                'label': '7. Operating Temperature - Maximum',
                'type': 'number',
                'unit': '°C',
                'required': False,
                'extraction_keywords': ['operating temp', 'max temp', 'maximum temp'],
                'example': '120',
                'excel_cell': 'F11'
            },
            'design_pressure_min': {
                'label': '8. Design Pressure - Min',
                'type': 'number',
                'unit': 'bar(g)',
                'required': False,
                'extraction_keywords': ['design pressure', 'min design pressure'],
                'example': '-1',
                'excel_cell': 'B12'
            },
            'design_pressure_normal': {
                'label': '8. Design Pressure - Normal',
                'type': 'number',
                'unit': 'bar(g)',
                'required': True,
                'extraction_keywords': ['design pressure', 'design'],
                'example': '20',
                'excel_cell': 'D12'
            },
            'design_pressure_max': {
                'label': '8. Design Pressure - Maximum',
                'type': 'number',
                'unit': 'bar(g)',
                'required': False,
                'extraction_keywords': ['design pressure', 'max design pressure'],
                'example': '25',
                'excel_cell': 'F12'
            },
            'design_temperature_min': {
                'label': '9. Design Temperature - Min',
                'type': 'number',
                'unit': '°C',
                'required': False,
                'extraction_keywords': ['design temp', 'min design temp'],
                'example': '-29',
                'excel_cell': 'B13'
            },
            'design_temperature_normal': {
                'label': '9. Design Temperature - Normal',
                'type': 'number',
                'unit': '°C',
                'required': True,
                'extraction_keywords': ['design temp', 'design'],
                'example': '150',
                'excel_cell': 'D13'
            },
            'design_temperature_max': {
                'label': '9. Design Temperature - Maximum',
                'type': 'number',
                'unit': '°C',
                'required': False,
                'extraction_keywords': ['design temp', 'max design temp'],
                'example': '180',
                'excel_cell': 'F13'
            },
            'source_service': {
                'label': '10. Source Service and Special Condition',
                'type': 'textarea',
                'required': False,
                'extraction_keywords': ['source', 'special condition', 'service condition'],
                'example': 'From compressor discharge / High vibration environment',
                'excel_cell': 'B14'
            },
            'shutoff_pressure': {
                'label': '11. Shut Off Pressure',
                'type': 'number',
                'unit': 'bar(g)',
                'required': False,
                'extraction_keywords': ['shutoff', 'shut off', 'shutoff pressure'],
                'example': '30',
                'excel_cell': 'B15'
            }
        }
    },
    
    # ========================================================================
    # SECTION 3: VALVE DETAILS
    # ========================================================================
    'valve_details': {
        'section_name': 'Valve Details',
        'fields': {
            'differential_pressure': {
                'label': '12. Differential Pressure',
                'type': 'number',
                'unit': 'bar',
                'required': False,
                'extraction_keywords': ['differential pressure', 'dp', 'delta p', 'pressure drop'],
                'example': '5',
                'excel_cell': 'B18'
            },
            'seat_leakage_class': {
                'label': '13.1 Seat Leakage Class',
                'type': 'select',
                'required': False,
                'options': ['Class I', 'Class II', 'Class III', 'Class IV', 'Class V', 'Class VI'],
                'extraction_keywords': ['leakage class', 'seat leakage', 'shutoff class'],
                'example': 'Class VI (Bubble Tight)',
                'excel_cell': 'B19'
            },
            'nace_compliant': {
                'label': '13.2 NACE Compliant',
                'type': 'boolean',
                'required': False,
                'options': ['Yes', 'No', 'N/A'],
                'extraction_keywords': ['nace', 'nace compliant', 'mr0175', 'mr0103'],
                'example': 'Yes',
                'excel_cell': 'D19'
            },
            'valve_type': {
                'label': 'Valve Type',
                'type': 'select',
                'required': True,
                'options': ['Ball Valve', 'Gate Valve', 'Globe Valve', 'Butterfly Valve', 'Plug Valve'],
                'extraction_keywords': ['valve type', 'type'],
                'example': 'Ball Valve',
                'excel_cell': 'B20'
            },
            'valve_size': {
                'label': 'Valve Size',
                'type': 'text',
                'required': True,
                'extraction_keywords': ['size', 'valve size', 'nominal size', 'dn', 'nps'],
                'pattern': r'\d+"|\d+mm|DN\d+|NPS\d+',
                'example': '2"',
                'excel_cell': 'D20'
            },
            'body_material': {
                'label': 'Body Material',
                'type': 'text',
                'required': False,
                'extraction_keywords': ['body material', 'body', 'material'],
                'options': ['Carbon Steel', 'Stainless Steel 316', 'Stainless Steel 304', 'Alloy Steel'],
                'example': 'Carbon Steel A216 WCB',
                'excel_cell': 'B21'
            },
            'trim_material': {
                'label': 'Trim Material',
                'type': 'text',
                'required': False,
                'extraction_keywords': ['trim material', 'trim', 'internals'],
                'example': 'Stainless Steel 316',
                'excel_cell': 'D21'
            },
            'seat_material': {
                'label': 'Seat Material',
                'type': 'text',
                'required': False,
                'extraction_keywords': ['seat material', 'seat'],
                'options': ['PTFE', 'Metal', 'Soft Seated', 'Stellite'],
                'example': 'PTFE / Metal Seated',
                'excel_cell': 'F21'
            },
            'end_connection': {
                'label': 'End Connection',
                'type': 'select',
                'required': False,
                'options': ['Flanged RF', 'Flanged RTJ', 'Butt Weld', 'Socket Weld', 'Threaded'],
                'extraction_keywords': ['end connection', 'connection type'],
                'example': 'Flanged RF',
                'excel_cell': 'B22'
            }
        }
    },
    
    # ========================================================================
    # SECTION 4: ACTUATOR DETAILS
    # ========================================================================
    'actuator_details': {
        'section_name': 'Actuator Details',
        'fields': {
            'fail_position': {
                'label': '14. Fail Position',
                'type': 'select',
                'required': True,
                'options': ['FC (Fail Close)', 'FO (Fail Open)', 'FL (Fail Last)', 'As-Is'],
                'extraction_keywords': ['fail position', 'fail safe', 'failure mode'],
                'example': 'FC (Fail Close)',
                'excel_cell': 'B25'
            },
            'valve_close_time': {
                'label': '15.1 Valve Close Time',
                'type': 'number',
                'unit': 'seconds',
                'required': False,
                'extraction_keywords': ['close time', 'closing time', 'stroke time close'],
                'example': '30',
                'excel_cell': 'B26'
            },
            'valve_open_time': {
                'label': '15.2 Valve Open Time',
                'type': 'number',
                'unit': 'seconds',
                'required': False,
                'extraction_keywords': ['open time', 'opening time', 'stroke time open'],
                'example': '30',
                'excel_cell': 'D26'
            },
            'actuator_type': {
                'label': 'Actuator Type',
                'type': 'select',
                'required': True,
                'options': ['Electric Motor', 'Pneumatic', 'Hydraulic', 'Manual'],
                'extraction_keywords': ['actuator type', 'actuator'],
                'example': 'Electric Motor',
                'excel_cell': 'B27'
            },
            'actuator_make': {
                'label': 'Actuator Make',
                'type': 'text',
                'required': False,
                'extraction_keywords': ['actuator make', 'manufacturer', 'make'],
                'example': 'Rotork / AUMA / Limitorque',
                'excel_cell': 'D27'
            },
            'operating_voltage': {
                'label': 'Operating Voltage',
                'type': 'text',
                'required': False,
                'extraction_keywords': ['voltage', 'supply voltage', 'power supply'],
                'example': '415V AC, 3-Phase, 50Hz',
                'excel_cell': 'B28'
            },
            'operating_current': {
                'label': 'Operating Current',
                'type': 'number',
                'unit': 'A',
                'required': False,
                'extraction_keywords': ['current', 'amperage', 'rated current'],
                'example': '5',
                'excel_cell': 'D28'
            },
            'power_rating': {
                'label': 'Power Rating',
                'type': 'number',
                'unit': 'kW',
                'required': False,
                'extraction_keywords': ['power rating', 'power', 'motor power'],
                'example': '2.2',
                'excel_cell': 'B29'
            },
            'operating_torque': {
                'label': 'Operating Torque',
                'type': 'number',
                'unit': 'Nm',
                'required': False,
                'extraction_keywords': ['torque', 'operating torque', 'breakout torque'],
                'example': '500',
                'excel_cell': 'D29'
            },
            'position_indicator': {
                'label': 'Position Indicator',
                'type': 'select',
                'required': False,
                'options': ['Visual', 'Electrical', 'Visual + Electrical', 'SCADA'],
                'extraction_keywords': ['position indicator', 'position feedback'],
                'example': 'Visual + Electrical',
                'excel_cell': 'B30'
            },
            'limit_switches': {
                'label': 'Limit Switches',
                'type': 'text',
                'required': False,
                'extraction_keywords': ['limit switch', 'limit switches', 'position switch'],
                'example': 'Open/Close with alarm',
                'excel_cell': 'D30'
            },
            'manual_override': {
                'label': 'Manual Override',
                'type': 'select',
                'required': False,
                'options': ['Handwheel', 'Gearbox', 'Chain Operated', 'None'],
                'extraction_keywords': ['manual override', 'handwheel', 'manual operation'],
                'example': 'Handwheel',
                'excel_cell': 'B31'
            }
        }
    }
}


# ============================================================================
# EXTRACTION PATTERNS AND RULES
# ============================================================================

EXTRACTION_PATTERNS = {
    'tag_number': {
        'regex': r'(MOV|XV|SDV|ESV|BDV|FCV|LCV|PCV|TCV)[-_]?\d{3,5}[A-Z]?',
        'priority': 1,
        'confidence_boost': 0.9  # High confidence for tag numbers
    },
    'pressure': {
        'regex': r'(\d+\.?\d*)\s*(bar|psi|kpa|mpa)',
        'priority': 2,
        'confidence_boost': 0.7
    },
    'temperature': {
        'regex': r'(\d+\.?\d*)\s*(°C|°F|C|F|deg)',
        'priority': 2,
        'confidence_boost': 0.7
    },
    'time': {
        'regex': r'(\d+\.?\d*)\s*(sec|second|s|min|minute)',
        'priority': 3,
        'confidence_boost': 0.6
    },
    'size': {
        'regex': r'(\d+\.?\d*)"|\d+mm|DN\d+|NPS\d+',
        'priority': 2,
        'confidence_boost': 0.8
    }
}


# ============================================================================
# DATA VALIDATION RULES
# ============================================================================

VALIDATION_RULES = {
    'operating_pressure_normal': {
        'min': 0,
        'max': 500,
        'warning': 'Operating pressure seems unusual. Please verify.'
    },
    'operating_temperature_normal': {
        'min': -50,
        'max': 600,
        'warning': 'Operating temperature seems unusual. Please verify.'
    },
    'design_pressure_normal': {
        'min': 0,
        'max': 1000,
        'warning': 'Design pressure seems unusual. Please verify.'
    },
    'valve_close_time': {
        'min': 1,
        'max': 300,
        'warning': 'Valve close time seems unusual. Typical range: 10-60 seconds.'
    },
    'valve_open_time': {
        'min': 1,
        'max': 300,
        'warning': 'Valve open time seems unusual. Typical range: 10-60 seconds.'
    }
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_all_fields():
    """Get all fields from all sections"""
    all_fields = {}
    for section_key, section_data in MOV_DATASHEET_FIELDS.items():
        all_fields.update(section_data['fields'])
    return all_fields


def get_required_fields():
    """Get list of required field keys"""
    required = []
    for section_key, section_data in MOV_DATASHEET_FIELDS.items():
        for field_key, field_config in section_data['fields'].items():
            if field_config.get('required', False):
                required.append(field_key)
    return required


def get_field_by_label(label):
    """Find field configuration by label"""
    for section_key, section_data in MOV_DATASHEET_FIELDS.items():
        for field_key, field_config in section_data['fields'].items():
            if field_config['label'] == label:
                return field_key, field_config
    return None, None


def validate_field_value(field_key, value):
    """Validate field value against rules"""
    if field_key not in VALIDATION_RULES:
        return True, None
    
    rule = VALIDATION_RULES[field_key]
    try:
        num_value = float(value)
        if num_value < rule['min'] or num_value > rule['max']:
            return False, rule['warning']
    except (ValueError, TypeError):
        pass
    
    return True, None
