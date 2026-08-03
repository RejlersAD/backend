"""
ADVANCED OIL & GAS PROCESS ENGINEERING AI
==========================================
Implements intelligent P&ID generation with industry-standard templates
Uses generative AI concepts to create complete process units
"""

class OilGasProcessTemplates:
    """Industry-standard Oil & Gas process unit templates"""
    
    @staticmethod
    def get_separation_unit():
        """Complete 3-phase separator with full instrumentation"""
        return {
            "equipment": [
                {
                    "tag": "V-101",
                    "type": "vessel",
                    "name": "Three-Phase Separator",
                    "service": "Oil/Water/Gas Separation",
                    "orientation": "horizontal",
                    "length": "6000 mm",
                    "diameter": "2400 mm",
                    "operating_pressure": "25 barg",
                    "design_pressure": "30 barg",
                    "operating_temperature": "65°C",
                    "design_temperature": "100°C",
                    "material": "CS + 3mm CA",
                    "insulation": "mineral wool 100mm",
                    "design_code": "ASME VIII Div 1",
                    "internals": ["Inlet diverter", "Wire mesh demister", "Weir plates", "Vortex breakers"],
                    "nozzles": {
                        "N1": {"size": "12\"", "service": "Inlet (Two-Phase)", "orientation": "Side"},
                        "N2": {"size": "10\"", "service": "Gas Outlet", "orientation": "Top"},
                        "N3": {"size": "8\"", "service": "Oil Outlet", "orientation": "Side"},
                        "N4": {"size": "6\"", "service": "Water Outlet", "orientation": "Bottom"},
                        "N5": {"size": "3\"", "service": "PSV", "orientation": "Top"},
                        "N6": {"size": "2\"", "service": "Level Bridle", "orientation": "Side"},
                        "N7": {"size": "1\"", "service": "Drain", "orientation": "Bottom"},
                        "N8": {"size": "1\"", "service": "Vent", "orientation": "Top"}
                    }
                },
                {
                    "tag": "P-101A",
                    "type": "pump",
                    "name": "Oil Transfer Pump A",
                    "service": "Oil Pumping",
                    "pump_type": "Centrifugal - OH2",
                    "design_flow": "150 m3/h",
                    "design_head": "60 m",
                    "operating_pressure": "28 barg",
                    "design_pressure": "35 barg",
                    "material": "CF8M",
                    "seal_type": "Mechanical Seal - API 682 Plan 11",
                    "motor_power": "45 kW",
                    "motor_speed": "3000 RPM",
                    "status": "Operating"
                },
                {
                    "tag": "P-101B",
                    "type": "pump",
                    "name": "Oil Transfer Pump B (Spare)",
                    "service": "Oil Pumping",
                    "pump_type": "Centrifugal - OH2",
                    "design_flow": "150 m3/h",
                    "design_head": "60 m",
                    "operating_pressure": "28 barg",
                    "design_pressure": "35 barg",
                    "material": "CF8M",
                    "seal_type": "Mechanical Seal - API 682 Plan 11",
                    "motor_power": "45 kW",
                    "motor_speed": "3000 RPM",
                    "status": "Standby"
                }
            ],
            
            "piping": [
                {
                    "line_number": "12-201-6HE-A1",
                    "from": "Inlet Header",
                    "to": "V-101",
                    "line_size": "12\"",
                    "material": "CS",
                    "rating": "300#",
                    "pipe_class": "A1",
                    "schedule": "SCH 40",
                    "fluid": "Two-Phase Oil/Gas",
                    "normal_flow": "200 m3/h",
                    "design_flow": "250 m3/h",
                    "insulation": "100mm mineral wool",
                    "heat_tracing": "Electric HT-101"
                },
                {
                    "line_number": "12-202-10HE-A1",
                    "from": "V-101",
                    "to": "Gas Outlet Header",
                    "line_size": "10\"",
                    "material": "CS",
                    "rating": "300#",
                    "pipe_class": "A1",
                    "schedule": "SCH 40",
                    "fluid": "Gas",
                    "normal_flow": "5000 Sm3/h",
                    "design_flow": "6500 Sm3/h",
                    "insulation": "none"
                },
                {
                    "line_number": "12-203-8HE-A2",
                    "from": "V-101",
                    "to": "P-101A",
                    "line_size": "8\"",
                    "material": "CS + CRA Lined",
                    "rating": "300#",
                    "pipe_class": "A2",
                    "schedule": "SCH 40",
                    "fluid": "Oil",
                    "normal_flow": "150 m3/h",
                    "design_flow": "200 m3/h",
                    "insulation": "50mm mineral wool"
                },
                {
                    "line_number": "12-204-8HE-A2",
                    "from": "P-101A",
                    "to": "Oil Export Header",
                    "line_size": "8\"",
                    "material": "CS + CRA Lined",
                    "rating": "600#",
                    "pipe_class": "A2",
                    "schedule": "SCH 80",
                    "fluid": "Oil",
                    "normal_flow": "150 m3/h",
                    "design_flow": "200 m3/h",
                    "insulation": "50mm mineral wool"
                }
            ],
            
            "instrumentation": [
                # Pressure Control Loop
                {
                    "tag": "PIC-201",
                    "type": "pressure indicator controller",
                    "service": "Separator Pressure Control",
                    "connected_to": "V-101",
                    "location_type": "control room",
                    "range": "0-40 barg",
                    "normal_value": "25 barg",
                    "control_action": "Reverse",
                    "output_to": "PV-201",
                    "alarm_high": "28 barg",
                    "alarm_low": "22 barg"
                },
                {
                    "tag": "PT-201",
                    "type": "pressure transmitter",
                    "service": "Separator Pressure",
                    "connected_to": "V-101",
                    "location_type": "field",
                    "range": "0-40 barg",
                    "signal_type": "4-20mA",
                    "transmitter_type": "Smart (HART)"
                },
                # Level Control Loop
                {
                    "tag": "LIC-201",
                    "type": "level indicator controller",
                    "service": "Oil Level Control",
                    "connected_to": "V-101",
                    "location_type": "control room",
                    "range": "0-100%",
                    "normal_value": "50%",
                    "control_action": "Reverse",
                    "output_to": "LV-201",
                    "alarm_high": "80%",
                    "alarm_high_high": "90%",
                    "alarm_low": "20%",
                    "alarm_low_low": "10%"
                },
                {
                    "tag": "LT-201",
                    "type": "level transmitter",
                    "service": "Oil Level",
                    "connected_to": "V-101",
                    "location_type": "field",
                    "range": "0-100%",
                    "transmitter_type": "Guided Wave Radar",
                    "signal_type": "4-20mA"
                },
                {
                    "tag": "LIC-202",
                    "type": "level indicator controller",
                    "service": "Water Level Control",
                    "connected_to": "V-101",
                    "location_type": "control room",
                    "range": "0-100%",
                    "normal_value": "30%",
                    "control_action": "Reverse",
                    "output_to": "LV-202"
                },
                {
                    "tag": "LT-202",
                    "type": "level transmitter",
                    "service": "Water Level (Interface)",
                    "connected_to": "V-101",
                    "location_type": "field",
                    "range": "0-100%",
                    "transmitter_type": "Displacer",
                    "signal_type": "4-20mA"
                },
                # Temperature Monitoring
                {
                    "tag": "TI-201",
                    "type": "temperature indicator",
                    "service": "Separator Temperature",
                    "connected_to": "V-101",
                    "location_type": "field",
                    "range": "0-150°C",
                    "sensor_type": "RTD Pt100"
                },
                # Flow Measurement
                {
                    "tag": "FT-201",
                    "type": "flow transmitter",
                    "service": "Oil Export Flow",
                    "connected_to": "P-101A",
                    "location_type": "field",
                    "range": "0-250 m3/h",
                    "meter_type": "Coriolis Mass Flow",
                    "signal_type": "4-20mA"
                },
                {
                    "tag": "FI-201",
                    "type": "flow indicator",
                    "service": "Oil Export Flow",
                    "connected_to": "P-101A",
                    "location_type": "control room",
                    "range": "0-250 m3/h"
                }
            ],
            
            "valves": [
                # Pressure Control Valve
                {
                    "tag": "PV-201",
                    "type": "control",
                    "service": "Gas Pressure Control",
                    "line": "12-202-10HE-A1",
                    "size": "10\"",
                    "actuator": "pneumatic",
                    "actuator_size": "Spring-diaphragm 12\"",
                    "fail_position": "fail open",
                    "cv": 450,
                    "body_material": "WCB",
                    "trim_material": "316SS",
                    "positioner": "Smart Digital",
                    "accessories": ["Limit switches", "Solenoid valve", "Air filter regulator"]
                },
                # Level Control Valves
                {
                    "tag": "LV-201",
                    "type": "control",
                    "service": "Oil Level Control",
                    "line": "12-203-8HE-A2",
                    "size": "8\"",
                    "actuator": "pneumatic",
                    "actuator_size": "Spring-diaphragm 10\"",
                    "fail_position": "fail close",
                    "cv": 280,
                    "body_material": "WCB + CRA",
                    "trim_material": "Duplex SS",
                    "positioner": "Smart Digital"
                },
                {
                    "tag": "LV-202",
                    "type": "control",
                    "service": "Water Level Control",
                    "line": "12-205-6HE-A3",
                    "size": "6\"",
                    "actuator": "pneumatic",
                    "fail_position": "fail close",
                    "cv": 180
                },
                # Manual Isolation Valves
                {
                    "tag": "HV-201",
                    "type": "gate",
                    "service": "Pump Suction Isolation",
                    "line": "12-203-8HE-A2",
                    "size": "8\"",
                    "operator": "Handwheel",
                    "position": "normally open",
                    "body_material": "WCB",
                    "lockable": True
                },
                {
                    "tag": "HV-202",
                    "type": "gate",
                    "service": "Pump Discharge Isolation",
                    "line": "12-204-8HE-A2",
                    "size": "8\"",
                    "operator": "Handwheel",
                    "position": "normally open",
                    "body_material": "WCB"
                },
                # Check Valve
                {
                    "tag": "CV-201",
                    "type": "check",
                    "service": "Pump Discharge Non-Return",
                    "line": "12-204-8HE-A2",
                    "size": "8\"",
                    "check_type": "Swing",
                    "body_material": "WCB"
                },
                # Safety Valve
                {
                    "tag": "PSV-201",
                    "type": "safety",
                    "service": "Separator Overpressure Protection",
                    "connected_to": "V-101",
                    "size": "3\"",
                    "set_pressure": "28 barg",
                    "relief_capacity": "5000 kg/h",
                    "relief_destination": "Flare Header FH-01",
                    "body_material": "WCB",
                    "orifice": "K"
                },
                # Emergency Shutdown Valves
                {
                    "tag": "SDV-201",
                    "type": "esd",
                    "service": "Emergency Shutdown - Inlet",
                    "line": "12-201-6HE-A1",
                    "size": "12\"",
                    "actuator": "pneumatic",
                    "fail_position": "fail close",
                    "close_time": "5 seconds",
                    "sil_rating": "SIL 2",
                    "interlocks": ["PAHH-201", "LAHH-201", "Fire Gas System"]
                }
            ],
            
            "utilities": [
                {
                    "type": "instrument_air",
                    "connection_point": "Near P-101A/B",
                    "header_pressure": "7 barg",
                    "supply_line": "1\" SS-150#-IA",
                    "consumers": ["PV-201", "LV-201", "LV-202", "SDV-201"]
                },
                {
                    "type": "seal_flush",
                    "connection_point": "P-101A seal",
                    "system": "API 682 Plan 11",
                    "supply_line": "1/2\" SS-600#-SF"
                },
                {
                    "type": "nitrogen",
                    "connection_point": "V-101 purge",
                    "header_pressure": "10 barg",
                    "supply_line": "1\" SS-150#-N2",
                    "purpose": "Startup purging"
                }
            ],
            
            "safety_systems": [
                {
                    "tag": "PAHH-201",
                    "description": "Separator High High Pressure Trip",
                    "setpoint": "29 barg",
                    "action": "Close SDV-201, Stop P-101A/B",
                    "sil": "SIL 2",
                    "voting": "1oo1"
                },
                {
                    "tag": "LAHH-201",
                    "description": "Separator High High Level Trip",
                    "setpoint": "90%",
                    "action": "Close SDV-201",
                    "sil": "SIL 1"
                },
                {
                    "tag": "LALL-201",
                    "description": "Separator Low Low Level Trip",
                    "setpoint": "10%",
                    "action": "Stop P-101A/B",
                    "sil": "SIL 1"
                }
            ]
        }
    
    @staticmethod
    def get_complete_process_unit():
        """Get a complete realistic process unit with multiple sections"""
        sep_unit = OilGasProcessTemplates.get_separation_unit()
        
        # Add heat exchanger upstream
        sep_unit["equipment"].insert(0, {
            "tag": "E-101",
            "type": "exchanger",
            "name": "Feed Preheater",
            "service": "Two-Phase Feed Heating",
            "exchanger_type": "Shell & Tube",
            "duty": "2.5 MW",
            "shell_side_fluid": "Process",
            "tube_side_fluid": "Hot Oil",
            "shell_side_pressure": "30 barg",
            "tube_side_pressure": "15 barg",
            "area": "250 m2",
            "material": "CS/SS",
            "tubes": "304 tubes, 6m length, 1\" OD"
        })
        
        # Add export tank downstream
        sep_unit["equipment"].append({
            "tag": "T-101",
            "type": "tank",
            "name": "Oil Storage Tank",
            "service": "Oil Storage",
            "tank_type": "Cone Roof",
            "capacity": "5000 m3",
            "diameter": "20 m",
            "height": "16 m",
            "design_code": "API 650",
            "material": "CS",
            "foundation": "Concrete Ring Wall"
        })
        
        return sep_unit


def enrich_with_oil_gas_intelligence(base_specs: dict) -> dict:
    """
    Enrich basic P&ID data with Oil & Gas industry intelligence
    """
    # If equipment count is low, use complete template
    if len(base_specs.get('equipment', [])) < 3:
        print("📊 Using complete Oil & Gas process template...")
        return OilGasProcessTemplates.get_complete_process_unit()
    
    # Otherwise enrich existing data
    enriched = dict(base_specs)
    
    # Add utility connections
    if 'utilities' not in enriched:
        enriched['utilities'] = [
            {"type": "instrument_air", "header_pressure": "7 barg", "supply_line": "1\" SS-150#-IA"},
            {"type": "nitrogen", "header_pressure": "10 barg", "supply_line": "1\" SS-150#-N2"}
        ]
    
    # Add safety systems
    if 'safety_systems' not in enriched:
        enriched['safety_systems'] = []
    
    return enriched
