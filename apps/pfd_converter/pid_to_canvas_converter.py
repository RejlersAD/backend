"""
P&ID to Canvas Converter
========================

Smart system to convert AI-generated P&ID drawings to editable canvas format.
Uses GPT-4 Vision to extract elements from P&ID images/PDFs and convert them
to structured data that can be loaded into the 2D Expert Mode canvas.

Intelligence Features:
- Recognizes equipment symbols and extracts positions
- Identifies instrumentation and connections
- Detects piping routes and flow directions
- Extracts annotations and labels
- Preserves layout and spatial relationships
"""

import openai
from openai import OpenAI
from decouple import config
import json
import base64
import logging
from typing import Dict, List, Optional
from pathlib import Path
from pdf2image import convert_from_path
import os

logger = logging.getLogger(__name__)

# Initialize OpenAI client
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY and OPENAI_API_KEY != '' else None


class PIDToCanvasConverter:
    """
    Converts AI-generated P&ID drawings to editable canvas format
    using GPT-4 Vision for intelligent element extraction
    """
    
    def __init__(self):
        self.client = openai_client
        self.model = "gpt-4o"
    
    def convert_pid_to_canvas_data(self, pid_file_path: str, pid_specs: dict = None) -> Dict:
        """
        Main conversion function: P&ID file → Canvas data structure
        
        Args:
            pid_file_path: Path to AI-generated P&ID PDF or image
            pid_specs: Original P&ID specifications from pipeline
            
        Returns:
            Dictionary with canvas-compatible data structure
        """
        logger.info(f"🎨 Converting P&ID to canvas format: {pid_file_path}")
        
        # Step 1: Convert PDF to image if needed
        image_path = self._ensure_image_format(pid_file_path)
        
        # Step 2: Analyze P&ID using GPT-4 Vision
        if self.client:
            vision_analysis = self._analyze_pid_with_vision(image_path)
        else:
            logger.warning("⚠️ OpenAI not configured, using specifications fallback")
            vision_analysis = None
        
        # Step 3: Merge vision analysis with original specs (smart fallback)
        canvas_data = self._create_canvas_structure(vision_analysis, pid_specs)
        
        logger.info(f"✅ Canvas data created with {len(canvas_data['equipment'])} equipment items")
        return canvas_data
    
    def _ensure_image_format(self, file_path: str) -> str:
        """Convert PDF to image if needed, return image path"""
        if file_path.lower().endswith('.pdf'):
            logger.info("  → Converting PDF to image for analysis...")
            try:
                images = convert_from_path(file_path, first_page=1, last_page=1, dpi=150)
                if images:
                    image_path = file_path.replace('.pdf', '_page1.png')
                    images[0].save(image_path, 'PNG')
                    logger.info(f"  → PDF converted: {image_path}")
                    return image_path
            except Exception as e:
                logger.error(f"  ❌ PDF conversion failed: {str(e)}")
                return file_path
        return file_path
    
    def _encode_image_base64(self, image_path: str) -> str:
        """Encode image to base64 for GPT-4 Vision"""
        with open(image_path, 'rb') as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    
    def _analyze_pid_with_vision(self, image_path: str) -> Dict:
        """
        Use GPT-4 Vision to analyze P&ID drawing and extract:
        - Equipment symbols and positions
        - Instrumentation and locations
        - Piping routes and connections
        - Annotations and labels
        - Flow directions
        """
        logger.info("  → Analyzing P&ID with GPT-4 Vision...")
        
        try:
            # Encode image
            image_data = self._encode_image_base64(image_path)
            
            # Create detailed analysis prompt
            prompt = """Analyze this P&ID (Piping & Instrumentation Diagram) and extract all elements with their positions.

**Task**: Extract structured data for canvas editor conversion.

**1. EQUIPMENT ITEMS**
For each equipment (vessels, tanks, pumps, heat exchangers, reactors):
- Tag/ID (e.g., V-101, P-201)
- Type (vessel, pump, heat_exchanger, tank, reactor, column, filter, etc.)
- Approximate position as % from top-left (x%, y%)
- Size category (small, medium, large)
- Orientation (vertical, horizontal)
- Connections (inlet/outlet positions)

**2. INSTRUMENTATION**
For each instrument (transmitters, indicators, control valves):
- Tag (e.g., FT-101, PT-201, TI-301, LCV-401)
- Type (flow, pressure, temperature, level, analyzer)
- Function (indicator, transmitter, controller, switch, valve)
- Position (x%, y%)
- Connected to which equipment/pipe
- Signal type (measurement, control, alarm)

**3. PIPING & CONNECTIONS**
For each pipe/line:
- Line number (e.g., 4"-P-101-CS)
- Start point (equipment tag or position)
- End point (equipment tag or position)
- Waypoints (intermediate points for route)
- Pipe size (e.g., 4", 6", 2")
- Specification (material, rating)
- Flow direction arrows

**4. ANNOTATIONS & LABELS**
- Process data (flow rates, temperatures, pressures)
- Equipment notes and specifications
- Safety notes (PSVs, interlocks, alarms)
- General notes and callouts

**5. LAYOUT INFORMATION**
- Overall flow direction (left-to-right, top-to-bottom, etc.)
- Drawing scale/dimensions
- Title block information

**OUTPUT FORMAT** (JSON):
```json
{
  "equipment": [
    {
      "tag": "V-101",
      "type": "vessel",
      "position": {"x": 25, "y": 40},
      "size": "large",
      "orientation": "vertical",
      "connections": {
        "inlet": {"position": "top", "x": 25, "y": 35},
        "outlet": {"position": "bottom", "x": 25, "y": 50}
      }
    }
  ],
  "instrumentation": [
    {
      "tag": "PT-101",
      "type": "pressure",
      "function": "transmitter",
      "position": {"x": 30, "y": 42},
      "connected_to": "V-101",
      "signal_type": "measurement"
    }
  ],
  "piping": [
    {
      "line_number": "4-P-101-CS",
      "from": "P-101",
      "to": "V-101",
      "waypoints": [
        {"x": 10, "y": 45},
        {"x": 25, "y": 45}
      ],
      "size": "4 inch",
      "flow_direction": "forward"
    }
  ],
  "annotations": [
    {
      "type": "process_data",
      "text": "350°C, 25 bar",
      "position": {"x": 35, "y": 40},
      "related_to": "V-101"
    }
  ],
  "layout": {
    "flow_direction": "left-to-right",
    "drawing_number": "P&ID-XXX-001",
    "title": "Process System"
  }
}
```

Be precise with positions (as % of canvas). Extract ALL visible elements."""

            # Call GPT-4 Vision
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_data}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            # Extract JSON from response
            content = response.choices[0].message.content
            logger.info(f"  → GPT-4 Vision response received ({len(content)} chars)")
            
            # Parse JSON (handle markdown code blocks)
            if '```json' in content:
                json_start = content.find('```json') + 7
                json_end = content.find('```', json_start)
                json_str = content[json_start:json_end].strip()
            elif '```' in content:
                json_start = content.find('```') + 3
                json_end = content.find('```', json_start)
                json_str = content[json_start:json_end].strip()
            else:
                json_str = content.strip()
            
            analysis = json.loads(json_str)
            logger.info(f"✅ Vision analysis complete:")
            logger.info(f"   - Equipment: {len(analysis.get('equipment', []))}")
            logger.info(f"   - Instruments: {len(analysis.get('instrumentation', []))}")
            logger.info(f"   - Pipes: {len(analysis.get('piping', []))}")
            logger.info(f"   - Annotations: {len(analysis.get('annotations', []))}")
            
            return analysis
            
        except Exception as e:
            logger.error(f"❌ Vision analysis failed: {str(e)}")
            return None
    
    def _create_canvas_structure(self, vision_analysis: Dict, pid_specs: Dict) -> Dict:
        """
        Create canvas-compatible data structure
        Intelligently merges vision analysis with original specifications
        """
        logger.info("  → Creating canvas data structure...")
        
        # Initialize canvas structure
        canvas_data = {
            "version": "1.0",
            "metadata": {
                "source": "ai_generated_pid",
                "conversion_method": "gpt4_vision_analysis"
            },
            "equipment": [],
            "instrumentation": [],
            "piping": [],
            "annotations": [],
            "layout": {}
        }
        
        # Use vision analysis if available, fallback to specs
        if vision_analysis:
            canvas_data["equipment"] = self._convert_equipment(vision_analysis.get("equipment", []))
            canvas_data["instrumentation"] = self._convert_instrumentation(vision_analysis.get("instrumentation", []))
            canvas_data["piping"] = self._convert_piping(vision_analysis.get("piping", []))
            canvas_data["annotations"] = self._convert_annotations(vision_analysis.get("annotations", []))
            canvas_data["layout"] = vision_analysis.get("layout", {})
        
        # Fallback: Use pid_specs if no vision analysis
        if not canvas_data["equipment"] and pid_specs:
            logger.info("  → Using specifications fallback...")
            canvas_data["equipment"] = self._extract_equipment_from_specs(pid_specs)
            canvas_data["instrumentation"] = self._extract_instrumentation_from_specs(pid_specs)
            canvas_data["layout"] = {
                "flow_direction": "left-to-right",
                "drawing_number": pid_specs.get("drawing_info", {}).get("drawing_number", ""),
                "title": pid_specs.get("drawing_info", {}).get("title", "")
            }
        
        # Enrich with original specifications data
        if pid_specs:
            canvas_data = self._enrich_with_specs(canvas_data, pid_specs)
        
        return canvas_data
    
    def _convert_equipment(self, equipment_list: List[Dict]) -> List[Dict]:
        """Convert vision-extracted equipment to canvas format"""
        converted = []
        for eq in equipment_list:
            converted.append({
                "id": eq.get("tag", f"EQ-{len(converted)+1}"),
                "tag": eq.get("tag", ""),
                "type": eq.get("type", "vessel"),
                "position": {
                    "x": float(eq.get("position", {}).get("x", 50)),
                    "y": float(eq.get("position", {}).get("y", 50))
                },
                "size": eq.get("size", "medium"),
                "orientation": eq.get("orientation", "vertical"),
                "connections": eq.get("connections", {}),
                "properties": {
                    "editable": True,
                    "draggable": True
                }
            })
        return converted
    
    def _convert_instrumentation(self, instrument_list: List[Dict]) -> List[Dict]:
        """Convert vision-extracted instruments to canvas format"""
        converted = []
        for inst in instrument_list:
            converted.append({
                "id": inst.get("tag", f"I-{len(converted)+1}"),
                "tag": inst.get("tag", ""),
                "type": inst.get("type", "pressure"),
                "function": inst.get("function", "indicator"),
                "position": {
                    "x": float(inst.get("position", {}).get("x", 50)),
                    "y": float(inst.get("position", {}).get("y", 50))
                },
                "connected_to": inst.get("connected_to", ""),
                "signal_type": inst.get("signal_type", "measurement"),
                "properties": {
                    "editable": True,
                    "draggable": True
                }
            })
        return converted
    
    def _convert_piping(self, piping_list: List[Dict]) -> List[Dict]:
        """Convert vision-extracted piping to canvas format"""
        converted = []
        for pipe in piping_list:
            converted.append({
                "id": pipe.get("line_number", f"PIPE-{len(converted)+1}"),
                "line_number": pipe.get("line_number", ""),
                "from": pipe.get("from", ""),
                "to": pipe.get("to", ""),
                "waypoints": pipe.get("waypoints", []),
                "size": pipe.get("size", ""),
                "specification": pipe.get("specification", ""),
                "flow_direction": pipe.get("flow_direction", "forward"),
                "properties": {
                    "editable": True,
                    "style": "orthogonal"
                }
            })
        return converted
    
    def _convert_annotations(self, annotation_list: List[Dict]) -> List[Dict]:
        """Convert vision-extracted annotations to canvas format"""
        converted = []
        for ann in annotation_list:
            converted.append({
                "id": f"ANN-{len(converted)+1}",
                "type": ann.get("type", "note"),
                "text": ann.get("text", ""),
                "position": {
                    "x": float(ann.get("position", {}).get("x", 50)),
                    "y": float(ann.get("position", {}).get("y", 50))
                },
                "related_to": ann.get("related_to", ""),
                "properties": {
                    "editable": True
                }
            })
        return converted
    
    def _extract_equipment_from_specs(self, pid_specs: Dict) -> List[Dict]:
        """Fallback: Extract equipment from original specifications"""
        equipment = []
        equipment_list = pid_specs.get("equipment_list", [])
        
        # Simple grid layout for fallback
        cols = 4
        spacing_x = 100.0 / (cols + 1)
        spacing_y = 100.0 / (len(equipment_list) / cols + 1)
        
        for i, eq in enumerate(equipment_list):
            row = i // cols
            col = i % cols
            
            equipment.append({
                "id": eq.get("tag", f"EQ-{i+1}"),
                "tag": eq.get("tag", ""),
                "type": eq.get("symbol_type", "vessel").lower(),
                "position": {
                    "x": (col + 1) * spacing_x,
                    "y": (row + 1) * spacing_y
                },
                "size": "medium",
                "orientation": "vertical",
                "properties": {
                    "editable": True,
                    "draggable": True
                }
            })
        
        return equipment
    
    def _extract_instrumentation_from_specs(self, pid_specs: Dict) -> List[Dict]:
        """Fallback: Extract instrumentation from original specifications"""
        instruments = []
        instrument_list = pid_specs.get("instrument_list", [])
        
        for i, inst in enumerate(instrument_list):
            tag = inst.get("tag", f"I-{i+1}")
            instruments.append({
                "id": tag,
                "tag": tag,
                "type": self._extract_instrument_type(tag),
                "function": self._extract_instrument_function(tag),
                "position": {
                    "x": 50 + (i % 5) * 10,  # Spread across
                    "y": 30 + (i // 5) * 15
                },
                "properties": {
                    "editable": True,
                    "draggable": True
                }
            })
        
        return instruments
    
    def _extract_instrument_type(self, tag: str) -> str:
        """Extract instrument type from tag (e.g., PT-101 → pressure)"""
        if not tag:
            return "general"
        prefix = tag.split('-')[0] if '-' in tag else tag[:2]
        type_map = {
            'P': 'pressure',
            'T': 'temperature',
            'F': 'flow',
            'L': 'level',
            'A': 'analyzer',
            'Q': 'quality',
            'V': 'valve'
        }
        return type_map.get(prefix[0], 'general')
    
    def _extract_instrument_function(self, tag: str) -> str:
        """Extract instrument function from tag (e.g., PT-101 → transmitter)"""
        if not tag:
            return "indicator"
        prefix = tag.split('-')[0] if '-' in tag else tag[:3]
        if 'T' in prefix and len(prefix) > 1:
            return 'transmitter'
        elif 'I' in prefix:
            return 'indicator'
        elif 'C' in prefix:
            return 'controller'
        elif 'V' in prefix:
            return 'valve'
        return 'indicator'
    
    def _enrich_with_specs(self, canvas_data: Dict, pid_specs: Dict) -> Dict:
        """Enrich canvas data with additional information from specifications"""
        # Add detailed properties to equipment
        equipment_list = pid_specs.get("equipment_list", [])
        for canvas_eq in canvas_data["equipment"]:
            # Find matching equipment in specs
            spec_eq = next((eq for eq in equipment_list if eq.get("tag") == canvas_eq["tag"]), None)
            if spec_eq:
                canvas_eq["properties"]["description"] = spec_eq.get("description", "")
                canvas_eq["properties"]["design_data"] = spec_eq.get("design_data", {})
                canvas_eq["properties"]["material"] = spec_eq.get("material", "")
        
        # Add detailed properties to instruments
        instrument_list = pid_specs.get("instrument_list", [])
        for canvas_inst in canvas_data["instrumentation"]:
            spec_inst = next((inst for inst in instrument_list if inst.get("tag") == canvas_inst["tag"]), None)
            if spec_inst:
                canvas_inst["properties"]["description"] = spec_inst.get("description", "")
                canvas_inst["properties"]["range"] = spec_inst.get("range", "")
                canvas_inst["properties"]["location"] = spec_inst.get("location", "")
        
        # Add drawing info to layout
        drawing_info = pid_specs.get("drawing_info", {})
        canvas_data["layout"].update({
            "drawing_number": drawing_info.get("drawing_number", ""),
            "title": drawing_info.get("title", ""),
            "revision": drawing_info.get("revision", ""),
            "date": drawing_info.get("date", ""),
            "project_name": drawing_info.get("project_name", "")
        })
        
        return canvas_data


# Utility function for API endpoint
def convert_pid_file_to_canvas(pid_file_path: str, pid_specs: Dict = None) -> Dict:
    """
    Convenience function to convert P&ID file to canvas data
    
    Usage:
        canvas_data = convert_pid_file_to_canvas('/path/to/pid.pdf', pid_specs)
    """
    converter = PIDToCanvasConverter()
    return converter.convert_pid_to_canvas_data(pid_file_path, pid_specs)
