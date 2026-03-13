"""
AI-Orchestrated MOV Datasheet Intelligence Layer
Smart mapping between extracted P&ID and HMB data for Motor Operated Valves
"""
import logging
import sys
from typing import Dict, List, Optional
from openai import OpenAI
from django.conf import settings
import json

logger = logging.getLogger(__name__)


def log_and_print(message):
    """Log to both logger and stderr (which Docker captures)"""
    logger.info(message)
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


class MOVDatasheetAIMapper:
    """
    AI Intelligence Layer for MOV Datasheet Generation
    
    Receives structured data from P&ID and HMB extraction
    Uses OpenAI to intelligently map and match fields
    Returns clean structured JSON for Excel generation
    
    POPULATES ONLY SECTION 1 & 2 - Sections 3 & 4 left blank
    """
    
    def __init__(self):
        """Initialize OpenAI client"""
        self.client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=60.0
        )
        log_and_print("[MOVDatasheetAIMapper] Initialized with OpenAI GPT-4")
    
    def map_pid_hmb_to_datasheet(
        self,
        pid_data: Dict,
        hmb_data: Dict,
        line_context: Optional[Dict] = None,
        line_list_data: Optional[Dict] = None
    ) -> Dict:
        """
        Intelligently map P&ID, HMB, and Line List data to MOV datasheet fields
        
        Args:
            pid_data: Structured data extracted from P&ID
            hmb_data: Structured data extracted from HMB
            line_context: Pre-mapped line associations (optional)
            line_list_data: Structured data extracted from Line List (optional)
        
        Returns:
            Dict with filled datasheet fields ready for Excel
        """
        log_and_print("[MOVDatasheetAIMapper] 🤖 Starting intelligent mapping...")
        
        try:
            # Build the system prompt with Line List support
            system_prompt = self._build_system_prompt()
            
            # Build the user prompt with structured data including Line List
            user_prompt = self._build_user_prompt(pid_data, hmb_data, line_context, line_list_data)
            
            log_and_print(f"[MOVDatasheetAIMapper] Sending to OpenAI GPT-4...")
            log_and_print(f"[MOVDatasheetAIMapper] 📊 INPUT DATA:")
            log_and_print(f"[MOVDatasheetAIMapper]   - P&ID valves: {len(pid_data.get('valves', []))}")
            log_and_print(f"[MOVDatasheetAIMapper]   - HMB streams: {len(hmb_data.get('streams', []))}")
            if line_list_data:
                log_and_print(f"[MOVDatasheetAIMapper]   - Line List entries: {len(line_list_data.get('lines', []))}")
            
            # Log sample valve and stream for debugging
            if pid_data.get('valves'):
                log_and_print(f"[MOVDatasheetAIMapper] 📄 Sample P&ID valve:")
                log_and_print(f"    {json.dumps(pid_data['valves'][0], indent=2)}")
            if hmb_data.get('streams'):
                log_and_print(f"[MOVDatasheetAIMapper] 🌡️ Sample HMB stream:")
                log_and_print(f"    {json.dumps(hmb_data['streams'][0], indent=2)}")
            
            # Call OpenAI
            response = self.client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            
            # Parse response
            result_text = response.choices[0].message.content
            mapped_data = json.loads(result_text)
            
            log_and_print(f"[MOVDatasheetAIMapper] ✅ AI mapping complete")
            log_and_print(f"[MOVDatasheetAIMapper] Mapped valves: {len(mapped_data.get('valves', []))}")
            
            # Log sample mapped valve
            if mapped_data.get('valves'):
                sample_valve = mapped_data['valves'][0]
                log_and_print(f"[MOVDatasheetAIMapper] 📋 Sample mapped valve (first valve):")
                log_and_print(f"    Tag: {sample_valve.get('tag_no')}")
                log_and_print(f"    ✅ SECTION 1 (General Data from P&ID):")
                log_and_print(f"       - PID No: {sample_valve.get('pid_no')}")
                log_and_print(f"       - Line No: {sample_valve.get('line_no')}")
                log_and_print(f"       - Service: {sample_valve.get('service')}")
                log_and_print(f"       - Piping Class: {sample_valve.get('piping_class')}")
                log_and_print(f"    🌡️ SECTION 2 (Operating Conditions from HMB):")
                log_and_print(f"       - Fluid: {sample_valve.get('fluid')}")
                log_and_print(f"       - State: {sample_valve.get('state')}")
                log_and_print(f"       - Phase: {sample_valve.get('phase')}")
                log_and_print(f"       - Operating Pressure: {sample_valve.get('operating_pressure_normal')} {sample_valve.get('pressure_unit')}")
                log_and_print(f"       - Operating Temp: {sample_valve.get('operating_temp_min')}/{sample_valve.get('operating_temp_max')} {sample_valve.get('operating_temp_unit')}")
            
            return mapped_data
            
        except Exception as e:
            logger.error(f"[MOVDatasheetAIMapper] ❌ Error: {e}")
            raise  # Re-raise to show clear error to user
    
    def _build_system_prompt(self) -> str:
        """Build the system prompt for AI - MOV SPECIFIC EXTRACTION WITH LINE LIST"""
        return """You are an expert engineering data extraction assistant for Motor Operated Valve (MOV) Datasheets.

🎯 MOV DATASHEET EXTRACTION RULES:

**STRUCTURE:**
- Section 1: General Data (FROM P&ID + Line List + Legend)
- Section 2: Operating Conditions (FROM HMB + Line List)
- Section 3: Valve Details (FROM P&ID + Vendor/Instrumentation)
- Section 4: Actuator Details (LEAVE BLANK for now)

**DATA SOURCE MAPPING:**

📋 SECTION 1 - GENERAL DATA:
Sources: P&ID (primary) + Line List + Legend
- tag_no: From P&ID (e.g., "MOV-100-001")
- service: From Legend P&ID (abbreviations) or P&ID description
- **pid_no: EXACTLY from pid_data['drawing_info']['pid_no']**
- line_no: From P&ID (match valve to line)
- piping_class: From **P&ID** (line specification on P&ID drawing)
- fluid: From **Line List** and **H&MB** (both sources)
- state: From Line List and H&MB (Liquid/Gas/Two-Phase)
- phase: From Line List and H&MB (Single/Multi-phase)

📋 SECTION 2 - OPERATING CONDITIONS:
Sources: H&MB (primary) + Line List (fallback)
- operating_pressure_min: From H&MB or calculated
- operating_pressure_normal: From **H&MB** or **Line List** "Normal Operating Pressure"
- operating_pressure_max: From H&MB or calculated
- pressure_unit: From H&MB
- operating_temp_min: From H&MB or calculated
- operating_temp_normal: From **H&MB** or **Line List** "Normal Operating Temperature"
- operating_temp_max: From H&MB or calculated
- operating_temp_unit: From H&MB
- design_pressure: From **Line List** (primary) or H&MB
- design_temp: From **Line List** (primary) or H&MB
- sour_service: From H2S content in H&MB (if H2S > 0 ppm → "Yes", else "No")
- shut_off_pressure: Upstream equipment max pressure (from P&ID equipment specs)

📋 SECTION 3 - VALVE DETAILS:
Sources: P&ID + Instrumentation Department + Vendor (if available)
- diff_pressure_delta_p: From hydraulic calculations (usually software-generated, leave null if not provided)
- fail_position: From **P&ID** (FO = Fail Open, FC = Fail Closed, FL = Fail Last)
- valve_close_time: From **Vendor inputs** (leave null if not provided)
- valve_open_time: From **Vendor inputs** (leave null if not provided)
- seat_leakage_class: From Vendor or standards (leave null if not provided)
- nace_compliant: From H2S content or specs (leave null if unknown)

🚫 SECTION 4 - ACTUATOR DETAILS (LEAVE AS NULL):
- actuator_type: null
- actuator_power: null
- actuator_voltage: null

**INTELLIGENT EXTRACTION STRATEGY:**

1. **TAG NO & P&ID NO**: Always from P&ID
2. **SERVICE**: Check Legend P&ID for abbreviations first, fallback to P&ID description
3. **LINE NO & PIPING CLASS**: 
   - Line number from P&ID
   - Piping class from P&ID (line specification on drawing)
4. **FLUID/STATE/PHASE**: Both Line List and H&MB (cross-reference both sources)
5. **OPERATING PRESSURE & TEMP**:
   - Normal values: H&MB primary, Line List fallback
   - Min/Max: Usually from calculations (not directly in documents)
6. **DESIGN PRESSURE & TEMP**: Line List (in general - overall design specs)
7. **SOUR SERVICE**: 
   - Check H&MB for H2S content
   - If H2S > 0 ppm or H2S mentioned → "Yes"
   - Otherwise → "No"
   - Note: Can be tricky to find in H&MB depending on format
8. **SHUT OFF PRESSURE**: 
   - Depends on MOV location
   - Use max possible pressure of upstream equipment
   - Look for upstream equipment in P&ID
9. **DIFF PRESSURE**: Usually from hydraulic calculation software, leave null if not provided
10. **FAIL POSITION**: Extract from P&ID symbols (FO=Fail Open, FC=Fail Closed, FL=Fail Last)
11. **VALVE CLOSE/OPEN TIME**: Generally from Vendor inputs, leave null if not provided

**LINE LIST DATA FORMAT:**
Line List typically contains:
- Line Number (matches valve line_no)
- Fluid Code & Description
- Piping Class/Spec
- Design Pressure
- Design Temperature
- Normal Operating Pressure
- Normal Operating Temperature
- Insulation requirements

**MATCHING STRATEGY:**
1. For each MOV valve in P&ID:
   - Extract TAG NO and P&ID NO from P&ID
   - Find associated LINE NO from P&ID
   - Match LINE NO to Line List → get piping_class, design_pressure, design_temp
   - Match LINE NO to H&MB streams → get fluid, operating conditions, H2S content
   - Check Legend for SERVICE abbreviation
   - Extract FAIL POSITION from P&ID (FO/FC/FL)
   - Calculate SHUT OFF PRESSURE from upstream equipment
   - Leave vendor-specific fields (valve times) as null unless provided

**EXAMPLE OUTPUT:**
Return ONLY valid JSON:
{
  "valves": [
    {
      "tag_no": "MOV-100-001",
      "service": "Natural Gas Isolation",
      "pid_no": "P-100-001-Rev-A",
      "line_no": "6\\"-GA-100-1501",
      "piping_class": "ASME B16.5 150#",
      "fluid": "Natural Gas",
      "state": "Gas",
      "phase": "Single Phase",
      "operating_pressure_normal": "75 barg",
      "operating_temp_normal": "25°C",
      "design_pressure": "90 barg",
      "design_temp": "85°C",
      "sour_service": "No",
      "shut_off_pressure": "95 barg",
      "fail_position": "FC",
      "diff_pressure_delta_p": null,
      "valve_close_time": null,
      "valve_open_time": null,
      "confidence": "high"
    }
  ],
  "overall_confidence": "high",
  "data_sources_used": ["P&ID", "H&MB", "Line List", "Legend"]
}"""
    
    def _flatten_hmb_streams(self, hmb_data: Dict) -> Dict:
        """
        Flatten nested HMB stream structure for AI
        """
        flattened_hmb = {
            'streams': [],
            'process_conditions': hmb_data.get('process_conditions', {})
        }
        
        for stream in hmb_data.get('streams', []):
            flat_stream = {
                'stream_id': stream.get('stream_id'),
                'line_no': stream.get('line_no'),
                'fluid': stream.get('fluid'),
                'phase': stream.get('phase'),
                'state': stream.get('state'),
                # Operating temperatures
                'operating_temp_min': stream.get('temperature', {}).get('min'),
                'operating_temp_normal': stream.get('temperature', {}).get('normal'),
                'operating_temp_max': stream.get('temperature', {}).get('max'),
                'operating_temp_unit': stream.get('temperature', {}).get('unit'),
                # Design temperatures
                'design_temp_min': stream.get('design_temperature', {}).get('min'),
                'design_temp_max': stream.get('design_temperature', {}).get('max'),
                'design_temp_unit': stream.get('design_temperature', {}).get('unit'),
                # Operating pressures
                'operating_pressure_min': stream.get('pressure', {}).get('min'),
                'operating_pressure_normal': stream.get('pressure', {}).get('normal'),
                'operating_pressure_max': stream.get('pressure', {}).get('max'),
                'operating_pressure_design': stream.get('pressure', {}).get('design'),
                'shut_off_pressure': stream.get('pressure', {}).get('shutoff'),
                'pressure_unit': stream.get('pressure', {}).get('unit'),
                # Design pressures
                'design_pressure_min': stream.get('design_pressure', {}).get('min', '0'),
                'design_pressure_max': stream.get('design_pressure', {}).get('max', stream.get('pressure', {}).get('design'))
            }
            flattened_hmb['streams'].append(flat_stream)
        
        return flattened_hmb
    
    def _build_user_prompt(
        self,
        pid_data: Dict,
        hmb_data: Dict,
        line_context: Optional[Dict],
        line_list_data: Optional[Dict] = None
    ) -> str:
        """Build user prompt with structured data including Line List"""
        
        # Flatten HMB data
        flattened_hmb = self._flatten_hmb_streams(hmb_data)
        
        prompt = f"""**EXTRACTED DATA FROM DOCUMENTS:**

**1. P&ID DATA:**
```json
{json.dumps(pid_data, indent=2)}
```

**2. H&MB DATA (FLATTENED):**
```json
{json.dumps(flattened_hmb, indent=2)}
```
"""
        
        if line_list_data:
            prompt += f"""
**3. LINE LIST DATA:**
```json
{json.dumps(line_list_data, indent=2)}
```

**LINE LIST USAGE INSTRUCTIONS:**
- Match LINE NO from P&ID valve to Line List entries
- Extract PIPING CLASS from Line List (primary source)
- Extract DESIGN PRESSURE from Line List
- Extract DESIGN TEMPERATURE from Line List
- Extract NORMAL OPERATING PRESSURE from Line List (if available)
- Extract NORMAL OPERATING TEMPERATURE from Line List (if available)
- Line List provides general design values for entire piping line
"""
        
        if line_context:
            prompt += f"""
**4. PRE-MAPPED LINE CONTEXT:**
```json
{json.dumps(line_context, indent=2)}
```
"""
        
        prompt += """

**YOUR TASK:**
Map MOV valve data intelligently from multiple sources:
1. P&ID → TAG NO, P&ID NO, LINE NO, FAIL POSITION (FO/FC/FL)
2. Line List → PIPING CLASS, DESIGN PRESSURE, DESIGN TEMP
3. H&MB → FLUID, STATE, PHASE, OPERATING CONDITIONS, H2S (for Sour Service)
4. Legend → SERVICE abbreviations
5. Calculate SHUT OFF PRESSURE from upstream equipment max pressure

Populate all available fields based on document sources.
Leave vendor-specific fields (valve times, diff pressure) as null unless provided.
Return complete datasheet data in the specified JSON format.
"""
        
        return prompt
