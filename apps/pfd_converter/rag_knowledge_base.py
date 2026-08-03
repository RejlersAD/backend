"""
RAG KNOWLEDGE BASE FOR OIL & GAS P&ID GENERATION
=================================================
Uses Retrieval-Augmented Generation concepts to retrieve engineering patterns
"""

class OilGasRAGKnowledgeBase:
    """Knowledge base of Oil & Gas engineering patterns"""
    
    # Standard equipment connections from industry practice
    EQUIPMENT_PATTERNS = {
        "vessel": {
            "required_connections": [
                {"type": "inlet", "typical_size": "larger", "orientation": "side/tangential"},
                {"type": "vapor_outlet", "typical_size": "based_on_vapor_load", "orientation": "top"},
                {"type": "liquid_outlet", "typical_size": "based_on_flow", "orientation": "bottom/side"},
                {"type": "drain", "typical_size": "2-3 inch", "orientation": "bottom"},
                {"type": "vent", "typical_size": "1-2 inch", "orientation": "top"},
                {"type": "relief", "typical_size": "per_API_520", "orientation": "top"},
                {"type": "level_instrument", "typical_size": "2 inch bridle", "orientation": "side"},
                {"type": "pressure_instrument", "typical_size": "1/2 inch", "orientation": "top"}
            ],
            "optional_connections": [
                {"type": "sample", "typical_size": "1/2-1 inch", "orientation": "side"},
                {"type": "chemical_injection", "typical_size": "1/2-1 inch", "orientation": "inlet"},
                {"type": "level_bridle", "typical_size": "2-3 inch", "count": 2},
                {"type": "manway", "typical_size": "20-24 inch", "orientation": "side"}
            ],
            "instruments": ["pressure", "temperature", "level", "level_switches"],
            "safety_devices": ["PSV", "rupture_disc"],
            "utilities": ["nitrogen_purge", "steam_coil_if_heated"]
        },
        
        "pump": {
            "required_connections": [
                {"type": "suction", "typical_size": "one_size_larger", "orientation": "horizontal"},
                {"type": "discharge", "typical_size": "same_as_pump", "orientation": "horizontal"},
                {"type": "seal_flush", "typical_size": "1/2-1 inch", "orientation": "seal_chamber"},
                {"type": "drain", "typical_size": "1/2 inch", "orientation": "bottom"}
            ],
            "optional_connections": [
                {"type": "recirculation_line", "typical_size": "per_min_flow", "destination": "suction_vessel"},
                {"type": "warm_up_line", "typical_size": "1 inch", "destination": "drain"},
                {"type": "vent", "typical_size": "1/2 inch", "orientation": "suction"}
            ],
            "instruments": ["suction_pressure", "discharge_pressure", "flow", "vibration", "bearing_temp"],
            "valves": ["suction_isolation", "discharge_isolation", "discharge_check", "min_flow_bypass"],
            "utilities": ["seal_flush", "cooling_water_if_bearing", "lube_oil_if_required"],
            "interlocks": ["low_suction_pressure_trip", "high_discharge_pressure_trip", "low_flow_trip"]
        },
        
        "tank": {
            "required_connections": [
                {"type": "inlet", "typical_size": "per_flow", "orientation": "top_or_side"},
                {"type": "outlet", "typical_size": "per_flow", "orientation": "bottom"},
                {"type": "vent", "typical_size": "per_API_2000", "orientation": "top"},
                {"type": "drain", "typical_size": "3-4 inch", "orientation": "bottom"},
                {"type": "overflow", "typical_size": "same_as_inlet", "orientation": "near_top"}
            ],
            "optional_connections": [
                {"type": "water_draw", "typical_size": "2-3 inch", "orientation": "bottom"},
                {"type": "sample", "typical_size": "1 inch", "orientation": "side"},
                {"type": "fire_water_spray", "typical_size": "header", "orientation": "top"}
            ],
            "instruments": ["level", "level_switches", "temperature", "pressure_if_pressurized"],
            "safety_devices": ["PV_or_vent", "flame_arrester_if_volatile"],
            "utilities": ["nitrogen_blanket_if_inerted", "heating_coil_if_required"]
        },
        
        "exchanger": {
            "required_connections": [
                {"type": "shell_inlet", "typical_size": "per_design", "orientation": "side"},
                {"type": "shell_outlet", "typical_size": "per_design", "orientation": "opposite_side"},
                {"type": "tube_inlet", "typical_size": "per_design", "orientation": "channel"},
                {"type": "tube_outlet", "typical_size": "per_design", "orientation": "channel"},
                {"type": "shell_vent", "typical_size": "1/2-1 inch", "orientation": "top"},
                {"type": "shell_drain", "typical_size": "1-2 inch", "orientation": "bottom"}
            ],
            "optional_connections": [
                {"type": "tube_vent", "typical_size": "1/2 inch", "orientation": "channel"},
                {"type": "tube_drain", "typical_size": "1 inch", "orientation": "channel"}
            ],
            "instruments": ["inlet_temp_both_sides", "outlet_temp_both_sides", "dp_shell", "dp_tube"],
            "valves": ["isolation_all_connections", "bypass_control"],
            "utilities": []
        }
    }
    
    # Control loop patterns from industry standards
    CONTROL_LOOP_PATTERNS = {
        "pressure_control": {
            "transmitter": {"type": "PT", "range_factor": 1.5, "location": "field"},
            "controller": {"type": "PIC", "location": "control_room"},
            "valve": {"type": "control", "fail": "based_on_safety", "actuator": "pneumatic"},
            "alarms": ["PAH", "PAL"],
            "trips": ["PAHH", "PALL"],
            "typical_applications": ["vessel_pressure", "header_pressure", "compressor_suction"]
        },
        
        "level_control": {
            "transmitter": {"type": "LT", "range": "0-100%", "location": "field"},
            "controller": {"type": "LIC", "location": "control_room"},
            "valve": {"type": "control", "fail": "close", "actuator": "pneumatic"},
            "alarms": ["LAH", "LAL"],
            "trips": ["LAHH", "LALL"],
            "switches": ["LSHH", "LSH", "LSL", "LSLL"],
            "typical_applications": ["vessel_level", "tank_level", "sump_level"]
        },
        
        "flow_control": {
            "transmitter": {"type": "FT", "location": "field"},
            "controller": {"type": "FIC", "location": "control_room"},
            "valve": {"type": "control", "fail": "based_on_process", "actuator": "pneumatic"},
            "alarms": ["FAH", "FAL"],
            "typical_applications": ["feed_rate", "product_rate", "utility_flow"]
        },
        
        "temperature_control": {
            "transmitter": {"type": "TT", "sensor": "RTD_Pt100", "location": "field"},
            "controller": {"type": "TIC", "location": "control_room"},
            "valve": {"type": "control", "service": "heating_or_cooling", "actuator": "pneumatic"},
            "alarms": ["TAH", "TAL"],
            "typical_applications": ["reactor_temp", "product_temp", "feed_preheating"]
        },
        
        "cascade_control": {
            "primary_loop": {"type": "level_or_temp", "output_to": "secondary_setpoint"},
            "secondary_loop": {"type": "flow", "manipulates": "valve"},
            "typical_applications": ["level_cascade_flow", "temp_cascade_flow"]
        }
    }
    
    # Piping system patterns
    PIPING_PATTERNS = {
        "pump_suction": {
            "features": [
                "eccentric_reducer_flat_on_top",
                "isolation_valve_near_pump",
                "strainer_if_required",
                "pressure_gauge",
                "straight_run_before_pump"
            ],
            "min_velocity": "0.6 m/s",
            "max_velocity": "2.0 m/s"
        },
        
        "pump_discharge": {
            "features": [
                "check_valve_near_pump",
                "isolation_valve_after_check",
                "pressure_gauge",
                "min_flow_bypass_if_required"
            ],
            "min_velocity": "1.0 m/s",
            "max_velocity": "3.0 m/s for_liquid"
        },
        
        "relief_system": {
            "features": [
                "PSV_on_vessel",
                "relief_header_or_direct_to_flare",
                "block_valve_upstream_if_required",
                "rupture_disc_if_fouling_service",
                "tail_pipe_to_safe_location"
            ],
            "sizing": "per_API_520"
        },
        
        "bypass_system": {
            "features": [
                "bypass_around_control_valve",
                "bypass_around_equipment",
                "startup_bypass_line",
                "isolation_valves_both_ends"
            ],
            "typical_size": "one_size_smaller_than_main"
        },
        
        "drain_system": {
            "features": [
                "drain_valve_low_point",
                "drain_to_closed_system_if_hazardous",
                "drain_to_sump_or_grade",
                "isolation_valve_before_drain"
            ],
            "typical_size": "2-3 inch for_vessels"
        },
        
        "vent_system": {
            "features": [
                "vent_valve_high_point",
                "vent_to_safe_location",
                "vent_to_closed_system_if_hazardous",
                "isolation_valve_before_vent"
            ],
            "typical_size": "1-2 inch for_vessels"
        }
    }
    
    # Utility system patterns
    UTILITY_PATTERNS = {
        "instrument_air": {
            "header_pressure": "7 barg typical",
            "distribution": "ring_main_or_headers",
            "connections": [
                {"equipment": "control_valves", "size": "per_actuator"},
                {"equipment": "pneumatic_instruments", "size": "1/2_inch"},
                {"equipment": "air_operated_valves", "size": "per_actuator"}
            ],
            "features": ["air_filter_regulator_each_user", "drain_legs_low_points", "isolation_valves"]
        },
        
        "nitrogen": {
            "header_pressure": "10-15 barg typical",
            "uses": ["purging", "blanketing", "pneumatic_valve_backup"],
            "connections": [
                {"equipment": "vessels_for_blanketing", "size": "1-2_inch"},
                {"equipment": "purge_connections", "size": "1_inch"}
            ],
            "features": ["pressure_regulator", "non_return_valve", "isolation_valve"]
        },
        
        "cooling_water": {
            "supply_pressure": "4-6 barg typical",
            "return_pressure": "1-2 barg typical",
            "connections": [
                {"equipment": "heat_exchangers", "size": "per_design"},
                {"equipment": "pump_bearings", "size": "1/2-1_inch"},
                {"equipment": "compressor_seals", "size": "1/2-1_inch"}
            ],
            "features": ["supply_and_return_lines", "isolation_valves", "flow_indicators"]
        },
        
        "steam": {
            "levels": ["HP_40_barg", "MP_10_barg", "LP_3_barg"],
            "connections": [
                {"equipment": "heat_exchangers", "size": "per_duty"},
                {"equipment": "steam_tracing", "size": "1/2-3/4_inch"},
                {"equipment": "turbines", "size": "per_design"}
            ],
            "features": ["steam_trap_after_each_user", "condensate_return", "isolation_valves", "strainer"]
        }
    }
    
    # Safety system patterns
    SAFETY_PATTERNS = {
        "overpressure_protection": {
            "devices": ["PSV", "rupture_disc", "PCV_with_high_integrity"],
            "relief_destination": ["flare_header", "atmosphere_if_safe", "catch_tank"],
            "requirements": ["per_API_520", "per_ASME_VIII"],
            "features": ["isolation_block_valves_if_allowed", "tail_pipe_to_safe_discharge"]
        },
        
        "emergency_shutdown": {
            "devices": ["ESD_valves_SIL_rated", "shutdown_logic", "manual_shutdown_buttons"],
            "logic": ["1oo1", "1oo2", "2oo3_based_on_SIL"],
            "features": ["fail_safe_design", "testable_under_operation", "position_switches"]
        },
        
        "fire_protection": {
            "devices": ["fire_detectors", "deluge_systems", "foam_systems"],
            "actions": ["shutdown_equipment", "isolate_feed", "depressure_if_required"],
            "features": ["manual_activation_stations", "automatic_detection"]
        },
        
        "process_interlocks": {
            "types": ["high_level_trip", "low_level_trip", "high_pressure_trip", "low_pressure_trip"],
            "actions": ["close_valves", "stop_pumps", "alarm_operators"],
            "logic": ["simple_trip", "time_delayed", "voted_logic"]
        }
    }
    
    @classmethod
    def retrieve_equipment_pattern(cls, equipment_type: str) -> dict:
        """RAG retrieval: Get standard connections for equipment type"""
        equipment_key = equipment_type.lower()
        return cls.EQUIPMENT_PATTERNS.get(equipment_key, {})
    
    @classmethod
    def retrieve_control_loop_pattern(cls, control_type: str) -> dict:
        """RAG retrieval: Get standard control loop configuration"""
        return cls.CONTROL_LOOP_PATTERNS.get(control_type, {})
    
    @classmethod
    def retrieve_piping_pattern(cls, piping_type: str) -> dict:
        """RAG retrieval: Get standard piping arrangement"""
        return cls.PIPING_PATTERNS.get(piping_type, {})
    
    @classmethod
    def retrieve_utility_pattern(cls, utility_type: str) -> dict:
        """RAG retrieval: Get standard utility system configuration"""
        return cls.UTILITY_PATTERNS.get(utility_type, {})
    
    @classmethod
    def retrieve_safety_pattern(cls, safety_type: str) -> dict:
        """RAG retrieval: Get standard safety system configuration"""
        return cls.SAFETY_PATTERNS.get(safety_type, {})
