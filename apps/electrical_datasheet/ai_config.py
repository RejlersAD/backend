"""
Soft-coded configuration for AI quality checking
Handles fallback strategies when OpenAI API is unavailable
"""

# AI Service Configuration
AI_CONFIG = {
    'enabled': True,  # Set to False to disable AI completely
    'fallback_enabled': True,  # Use rule-based fallback when AI fails
    'retry_attempts': 1,  # Number of retries for AI calls
    'timeout_seconds': 60,  # Timeout for AI requests
    
    # OpenAI specific settings
    'openai': {
        'model': 'gpt-4-turbo-preview',
        'temperature': 0.2,
        'max_tokens': 4000,
        'handle_quota_errors': True  # Gracefully handle quota exceeded errors
    },
    
    # Fallback behavior
    'fallback': {
        'method': 'rule_based',  # Options: 'rule_based', 'basic', 'none'
        'provide_basic_analysis': True,
        'check_critical_fields': True,
        'check_standards_compliance': True
    }
}

# Error codes that trigger fallback
FALLBACK_TRIGGER_ERRORS = [
    'insufficient_quota',
    'rate_limit_exceeded',
    'invalid_api_key',
    'service_unavailable',
    'timeout'
]

# Rule-based quality checking configuration
RULE_BASED_CHECKS = {
    'critical_fields': {
        'all': [
            'tag_number',
            'equipment_type',
            'description'
        ],
        'EM': [  # Motors
            'power_rating',
            'voltage',
            'frequency',
            'current',
            'power_factor',
            'efficiency',
            'speed',
            'poles'
        ],
        'EC': [  # Cables
            'cable_size',
            'conductor_material',
            'insulation_type',
            'voltage_rating',
            'temperature_rating',
            'core_configuration'
        ],
        'ET': [  # Transformers
            'power_rating',
            'primary_voltage',
            'secondary_voltage',
            'frequency',
            'impedance',
            'cooling_type'
        ],
        'ES': [  # Switchgear
            'rated_voltage',
            'rated_current',
            'short_circuit_rating',
            'frequency',
            'insulation_level'
        ],
        'EP': [  # Distribution Panels
            'rated_voltage',
            'rated_current',
            'short_circuit_rating',
            'number_of_ways',
            'busbar_rating'
        ],
        'LV': [  # LV Equipment
            'rated_voltage',
            'rated_current',
            'frequency',
            'protection_type'
        ],
        'EE': [  # Electrical Equipment
            'rated_voltage',
            'rated_current',
            'power_rating',
            'frequency'
        ],
        'ER': [  # Protection Relays
            'relay_type',
            'rated_voltage',
            'rated_current',
            'protection_functions'
        ]
    },
    
    'voltage_ranges': {
        'LV': {'min': 0, 'max': 1000, 'unit': 'V'},
        'MV': {'min': 1000, 'max': 35000, 'unit': 'V'},
        'HV': {'min': 35000, 'max': 500000, 'unit': 'V'}
    },
    
    'frequency_standards': [50, 60],  # Hz
    
    'power_factor_range': {'min': 0.7, 'max': 1.0},
    
    'efficiency_range': {'min': 70, 'max': 100},  # Percentage
    
    'temperature_ratings': {
        'common': [60, 75, 90, 105, 125, 155, 180, 200, 220],  # °C
        'cable_insulation': {
            'PVC': 70,
            'XLPE': 90,
            'EPR': 90,
            'Silicone': 180
        }
    }
}

# Quality scoring weights for rule-based checking
QUALITY_WEIGHTS = {
    'critical_fields_present': 40,  # 40% weight
    'field_values_valid': 30,        # 30% weight
    'standards_compliance': 20,      # 20% weight
    'data_completeness': 10          # 10% weight
}

# Compliance score thresholds
COMPLIANCE_THRESHOLDS = {
    'excellent': 90,
    'good': 75,
    'acceptable': 60,
    'poor': 40,
    'critical': 0
}


def get_quality_level(score):
    """
    Determine quality level from compliance score
    
    Args:
        score: Compliance score (0-100)
    
    Returns:
        str: Quality level
    """
    if score >= COMPLIANCE_THRESHOLDS['excellent']:
        return 'excellent'
    elif score >= COMPLIANCE_THRESHOLDS['good']:
        return 'good'
    elif score >= COMPLIANCE_THRESHOLDS['acceptable']:
        return 'acceptable'
    elif score >= COMPLIANCE_THRESHOLDS['poor']:
        return 'poor'
    else:
        return 'critical'


def should_use_fallback(error_code=None, error_message=None):
    """
    Determine if fallback should be used based on error
    
    Args:
        error_code: Error code from API
        error_message: Error message text
    
    Returns:
        bool: True if fallback should be used
    """
    if not AI_CONFIG['fallback_enabled']:
        return False
    
    if error_code and error_code in FALLBACK_TRIGGER_ERRORS:
        return True
    
    if error_message:
        error_lower = error_message.lower()
        for trigger in FALLBACK_TRIGGER_ERRORS:
            if trigger.replace('_', ' ') in error_lower:
                return True
    
    return False


def get_critical_fields_for_equipment(equipment_code):
    """
    Get critical fields for specific equipment type
    
    Args:
        equipment_code: Equipment type code (e.g., 'EM', 'EC')
    
    Returns:
        list: List of critical field names
    """
    all_fields = RULE_BASED_CHECKS['critical_fields']['all'].copy()
    
    if equipment_code in RULE_BASED_CHECKS['critical_fields']:
        all_fields.extend(RULE_BASED_CHECKS['critical_fields'][equipment_code])
    
    return all_fields


def validate_voltage_value(value, expected_range='LV'):
    """
    Validate voltage value against expected range
    
    Args:
        value: Voltage value (can be string with unit or number)
        expected_range: Expected voltage range (LV, MV, HV)
    
    Returns:
        tuple: (is_valid, message)
    """
    try:
        # Extract numeric value
        if isinstance(value, str):
            # Remove common units and convert to float
            numeric_value = float(''.join(c for c in value if c.isdigit() or c == '.'))
        else:
            numeric_value = float(value)
        
        # Get expected range
        voltage_range = RULE_BASED_CHECKS['voltage_ranges'].get(expected_range, {})
        min_val = voltage_range.get('min', 0)
        max_val = voltage_range.get('max', float('inf'))
        
        if min_val <= numeric_value <= max_val:
            return True, f"Voltage {numeric_value}V is within {expected_range} range"
        else:
            return False, f"Voltage {numeric_value}V is outside {expected_range} range ({min_val}-{max_val}V)"
    
    except (ValueError, TypeError):
        return False, f"Invalid voltage value: {value}"


def validate_frequency_value(value):
    """
    Validate frequency against standard frequencies
    
    Args:
        value: Frequency value
    
    Returns:
        tuple: (is_valid, message)
    """
    try:
        # Extract numeric value
        if isinstance(value, str):
            numeric_value = float(''.join(c for c in value if c.isdigit() or c == '.'))
        else:
            numeric_value = float(value)
        
        if numeric_value in RULE_BASED_CHECKS['frequency_standards']:
            return True, f"Frequency {numeric_value}Hz is standard"
        else:
            return False, f"Frequency {numeric_value}Hz is non-standard (expected {RULE_BASED_CHECKS['frequency_standards']})"
    
    except (ValueError, TypeError):
        return False, f"Invalid frequency value: {value}"


def validate_power_factor(value):
    """
    Validate power factor value
    
    Args:
        value: Power factor value
    
    Returns:
        tuple: (is_valid, message)
    """
    try:
        numeric_value = float(value)
        pf_range = RULE_BASED_CHECKS['power_factor_range']
        
        if pf_range['min'] <= numeric_value <= pf_range['max']:
            return True, f"Power factor {numeric_value} is within acceptable range"
        else:
            return False, f"Power factor {numeric_value} is outside acceptable range ({pf_range['min']}-{pf_range['max']})"
    
    except (ValueError, TypeError):
        return False, f"Invalid power factor value: {value}"
