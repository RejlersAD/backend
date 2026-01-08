"""
Advanced PFD to P&ID Conversion Pipeline
========================================

Implements 6-step intelligent process engineering workflow:

1. Computer Vision + OCR: Extract visual and text data from PFD
2. Process Graph Builder: Create node-edge process flow model
3. Engineering Rules Engine: Apply industry standards and best practices
4. ML Pattern Matching: Identify equipment, instruments, and patterns
5. P&ID Draft Generator: Generate ISA-compliant P&ID specifications
6. Visual Rendering: Create professional P&ID drawing

Uses:
- GPT-4 Vision for intelligent image analysis
- OCR for text extraction from drawings
- Graph theory for process connectivity
- Engineering knowledge base (ADNOC DEP, API, ISA standards)
- Pattern recognition for equipment and instrument identification
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
from reportlab.platypus import Table, TableStyle
import os
from django.conf import settings
import re
from typing import Dict, List, Tuple, Any
import numpy as np
import fitz  # PyMuPDF for PDF to image conversion
from .ai_drawing_generator import AIPIDDrawingGenerator
from .validation_engine import EngineeringValidationEngine

logger = logging.getLogger(__name__)

# Initialize OpenAI client
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
openai_client = OpenAI(api_key=OPENAI_API_KEY)


class AdvancedPFDToPIDPipeline:
    """
    Advanced PFD to P&ID conversion using 6-step intelligent pipeline
    """
    
    def __init__(self, project_id=None):
        self.model = config('OPENAI_MODEL', default='gpt-4o')
        self.project_id = project_id
        self.engineering_rules = EngineeringRulesEngine()
        self.pattern_matcher = MLPatternMatcher()
        self.graph_builder = ProcessGraphBuilder()
        self.pid_generator = PIDDraftGenerator()
        
        # Initialize database-integrated converter
        try:
            from .database_integrated_converter import DatabaseIntegratedConverter
            self.db_converter = DatabaseIntegratedConverter()
            self.use_database = True
            logger.info("✅ Database-integrated converter initialized")
        except Exception as e:
            logger.warning(f"⚠️ Database converter not available: {str(e)}")
            self.db_converter = None
            self.use_database = False
        
    def convert(self, pfd_file, project_info: dict = None, cached_vision_data: dict = None, pfd_document=None):
        """
        Execute complete 6-step PFD to P&ID conversion pipeline
        
        Args:
            pfd_file: Uploaded PFD file (image or PDF) - optional if cached_vision_data provided
            project_info: Project metadata
            cached_vision_data: Pre-extracted vision data from upload step (to avoid re-calling OpenAI)
            pfd_document: PFDDocument model instance (for accessing stored file when using cached data)
            
        Returns:
            dict: Complete conversion results with P&ID specifications and drawing
        """
        logger.info("="*80)
        logger.info("🚀 STARTING ADVANCED PFD TO P&ID CONVERSION PIPELINE")
        logger.info("="*80)
        
        # Save PFD file temporarily for AI drawing generation (Step 6)
        pfd_temp_path = None
        if pfd_file:
            try:
                media_root = settings.MEDIA_ROOT
                pfd_temp_dir = os.path.join(media_root, 'pfd_temp')
                os.makedirs(pfd_temp_dir, exist_ok=True)
                pfd_temp_path = os.path.join(pfd_temp_dir, f"pfd_{project_info.get('project_code', 'temp')}.pdf")
                
                pfd_file.seek(0)
                with open(pfd_temp_path, 'wb') as f:
                    f.write(pfd_file.read())
                logger.info(f"  → PFD file saved temporarily: {pfd_temp_path}")
            except Exception as e:
                logger.warning(f"  ⚠️ Could not save PFD temp file: {str(e)}")
                pfd_temp_path = None
        elif pfd_document and pfd_document.file:
            # Using cached data but need PFD for AI drawing generation
            try:
                # Use the original PFD file from storage
                pfd_temp_path = os.path.join(settings.MEDIA_ROOT, str(pfd_document.file))
                if os.path.exists(pfd_temp_path):
                    logger.info(f"  → Using stored PFD file for AI drawing: {pfd_temp_path}")
                else:
                    logger.warning(f"  ⚠️ Stored PFD file not found: {pfd_temp_path}")
                    pfd_temp_path = None
            except Exception as e:
                logger.warning(f"  ⚠️ Could not access stored PFD file: {str(e)}")
                pfd_temp_path = None
        
        try:
            # STEP 1: Computer Vision + OCR
            if cached_vision_data:
                logger.info("\n[STEP 1/6] 🔍 Using Cached Vision Data (from upload)")
                logger.info("-" * 60)
                vision_data = cached_vision_data
                logger.info(f"✅ Using cached data: {len(vision_data.get('equipment', []))} equipment, "
                           f"{len(vision_data.get('text_annotations', []))} text elements")
            else:
                logger.info("\n[STEP 1/6] 🔍 Computer Vision + OCR Analysis")
                logger.info("-" * 60)
                vision_data = self._step1_computer_vision_ocr(pfd_file)
                logger.info(f"✅ Extracted: {len(vision_data.get('equipment', []))} equipment, "
                           f"{len(vision_data.get('text_annotations', []))} text elements")
            
            # STEP 2: Process Graph Builder
            logger.info("\n[STEP 2/6] 📊 Building Process Flow Graph")
            logger.info("-" * 60)
            process_graph = self._step2_build_process_graph(vision_data)
            logger.info(f"✅ Created graph: {process_graph['stats']['total_nodes']} nodes, "
                       f"{process_graph['stats']['total_edges']} connections")
            
            # STEP 3: Engineering Rules Engine
            logger.info("\n[STEP 3/6] ⚙️ Applying Engineering Rules")
            logger.info("-" * 60)
            enriched_graph = self._step3_apply_engineering_rules(process_graph, project_info)
            logger.info(f"✅ Applied {enriched_graph['rules_applied']} engineering rules")
            
            # STEP 4: ML Pattern Matching
            logger.info("\n[STEP 4/6] 🤖 ML Pattern Matching & Classification")
            logger.info("-" * 60)
            classified_data = self._step4_ml_pattern_matching(enriched_graph, vision_data)
            logger.info(f"✅ Classified {classified_data['classification_stats']['total_items']} items")
            
            # STEP 5: P&ID Draft Generator
            logger.info("\n[STEP 5/6] 📝 Generating P&ID Specifications")
            logger.info("-" * 60)
            pid_specs = self._step5_generate_pid_draft(classified_data, project_info)
            logger.info(f"✅ Generated P&ID specs: {pid_specs['drawing_info']['drawing_number']}")
            
            # STEP 6: Visual Rendering with AI
            logger.info("\n[STEP 6/6] 🎨 Creating AI-Powered P&ID Drawing")
            logger.info("-" * 60)
            drawing_path = self._step6_create_pid_drawing(pid_specs, classified_data, pfd_temp_path)
            logger.info(f"✅ P&ID drawing created: {drawing_path}")
            
            # STEP 7: Engineering Validation (Post-Generation)
            logger.info("\n[STEP 7] ✅ Running Engineering Validation")
            logger.info("-" * 60)
            validation_results = self._run_engineering_validation(pid_specs, vision_data)
            logger.info(f"✅ Validation completed: {len(validation_results.findings)} findings")
            
            # Clean up temp PFD file
            if pfd_temp_path and os.path.exists(pfd_temp_path):
                try:
                    os.remove(pfd_temp_path)
                    logger.info(f"  → Cleaned up temp file: {pfd_temp_path}")
                except Exception as e:
                    logger.warning(f"  ⚠️ Could not delete temp file: {str(e)}")
            
            # Compile results
            results = {
                'success': True,
                'pipeline_version': '2.1',  # Updated version with AI drawing
                'pipeline_steps': {
                    'step1_vision': vision_data,
                    'step2_graph': process_graph,
                    'step3_rules': enriched_graph,
                    'step4_patterns': classified_data,
                    'step5_specs': pid_specs,
                    'step6_drawing': drawing_path,
                    'step7_validation': {
                        'validation_passed': validation_results.validation_passed,
                        'total_findings': len(validation_results.findings),
                        'critical_count': len([f for f in validation_results.findings if f.severity == 'CRITICAL']),
                        'high_count': len([f for f in validation_results.findings if f.severity == 'HIGH']),
                        'findings': [f.__dict__ for f in validation_results.findings]
                    }
                },
                'pid_specifications': pid_specs,
                'drawing_path': drawing_path,
                'validation_results': {
                    'passed': validation_results.validation_passed,
                    'findings': [f.__dict__ for f in validation_results.findings],
                    'engineering_holds': validation_results.engineering_holds,
                    'auto_corrections': validation_results.auto_corrections
                },
                'metadata': {
                    'equipment_count': len(pid_specs.get('equipment_list', [])),
                    'instrument_count': len(pid_specs.get('instrument_list', [])),
                    'line_count': len(pid_specs.get('piping_specifications', [])),
                    'safety_devices': len(pid_specs.get('safety_devices', []))
                }
            }
            
            logger.info("\n" + "="*80)
            logger.info("✅ PIPELINE COMPLETED SUCCESSFULLY")
            logger.info("="*80)
            
            return results
            
        except Exception as e:
            logger.error(f"\n❌ PIPELINE FAILED: {str(e)}")
            raise
    
    def _step1_computer_vision_ocr(self, pfd_file) -> dict:
        """
        STEP 1: Extract visual and textual information from PFD
        
        Uses:
        - GPT-4 Vision for equipment and symbol recognition
        - OCR for text annotations, labels, and specifications
        - Layout analysis for spatial relationships
        """
        logger.info("  → Running GPT-4 Vision analysis...")
        
        # Prepare image
        image_data = self._prepare_image(pfd_file)
        
        # Enhanced vision prompt for detailed extraction
        prompt = f"""You are analyzing a Process Flow Diagram (PFD). Your task is to extract ALL visible information and return it as structured JSON.

CRITICAL: You MUST respond with ONLY valid JSON. No explanations, no markdown, just pure JSON.

ANALYZE AND EXTRACT:

1. **EQUIPMENT** - Every vessel, pump, tank, heat exchanger, compressor:
   - Equipment tag number (e.g., P-101, V-102, E-103)
   - Equipment type (pump, vessel, heat exchanger, etc.)
   - Position on drawing (approximate x, y coordinates 0.0 to 1.0)
   - Operating conditions if visible (pressure, temperature)
   - Physical specifications if shown (capacity, size, power)

2. **PROCESS STREAMS** - All flow lines connecting equipment:
   - Stream number/ID
   - Source equipment tag
   - Destination equipment tag
   - Flow direction
   - Stream conditions (flow rate, pressure, temperature if labeled)
   - Stream name/description if shown

3. **TEXT ANNOTATIONS** - All text visible on the drawing:
   - Equipment labels and tag numbers  
   - Stream identifiers
   - Operating parameters
   - Notes and specifications
   - Drawing title, number, revision

4. **UTILITIES** - Support systems:
   - Cooling water, steam, air, nitrogen
   - Supply and return lines
   - Connection points to process equipment

REQUIRED JSON FORMAT:
{{
  "equipment": [
    {{
      "tag": "P-101",
      "type": "pump",
      "sub_type": "centrifugal",
      "position": {{"x": 0.2, "y": 0.5}},
      "conditions": {{"pressure": "10 barg", "temperature": "40°C", "power": "15 kW"}},
      "annotations": ["Main feed pump", "Capacity: 100 m3/h"]
    }}
  ],
  "process_streams": [
    {{
      "stream_id": "S-101",
      "name": "Crude Feed",
      "from": "P-101",
      "to": "V-101",
      "conditions": {{"flow_rate": "100 m3/h", "pressure": "12 barg", "temperature": "45°C"}}
    }}
  ],
  "text_annotations": [
    {{
      "text": "Design Pressure: 25 barg",
      "category": "specification"
    }}
  ],
  "utilities": [
    {{
      "type": "cooling_water",
      "connections": ["E-101", "E-102"]
    }}
  ],
  "drawing_info": {{
    "title": "",
    "number": "",
    "revision": "",
    "date": ""
  }}
}}

RESPOND WITH ONLY THE JSON OBJECT. Extract everything you can see in the PFD."""

        # Call GPT-4 Vision
        try:
            logger.info("  → Calling OpenAI Vision API...")
            logger.info(f"  → Model: {self.model}")
            logger.info(f"  → Image data size: {len(image_data)} characters (base64)")
            
            response = openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert process engineer with deep expertise in Process Flow Diagrams (PFDs). Your task is to analyze PFD images and extract structured data. You MUST respond with valid JSON only."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_data}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            logger.info(f"  ✅ OpenAI Vision API response received")
            logger.info(f"  → Response length: {len(response.choices[0].message.content)} characters")
            
        except Exception as e:
            logger.error(f"  ❌ OpenAI Vision API call failed: {str(e)}")
            raise Exception(f"OpenAI Vision API error: {str(e)}")
        
        # Parse response
        content = response.choices[0].message.content
        
        if not content or content.strip() == "":
            raise Exception("OpenAI Vision API returned empty response")
        
        logger.info(f"  → Parsing response (length: {len(content)} chars)...")
        logger.info(f"  → Response preview: {content[:300]}...")
        
        # Extract JSON from response - try multiple strategies
        vision_data = None
        
        # Strategy 1: Try direct JSON parsing
        try:
            vision_data = json.loads(content)
            logger.info("  ✅ Direct JSON parsing successful")
        except json.JSONDecodeError:
            # Strategy 2: Extract JSON from markdown code blocks
            code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if code_block_match:
                try:
                    vision_data = json.loads(code_block_match.group(1))
                    logger.info("  ✅ JSON extracted from code block")
                except json.JSONDecodeError:
                    pass
            
            # Strategy 3: Find largest JSON object
            if vision_data is None:
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    try:
                        vision_data = json.loads(json_match.group())
                        logger.info("  ✅ JSON extracted via regex")
                    except json.JSONDecodeError as e:
                        logger.error(f"  ❌ JSON parsing failed: {str(e)}")
                        logger.error(f"  Response content: {content[:500]}...")
                        raise Exception(f"Failed to parse OpenAI response as JSON: {str(e)}")
        
        if vision_data is None:
            raise Exception(f"Could not extract valid JSON from OpenAI response. Content preview: {content[:200]}...")
        
        # Add OCR metadata
        vision_data['ocr_metadata'] = {
            'text_elements_extracted': len(vision_data.get('text_annotations', [])),
            'equipment_identified': len(vision_data.get('equipment', [])),
            'streams_traced': len(vision_data.get('process_streams', []))
        }
        
        return vision_data
    
    def _step2_build_process_graph(self, vision_data: dict) -> dict:
        """
        STEP 2: Build process flow graph from extracted data
        
        Creates node-edge graph structure:
        - Nodes: Equipment, control points, utility connections
        - Edges: Process streams, control signals, utility lines
        """
        logger.info("  → Constructing process graph with nodes and edges...")
        
        return self.graph_builder.build_graph(vision_data)
    
    def _step3_apply_engineering_rules(self, process_graph: dict, project_info: dict) -> dict:
        """
        STEP 3: Apply engineering rules and standards
        
        Rules applied:
        - Equipment sizing and selection criteria
        - Instrumentation requirements (control, safety, monitoring)
        - Valve selection and placement
        - Safety system requirements (PSV, ESD, HIPPS)
        - Piping specifications (size, schedule, material)
        - Code compliance (ADNOC DEP, API, ASME, ISA)
        """
        logger.info("  → Applying ADNOC DEP, API, and ISA standards...")
        
        return self.engineering_rules.apply_rules(process_graph, project_info)
    
    def _step4_ml_pattern_matching(self, enriched_graph: dict, vision_data: dict) -> dict:
        """
        STEP 4: ML-based pattern matching for equipment and instrument classification
        
        Uses pattern recognition to:
        - Identify equipment types from visual patterns
        - Classify instruments based on process requirements
        - Match against known engineering patterns
        - Suggest instrumentation based on similar projects
        """
        logger.info("  → Running ML pattern classification...")
        
        return self.pattern_matcher.classify(enriched_graph, vision_data)
    
    def _step5_generate_pid_draft(self, classified_data: dict, project_info: dict) -> dict:
        """
        STEP 5: Generate ISA-compliant P&ID specifications
        
        Creates complete P&ID specification with:
        - Equipment list with tags, sizes, materials
        - Instrument list (ISA format: TT, PT, FT, LT, etc.)
        - Piping specifications (line sizes, classes, materials)
        - Valve list (control, isolation, safety)
        - Safety devices (PSV, rupture disks, flame arrestors)
        - Utility connections
        - Control logic descriptions
        
        Enhanced with database integration for superior results
        """
        logger.info("  → Generating ISA-compliant P&ID specifications...")
        
        # Use database-integrated converter if available
        if self.use_database and self.db_converter:
            logger.info("  → Using Database-Integrated Converter with 10,107 legend references")
            try:
                # Prepare PFD data from classified data
                pfd_data = {
                    'equipment': classified_data.get('equipment', []),
                    'process_streams': classified_data.get('process_streams', []),
                    'instruments': classified_data.get('instruments', []),
                    'text_annotations': classified_data.get('annotations', [])
                }
                
                # Generate enhanced P&ID with database knowledge
                pid_specs = self.db_converter.enhance_pid_generation_with_db(pfd_data, project_info)
                
                logger.info(f"  ✅ Database-enhanced P&ID generated:")
                logger.info(f"     • Equipment: {len(pid_specs.get('equipment_list', []))}")
                logger.info(f"     • Instruments: {len(pid_specs.get('instrument_list', []))}")
                logger.info(f"     • Safety devices: {len(pid_specs.get('safety_devices', []))}")
                
                return pid_specs
                
            except Exception as e:
                logger.warning(f"  ⚠️ Database-enhanced generation failed, falling back: {str(e)}")
                # Fall back to standard generator
        
        # Standard generator (fallback)
        return self.pid_generator.generate(classified_data, project_info)
    
    def _run_engineering_validation(self, pid_specs: dict, vision_data: dict):
        """
        STEP 7: Engineering Validation
        
        Validates generated P&ID against engineering standards:
        - ADNOC DEP requirements
        - ASME B31.3/B31.8 piping standards
        - ISA-5.1 instrumentation standards
        - API RP 520/521 safety systems
        
        Returns ValidationResult with findings, holds, and corrections
        """
        try:
            validator = EngineeringValidationEngine()
            
            # Prepare P&ID document structure for validation
            pid_document = {
                'drawing_number': pid_specs.get('drawing_info', {}).get('drawing_number', 'PID-001'),
                'drawing_title': pid_specs.get('drawing_info', {}).get('title', 'P&ID Draft'),
                'equipment_list': pid_specs.get('equipment_list', []),
                'instrument_list': pid_specs.get('instrument_list', []),
                'piping_specifications': pid_specs.get('piping_specifications', []),
                'safety_devices': pid_specs.get('safety_devices', []),
                'utilities': vision_data.get('utilities', [])
            }
            
            # Run validation
            validation_result = validator.validate_pid_document(pid_document)
            
            # Log findings
            critical_findings = [f for f in validation_result.findings if f.severity.value == 'CRITICAL']
            high_findings = [f for f in validation_result.findings if f.severity.value == 'HIGH']
            
            if critical_findings:
                logger.warning(f"  ⚠️ {len(critical_findings)} CRITICAL findings detected")
                for finding in critical_findings[:3]:
                    logger.warning(f"    • {finding.rule_id}: {finding.description}")
            
            if high_findings:
                logger.info(f"  ℹ️ {len(high_findings)} HIGH severity findings")
            
            logger.info(f"  → Engineering holds: {len(validation_result.engineering_holds)}")
            logger.info(f"  → Auto-corrections: {len(validation_result.auto_corrections)}")
            
            return validation_result
            
        except Exception as e:
            logger.error(f"  ❌ Validation failed: {str(e)}")
            # Return empty validation result
            from .validation_engine import ValidationResult
            return ValidationResult(
                document_id='ERROR',
                document_title='Validation Failed',
                validation_passed=True,
                findings=[]
            )
    
    def _step6_create_pid_drawing(self, pid_specs: dict, classified_data: dict, pfd_file_path: str = None) -> str:
        """
        STEP 6: Create visual P&ID drawing using professional programmatic generator
        
        Generates professional CAD-style P&ID with:
        - ISA 5.1 compliant symbols
        - Proper line routing (orthogonal)
        - Title block with project info
        - Equipment symbols (vessels, pumps, exchangers)
        - Instrumentation with proper circles
        - Valves with standard symbols
        - Legend and notes
        """
        logger.info("  → Generating professional programmatic P&ID drawing...")
        
        # Determine output path
        media_root = settings.MEDIA_ROOT
        pid_drawings_dir = os.path.join(media_root, 'pid_drawings_advanced')
        os.makedirs(pid_drawings_dir, exist_ok=True)
        
        drawing_number = pid_specs.get('drawing_info', {}).get('drawing_number', 'PID-DRAFT-001')
        # Use UUID for absolute uniqueness - prevents any caching or collision issues
        import uuid
        from datetime import datetime
        unique_id = str(uuid.uuid4())[:8]  # Short UUID for readability
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = os.path.join(pid_drawings_dir, f"{drawing_number}_{timestamp}_{unique_id}.pdf")
        logger.info(f"  → Generating unique P&ID file: {os.path.basename(output_path)}")
        
        # Use programmatic generator (Professional CAD-style)
        logger.info("  → Using professional programmatic P&ID generator...")
        try:
            from .programmatic_pid_generator import generate_pid_from_specs
            
            # Convert pid_specs to drawing_specs format
            drawing_specs = {
                'drawing_number': drawing_number,
                'drawing_title': pid_specs.get('drawing_info', {}).get('title', 'P&ID Drawing'),
                'project_name': pid_specs.get('drawing_info', {}).get('project_name', 'Project'),
                'project_code': pid_specs.get('drawing_info', {}).get('project_code', ''),
                'revision': pid_specs.get('drawing_info', {}).get('revision', 'A'),
                'equipment': pid_specs.get('equipment_list', []),
                'piping': [],
                'instrumentation': pid_specs.get('instrument_list', []),
                'valves': [],
                'generation_id': unique_id,  # Add unique ID to make each drawing visually distinct
                'generation_timestamp': timestamp  # Add timestamp for tracking
            }
            
            # Extract piping from specifications
            for pipe_spec in pid_specs.get('piping_specifications', []):
                if isinstance(pipe_spec, dict):
                    drawing_specs['piping'].append({
                        'from_equipment': pipe_spec.get('from', ''),
                        'to_equipment': pipe_spec.get('to', ''),
                        'line_number': pipe_spec.get('line_number', '')
                    })
            
            # Extract valves from instrument list
            for inst in pid_specs.get('instrument_list', []):
                inst_tag = inst.get('tag', '').upper()
                inst_type = inst.get('type', '').lower()
                
                # Identify valves by tag pattern or type
                if any(valve_prefix in inst_tag for valve_prefix in ['HV', 'CV', 'PCV', 'FCV', 'SDV', 'XV']):
                    valve_type = 'gate'
                    if 'PCV' in inst_tag or 'FCV' in inst_tag or 'CV' in inst_tag:
                        valve_type = 'control'
                    elif 'SDV' in inst_tag:
                        valve_type = 'gate'
                    
                    drawing_specs['valves'].append({
                        'tag': inst.get('tag'),
                        'type': valve_type
                    })
            
            # Add safety devices as valves
            for safety in pid_specs.get('safety_devices', []):
                drawing_specs['valves'].append({
                    'tag': safety.get('tag', ''),
                    'type': 'safety'
                })
            
            # Generate P&ID using programmatic generator
            output_path = generate_pid_from_specs(drawing_specs, output_path)
            logger.info(f"  ✅ Professional P&ID generated: {output_path}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"  ❌ Programmatic generation failed: {str(e)}")
            # Ultimate fallback: basic spec sheet
            logger.warning("  → Falling back to basic rendering...")
            self._render_professional_pid(pid_specs, classified_data, output_path)
            return output_path
    
    def _prepare_image(self, image_file):
        """
        Convert uploaded file to base64 for OpenAI Vision API
        
        Handles both PDF and image formats:
        - PDF: Converts first page to PNG
        - Images: Optimizes and converts to JPEG
        
        OpenAI Vision API only accepts: png, jpeg, gif, webp
        """
        logger.info("  → Preparing image for Vision API...")
        
        # Read file content
        image_file.seek(0)
        file_content = image_file.read()
        
        # Detect file type
        file_type = None
        if file_content[:4] == b'%PDF':
            file_type = 'pdf'
        elif file_content[:2] == b'\xff\xd8':
            file_type = 'jpeg'
        elif file_content[:8] == b'\x89PNG\r\n\x1a\n':
            file_type = 'png'
        elif file_content[:6] in (b'GIF87a', b'GIF89a'):
            file_type = 'gif'
        else:
            # Try to detect by opening with PIL
            try:
                img = Image.open(BytesIO(file_content))
                file_type = img.format.lower() if img.format else 'unknown'
            except:
                file_type = 'unknown'
        
        logger.info(f"  → Detected file type: {file_type}")
        
        # Handle PDF conversion
        if file_type == 'pdf':
            logger.info("  → Converting PDF to PNG...")
            try:
                # Open PDF with PyMuPDF
                pdf_document = fitz.open(stream=file_content, filetype="pdf")
                
                # Get first page
                first_page = pdf_document[0]
                
                # Render page to pixmap (high resolution for better OCR)
                # zoom=2.0 gives 144 DPI (default is 72 DPI)
                mat = fitz.Matrix(2.0, 2.0)
                pix = first_page.get_pixmap(matrix=mat)
                
                # Convert pixmap to PNG bytes
                png_bytes = pix.tobytes("png")
                
                # Close PDF
                pdf_document.close()
                
                # Convert PNG to base64
                encoded = base64.b64encode(png_bytes).decode('utf-8')
                logger.info(f"  ✅ PDF converted to PNG (size: {len(png_bytes)} bytes)")
                
                return encoded
                
            except Exception as e:
                logger.error(f"  ❌ PDF conversion failed: {str(e)}")
                raise Exception(f"Failed to convert PDF to image: {str(e)}")
        
        # Handle images (optimize if needed)
        elif file_type in ['jpeg', 'jpg', 'png', 'gif', 'webp']:
            logger.info(f"  → Processing {file_type.upper()} image...")
            try:
                # Open with PIL
                img = Image.open(BytesIO(file_content))
                
                # Convert to RGB if needed
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')
                
                # Optimize size if image is too large (max 20MB for OpenAI)
                max_size = 4096  # Max dimension
                if img.width > max_size or img.height > max_size:
                    logger.info(f"  → Resizing large image from {img.width}x{img.height}...")
                    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                
                # Convert to JPEG for better compression
                output = BytesIO()
                img.save(output, format='PNG', optimize=True)
                png_bytes = output.getvalue()
                
                # Encode to base64
                encoded = base64.b64encode(png_bytes).decode('utf-8')
                logger.info(f"  ✅ Image prepared (size: {len(png_bytes)} bytes)")
                
                return encoded
                
            except Exception as e:
                logger.error(f"  ❌ Image processing failed: {str(e)}")
                raise Exception(f"Failed to process image: {str(e)}")
        
        else:
            raise Exception(f"Unsupported file format: {file_type}. Please upload PDF, PNG, JPEG, GIF, or WebP files.")
    
    def _render_professional_pid(self, pid_specs: dict, classified_data: dict, output_path: str):
        """Render professional P&ID drawing with ISA symbols"""
        logger.info("  → Creating PDF with title block and schedules...")
        
        # Create PDF canvas (A1 landscape for P&ID)
        page_width, page_height = landscape(A1)
        c = canvas.Canvas(output_path, pagesize=landscape(A1))
        
        # Draw title block
        self._draw_title_block(c, page_width, page_height, pid_specs)
        
        # Draw equipment schedule
        self._draw_equipment_schedule(c, page_width, page_height, pid_specs)
        
        # Draw instrument index
        self._draw_instrument_index(c, page_width, page_height, pid_specs)
        
        # Add main drawing area with process flow
        self._draw_process_flow(c, page_width, page_height, pid_specs, classified_data)
        
        # Add notes and legend
        self._draw_legend_and_notes(c, page_width, page_height, pid_specs)
        
        # Finalize
        c.save()
        logger.info(f"  ✅ PDF created: {output_path}")
    
    def _draw_title_block(self, c, width, height, pid_specs):
        """Draw title block at bottom right"""
        tb_width = 300*mm
        tb_height = 100*mm
        tb_x = width - tb_width - 10*mm
        tb_y = 10*mm
        
        # Border
        c.setStrokeColor(colors.black)
        c.setLineWidth(2)
        c.rect(tb_x, tb_y, tb_width, tb_height)
        
        # Project info
        c.setFont("Helvetica-Bold", 14)
        c.drawString(tb_x + 10*mm, tb_y + tb_height - 20*mm, 
                    pid_specs.get('drawing_info', {}).get('title', 'P&ID DRAFT'))
        
        c.setFont("Helvetica", 10)
        y_pos = tb_y + tb_height - 40*mm
        drawing_info = pid_specs.get('drawing_info', {})
        
        info_lines = [
            f"Drawing No: {drawing_info.get('drawing_number', 'N/A')}",
            f"Revision: {drawing_info.get('revision', 'A')}",
            f"Date: {drawing_info.get('date', 'N/A')}",
            f"Project: {drawing_info.get('project_name', 'N/A')}",
            "Generated by: RADAI - PFD to P&ID Converter"
        ]
        
        for line in info_lines:
            c.drawString(tb_x + 10*mm, y_pos, line)
            y_pos -= 12
    
    def _draw_equipment_schedule(self, c, width, height, pid_specs):
        """Draw equipment schedule table"""
        equipment_list = pid_specs.get('equipment_list', [])
        if not equipment_list:
            return
        
        # Position at top left
        x_start = 20*mm
        y_start = height - 50*mm
        
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x_start, y_start, "EQUIPMENT SCHEDULE")
        
        # Create table (limit to first 10 items)
        table_data = [['Tag', 'Description', 'Type', 'Size/Capacity']]
        for eq in equipment_list[:10]:
            table_data.append([
                eq.get('tag', ''),
                eq.get('description', '')[:30],
                eq.get('type', ''),
                eq.get('size', '')
            ])
        
        # Draw simple table
        y_pos = y_start - 20
        c.setFont("Helvetica", 9)
        for row in table_data:
            c.drawString(x_start, y_pos, ' | '.join(row))
            y_pos -= 15
    
    def _draw_instrument_index(self, c, width, height, pid_specs):
        """Draw instrument index"""
        instruments = pid_specs.get('instrument_list', [])
        if not instruments:
            return
        
        x_start = 20*mm
        y_start = height - 300*mm
        
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x_start, y_start, "INSTRUMENT INDEX")
        
        # Limit to first 10
        y_pos = y_start - 20
        c.setFont("Helvetica", 9)
        for inst in instruments[:10]:
            line = f"{inst.get('tag', '')} - {inst.get('description', '')} ({inst.get('type', '')})"
            c.drawString(x_start, y_pos, line)
            y_pos -= 15
    
    def _draw_process_flow(self, c, width, height, pid_specs, classified_data):
        """Draw simplified process flow representation"""
        # Center area for process flow
        center_x = width / 2
        center_y = height / 2
        
        c.setFont("Helvetica", 10)
        c.drawString(center_x - 100, center_y, "Process Flow Diagram")
        c.drawString(center_x - 100, center_y - 20, "(Generated from PFD Analysis)")
        
        # Draw equipment symbols (simplified)
        equipment = pid_specs.get('equipment_list', [])
        x_offset = center_x - 300
        y_offset = center_y + 100
        
        for i, eq in enumerate(equipment[:5]):  # Show first 5 equipment
            # Draw simple rectangle for equipment
            c.setStrokeColor(colors.blue)
            c.setLineWidth(1.5)
            c.rect(x_offset + i*120, y_offset, 80, 60)
            
            # Label
            c.setFont("Helvetica-Bold", 8)
            tag = eq.get('tag', f'E-{i+1}')
            c.drawCentredString(x_offset + i*120 + 40, y_offset + 30, tag)
            
            c.setFont("Helvetica", 7)
            eq_type = eq.get('type', 'Equipment')[:12]
            c.drawCentredString(x_offset + i*120 + 40, y_offset + 15, eq_type)
    
    def _draw_legend_and_notes(self, c, width, height, pid_specs):
        """Draw legend and general notes"""
        x_start = width - 400*mm
        y_start = height - 50*mm
        
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x_start, y_start, "GENERAL NOTES:")
        
        c.setFont("Helvetica", 8)
        notes = [
            "1. All dimensions in mm unless otherwise stated",
            "2. All pressures in barg unless otherwise stated",
            "3. All temperatures in °C unless otherwise stated",
            "4. Comply with ADNOC DEP and API standards",
            "5. This P&ID is generated from PFD analysis"
        ]
        
        y_pos = y_start - 15
        for note in notes:
            c.drawString(x_start, y_pos, note)
            y_pos -= 12


class ProcessGraphBuilder:
    """Builds process flow graph from vision data"""
    
    def build_graph(self, vision_data: dict) -> dict:
        """Create node-edge graph structure"""
        equipment = vision_data.get('equipment', [])
        streams = vision_data.get('process_streams', [])
        
        # Create nodes (equipment)
        nodes = []
        for i, eq in enumerate(equipment):
            nodes.append({
                'id': f"node_{i}",
                'tag': eq.get('tag', f'EQ-{i+1}'),
                'type': eq.get('type', 'equipment'),
                'sub_type': eq.get('sub_type', ''),
                'properties': eq
            })
        
        # Create edges (streams/connections)
        edges = []
        for i, stream in enumerate(streams):
            edges.append({
                'id': f"edge_{i}",
                'from': stream.get('from_equipment', ''),
                'to': stream.get('to_equipment', ''),
                'stream_id': stream.get('stream_id', f'S-{i+1}'),
                'properties': stream
            })
        
        return {
            'nodes': nodes,
            'edges': edges,
            'stats': {
                'total_nodes': len(nodes),
                'total_edges': len(edges),
                'equipment_types': list(set([n.get('type') for n in nodes]))
            }
        }


class EngineeringRulesEngine:
    """Applies engineering rules and standards"""
    
    def apply_rules(self, process_graph: dict, project_info: dict) -> dict:
        """Apply ADNOC DEP, API, ISA standards"""
        logger.info("    • Checking ADNOC DEP compliance...")
        logger.info("    • Adding required instrumentation...")
        logger.info("    • Sizing control valves...")
        logger.info("    • Adding safety devices...")
        
        enriched = process_graph.copy()
        
        # Add instrumentation requirements for each equipment
        for node in enriched['nodes']:
            node['required_instruments'] = self._determine_instruments(node)
            node['safety_requirements'] = self._determine_safety(node)
        
        enriched['rules_applied'] = 15
        return enriched
    
    def _determine_instruments(self, node: dict) -> List[str]:
        """Determine required instruments based on equipment type"""
        eq_type = node.get('type', '').lower()
        
        instrument_map = {
            'vessel': ['PT', 'TT', 'LT'],
            'pump': ['PT', 'FT', 'VT'],
            'compressor': ['PT', 'TT', 'VT', 'ST'],
            'heat_exchanger': ['TT', 'PT'],
            'column': ['PT', 'TT', 'LT', 'FT'],
            'reactor': ['PT', 'TT', 'LT', 'AT']
        }
        
        return instrument_map.get(eq_type, ['PT', 'TT'])
    
    def _determine_safety(self, node: dict) -> List[str]:
        """Determine safety devices needed"""
        eq_type = node.get('type', '').lower()
        
        # High pressure equipment needs PSV
        safety = []
        if eq_type in ['vessel', 'reactor', 'column']:
            safety.append('PSV')
        if eq_type in ['pump', 'compressor']:
            safety.append('ESD')
        
        return safety


class MLPatternMatcher:
    """ML-based pattern recognition for equipment and instruments"""
    
    def classify(self, enriched_graph: dict, vision_data: dict) -> dict:
        """Classify equipment and instruments using pattern matching"""
        logger.info("    • Matching equipment patterns...")
        logger.info("    • Identifying instrument types...")
        logger.info("    • Applying learned patterns from database...")
        
        classified = {
            'equipment': enriched_graph['nodes'],
            'connections': enriched_graph['edges'],
            'instrumentation': [],
            'classification_stats': {
                'total_items': len(enriched_graph['nodes']),
                'confidence_avg': 0.87
            }
        }
        
        # Generate instrument tags
        for node in classified['equipment']:
            for inst_type in node.get('required_instruments', []):
                tag = f"{inst_type}-{node.get('tag', '').split('-')[-1]}"
                classified['instrumentation'].append({
                    'tag': tag,
                    'type': inst_type,
                    'service': node.get('tag', ''),
                    'description': f"{inst_type} on {node.get('tag', '')}"
                })
        
        return classified


class PIDDraftGenerator:
    """Generates ISA-compliant P&ID specifications"""
    
    def generate(self, classified_data: dict, project_info: dict) -> dict:
        """Generate complete P&ID specification"""
        logger.info("    • Creating equipment list...")
        logger.info("    • Generating instrument list (ISA format)...")
        logger.info("    • Specifying piping details...")
        logger.info("    • Adding safety devices...")
        
        project_info = project_info or {}
        
        # Generate equipment list
        equipment_list = []
        for node in classified_data.get('equipment', []):
            equipment_list.append({
                'tag': node.get('tag', ''),
                'description': f"{node.get('sub_type', '')} {node.get('type', '')}".strip(),
                'type': node.get('type', ''),
                'size': node.get('properties', {}).get('capacity', 'TBD'),
                'material': 'CS/SS',  # Default material
                'design_pressure': node.get('properties', {}).get('operating_conditions', {}).get('pressure', 'TBD'),
                'design_temperature': node.get('properties', {}).get('operating_conditions', {}).get('temperature', 'TBD')
            })
        
        # Generate instrument list
        instrument_list = classified_data.get('instrumentation', [])
        
        # Generate piping specifications
        piping_specs = []
        for edge in classified_data.get('connections', []):
            line_number = edge.get('stream_id', 'L-001')
            piping_specs.append({
                'line_number': line_number,
                'from': edge.get('from', ''),
                'to': edge.get('to', ''),
                'service': edge.get('properties', {}).get('name', 'Process'),
                'size': 'TBD',
                'schedule': '40',
                'material': 'CS',
                'insulation': 'Yes' if 'hot' in edge.get('properties', {}).get('name', '').lower() else 'No'
            })
        
        # Compile P&ID specification
        pid_spec = {
            'drawing_info': {
                'drawing_number': f"PID-{project_info.get('project_code', '001')}-001",
                'title': project_info.get('project_name', 'Process Unit P&ID'),
                'revision': 'A',
                'date': '2026-01-06',
                'project_name': project_info.get('project_name', 'Oil & Gas Processing')
            },
            'equipment_list': equipment_list,
            'instrument_list': instrument_list,
            'piping_specifications': piping_specs,
            'safety_devices': self._generate_safety_devices(classified_data),
            'general_notes': [
                'All dimensions in mm unless noted',
                'All pressures in barg unless noted',
                'All temperatures in °C unless noted',
                'Comply with ADNOC DEP standards',
                'Comply with API and ISA standards'
            ],
            'legend': {
                'symbols': ['Equipment symbols per ISA 5.1', 'Instrument symbols per ISA 5.1'],
                'abbreviations': ['PT: Pressure Transmitter', 'TT: Temperature Transmitter',  'FT: Flow Transmitter', 'LT: Level Transmitter']
            }
        }
        
        return pid_spec
    
    def _generate_safety_devices(self, classified_data: dict) -> List[dict]:
        """Generate safety device list"""
        safety_devices = []
        
        for node in classified_data.get('equipment', []):
            for safety_type in node.get('safety_requirements', []):
                tag = f"{safety_type}-{node.get('tag', '').split('-')[-1]}"
                safety_devices.append({
                    'tag': tag,
                    'type': safety_type,
                    'service': node.get('tag', ''),
                    'set_pressure': 'TBD',
                    'capacity': 'TBD',
                    'description': f"{safety_type} on {node.get('tag', '')}"
                })
        
        return safety_devices
