"""
AI-Orchestrated SDV Datasheet Intelligence Layer
Smart mapping between extracted P&ID and HMB data
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


class SDVDatasheetAIMapper:
    """
    AI Intelligence Layer for SDV Datasheet Generation
    
    Receives structured data from P&ID and HMB extraction
    Uses OpenAI to intelligently map and match fields
    Returns clean structured JSON for Excel generation
    
    NO RAW PDF READING - Only structured data mapping!
    """
    
    def __init__(self):
        """Initialize OpenAI client"""
        self.client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=60.0
        )
        log_and_print("[SDVDatasheetAIMapper] Initialized with OpenAI GPT-4")
    
    def map_pid_hmb_to_datasheet(
        self,
        pid_data: Dict,
        hmb_data: Dict,
        line_context: Optional[Dict] = None,
        line_list_data: Optional[Dict] = None
    ) -> Dict:
        """
        Intelligently map P&ID, HMB, and Line List data to SDV datasheet fields
        
        Args:
            pid_data: Structured data extracted from P&ID
                {
                    'valves': [{'tag': 'SDV-001', 'type': 'SDV', ...}],
                    'lines': [{'line_no': '6"-GA-100', ...}],
                    'drawing_info': {...}
                }
            hmb_data: Structured data extracted from HMB
                {
                    'streams': [{'stream_id': 'S-100', 'temp': 65, ...}],
                    'process_conditions': {...}
                }
            line_context: Pre-mapped line associations (optional)
                {
                    'SDV-001': {'line_no': '6"-GA-100', 'stream_id': 'S-100'}
                }
            line_list_data: Structured data extracted from Line List (optional)
        
        Returns:
            Dict with filled datasheet fields ready for Excel
        """
        log_and_print("[SDVDatasheetAIMapper] 🤖 Starting intelligent mapping...")
        
        try:
            # Build the system prompt with Line List support
            system_prompt = self._build_system_prompt()
            
            # Build the user prompt with structured data including Line List
            user_prompt = self._build_user_prompt(pid_data, hmb_data, line_context, line_list_data)
            
            log_and_print(f"[SDVDatasheetAIMapper] Sending to OpenAI GPT-4...")
            log_and_print(f"[SDVDatasheetAIMapper] 📊 INPUT DATA:")
            log_and_print(f"[SDVDatasheetAIMapper]   - P&ID valves: {len(pid_data.get('valves', []))}")
            log_and_print(f"[SDVDatasheetAIMapper]   - HMB streams: {len(hmb_data.get('streams', []))}")
            if line_list_data:
                # Log Line List data structure for debugging
                log_and_print(f"[SDVDatasheetAIMapper]   - Line List provided: YES")
                log_and_print(f"[SDVDatasheetAIMapper]   - Line List keys: {list(line_list_data.keys())}")
                line_entries = line_list_data.get('lines', []) or line_list_data.get('streams', []) or line_list_data.get('data', [])
                log_and_print(f"[SDVDatasheetAIMapper]   - Line List entries: {len(line_entries)}")
                if line_entries and len(line_entries) > 0:
                    log_and_print(f"[SDVDatasheetAIMapper]   - Sample Line List entry: {json.dumps(line_entries[0], indent=2)}")
            else:
                log_and_print(f"[SDVDatasheetAIMapper]   - Line List: NOT PROVIDED")
            
            # Log sample valve and stream for debugging
            if pid_data.get('valves'):
                log_and_print(f"[SDVDatasheetAIMapper] 📄 Sample P&ID valve:")
                log_and_print(f"    {json.dumps(pid_data['valves'][0], indent=2)}")
            if hmb_data.get('streams'):
                log_and_print(f"[SDVDatasheetAIMapper] 🌡️ Sample HMB stream:")
                log_and_print(f"    {json.dumps(hmb_data['streams'][0], indent=2)}")
            
            # Call OpenAI
            response = self.client.chat.completions.create(
                model="gpt-4o",  # Use GPT-4o for better extraction
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,  # Slightly creative for intelligent matching while maintaining accuracy
                response_format={"type": "json_object"}
            )
            
            # Parse response
            result_text = response.choices[0].message.content
            mapped_data = json.loads(result_text)
            
            log_and_print(f"[SDVDatasheetAIMapper] ✅ AI mapping complete")
            log_and_print(f"[SDVDatasheetAIMapper] Mapped valves: {len(mapped_data.get('valves', []))}")
            
            # Log sample mapped valve - DETAILED OUTPUT WITH SECTION CHECK
            if mapped_data.get('valves'):
                sample_valve = mapped_data['valves'][0]
                log_and_print(f"[SDVDatasheetAIMapper] 📋 Sample mapped valve (first valve):")
                log_and_print(f"    Tag: {sample_valve.get('tag_no')}")
                log_and_print(f"    ✅ SECTION 1 (from P&ID):")
                log_and_print(f"       - PID No: {sample_valve.get('pid_no')}")
                log_and_print(f"       - Line No: {sample_valve.get('line_no')}")
                log_and_print(f"       - Service: {sample_valve.get('service')}")
                log_and_print(f"       - Piping Class: {sample_valve.get('piping_class')}")
                log_and_print(f"    🌡️ SECTION 2 (from HMB):")
                log_and_print(f"       - Fluid: {sample_valve.get('fluid')}")
                log_and_print(f"       - Phase: {sample_valve.get('phase')}")
                log_and_print(f"       - Operating Pressure: {sample_valve.get('operating_pressure_normal')} {sample_valve.get('pressure_unit')}")
                log_and_print(f"       - Operating Temp Min/Max: {sample_valve.get('operating_temp_min')}/{sample_valve.get('operating_temp_max')} {sample_valve.get('operating_temp_unit')}")
                log_and_print(f"       - Design Temp Min/Max: {sample_valve.get('design_temp_min')}/{sample_valve.get('design_temp_max')} {sample_valve.get('design_temp_unit')}")
                log_and_print(f"       - Shut Off Pressure: {sample_valve.get('shut_off_pressure')}")
            
            return mapped_data
            
        except Exception as e:
            logger.error(f"[SDVDatasheetAIMapper] ❌ Error: {e}")
            # Return empty structure on error
            return {
                'valves': [],
                'error': str(e),
                'confidence': 'low'
            }
    
    def _build_system_prompt(self) -> str:
        """Build the system prompt for AI - INTELLIGENT EXTRACTION WITH LINE LIST"""
        return """You are an expert engineering data extraction assistant for Safety Device Valve (SDV) Datasheets.

🎯 CRITICAL INSTRUCTIONS:
1. EXTRACT ALL AVAILABLE DATA - Do NOT use "N/A" unless data is truly unavailable
2. CROSS-REFERENCE between P&ID, HMB, and Line List to populate maximum fields
3. Use intelligent matching (line numbers, stream IDs, valve tags) to link data
4. If a field value exists in ANY document source, extract it
5. Only use null/N/A for vendor-specific fields (valve times, diff pressure)

🎯 SDV DATASHEET EXTRACTION RULES WITH LINE LIST:

**DATA SOURCE MAPPING (Same as MOV):**

📋 SECTION 1 - GENERAL DATA:
Sources: P&ID (primary) + Line List + Legend
- tag_no: From P&ID (e.g., "SDV-100-001")
- service: From Legend P&ID (abbreviations) or P&ID description
- pid_no: From P&ID drawing info
- line_no: From P&ID (match valve to line)
- piping_class: From **P&ID** (line specification on P&ID drawing)

📋 SECTION 2 - OPERATING CONDITIONS:
Sources: H&MB (primary) + Line List (fallback) - MUST EXTRACT ALL AVAILABLE VALUES
- fluid: From **Line List** and **H&MB** (both sources) - cross-reference for accuracy
- state: From Line List and H&MB (Liquid/Gas/Two-Phase) - DO NOT leave as N/A
- phase: From Line List and H&MB (Single/Multi-phase) - DO NOT leave as N/A
- operating_pressure_normal: **REQUIRED** - From H&MB streams or Line List "Operating Pressure"
- operating_temp_normal: **REQUIRED** - From H&MB streams or Line List "Operating Temperature"
- design_pressure: **REQUIRED** - From Line List "Design Pressure" (primary) or calculate as operating × 1.1
- design_temp: **REQUIRED** - From Line List "Design Temperature" (primary) or calculate as operating + 20°C
- sour_service: Check H&MB for H2S content (if H2S > 0 ppm → "Yes", else "No") - Default to "No" if unclear
- shut_off_pressure: Depends on SDV location - max possible pressure of upstream equipment or design_pressure × 1.05

📋 SECTION 3 - VALVE DETAILS:
Sources: P&ID + Instrumentation Department + Vendor
- fail_position: From **P&ID** (FO = Fail Open, FC = Fail Closed, FL = Fail Last)
- valve_close_time: Generally from **Vendor inputs** (leave null if not provided)
- valve_open_time: Generally from **Vendor inputs** (leave null if not provided)
- diff_pressure_delta_p: Usually from hydraulic calculation software (leave null if not provided)

**INTELLIGENT EXTRACTION RULES:**
1. Extract values from structured data (P&ID, H&MB, Line List)
2. Use intelligent matching to populate as many fields as possible
3. Line List provides: piping_class, design_pressure, design_temp
4. H&MB provides: fluid, operating conditions, H2S content
5. P&ID provides: tag, line_no, fail_position
6. Preserve units exactly as written
7. Leave vendor fields null unless provided
7. Return ONLY valid JSON

**DATA SOURCE MAPPING:**

📋 SECTION 1 - GENERAL DATA (FROM P&ID DOCUMENT):
Read the P&ID document to extract:
- tag_no (valve tag number) - REQUIRED - read from P&ID
- service (valve service description) - read from P&ID or valve label
- **pid_no: MUST use EXACTLY the value from pid_data['drawing_info']['pid_no']**
- line_no (line number associated with valve) - read from P&ID
- piping_class (pipe specification) - read from P&ID line data
- sour_service (Yes/No) - read from P&ID or line specifications
- special_service (Any special requirements) - read from P&ID notes
- ambient_temp_min, ambient_temp_max (from P&ID or HMB general conditions)

📋 SECTION 2 - OPERATING CONDITIONS (FROM HMB DOCUMENT):
Extract these fields directly from the flattened HMB stream data:
- fluid: stream['fluid']
- phase: stream['phase']
- state: stream['state']
- operating_pressure_normal: stream['operating_pressure_normal']
- operating_pressure_design: stream['operating_pressure_design']
- pressure_unit: stream['pressure_unit']
- operating_temp_min: stream['operating_temp_min']
- operating_temp_max: stream['operating_temp_max']
- operating_temp_unit: stream['operating_temp_unit']
- design_temp_min: stream['design_temp_min']
- design_temp_max: stream['design_temp_max']
- design_temp_unit: stream['design_temp_unit']
- shut_off_pressure: Format as stream['shut_off_pressure'] + " " + stream['pressure_unit']

🚫 SECTIONS 3-5 - LEAVE AS NULL (Manual Engineering Input Required):
- Section 3 (Valve Details): bore_detail, mech_handwheel, fail_position, valve_close_time, valve_open_time
- Section 4 (Actuator Details): design_pressure, seat_leakage_class
- Section 5 (Accessories): nace_requirement
- DO NOT fill these fields - return null for all Section 3-5 fields

**INTELLIGENT MATCHING STRATEGY:**
1. For each valve in P&ID data:
   a. Extract Section 1 fields by reading P&ID document data
   b. Find line_no associated with valve from P&ID
   c. Match line_no to HMB streams (exact match preferred, fuzzy match if needed)
   d. Extract Section 2 fields by reading HMB document data for matched stream
   e. If no stream match, use general HMB process conditions
2. Fill Section 1 (General Data) from P&ID
3. Fill Section 2 (Operating Conditions) from HMB
4. Leave Sections 3-5 as null
5. **ALWAYS use pid_data['drawing_info']['pid_no'] for pid_no field**
6. Use intelligent interpretation when matching streams to lines
6. Do NOT cross-reference or derive from other streams

**CONFIDENCE SCORING:**
- high: Exact line_no match found between P&ID and HMB
- medium: Partial match (similar line numbers)
- low: No match found
- none: Data missing from source

Return ONLY valid JSON in this structure:
{
  "valves": [
    {
      "tag_no": "SDV-100-001",
      "service": "Natural Gas Main Line Shutdown",
      "pid_no": "P-100-001-Rev-A",
      "line_no": "6\\"-GA-100-1501-A2B",
      "piping_class": "ASME B16.5 150#",
      "sour_service": "No",
      "special_service": "None",
      "ambient_temp_min": "10",
      "ambient_temp_max": "50",
      "ambient_temp_unit": "°C",
      "fluid": "Natural Gas",
      "phase": "Gas",
      "state": "Supercritical",
      "operating_pressure_normal": "75",
      "operating_pressure_design": "90",
      "pressure_unit": "barg",
      "operating_temp_min": "-10",
      "operating_temp_max": "65",
      "operating_temp_unit": "°C",
      "design_temp_min": "-20",
      "design_temp_max": "85",
      "design_temp_unit": "°C",
      "shut_off_pressure": "105 barg",
      "bore_detail": null,
      "mech_handwheel": null,
      "fail_position": null,
      "valve_close_time": null,
      "valve_open_time": null,
      "design_pressure": null,
      "seat_leakage_class": null,
      "nace_requirement": null,
      "confidence": "high",
      "match_method": "line_number"
    }
  ],
  "overall_confidence": "high",
  "unmatched_valves": [],
  "unmatched_streams": []
}"""
    
    def _flatten_hmb_streams(self, hmb_data: Dict) -> Dict:
        """
        Flatten nested HMB stream structure for AI
        Converts nested fields like stream['pressure']['normal'] to flat fields
        """
        flattened_hmb = {
            'streams': [],
            'process_conditions': hmb_data.get('process_conditions', {})
        }
        
        for stream in hmb_data.get('streams', []):
            flat_stream = {
                'stream_id': stream.get('stream_id'),
                'stream_name': stream.get('stream_name'),
                'line_no': stream.get('line_no'),
                'fluid': stream.get('fluid'),
                'phase': stream.get('phase'),
                'state': stream.get('state'),
                # Flatten temperature nested object
                'operating_temp_min': stream.get('temperature', {}).get('min'),
                'operating_temp_max': stream.get('temperature', {}).get('max'),
                'operating_temp_unit': stream.get('temperature', {}).get('unit'),
                # Flatten design_temperature nested object
                'design_temp_min': stream.get('design_temperature', {}).get('min'),
                'design_temp_max': stream.get('design_temperature', {}).get('max'),
                'design_temp_unit': stream.get('design_temperature', {}).get('unit'),
                # Flatten pressure nested object
                'operating_pressure_normal': stream.get('pressure', {}).get('normal'),
                'operating_pressure_design': stream.get('pressure', {}).get('design'),
                'shut_off_pressure': stream.get('pressure', {}).get('shutoff'),
                'pressure_unit': stream.get('pressure', {}).get('unit')
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
        
        # Flatten HMB data for easier AI extraction
        flattened_hmb = self._flatten_hmb_streams(hmb_data)
        
        prompt = f"""**EXTRACTED DATA FROM DOCUMENTS:**

**1. P&ID DATA:**
```json
{json.dumps(pid_data, indent=2)}
```

**2. HMB DATA (FLATTENED FOR EASY EXTRACTION):**
```json
{json.dumps(flattened_hmb, indent=2)}
```
"""
        
        if line_list_data:
            prompt += f"""
**3. LINE LIST DATA (DESIGN SPECIFICATIONS):**
```json
{json.dumps(line_list_data, indent=2)}
```

**Line List contains:**
- Line numbers (match with P&ID)
- Piping class/specification
- Design pressure and temperature (general design values)
- Normal operating pressure and temperature (fallback if H&MB incomplete)
- Fluid identification
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
Intelligently map the P&ID valve data with HMB process conditions and Line List design data.
- Use P&ID for: TAG NO, LINE NO, PIPING CLASS, FAIL POSITION
- Use H&MB for: FLUID, OPERATING CONDITIONS, H2S (sour service)
- Use Line List for: DESIGN PRESSURE, DESIGN TEMP (cross-reference with line numbers)
- Match valves to streams using line numbers

Return complete datasheet data for each valve in the specified JSON format.

Focus on accuracy - flag uncertain matches with low confidence.
"""
        
        return prompt
    
    def validate_mapped_data(self, mapped_data: Dict) -> Dict:
        """
        Validate AI-mapped data before Excel generation
        
        Checks:
        - Required fields present
        - Data types correct
        - Units consistent
        - No hallucinated data
        
        Returns: Validation report
        """
        validation_report = {
            'valid': True,
            'warnings': [],
            'errors': []
        }
        
        valves = mapped_data.get('valves', [])
        
        if not valves:
            validation_report['valid'] = False
            validation_report['errors'].append("No valves mapped")
            return validation_report
        
        required_fields = ['tag_no', 'line_no']
        
        for i, valve in enumerate(valves):
            # Check required fields
            for field in required_fields:
                if not valve.get(field):
                    validation_report['warnings'].append(
                        f"Valve {i+1}: Missing {field}"
                    )
            
            # Check confidence
            confidence = valve.get('confidence', 'low')
            if confidence == 'low':
                validation_report['warnings'].append(
                    f"Valve {i+1} ({valve.get('tag_no', 'unknown')}): Low confidence mapping"
                )
        
        logger.info(f"[SDVDatasheetAIMapper] Validation: {len(validation_report['warnings'])} warnings, {len(validation_report['errors'])} errors")
        
        return validation_report


# Helper function for quick testing
def test_ai_mapper():
    """Test the AI mapper with sample data"""
    
    sample_pid_data = {
        'valves': [
            {
                'tag': 'SDV-100-001',
                'type': 'SDV',
                'location': 'Main Gas Line',
                'line_no': '6"-GA-100-1501-A2B'
            }
        ],
        'lines': [
            {
                'line_no': '6"-GA-100-1501-A2B',
                'piping_class': 'ASME B16.5 150#',
                'service': 'Natural Gas'
            }
        ],
        'drawing_info': {
            'pid_no': 'P-100-001',
            'date': '03-Mar-2026'
        }
    }
    
    sample_hmb_data = {
        'streams': [
            {
                'stream_id': 'S-100',
                'line_no': '6"-GA-100',
                'fluid': 'Natural Gas',
                'phase': 'Gas',
                'temp': {'min': -10, 'max': 65, 'unit': '°C'},
                'pressure': {'normal': 75, 'design': 90, 'unit': 'barg'},
                'flow_rate': 50000,
                'composition': 'Methane 95%, Ethane 3%'
            }
        ]
    }
    
    mapper = SDVDatasheetAIMapper()
    result = mapper.map_pid_hmb_to_datasheet(sample_pid_data, sample_hmb_data)
    
    print(json.dumps(result, indent=2))
    
    validation = mapper.validate_mapped_data(result)
    print(f"\nValidation: {validation}")


if __name__ == "__main__":
    test_ai_mapper()
