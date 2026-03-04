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
        line_context: Optional[Dict] = None
    ) -> Dict:
        """
        Intelligently map P&ID and HMB data to MOV datasheet fields
        
        Args:
            pid_data: Structured data extracted from P&ID
            hmb_data: Structured data extracted from HMB
            line_context: Pre-mapped line associations (optional)
        
        Returns:
            Dict with filled datasheet fields ready for Excel
        """
        log_and_print("[MOVDatasheetAIMapper] 🤖 Starting intelligent mapping...")
        
        try:
            # Build the system prompt
            system_prompt = self._build_system_prompt()
            
            # Build the user prompt with structured data
            user_prompt = self._build_user_prompt(pid_data, hmb_data, line_context)
            
            log_and_print(f"[MOVDatasheetAIMapper] Sending to OpenAI GPT-4...")
            log_and_print(f"[MOVDatasheetAIMapper] 📊 INPUT DATA:")
            log_and_print(f"[MOVDatasheetAIMapper]   - P&ID valves: {len(pid_data.get('valves', []))}")
            log_and_print(f"[MOVDatasheetAIMapper]   - HMB streams: {len(hmb_data.get('streams', []))}")
            
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
        """Build the system prompt for AI - MOV SPECIFIC EXTRACTION"""
        return """You are an expert engineering data extraction assistant for Motor Operated Valve (MOV) Datasheets.

🎯 MOV DATASHEET EXTRACTION RULES:

**STRUCTURE:**
- Section 1: General Data (FROM P&ID)
- Section 2: Operating Conditions (FROM HMB)
- Section 3: Valve Details (LEAVE BLANK)
- Section 4: Actuator Details (LEAVE BLANK)

**DATA SOURCE MAPPING:**

📋 SECTION 1 - GENERAL DATA (FROM P&ID):
Extract from P&ID document:
- tag_no: Valve tag number (e.g., "MOV-100-001")
- service: Valve service description
- **pid_no: EXACTLY from pid_data['drawing_info']['pid_no']**
- line_no: Line number associated with valve
- piping_class: Pipe specification
- fluid: From HMB stream matching line_no
- state: From HMB stream (Liquid/Gas/Two-Phase)
- phase: From HMB stream (Single/Multi-phase)

📋 SECTION 2 - OPERATING CONDITIONS (FROM HMB):
Extract from flattened HMB stream data:
- operating_pressure_min: stream['operating_pressure_min']
- operating_pressure_normal: stream['operating_pressure_normal']
- operating_pressure_max: stream['operating_pressure_max']
- pressure_unit: stream['pressure_unit']
- operating_temp_min: stream['operating_temp_min']
- operating_temp_normal: stream['operating_temp_normal']
- operating_temp_max: stream['operating_temp_max']
- operating_temp_unit: stream['operating_temp_unit']
- design_pressure_min: stream['design_pressure_min']
- design_pressure_max: stream['design_pressure_max']
- design_temp_min: stream['design_temp_min']
- design_temp_max: stream['design_temp_max']
- design_temp_unit: stream['design_temp_unit']
- sour_service: "Yes"/"No" from P&ID or HMB
- special_conditions: Any special requirements
- shut_off_pressure: Format as value + unit

🚫 SECTIONS 3 & 4 - LEAVE AS NULL:
- diff_pressure_delta_p: null
- seat_leakage_class: null
- nace_compliant: null
- fail_position: null
- valve_close_time: null
- valve_open_time: null

**MATCHING STRATEGY:**
1. For each MOV valve in P&ID:
   - Extract Section 1 fields from P&ID
   - Find line_no
   - Match line_no to HMB streams
   - Extract Section 2 from matched HMB stream
   - Leave Sections 3 & 4 as null

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
      "operating_pressure_min": "65",
      "operating_pressure_normal": "75",
      "operating_pressure_max": "85",
      "pressure_unit": "barg",
      "operating_temp_min": "-10",
      "operating_temp_normal": "25",
      "operating_temp_max": "65",
      "operating_temp_unit": "°C",
      "design_pressure_min": "0",
      "design_pressure_max": "90",
      "design_temp_min": "-20",
      "design_temp_max": "85",
      "design_temp_unit": "°C",
      "sour_service": "No",
      "special_conditions": "None",
      "shut_off_pressure": "95 barg",
      "diff_pressure_delta_p": null,
      "seat_leakage_class": null,
      "nace_compliant": null,
      "fail_position": null,
      "valve_close_time": null,
      "valve_open_time": null,
      "confidence": "high"
    }
  ],
  "overall_confidence": "high"
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
        line_context: Optional[Dict]
    ) -> str:
        """Build user prompt with structured data"""
        
        # Flatten HMB data
        flattened_hmb = self._flatten_hmb_streams(hmb_data)
        
        prompt = f"""**EXTRACTED DATA FROM DOCUMENTS:**

**1. P&ID DATA:**
```json
{json.dumps(pid_data, indent=2)}
```

**2. HMB DATA (FLATTENED):**
```json
{json.dumps(flattened_hmb, indent=2)}
```
"""
        
        if line_context:
            prompt += f"""
**3. PRE-MAPPED LINE CONTEXT:**
```json
{json.dumps(line_context, indent=2)}
```
"""
        
        prompt += """

**YOUR TASK:**
Map P&ID MOV valve data with HMB process conditions.
Populate ONLY Section 1 (General Data) and Section 2 (Operating Conditions).
Leave Sections 3 & 4 as null.
Return complete datasheet data in the specified JSON format.
"""
        
        return prompt
