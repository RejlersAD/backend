"""
Reference P&ID Learning System
================================

Uses GPT-4 Vision to analyze reference P&ID drawings and extract:
- Drawing style and conventions
- Symbol usage patterns
- Instrumentation placement rules
- Layout and spacing standards
- Line routing conventions
- Tag naming patterns

This learned knowledge is then used to generate accurate P&IDs
"""

import openai
from openai import OpenAI
from decouple import config
import json
import base64
import logging
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Initialize OpenAI client
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY and OPENAI_API_KEY != '' else None


class ReferencePIDLearner:
    """
    Analyzes reference P&ID drawings using GPT-4 Vision to learn drawing standards
    """
    
    def __init__(self):
        self.client = openai_client
        self.model = "gpt-4o"
        self.learned_patterns = None
        
    def analyze_reference_pid(self, pid_image_path: str) -> Dict:
        """
        Analyze a reference P&ID to extract drawing patterns and conventions
        
        Args:
            pid_image_path: Path to reference P&ID drawing
            
        Returns:
            Dict containing learned patterns, symbols, and conventions
        """
        logger.info("🔍 Analyzing reference P&ID drawing...")
        
        if not self.client:
            logger.warning("⚠️ OpenAI API not configured")
            return self._get_default_patterns()
        
        try:
            # Read and encode image
            with open(pid_image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            prompt = """Analyze this P&ID (Piping & Instrumentation Diagram) drawing in extreme detail and extract ALL drawing conventions, patterns, and standards used.

ANALYZE AND DOCUMENT:

1. **EQUIPMENT SYMBOLS** - How are equipment drawn:
   - Vessel/tank symbols (shape, size, orientation)
   - Pump symbols (type, style, motor representation)
   - Heat exchanger symbols (shell-and-tube, plate, etc.)
   - Valve symbols (gate, globe, ball, check, control)
   - How equipment tags are positioned
   - Equipment elevation/orientation

2. **INSTRUMENTATION SYMBOLS** - ISA-5.1 conventions:
   - How instrument circles are drawn (size, style)
   - Letter codes used (PI, TI, FI, LI, etc.)
   - Where instruments are placed relative to equipment
   - How transmitters vs indicators are shown
   - Control loop connections (dashed lines, solid lines)
   - Actuator symbols on control valves

3. **PIPING & LINES**:
   - Main process line thickness and style
   - Utility line representation
   - Signal line style (dashed, dotted)
   - How line crossings are handled
   - Flow direction arrow style and placement
   - Line numbering format and position
   - Line sizing notation

4. **VALVE PLACEMENT & TYPES**:
   - Where valves are placed on lines
   - Isolation valve symbols
   - Control valve representation
   - Check valve orientation
   - Safety valve (PSV) symbols
   - Block valve spacing

5. **LAYOUT & SPACING**:
   - Equipment spacing (horizontal/vertical)
   - Flow direction (left-to-right, top-to-bottom)
   - How streams are organized
   - Equipment elevation differences
   - Drawing density and crowding

6. **LABELS & TEXT**:
   - Equipment tag format (e.g., V-101, P-102A/B)
   - Instrument tag format (e.g., PI-101)
   - Line number format (e.g., 6"-P-101-CS150)
   - Text size relative to symbols
   - Where labels are positioned
   - Process condition annotations

7. **TITLE BLOCK & BORDERS**:
   - Title block position and content
   - Border style
   - Drawing number format
   - Revision system
   - Notes and legends

8. **SPECIAL FEATURES**:
   - How tie-ins are shown
   - Continuation symbols
   - North arrow or flow direction indicator
   - Scale notation
   - Any unique conventions

9. **LINE ROUTING STYLE**:
   - Preference for orthogonal vs angled lines
   - How to avoid crossings
   - Parallel line spacing
   - Branch connection style

10. **OVERALL DRAWING STYLE**:
    - Hand-drawn vs CAD
    - Line weight consistency
    - Symbol proportions
    - Drawing cleanliness
    - Professional standards evident

Return a comprehensive JSON with all observed patterns, conventions, and rules that should be followed to create identical drawings:

{
  "equipment_symbols": {
    "vessels": "description of how vessels are drawn",
    "pumps": "pump symbol style",
    "exchangers": "heat exchanger style",
    ...
  },
  "instrumentation": {
    "circle_style": "how instrument circles look",
    "placement_rules": "where instruments are positioned",
    "letter_codes": ["PI", "TI", "FI", ...],
    "connection_style": "how signal lines connect",
    ...
  },
  "piping": {
    "main_line_style": "thick solid lines",
    "utility_style": "thin solid lines",
    "signal_style": "dashed lines",
    "flow_arrows": "arrow style and placement",
    ...
  },
  "layout": {
    "flow_direction": "left-to-right",
    "equipment_spacing": "description",
    "organization": "how drawing is organized",
    ...
  },
  "labels": {
    "equipment_tag_format": "format description",
    "instrument_tag_format": "format description",
    "text_placement": "where text goes",
    ...
  },
  "drawing_style": {
    "line_thickness": "description",
    "symbol_size": "proportions",
    "overall_appearance": "CAD/hand-drawn/etc",
    ...
  },
  "key_conventions": [
    "list of critical conventions to follow"
  ]
}"""

            logger.info("  → Calling GPT-4 Vision to analyze reference P&ID...")
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert P&ID drafter with 20+ years experience analyzing engineering drawings. Extract every detail and convention from P&ID drawings."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_data}",
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
            
            # Try to parse JSON
            try:
                patterns = json.loads(content)
            except:
                # If not pure JSON, try to extract JSON from markdown
                import re
                json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
                if json_match:
                    patterns = json.loads(json_match.group(1))
                else:
                    # Fallback: create structured data from text
                    patterns = {
                        "analysis": content,
                        "format": "text"
                    }
            
            logger.info(f"✅ Reference P&ID analyzed successfully")
            logger.info(f"  → Extracted patterns for: {', '.join(patterns.keys())}")
            
            self.learned_patterns = patterns
            return patterns
            
        except Exception as e:
            logger.error(f"❌ Reference analysis failed: {str(e)}")
            return self._get_default_patterns()
    
    def compare_pfd_with_pid(self, pfd_path: str, pid_path: str) -> Dict:
        """
        Compare PFD with its corresponding P&ID to understand transformation rules
        
        Args:
            pfd_path: Path to PFD drawing
            pid_path: Path to corresponding P&ID drawing
            
        Returns:
            Dict with transformation rules and patterns
        """
        logger.info("🔄 Comparing PFD → P&ID transformation...")
        
        if not self.client:
            return {"status": "API not available"}
        
        try:
            # Read both images
            with open(pfd_path, 'rb') as f:
                pfd_data = base64.b64encode(f.read()).decode('utf-8')
            with open(pid_path, 'rb') as f:
                pid_data = base64.b64encode(f.read()).decode('utf-8')
            
            prompt = """Compare these two drawings - PFD (Process Flow Diagram) and P&ID (Piping & Instrumentation Diagram) - to understand the transformation rules.

CRITICAL ANALYSIS REQUIRED:

1. **WHAT WAS ADDED** (PFD → P&ID):
   - What instrumentation was added?
   - What valves were added?
   - What details were expanded?
   - What control loops appeared?

2. **WHAT WAS KEPT THE SAME**:
   - Which equipment remained identical?
   - What flow paths stayed the same?
   - What process conditions were preserved?

3. **WHAT WAS MODIFIED**:
   - How were equipment symbols changed?
   - How was layout adjusted?
   - How were connections detailed?

4. **INSTRUMENTATION RULES**:
   - For each equipment type, what instruments were added?
   - Where were instruments placed?
   - What control strategies are evident?

5. **TRANSFORMATION PATTERNS**:
   - Equipment → Detailed equipment + instrumentation
   - Simple line → Detailed piping with valves
   - Process condition → Multiple measurement points

Provide detailed transformation rules in JSON:

{
  "transformation_rules": {
    "vessels": "what instruments/valves get added to vessels",
    "pumps": "what gets added to pump circuits",
    "heat_exchangers": "exchanger P&ID details",
    ...
  },
  "instrumentation_placement": {
    "pressure": "where PT/PI go",
    "temperature": "where TT/TI go",
    "level": "where LT/LI go",
    "flow": "where FT/FI go"
  },
  "valve_additions": {
    "isolation": "where isolation valves go",
    "control": "where control valves go",
    "check": "where check valves go"
  },
  "detail_level": "how much detail is added",
  "key_principles": [
    "list of transformation principles"
  ]
}"""

            logger.info("  → Analyzing PFD → P&ID transformation with AI...")
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a senior process engineer analyzing the transformation from PFD to P&ID. Extract the exact rules and patterns used."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "PFD Drawing:"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{pfd_data}",
                                    "detail": "high"
                                }
                            },
                            {"type": "text", "text": "Corresponding P&ID Drawing:"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{pid_data}",
                                    "detail": "high"
                                }
                            },
                            {"type": "text", "text": prompt}
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            
            # Parse JSON response
            try:
                rules = json.loads(content)
            except:
                import re
                json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
                if json_match:
                    rules = json.loads(json_match.group(1))
                else:
                    rules = {"analysis": content, "format": "text"}
            
            logger.info(f"✅ Transformation rules extracted")
            
            return rules
            
        except Exception as e:
            logger.error(f"❌ Comparison failed: {str(e)}")
            return {"status": "failed", "error": str(e)}
    
    def generate_enhanced_prompt(self, pid_specs: dict, learned_patterns: Dict) -> str:
        """
        Generate DALL-E 3 prompt using learned patterns from reference P&ID
        
        Args:
            pid_specs: P&ID specifications from pipeline
            learned_patterns: Patterns learned from reference P&ID
            
        Returns:
            Enhanced prompt for DALL-E 3
        """
        logger.info("  → Creating enhanced prompt using learned patterns...")
        
        equipment_list = pid_specs.get('equipment_list', [])
        instrument_list = pid_specs.get('instrument_list', [])
        
        # Build equipment list
        equipment_text = "\n".join([
            f"   - {eq.get('tag', '')}: {eq.get('type', '').upper()}" 
            for eq in equipment_list[:10]
        ])
        
        # Build instrument list
        instrument_text = "\n".join([
            f"   - {inst.get('tag', '')}: {inst.get('type', '')}"
            for inst in instrument_list[:15]
        ])
        
        # Extract key conventions from learned patterns
        drawing_style = learned_patterns.get('drawing_style', {})
        equipment_symbols = learned_patterns.get('equipment_symbols', {})
        instrumentation = learned_patterns.get('instrumentation', {})
        piping = learned_patterns.get('piping', {})
        layout = learned_patterns.get('layout', {})
        key_conventions = learned_patterns.get('key_conventions', [])
        
        prompt = f"""Create a professional P&ID (Piping & Instrumentation Diagram) that EXACTLY matches this reference style:

REFERENCE DRAWING ANALYSIS - MUST FOLLOW THESE CONVENTIONS:

Drawing Style:
{json.dumps(drawing_style, indent=2)}

Equipment Symbols to Use:
{json.dumps(equipment_symbols, indent=2)}

Instrumentation Standards:
{json.dumps(instrumentation, indent=2)}

Piping Conventions:
{json.dumps(piping, indent=2)}

Layout Organization:
{json.dumps(layout, indent=2)}

CRITICAL CONVENTIONS TO FOLLOW:
{chr(10).join([f'- {conv}' for conv in key_conventions])}

EQUIPMENT TO DRAW:
{equipment_text}

INSTRUMENTATION TO SHOW:
{instrument_text}

REQUIREMENTS:
1. Match the EXACT symbol style from the reference
2. Follow the SAME layout organization
3. Use IDENTICAL line styles and weights
4. Place instruments in the SAME manner
5. Use the SAME text sizing and placement
6. Match the overall drawing density and spacing
7. Black and white professional engineering drawing
8. ISA-5.1 compliant symbols
9. Clear equipment and instrument tags
10. Professional CAD appearance

Create a drawing that looks like it came from the same drawing set as the reference."""

        return prompt
    
    def _get_default_patterns(self) -> Dict:
        """Return default patterns if AI analysis fails"""
        return {
            "equipment_symbols": {
                "vessels": "Vertical cylinders with elliptical heads",
                "pumps": "Circle with triangle impeller",
                "exchangers": "Rectangle with tube pattern"
            },
            "instrumentation": {
                "circle_style": "Standard ISA-5.1 circles",
                "placement_rules": "On or near equipment/piping"
            },
            "piping": {
                "main_line_style": "Thick solid lines",
                "signal_style": "Dashed lines"
            },
            "layout": {
                "flow_direction": "left-to-right",
                "spacing": "adequate"
            },
            "drawing_style": {
                "overall_appearance": "Professional CAD"
            },
            "key_conventions": [
                "ISA-5.1 compliant",
                "ADNOC DEP standards",
                "Professional engineering quality"
            ]
        }
