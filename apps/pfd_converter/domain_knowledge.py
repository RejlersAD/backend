"""
Domain Knowledge Manager for PFD to P&ID Conversion
Manages engineering standards, legends, design basis, and project-specific data
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class DomainKnowledgeManager:
    """
    Manages domain knowledge for PFD to P&ID conversion including:
    - P&ID Legends and symbol standards
    - Design Basis documents
    - Line List specifications
    - Valve and instrument standards
    - Engineering standards (ADNOC DEP, API, ISA)
    """
    
    def __init__(self):
        self.base_path = Path(__file__).parent.parent.parent
        self.domain_data_path = self.base_path / 'domain_knowledge'
        self.project_data_path = self.base_path / 'project_data'
        
        # Create directories if they don't exist
        self.domain_data_path.mkdir(exist_ok=True)
        self.project_data_path.mkdir(exist_ok=True)
        
        # Load domain knowledge
        self.pid_legends = self._load_pid_legends()
        self.symbol_standards = self._load_symbol_standards()
        self.instrument_standards = self._load_instrument_standards()
        self.valve_standards = self._load_valve_standards()
        self.line_standards = self._load_line_standards()
        
    def _load_pid_legends(self) -> Dict:
        """Load P&ID legend and symbol definitions"""
        legend_file = self.domain_data_path / 'pid_legends.json'
        if legend_file.exists():
            with open(legend_file, 'r') as f:
                return json.load(f)
        
        # Default P&ID legends based on ISA/API standards
        return {
            "equipment_symbols": {
                "vessel": "Vertical/horizontal cylinder with rounded ends",
                "pump": "Circle with impeller or piston symbol",
                "compressor": "Circle with compression symbol",
                "heat_exchanger": "Rectangle with diagonal lines",
                "valve": "Standard valve symbols (gate, globe, ball, check)",
                "tank": "Cylindrical or rectangular vessel",
                "column": "Tall vertical vessel with internals"
            },
            "line_types": {
                "process": "Solid line",
                "utility": "Dashed line",
                "instrument": "Dash-dot line",
                "electrical": "Dash-dot-dot line"
            },
            "instrument_symbols": {
                "locally_mounted": "Circle on line",
                "panel_mounted": "Circle with horizontal line",
                "field_mounted": "Hexagon",
                "computer_function": "Circle with square"
            },
            "valve_symbols": {
                "gate_valve": "Triangle pointing down",
                "globe_valve": "Globe shape",
                "ball_valve": "Circle with line",
                "check_valve": "Triangle with bar",
                "control_valve": "Triangle with actuator symbol",
                "safety_valve": "Special relief symbol"
            }
        }
    
    def _load_symbol_standards(self) -> Dict:
        """Load engineering symbol standards"""
        return {
            "standards": ["ISA-5.1", "API RP 551", "ADNOC DEP"],
            "instrument_tagging": {
                "format": "XX-YYYY-###",
                "first_letter": {
                    "A": "Analysis",
                    "F": "Flow",
                    "L": "Level",
                    "P": "Pressure",
                    "T": "Temperature"
                },
                "subsequent_letters": {
                    "I": "Indicator",
                    "C": "Controller",
                    "T": "Transmitter",
                    "V": "Valve",
                    "S": "Switch",
                    "A": "Alarm"
                }
            },
            "equipment_tagging": {
                "format": "UUU-T-###",
                "unit": "3-digit unit number",
                "type": {
                    "P": "Pump",
                    "V": "Vessel/Drum",
                    "E": "Heat Exchanger",
                    "C": "Compressor",
                    "T": "Tower/Column",
                    "K": "Package",
                    "M": "Motor"
                },
                "sequential": "3-digit sequential number"
            }
        }
    
    def _load_instrument_standards(self) -> Dict:
        """Load instrument standards and specifications"""
        return {
            "control_loops": {
                "pressure_control": {
                    "typical": "PC (Pressure Controller) + PCV (Pressure Control Valve) + PT (Pressure Transmitter)",
                    "alarm": "PAH (Pressure Alarm High) / PAL (Pressure Alarm Low)",
                    "interlock": "PSH (Pressure Switch High) / PSL (Pressure Switch Low)"
                },
                "level_control": {
                    "typical": "LC (Level Controller) + LCV (Level Control Valve) + LT (Level Transmitter)",
                    "alarm": "LAH (Level Alarm High) / LAL (Level Alarm Low)",
                    "interlock": "LSH (Level Switch High) / LSL (Level Switch Low)"
                },
                "flow_control": {
                    "typical": "FC (Flow Controller) + FCV (Flow Control Valve) + FT (Flow Transmitter)",
                    "alarm": "FAH (Flow Alarm High) / FAL (Flow Alarm Low)"
                },
                "temperature_control": {
                    "typical": "TC (Temperature Controller) + TCV (Temperature Control Valve) + TT (Temperature Transmitter)",
                    "alarm": "TAH (Temperature Alarm High) / TAL (Temperature Alarm Low)"
                }
            },
            "safety_instruments": {
                "shutdown_valves": ["SDV", "ESV", "BDV", "XV"],
                "relief_devices": ["PSV", "PRV", "RD"],
                "emergency_systems": ["ESD", "F&G", "Interlock"]
            }
        }
    
    def _load_valve_standards(self) -> Dict:
        """Load valve standards and specifications"""
        return {
            "valve_types": {
                "isolation": {
                    "types": ["Gate Valve", "Ball Valve", "Plug Valve"],
                    "applications": "On/off service, full bore"
                },
                "control": {
                    "types": ["Globe Valve", "Butterfly Valve", "Control Valve"],
                    "applications": "Throttling service, flow control"
                },
                "check": {
                    "types": ["Swing Check", "Lift Check", "Dual Plate Check"],
                    "applications": "Prevent backflow"
                },
                "safety": {
                    "types": ["PSV", "PRV", "Rupture Disc"],
                    "applications": "Overpressure protection"
                }
            },
            "failure_positions": {
                "FC": "Fail Close",
                "FO": "Fail Open",
                "FL": "Fail Last Position",
                "FS": "Fail Safe Position"
            },
            "actuation_types": [
                "Pneumatic (air-operated)",
                "Electric (motor-operated)",
                "Hydraulic (oil-operated)",
                "Manual (hand-operated)",
                "Solenoid (electrically actuated)"
            ]
        }
    
    def _load_line_standards(self) -> Dict:
        """Load line numbering and specification standards"""
        return {
            "line_number_format": "SS-DDDD-NN-PPP-TT-I",
            "components": {
                "SS": "Size (inches)",
                "DDDD": "Fluid service code",
                "NN": "Sequential number",
                "PPP": "Pipe specification class",
                "TT": "Tracing/insulation code",
                "I": "Insulation type"
            },
            "fluid_codes": {
                "HC": "Hydrocarbon",
                "ST": "Steam",
                "CW": "Cooling Water",
                "CD": "Condensate",
                "FG": "Fuel Gas",
                "NG": "Natural Gas",
                "N2": "Nitrogen",
                "IA": "Instrument Air",
                "PA": "Plant Air"
            },
            "pipe_specs": {
                "150": "ANSI 150#",
                "300": "ANSI 300#",
                "600": "ANSI 600#",
                "900": "ANSI 900#",
                "1500": "ANSI 1500#"
            },
            "insulation_codes": {
                "HI": "Hot Insulation",
                "CI": "Cold Insulation",
                "PI": "Personnel Protection",
                "AC": "Acoustic Insulation"
            },
            "tracing_codes": {
                "ST": "Steam Tracing",
                "ET": "Electric Tracing",
                "HW": "Hot Water Tracing"
            }
        }
    
    def load_project_design_basis(self, project_id: Optional[str] = None) -> Dict:
        """Load project-specific design basis"""
        if project_id:
            design_basis_file = self.project_data_path / f'{project_id}_design_basis.json'
            if design_basis_file.exists():
                with open(design_basis_file, 'r') as f:
                    return json.load(f)
        
        # Default design basis template
        return {
            "project_info": {
                "project_name": "To be specified",
                "project_code": "XXX-XXXX",
                "client": "ADNOC Gas",
                "location": "UAE"
            },
            "design_conditions": {
                "ambient_temperature": {"min": "0°C", "max": "50°C"},
                "design_pressure": "To be specified per equipment",
                "design_temperature": "To be specified per equipment",
                "corrosion_allowance": "3 mm (carbon steel)",
                "seismic_zone": "Zone 2A (UAE)"
            },
            "materials": {
                "piping": "Carbon Steel (ASTM A106 Gr B) / Stainless Steel (ASTM A312 TP316)",
                "vessels": "Carbon Steel (ASTM A516 Gr 70)",
                "internals": "Stainless Steel 316"
            },
            "codes_standards": [
                "ADNOC DEP (Design & Engineering Practice)",
                "API 600 (Gate Valves)",
                "API 610 (Centrifugal Pumps)",
                "ASME B31.3 (Process Piping)",
                "ASME Section VIII (Pressure Vessels)",
                "ISA-5.1 (Instrumentation Symbols)"
            ]
        }
    
    def load_project_line_list(self, project_id: Optional[str] = None) -> Dict:
        """Load project-specific line list"""
        if project_id:
            line_list_file = self.project_data_path / f'{project_id}_line_list.json'
            if line_list_file.exists():
                with open(line_list_file, 'r') as f:
                    return json.load(f)
        
        # Default line list template
        return {
            "lines": [],
            "template": {
                "line_number": "SS-DDDD-NN-PPP-TT-I",
                "size": "Pipe nominal size (inches)",
                "pipe_spec": "Material specification class",
                "fluid_service": "Process fluid description",
                "design_pressure": "Maximum design pressure",
                "design_temperature": "Maximum design temperature",
                "insulation": "Insulation requirement",
                "tracing": "Heat tracing requirement",
                "operating_pressure": "Normal operating pressure",
                "operating_temperature": "Normal operating temperature"
            }
        }
    
    def get_utility_line_standards(self) -> Dict:
        """Get standard utility line specifications"""
        return {
            "utility_lines": {
                "vents": {
                    "description": "Atmospheric vents from vessels",
                    "typical_size": "2\" - 4\"",
                    "elevation": "Extend to safe height with weather cap"
                },
                "drains": {
                    "description": "Equipment and line drains",
                    "typical_size": "3/4\" - 2\"",
                    "routing": "To closed drain or open drain system"
                },
                "sample_points": {
                    "description": "Process sampling connections",
                    "typical_size": "1/2\" - 3/4\"",
                    "components": "Isolation valves, sample cooler if required"
                },
                "instrument_air": {
                    "description": "Clean, dry air for instruments",
                    "pressure": "6-8 bar g",
                    "typical_size": "1/2\" - 1\""
                },
                "nitrogen": {
                    "description": "Inert gas for purging/blanketing",
                    "pressure": "Variable",
                    "typical_size": "1\" - 2\""
                },
                "seal_gas": {
                    "description": "Clean gas for mechanical seals",
                    "pressure": "Higher than process",
                    "typical_size": "1/2\" - 1\""
                }
            }
        }
    
    def get_step1_extraction_prompt_enhancement(self) -> str:
        """Get prompt enhancement for Step 1: Core content extraction"""
        return f"""
STEP 1: CORE CONTENT EXTRACTION
As a Chemical Process Engineer experienced in oil and gas projects including gas treatment, extract:

1. MAIN EQUIPMENT LIST AND TAGS:
   Format: {self.symbol_standards['equipment_tagging']['format']}
   Examples: V-3611-01 (glycol contactor), C-3611-01 (compressor), U-3611 (package)
   
   For each equipment extract:
   - Equipment tag (Unit-Type-Number)
   - Equipment type and description
   - Design specifications (pressure, temperature, capacity)
   - Physical dimensions (diameter, height, length)
   - Design duty (heat transfer, flow rate, etc.)
   
2. PRIMARY PROCESS LINES:
   - Flow paths between equipment
   - Package boundaries and interconnections
   - Stream identification and properties
   - Operating conditions (P, T, Flow)
   
3. SAFETY AND SHUTDOWN VALVES:
   Safety instruments: {', '.join(self.instrument_standards['safety_instruments']['shutdown_valves'])}
   Relief devices: {', '.join(self.instrument_standards['safety_instruments']['relief_devices'])}
   
   For each safety valve capture:
   - Valve tag (e.g., SDV-101, PSV-201)
   - Location in network (upstream/downstream equipment)
   - Set pressure (if visible)
   - Failure position (FC/FO)
   
4. KEY CONTROL LOOPS:
   Pressure Control: {self.instrument_standards['control_loops']['pressure_control']['typical']}
   Level Control: {self.instrument_standards['control_loops']['level_control']['typical']}
   Flow Control: {self.instrument_standards['control_loops']['flow_control']['typical']}
   Temperature Control: {self.instrument_standards['control_loops']['temperature_control']['typical']}
   
   Tag format: {self.symbol_standards['instrument_tagging']['format']}
   First letter codes: {', '.join([f"{k}={v}" for k, v in list(self.symbol_standards['instrument_tagging']['first_letter'].items())[:5]])}
"""
    
    def get_step2_pid_structure_prompt_enhancement(self, design_basis: Dict) -> str:
        """Get prompt enhancement for Step 2: P&ID structure definition"""
        return f"""
STEP 2: PRELIMINARY P&ID STRUCTURE

1. SHEET BOUNDARY AND LAYOUT:
   - Define P&ID sheet boundaries based on process units
   - Arrange equipment in similar layout as PFD
   - Maintain logical flow direction (left to right, top to bottom)
   
2. STANDARD SYMBOLS AND CONVENTIONS:
   Equipment symbols: {', '.join([f"{k}: {v}" for k, v in list(self.pid_legends['equipment_symbols'].items())[:5]])}
   
   Line types:
   {chr(10).join([f"   - {k}: {v}" for k, v in self.pid_legends['line_types'].items()])}
   
   Instrument symbols (ISA-5.1):
   {chr(10).join([f"   - {k}: {v}" for k, v in self.pid_legends['instrument_symbols'].items()])}
   
3. DESIGN SPECIFICATIONS FROM DESIGN BASIS:
   Materials: {design_basis.get('materials', {}).get('piping', 'TBD')}
   Design Pressure: {design_basis.get('design_conditions', {}).get('design_pressure', 'TBD')}
   Design Temperature: {design_basis.get('design_conditions', {}).get('design_temperature', 'TBD')}
   
   Extract from design basis for each equipment:
   - Material of construction
   - Design pressure and temperature rating
   - Physical dimensions and capacity
   - Design duty and performance requirements
   
4. NODE-EDGE STRUCTURE:
   Build graph representation:
   - Nodes: Equipment (vessels, pumps, etc.) and connection points (nozzles)
   - Edges: Process lines with preliminary IDs
   - Attributes: Line sizes, fluid service, operating conditions
   
5. INSTRUMENT ATTACHMENT:
   Attach instruments based on control philosophy:
   - PC/PCV on gas outlet lines (pressure control)
   - LC/LCV on vessel liquid levels (level control)
   - FC/FCV on flow control applications
   - TC/TCV on temperature control applications
   
   Control loop completeness: Each control valve (PCV, LCV, FCV, TCV) must have:
   - Associated transmitter (PT, LT, FT, TT)
   - Controller (PC, LC, FC, TC) - can be in DCS
   - Alarm points (PAH/PAL, LAH/LAL, etc.)
   
6. UTILITY LINES:
   Add per standard practice:
   {chr(10).join([f"   - {k}: {v['description']}, Size: {v['typical_size']}" for k, v in self.get_utility_line_standards()['utility_lines'].items()])}
"""
    
    def get_step3_detailed_engineering_prompt_enhancement(self, line_list: Dict) -> str:
        """Get prompt enhancement for Step 3: Detailed engineering"""
        return f"""
STEP 3: DETAILED ENGINEERING

1. LINE NUMBERING SYSTEM:
   Format: {self.line_standards['line_number_format']}
   
   Components:
   {chr(10).join([f"   {k}: {v}" for k, v in self.line_standards['components'].items()])}
   
   Fluid service codes:
   {chr(10).join([f"   {k} = {v}" for k, v in list(self.line_standards['fluid_codes'].items())[:6]])}
   
   Pipe specifications:
   {chr(10).join([f"   {k}: {v}" for k, v in self.line_standards['pipe_specs'].items()])}
   
   For each line assign:
   - Unique line number following project standard
   - Pipe size (nominal diameter in inches)
   - Pipe specification class (material, pressure rating)
   - Insulation type and thickness (if required)
   - Heat tracing type (if required)
   - Normal operating conditions (P, T, Flow)
   - Maximum design conditions
   
2. VALVE DETAILS:
   Valve types:
   {chr(10).join([f"   {k.title()}: {v['types']}" for k, v in list(self.valve_standards['valve_types'].items())[:3]])}
   
   For each valve specify:
   - Valve type (gate, globe, ball, check, control)
   - Valve tag number
   - Failure position: {', '.join(f"{k}={v}" for k, v in self.valve_standards['failure_positions'].items())}
   - Actuation type: {', '.join(self.valve_standards['actuation_types'][:4])}
   - Line number (same as associated line)
   - Size and rating
   
3. INSTRUMENT DETAILS:
   For each instrument specify:
   - Instrument tag (following ISA-5.1 standard)
   - Instrument type (transmitter, gauge, switch, controller)
   - Measurement range and engineering units
   - Set points (for controllers and switches)
   - Alarm levels (high/low)
   - Interlock logic (cause and effect)
   - Location (field mounted, panel mounted, DCS function)
   - Signal type (4-20mA, digital, pneumatic)
   
   Control loop documentation:
   - Loop number (cross-reference to control narrative)
   - Normal operating value
   - Control action (direct/reverse)
   - Tuning parameters (if known)
   
4. LINE LIST INTEGRATION:
   Cross-reference with project line list for:
   - Standard line sizes for each service
   - Pipe specification classes
   - Material compatibility
   - Insulation requirements
   - Tracing requirements
   - Pressure drop allowances
"""


# Global instance
domain_knowledge = DomainKnowledgeManager()


def get_domain_enhanced_extraction_prompt(project_id: Optional[str] = None) -> str:
    """
    Get complete domain-enhanced prompt for PFD extraction
    Incorporates all 3 steps of the engineering process
    """
    design_basis = domain_knowledge.load_project_design_basis(project_id)
    line_list = domain_knowledge.load_project_line_list(project_id)
    
    step1_prompt = domain_knowledge.get_step1_extraction_prompt_enhancement()
    step2_prompt = domain_knowledge.get_step2_pid_structure_prompt_enhancement(design_basis)
    step3_prompt = domain_knowledge.get_step3_detailed_engineering_prompt_enhancement(line_list)
    
    full_prompt = f"""
You are an expert Chemical Process Engineer with extensive experience in oil and gas project design and engineering.
Your task is to convert a Process Flow Diagram (PFD) to detailed Piping & Instrumentation Diagram (P&ID) specifications
following industry standards (ADNOC DEP, API, ISA) and best practices.

{step1_prompt}

{step2_prompt}

{step3_prompt}

OUTPUT FORMAT:
Provide complete JSON structure with:
{{
    "step1_core_extraction": {{
        "equipment_list": [...],
        "process_lines": [...],
        "safety_valves": [...],
        "control_loops": [...]
    }},
    "step2_pid_structure": {{
        "sheet_boundaries": [...],
        "equipment_layout": [...],
        "node_edge_graph": [...],
        "instruments": [...],
        "utility_lines": [...]
    }},
    "step3_detailed_engineering": {{
        "line_numbers": [...],
        "valve_specifications": [...],
        "instrument_specifications": [...],
        "design_data": [...]
    }}
}}

Apply your expert knowledge to ensure:
- Complete and accurate equipment identification
- Proper control loop design and instrumentation
- Safety system completeness
- Standard compliance
- Engineering best practices
"""
    
    return full_prompt
