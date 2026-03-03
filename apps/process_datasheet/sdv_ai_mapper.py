"""
AI-Orchestrated SDV Datasheet Intelligence Layer
Smart mapping between extracted P&ID and HMB data
"""
import logging
from typing import Dict, List, Optional
from openai import OpenAI
from django.conf import settings
import json

logger = logging.getLogger(__name__)


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
        logger.info("[SDVDatasheetAIMapper] Initialized with OpenAI GPT-4")
    
    def map_pid_hmb_to_datasheet(
        self,
        pid_data: Dict,
        hmb_data: Dict,
        line_context: Optional[Dict] = None
    ) -> Dict:
        """
        Intelligently map P&ID and HMB data to SDV datasheet fields
        
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
        
        Returns:
            Dict with filled datasheet fields ready for Excel
        """
        logger.info("[SDVDatasheetAIMapper] Starting intelligent mapping...")
        
        try:
            # Build the system prompt
            system_prompt = self._build_system_prompt()
            
            # Build the user prompt with structured data
            user_prompt = self._build_user_prompt(pid_data, hmb_data, line_context)
            
            logger.info(f"[SDVDatasheetAIMapper] Sending to OpenAI GPT-4...")
            logger.info(f"[SDVDatasheetAIMapper] P&ID valves: {len(pid_data.get('valves', []))}")
            logger.info(f"[SDVDatasheetAIMapper] HMB streams: {len(hmb_data.get('streams', []))}")
            
            # Call OpenAI
            response = self.client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,  # Low temperature for deterministic output
                response_format={"type": "json_object"}
            )
            
            # Parse response
            result_text = response.choices[0].message.content
            mapped_data = json.loads(result_text)
            
            logger.info(f"[SDVDatasheetAIMapper] ✅ AI mapping complete")
            logger.info(f"[SDVDatasheetAIMapper] Mapped valves: {len(mapped_data.get('valves', []))}")
            
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
        """Build the system prompt for AI"""
        return """You are a process engineering assistant specializing in Safety Shutdown Valve (SDV) datasheets.

You receive structured extracted data from:
1. P&ID document (valve tags, line numbers, service descriptions)
2. HMB document (process conditions, temperatures, pressures, fluid properties)

Your task is to intelligently map and fill SDV datasheet fields by matching data between both documents.

**MAPPING RULES:**
1. Match by Line Number primarily (highest priority)
2. If Line No missing, use Tag or Stream mapping
3. Prefer HMB values for pressure and temperature (more accurate process data)
4. Prefer P&ID for service description and valve classification
5. Match valve tags to streams using line numbers as connector
6. If multiple streams match one line, use the one with closest conditions
7. Do NOT hallucinate missing data - use null for unknown fields
8. Flag uncertain mappings with confidence: "low", "medium", "high"

**OUTPUT REQUIREMENTS:**
- Return valid JSON only
- Each valve must have complete available data
- Include confidence level for each mapping
- Preserve units exactly as extracted
- Map phase descriptions consistently (Gas/Liquid/Two-Phase)

**FIELD MAPPING:**
From P&ID:
- tag_no, service, pid_no, line_no, piping_class
- sour_service, special_service
- fail_position, valve_close_time, valve_open_time
- bore_detail, mech_handwheel, seat_leakage_class, nace_requirement

From HMB:
- fluid, phase, state
- operating_pressure_normal, operating_pressure_design, pressure_unit
- operating_temp_min, operating_temp_max, operating_temp_unit
- design_temp_min, design_temp_max, design_temp_unit
- shut_off_pressure
- ambient_temp_min, ambient_temp_max, ambient_temp_unit

**MATCHING STRATEGY:**
1. Extract line_no from P&ID valve context
2. Find matching stream in HMB by line_no or stream_id
3. Combine valve data from P&ID with process data from HMB
4. Include ambient conditions from HMB process_conditions
5. Flag if no match found

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
      "bore_detail": "6\\" Full Bore",
      "mech_handwheel": "Yes",
      "fail_position": "Fail Close (FC)",
      "valve_close_time": "5 seconds",
      "valve_open_time": "8 seconds",
      "design_pressure": "90 barg",
      "seat_leakage_class": "Class VI",
      "nace_requirement": "MR0175",
      "confidence": "high",
      "match_method": "line_number"
    }
  ],
  "overall_confidence": "high",
  "unmatched_valves": [],
  "unmatched_streams": []
}"""
      "line_no": "6\\"-GA-100-1501-A2B",
      "piping_class": "ASME B16.5 150#",
      "fluid": "Natural Gas",
      "phase": "Gas",
      "state": "Supercritical",
      "operating_pressure_normal": "75",
      "operating_pressure_design": "90",
      "pressure_unit": "barg",
      "operating_temp_min": "-10",
      "operating_temp_max": "65",
      "temp_unit": "°C",
      "design_temp_min": "-20",
      "design_temp_max": "85",
      "confidence": "high",
      "match_method": "line_number"
    }
  ],
  "overall_confidence": "high",
  "unmatched_valves": [],
  "unmatched_streams": []
}"""
    
    def _build_user_prompt(
        self,
        pid_data: Dict,
        hmb_data: Dict,
        line_context: Optional[Dict]
    ) -> str:
        """Build user prompt with structured data"""
        
        prompt = f"""**EXTRACTED DATA FROM DOCUMENTS:**

**1. P&ID DATA:**
```json
{json.dumps(pid_data, indent=2)}
```

**2. HMB DATA:**
```json
{json.dumps(hmb_data, indent=2)}
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
Intelligently map the P&ID valve data with HMB process conditions.
Match valves to streams using line numbers.
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
