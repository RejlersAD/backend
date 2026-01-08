"""
PFD to P&ID Pattern Learning Service
Soft-coded service that learns conversion patterns from PFD-P&ID pairs using GPT-4 Vision
"""
import os
import json
import base64
import logging
from openai import OpenAI
from django.conf import settings
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# ============================================================================
# PATTERN LEARNING CONFIGURATION
# ============================================================================

PATTERN_LEARNING_CONFIG = {
    "analysis_categories": {
        "equipment_mapping": {
            "description": "How PFD equipment maps to P&ID equipment",
            "extract": [
                "nozzle additions",
                "internal details added",
                "elevation specifications",
                "connection details",
                "equipment symbol detail level"
            ]
        },
        "piping_expansion": {
            "description": "How PFD lines expand to detailed P&ID piping",
            "extract": [
                "line numbering pattern",
                "isolation valve placement rules",
                "control valve placement rules",
                "typical valve spacing",
                "line size specifications",
                "material class assignments"
            ]
        },
        "instrumentation_addition": {
            "description": "What instruments are added in P&ID",
            "extract": [
                "instrument types by equipment type",
                "instrument placement patterns",
                "tag numbering scheme",
                "measurement range selection",
                "control loop configurations",
                "alarm and trip settings"
            ]
        },
        "control_strategy": {
            "description": "Control philosophy implementation",
            "extract": [
                "control loop types",
                "cascade control patterns",
                "ratio control patterns",
                "override control patterns",
                "PID vs ON/OFF selection",
                "setpoint management"
            ]
        },
        "safety_integration": {
            "description": "Safety system additions",
            "extract": [
                "PSV placement rules",
                "ESD valve placement",
                "high/low trip patterns",
                "interlock logic patterns",
                "SIL level assignments",
                "safety device sizing rules"
            ]
        },
        "utility_connections": {
            "description": "Utility integration patterns",
            "extract": [
                "instrument air distribution",
                "steam tracing patterns",
                "cooling water connections",
                "drain and vent placement",
                "sample point locations",
                "utility line sizing"
            ]
        }
    },
    "conversion_rules_to_learn": {
        "equipment_rules": [
            "What details are added to each equipment type",
            "How equipment is sized and specified",
            "What connections are required",
            "What internals are shown"
        ],
        "piping_rules": [
            "Line numbering format and logic",
            "Valve placement decision trees",
            "Line size selection criteria",
            "Material class selection",
            "Insulation and tracing requirements"
        ],
        "instrument_rules": [
            "Instrument selection by process variable",
            "Tag numbering patterns",
            "Range and accuracy requirements",
            "Installation and mounting details",
            "Signal type selection"
        ],
        "control_rules": [
            "Control loop configuration",
            "Controller type selection",
            "Tuning parameter guidelines",
            "Alarm limit setting",
            "Trip point determination"
        ],
        "safety_rules": [
            "Safety device selection criteria",
            "Set pressure determination",
            "Discharge routing",
            "Interlock logic development",
            "Redundancy requirements"
        ]
    },
    "output_structure": {
        "learned_patterns": {
            "equipment_mapping": {},
            "piping_rules": {},
            "instrumentation_rules": {},
            "control_patterns": {},
            "safety_patterns": {},
            "utility_patterns": {}
        },
        "conversion_workflow": [],
        "decision_trees": {},
        "automation_config": {}
    }
}


class PFDtoPIDPatternLearner:
    """Learn conversion patterns from PFD-P&ID pairs using GPT-4 Vision"""
    
    def __init__(self, api_key=None):
        """Initialize with OpenAI API key"""
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.client = OpenAI(api_key=self.api_key)
        self.config = PATTERN_LEARNING_CONFIG
    
    def convert_pdf_to_base64(self, pdf_path, page_num=0, dpi=200):
        """Convert PDF page to base64 image"""
        try:
            pdf_document = fitz.open(pdf_path)
            page = pdf_document[page_num]
            
            # Convert to high-quality image
            zoom = dpi / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            
            # Convert to PNG bytes and encode
            img_bytes = pix.tobytes("png")
            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            
            pdf_document.close()
            return img_base64
            
        except Exception as e:
            logger.error(f"PDF conversion failed: {e}")
            raise
    
    def analyze_pfd_pid_pair(self, pfd_path, pid_path, pfd_data=None):
        """Analyze PFD and P&ID pair to learn conversion patterns"""
        
        logger.info("🔍 Analyzing PFD-P&ID pair for pattern learning...")
        
        # Convert both documents to images
        pfd_image = self.convert_pdf_to_base64(pfd_path, dpi=200)
        pid_image = self.convert_pdf_to_base64(pid_path, dpi=200)
        
        # Build comprehensive analysis prompt
        prompt = self._build_pattern_learning_prompt(pfd_data)
        
        try:
            # Send both images to GPT-4 Vision for comparison
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{pfd_image}",
                                    "detail": "high"
                                }
                            },
                            {
                                "type": "text",
                                "text": "\n\n**AND HERE IS THE CORRESPONDING P&ID:**\n"
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{pid_image}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            logger.info("✅ Pattern analysis complete")
            
            # Parse and structure the learned patterns
            patterns = self._parse_learned_patterns(content)
            return patterns
            
        except Exception as e:
            logger.error(f"Pattern learning failed: {e}")
            raise
    
    def _build_pattern_learning_prompt(self, pfd_data=None):
        """Build comprehensive prompt for pattern learning"""
        
        prompt = """You are an expert process engineer analyzing PFD to P&ID conversion patterns.

**OBJECTIVE:**
Compare the PFD (Process Flow Diagram) and P&ID (Piping & Instrumentation Diagram) provided and learn the detailed conversion patterns, rules, and decision-making logic used to transform PFD into P&ID.

**ANALYSIS FRAMEWORK:**

1. **EQUIPMENT MAPPING ANALYSIS:**
   For each equipment item in PFD, identify:
   - How it's represented in P&ID (symbol detail, size, orientation)
   - What additional details are added (nozzles, internals, supports)
   - Equipment tag mapping and numbering
   - Specification details added (materials, dimensions, ratings)
   - Connection points and their specifications
   
   **Provide Rules:**
   - Equipment detail addition rules by type (vessels, pumps, heat exchangers, etc.)
   - Symbol library requirements
   - Tag numbering schemes
   - Specification documentation patterns

2. **PIPING EXPANSION ANALYSIS:**
   For each PFD line, identify:
   - How single PFD line becomes detailed P&ID piping
   - Line numbering format and assignment logic
   - Where isolation valves are placed and why
   - Where control valves are added and why
   - Typical valve spacing rules
   - Line size determination method
   - Material class selection logic
   - Insulation and tracing requirements
   
   **Provide Rules:**
   - Line numbering format: [Project]-[Area]-[System]-[Line#]
   - Isolation valve placement decision tree
   - Control valve sizing and placement rules
   - Typical valve spacing standards
   - Material selection criteria by service
   - Insulation requirements by temperature/service

3. **INSTRUMENTATION ADDITION ANALYSIS:**
   For each equipment and process section, identify:
   - What instruments are added (PT, FT, LT, TT, etc.)
   - Instrument placement patterns (inlet, outlet, top, bottom)
   - Tag numbering scheme and logic
   - Measurement range selection criteria
   - Control loop configuration
   - Alarm and trip point setting logic
   - Signal type selection (4-20mA, digital, etc.)
   
   **Provide Rules:**
   - Instrument selection matrix by equipment type
   - Tag numbering format: [Type]-[Area]-[Equipment]-[Sequence]
   - Range selection guidelines
   - Alarm limit calculation methods
   - Trip point setting criteria
   - Instrument placement standards

4. **CONTROL STRATEGY ANALYSIS:**
   Identify control philosophy:
   - Control loop types (feedback, feedforward, cascade, ratio)
   - PID vs ON/OFF control selection criteria
   - Controller tuning parameter guidelines
   - Setpoint management approach
   - Override control logic
   - Interlock conditions
   
   **Provide Rules:**
   - Control loop selection decision tree
   - PID tuning parameter recommendations
   - Alarm and trip hierarchy
   - Control valve fail-safe positions
   - Override logic patterns

5. **SAFETY SYSTEM ANALYSIS:**
   Identify safety additions:
   - Where PSVs (Pressure Safety Valves) are placed
   - How set pressures are determined
   - Where ESD (Emergency Shutdown) valves are located
   - High/low pressure trip patterns
   - High/low level trip patterns
   - Interlock logic development
   - SIL (Safety Integrity Level) requirements
   
   **Provide Rules:**
   - PSV placement criteria
   - Set pressure calculation method
   - ESD valve placement logic
   - Trip point determination
   - Interlock logic patterns
   - Redundancy requirements

6. **UTILITY INTEGRATION ANALYSIS:**
   Identify utility connections:
   - Instrument air distribution pattern
   - Steam tracing requirements and layout
   - Cooling water connections
   - Drain and vent placement rules
   - Sample point locations
   - Utility line sizing
   
   **Provide Rules:**
   - Utility connection standards
   - Header sizing calculations
   - Drain and vent spacing rules
   - Sample point selection criteria

7. **DESIGN STANDARDS AND CONVENTIONS:**
   - Drawing symbols and their meanings
   - Line type conventions (solid, dashed, dotted)
   - Instrument symbol conventions (ISA/ANSI standards)
   - Tag numbering systems
   - Material and piping class codes
   - Design codes referenced (ASME, API, etc.)

8. **CONVERSION WORKFLOW:**
   Provide step-by-step process:
   1. [Step 1 description]
   2. [Step 2 description]
   ...
   Include decision points and validation checksl

9. **SOFT CODING CONFIGURATION:**
   Provide a configuration structure that can be used to automate the conversion:
   - Equipment type definitions and rules
   - Piping specification templates
   - Instrument selection matrices
   - Control loop templates
   - Safety device templates
   - Tag numbering generators
   - Validation rules

**OUTPUT FORMAT:**
Return a comprehensive JSON object with the following structure:
```json
{
  "equipment_mapping_rules": {
    "by_equipment_type": {
      "vessel": {...},
      "pump": {...},
      "heat_exchanger": {...}
    },
    "detail_addition_patterns": [...],
    "tag_numbering": {...}
  },
  "piping_rules": {
    "line_numbering_format": "...",
    "isolation_valve_placement": {...},
    "control_valve_placement": {...},
    "line_size_selection": {...},
    "material_selection": {...}
  },
  "instrumentation_rules": {
    "instrument_selection_matrix": {...},
    "tag_numbering_format": "...",
    "placement_patterns": {...},
    "range_selection": {...},
    "alarm_trip_settings": {...}
  },
  "control_patterns": {
    "loop_types": {...},
    "controller_selection": {...},
    "tuning_guidelines": {...},
    "interlock_patterns": {...}
  },
  "safety_patterns": {
    "psv_placement": {...},
    "set_pressure_calculation": "...",
    "esd_placement": {...},
    "trip_settings": {...},
    "redundancy_rules": {...}
  },
  "utility_patterns": {
    "instrument_air": {...},
    "steam_tracing": {...},
    "drain_vent": {...}
  },
  "conversion_workflow": [
    "step 1...",
    "step 2..."
  ],
  "soft_coding_config": {
    "templates": {...},
    "decision_trees": {...},
    "validation_rules": {...}
  }
}
```

Be extremely detailed and specific - these patterns will be used to automatically generate P&IDs from PFDs."""

        if pfd_data:
            prompt += f"\n\n**PFD DATA FOR REFERENCE:**\n{json.dumps(pfd_data, indent=2)}"
        
        return prompt
    
    def _parse_learned_patterns(self, content):
        """Parse GPT-4 response into structured patterns"""
        try:
            # Try to extract JSON
            if '```json' in content:
                json_str = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                json_str = content.split('```')[1].split('```')[0].strip()
            else:
                json_str = content.strip()
            
            patterns = json.loads(json_str)
            logger.info("✅ Patterns parsed successfully")
            return patterns
            
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ JSON parsing failed: {e}")
            return {
                "raw_content": content,
                "error": "Failed to parse as JSON",
                "note": "Manual processing required"
            }
    
    def save_learned_patterns(self, patterns, output_path):
        """Save learned patterns to JSON file"""
        try:
            with open(output_path, 'w') as f:
                json.dump(patterns, f, indent=2)
            logger.info(f"✅ Patterns saved to: {output_path}")
        except Exception as e:
            logger.error(f"❌ Failed to save patterns: {e}")
            raise


def learn_patterns_from_pair(pfd_path, pid_path, pfd_data=None, output_path=None):
    """
    Convenience function to learn patterns from PFD-P&ID pair
    
    Args:
        pfd_path: Path to PFD PDF file
        pid_path: Path to P&ID PDF file
        pfd_data: Optional existing PFD analysis data
        output_path: Where to save learned patterns
    
    Returns:
        dict: Learned conversion patterns
    """
    learner = PFDtoPIDPatternLearner()
    patterns = learner.analyze_pfd_pid_pair(pfd_path, pid_path, pfd_data)
    
    if output_path:
        learner.save_learned_patterns(patterns, output_path)
    
    return patterns
