"""
PFD to P&ID AI Conversion Service
Advanced AI-powered conversion using GPT-4o Vision and engineering intelligence
"""
import openai
from decouple import config
from django.utils import timezone
import json
import base64
from io import BytesIO
from PIL import Image
import logging

logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai.api_key = config('OPENAI_API_KEY', default='')


class PFDToPIDConverter:
    """
    AI service for converting Process Flow Diagrams to P&ID drawings
    Uses GPT-4o Vision for intelligent extraction and conversion
    """
    
    def __init__(self):
        self.model = config('OPENAI_MODEL', default='gpt-4o')
        
    def extract_pfd_data(self, image_file):
        """
        Extract process flow information from PFD using AI vision
        
        Args:
            image_file: File object containing PFD image
            
        Returns:
            dict: Extracted process flow data
        """
        try:
            # Convert image to base64
            image_data = self._prepare_image(image_file)
            
            # AI prompt for PFD extraction
            prompt = self._get_extraction_prompt()
            
            # Call OpenAI API
            response = openai.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert process engineer specializing in oil & gas process design. You analyze Process Flow Diagrams (PFDs) and extract detailed process information for P&ID generation."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_data}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.2
            )
            
            # Parse response
            content = response.choices[0].message.content
            extracted_data = self._parse_extraction_response(content)
            
            logger.info(f"Successfully extracted PFD data: {len(extracted_data.get('equipment', []))} equipment items")
            return extracted_data
            
        except Exception as e:
            logger.error(f"PFD extraction failed: {str(e)}")
            raise
    
    def generate_pid_specifications(self, pfd_data):
        """
        Generate detailed P&ID specifications from extracted PFD data
        
        Args:
            pfd_data: Dictionary of extracted PFD information
            
        Returns:
            dict: Complete P&ID specifications
        """
        try:
            prompt = self._get_pid_generation_prompt(pfd_data)
            
            response = openai.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert P&ID designer with 20+ years experience in oil & gas engineering. You create detailed, compliant P&ID specifications following ADNOC DEP, API, and ISA standards."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=4000,
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            pid_specs = json.loads(response.choices[0].message.content)
            
            logger.info("Successfully generated P&ID specifications")
            return pid_specs
            
        except Exception as e:
            logger.error(f"P&ID generation failed: {str(e)}")
            raise
    
    def validate_conversion(self, pid_specs, pfd_data):
        """
        Validate generated P&ID against PFD and industry standards
        
        Args:
            pid_specs: Generated P&ID specifications
            pfd_data: Original PFD data
            
        Returns:
            dict: Validation results with compliance checks
        """
        try:
            validation_prompt = f"""
Validate the following P&ID specifications against the original PFD data and industry standards:

PFD Data:
{json.dumps(pfd_data, indent=2)}

P&ID Specifications:
{json.dumps(pid_specs, indent=2)}

Perform comprehensive validation:
1. Equipment mapping accuracy
2. Material balance verification
3. Safety system completeness (PSVs, emergency shutdowns)
4. Instrumentation adequacy
5. ADNOC DEP 30.20.10.13 compliance
6. API 14C compliance
7. ISA 5.1 symbol compliance
8. Piping class specifications
9. Isolation valve requirements
10. Process parameters consistency

Return JSON with:
- compliance_score (0-100)
- passed_checks: list
- failed_checks: list with severity
- recommendations: list
- missing_elements: list
"""
            
            response = openai.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a senior process safety engineer performing P&ID validation and compliance review."
                    },
                    {
                        "role": "user",
                        "content": validation_prompt
                    }
                ],
                max_tokens=3000,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            validation_results = json.loads(response.choices[0].message.content)
            
            logger.info(f"Validation completed - Score: {validation_results.get('compliance_score', 0)}")
            return validation_results
            
        except Exception as e:
            logger.error(f"Validation failed: {str(e)}")
            raise
    
    def _prepare_image(self, image_file):
        """Convert image to base64 for API"""
        try:
            # Read image
            image = Image.open(image_file)
            
            # Resize if too large (max 2048px)
            max_size = 2048
            if max(image.size) > max_size:
                ratio = max_size / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)
            
            # Convert to JPEG if not already
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Save to buffer
            buffer = BytesIO()
            image.save(buffer, format='JPEG', quality=85)
            buffer.seek(0)
            
            # Encode to base64
            return base64.b64encode(buffer.read()).decode('utf-8')
            
        except Exception as e:
            logger.error(f"Image preparation failed: {str(e)}")
            raise
    
    def _get_extraction_prompt(self):
        """Comprehensive prompt for PFD data extraction"""
        return """
Analyze this Process Flow Diagram (PFD) and extract ALL process information in JSON format:

{
  "document_info": {
    "title": "Drawing title",
    "drawing_number": "Drawing number",
    "revision": "Revision",
    "project": "Project name",
    "process_description": "Brief process description"
  },
  "equipment": [
    {
      "tag": "Equipment tag (e.g., V-101)",
      "type": "Equipment type (Vessel, Pump, Exchanger, etc.)",
      "service": "Equipment service description",
      "design_pressure": "Design pressure with units",
      "design_temperature": "Design temperature with units",
      "material": "Material of construction",
      "capacity": "Capacity/size with units",
      "connections": {
        "inlet": ["connected equipment/line"],
        "outlet": ["connected equipment/line"]
      }
    }
  ],
  "process_streams": [
    {
      "stream_number": "Stream number",
      "description": "Stream description",
      "from_equipment": "Source equipment",
      "to_equipment": "Destination equipment",
      "phase": "Gas/Liquid/Two-phase",
      "flow_rate": "Flow rate with units",
      "pressure": "Operating pressure with units",
      "temperature": "Operating temperature with units",
      "composition": "Key components"
    }
  ],
  "utilities": {
    "cooling_water": [],
    "steam": [],
    "nitrogen": [],
    "fuel_gas": [],
    "instrument_air": [],
    "plant_air": []
  },
  "control_loops": [
    {
      "loop_tag": "Control loop identifier",
      "type": "Flow/Pressure/Temperature/Level control",
      "controlled_variable": "What is being controlled",
      "manipulated_variable": "What is being manipulated",
      "setpoint": "Normal setpoint value"
    }
  ],
  "safety_systems": [
    {
      "type": "PSV/Relief Valve/Rupture Disc/ESD",
      "location": "Equipment/line",
      "set_pressure": "Relief set pressure",
      "capacity": "Relief capacity",
      "discharge_to": "Discharge location"
    }
  ],
  "design_basis": {
    "process_capacity": "Normal operating capacity",
    "design_capacity": "Maximum design capacity",
    "operating_philosophy": "Continuous/Batch/etc.",
    "ambient_conditions": "Design ambient temperature"
  }
}

Be thorough and extract ALL visible information. Include equipment tags, process conditions, stream data, and safety systems.
"""
    
    def _get_pid_generation_prompt(self, pfd_data):
        """Detailed prompt for P&ID generation"""
        return f"""
Based on this PFD data, generate complete P&ID specifications following oil & gas industry standards:

PFD Data:
{json.dumps(pfd_data, indent=2)}

Generate detailed P&ID specifications in JSON format:

{{
  "equipment_list": [
    {{
      "tag": "Equipment tag",
      "type": "Type",
      "service": "Service",
      "specifications": {{
        "design_pressure": "value with units",
        "design_temperature": "value with units",
        "material": "MOC",
        "nozzles": [
          {{"size": "NPS", "rating": "class", "service": "inlet/outlet/etc"}},
        ]
      }},
      "instrumentation": [
        {{"tag": "LI-101", "type": "Level Indicator", "range": "0-100%"}},
        {{"tag": "LSH-101", "type": "Level Switch High", "setpoint": "80%"}},
      ],
      "iso_valves": ["HV-101", "HV-102"]
    }}
  ],
  "piping_specifications": [
    {{
      "line_number": "1\"-P1001-1501-CA6",
      "from": "V-101",
      "to": "P-101",
      "size": "1 inch NPS",
      "piping_class": "1501",
      "service": "Crude oil",
      "design_pressure": "150 psig",
      "design_temperature": "250°F",
      "material": "CS A106 Gr B",
      "insulation": "required/not required",
      "heat_tracing": "required/not required",
      "valves": [
        {{"tag": "HV-101", "type": "Gate Valve", "size": "1 inch"}}
      ]
    }}
  ],
  "instrumentation_list": [
    {{
      "tag": "FT-101",
      "type": "Flow Transmitter",
      "service": "Crude oil flow",
      "process_connection": "1\"-P1001-1501-CA6",
      "range": "0-100 m3/h",
      "signal": "4-20mA",
      "power": "24VDC",
      "hazardous_area": "Zone 1"
    }}
  ],
  "control_valves": [
    {{
      "tag": "FCV-101",
      "type": "Control Valve",
      "service": "Flow control",
      "line": "1\"-P1001-1501-CA6",
      "size": "1 inch",
      "Cv": "calculated value",
      "actuator": "Pneumatic/Electric",
      "fail_action": "FC/FO",
      "positioner": "required"
    }}
  ],
  "safety_devices": [
    {{
      "tag": "PSV-101",
      "type": "Pressure Safety Valve",
      "protected_equipment": "V-101",
      "set_pressure": "150 psig",
      "capacity": "calculated value",
      "orifice": "J",
      "inlet_size": "1 inch",
      "outlet_size": "2 inch",
      "discharge_to": "Flare header",
      "isolation_valves": ["CSC-PSV-101A", "CSC-PSV-101B"]
    }}
  ],
  "utility_connections": [
    {{
      "utility": "Cooling Water",
      "connection_point": "E-101",
      "supply_line": "6\"-CWS-1501-U1",
      "return_line": "6\"-CWR-1501-U1",
      "flow_rate": "50 m3/h",
      "control": "TCV-101"
    }}
  ],
  "electrical_loads": [
    {{
      "equipment": "P-101A",
      "motor_power": "15 kW",
      "voltage": "415V",
      "frequency": "50Hz",
      "area_classification": "Zone 2"
    }}
  ],
  "standards_applied": [
    "ADNOC DEP 30.20.10.13 - P&ID Preparation",
    "ADNOC DEP 30.20.10.15 - Instrument Identification",
    "API 14C - Safety Systems",
    "ISA 5.1 - Instrumentation Symbols",
    "ASME B31.3 - Process Piping"
  ]
}}

Ensure:
1. ALL equipment has proper isolation valves
2. ALL pressure vessels have PSVs per API 521
3. ALL control loops are complete with sensors, transmitters, controllers, final elements
4. Line numbers follow format: SIZE-SERVICE-CLASS-SEQUENCE
5. Instrument tags follow ISA 5.1 format
6. Material selection appropriate for service
7. Hazardous area classification considered
8. Emergency shutdown capability included
"""
    
    def _parse_extraction_response(self, content):
        """Parse AI response into structured data"""
        try:
            # Try to parse as JSON first
            if content.strip().startswith('{'):
                return json.loads(content)
            
            # If not JSON, try to extract JSON from markdown
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            
            # Fallback: return raw content
            return {"raw_content": content}
            
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON response, returning raw content")
            return {"raw_content": content}
