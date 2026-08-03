"""
Enhanced PFD Extraction Prompt Generator with RAG (Retrieval Augmented Generation)
This module uses knowledge base patterns to improve AI extraction accuracy
"""
import json
import os
from pathlib import Path
from typing import Dict, List

class PFDPromptEnhancer:
    """
    Enhances PFD extraction prompts with learned patterns from knowledge base
    Uses few-shot learning and contextual examples
    """
    
    def __init__(self):
        self.knowledge_base_path = Path('pfd_knowledge_base.json')
        self.training_examples_path = Path('pfd_training_examples.json')
        self.knowledge_base = self._load_knowledge_base()
        self.training_examples = self._load_training_examples()
    
    def _load_knowledge_base(self) -> Dict:
        """Load PFD patterns from knowledge base"""
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
    
    def get_enhanced_extraction_prompt(self) -> str:
        """
        Generate an enhanced prompt with learned patterns and examples
        """
        # Build contextual examples from knowledge base
        examples_text = self._build_examples_section()
        
        # Build pattern recognition guidelines
        patterns_text = self._build_patterns_section()
        
        enhanced_prompt = f"""
You are an expert Process Engineer specializing in PFD (Process Flow Diagram) analysis for oil & gas projects.
Your task is to extract detailed process information from this PFD image following ADNOC standards.

{patterns_text}

{examples_text}

EXTRACTION REQUIREMENTS:

1. EQUIPMENT IDENTIFICATION (CRITICAL):
   - Extract ALL equipment tags with format: XXX-Y-ZZZ
     * XXX = Unit number (e.g., 562, 555, 558)
     * Y = Equipment type (P=Pump, V=Vessel/Drum, E=Exchanger/Cooler, C=Compressor, T=Tower)
     * ZZZ = Sequential number
   - Equipment types to look for:
     ✓ Pumps (P): Centrifugal, positive displacement
     ✓ Vessels/Drums (V): Collection drums, separators, flash drums
     ✓ Heat Exchangers (E): Coolers, condensers, heaters
     ✓ Compressors (C): Gas compression equipment
     ✓ Towers/Columns (T): Distillation, absorption columns
     ✓ Tanks: Storage vessels
   - For EACH equipment, extract:
     * Equipment tag (e.g., 562-V-203)
     * Equipment type/description (e.g., "GRADE-2 CONDENSATE COLLECTION DRUM")
     * Size/capacity specifications (e.g., "1600 mm (I.D), 4000 mm (T.T.)")
     * Design duty (e.g., "1.92 Gcal/hr")
     * Operating parameters (flow rate, pressure, temperature)

2. PROCESS STREAMS (CRITICAL):
   - Identify ALL streams between equipment
   - For each stream extract:
     * Source equipment tag
     * Destination equipment tag
     * Stream description (e.g., "GRADE 1 STEAM CONDENSATE")
     * Flow rate with units (kg/hr, m³/hr)
     * Temperature with units (°C, K)
     * Pressure with units (bar, psig, kPa)
     * Phase (liquid/gas/two-phase)
     * Stream number if visible

3. UTILITY SYSTEMS:
   - Steam systems: Grade-1, Grade-2, HP steam, etc.
   - Condensate recovery systems
   - Cooling water systems
   - Nitrogen systems
   - Fuel gas systems
   - Instrument air / Plant air

4. PROCESS CONDITIONS:
   - Operating temperatures and pressures
   - Flow rates and mass balances
   - Design capacities
   - Normal operating range vs design range

5. UNITS AND CONNECTIONS:
   - Unit numbers (e.g., "UNIT 562", "UNIT 555")
   - Inter-unit connections
   - Feed sources
   - Product destinations
   - Recycle streams

6. CONTROL SYSTEMS:
   - Control valves and their tags
   - Measurement points (TC, PT, FT, LT)
   - Control loops
   - Interlock systems

7. SAFETY SYSTEMS:
   - Relief valves (PSV)
   - Rupture discs
   - Emergency shutdown systems (ESD)
   - Pressure relief systems

8. PIPING AND CONNECTIONS:
   - Inlet/outlet connections for each equipment
   - Piping size specifications if visible
   - Branch connections

9. NOTES AND SPECIFICATIONS:
   - Extract all numbered notes
   - Design basis information
   - Project references
   - MOC (Management of Change) numbers
   - Drawing revisions and dates

10. SPECIAL EXTRACTION RULES:
    - Look for clouded/highlighted areas indicating scope of work
    - Extract modification notes (e.g., "NEW SYSTEM", "MODIFIED")
    - Capture pump specifications (capacity, head)
    - Note any redundant equipment (A/B designation)
    - Include vessel internals if shown

IMPORTANT EXTRACTION TIPS FROM LEARNED PATTERNS:
- PFDs often show utility systems (steam, condensate) rather than main process
- Look for equipment arranged in logical process flow sequence
- Condensate collection systems typically include: Drum → Cooler → Pump
- Equipment tags follow unit-type-number format consistently
- Notes sections contain critical design and modification information
- Units are referenced by number and connected through piping

OUTPUT FORMAT:
Return a comprehensive JSON structure with ALL extracted information:

{{
  "document_info": {{
    "drawing_number": "Drawing number from PFD",
    "title": "Document title",
    "revision": "Revision letter/number",
    "date": "Issue date",
    "project": "Project name/number",
    "unit": "Process unit(s) covered"
  }},
  "equipment": [
    {{
      "tag": "Equipment tag (e.g., 562-V-203)",
      "type": "Equipment type category",
      "description": "Full equipment description",
      "specifications": {{
        "size": "Equipment size",
        "capacity": "Design capacity",
        "duty": "Heat duty if applicable",
        "head": "Pump head if applicable"
      }},
      "operating_conditions": {{
        "pressure": "Operating pressure",
        "temperature": "Operating temperature",
        "flow_rate": "Flow rate"
      }},
      "connections": {{
        "inlet": ["Source equipment/lines"],
        "outlet": ["Destination equipment/lines"]
      }},
      "notes": "Special notes about this equipment"
    }}
  ],
  "process_streams": [
    {{
      "stream_id": "Stream identifier",
      "description": "Stream description",
      "from_equipment": "Source equipment tag",
      "to_equipment": "Destination equipment tag",
      "phase": "Gas/Liquid/Two-phase",
      "flow_rate": "Flow rate with units",
      "pressure": "Operating pressure",
      "temperature": "Operating temperature",
      "composition": "Key components if shown"
    }}
  ],
  "utility_systems": {{
    "steam": [
      {{
        "grade": "Steam grade",
        "source": "Steam source",
        "users": ["Equipment using steam"],
        "conditions": "Steam conditions"
      }}
    ],
    "condensate": [
      {{
        "grade": "Condensate grade",
        "collection_drum": "Drum tag",
        "pumps": ["Pump tags"],
        "destinations": ["Where condensate goes"]
      }}
    ],
    "cooling_water": [],
    "nitrogen": [],
    "fuel_gas": [],
    "instrument_air": []
  }},
  "control_systems": [
    {{
      "type": "Control type (TC, PT, FT, LT)",
      "tag": "Instrument tag if visible",
      "location": "Equipment or line",
      "function": "What it controls/measures"
    }}
  ],
  "safety_systems": [
    {{
      "type": "Safety device type",
      "tag": "Device tag",
      "location": "Installation point",
      "set_pressure": "Relief pressure if shown"
    }}
  ],
  "notes": [
    {{
      "number": "Note number",
      "content": "Full note text"
    }}
  ],
  "design_basis": {{
    "process_description": "Brief process description",
    "capacity": "Design capacity",
    "scope": "Scope of work/modifications"
  }}
}}

CRITICAL REMINDERS:
- Extract EVERY visible equipment tag
- Do NOT invent or assume data - only extract what is clearly visible
- Include units with all numerical values
- Capture ALL notes and annotations
- List all equipment connections
- If something is unclear, note it but still include it
- Be especially thorough with steam/condensate systems as they are common in oil & gas facilities

Begin extraction now, focusing on completeness and accuracy.
"""
        return enhanced_prompt
    
    def _build_examples_section(self) -> str:
        """Build few-shot learning examples section"""
        if not self.training_examples:
            return ""
        
        examples = []
        for i, example in enumerate(self.training_examples[:3], 1):
            examples.append(f"""
Example {i} - Pattern from: {example.get('document', 'Unknown')}
  Equipment Found: {', '.join(example.get('equipment_found', []))}
  Has Process Data: {'Yes' if example.get('has_process_data') else 'No'}
  Approach: {example.get('extraction_approach', 'Standard extraction')}
""")
        
        return f"""
LEARNED PATTERNS FROM SIMILAR PFDs:
Based on analysis of {len(self.training_examples)} similar PFD documents in our database:
{''.join(examples)}
"""
    
    def _build_patterns_section(self) -> str:
        """Build pattern recognition section"""
        if not self.knowledge_base or not self.knowledge_base.get('documents'):
            return ""
        
        # Analyze common patterns from documents
        documents = self.knowledge_base.get('documents', [])
        total_docs = len(documents)
        with_equipment = sum(1 for kb in documents if kb.get('has_equipment'))
        with_flow_info = sum(1 for kb in documents if kb.get('has_flow_info'))
        
        # Collect all equipment types
        all_equipment = []
        for kb in documents:
            all_equipment.extend(kb.get('equipment_types', []))
        common_equipment = list(set(all_equipment))
        
        # Add SFILES2 notation system context if available
        sfiles2_context = ""
        sfiles2_data = self.knowledge_base.get('sfiles2_integration')
        if sfiles2_data:
            unit_mappings = sfiles2_data.get('unit_operation_mappings', {})
            insights = sfiles2_data.get('insights', {})
            
            # Create unit abbreviations guide
            abbrev_guide = []
            for abbr, full_name in list(unit_mappings.items())[:15]:  # Show first 15
                if abbr not in ['X', 'C']:  # Skip generic ones
                    abbrev_guide.append(f"  - {abbr}: {full_name}")
            
            sfiles2_context = f"""

SFILES2 NOTATION SYSTEM INTEGRATION:
Research-backed process engineering abbreviations to recognize:
{chr(10).join(abbrev_guide)}

Key Concepts from SFILES 2.0:
- Unit operations are typically abbreviated (e.g., pump/pp, hex, dist, flash)
- Process flows connect equipment in sequence: feed → process → separation → product
- Common patterns: (raw)(pump)(hex)(reactor)(separator)(dist)(product)
- Utility systems: steam, condensate, cooling water, nitrogen
- Heat integration: exchangers may be heat integrated for energy efficiency
"""
        
        return f"""
PATTERN RECOGNITION CONTEXT:
Analysis of {total_docs} similar PFD documents from real projects shows:
- {with_equipment}/{total_docs} documents contain equipment specifications
- {with_flow_info}/{total_docs} documents include flow rate information
- Common equipment types: {', '.join(common_equipment) if common_equipment else 'Various'}
- These PFDs typically represent utility systems (steam, condensate, cooling water)
{sfiles2_context}
"""

# Singleton instance
_prompt_enhancer = None

def get_enhanced_prompt() -> str:
    """Get the enhanced extraction prompt"""
    global _prompt_enhancer
    if _prompt_enhancer is None:
        _prompt_enhancer = PFDPromptEnhancer()
    return _prompt_enhancer.get_enhanced_extraction_prompt()
