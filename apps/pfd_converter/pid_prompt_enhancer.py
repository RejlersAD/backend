"""
Enhanced P&ID Generation Prompt with RAG (Retrieval Augmented Generation)
Uses learned patterns from real P&ID documents to improve generation accuracy
"""
import json
from pathlib import Path
from typing import Dict, List

class PIDPromptEnhancer:
    """
    Enhances P&ID generation prompts with learned instrumentation and piping patterns
    """
    
    def __init__(self):
        self.knowledge_base_path = Path('pid_knowledge_base.json')
        self.training_examples_path = Path('pid_training_examples.json')
        self.knowledge_base = self._load_knowledge_base()
        self.training_examples = self._load_training_examples()
    
    def _load_knowledge_base(self) -> Dict:
        """Load P&ID patterns from knowledge base"""
        if self.knowledge_base_path.exists():
            with open(self.knowledge_base_path, 'r') as f:
                data = json.load(f)
                # Handle both old (list) and new (dict) formats
                if isinstance(data, list):
                    return {'documents': data}
                return data
        return {'documents': []}
    
    def _load_training_examples(self) -> List[Dict]:
        """Load training examples"""
        if self.training_examples_path.exists():
            with open(self.training_examples_path, 'r') as f:
                return json.load(f)
        return []
    
    def get_enhanced_pid_generation_prompt(self, pfd_data: Dict) -> str:
        """
        Generate an enhanced P&ID generation prompt with learned patterns
        """
        # Build pattern insights
        patterns_text = self._build_patterns_section()
        
        # Build instrumentation examples
        instrumentation_text = self._build_instrumentation_section()
        
        # Build examples from training data
        examples_text = self._build_examples_section()
        
        enhanced_prompt = f"""
Based on the provided PFD data, generate comprehensive P&ID specifications following oil & gas industry standards.
Your output will be used to create detailed engineering drawings.

{patterns_text}

{instrumentation_text}

{examples_text}

PFD DATA TO CONVERT:
{json.dumps(pfd_data, indent=2)}

P&ID GENERATION REQUIREMENTS:

1. INSTRUMENTATION (ISA 5.1 STANDARD):
   
   a) Flow Measurement & Control:
      - FT (Flow Transmitter): For all process streams
      - FIC (Flow Indicating Controller): For controlled flow loops
      - FCV (Flow Control Valve): Actuated control valves
      - FE (Flow Element): Orifice plates, venturi meters
      - FSL/FSH (Flow Switch Low/High): Flow alarms
   
   b) Pressure Measurement & Control:
      - PT (Pressure Transmitter): All critical pressure points
      - PIC (Pressure Indicating Controller): Pressure control loops
      - PCV (Pressure Control Valve): Pressure regulators
      - PI (Pressure Indicator): Local pressure gauges
      - PSV (Pressure Safety Valve): All pressure relief points
      - PSHH/PSLL (Pressure Switch High-High/Low-Low): Interlocks
      - PAH/PAL (Pressure Alarm High/Low): Alarm points
   
   c) Temperature Measurement & Control:
      - TT (Temperature Transmitter): All process streams
      - TIC (Temperature Indicating Controller): Temperature control
      - TCV (Temperature Control Valve): Temperature regulators
      - TE (Temperature Element): RTDs, thermocouples
      - TSH/TSL (Temperature Switch High/Low): Alarms
   
   d) Level Measurement & Control:
      - LT (Level Transmitter): All vessels and drums
      - LIC (Level Indicating Controller): Level control loops
      - LCV (Level Control Valve): Level control valves
      - LG (Level Gauge): Visual level indicators
      - LAH/LAL (Level Alarm High/Low): Level alarms
      - LAHH/LALL (Level Alarm High-High/Low-Low): Critical alarms
      - LSH/LSL (Level Switch High/Low): Level switches
   
   e) Analytical Instrumentation:
      - AT (Analyzer Transmitter): Composition, pH, conductivity
      - AIC (Analyzer Indicating Controller): Analytical control

2. VALVE SPECIFICATIONS:
   
   a) Isolation Valves:
      - Gate Valves: Full bore isolation
      - Ball Valves: Quarter-turn isolation
      - Tag format: XV-### (where ### is sequential number)
   
   b) Control Valves:
      - Control Valves: Modulating control (FCV, PCV, TCV, LCV)
      - Specifications: Size, Cv, actuator type (pneumatic/electric)
      - Fail position: FC (Fail Close), FO (Fail Open), FL (Fail Last)
   
   c) Check Valves:
      - Check Valves: Prevent reverse flow
      - Types: Swing, lift, dual-plate
   
   d) Safety/Relief Valves:
      - PSV: Pressure Safety Valve
      - Set pressure, capacity, discharge location
      - Tag format: PSV-###
   
   e) Special Valves:
      - Globe Valves: Throttling service
      - Butterfly Valves: Large diameter, low pressure drop
      - Needle Valves: Fine control
      - Block Valves: Double isolation

3. PIPING SPECIFICATIONS:
   
   a) Line Numbering:
      - Format: Size-Material-Service-Sequential
      - Example: 4"-CS-150#-001
      - Size: Nominal pipe size (NPS) or DN
      - Material: CS (Carbon Steel), SS (Stainless Steel), etc.
      - Rating: 150#, 300#, 600#, 900#, 1500#
   
   b) Line Classes:
      - Class 150: Low pressure services (up to 285 psig @ 100°F)
      - Class 300: Medium pressure (up to 740 psig @ 100°F)
      - Class 600: High pressure (up to 1480 psig @ 100°F)
      - Class 900: Very high pressure
      - Class 1500: Extreme pressure
   
   c) Piping Specifications:
      - Include for EACH line: Size, material, pressure rating
      - Insulation requirements (hot/cold)
      - Tracing requirements (steam/electric)
      - Special coatings if required

4. EQUIPMENT DETAILS:
   
   a) Equipment Numbering:
      - Pumps: P-### or ###-P-###
      - Vessels: V-### or ###-V-###
      - Heat Exchangers: E-### or ###-E-###
      - Compressors: C-### or ###-C-###
      - Tanks: TK-### or ###-TK-###
   
   b) For Each Equipment Include:
      - Equipment tag from PFD
      - Nozzle schedule: Size, rating, service for each connection
      - Elevation requirements if critical
      - Spare/standby designation (A/B notation)

5. PROCESS CONTROL PHILOSOPHY:
   
   a) Control Loops:
      - Primary control loops (Flow, Pressure, Temperature, Level)
      - Cascade control schemes
      - Ratio control
      - Split range control
   
   b) Interlocks & Safety:
      - High-High and Low-Low alarms
      - Emergency Shutdown (ESD) logic
      - Permissive interlocks
      - Sequential interlocks
   
   c) Control Logic:
      - Normal control logic
      - Start-up sequence
      - Shutdown sequence
      - Emergency scenarios

6. UTILITY CONNECTIONS:
   
   a) Utilities to Include:
      - Instrument Air: Supply to all pneumatic instruments/valves
      - Plant Air: Maintenance and operational needs
      - Cooling Water: Heat exchangers, coolers
      - Steam: Various grades (HP, MP, LP)
      - Nitrogen: Blanketing, purging
      - Fuel Gas: If applicable
   
   b) Utility Headers:
      - Header size and pressure
      - Branch connections with isolation valves
      - Drain and vent provisions

7. SAFETY SYSTEMS:
   
   a) Pressure Relief:
      - PSV on all pressure vessels
      - Relief capacity calculation basis
      - Discharge to flare/vent system
      - Block valves upstream and downstream
   
   b) Emergency Systems:
      - ESD valves with manual override
      - Fire water connections if required
      - Depressurization system
      - Emergency vents
   
   c) Process Safety:
      - High integrity protection systems (HIPS)
      - Safety instrumented functions (SIF)
      - SIL rated instruments/valves

8. DRAINAGE & VENTING:
   
   a) Drain Points:
      - Low point drains on all lines
      - Equipment drains
      - Drain collection system
      - Drain valve specifications
   
   b) Vent Points:
      - High point vents
      - Equipment vents
      - Vent collection/disposal
      - Atmospheric vs closed vents

9. SAMPLING & MONITORING:
   
   a) Sample Points:
      - Process sample points
      - Sample coolers if required
      - Sample conditioning
      - Safe sampling (closed loop)
   
   b) Monitoring:
      - Online analyzers
      - Corrosion monitoring points
      - Erosion monitoring

10. ADNOC SPECIFIC REQUIREMENTS:
    
    a) Standards Compliance:
       - ADNOC DEP 30.20.10.13 (P&ID standards)
       - ADNOC DEP 30.20.10.10 (Instrument specifications)
       - API 14C (Process safety)
       - ISA 5.1 (Instrumentation symbols)
    
    b) Drawing Details:
       - All notes and legends
       - Revision history
       - Material specifications
       - Operating conditions
    
    c) Safety Requirements:
       - All PSVs with set pressures
       - All ESD valves clearly marked
       - All critical alarms identified
       - All interlocks documented

OUTPUT FORMAT:
Return comprehensive JSON with ALL specifications:

{{
  "drawing_info": {{
    "pid_number": "P&ID drawing number",
    "title": "Drawing title from PFD",
    "area": "Process area/unit",
    "revision": "Initial or as specified"
  }},
  "instrumentation": [
    {{
      "tag": "Instrument tag (e.g., FT-101)",
      "type": "Instrument type full name",
      "service": "What it measures/controls",
      "location": "Equipment or line",
      "specifications": {{
        "range": "Measurement range",
        "accuracy": "Required accuracy",
        "signal": "4-20mA, digital, etc.",
        "power": "24VDC, 120VAC, etc."
      }},
      "io_type": "AI, AO, DI, DO",
      "safety_critical": "Yes/No",
      "sil_rating": "If applicable"
    }}
  ],
  "control_valves": [
    {{
      "tag": "Control valve tag (e.g., FCV-101)",
      "type": "Control valve type",
      "size": "Valve size",
      "cv": "Valve Cv coefficient",
      "actuator": "Pneumatic/Electric/Hydraulic",
      "fail_position": "FC/FO/FL",
      "location": "Line or equipment",
      "control_loop": "Associated instrument tag"
    }}
  ],
  "isolation_valves": [
    {{
      "tag": "Valve tag (e.g., XV-101)",
      "type": "Gate/Ball/Globe/Butterfly",
      "size": "Valve size matching line",
      "rating": "Pressure class",
      "location": "Line location",
      "service": "Isolation purpose",
      "normally": "Open/Closed",
      "operator": "Manual/Pneumatic/Motor"
    }}
  ],
  "check_valves": [
    {{
      "tag": "Check valve tag",
      "type": "Swing/Lift/Dual plate",
      "size": "Valve size",
      "location": "Line location",
      "service": "Purpose"
    }}
  ],
  "safety_valves": [
    {{
      "tag": "PSV tag (e.g., PSV-101)",
      "location": "Equipment protected",
      "set_pressure": "Relief set pressure with units",
      "capacity": "Relief capacity",
      "inlet_size": "Inlet connection size",
      "outlet_size": "Outlet connection size",
      "discharge_to": "Flare/Vent/Atmosphere",
      "sizing_basis": "Fire/Blocked outlet/etc."
    }}
  ],
  "piping_lines": [
    {{
      "line_number": "Complete line number",
      "from": "Source equipment/line",
      "to": "Destination equipment/line",
      "size": "NPS or DN with units",
      "material": "Piping material",
      "rating": "Pressure class",
      "fluid": "Fluid service",
      "insulation": "Type if required",
      "tracing": "Type if required",
      "special_requirements": "Any special specs"
    }}
  ],
  "equipment_nozzles": [
    {{
      "equipment_tag": "Equipment tag from PFD",
      "nozzle_id": "Nozzle marking (N1, N2, etc.)",
      "size": "Nozzle size and rating",
      "service": "Inlet/Outlet/Drain/Vent",
      "connection_type": "Flanged/Threaded/Welded",
      "elevation": "If critical"
    }}
  ],
  "utility_connections": [
    {{
      "utility_type": "IA/PA/CW/Steam/N2/FG",
      "connection_point": "Where connected",
      "size": "Connection size",
      "isolation_valve": "Valve tag",
      "header_pressure": "Normal pressure"
    }}
  ],
  "interlocks": [
    {{
      "interlock_id": "Interlock identifier",
      "type": "Permissive/Sequential/Emergency",
      "trigger": "What initiates the interlock",
      "action": "What happens",
      "instruments_involved": ["List of tags"],
      "valves_involved": ["List of valve tags"],
      "logic": "Brief logic description"
    }}
  ],
  "alarms": [
    {{
      "tag": "Alarm tag (e.g., PAH-101)",
      "type": "High/Low/High-High/Low-Low",
      "parameter": "Pressure/Temperature/Level/Flow",
      "setpoint": "Alarm setpoint",
      "action": "Operator action required"
    }}
  ],
  "notes": [
    {{
      "note_number": "Note sequence",
      "content": "Note text"
    }}
  ],
  "standards_compliance": {{
    "adnoc_deps": ["List of applicable DEPs"],
    "api_standards": ["List of applicable API standards"],
    "isa_standards": ["ISA 5.1 and others"],
    "material_specs": ["Material specifications"]
  }}
}}

CRITICAL REMINDERS:
- Include instrumentation for ALL process streams (Flow, Pressure, Temperature)
- Add level instrumentation for ALL vessels and drums
- Include PSV on EVERY pressure vessel
- Specify control valve fail positions (FC/FO/FL)
- Include utility connections (IA, CW, Steam, N2)
- Add drain and vent provisions
- Follow ISA 5.1 for all instrument tags
- Use ADNOC standard valve numbering
- Specify piping class and material for each line
- Include all safety interlocks and alarms
- Document all ESD systems
- Be thorough and complete - this is engineering documentation!

Generate complete, professional P&ID specifications that can be directly used for detailed engineering.
"""
        return enhanced_prompt
    
    def _build_patterns_section(self) -> str:
        """Build pattern recognition section from knowledge base"""
        if not self.knowledge_base or not self.knowledge_base.get('documents'):
            return ""
        
        documents = self.knowledge_base.get('documents', [])
        total_docs = len(documents)
        with_instruments = sum(1 for kb in documents 
                              if kb['instrumentation']['has_instruments'])
        with_valves = sum(1 for kb in documents 
                         if kb['valves']['has_valves'])
        with_piping = sum(1 for kb in documents 
                         if kb['piping']['has_piping_specs'])
        
        # Collect all instruments
        all_instruments = []
        for kb in documents:
            all_instruments.extend(kb['instrumentation']['instrument_types'])
        unique_instruments = list(set(all_instruments))
        
        # Collect all valve types
        all_valves = []
        for kb in documents:
            all_valves.extend(kb['valves']['valve_types'])
        unique_valves = list(set(all_valves))
        
        # Add SFILES2 notation system context if available
        sfiles2_context = ""
        sfiles2_data = self.knowledge_base.get('sfiles2_integration')
        if sfiles2_data:
            unit_mappings = sfiles2_data.get('unit_operation_mappings', {})
            insights = sfiles2_data.get('insights', {})
            
            # Create P&ID-relevant unit abbreviations
            pid_relevant = {}
            for abbr, full_name in unit_mappings.items():
                if any(keyword in full_name.lower() for keyword in 
                      ['valve', 'pump', 'compressor', 'exchanger', 'vessel', 'drum', 
                       'distill', 'reactor', 'flash', 'separator', 'filter', 'mixer']):
                    pid_relevant[abbr] = full_name
            
            if pid_relevant:
                abbrev_lines = [f"  - {abbr}: {name}" for abbr, name in list(pid_relevant.items())[:12]]
                sfiles2_context = f"""

SFILES2 PROCESS ENGINEERING NOTATION:
Industry-standard abbreviations from research (use for equipment naming):
{chr(10).join(abbrev_lines)}

Process Flow Understanding:
- Equipment connections follow logical process sequences
- Control loops protect equipment and maintain process stability
- Safety devices (PSV, rupture discs) prevent overpressure
- Measurement instruments enable monitoring and control
"""
        
        return f"""
LEARNED P&ID PATTERNS FROM {total_docs} REAL DOCUMENTS:
- {with_instruments}/{total_docs} documents contain instrumentation specifications
- {with_valves}/{total_docs} documents include valve specifications
- {with_piping}/{total_docs} documents have piping class specifications

Common Instrumentation Found: {', '.join(unique_instruments[:10]) if unique_instruments else 'Various'}
Common Valve Types: {', '.join(unique_valves[:5]) if unique_valves else 'Various'}

These P&IDs follow ISA 5.1 standards and ADNOC DEP requirements.
{sfiles2_context}
"""
    
    def _build_instrumentation_section(self) -> str:
        """Build instrumentation insights from knowledge base"""
        if not self.knowledge_base or not self.knowledge_base.get('documents'):
            return ""
        
        documents = self.knowledge_base.get('documents', [])
        
        # Count most common instruments
        all_instruments = []
        for kb in documents:
            all_instruments.extend(kb['instrumentation']['instrument_types'])
        
        from collections import Counter
        instrument_counts = Counter(all_instruments)
        top_instruments = instrument_counts.most_common(5)
        
        if not top_instruments:
            return ""
        
        instruments_text = []
        for instrument, count in top_instruments:
            instruments_text.append(f"  • {instrument}: Found in {count} documents")
        
        instruments_joined = '\n'.join(instruments_text)
        
        return f"""
MOST CRITICAL INSTRUMENTATION (Based on Frequency):
{instruments_joined}

These are the most commonly used instruments in similar projects. Ensure all are considered.
"""
    
    def _build_examples_section(self) -> str:
        """Build examples from training data"""
        if not self.training_examples:
            return ""
        
        examples = []
        for i, example in enumerate(self.training_examples[:3], 1):
            instruments = ', '.join(example.get('instrument_types', [])[:5])
            valves = ', '.join(example.get('valve_types', [])[:3])
            
            examples.append(f"""
Example {i} - Real P&ID Pattern:
  Document: {example.get('document', 'Unknown')[:60]}
  Instruments: {instruments if instruments else 'Various'}
  Valves: {valves if valves else 'Various'}
  Has Piping Specs: {'Yes' if example.get('has_piping_specs') else 'No'}
  Approach: {example.get('extraction_approach', 'Standard')}
""")
        
        examples_joined = ''.join(examples)
        
        return f"""
EXAMPLES FROM SIMILAR P&ID DOCUMENTS:
Based on {len(self.training_examples)} analyzed P&ID documents:
{examples_joined}
"""

# Singleton instance
_pid_prompt_enhancer = None

def get_enhanced_pid_prompt(pfd_data: Dict) -> str:
    """Get the enhanced P&ID generation prompt"""
    global _pid_prompt_enhancer
    if _pid_prompt_enhancer is None:
        _pid_prompt_enhancer = PIDPromptEnhancer()
    return _pid_prompt_enhancer.get_enhanced_pid_generation_prompt(pfd_data)
