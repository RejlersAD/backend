"""
Professional P&ID Generator from PFD
Uses AI and soft-coded engineering rules to generate detailed, accurate P&IDs
"""
import os
import sys
import json
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from openai import OpenAI
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)

client = OpenAI(api_key=settings.OPENAI_API_KEY)

# ============================================================================
# ENGINEERING RULES CONFIGURATION (Soft-Coded)
# ============================================================================

PID_GENERATION_CONFIG = {
    "equipment_expansion": {
        "vessel": {
            "required_nozzles": ["inlet", "outlet", "drain", "vent", "level_gauge", "pressure_gauge"],
            "typical_instruments": ["PT", "LT", "TT"],
            "safety_devices": ["PSV"],
            "control_valves": ["level_control", "pressure_control"],
            "internals_to_show": ["demister", "trays", "baffles", "supports"]
        },
        "pump": {
            "required_nozzles": ["suction", "discharge"],
            "typical_instruments": ["PT_suction", "PT_discharge", "FT"],
            "safety_devices": ["PRV", "check_valve"],
            "control_valves": ["discharge_control"],
            "details_to_show": ["driver", "seal_type", "coupling"]
        },
        "heat_exchanger": {
            "required_nozzles": ["shell_inlet", "shell_outlet", "tube_inlet", "tube_outlet"],
            "typical_instruments": ["TT_inlet", "TT_outlet", "PT_inlet", "PT_outlet"],
            "safety_devices": ["PSV"],
            "control_valves": ["temperature_control"],
            "details_to_show": ["tube_bundle", "shell_type"]
        }
    },
    "piping_rules": {
        "line_numbering": {
            "format": "{project}-{area}-{system}-{line_number}",
            "example": "14-01-08-1602"
        },
        "isolation_valves": {
            "placement": [
                "equipment_inlet",
                "equipment_outlet",
                "branch_points",
                "before_control_valves",
                "maintenance_points"
            ],
            "typical_type": "gate_valve"
        },
        "control_valves": {
            "placement_criteria": [
                "pressure_control_required",
                "flow_control_required",
                "level_control_required",
                "temperature_control_required"
            ],
            "sizing": "based_on_flow_and_pressure_drop"
        },
        "check_valves": {
            "placement": [
                "pump_discharge",
                "prevent_backflow",
                "parallel_equipment"
            ]
        }
    },
    "instrumentation_rules": {
        "pressure_transmitter": {
            "placement": ["vessel_top", "line_high_point", "pump_discharge"],
            "range_selection": "1.5x_operating_pressure",
            "tag_format": "PT-{area}-{equipment}-{seq}"
        },
        "level_transmitter": {
            "placement": ["vessel_side_middle"],
            "range_selection": "vessel_height_plus_margin",
            "tag_format": "LT-{area}-{equipment}-{seq}"
        },
        "temperature_transmitter": {
            "placement": ["vessel_bottom", "line_after_heater"],
            "range_selection": "1.2x_operating_temperature",
            "tag_format": "TT-{area}-{equipment}-{seq}"
        },
        "flow_transmitter": {
            "placement": ["main_lines", "custody_transfer"],
            "range_selection": "max_expected_flow_plus_margin",
            "tag_format": "FT-{area}-{equipment}-{seq}"
        }
    },
    "control_loops": {
        "pressure_control": {
            "configuration": "PID_reverse_acting",
            "instruments": ["PT", "PIC", "PCV"],
            "alarms": ["PAH", "PAL"],
            "trips": ["PAHH", "PALL"]
        },
        "level_control": {
            "configuration": "PID_direct_acting",
            "instruments": ["LT", "LIC", "LCV"],
            "alarms": ["LAH", "LAL"],
            "trips": ["LAHH", "LALL"]
        },
        "temperature_control": {
            "configuration": "PID_with_cascade",
            "instruments": ["TT", "TIC", "TCV"],
            "alarms": ["TAH", "TAL"],
            "trips": ["TAHH", "TALL"]
        },
        "flow_control": {
            "configuration": "PID_reverse_acting",
            "instruments": ["FT", "FIC", "FCV"],
            "alarms": ["FAH", "FAL"],
            "trips": []
        }
    },
    "safety_systems": {
        "PSV": {
            "placement_criteria": [
                "any_isolated_vessel",
                "thermal_expansion",
                "external_fire",
                "process_upset"
            ],
            "set_pressure": "design_pressure_x_1.0",
            "sizing_method": "API_520_521",
            "discharge": "to_flare_or_atmosphere"
        },
        "ESD_valve": {
            "placement": [
                "equipment_main_inlet",
                "equipment_main_outlet",
                "cross_ties",
                "before_after_critical_cv"
            ],
            "fail_position": "fail_closed",
            "actuation": "pneumatic_with_solenoid"
        },
        "high_pressure_trips": {
            "setpoint": "95_percent_design_pressure",
            "action": "close_inlet_valves_open_relief"
        },
        "low_level_trips": {
            "setpoint": "10_percent_vessel_height",
            "action": "stop_downstream_pumps"
        },
        "high_level_trips": {
            "setpoint": "90_percent_vessel_height",
            "action": "close_inlet_valves"
        }
    },
    "utility_systems": {
        "instrument_air": {
            "pressure": "6-7_barg",
            "connections": ["pneumatic_instruments", "control_valves", "actuated_valves"],
            "line_size": "typically_1_inch"
        },
        "drains": {
            "placement": ["low_points", "before_isolation_valves", "equipment_bottom"],
            "typical_size": "3/4_inch_or_1_inch",
            "valve_type": "ball_or_globe"
        },
        "vents": {
            "placement": ["high_points", "after_isolation_valves", "equipment_top"],
            "typical_size": "3/4_inch_or_1_inch",
            "valve_type": "ball_or_globe"
        }
    },
    "drawing_standards": {
        "symbols": "ISA_S5.1",
        "line_types": {
            "process": "solid_thick",
            "instrument_signal": "dashed_thin",
            "pneumatic_signal": "dashed_medium",
            "electric_signal": "dashed_thin_with_slash"
        },
        "text_standards": {
            "equipment_tags": "bold_large",
            "instrument_tags": "regular_medium",
            "line_numbers": "italic_small",
            "notes": "regular_small"
        }
    }
}


class ProfessionalPIDGenerator:
    """Generate professional P&IDs from PFD analysis using engineering rules"""
    
    def __init__(self):
        self.config = PID_GENERATION_CONFIG
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    def generate_pid_from_pfd(self, pfd_analysis, output_path=None):
        """
        Main method to generate P&ID from PFD analysis
        
        Args:
            pfd_analysis: Dict containing PFD comprehensive analysis
            output_path: Where to save generated P&ID image
        
        Returns:
            Dict with P&ID specifications and image path
        """
        logger.info("🎨 Starting Professional P&ID Generation")
        logger.info("=" * 100)
        
        # Step 1: Expand equipment with detailed specifications
        logger.info("\n📦 Step 1: Expanding Equipment Details")
        expanded_equipment = self._expand_equipment(pfd_analysis)
        
        # Step 2: Generate detailed piping with valves
        logger.info("\n🔧 Step 2: Generating Detailed Piping Network")
        detailed_piping = self._generate_piping_details(pfd_analysis, expanded_equipment)
        
        # Step 3: Add comprehensive instrumentation
        logger.info("\n📊 Step 3: Adding Instrumentation and Control Loops")
        instrumentation = self._add_instrumentation(expanded_equipment, detailed_piping)
        
        # Step 4: Integrate safety systems
        logger.info("\n🛡️ Step 4: Integrating Safety Systems")
        safety_systems = self._add_safety_systems(expanded_equipment, detailed_piping)
        
        # Step 5: Add utility connections
        logger.info("\n⚙️ Step 5: Adding Utility Connections")
        utilities = self._add_utilities(expanded_equipment, instrumentation)
        
        # Step 6: Compile complete P&ID specification
        logger.info("\n📋 Step 6: Compiling P&ID Specifications")
        pid_spec = self._compile_pid_specification(
            pfd_analysis, expanded_equipment, detailed_piping,
            instrumentation, safety_systems, utilities
        )
        
        # Step 7: Generate professional P&ID drawing using AI
        logger.info("\n🎨 Step 7: Generating Professional P&ID Drawing")
        image_path = self._generate_pid_drawing(pid_spec, output_path)
        
        logger.info("\n" + "=" * 100)
        logger.info("✅ P&ID Generation Complete!")
        logger.info("=" * 100)
        
        return {
            "specification": pid_spec,
            "image_path": image_path,
            "equipment_count": len(expanded_equipment),
            "instrument_count": len(instrumentation),
            "valve_count": len(detailed_piping.get("valves", [])),
            "safety_device_count": len(safety_systems)
        }
    
    def _expand_equipment(self, pfd_analysis):
        """Expand PFD equipment with P&ID details"""
        expanded = []
        
        for equip in pfd_analysis.get("all_equipment", []):
            equip_type = self._determine_equipment_type(equip)
            rules = self.config["equipment_expansion"].get(equip_type, {})
            
            expanded_equip = {
                **equip,
                "nozzles": self._generate_nozzles(equip, rules),
                "instruments": self._get_required_instruments(equip, rules),
                "safety_devices": rules.get("safety_devices", []),
                "internals": rules.get("internals_to_show", []),
                "type": equip_type
            }
            
            expanded.append(expanded_equip)
            logger.info(f"   ✓ Expanded {equip.get('tag', 'Unknown')}: {equip_type}")
        
        return expanded
    
    def _generate_piping_details(self, pfd_analysis, equipment):
        """Generate detailed piping with valves"""
        piping = {
            "lines": [],
            "valves": []
        }
        
        # Process existing piping from PFD
        for pipe in pfd_analysis.get("all_piping", []):
            # Add isolation valves
            isolation_valves = self._add_isolation_valves(pipe, equipment)
            
            # Add control valves if needed
            control_valves = self._add_control_valves(pipe, equipment)
            
            # Add check valves where needed
            check_valves = self._add_check_valves(pipe, equipment)
            
            detailed_line = {
                **pipe,
                "isolation_valves": isolation_valves,
                "control_valves": control_valves,
                "check_valves": check_valves
            }
            
            piping["lines"].append(detailed_line)
            piping["valves"].extend(isolation_valves + control_valves + check_valves)
            
            logger.info(f"   ✓ Detailed line {pipe.get('line_number', 'Unknown')}: "
                       f"{len(isolation_valves)} ISO, {len(control_valves)} CV, {len(check_valves)} CHK")
        
        return piping
    
    def _add_instrumentation(self, equipment, piping):
        """Add comprehensive instrumentation"""
        instruments = []
        
        for equip in equipment:
            # Add required instruments based on equipment type
            for inst_type in equip.get("instruments", []):
                instrument = self._create_instrument(inst_type, equip)
                instruments.append(instrument)
                logger.info(f"   ✓ Added {instrument['tag']}: {instrument['type']}")
        
        return instruments
    
    def _add_safety_systems(self, equipment, piping):
        """Add safety systems (PSVs, ESDs, trips)"""
        safety = []
        
        for equip in equipment:
            # Add PSVs
            for psv_type in equip.get("safety_devices", []):
                if "PSV" in psv_type:
                    psv = self._create_psv(equip)
                    safety.append(psv)
                    logger.info(f"   ✓ Added {psv['tag']}: PSV to {psv['discharge_to']}")
            
            # Add ESD valves
            esd_valves = self._add_esd_valves(equip)
            safety.extend(esd_valves)
        
        return safety
    
    def _add_utilities(self, equipment, instruments):
        """Add utility connections"""
        utilities = {
            "instrument_air": [],
            "drains": [],
            "vents": []
        }
        
        # Add instrument air to pneumatic devices
        for inst in instruments:
            if "pneumatic" in inst.get("signal_type", "").lower():
                utilities["instrument_air"].append({
                    "connection_to": inst["tag"],
                    "line_size": "1 inch",
                    "pressure": "6 barg"
                })
        
        # Add drains and vents to equipment
        for equip in equipment:
            utilities["drains"].append({
                "equipment": equip["tag"],
                "location": "bottom",
                "size": "3/4 inch"
            })
            utilities["vents"].append({
                "equipment": equip["tag"],
                "location": "top",
                "size": "3/4 inch"
            })
        
        logger.info(f"   ✓ Added {len(utilities['instrument_air'])} IA connections")
        logger.info(f"   ✓ Added {len(utilities['drains'])} drains")
        logger.info(f"   ✓ Added {len(utilities['vents'])} vents")
        
        return utilities
    
    def _compile_pid_specification(self, pfd_analysis, equipment, piping, 
                                    instrumentation, safety, utilities):
        """Compile complete P&ID specification"""
        
        drawing_info = pfd_analysis.get("drawing_information", {})
        
        spec = {
            "drawing_information": {
                "number": drawing_info.get("drawing_number", "") + " - P&ID",
                "title": drawing_info.get("drawing_title", "").replace("PFD", "P&ID"),
                "revision": drawing_info.get("revision", "A"),
                "project": drawing_info.get("project_name", ""),
                "standard": "ISA S5.1",
                "scale": "NTS"
            },
            "equipment": equipment,
            "piping": piping,
            "instrumentation": instrumentation,
            "safety_systems": safety,
            "utilities": utilities,
            "notes": self._generate_notes(pfd_analysis)
        }
        
        return spec
    
    def _generate_pid_drawing(self, pid_spec, output_path=None):
        """Generate professional P&ID drawing using DALL-E"""
        
        # Build detailed drawing prompt
        prompt = self._build_drawing_prompt(pid_spec)
        
        logger.info("   🎨 Generating P&ID image with DALL-E 3...")
        
        try:
            response = self.client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1792x1024",
                quality="hd",
                n=1,
                style="natural"
            )
            
            image_url = response.data[0].url
            logger.info(f"   ✅ Image generated: {image_url}")
            
            # Download and save if output_path provided
            if output_path:
                import requests
                img_data = requests.get(image_url).content
                with open(output_path, 'wb') as f:
                    f.write(img_data)
                logger.info(f"   💾 Saved to: {output_path}")
                return output_path
            
            return image_url
            
        except Exception as e:
            logger.error(f"   ❌ Drawing generation failed: {e}")
            raise
    
    def _build_drawing_prompt(self, pid_spec):
        """Build detailed prompt for DALL-E P&ID generation"""
        
        drawing_info = pid_spec["drawing_information"]
        equipment = pid_spec["equipment"]
        instrumentation = pid_spec["instrumentation"]
        piping = pid_spec["piping"]
        
        prompt = f"""Create a professional Piping and Instrumentation Diagram (P&ID) for:
Drawing: {drawing_info['number']}
Title: {drawing_info['title']}

CRITICAL REQUIREMENTS - PROFESSIONAL P&ID STYLE:
- Use standard ISA S5.1 symbols - NO sketches or artistic interpretations
- Black lines on white background - clean technical drawing
- Equipment shown as proper engineering symbols (vessels as cylinders, pumps as circles with impeller)
- All piping as single lines with proper connections
- Instruments shown as circles with standard letter codes
- Valves shown as proper symbols (gate, globe, control, safety)
- Professional engineering drawing appearance - NOT artistic or fancy

EQUIPMENT TO DRAW:
"""
        
        # Add equipment details
        for eq in equipment:
            prompt += f"\n- {eq['tag']}: {eq.get('description', 'Vessel')}"
            prompt += f"\n  Size: {eq.get('dimensions', 'Standard')}"
            prompt += f"\n  Material: {eq.get('material', 'CS')}"
            prompt += f"\n  Nozzles: {', '.join([n['name'] for n in eq.get('nozzles', [])])}"
            if eq.get('internals'):
                prompt += f"\n  Internals: {', '.join(eq['internals'])}"
        
        prompt += "\n\nPIPING LINES:"
        for line in piping.get("lines", []):
            prompt += f"\n- Line {line.get('line_number')}: {line.get('size')} {line.get('class')} {line.get('material')}"
            prompt += f"\n  From: {line.get('from_equipment')} To: {line.get('to_equipment')}"
            
            # Add valves on this line
            for valve in line.get("isolation_valves", []):
                prompt += f"\n  • Isolation valve {valve['tag']} at {valve['location']}"
            for valve in line.get("control_valves", []):
                prompt += f"\n  • Control valve {valve['tag']} at {valve['location']}"
        
        prompt += "\n\nINSTRUMENTATION:"
        for inst in instrumentation:
            prompt += f"\n- {inst['tag']}: {inst['type']}"
            prompt += f"\n  Range: {inst.get('range', 'Standard')}"
            prompt += f"\n  Location: {inst.get('location', 'On equipment')}"
            if inst.get('control_loop'):
                prompt += f"\n  Control Loop: {inst['control_loop']}"
        
        prompt += "\n\nSAFETY DEVICES:"
        for safety in pid_spec.get("safety_systems", []):
            prompt += f"\n- {safety['tag']}: {safety['type']}"
            if safety.get('set_pressure'):
                prompt += f" (Set: {safety['set_pressure']})"
            if safety.get('discharge_to'):
                prompt += f" → {safety['discharge_to']}"
        
        prompt += """\n\nDRAWING STANDARDS:
- Professional P&ID layout (equipment arranged logically)
- All equipment labeled with clear tags
- All piping lines shown as single lines with proper connections
- All instruments shown as circles with standard ISA symbols (PT, FT, LT, etc.)
- All valves shown with standard symbols
- Flow direction arrows on main lines
- Title block with drawing number and title
- Clean, technical, professional appearance
- Black and white technical drawing style
- NO artistic effects, NO sketches, NO fancy graphics
- Industry standard P&ID format"""
        
        return prompt
    
    # Helper methods
    def _determine_equipment_type(self, equip):
        """Determine equipment type from description"""
        desc = equip.get("description", "").lower()
        if "vessel" in desc or "drum" in desc or "kod" in desc or "separator" in desc:
            return "vessel"
        elif "pump" in desc:
            return "pump"
        elif "exchanger" in desc or "cooler" in desc or "heater" in desc:
            return "heat_exchanger"
        return "vessel"  # Default
    
    def _generate_nozzles(self, equip, rules):
        """Generate nozzle list for equipment"""
        nozzles = []
        for nozzle_name in rules.get("required_nozzles", []):
            nozzles.append({
                "name": nozzle_name,
                "size": self._estimate_nozzle_size(equip, nozzle_name),
                "rating": equip.get("class", "300#"),
                "orientation": self._get_nozzle_orientation(nozzle_name)
            })
        return nozzles
    
    def _estimate_nozzle_size(self, equip, nozzle_name):
        """Estimate nozzle size based on equipment and function"""
        if "inlet" in nozzle_name or "outlet" in nozzle_name:
            # Main process nozzles - use line size if available
            return "16 inch"  # Default large
        elif "drain" in nozzle_name or "vent" in nozzle_name:
            return "3/4 inch"
        elif "gauge" in nozzle_name or "instrument" in nozzle_name:
            return "1/2 inch"
        return "2 inch"
    
    def _get_nozzle_orientation(self, nozzle_name):
        """Get typical nozzle orientation"""
        if "top" in nozzle_name or "vent" in nozzle_name or "level" in nozzle_name:
            return "top"
        elif "bottom" in nozzle_name or "drain" in nozzle_name:
            return "bottom"
        elif "outlet" in nozzle_name:
            return "side"
        return "side"
    
    def _get_required_instruments(self, equip, rules):
        """Get list of required instrument types"""
        return rules.get("typical_instruments", [])
    
    def _add_isolation_valves(self, pipe, equipment):
        """Add isolation valves to piping"""
        valves = []
        
        # Add valve at equipment inlet
        valves.append({
            "tag": f"HV-{pipe.get('line_number', '0000')[-4:]}-01",
            "type": "gate_valve",
            "size": pipe.get("size", "16 inch"),
            "class": pipe.get("class", "300#"),
            "location": "equipment_inlet",
            "actuation": "manual"
        })
        
        # Add valve at equipment outlet
        valves.append({
            "tag": f"HV-{pipe.get('line_number', '0000')[-4:]}-02",
            "type": "gate_valve",
            "size": pipe.get("size", "16 inch"),
            "class": pipe.get("class", "300#"),
            "location": "equipment_outlet",
            "actuation": "manual"
        })
        
        return valves
    
    def _add_control_valves(self, pipe, equipment):
        """Add control valves if needed"""
        valves = []
        
        # Add control valve if pressure/flow control needed
        if "export" in pipe.get("description", "").lower():
            valves.append({
                "tag": f"PCV-{pipe.get('line_number', '0000')[-4:]}-01",
                "type": "control_valve",
                "size": pipe.get("size", "16 inch"),
                "class": pipe.get("class", "300#"),
                "location": "downstream",
                "actuation": "pneumatic",
                "fail_position": "fail_open",
                "control_loop": "PIC-3601-01"
            })
        
        return valves
    
    def _add_check_valves(self, pipe, equipment):
        """Add check valves where needed"""
        valves = []
        
        # Add check valve if pump discharge or backflow prevention needed
        if "export" in pipe.get("description", "").lower():
            valves.append({
                "tag": f"CV-{pipe.get('line_number', '0000')[-4:]}-01",
                "type": "check_valve",
                "size": pipe.get("size", "16 inch"),
                "class": pipe.get("class", "300#"),
                "location": "after_control_valve"
            })
        
        return valves
    
    def _create_instrument(self, inst_type, equip):
        """Create instrument specification"""
        tag = equip.get("tag", "0000")
        area = tag.split("-")[0] if "-" in tag else "3601"
        
        if inst_type == "PT":
            return {
                "tag": f"PT-{area}-01",
                "type": "Pressure Transmitter",
                "range": "0-25 barg",
                "location": f"{tag} outlet",
                "signal_type": "4-20mA",
                "function": "Pressure monitoring and control",
                "control_loop": f"PIC-{area}-01"
            }
        elif inst_type == "LT":
            return {
                "tag": f"LT-{area}-01",
                "type": "Level Transmitter",
                "range": "0-100%",
                "location": f"{tag} side",
                "signal_type": "4-20mA",
                "function": "Level monitoring",
                "control_loop": None
            }
        elif inst_type == "TT":
            return {
                "tag": f"TT-{area}-01",
                "type": "Temperature Transmitter",
                "range": "-40 to 80°C",
                "location": f"{tag} bottom",
                "signal_type": "4-20mA",
                "function": "Temperature monitoring",
                "control_loop": None
            }
        
        return {"tag": f"{inst_type}-{area}-01", "type": inst_type}
    
    def _create_psv(self, equip):
        """Create PSV specification"""
        tag = equip.get("tag", "0000")
        area = tag.split("-")[0] if "-" in tag else "3601"
        
        return {
            "tag": f"PSV-{area}-01",
            "type": "Pressure Safety Valve",
            "set_pressure": equip.get("design_pressure", "20 barg"),
            "size": "3 inch",
            "discharge_to": "HP Flare",
            "location": f"{tag} top",
            "relief_capacity": "Sized per API 520/521"
        }
    
    def _add_esd_valves(self, equip):
        """Add ESD valves for equipment"""
        tag = equip.get("tag", "0000")
        area = tag.split("-")[0] if "-" in tag else "3601"
        
        return [{
            "tag": f"SDV-{area}-01",
            "type": "Shutdown Valve",
            "size": "16 inch",
            "class": "300#",
            "location": f"{tag} inlet",
            "actuation": "pneumatic",
            "fail_position": "fail_closed",
            "interlock": "High pressure trip, Emergency shutdown"
        }]
    
    def _generate_notes(self, pfd_analysis):
        """Generate general notes for P&ID"""
        return [
            "All instruments and control valves pneumatically operated",
            "All pressure ratings at design temperature",
            "Refer to piping specification for line details",
            "Safety valves sized per API 520/521",
            "Control system: DCS with field instruments",
            "Drawing follows ISA S5.1 standards"
        ]


def main():
    """Main execution"""
    
    # Load PFD analysis
    pfd_analysis_path = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend\P16093_PFD_Analysis.json"
    
    logger.info("=" * 100)
    logger.info("PROFESSIONAL P&ID GENERATOR FROM PFD")
    logger.info("=" * 100)
    
    logger.info(f"\n📂 Loading PFD analysis: {pfd_analysis_path}")
    with open(pfd_analysis_path, 'r') as f:
        pfd_analysis = json.load(f)
    
    logger.info(f"✅ PFD loaded:")
    logger.info(f"   - Equipment: {len(pfd_analysis.get('all_equipment', []))}")
    logger.info(f"   - Piping: {len(pfd_analysis.get('all_piping', []))}")
    logger.info(f"   - Instruments: {len(pfd_analysis.get('all_instruments', []))}")
    
    # Generate P&ID
    output_dir = Path(r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend")
    output_image = output_dir / "Generated_PID_P16093.png"
    output_spec = output_dir / "Generated_PID_P16093_Specification.json"
    
    generator = ProfessionalPIDGenerator()
    result = generator.generate_pid_from_pfd(pfd_analysis, str(output_image))
    
    # Save specification
    with open(output_spec, 'w') as f:
        json.dump(result["specification"], f, indent=2)
    
    logger.info(f"\n📁 Output Files:")
    logger.info(f"   1. P&ID Image: {output_image}")
    logger.info(f"   2. P&ID Specification: {output_spec}")
    
    logger.info(f"\n📊 Generation Summary:")
    logger.info(f"   - Equipment: {result['equipment_count']}")
    logger.info(f"   - Instruments: {result['instrument_count']}")
    logger.info(f"   - Valves: {result['valve_count']}")
    logger.info(f"   - Safety Devices: {result['safety_device_count']}")
    
    logger.info("\n" + "=" * 100)
    logger.info("✅ PROFESSIONAL P&ID GENERATION COMPLETE!")
    logger.info("=" * 100)


if __name__ == "__main__":
    main()
