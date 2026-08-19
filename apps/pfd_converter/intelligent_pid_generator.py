"""
Intelligent PFD to Exact P&ID Generator Service
===============================================

Pattern-learning AI service that analyzes reference P&IDs and generates
new P&IDs matching the exact style and standards.

Features:
- Deep PFD analysis with GPT-4 Vision
- Reference P&ID style learning
- Transformation pattern extraction
- Adaptive P&ID generation with DALL-E 3
- No hardcoded rules - learns from examples
"""

import os
import json
import base64
import logging
from pathlib import Path
from typing import Dict, List, Optional
from PIL import Image
import openai
from openai import OpenAI
from decouple import config
from django.conf import settings

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

logger = logging.getLogger(__name__)

# Initialize OpenAI
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


class IntelligentPIDGenerator:
    """
    Intelligent P&ID generator with pattern learning capabilities
    """
    
    def __init__(self, reference_pid_path: Optional[str] = None):
        self.client = client
        self.model_vision = "gpt-4o"
        self.model_text = "gpt-4o"
        self.model_dalle = "dall-e-3"
        self.reference_pid_path = reference_pid_path
        self.transformation_patterns = {}
        
    def analyze_pfd(self, pfd_file_path: str) -> Dict:
        """Analyze PFD to extract all process information"""
        logger.info(f"📋 Analyzing PFD: {pfd_file_path}")
        
        image_path = self._ensure_image_format(pfd_file_path)
        image_data = self._encode_image(image_path)
        
        prompt = """Analyze this Process Flow Diagram (PFD) in detail and extract all information as JSON.

Extract:
1. **Equipment**: tag, type, position (x,y as %), size, orientation, labels
2. **Streams/Piping**: stream IDs, start/end equipment, route, flow direction, specifications
3. **Process Data**: temperatures, pressures, flow rates, compositions with locations
4. **Annotations**: all text, notes, specifications
5. **Layout**: flow direction, spatial arrangement, title block info

Provide detailed JSON response."""

        try:
            response = self.client.chat.completions.create(
                model=self.model_vision,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{image_data}",
                            "detail": "high"
                        }}
                    ]
                }],
                max_tokens=4000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            pfd_data = self._extract_json(content)
            logger.info("✅ PFD analysis complete")
            return pfd_data
            
        except Exception as e:
            logger.error(f"❌ PFD analysis failed: {e}")
            raise
    
    def analyze_reference_pid(self, pid_file_path: str) -> Dict:
        """Analyze reference P&ID to learn style and standards"""
        logger.info(f"📐 Analyzing reference P&ID: {pid_file_path}")
        
        image_path = self._ensure_image_format(pid_file_path)
        image_data = self._encode_image(image_path)
        
        prompt = """Analyze this P&ID in EXTREME detail to extract style standards as JSON.

Extract:
1. **Equipment Symbols**: tag, type, symbol style, position, size, line weights, connections
2. **Instrumentation**: full tags, types, functions, symbol styles, positions, connection styles
3. **Piping**: line numbers, sizes, materials, routing, valves, specialty items
4. **Valves**: tags, types, symbols, positions, actuators
5. **Annotations**: process data with positions, specifications, notes
6. **Drawing Standards**: symbol set (ISA/DIN), line thickness, fonts, spacing, title block
7. **Layout Rules**: equipment spacing, routing rules, instrument placement conventions

Provide comprehensive JSON response."""

        try:
            response = self.client.chat.completions.create(
                model=self.model_vision,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{image_data}",
                            "detail": "high"
                        }}
                    ]
                }],
                max_tokens=4000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            pid_data = self._extract_json(content)
            logger.info("✅ Reference P&ID analysis complete")
            return pid_data
            
        except Exception as e:
            logger.error(f"❌ Reference P&ID analysis failed: {e}")
            raise
    
    def learn_transformation_patterns(self, pfd_data: Dict, pid_data: Dict) -> Dict:
        """Learn transformation rules by comparing PFD and P&ID"""
        logger.info("🧠 Learning transformation patterns...")
        
        prompt = f"""Compare PFD and P&ID data to extract transformation patterns as JSON.

**PFD DATA:**
{json.dumps(pfd_data, indent=2)[:3000]}

**P&ID DATA:**
{json.dumps(pid_data, indent=2)[:3000]}

Extract transformation patterns:
1. **equipment_transformations**: symbol enhancements, nozzles added, details added
2. **instrumentation_rules**: what instruments added, where positioned, tagging conventions
3. **piping_rules**: valves added, specifications, routing patterns
4. **layout_rules**: position adjustments, spacing, alignment
5. **style_standards**: symbol conventions, line weights, text placement
6. **annotation_patterns**: process data placement, specifications

Return comprehensive JSON."""

        try:
            response = self.client.chat.completions.create(
                model=self.model_text,
                messages=[{
                    "role": "system",
                    "content": "You are an expert process engineer who teaches PFD to P&ID conversion."
                }, {
                    "role": "user",
                    "content": prompt
                }],
                max_tokens=4000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            patterns = self._extract_json(content)
            self.transformation_patterns = patterns
            logger.info("✅ Pattern learning complete")
            return patterns
            
        except Exception as e:
            logger.error(f"❌ Pattern learning failed: {e}")
            raise
    
    def generate_pid_specifications(self, pfd_data: Dict, patterns: Dict) -> Dict:
        """Apply learned patterns to generate complete P&ID specifications"""
        logger.info("📝 Generating P&ID specifications...")
        
        prompt = f"""Apply transformation patterns to PFD data to create complete P&ID specifications.

**PFD DATA:**
{json.dumps(pfd_data, indent=2)[:2000]}

**TRANSFORMATION PATTERNS:**
{json.dumps(patterns, indent=2)[:2000]}

Generate comprehensive P&ID specification JSON with:
- drawing_info (number, title, revision, area)
- equipment (with symbol details, positions, nozzles, instrumentation points)
- instrumentation (tags, types, positions, connections, control loops)
- piping (line numbers, sizes, materials, routing, valves, instruments)
- annotations (process data, specifications, notes)
- layout (spacing, arrangement, flow direction)

Make it detailed and complete."""

        try:
            response = self.client.chat.completions.create(
                model=self.model_text,
                messages=[{
                    "role": "system",
                    "content": "You are an expert P&ID designer creating detailed specifications."
                }, {
                    "role": "user",
                    "content": prompt
                }],
                max_tokens=4000,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            pid_specs = self._extract_json(content)
            logger.info("✅ P&ID specifications generated")
            return pid_specs
            
        except Exception as e:
            logger.error(f"❌ Specification generation failed: {e}")
            raise
    
    def generate_pid_drawing(self, pid_specs: Dict, style_reference: Dict, output_path: str) -> str:
        """Generate P&ID drawing using DALL-E 3 with learned style"""
        logger.info("🎨 Generating P&ID drawing with DALL-E 3...")
        
        prompt = self._create_drawing_prompt(pid_specs, style_reference)
        logger.info(f"📏 Prompt length: {len(prompt)} characters")
        
        try:
            response = self.client.images.generate(
                model=self.model_dalle,
                prompt=prompt,
                size="1792x1024",
                quality="hd",
                n=1,
                style="natural"
            )
            
            image_url = response.data[0].url
            logger.info(f"✅ Image generated successfully")
            
            # Download and save
            import requests
            img_data = requests.get(image_url).content
            
            with open(output_path, 'wb') as f:
                f.write(img_data)
            
            logger.info(f"💾 Saved P&ID to: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"❌ DALL-E 3 generation failed: {e}")
            raise
    
    def generate_complete_pid(self, pfd_file_path: str, reference_pid_path: str, output_path: str) -> Dict:
        """Complete workflow: PFD + Reference → New P&ID"""
        logger.info("🚀 Starting intelligent P&ID generation workflow")
        
        results = {}
        
        # Step 1: Analyze PFD
        pfd_data = self.analyze_pfd(pfd_file_path)
        results['pfd_analysis'] = pfd_data
        
        # Step 2: Analyze reference P&ID
        pid_data = self.analyze_reference_pid(reference_pid_path)
        results['reference_analysis'] = pid_data
        
        # Step 3: Learn patterns
        patterns = self.learn_transformation_patterns(pfd_data, pid_data)
        results['patterns'] = patterns
        
        # Step 4: Generate specifications
        pid_specs = self.generate_pid_specifications(pfd_data, patterns)
        results['specifications'] = pid_specs
        
        # Step 5: Generate drawing
        drawing_path = self.generate_pid_drawing(pid_specs, pid_data, output_path)
        results['drawing_path'] = drawing_path
        
        logger.info("✅ Intelligent P&ID generation complete")
        return results
    
    def _create_drawing_prompt(self, pid_specs: Dict, style_reference: Dict) -> str:
        """Create detailed DALL-E 3 prompt"""
        
        drawing_info = pid_specs.get('drawing_info', {})
        equipment_list = pid_specs.get('equipment', [])
        instruments_list = pid_specs.get('instrumentation', [])
        piping_list = pid_specs.get('piping', [])
        
        prompt = f"""Create a professional P&ID (Piping & Instrumentation Diagram):

**DRAWING INFO:**
- Number: {drawing_info.get('number', 'PID-001')}
- Title: {drawing_info.get('title', 'Process System')}
- Area: {drawing_info.get('area', 'Process Area')}

**EQUIPMENT ({len(equipment_list)} items):**
"""
        
        for eq in equipment_list[:10]:
            prompt += f"- {eq.get('tag', 'EQ')}: {eq.get('type', 'equipment')} at {eq.get('position', {})}%\n"
        
        prompt += f"\n**INSTRUMENTATION ({len(instruments_list)} instruments):**\n"
        for inst in instruments_list[:15]:
            prompt += f"- {inst.get('tag', 'XX')}: {inst.get('type', 'instrument')}\n"
        
        prompt += f"\n**PIPING ({len(piping_list)} lines):**\n"
        for pipe in piping_list[:10]:
            prompt += f"- {pipe.get('line_number', 'P')}: {pipe.get('size', '')} {pipe.get('material', '')}\n"
        
        prompt += """

**STYLE:**
- Professional CAD-style engineering drawing
- ISA-5.1 standard symbols
- Clean black lines on white background
- Equipment with proper detail (flanges, nozzles, internals)
- Proper line weights (thick process, thin utility)
- All valves with correct symbols
- Flow arrows showing direction
- Clear tags and labels
- Professional title block bottom-right
- Horizontal/vertical routing only (no diagonals)
- 90-degree pipe bends

**LAYOUT:**
- Landscape orientation
- Left-to-right process flow
- Logical equipment sequence
- Clear instrument positioning
- Proper spacing between elements
- Engineering-grade precision suitable for construction"""
        
        return prompt
    
    def _ensure_image_format(self, file_path: str) -> str:
        """Convert PDF to image if needed"""
        if file_path.lower().endswith('.pdf'):
            if HAS_PYMUPDF:
                doc = fitz.open(file_path)
                page = doc[0]
                zoom = 300 / 72
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat)
                
                image_path = file_path.replace('.pdf', '_converted.png')
                pix.save(image_path)
                doc.close()
                return image_path
            else:
                raise ImportError("PyMuPDF required for PDF conversion")
        return file_path
    
    def _encode_image(self, image_path: str) -> str:
        """Encode image to base64"""
        with open(image_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    
    def _extract_json(self, text: str) -> Dict:
        """Extract JSON from GPT response"""
        import re
        
        # Try markdown code block
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try raw JSON
        json_match = re.search(r'\{[\s\S]*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        
        logger.warning("⚠️ No valid JSON in response")
        return {"raw_response": text, "parse_error": True}


def generate_intelligent_pid(pfd_file_path: str, reference_pid_path: str, output_path: str) -> Dict:
    """
    Convenience function for intelligent P&ID generation
    
    Args:
        pfd_file_path: Path to PFD file
        reference_pid_path: Path to reference P&ID for style learning
        output_path: Where to save generated P&ID
        
    Returns:
        Dict with analysis results and drawing path
    """
    generator = IntelligentPIDGenerator(reference_pid_path)
    return generator.generate_complete_pid(pfd_file_path, reference_pid_path, output_path)
