"""
AI-Powered MOV Equipment P&ID Extraction Service

Intelligent extraction of Motor Operated Valve (MOV) data from P&ID diagrams
using OpenAI Vision API with soft-coded field configuration.

SMART EXTRACTION: Automatically extracts all MOV fields defined in mov_datasheet_config.py
"""

import logging
import json
import base64
import io
from openai import OpenAI
from PIL import Image
from pdf2image import convert_from_bytes
from django.conf import settings
import os

from apps.process_datasheet.config.mov_datasheet_config import (
    MOV_DATASHEET_FIELDS,
    get_all_fields,
    EXTRACTION_PATTERNS
)

logger = logging.getLogger(__name__)


class MOVEquipmentExtractor:
    """
    AI-powered extractor for MOV equipment from P&ID diagrams
    Uses soft-coded field configuration for intelligent extraction
    """

    def __init__(self):
        """Initialize OpenAI client and configuration"""
        api_key = os.getenv('OPENAI_API_KEY') or getattr(settings, 'OPENAI_API_KEY', None)
        if api_key:
            self.openai_client = OpenAI(api_key=api_key, timeout=120.0)
            logger.info("[MOVExtractor] ✅ OpenAI client initialized successfully")
        else:
            self.openai_client = None
            logger.error("[MOVExtractor] ❌ OpenAI API key not found - extraction will fail!")
        
        # Get all configured MOV fields
        self.all_fields = get_all_fields()
        
        # Build extraction instructions from configuration
        self.extraction_instructions = self._build_extraction_instructions()
    
    def _build_extraction_instructions(self):
        """
        Build AI extraction instructions from soft-coded field configuration
        
        Returns:
            str: Formatted extraction instructions
        """
        instructions = []
        
        for section_key, section_data in MOV_DATASHEET_FIELDS.items():
            section_name = section_data['section_name']
            instructions.append(f"\n**{section_name}:**")
            
            for field_key, field_config in section_data['fields'].items():
                label = field_config['label']
                keywords = ', '.join(field_config.get('extraction_keywords', []))
                example = field_config.get('example', '')
                required = '(REQUIRED)' if field_config.get('required', False) else '(Optional)'
                
                instruction = f"- {label} {required}: Look for {keywords}"
                if example:
                    instruction += f" (e.g., {example})"
                instructions.append(instruction)
        
        return '\n'.join(instructions)
    
    def analyze_pid_for_movs(self, pid_file_path, drawing_info=None):
        """
        Analyze P&ID diagram to extract MOV equipment using AI
        
        Args:
            pid_file_path: Path to P&ID file (PDF, PNG, JPG)
            drawing_info: Optional drawing metadata
            
        Returns:
            list: Extracted MOV equipment data
        """
        try:
            logger.info(f"[MOVExtractor] 🔍 Starting MOV extraction from: {pid_file_path}")
            
            # Convert file to base64 image
            base64_image = self._convert_to_base64(pid_file_path)
            if not base64_image:
                logger.error("[MOVExtractor] ❌ Failed to convert file to image")
                return []
            
            # Extract MOVs using OpenAI Vision
            movs = self._extract_movs_with_ai(base64_image, drawing_info or {})
            
            logger.info(f"[MOVExtractor] ✅ Extracted {len(movs)} MOV equipment")
            return movs
            
        except Exception as e:
            logger.error(f"[MOVExtractor] ❌ Extraction error: {str(e)}")
            import traceback
            logger.error(f"[MOVExtractor] Traceback: {traceback.format_exc()}")
            return []
    
    def _convert_to_base64(self, file_path):
        """
        Convert PDF/Image file to base64 for OpenAI Vision API
        
        Args:
            file_path: Path to file
            
        Returns:
            str: Base64 encoded image
        """
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            # Check if PDF
            if file_path.lower().endswith('.pdf'):
                logger.info("[MOVExtractor] Converting PDF to image...")
                images = convert_from_bytes(file_data, dpi=200, first_page=1, last_page=1)
                if images:
                    img = images[0]
                    buffered = io.BytesIO()
                    img.save(buffered, format="JPEG", quality=95)
                    image_data = buffered.getvalue()
                    return base64.b64encode(image_data).decode('utf-8')
            else:
                # Image file
                logger.info("[MOVExtractor] Processing image file...")
                return base64.b64encode(file_data).decode('utf-8')
                
        except Exception as e:
            logger.error(f"[MOVExtractor] ❌ File conversion error: {str(e)}")
            return None
    
    def _extract_movs_with_ai(self, base64_image, drawing_info):
        """
        Use OpenAI Vision API to extract MOV equipment data
        
        Args:
            base64_image: Base64 encoded P&ID image
            drawing_info: Drawing metadata
            
        Returns:
            list: Extracted MOV data
        """
        if not self.openai_client:
            logger.error("[MOVExtractor] ❌ OpenAI client not available")
            return []
        
        try:
            prompt = self._create_extraction_prompt(drawing_info)
            
            logger.info("[MOVExtractor] 🤖 Calling OpenAI Vision API...")
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": """You are an advanced AI with VISION CAPABILITIES analyzing P&ID engineering drawings.

🎯 YOUR TASK: Extract ALL Motor Operated Valves (MOVs) from the P&ID image provided.

✅ YOU CAN AND MUST:
- Look at the image I will provide
- Read text, symbols, and tags from the drawing  
- Identify valve symbols with motor actuators
- Extract data from the visual P&ID diagram
- Return a JSON array of all MOVs found

🔍 WHAT TO LOOK FOR:
Motor Operated Valves appear as:
- Valve symbols with tags like: MOV-101, MV-202, EV-303, XV-404, MOT-V-505, MTV-606
- Valve symbols with "M" indicators showing motor actuation
- Valves with electric motor symbols drawn above/beside them
- Valves with electrical connection lines

📋 DETECTION PROCESS:
1. SCAN the entire P&ID image systematically
2. IDENTIFY every valve symbol you see
3. CHECK which valves have motor/electric actuators
4. EXTRACT tag numbers and visible specifications
5. RETURN a JSON array with all MOVs

⚠️ IMPORTANT: 
- DO NOT say you cannot see images - you HAVE vision capabilities
- DO NOT ask for text descriptions - analyze the IMAGE I provide
- A typical P&ID has 3-15+ motor operated valves
- If you find 0 MOVs, double-check before returning empty []
- Use "N/A" for any field not visible in the drawing

OUTPUT: Return ONLY a JSON array, nothing else."""
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=10000,
                temperature=0.1
            )
            
            logger.info(f"[MOVExtractor] ✅ OpenAI API call completed")
            logger.info(f"[MOVExtractor] Response usage: {response.usage}")
            
            # Parse AI response
            ai_response = response.choices[0].message.content
            logger.info(f"[MOVExtractor] 📄 AI Response: {len(ai_response)} characters")
            logger.info(f"[MOVExtractor] 📄 AI Response Content (first 500 chars): {ai_response[:500]}")
            logger.info(f"[MOVExtractor] 📄 AI Response Content (last 200 chars): ...{ai_response[-200:]}")
            
            # Extract structured MOV data
            movs = self._parse_ai_response(ai_response)
            logger.info(f"[MOVExtractor] 📊 Parsed {len(movs)} MOV equipment")
            
            if len(movs) == 0:
                logger.warning(f"[MOVExtractor] ⚠️ ZERO MOVs extracted! Full AI response: {ai_response}")
            
            return movs
            
        except Exception as e:
            logger.error(f"[MOVExtractor] ❌ OpenAI API error: {str(e)}")
            import traceback
            logger.error(f"[MOVExtractor] Traceback: {traceback.format_exc()}")
            return []
    
    def _create_extraction_prompt(self, drawing_info):
        """
        Create comprehensive AI prompt for MOV extraction
        
        Args:
            drawing_info: Drawing metadata
            
        Returns:
            str: Formatted prompt
        """
        prompt = f"""🎯 MISSION: Analyze this P&ID diagram and extract ALL Motor Operated Valves (MOVs) with COMPLETE specifications.

📋 Drawing Information:
- Drawing Number: {drawing_info.get('drawing_number', 'N/A')}
- Drawing Title: {drawing_info.get('drawing_title', 'N/A')}
- Area: {drawing_info.get('area', 'N/A')}
- Project: {drawing_info.get('project_name', 'N/A')}

⚠️ **CRITICAL**: A typical P&ID contains 3-15+ motor operated valves. If you find 0, YOU MISSED THEM!

🔍 **STEP-BY-STEP DETECTION PROCESS:**

1. **SYSTEMATIC SCAN**: Scan the ENTIRE drawing left-to-right, top-to-bottom
2. **IDENTIFY ALL VALVES**: Look at EVERY valve symbol (gate, globe, ball, butterfly, etc.)
3. **CHECK FOR MOTORS**: Identify which valves have motor actuators or "M" indicators
4. **READ ALL TAGS**: Check every valve tag for motor-operated valve codes
5. **EXAMINE LEGENDS**: Extract all MOV symbols from legend/symbol tables
6. **FOLLOW LINES**: Trace every process line and check valves on it

📋 **MOTOR OPERATED VALVE IDENTIFICATION PATTERNS:**

**PRIMARY PATTERNS (Most Common):**
- Tags starting with **MOV-** (MOV-101, MOV-202, MOV-2003, etc.)
- Tags starting with **MV-** (MV-101, MV-202)
- Tags starting with **MOT-V-** or **MTV-** (MOT-V-101, MTV-202)
- Tags starting with **EV-** (Electric Valve: EV-101, EV-202)
- Tags starting with **XV-** with motor symbol (XV-101, XV-202)

**SECONDARY PATTERNS:**
- Any valve symbol with "M" indicator or label
- Valve symbols with motor actuator icon (typically electric motor shape above valve)
- Valves with electrical power line connections
- Valves labeled as "Motor Operated" or "Electric Actuated"
- Control valves with "MOV" annotation

**VALVE TYPES TO CHECK:**
- Gate valves, Globe valves, Ball valves, Butterfly valves, Plug valves
- Isolation valves, Control valves, On-Off valves
- Any valve with an electric/motor actuator symbol

🎯 **DATA EXTRACTION REQUIREMENTS:**

For EACH motor operated valve found, extract:

```json
[
  {{
    "tag_number": "MOV-101",
    "service": "Main Feed Isolation",
    "pid_no": "P-16093-001",
    "line_number": "2\\"-HC-1001-A1",
    "piping_class": "300# RF",
    "fluid": "Natural Gas",
    "state": "Gas",
    "phase": "Single Phase",
    "operating_pressure_min": "0",
    "operating_pressure_normal": "10",
    "operating_pressure_max": "12",
    "operating_temp_min": "15",
    "operating_temp_normal": "50",
    "operating_temp_max": "80",
    "design_pressure_min": "0",
    "design_pressure_max": "20",
    "design_temp_min": "0",
    "design_temp_max": "100",
    "source_service": "Natural Gas",
    "shutoff_pressure": "15",
    "differential_pressure": "2",
    "seat_leakage_class": "Class VI",
    "nace_compliant": "Yes",
    "valve_type": "Ball Valve",
    "valve_size": "2 inch",
    "valve_rating": "300#",
    "valve_ends": "RF (Raised Face)",
    "body_material": "A105",
    "trim_material": "316 SS",
    "disc_material": "CF8M",
    "seat_material": "PTFE",
    "fail_position": "FC (Fail Close)",
    "valve_close_time": "30",
    "valve_open_time": "30",
    "actuator_type": "Rotork",
    "actuator_voltage": "220 VAC",
    "actuator_current": "2.5",
    "actuator_power": "0.55",
    "actuator_torque": "150",
    "accessories": "Local Position Indicator, Limit Switches, Solenoid"
  }}
]
```

📦 **EXTRACTION RULES:**
- **IF tag clearly visible**: Extract all available information
- **IF tag partially visible**: Use best judgment, note uncertainty in "notes"
- **IF data not visible**: Use "N/A" for unknown fields
- **IF only one value visible**: Put in appropriate field, use "N/A" for others
- **BETTER TO INCLUDE uncertain valves than miss them**

⚠️ **DETECTION CHECKLIST:**
✓ Scan entire P&ID systematically
✓ Check every valve symbol for motor/electric actuator
✓ Read all valve tags (MOV-, MV-, EV-, XV-, MOT-V-, MTV-)
✓ Look for "M" indicators on valves
✓ Check valve symbols with electrical connections
✓ Examine legend for MOV symbols
✓ Follow all process lines and check valves

🎯 **OUTPUT REQUIREMENTS:**
- Return JSON array with AT LEAST 1 MOV (typical is 3-15)
- If truly NO motor operated valves (very rare), return empty array []
- Use "N/A" for fields you cannot determine
- Include ALL MOVs found, even if data incomplete
- **RETURN ONLY THE JSON ARRAY** - NO markdown blocks, NO explanations, NO text before/after
- Start with **[** and end with **]**
"""
        return prompt
    
    def _parse_ai_response(self, ai_response):
        """
        Parse AI response to extract MOV data
        
        Args:
            ai_response: OpenAI response text
            
        Returns:
            list: Parsed MOV equipment data
        """
        try:
            # Remove markdown code blocks if present
            if "```json" in ai_response:
                start = ai_response.find("```json") + 7
                end = ai_response.find("```", start)
                ai_response = ai_response[start:end].strip()
            elif "```" in ai_response:
                start = ai_response.find("```") + 3
                end = ai_response.find("```", start)
                ai_response = ai_response[start:end].strip()
            
            # Parse JSON
            movs = json.loads(ai_response)
            
            if not isinstance(movs, list):
                logger.warning("[MOVExtractor] ⚠️ Response is not an array")
                return []
            
            logger.info(f"[MOVExtractor] ✅ Successfully parsed {len(movs)} MOVs")
            return movs
            
        except json.JSONDecodeError as e:
            logger.error(f"[MOVExtractor] ❌ JSON parse error: {str(e)}")
            logger.error(f"[MOVExtractor] Response preview: {ai_response[:500]}")
            return []
        except Exception as e:
            logger.error(f"[MOVExtractor] ❌ Parse error: {str(e)}")
            return []


# Convenience function for easy integration
def extract_movs_from_pid(pid_file_path, drawing_info=None):
    """
    Extract MOV equipment from P&ID file
    
    Args:
        pid_file_path: Path to P&ID file
        drawing_info: Optional drawing metadata
        
    Returns:
        list: Extracted MOV data
    """
    extractor = MOVEquipmentExtractor()
    return extractor.analyze_pid_for_movs(pid_file_path, drawing_info)
