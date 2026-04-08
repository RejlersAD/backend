"""
Electrical Equipment Types Configuration
Soft-coded configuration for all electrical datasheet types based on reference documents
Aligned with ADNOC standards and common electrical engineering practices
"""

EQUIPMENT_TYPES_CONFIG = [
    {
        'id': 'motor',
        'name': 'Motor',
        'code': 'EM',
        'description': 'Electric motors - AC/DC, induction, synchronous motors',
        'icon': '⚡',
        'category': 'Rotating Equipment',
        'standards': [
            'IEC 60034 - Rotating electrical machines',
            'NEMA MG 1 - Motors and Generators',
            'API 541 - Form-wound squirrel-cage motors',
            'IEEE 841 - Severe duty motors',
            'ADNOC-AGES-GL-002 - Electrical Equipment Guidelines'
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'service_description', 'location', 'manufacturer', 'model_number']
            },
            {
                'name': 'Motor Specifications',
                'fields': ['power_rating', 'voltage', 'frequency', 'current', 'power_factor', 'efficiency', 'speed', 'poles']
            },
            {
                'name': 'Construction Details',
                'fields': ['frame_size', 'enclosure_type', 'insulation_class', 'duty_type', 'mounting_arrangement']
            },
            {
                'name': 'Temperature & Environment',
                'fields': ['ambient_temperature', 'temperature_rise', 'altitude', 'area_classification']
            },
            {
                'name': 'Protection & Control',
                'fields': ['protection_type', 'starting_method', 'control_voltage', 'motor_starter_type']
            }
        ],
        'critical_fields': ['power_rating', 'voltage', 'current', 'speed', 'enclosure_type', 'area_classification'],
        'validation_rules': {
            'power_rating': {'min': 0.1, 'max': 50000, 'unit': 'kW'},
            'voltage': {'allowed_values': [220, 380, 400, 415, 440, 660, 690, 3300, 6600, 11000, 13800]},
            'frequency': {'allowed_values': [50, 60]},
            'efficiency': {'min': 70, 'max': 99.5},
            'power_factor': {'min': 0.6, 'max': 1.0}
        }
    },
    {
        'id': 'cable',
        'name': 'Power Cable',
        'code': 'EC',
        'description': 'Power cables - LV, MV, HV cables and cable systems',
        'icon': '🔌',
        'category': 'Cables & Wiring',
        'standards': [
            'IEC 60502 - Power cables with extruded insulation',
            'BS 6346 - PVC insulated cables',
            'BS 5467 - Armoured cables',
            'IEEE 1202 - Flame testing',
            'ADNOC-AGES-CA-001 - Cable Selection & Installation'
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'cable_route', 'from_location', 'to_location', 'cable_length']
            },
            {
                'name': 'Cable Specifications',
                'fields': ['cable_type', 'conductor_material', 'conductor_size', 'number_of_cores', 'voltage_rating', 'current_rating']
            },
            {
                'name': 'Insulation & Protection',
                'fields': ['insulation_material', 'insulation_thickness', 'sheath_material', 'armour_type', 'screen_type']
            },
            {
                'name': 'Installation Details',
                'fields': ['installation_method', 'cable_tray_type', 'depth_of_burial', 'ambient_temperature', 'soil_thermal_resistivity']
            },
            {
                'name': 'Fire & Safety',
                'fields': ['fire_resistance_rating', 'flame_retardant', 'low_smoke_zero_halogen', 'area_classification']
            }
        ],
        'critical_fields': ['conductor_size', 'number_of_cores', 'voltage_rating', 'current_rating', 'cable_type'],
        'validation_rules': {
            'voltage_rating': {'allowed_values': [300, 600, 1000, 1900, 3300, 6600, 11000, 33000, 66000, 132000]},
            'conductor_material': {'allowed_values': ['Copper', 'Aluminium', 'Copper Clad Aluminium']},
            'number_of_cores': {'min': 1, 'max': 61}
        }
    },
    {
        'id': 'transformer',
        'name': 'Transformer',
        'code': 'ET',
        'description': 'Power transformers - distribution, dry-type, oil-filled',
        'icon': '🔋',
        'category': 'Power Distribution',
        'standards': [
            'IEC 60076 - Power transformers',
            'IEEE C57.12.00 - General requirements',
            'IEC 60726 - Dry-type transformers',
            'ANSI/IEEE C57.12.01 - Dry-type transformers',
            'ADNOC-AGES-TR-001 - Transformer Specifications'
        ],
        'supported_documents': [
            {
                'type': 'transformer_sizing_calculation',
                'label': 'Transformer Sizing Calculation (Power and Distribution)',
                'description': 'Comprehensive transformer sizing calculation document including MV/LV calculations, criteria, and formulas',
                'required': True
            }
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'service_description', 'location', 'manufacturer', 'serial_number']
            },
            {
                'name': 'Ratings',
                'fields': ['rated_power', 'primary_voltage', 'secondary_voltage', 'frequency', 'number_of_phases', 'connection_type']
            },
            {
                'name': 'Impedance & Losses',
                'fields': ['impedance_voltage', 'no_load_losses', 'load_losses', 'efficiency']
            },
            {
                'name': 'Construction',
                'fields': ['transformer_type', 'cooling_type', 'insulation_class', 'vector_group', 'tap_changer']
            },
            {
                'name': 'Protection',
                'fields': ['protection_class', 'temperature_monitoring', 'buchholz_relay', 'surge_arresters']
            }
        ],
        'critical_fields': ['rated_power', 'primary_voltage', 'secondary_voltage', 'impedance_voltage', 'transformer_type'],
        'validation_rules': {
            'rated_power': {'min': 5, 'max': 100000, 'unit': 'kVA'},
            'frequency': {'allowed_values': [50, 60]},
            'number_of_phases': {'allowed_values': [1, 3]},
            'efficiency': {'min': 90, 'max': 99.5}
        }
    },
    {
        'id': 'switchgear',
        'name': 'Switchgear',
        'code': 'ES',
        'description': 'Switchgear assemblies - MV, LV, AIS, GIS',
        'icon': '🔧',
        'category': 'Power Distribution',
        'standards': [
            'IEC 62271 - High-voltage switchgear',
            'IEC 61439 - Low-voltage switchgear',
            'IEEE C37 - Power switchgear',
            'ANSI C37.20 - Metal-enclosed switchgear',
            'ADNOC-AGES-SW-001 - Switchgear Requirements'
        ],
        'supported_documents': [
            {
                'type': 'sld_11kv_switchgear',
                'label': 'SLD for 11KV Switchgear',
                'description': 'Comprehensive Single Line Diagram for 11KV switchgear including equipment schedule and protection settings',
                'required': True
            }
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'service_description', 'location', 'manufacturer', 'type_designation']
            },
            {
                'name': 'Electrical Ratings',
                'fields': ['rated_voltage', 'rated_current', 'short_circuit_current', 'frequency', 'number_of_phases']
            },
            {
                'name': 'Construction',
                'fields': ['switchgear_type', 'enclosure_type', 'busbar_arrangement', 'circuit_breaker_type', 'insulation_medium']
            },
            {
                'name': 'Protection & Metering',
                'fields': ['protection_relay', 'metering_class', 'ct_ratio', 'vt_ratio', 'interlocking_scheme']
            },
            {
                'name': 'Environmental',
                'fields': ['ip_rating', 'arc_classification', 'ambient_temperature', 'area_classification']
            }
        ],
        'critical_fields': ['rated_voltage', 'rated_current', 'short_circuit_current', 'switchgear_type', 'ip_rating'],
        'validation_rules': {
            'rated_voltage': {'allowed_values': [400, 690, 3300, 6600, 11000, 13800, 33000, 66000, 132000]},
            'frequency': {'allowed_values': [50, 60]},
            'number_of_phases': {'allowed_values': [1, 3]}
        }
    },
    {
        'id': 'panel',
        'name': 'Distribution Panel',
        'code': 'EP',
        'description': 'Distribution panels - MDB, SDB, PDB, MCC',
        'icon': '📦',
        'category': 'Power Distribution',
        'standards': [
            'IEC 61439-1 - Low-voltage switchgear assemblies',
            'IEC 61439-2 - Power switchgear assemblies',
            'UL 508A - Industrial control panels',
            'NEMA 250 - Enclosures for electrical equipment',
            'ADNOC-AGES-PN-001 - Panel Requirements'
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'panel_name', 'location', 'manufacturer', 'panel_type']
            },
            {
                'name': 'Electrical Ratings',
                'fields': ['main_busbar_rating', 'incoming_supply_voltage', 'frequency', 'number_of_phases', 'neutral_size']
            },
            {
                'name': 'Construction',
                'fields': ['enclosure_material', 'ip_rating', 'color', 'busbar_material', 'number_of_outgoing_ways']
            },
            {
                'name': 'Protection Devices',
                'fields': ['main_circuit_breaker', 'residual_current_device', 'surge_protection_device', 'metering']
            },
            {
                'name': 'Environmental',
                'fields': ['ambient_temperature', 'humidity', 'area_classification', 'altitude']
            }
        ],
        'critical_fields': ['main_busbar_rating', 'incoming_supply_voltage', 'ip_rating', 'panel_type'],
        'validation_rules': {
            'incoming_supply_voltage': {'allowed_values': [220, 230, 380, 400, 415, 440]},
            'frequency': {'allowed_values': [50, 60]},
            'number_of_phases': {'allowed_values': [1, 3]}
        }
    },
    {
        'id': 'lv_equipment',
        'name': 'LV Equipment',
        'code': 'LV',
        'description': 'Low voltage electrical equipment and accessories',
        'icon': '⚙️',
        'category': 'Low Voltage',
        'standards': [
            'IEC 60947 - Low-voltage switchgear and control gear',
            'IEC 60898 - Circuit-breakers for overcurrent protection',
            'IEC 61008 - Residual current circuit-breakers',
            'IEC 60269 - Low-voltage fuses',
            'ADNOC-AGES-LV-001 - LV Equipment Standards'
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'equipment_description', 'location', 'manufacturer', 'model_number']
            },
            {
                'name': 'Electrical Ratings',
                'fields': ['rated_voltage', 'rated_current', 'breaking_capacity', 'frequency', 'number_of_poles']
            },
            {
                'name': 'Technical Details',
                'fields': ['equipment_type', 'tripping_characteristic', 'coordination_class', 'mounting_type']
            },
            {
                'name': 'Compliance',
                'fields': ['standards_compliance', 'type_test_certificate', 'ip_rating', 'area_classification']
            }
        ],
        'critical_fields': ['rated_voltage', 'rated_current', 'breaking_capacity', 'equipment_type'],
        'validation_rules': {
            'rated_voltage': {'allowed_values': [110, 220, 230, 240, 380, 400, 415, 440]},
            'frequency': {'allowed_values': [50, 60]},
            'number_of_poles': {'allowed_values': [1, 2, 3, 4]}
        }
    },
    {
        'id': 'electrical_equipment',
        'name': 'Electrical Equipment (General)',
        'code': 'EE',
        'description': 'General electrical equipment and apparatus',
        'icon': '🔩',
        'category': 'General Equipment',
        'standards': [
            'IEC 60204 - Electrical equipment of machines',
            'IEC 60335 - Household electrical appliances',
            'IEC 60950 - Information technology equipment',
            'NEMA ICS - Industrial control standards',
            'ADNOC-AGES-EE-001 - Electrical Equipment Guidelines'
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'equipment_name', 'equipment_function', 'location', 'manufacturer']
            },
            {
                'name': 'Electrical Specifications',
                'fields': ['supply_voltage', 'rated_power', 'rated_current', 'frequency', 'number_of_phases']
            },
            {
                'name': 'Construction & Environment',
                'fields': ['enclosure_type', 'ip_rating', 'material', 'weight', 'dimensions']
            },
            {
                'name': 'Safety & Compliance',
                'fields': ['safety_certification', 'area_classification', 'protection_type', 'earthing_requirements']
            }
        ],
        'critical_fields': ['supply_voltage', 'rated_current', 'ip_rating'],
        'validation_rules': {
            'supply_voltage': {'allowed_values': [110, 220, 230, 380, 400, 415, 440, 660, 690]},
            'frequency': {'allowed_values': [50, 60]}
        }
    },
    {
        'id': 'relay_protection',
        'name': 'Protection Relay',
        'code': 'ER',
        'description': 'Protection relays and devices',
        'icon': '🛡️',
        'category': 'Protection & Control',
        'standards': [
            'IEC 60255 - Measuring relays and protection equipment',
            'IEEE C37.90 - Relay and relay systems',
            'IEC 61850 - Substation automation',
            'IEEE C37.2 - Electrical power system device function numbers',
            'ADNOC-AGES-PR-001 - Protection System Requirements'
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'relay_function', 'protected_equipment', 'location', 'manufacturer']
            },
            {
                'name': 'Relay Settings',
                'fields': ['relay_type', 'protection_function', 'pickup_setting', 'time_delay', 'operating_voltage']
            },
            {
                'name': 'CT & VT Information',
                'fields': ['ct_primary', 'ct_secondary', 'ct_ratio', 'vt_primary', 'vt_secondary', 'vt_ratio']
            },
            {
                'name': 'Communication & Control',
                'fields': ['communication_protocol', 'auxiliary_supply', 'contact_rating', 'testing_requirements']
            }
        ],
        'critical_fields': ['relay_function', 'protection_function', 'operating_voltage', 'relay_type'],
        'validation_rules': {
            'operating_voltage': {'allowed_values': [24, 48, 110, 220]},
            'ct_secondary': {'allowed_values': [1, 5]},
            'vt_secondary': {'allowed_values': [110, 115, 120]}
        }
    },
    {
        'id': 'ups',
        'name': 'UPS System',
        'code': 'EU',
        'description': 'Uninterruptible Power Supply systems',
        'icon': '🔋',
        'category': 'Power Quality',
        'standards': [
            'IEC 62040 - UPS systems',
            'IEEE 946 - UPS recommended practice',
            'EN 50091 - UPS systems',
            'ADNOC-AGES-UP-001 - UPS Requirements'
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'system_name', 'location', 'manufacturer', 'model']
            },
            {
                'name': 'Ratings',
                'fields': ['rated_power', 'input_voltage', 'output_voltage', 'frequency', 'efficiency']
            },
            {
                'name': 'Configuration',
                'fields': ['ups_type', 'topology', 'redundancy', 'backup_time', 'battery_type']
            },
            {
                'name': 'Performance',
                'fields': ['voltage_regulation', 'frequency_regulation', 'waveform', 'overload_capability']
            }
        ],
        'critical_fields': ['rated_power', 'ups_type', 'backup_time', 'redundancy'],
        'validation_rules': {
            'rated_power': {'min': 0.5, 'max': 10000, 'unit': 'kVA'},
            'efficiency': {'min': 85, 'max': 99}
        }
    },
    {
        'id': 'battery',
        'name': 'Battery System',
        'code': 'EB',
        'description': 'Battery systems for backup and UPS',
        'icon': '🔋',
        'category': 'Power Quality',
        'standards': [
            'IEEE 450 - Maintenance, testing of lead-acid batteries',
            'IEEE 485 - Sizing lead-acid batteries',
            'IEC 60896 - Stationary lead-acid batteries',
            'ADNOC-AGES-BA-001 - Battery System Standards'
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'system_description', 'location', 'manufacturer', 'model']
            },
            {
                'name': 'Battery Specifications',
                'fields': ['battery_type', 'nominal_voltage', 'capacity', 'number_of_cells', 'backup_duration']
            },
            {
                'name': 'Charger Details',
                'fields': ['charger_type', 'input_voltage', 'charging_current', 'float_voltage', 'equalizing_voltage']
            },
            {
                'name': 'Environmental',
                'fields': ['ambient_temperature', 'ventilation_requirements', 'seismic_qualification']
            }
        ],
        'critical_fields': ['battery_type', 'nominal_voltage', 'capacity', 'backup_duration'],
        'validation_rules': {
            'nominal_voltage': {'allowed_values': [12, 24, 48, 110, 125, 220, 250]},
            'capacity': {'min': 10, 'max': 10000, 'unit': 'Ah'}
        }
    },
    {
        'id': 'edg',
        'name': 'Emergency Diesel Generator',
        'code': 'EG',
        'description': 'Emergency diesel generators - standby power systems',
        'icon': '⚙️',
        'category': 'Power Generation',
        'standards': [
            'IEC 60034 - Rotating electrical machines',
            'ISO 8528 - Reciprocating internal combustion engine generator sets',
            'NFPA 110 - Emergency and standby power systems',
            'ADNOC-AGES-GE-001 - Generator Requirements'
        ],
        'supported_documents': [
            {
                'type': 'edg_sizing_calculation',
                'label': 'Emergency Diesel Generator (EDG) Sizing Calculation',
                'description': 'Comprehensive EDG sizing calculation document including load list and power requirements',
                'required': True
            }
        ],
        'sections': [
            {
                'name': 'General Information',
                'fields': ['tag_number', 'generator_purpose', 'location', 'manufacturer', 'model']
            },
            {
                'name': 'Electrical Ratings',
                'fields': ['rated_power', 'voltage', 'current', 'frequency', 'power_factor', 'number_of_phases']
            },
            {
                'name': 'Engine Details',
                'fields': ['engine_manufacturer', 'engine_model', 'fuel_type', 'fuel_consumption', 'cooling_type']
            },
            {
                'name': 'Control & Protection',
                'fields': ['control_panel', 'starting_method', 'protection_system', 'auto_transfer_switch']
            }
        ],
        'critical_fields': ['rated_power', 'voltage', 'frequency', 'fuel_type'],
        'validation_rules': {
            'rated_power': {'min': 10, 'max': 10000, 'unit': 'kVA'},
            'voltage': {'allowed_values': [380, 400, 415, 440, 6600, 11000, 13800]},
            'frequency': {'allowed_values': [50, 60]}
        }
    }
]


def get_equipment_type_by_code(code):
    """Get equipment type configuration by code"""
    for eq_type in EQUIPMENT_TYPES_CONFIG:
        if eq_type['code'].upper() == code.upper():
            return eq_type
    return None


def get_equipment_type_by_id(equipment_id):
    """Get equipment type configuration by ID"""
    for eq_type in EQUIPMENT_TYPES_CONFIG:
        if eq_type['id'] == equipment_id:
            return eq_type
    return None


def get_all_equipment_codes():
    """Get all equipment type codes"""
    return [eq_type['code'] for eq_type in EQUIPMENT_TYPES_CONFIG]


def get_equipment_types_by_category(category):
    """Get all equipment types in a specific category"""
    return [eq_type for eq_type in EQUIPMENT_TYPES_CONFIG if eq_type['category'] == category]


def get_critical_fields(equipment_type_id):
    """Get critical fields for an equipment type"""
    eq_type = get_equipment_type_by_id(equipment_type_id)
    return eq_type['critical_fields'] if eq_type else []


def get_validation_rules(equipment_type_id):
    """Get validation rules for an equipment type"""
    eq_type = get_equipment_type_by_id(equipment_type_id)
    return eq_type.get('validation_rules', {}) if eq_type else {}


def validate_field(equipment_type_id, field_name, value):
    """Validate a field value against equipment type rules"""
    rules = get_validation_rules(equipment_type_id)
    
    if field_name not in rules:
        return True, None  # No rules, pass validation
    
    field_rules = rules[field_name]
    
    # Check min/max
    if 'min' in field_rules:
        try:
            if float(value) < field_rules['min']:
                return False, f"Value must be at least {field_rules['min']}"
        except (ValueError, TypeError):
            pass
    
    if 'max' in field_rules:
        try:
            if float(value) > field_rules['max']:
                return False, f"Value must not exceed {field_rules['max']}"
        except (ValueError, TypeError):
            pass
    
    # Check allowed values
    if 'allowed_values' in field_rules:
        try:
            if float(value) not in field_rules['allowed_values'] and value not in field_rules['allowed_values']:
                return False, f"Value must be one of: {', '.join(map(str, field_rules['allowed_values']))}"
        except (ValueError, TypeError):
            if str(value) not in [str(v) for v in field_rules['allowed_values']]:
                return False, f"Value must be one of: {', '.join(map(str, field_rules['allowed_values']))}"
    
    return True, None
