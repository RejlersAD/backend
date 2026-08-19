"""
Equipment Type Configurations
Soft-coded equipment type definitions with all fields, validations, and calculations
"""

# Control Valve Configuration
CONTROL_VALVE_CONFIG = {
    "id": "control_valve",
    "code": "control_valve",
    "name": "Control Valve",
    "icon": "🔧",
    "version": "3.0",
    "category": "valves",
    "description": "Process control valves with actuators and positioners",
    
    "sections": [
        {
            "id": "identification",
            "name": "Equipment Identification",
            "order": 1,
            "required": True,
            "fields": [
                {
                    "id": "tag_number",
                    "label": "Tag Number",
                    "type": "text",
                    "required": True,
                    "pattern": r"^\d{3}-[A-Z]{2}-\d{4}$",
                    "placeholder": "604-FV-0103",
                    "helpText": "Format: XXX-YY-ZZZZ",
                    "aiExtraction": {
                        "sources": ["pid", "equipment_list"],
                        "confidenceThreshold": 0.95
                    }
                },
                {
                    "id": "service",
                    "label": "Service Description",
                    "type": "textarea",
                    "required": True,
                    "placeholder": "e.g., PRODUCED WATER FROM 604-P-0104A/B",
                    "aiExtraction": {
                        "sources": ["pid", "process_description"],
                        "confidenceThreshold": 0.85
                    }
                },
                {
                    "id": "location",
                    "label": "Location",
                    "type": "text",
                    "required": True,
                    "placeholder": "e.g., MINIMUM FLOW",
                    "aiExtraction": {
                        "sources": ["pid", "plot_plan"],
                        "confidenceThreshold": 0.90
                    }
                }
            ]
        },
        {
            "id": "operating_conditions",
            "name": "Operating Conditions",
            "order": 2,
            "required": True,
            "fields": [
                {
                    "id": "flow_rate_normal",
                    "label": "Flow Rate - Normal",
                    "type": "number",
                    "required": True,
                    "unit": "m³/h",
                    "units": ["m³/h", "kg/h", "gpm", "lb/h"],
                    "min": 0,
                    "aiExtraction": {
                        "sources": ["pid", "process_data", "simulation"],
                        "confidenceThreshold": 0.90
                    }
                },
                {
                    "id": "flow_rate_max",
                    "label": "Flow Rate - Maximum",
                    "type": "number",
                    "required": True,
                    "unit": "m³/h",
                    "units": ["m³/h", "kg/h", "gpm", "lb/h"],
                    "min": 0
                },
                {
                    "id": "flow_rate_min",
                    "label": "Flow Rate - Minimum",
                    "type": "number",
                    "required": False,
                    "unit": "m³/h",
                    "units": ["m³/h", "kg/h", "gpm", "lb/h"],
                    "min": 0
                },
                {
                    "id": "pressure_operating",
                    "label": "Operating Pressure",
                    "type": "number",
                    "required": True,
                    "unit": "barg",
                    "units": ["barg", "bar abs", "psi", "kPa"],
                    "min": 0,
                    "aiExtraction": {
                        "sources": ["pid", "process_data"],
                        "confidenceThreshold": 0.92
                    }
                },
                {
                    "id": "pressure_design",
                    "label": "Design Pressure",
                    "type": "number",
                    "required": True,
                    "unit": "barg",
                    "units": ["barg", "bar abs", "psi", "kPa"],
                    "min": 0,
                    "aiExtraction": {
                        "sources": ["line_list", "piping_spec"],
                        "calculation": "apply_design_margin",
                        "confidenceThreshold": 0.90
                    }
                },
                {
                    "id": "temperature_operating",
                    "label": "Operating Temperature",
                    "type": "number",
                    "required": True,
                    "unit": "°C",
                    "units": ["°C", "°F", "K"],
                    "aiExtraction": {
                        "sources": ["pid", "process_data"],
                        "confidenceThreshold": 0.90
                    }
                },
                {
                    "id": "temperature_design_min",
                    "label": "Design Temperature - Min",
                    "type": "number",
                    "required": True,
                    "unit": "°C",
                    "units": ["°C", "°F", "K"]
                },
                {
                    "id": "temperature_design_max",
                    "label": "Design Temperature - Max",
                    "type": "number",
                    "required": True,
                    "unit": "°C",
                    "units": ["°C", "°F", "K"]
                },
                {
                    "id": "density",
                    "label": "Fluid Density",
                    "type": "number",
                    "required": True,
                    "unit": "kg/m³",
                    "units": ["kg/m³", "lb/ft³"],
                    "min": 0,
                    "aiExtraction": {
                        "sources": ["process_simulation", "fluid_database"],
                        "confidenceThreshold": 0.85
                    }
                },
                {
                    "id": "viscosity",
                    "label": "Fluid Viscosity",
                    "type": "number",
                    "required": True,
                    "unit": "cP",
                    "units": ["cP", "Pa·s"],
                    "min": 0,
                    "aiExtraction": {
                        "sources": ["process_simulation", "fluid_database"],
                        "confidenceThreshold": 0.80
                    }
                },
                {
                    "id": "vapor_pressure",
                    "label": "Vapor Pressure",
                    "type": "number",
                    "required": True,
                    "unit": "barg",
                    "units": ["barg", "psi"],
                    "min": 0,
                    "aiExtraction": {
                        "sources": ["process_simulation", "steam_tables"],
                        "confidenceThreshold": 0.85
                    }
                }
            ]
        },
        {
            "id": "control_parameters",
            "name": "Control & Sizing Parameters",
            "order": 3,
            "required": True,
            "fields": [
                {
                    "id": "fail_action",
                    "label": "Fail Action",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "FC", "label": "Fail Closed (FC)"},
                        {"value": "FO", "label": "Fail Open (FO)"},
                        {"value": "FL", "label": "Fail Last (FL)"}
                    ],
                    "aiExtraction": {
                        "sources": ["pid", "cause_effect_matrix", "safety_study"],
                        "logic": "determine_safe_failure_mode",
                        "confidenceThreshold": 0.95
                    }
                },
                {
                    "id": "cv_required",
                    "label": "Cv Required",
                    "type": "number",
                    "required": True,
                    "calculated": True,
                    "readonly": True,
                    "min": 0.1,
                    "helpText": "Auto-calculated from flow and pressure drop"
                },
                {
                    "id": "pressure_drop",
                    "label": "Pressure Drop",
                    "type": "number",
                    "required": True,
                    "unit": "bar",
                    "units": ["bar", "psi", "kPa"],
                    "min": 0,
                    "aiExtraction": {
                        "sources": ["process_simulation", "hydraulic_calc"],
                        "confidenceThreshold": 0.88
                    }
                },
                {
                    "id": "noise_level",
                    "label": "Predicted Noise Level",
                    "type": "number",
                    "unit": "dBA",
                    "calculated": True,
                    "readonly": True,
                    "helpText": "Calculated per IEC 60534-8-3"
                },
                {
                    "id": "cavitation_index",
                    "label": "Cavitation Index (σ)",
                    "type": "number",
                    "calculated": True,
                    "readonly": True,
                    "helpText": "Calculated per IEC 60534"
                },
                {
                    "id": "valve_characteristic",
                    "label": "Valve Characteristic",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "linear", "label": "Linear"},
                        {"value": "equal_percentage", "label": "Equal Percentage"},
                        {"value": "quick_opening", "label": "Quick Opening"}
                    ],
                    "default": "equal_percentage"
                }
            ]
        },
        {
            "id": "materials",
            "name": "Materials of Construction",
            "order": 4,
            "required": True,
            "fields": [
                {
                    "id": "body_material",
                    "label": "Body Material",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "CS", "label": "Carbon Steel (CS)"},
                        {"value": "SS316", "label": "Stainless Steel 316 (SS316)"},
                        {"value": "SS316L", "label": "Stainless Steel 316L (SS316L)"},
                        {"value": "duplex", "label": "Duplex Stainless Steel"},
                        {"value": "alloy625", "label": "Alloy 625"},
                        {"value": "bronze", "label": "Bronze"}
                    ],
                    "aiExtraction": {
                        "sources": ["material_selection", "corrosion_study"],
                        "confidenceThreshold": 0.90
                    }
                },
                {
                    "id": "trim_material",
                    "label": "Trim Material",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "SS316", "label": "Stainless Steel 316"},
                        {"value": "stellite", "label": "Stellite"},
                        {"value": "tungsten_carbide", "label": "Tungsten Carbide"},
                        {"value": "ceramic", "label": "Ceramic"}
                    ],
                    "aiExtraction": {
                        "sources": ["service_conditions"],
                        "confidenceThreshold": 0.85
                    }
                }
            ]
        },
        {
            "id": "connections",
            "name": "Connection Details",
            "order": 5,
            "required": True,
            "fields": [
                {
                    "id": "valve_size",
                    "label": "Valve Size",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "1/2", "label": '1/2"'},
                        {"value": "3/4", "label": '3/4"'},
                        {"value": "1", "label": '1"'},
                        {"value": "1-1/2", "label": '1-1/2"'},
                        {"value": "2", "label": '2"'},
                        {"value": "3", "label": '3"'},
                        {"value": "4", "label": '4"'},
                        {"value": "6", "label": '6"'},
                        {"value": "8", "label": '8"'},
                        {"value": "10", "label": '10"'},
                        {"value": "12", "label": '12"'}
                    ],
                    "aiExtraction": {
                        "sources": ["line_list", "pid"],
                        "confidenceThreshold": 0.95
                    }
                },
                {
                    "id": "pressure_rating",
                    "label": "Pressure Rating",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "class150", "label": "Class 150"},
                        {"value": "class300", "label": "Class 300"},
                        {"value": "class600", "label": "Class 600"},
                        {"value": "class900", "label": "Class 900"},
                        {"value": "class1500", "label": "Class 1500"},
                        {"value": "class2500", "label": "Class 2500"}
                    ],
                    "aiExtraction": {
                        "sources": ["line_list", "piping_class"],
                        "confidenceThreshold": 0.95
                    }
                },
                {
                    "id": "end_connections",
                    "label": "End Connections",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "RF", "label": "Raised Face (RF)"},
                        {"value": "RTJ", "label": "Ring Type Joint (RTJ)"},
                        {"value": "BW", "label": "Butt Weld (BW)"},
                        {"value": "SW", "label": "Socket Weld (SW)"},
                        {"value": "threaded", "label": "Threaded"}
                    ],
                    "aiExtraction": {
                        "sources": ["material_spec", "piping_class"],
                        "confidenceThreshold": 0.92
                    }
                }
            ]
        },
        {
            "id": "actuator",
            "name": "Actuator & Accessories",
            "order": 6,
            "required": True,
            "fields": [
                {
                    "id": "actuator_type",
                    "label": "Actuator Type",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "pneumatic_spring", "label": "Pneumatic with Spring Return"},
                        {"value": "pneumatic_double", "label": "Pneumatic Double Acting"},
                        {"value": "electric", "label": "Electric"},
                        {"value": "hydraulic", "label": "Hydraulic"}
                    ],
                    "default": "pneumatic_spring",
                    "aiExtraction": {
                        "sources": ["instrument_air_spec", "safety_requirements"],
                        "confidenceThreshold": 0.88
                    }
                },
                {
                    "id": "air_supply_pressure",
                    "label": "Air Supply Pressure",
                    "type": "number",
                    "unit": "barg",
                    "units": ["barg", "psi"],
                    "default": 6,
                    "conditionalRequired": {
                        "field": "actuator_type",
                        "values": ["pneumatic_spring", "pneumatic_double"]
                    }
                },
                {
                    "id": "positioner_type",
                    "label": "Positioner Type",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "none", "label": "None"},
                        {"value": "pneumatic", "label": "Pneumatic"},
                        {"value": "electropneumatic", "label": "Electro-Pneumatic (I/P)"},
                        {"value": "digital", "label": "Digital/Smart"}
                    ],
                    "default": "digital"
                },
                {
                    "id": "accessories",
                    "label": "Accessories",
                    "type": "multi_select",
                    "options": [
                        {"value": "solenoid", "label": "Solenoid Valve"},
                        {"value": "limit_switch", "label": "Limit Switches"},
                        {"value": "position_transmitter", "label": "Position Transmitter"},
                        {"value": "filter_regulator", "label": "Filter Regulator"},
                        {"value": "quick_exhaust", "label": "Quick Exhaust Valve"},
                        {"value": "volume_booster", "label": "Volume Booster"}
                    ]
                }
            ]
        },
        {
            "id": "standards",
            "name": "Standards & References",
            "order": 7,
            "required": True,
            "fields": [
                {
                    "id": "design_codes",
                    "label": "Design Codes",
                    "type": "multi_select",
                    "required": True,
                    "options": [
                        {"value": "ASME_B16.34", "label": "ASME B16.34"},
                        {"value": "API_6D", "label": "API 6D"},
                        {"value": "BS_1873", "label": "BS 1873"},
                        {"value": "IEC_60534", "label": "IEC 60534"},
                        {"value": "ISA_75.01", "label": "ISA-75.01"},
                        {"value": "ADNOC_DEP", "label": "ADNOC DEP 31.40.10.31-Gen"}
                    ],
                    "default": ["ASME_B16.34", "IEC_60534"]
                },
                {
                    "id": "test_standard",
                    "label": "Test Standard",
                    "type": "select",
                    "required": True,
                    "options": [
                        {"value": "API_598", "label": "API 598"},
                        {"value": "API_6D", "label": "API 6D"},
                        {"value": "BS_6755", "label": "BS 6755"},
                        {"value": "ISO_5208", "label": "ISO 5208"}
                    ],
                    "default": "API_598"
                }
            ]
        }
    ],
    
    "calculations": [
        {
            "id": "cv_calculation",
            "name": "Cv Calculation",
            "formula": "liquid_cv",
            "inputs": ["flow_rate_normal", "density", "pressure_drop"],
            "output": "cv_required"
        },
        {
            "id": "cavitation_index",
            "name": "Cavitation Index",
            "formula": "cavitation_sigma",
            "inputs": ["pressure_operating", "pressure_drop", "vapor_pressure"],
            "output": "cavitation_index"
        },
        {
            "id": "noise_prediction",
            "name": "Noise Prediction",
            "formula": "iec_60534_noise",
            "inputs": ["cv_required", "pressure_drop", "flow_rate_normal", "valve_characteristic"],
            "output": "noise_level"
        }
    ],
    
    "validationRules": [
        {
            "id": "pressure_design_margin",
            "check": "pressure_design >= pressure_operating * 1.1",
            "severity": "error",
            "message": "Design pressure must be at least 10% above operating pressure"
        },
        {
            "id": "temperature_range",
            "check": "temperature_design_min <= temperature_operating <= temperature_design_max",
            "severity": "error",
            "message": "Operating temperature must be within design range"
        },
        {
            "id": "flow_rate_logic",
            "check": "flow_rate_min <= flow_rate_normal <= flow_rate_max",
            "severity": "error",
            "message": "Normal flow must be between minimum and maximum"
        },
        {
            "id": "noise_warning",
            "check": "noise_level <= 85",
            "severity": "warning",
            "message": "Consider noise attenuation for levels above 85 dBA"
        },
        {
            "id": "cavitation_check",
            "check": "cavitation_index >= 0.7",
            "severity": "error",
            "message": "High risk of cavitation - redesign required"
        }
    ]
}


# Pump Configuration (Simplified - can be expanded)
CENTRIFUGAL_PUMP_CONFIG = {
    "id": "centrifugal_pump",
    "code": "centrifugal_pump",
    "name": "Centrifugal Pump",
    "icon": "⚙️",
    "version": "2.0",
    "category": "rotating_equipment",
    "description": "API 610 Centrifugal Pumps",
    "sections": [
        # To be expanded...
    ]
}


# Pressure Vessel Configuration (Simplified)
PRESSURE_VESSEL_CONFIG = {
    "id": "pressure_vessel",
    "code": "pressure_vessel",
    "name": "Pressure Vessel",
    "icon": "🛢️",
    "version": "2.0",
    "category": "static_equipment",
    "description": "ASME Section VIII Pressure Vessels",
    "sections": [
        # To be expanded...
    ]
}


# Registry of all equipment types
EQUIPMENT_TYPE_REGISTRY = {
    "control_valve": CONTROL_VALVE_CONFIG,
    "centrifugal_pump": CENTRIFUGAL_PUMP_CONFIG,
    "pressure_vessel": PRESSURE_VESSEL_CONFIG,
}


def get_equipment_config(equipment_code):
    """Get configuration for an equipment type"""
    return EQUIPMENT_TYPE_REGISTRY.get(equipment_code)


def list_equipment_types():
    """List all available equipment types"""
    return [
        {
            "code": config["code"],
            "name": config["name"],
            "icon": config["icon"],
            "category": config["category"],
            "version": config["version"]
        }
        for config in EQUIPMENT_TYPE_REGISTRY.values()
    ]
