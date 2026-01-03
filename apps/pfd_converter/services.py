"""
PFD to P&ID AI Conversion Service
Advanced AI-powered conversion using GPT-4o Vision and engineering intelligence
Enhanced with RAG (Retrieval Augmented Generation) for improved pattern recognition
Enhanced DALL-E 3 Integration for AI-Generated P&ID Drawings
"""
import openai
from openai import OpenAI
from decouple import config
from django.utils import timezone
import json
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import logging
import requests
from reportlab.lib.pagesizes import A1, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
import os
from django.conf import settings

logger = logging.getLogger(__name__)

# Initialize OpenAI client with API key
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
openai.api_key = OPENAI_API_KEY

# Initialize new OpenAI client for DALL-E 3
try:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    logger.info("✅ OpenAI client initialized for DALL-E 3 image generation")
except Exception as e:
    openai_client = None
    logger.warning(f"⚠️ Failed to initialize OpenAI client: {str(e)}")

# Import RAG-enhanced prompt systems and domain knowledge
try:
    from .prompt_enhancer import get_enhanced_prompt
    from .pid_prompt_enhancer import get_enhanced_pid_prompt
    from .domain_knowledge import get_domain_enhanced_extraction_prompt, domain_knowledge
    USE_RAG_PROMPTS = True
    USE_DOMAIN_KNOWLEDGE = True
    logger.info("✅ RAG-enhanced prompts enabled for PFD extraction and P&ID generation")
    logger.info("✅ Domain knowledge system loaded (P&ID Legends, Design Basis, Line Standards)")
except ImportError as e:
    USE_RAG_PROMPTS = False
    USE_DOMAIN_KNOWLEDGE = False
    logger.warning(f"⚠️ RAG prompts not available: {str(e)}, using default prompts")

# AI Drawing Generation Configuration
class DrawingConfig:
    """Configuration for AI-powered P&ID drawing generation"""
    
    # DALL-E 3 Settings (Best Quality)
    DALLE3_MODEL = "dall-e-3"
    DALLE3_SIZE = "1792x1024"  # Landscape HD
    DALLE3_QUALITY = "hd"
    
    # DALL-E 2 Settings (Fallback)
    DALLE2_MODEL = "dall-e-2"
    DALLE2_SIZE = "1024x1024"
    
    # Retry Configuration
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds
    
    # Enable/Disable features
    ENABLE_DALLE3 = config('ENABLE_DALLE3', default=True, cast=bool)
    ENABLE_DALLE2_FALLBACK = config('ENABLE_DALLE2_FALLBACK', default=True, cast=bool)
    ENABLE_PROGRAMMATIC_FALLBACK = True  # Always enabled
    
    @staticmethod
    def is_api_key_valid():
        """Check if OpenAI API key is configured"""
        return bool(OPENAI_API_KEY and OPENAI_API_KEY != '' and not OPENAI_API_KEY.startswith('your-'))


class PFDToPIDConverter:
    """
    AI service for converting Process Flow Diagrams to P&ID drawings
    Enhanced with:
    - GPT-4o Vision for intelligent extraction
    - RAG (Retrieval Augmented Generation) for learned patterns
    - Domain Knowledge (P&ID Legends, Design Basis, Engineering Standards)
    - 3-Step Process Engineering Workflow
    """
    
    def __init__(self, project_id=None):
        self.model = config('OPENAI_MODEL', default='gpt-4o')
        self.project_id = project_id
        
    def extract_pfd_data(self, image_file, project_id=None):
        """
        Extract process flow information from PFD using AI vision
        Implements Step 1 of PFD to P&ID conversion workflow
        
        Args:
            image_file: File object containing PFD image
            project_id: Optional project ID for project-specific design basis
            
        Returns:
            dict: Extracted process flow data with 3-step structure
        """
        try:
            # Convert image to base64
            image_data = self._prepare_image(image_file)
            
            # Use domain knowledge enhanced prompt for 3-step process
            if USE_DOMAIN_KNOWLEDGE:
                prompt = get_domain_enhanced_extraction_prompt(project_id)
                logger.info("🚀 Using Domain Knowledge Enhanced Prompt (3-Step Process Engineering)")
            elif USE_RAG_PROMPTS:
                prompt = get_enhanced_prompt()
                logger.info("🚀 Using RAG-enhanced prompt with learned patterns")
            else:
                prompt = self._get_extraction_prompt()
                logger.info("Using default extraction prompt")
            
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
            # Use RAG-enhanced P&ID prompt if available
            if USE_RAG_PROMPTS:
                prompt = get_enhanced_pid_prompt(pfd_data)
                logger.info("🚀 Using RAG-enhanced P&ID generation prompt with learned instrumentation patterns")
            else:
                prompt = self._get_pid_generation_prompt(pfd_data)
                logger.info("Using default P&ID generation prompt")
            
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
    
    def generate_pid_drawing(self, pfd_data, pid_specs, output_path=None):
        """
        Generate visual P&ID drawing from specifications using AI image generation
        
        Advanced multi-model approach:
        1. Try DALL-E 3 (HD quality, best results)
        2. Fallback to DALL-E 2 (if DALL-E 3 fails)
        3. Final fallback to programmatic PDF generation
        
        Args:
            pfd_data: Extracted PFD data
            pid_specs: Generated P&ID specifications
            output_path: Path to save the generated drawing (optional)
            
        Returns:
            str: Path to the generated P&ID drawing PDF
        """
        logger.info("🎨 Starting AI-powered P&ID diagram generation...")
        
        # Determine output path
        if output_path is None:
            media_root = settings.MEDIA_ROOT
            pid_drawings_dir = os.path.join(media_root, 'pid_drawings')
            os.makedirs(pid_drawings_dir, exist_ok=True)
            
            drawing_number = pid_specs.get('pid_drawing_number', 'PID-001')
            output_path = os.path.join(pid_drawings_dir, f"{drawing_number}.pdf")
        
        # Validate API key
        if not DrawingConfig.is_api_key_valid():
            logger.warning("⚠️ OpenAI API key not configured or invalid. Using programmatic fallback.")
            return self._create_fallback_pid_drawing(pid_specs, output_path)
        
        # Try DALL-E 3 first (best quality)
        if DrawingConfig.ENABLE_DALLE3 and openai_client:
            try:
                logger.info("🚀 Attempting P&ID generation with DALL-E 3 (HD Quality)...")
                image = self._generate_with_dalle3(pfd_data, pid_specs)
                if image:
                    self._create_pid_pdf(image, pid_specs, output_path)
                    logger.info(f"✅ P&ID drawing generated successfully with DALL-E 3: {output_path}")
                    return output_path
            except Exception as e:
                logger.warning(f"⚠️ DALL-E 3 generation failed: {str(e)}")
        
        # Try DALL-E 2 as fallback
        if DrawingConfig.ENABLE_DALLE2_FALLBACK:
            try:
                logger.info("🔄 Attempting P&ID generation with DALL-E 2 (Fallback)...")
                image = self._generate_with_dalle2(pfd_data, pid_specs)
                if image:
                    self._create_pid_pdf(image, pid_specs, output_path)
                    logger.info(f"✅ P&ID drawing generated successfully with DALL-E 2: {output_path}")
                    return output_path
            except Exception as e:
                logger.warning(f"⚠️ DALL-E 2 generation failed: {str(e)}")
        
        # Final fallback: Programmatic generation
        logger.info("📄 Using programmatic specification-based P&ID generation (final fallback)...")
        return self._create_fallback_pid_drawing(pid_specs, output_path)
    
    def _generate_with_dalle3(self, pfd_data, pid_specs):
        """
        Generate P&ID drawing using DALL-E 3 (HD Quality)
        
        Returns:
            PIL.Image: Generated drawing image, or None if failed
        """
        try:
            # Create enhanced prompt for DALL-E 3
            prompt = self._create_enhanced_pid_prompt(pfd_data, pid_specs)
            
            logger.info(f"📝 DALL-E 3 Prompt length: {len(prompt)} characters")
            
            # Generate with DALL-E 3
            response = openai_client.images.generate(
                model=DrawingConfig.DALLE3_MODEL,
                prompt=prompt,
                size=DrawingConfig.DALLE3_SIZE,
                quality=DrawingConfig.DALLE3_QUALITY,
                n=1
            )
            
            # Download and return image
            image_url = response.data[0].url
            logger.info(f"🖼️ DALL-E 3 generated image URL: {image_url[:50]}...")
            
            image_response = requests.get(image_url, timeout=30)
            image_response.raise_for_status()
            image = Image.open(BytesIO(image_response.content))
            
            logger.info(f"✅ DALL-E 3 image downloaded successfully: {image.size}")
            return image
            
        except Exception as e:
            logger.error(f"❌ DALL-E 3 generation failed: {str(e)}")
            return None
    
    def _generate_with_dalle2(self, pfd_data, pid_specs):
        """
        Generate P&ID drawing using DALL-E 2 (Fallback)
        
        Returns:
            PIL.Image: Generated drawing image, or None if failed
        """
        try:
            # Create simplified prompt for DALL-E 2 (1000 char limit)
            prompt = self._create_simplified_pid_prompt(pfd_data, pid_specs)
            
            logger.info(f"📝 DALL-E 2 Prompt length: {len(prompt)} characters")
            
            # Use old API for DALL-E 2 compatibility
            response = openai.Image.create(
                model=DrawingConfig.DALLE2_MODEL,
                prompt=prompt,
                size=DrawingConfig.DALLE2_SIZE,
                n=1
            )
            
            # Download and return image
            image_url = response['data'][0]['url']
            logger.info(f"🖼️ DALL-E 2 generated image URL: {image_url[:50]}...")
            
            image_response = requests.get(image_url, timeout=30)
            image_response.raise_for_status()
            image = Image.open(BytesIO(image_response.content))
            
            logger.info(f"✅ DALL-E 2 image downloaded successfully: {image.size}")
            return image
            
        except Exception as e:
            logger.error(f"❌ DALL-E 2 generation failed: {str(e)}")
            return None
    
    def _create_enhanced_pid_prompt(self, pfd_data, pid_specs):
        """
        Create enhanced, detailed prompt for DALL-E 3
        DALL-E 3 supports longer, more detailed prompts
        """
        equipment_list = pid_specs.get('equipment_list', [])
        instrument_list = pid_specs.get('instrument_list', [])
        piping_specs = pid_specs.get('piping_specifications', [])
        safety_devices = pid_specs.get('safety_devices', [])
        
        # Build comprehensive description
        prompt = f"""Create a professional P&ID (Piping and Instrumentation Diagram) technical engineering drawing:

🎯 DRAWING STYLE:
- Clean, professional engineering schematic
- Black lines on white background
- Technical blueprint aesthetic
- Clear, readable labels and tags
- Standard ISA-5.1 and ANSI symbols

📦 EQUIPMENT ({len(equipment_list)} items):"""
        
        # Add top equipment with details
        for i, equip in enumerate(equipment_list[:6], 1):
            tag = equip.get('tag', f'E-{i:03d}')
            equip_type = equip.get('type', 'Equipment')
            service = equip.get('service', equip.get('description', ''))
            prompt += f"\n• {tag}: {equip_type}"
            if service:
                prompt += f" ({service})"
        
        prompt += f"\n\n🎛️ INSTRUMENTATION ({len(instrument_list)} instruments):"
        
        # Add key instruments
        for i, inst in enumerate(instrument_list[:8], 1):
            tag = inst.get('tag', f'I-{i:03d}')
            inst_type = inst.get('type', 'Instrument')
            prompt += f"\n• {tag}: {inst_type} with ISA symbol bubble"
        
        # Add piping information
        if piping_specs and len(piping_specs) > 0:
            prompt += f"\n\n🔧 PIPING ({len(piping_specs[:4])} main lines):"
            for i, pipe in enumerate(piping_specs[:4], 1):
                line_num = pipe.get('line_number', f'LINE-{i}')
                size = pipe.get('size', '')
                service = pipe.get('service', '')
                prompt += f"\n• {line_num}"
                if size:
                    prompt += f" ({size})"
        
        # Add safety systems
        if safety_devices and len(safety_devices) > 0:
            prompt += f"\n\n🛡️ SAFETY SYSTEMS:" 
            for safety in safety_devices[:3]:
                tag = safety.get('tag', 'PSV')
                s_type = safety.get('type', 'Safety Valve')
                prompt += f"\n• {tag}: {s_type}"
        
        prompt += """\n\n📐 LAYOUT REQUIREMENTS:
- Horizontal flow from left to right
- Equipment shown with standard P&ID symbols
- Instruments in circular bubbles with tags
- Piping lines connecting all equipment
- Valves shown at appropriate locations
- Clear process flow direction
- Professional spacing and organization
- Grid layout for clarity

✨ QUALITY: High-detail technical engineering drawing, professional grade, clean and precise."""
        
        # Truncate if too long (DALL-E 3 limit is 4000 chars)
        if len(prompt) > 3900:
            prompt = prompt[:3900] + "..."
        
        return prompt
    
    def _create_simplified_pid_prompt(self, pfd_data, pid_specs):
        """
        Create simplified prompt for DALL-E 2 (1000 character limit)
        """
        equipment_count = len(pid_specs.get('equipment_list', []))
        instrument_count = len(pid_specs.get('instrument_list', []))
        
        prompt = f"""Professional P&ID engineering diagram: {equipment_count} equipment items with standard symbols, {instrument_count} instruments with ISA bubbles, piping connections, valves, technical blueprint style, black on white, clear labels, left-to-right flow, industrial process diagram."""
        
        # Ensure under 1000 characters for DALL-E 2
        if len(prompt) > 950:
            prompt = prompt[:947] + "..."
        
        return prompt
    
    def _create_pid_drawing_prompt(self, pfd_data, pid_specs):
        """Create detailed prompt for AI diagram generation (Legacy method, now uses _create_enhanced_pid_prompt)"""
        
        equipment_count = len(pid_specs.get('equipment_list', []))
        instrument_count = len(pid_specs.get('instrument_list', []))
        
        # Build comprehensive description
        prompt = f"""Professional engineering P&ID (Piping and Instrumentation Diagram) drawing in technical blueprint style:

DRAWING STANDARDS:
- ISA-5.1 standard symbols for instrumentation
- ANSI/API standard symbols for equipment and piping
- Professional engineering drawing appearance
- Black lines on white background
- Clear tag labels and line numbers

EQUIPMENT TO SHOW ({equipment_count} items):
"""
        
        # Add equipment details
        for i, equip in enumerate(pid_specs.get('equipment_list', [])[:8], 1):  # Limit to 8 for clarity
            tag = equip.get('tag', f'EQUIP-{i}')
            equip_type = equip.get('type', 'equipment')
            prompt += f"- {tag}: {equip_type} with standard P&ID symbol\n"
        
        prompt += f"\nINSTRUMENTATION ({instrument_count} instruments):\n"
        
        # Add instrumentation details
        for i, inst in enumerate(pid_specs.get('instrument_list', [])[:12], 1):  # Limit to 12
            tag = inst.get('tag', f'INST-{i}')
            inst_type = inst.get('type', 'instrument')
            prompt += f"- {tag}: {inst_type} with ISA symbol and bubble\n"
        
        # Add piping connections
        piping_specs = pid_specs.get('piping_specifications', [])
        if piping_specs:
            prompt += f"\nPIPING CONNECTIONS ({len(piping_specs[:6])} main lines):\n"
            for pipe in piping_specs[:6]:
                line_num = pipe.get('line_number', 'LINE')
                from_equip = pipe.get('from', 'SOURCE')
                to_equip = pipe.get('to', 'DEST')
                prompt += f"- {line_num}: connecting {from_equip} to {to_equip}\n"
        
        # Add safety devices
        safety_devices = pid_specs.get('safety_devices', [])
        if safety_devices:
            prompt += f"\nSAFETY SYSTEMS:\n"
            for safety in safety_devices[:4]:
                tag = safety.get('tag', 'PSV')
                prompt += f"- {tag}: pressure relief valve symbol\n"
        
        prompt += """
DRAWING REQUIREMENTS:
- Horizontal layout, left to right process flow
- Equipment symbols per ISA standard
- Clear instrument bubbles with tags
- Piping lines with proper connections
- Line numbers clearly labeled
- Valves shown with standard symbols
- Control loops indicated with dashed lines
- Professional engineering drawing quality
- Neat, organized, readable layout
- Standard P&ID symbology throughout

Style: Technical engineering drawing, professional P&ID, blueprint quality, black and white, industrial process diagram"""
        
        return prompt
    
    def _create_pid_pdf(self, image, pid_specs, output_path):
        """Create professional PDF with AI-generated P&ID drawing"""
        
        # Create PDF in A1 landscape format
        pdf = canvas.Canvas(output_path, pagesize=landscape(A1))
        width, height = landscape(A1)
        
        # Add title block
        drawing_number = pid_specs.get('pid_drawing_number', 'P&ID-001')
        title = pid_specs.get('pid_title', 'Process & Instrumentation Diagram')
        revision = pid_specs.get('pid_revision', 'A')
        
        # Draw title block border
        pdf.setStrokeColor(colors.black)
        pdf.setLineWidth(2)
        pdf.rect(20*mm, 20*mm, width - 40*mm, height - 40*mm)
        
        # Add title block information
        pdf.setFont("Helvetica-Bold", 24)
        pdf.drawString(30*mm, height - 40*mm, title)
        
        pdf.setFont("Helvetica", 14)
        pdf.drawString(30*mm, height - 50*mm, f"Drawing No: {drawing_number}")
        pdf.drawString(30*mm, height - 60*mm, f"Revision: {revision}")
        pdf.drawString(30*mm, height - 70*mm, f"Generated: {timezone.now().strftime('%Y-%m-%d %H:%M')}")
        
        # Add AI-generated P&ID drawing
        # Scale image to fit in drawing area
        drawing_area_width = width - 60*mm
        drawing_area_height = height - 120*mm
        
        # Save image temporarily
        temp_image_path = output_path.replace('.pdf', '_temp.png')
        image.save(temp_image_path, 'PNG')
        
        # Add image to PDF
        pdf.drawImage(temp_image_path, 30*mm, 80*mm, 
                     width=drawing_area_width, height=drawing_area_height,
                     preserveAspectRatio=True, mask='auto')
        
        # Add notes section
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(30*mm, 60*mm, "NOTES:")
        pdf.setFont("Helvetica", 9)
        notes = [
            "1. All instrumentation per ISA-5.1 standards",
            "2. Piping specifications per ASME B31.3",
            "3. Safety systems per API 14C",
            "4. Generated by AI-powered PFD to P&ID converter"
        ]
        y_pos = 55*mm
        for note in notes:
            pdf.drawString(35*mm, y_pos, note)
            y_pos -= 5*mm
        
        # Save PDF
        pdf.save()
        
        # Clean up temporary image
        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)
        
        logger.info(f"✅ P&ID PDF created: {output_path}")
    
    def _create_fallback_pid_drawing(self, pid_specs, output_path):
        """Create a basic P&ID drawing using programmatic approach (fallback)"""
        try:
            logger.info("Using fallback method to create P&ID drawing...")
            
            if output_path is None:
                media_root = settings.MEDIA_ROOT
                pid_drawings_dir = os.path.join(media_root, 'pid_drawings')
                os.makedirs(pid_drawings_dir, exist_ok=True)
                
                drawing_number = pid_specs.get('pid_drawing_number', 'PID-001')
                output_path = os.path.join(pid_drawings_dir, f"{drawing_number}_spec.pdf")
            
            # Create specification sheet PDF
            pdf = canvas.Canvas(output_path, pagesize=landscape(A1))
            width, height = landscape(A1)
            
            # Title
            pdf.setFont("Helvetica-Bold", 28)
            pdf.drawString(50*mm, height - 50*mm, "P&ID SPECIFICATIONS")
            
            drawing_number = pid_specs.get('pid_drawing_number', 'P&ID-001')
            pdf.setFont("Helvetica", 16)
            pdf.drawString(50*mm, height - 70*mm, f"Drawing No: {drawing_number}")
            
            # Equipment list
            y_position = height - 100*mm
            pdf.setFont("Helvetica-Bold", 14)
            pdf.drawString(50*mm, y_position, "EQUIPMENT LIST:")
            y_position -= 10*mm
            
            pdf.setFont("Helvetica", 11)
            for equip in pid_specs.get('equipment_list', [])[:15]:
                tag = equip.get('tag', 'N/A')
                equip_type = equip.get('type', 'N/A')
                description = equip.get('description', equip.get('service', 'N/A'))
                text = f"{tag} - {equip_type}: {description}"
                if len(text) > 100:
                    text = text[:97] + "..."
                pdf.drawString(55*mm, y_position, text)
                y_position -= 6*mm
                if y_position < 50*mm:
                    break
            
            # Instrumentation list
            y_position = height - 100*mm
            x_position = width / 2
            pdf.setFont("Helvetica-Bold", 14)
            pdf.drawString(x_position, y_position, "INSTRUMENTATION:")
            y_position -= 10*mm
            
            pdf.setFont("Helvetica", 11)
            for inst in pid_specs.get('instrument_list', [])[:15]:
                tag = inst.get('tag', 'N/A')
                inst_type = inst.get('type', 'N/A')
                text = f"{tag} - {inst_type}"
                pdf.drawString(x_position + 5*mm, y_position, text)
                y_position -= 6*mm
                if y_position < 50*mm:
                    break
            
            # Add note about visual diagram
            pdf.setFont("Helvetica-Bold", 11)
            pdf.setFillColorRGB(0, 0.3, 0.6)
            pdf.drawString(50*mm, 50*mm, "ℹ️ IMPORTANT INFORMATION:")
            
            pdf.setFont("Helvetica", 10)
            pdf.setFillColorRGB(0, 0, 0)
            notes = [
                "• This is a specification sheet generated programmatically.",
                "• For AI-generated visual P&ID drawings, ensure OpenAI API key is configured.",
                "• The system supports DALL-E 3 (HD quality) and DALL-E 2 (fallback) models.",
                "• Visual diagrams show equipment symbols, instrumentation bubbles, and piping connections.",
                "• To enable: Set OPENAI_API_KEY in environment variables or .env file.",
                "• For production use, verify all specifications with a qualified engineer."
            ]
            
            y_note = 45*mm
            for note in notes:
                pdf.drawString(52*mm, y_note, note)
                y_note -= 5*mm
            
            pdf.save()
            logger.info(f"✅ Fallback P&ID specification PDF created: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Fallback P&ID generation failed: {str(e)}")
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
        """Convert image or PDF to base64 for API"""
        try:
            # Read file content
            file_content = image_file.read()
            
            # Validate file content
            if not file_content or len(file_content) == 0:
                raise ValueError("Empty file uploaded. Please upload a valid PDF or image file.")
            
            image_file.seek(0)  # Reset file pointer
            
            # Detect file type by checking magic bytes and filename
            magic_bytes = file_content[:4] if len(file_content) >= 4 else b''
            filename = getattr(image_file, 'name', '').lower()
            
            is_pdf = (magic_bytes == b'%PDF' or filename.endswith('.pdf'))
            
            logger.info(f"File detection - Name: {filename}, Size: {len(file_content)} bytes, Magic: {magic_bytes[:10]}, Is PDF: {is_pdf}")
            
            if is_pdf:
                # Handle PDF file - convert first page to image
                import fitz  # PyMuPDF
                
                logger.info(f"Detected PDF file ({len(file_content)} bytes), converting to image...")
                
                # Validate PDF content before opening
                if len(file_content) < 100:
                    raise ValueError(f"PDF file is too small ({len(file_content)} bytes). File may be corrupted.")
                
                # Open PDF from bytes
                pdf_document = fitz.open(stream=file_content, filetype="pdf")
                
                # Get first page
                first_page = pdf_document[0]
                
                # Render page to image (high resolution)
                zoom = 2.0  # Higher zoom for better quality
                mat = fitz.Matrix(zoom, zoom)
                pix = first_page.get_pixmap(matrix=mat)
                
                # Convert to PIL Image
                img_data = pix.tobytes("png")
                image = Image.open(BytesIO(img_data))
                
                pdf_document.close()
                logger.info("✅ Successfully converted PDF to image")
            else:
                # Handle regular image file - open from bytes
                logger.info("Detected image file, processing...")
                try:
                    image = Image.open(BytesIO(file_content))
                    logger.info("✅ Successfully opened image file")
                except Exception as img_error:
                    # If opening from bytes fails, try from file object
                    logger.warning(f"Opening from bytes failed, trying file object: {img_error}")
                    image_file.seek(0)
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
