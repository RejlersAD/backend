"""
AI-Powered P&ID Visual Drawing Generator
==========================================

Uses OpenAI's GPT-4 Vision + DALL-E 3 to generate professional P&ID drawings
that look like actual engineering diagrams with proper ISA symbols, piping,
instrumentation, and layout.

Approach:
1. Analyze PFD visually to understand layout and flow direction
2. Extract equipment, instruments, piping connections from PFD
3. Generate P&ID specifications with added instrumentation and details
4. Use DALL-E 3 to create a professional P&ID drawing based on specifications
5. Enhance the generated image with annotations and title block overlay
"""

import openai
from openai import OpenAI
from decouple import config
import json
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import logging
import requests
from reportlab.lib.pagesizes import A1, A3, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors
import os
from django.conf import settings
from .reference_learning_system import ReferencePIDLearner

logger = logging.getLogger(__name__)

# Initialize OpenAI client
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY and OPENAI_API_KEY != '' else None


class AIPIDDrawingGenerator:
    """
    Generates professional P&ID drawings using AI with reference learning
    """
    
    def __init__(self, reference_pid_path: str = None):
        self.client = openai_client
        self.model_vision = "gpt-4o"
        self.model_dalle = "dall-e-3"
        self.learner = ReferencePIDLearner()
        self.learned_patterns = None
        
        # If reference P&ID provided, learn from it
        if reference_pid_path and os.path.exists(reference_pid_path):
            logger.info(f"📚 Learning from reference P&ID: {reference_pid_path}")
            self.learned_patterns = self.learner.analyze_reference_pid(reference_pid_path)
        
    def generate_pid_drawing(self, pfd_image_path: str, pid_specs: dict, output_path: str, reference_pid_path: str = None) -> str:
        """
        Generate professional P&ID drawing from PFD and specifications
        
        Args:
            pfd_image_path: Path to original PFD image for reference
            pid_specs: P&ID specifications with equipment, instruments, piping
            output_path: Path to save the generated P&ID drawing
            reference_pid_path: Optional path to reference P&ID for learning
            
        Returns:
            str: Path to generated P&ID drawing
        """
        logger.info("🎨 Starting AI P&ID Drawing Generation with Reference Learning...")
        
        if not self.client:
            logger.warning("⚠️ OpenAI API key not configured. Using fallback method.")
            return self._create_fallback_drawing(pid_specs, output_path)
        
        try:
            # Step 0: Learn from reference P&ID if provided
            if reference_pid_path and os.path.exists(reference_pid_path):
                logger.info("  → Step 0: Learning from reference P&ID...")
                self.learned_patterns = self.learner.analyze_reference_pid(reference_pid_path)
                
                # Also compare PFD → P&ID if both available
                if pfd_image_path and os.path.exists(pfd_image_path):
                    logger.info("  → Analyzing PFD → P&ID transformation...")
                    transformation_rules = self.learner.compare_pfd_with_pid(pfd_image_path, reference_pid_path)
                    # Store transformation rules for use in prompt
                    self.learned_patterns['transformation_rules'] = transformation_rules
            
            # Step 1: Analyze PFD layout and flow direction
            logger.info("  → Step 1: Analyzing PFD layout...")
            layout_analysis = self._analyze_pfd_layout(pfd_image_path)
            
            # Step 2: Generate enhanced P&ID prompt with learned patterns
            logger.info("  → Step 2: Creating detailed P&ID specifications with learned patterns...")
            if self.learned_patterns:
                pid_prompt = self.learner.generate_enhanced_prompt(pid_specs, self.learned_patterns)
            else:
                pid_prompt = self._create_detailed_pid_prompt(pid_specs, layout_analysis)
            
            # Step 3: Generate P&ID image with DALL-E 3
            logger.info("  → Step 3: Generating P&ID drawing with AI...")
            pid_image = self._generate_with_dalle3(pid_prompt)
            
            if not pid_image:
                logger.warning("⚠️ AI generation failed. Using fallback.")
                return self._create_fallback_drawing(pid_specs, output_path)
            
            # Step 4: Enhance image with title block and annotations
            logger.info("  → Step 4: Adding title block and annotations...")
            enhanced_image = self._enhance_pid_image(pid_image, pid_specs)
            
            # Step 5: Create professional PDF
            logger.info("  → Step 5: Creating PDF document...")
            self._create_pid_pdf(enhanced_image, pid_specs, output_path)
            
            logger.info(f"✅ AI P&ID drawing generated successfully: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"❌ AI drawing generation failed: {str(e)}")
            logger.warning("  → Using fallback drawing method...")
            return self._create_fallback_drawing(pid_specs, output_path)
    
    def _analyze_pfd_layout(self, pfd_image_path: str) -> dict:
        """
        Analyze PFD layout using GPT-4 Vision to understand flow direction,
        equipment arrangement, and spatial relationships
        """
        try:
            # Read and encode image
            with open(pfd_image_path, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            
            prompt = """Analyze this Process Flow Diagram (PFD) and describe:

1. FLOW DIRECTION: Is the main process flow left-to-right, right-to-left, top-to-bottom, or circular?
2. EQUIPMENT ARRANGEMENT: How are major equipment positioned (horizontal line, vertical stack, grid)?
3. SPATIAL LAYOUT: Relative positions of equipment (upstream/downstream relationships)
4. DRAWING STYLE: Hand-drawn, CAD, schematic complexity level
5. KEY VISUAL ELEMENTS: Main process lines, utility connections, control loops

Respond in JSON format:
{
  "flow_direction": "left-to-right",
  "equipment_arrangement": "horizontal",
  "complexity": "medium",
  "style": "cad",
  "key_features": ["main process line", "heat exchangers", "vessels"]
}"""

            response = self.client.chat.completions.create(
                model=self.model_vision,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert process engineer analyzing PFD layouts. Return only valid JSON."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_data}",
                                    "detail": "low"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=500,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            layout_analysis = json.loads(content)
            logger.info(f"  ✅ Layout analysis: {layout_analysis.get('flow_direction', 'unknown')} flow")
            return layout_analysis
            
        except Exception as e:
            logger.warning(f"  ⚠️ Layout analysis failed: {str(e)}")
            return {
                "flow_direction": "left-to-right",
                "equipment_arrangement": "horizontal",
                "complexity": "medium",
                "style": "cad",
                "key_features": ["process flow"]
            }
    
    def _create_detailed_pid_prompt(self, pid_specs: dict, layout_analysis: dict) -> str:
        """
        Create detailed prompt for DALL-E 3 to generate professional P&ID drawing
        Based on actual engineering P&ID standards (ADNOC, ISA-5.1, ASME)
        """
        equipment_list = pid_specs.get('equipment_list', [])
        instrument_list = pid_specs.get('instrument_list', [])
        piping_specs = pid_specs.get('piping_specifications', [])
        safety_devices = pid_specs.get('safety_devices', [])
        
        # Build detailed equipment description with specifications
        equipment_desc = []
        for eq in equipment_list[:10]:
            tag = eq.get('tag', 'EQ-001')
            eq_type = eq.get('type', 'vessel').upper()
            description = eq.get('description', '')
            specs = eq.get('specifications', {})
            
            spec_text = ""
            if specs:
                if specs.get('design_pressure'):
                    spec_text += f" {specs.get('design_pressure')}"
                if specs.get('design_temperature'):
                    spec_text += f" {specs.get('design_temperature')}"
            
            equipment_desc.append(f"{tag}: {eq_type}{spec_text}")
        
        # Build instrumentation with loop details
        instrument_desc = []
        for inst in instrument_list[:15]:
            tag = inst.get('tag', 'PI-001')
            inst_type = inst.get('type', 'indicator')
            service = inst.get('description', inst.get('service', ''))
            instrument_desc.append(f"{tag} - {service}")
        
        # Build piping line details
        piping_desc = []
        for pipe in piping_specs[:8]:
            line_no = pipe.get('line_number', '')
            from_eq = pipe.get('from', '')
            to_eq = pipe.get('to', '')
            size = pipe.get('size', '')
            piping_desc.append(f"{line_no}: {from_eq} → {to_eq} ({size})")
        
        # Create DALL-E 3 prompt with extreme detail and engineering accuracy
        flow_direction = layout_analysis.get('flow_direction', 'left-to-right')
        
        prompt = f"""Create a highly detailed, professional Piping & Instrumentation Diagram (P&ID) following oil & gas industry standards (ADNOC DEP, ISA-5.1, ASME B31.3):

CRITICAL REQUIREMENTS - MUST FOLLOW EXACTLY:

1. DRAWING STYLE:
   - Black lines on white background (monochrome engineering drawing)
   - Technical CAD appearance with precise line work
   - Single-line diagram representation
   - Flow {flow_direction}
   - Grid background (faint)
   - Professional engineering quality

2. EQUIPMENT SYMBOLS - EXACT ISA STANDARDS:

{chr(10).join([f"   • {eq}" for eq in equipment_desc])}

   Equipment Symbol Rules:
   - VESSELS/TANKS: Vertical cylinder with elliptical/hemispherical heads, center vertical line
   - PUMPS: Circle with small triangle (impeller) inside, motor connection shown
   - HEAT EXCHANGERS: Rectangle with internal tubes/baffles, shell-and-tube pattern
   - COLUMNS/TOWERS: Tall vertical cylinder with platforms/trays indicated
   - COMPRESSORS: Circle with curved blade symbols
   - Each equipment MUST show equipment tag number clearly above/inside symbol
   - Show nozzle connections (inlet/outlet) on equipment

3. PIPING & CONNECTIONS:

{chr(10).join([f"   • {pipe}" for pipe in piping_desc]) if piping_desc else "   • Main process piping between all equipment"}

   Piping Rules:
   - Thick solid lines (process piping)
   - Thin solid lines (utility lines)
   - Dashed lines (instrument signal lines)
   - Direction arrows on all flow lines
   - Line sizes and numbers labeled
   - Proper orthogonal routing (90° angles, minimal crossings)
   - Show all valves inline (gate, globe, ball, check, control)

4. INSTRUMENTATION - ISA-5.1 SYMBOLS:

{chr(10).join([f"   • {inst}" for inst in instrument_desc])}

   Instrument Symbol Rules:
   - CIRCLE symbols with letter codes inside:
     * PI = Pressure Indicator (P in circle)
     * TI = Temperature Indicator (T in circle)
     * LI = Level Indicator (L in circle)
     * FI = Flow Indicator (F in circle)
     * FT = Flow Transmitter (F in circle)
     * LT = Level Transmitter (L in circle)
     * PT = Pressure Transmitter (P in circle)
     * TT = Temperature Transmitter (T in circle)
   - Place circles on or near equipment/piping where measurement occurs
   - Dashed lines connect instruments to control valves
   - Control valves shown with actuator symbol (rectangle on top)

5. VALVES - SHOW ALL TYPES:
   - Manual gate valves: X in line
   - Globe valves: Triangle in line
   - Ball valves: Circle with diameter line
   - Check valves: Triangle with line
   - Control valves: Valve symbol + actuator (square/rectangle on top)
   - Safety relief valves (PSV): Spring-loaded valve symbol
   - All valves must be clearly visible on piping lines

6. SAFETY SYSTEMS:

{chr(10).join([f"   • {d.get('tag', 'PSV')}: {d.get('type', 'PSV')} on {d.get('protected_equipment', ['equipment'])[0] if d.get('protected_equipment') else 'vessel'}" for d in safety_devices[:5]])}

   - PSVs: Spring-loaded valve symbol with discharge to flare/atmosphere
   - Rupture disks: Burst disk symbol
   - Emergency shutdown valves: ESD/SDV labels clearly shown

7. LABELS & ANNOTATIONS:
   - Every equipment: Tag number (e.g., V-101, P-102A/B, E-103)
   - Every instrument: Tag number (e.g., PI-101, TI-102, FIC-103)
   - Every major line: Line number with size (e.g., 6"-P-101-CS150)
   - Flow direction arrows on ALL piping
   - Process conditions on major streams (P, T, Flow if known)

8. LAYOUT & ORGANIZATION:
   - Equipment arranged {flow_direction} following process flow
   - Adequate spacing between equipment (not crowded)
   - Piping routed to minimize crossings
   - Keep drawing clean and readable
   - Title block area at bottom right corner

9. PROFESSIONAL QUALITY:
   - Sharp, clean lines
   - Consistent symbol sizes
   - Proper spacing and alignment
   - Engineering-grade clarity
   - Suitable for construction/installation use
   - No artistic interpretation - technical accuracy only

IMPORTANT: This is an ENGINEERING DOCUMENT, not an artistic rendering. Use standard ISA-5.1 symbols EXACTLY as they appear in engineering standards. The drawing must be technically accurate and follow oil & gas industry conventions."""

        logger.info(f"  → Generated detailed engineering prompt ({len(prompt)} chars)")
        logger.info(f"  → Equipment: {len(equipment_list)}, Instruments: {len(instrument_list)}, Piping: {len(piping_specs)}")
        
        return prompt
    
    def _generate_with_dalle3(self, prompt: str) -> Image.Image:
        """
        Generate P&ID drawing using DALL-E 3
        """
        try:
            logger.info("  → Calling DALL-E 3 API...")
            
            response = self.client.images.generate(
                model=self.model_dalle,
                prompt=prompt,
                size="1792x1024",  # Landscape format for P&ID
                quality="hd",
                n=1,
                style="natural"  # More technical/realistic style
            )
            
            # Download generated image
            image_url = response.data[0].url
            logger.info(f"  → Downloading generated image...")
            
            image_response = requests.get(image_url)
            image = Image.open(BytesIO(image_response.content))
            
            logger.info(f"  ✅ Image generated: {image.size[0]}x{image.size[1]} pixels")
            return image
            
        except Exception as e:
            logger.error(f"  ❌ DALL-E 3 generation failed: {str(e)}")
            return None
    
    def _enhance_pid_image(self, image: Image.Image, pid_specs: dict) -> Image.Image:
        """
        Enhance AI-generated image with:
        - Title block overlay
        - Drawing number and revision
        - Project information
        - Scale and notes
        """
        # Create larger canvas with space for title block
        enhanced_width = image.width
        enhanced_height = image.height + 200  # Add 200px for title block
        
        enhanced = Image.new('RGB', (enhanced_width, enhanced_height), 'white')
        
        # Paste original image
        enhanced.paste(image, (0, 0))
        
        # Draw title block
        draw = ImageDraw.Draw(enhanced)
        
        # Try to use a nice font, fallback to default
        try:
            title_font = ImageFont.truetype("arial.ttf", 24)
            info_font = ImageFont.truetype("arial.ttf", 16)
            small_font = ImageFont.truetype("arial.ttf", 12)
        except:
            title_font = ImageFont.load_default()
            info_font = ImageFont.load_default()
            small_font = ImageFont.load_default()
        
        # Title block position (bottom right)
        tb_x = enhanced_width - 600
        tb_y = image.height + 20
        
        # Draw title block border
        draw.rectangle(
            [(tb_x, tb_y), (enhanced_width - 20, enhanced_height - 20)],
            outline='black',
            width=3
        )
        
        # Drawing info
        drawing_info = pid_specs.get('drawing_info', {})
        title = drawing_info.get('title', 'P&ID DRAFT')
        drawing_number = drawing_info.get('drawing_number', 'PID-001')
        revision = drawing_info.get('revision', 'A')
        project_name = drawing_info.get('project_name', 'Oil & Gas Processing')
        
        # Draw text
        y_pos = tb_y + 20
        draw.text((tb_x + 20, y_pos), title, fill='black', font=title_font)
        
        y_pos += 40
        draw.text((tb_x + 20, y_pos), f"Drawing No: {drawing_number}", fill='black', font=info_font)
        
        y_pos += 30
        draw.text((tb_x + 20, y_pos), f"Revision: {revision}", fill='black', font=info_font)
        
        y_pos += 30
        draw.text((tb_x + 20, y_pos), f"Project: {project_name}", fill='black', font=small_font)
        
        y_pos += 25
        draw.text((tb_x + 20, y_pos), "Generated by RADAI AI System", fill='gray', font=small_font)
        
        return enhanced
    
    def _create_pid_pdf(self, image: Image.Image, pid_specs: dict, output_path: str):
        """
        Create professional PDF from enhanced P&ID image
        """
        # Save image to temporary location
        temp_image_path = output_path.replace('.pdf', '_temp.png')
        image.save(temp_image_path, 'PNG', quality=95)
        
        # Create PDF (A1 landscape)
        page_width, page_height = landscape(A1)
        c = canvas.Canvas(output_path, pagesize=landscape(A1))
        
        # Calculate image dimensions to fit page
        margin = 20*mm
        available_width = page_width - 2*margin
        available_height = page_height - 2*margin
        
        # Scale image to fit while maintaining aspect ratio
        img_ratio = image.width / image.height
        page_ratio = available_width / available_height
        
        if img_ratio > page_ratio:
            # Image is wider
            img_width = available_width
            img_height = available_width / img_ratio
        else:
            # Image is taller
            img_height = available_height
            img_width = available_height * img_ratio
        
        # Center image on page
        x_pos = margin + (available_width - img_width) / 2
        y_pos = margin + (available_height - img_height) / 2
        
        # Draw image
        c.drawImage(temp_image_path, x_pos, y_pos, width=img_width, height=img_height)
        
        # Add equipment schedule on separate page
        c.showPage()
        self._add_equipment_schedule_page(c, page_width, page_height, pid_specs)
        
        # Save PDF
        c.save()
        
        # Clean up temp image
        try:
            os.remove(temp_image_path)
        except:
            pass
        
        logger.info(f"  ✅ PDF created: {output_path}")
    
    def _add_equipment_schedule_page(self, c, width, height, pid_specs: dict):
        """
        Add equipment schedule and instrument index as second page
        """
        c.setFont("Helvetica-Bold", 18)
        c.drawString(50*mm, height - 50*mm, "EQUIPMENT SCHEDULE")
        
        # Equipment table
        equipment_list = pid_specs.get('equipment_list', [])
        y_pos = height - 80*mm
        
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50*mm, y_pos, "TAG")
        c.drawString(100*mm, y_pos, "TYPE")
        c.drawString(180*mm, y_pos, "DESCRIPTION")
        c.drawString(350*mm, y_pos, "SPECIFICATIONS")
        
        y_pos -= 20
        c.setFont("Helvetica", 9)
        
        for eq in equipment_list[:20]:  # Show up to 20 items
            tag = eq.get('tag', '')
            eq_type = eq.get('type', '')
            description = eq.get('description', '')[:40]
            specs = eq.get('specifications', {})
            
            spec_str = f"{specs.get('design_pressure', '')} {specs.get('design_temperature', '')}"
            
            c.drawString(50*mm, y_pos, tag)
            c.drawString(100*mm, y_pos, eq_type)
            c.drawString(180*mm, y_pos, description)
            c.drawString(350*mm, y_pos, spec_str)
            
            y_pos -= 15
            
            if y_pos < 100*mm:
                break
        
        # Instrument index
        y_pos = height - 400*mm
        c.setFont("Helvetica-Bold", 18)
        c.drawString(50*mm, y_pos, "INSTRUMENT INDEX")
        
        y_pos -= 30
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50*mm, y_pos, "TAG")
        c.drawString(100*mm, y_pos, "TYPE")
        c.drawString(180*mm, y_pos, "DESCRIPTION")
        c.drawString(350*mm, y_pos, "RANGE")
        
        y_pos -= 20
        c.setFont("Helvetica", 9)
        
        instrument_list = pid_specs.get('instrument_list', [])
        for inst in instrument_list[:15]:  # Show up to 15 instruments
            tag = inst.get('tag', '')
            inst_type = inst.get('type', '')
            description = inst.get('description', '')[:40]
            range_val = inst.get('range', '')
            
            c.drawString(50*mm, y_pos, tag)
            c.drawString(100*mm, y_pos, inst_type)
            c.drawString(180*mm, y_pos, description)
            c.drawString(350*mm, y_pos, range_val)
            
            y_pos -= 15
            
            if y_pos < 50*mm:
                break
    
    def _create_fallback_drawing(self, pid_specs: dict, output_path: str) -> str:
        """
        Create basic P&ID drawing using programmatic approach (fallback when AI fails)
        """
        logger.info("  → Creating fallback P&ID drawing...")
        
        # Create A1 landscape PDF
        page_width, page_height = landscape(A1)
        c = canvas.Canvas(output_path, pagesize=landscape(A1))
        
        # Title
        c.setFont("Helvetica-Bold", 24)
        drawing_info = pid_specs.get('drawing_info', {})
        title = drawing_info.get('title', 'P&ID DRAFT')
        c.drawString(50*mm, page_height - 50*mm, title)
        
        # Drawing info
        c.setFont("Helvetica", 14)
        c.drawString(50*mm, page_height - 70*mm, f"Drawing No: {drawing_info.get('drawing_number', 'PID-001')}")
        c.drawString(50*mm, page_height - 85*mm, f"Revision: {drawing_info.get('revision', 'A')}")
        
        # Draw equipment symbols
        equipment_list = pid_specs.get('equipment_list', [])
        x_start = 100*mm
        y_start = page_height - 150*mm
        
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50*mm, y_start + 20*mm, "PROCESS EQUIPMENT:")
        
        for i, eq in enumerate(equipment_list[:8]):
            x_pos = x_start + (i % 4) * 150*mm
            y_pos = y_start - (i // 4) * 100*mm
            
            # Draw equipment symbol (simple rectangle)
            c.setStrokeColor(colors.blue)
            c.setLineWidth(2)
            c.rect(x_pos, y_pos, 80*mm, 60*mm)
            
            # Label
            c.setFont("Helvetica-Bold", 11)
            tag = eq.get('tag', f'EQ-{i+1}')
            c.drawCentredString(x_pos + 40*mm, y_pos + 45*mm, tag)
            
            c.setFont("Helvetica", 9)
            eq_type = eq.get('type', 'Equipment')
            c.drawCentredString(x_pos + 40*mm, y_pos + 30*mm, eq_type)
            
            # Specifications
            c.setFont("Helvetica", 7)
            specs = eq.get('specifications', {})
            if specs:
                spec_text = f"{specs.get('design_pressure', '')} {specs.get('design_temperature', '')}"
                c.drawCentredString(x_pos + 40*mm, y_pos + 15*mm, spec_text)
        
        # Add equipment schedule page
        c.showPage()
        self._add_equipment_schedule_page(c, page_width, page_height, pid_specs)
        
        c.save()
        logger.info(f"  ✅ Fallback drawing created: {output_path}")
        
        return output_path
