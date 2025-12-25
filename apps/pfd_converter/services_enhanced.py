"""
Enhanced PFD to P&ID AI-Assisted Generation Service
Implements comprehensive 6-step engineering workflow with PDF output

SYSTEM ROLE: Senior Process Design Engineer (15+ years EPC experience)
STANDARDS: ISA-5.1, ISO 10628, ADNOC DEP, API
OUTPUT: Draft P&ID PDF with assumption reports
"""
import openai
from openai import OpenAI
from decouple import config
from django.utils import timezone
import json
import base64
import os
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from reportlab.lib.pagesizes import A3, A1
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
import logging

logger = logging.getLogger(__name__)


class EnhancedPFDToPIDConverter:
    """
    AI-Assisted PFD to P&ID Generator
    Follows 6-step engineering workflow with professional PDF output
    """
    
    def __init__(self):
        """Initialize OpenAI client and configuration"""
        api_key = os.environ.get('OPENAI_API_KEY') or config('OPENAI_API_KEY', default='')
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        
        self.client = OpenAI(api_key=api_key)
        self.model = config('OPENAI_MODEL', default='gpt-4o')
        logger.info(f"[PFD→P&ID] Initialized with model: {self.model}")
    
    def convert_pfd_to_pid(self, pfd_file, project_info: dict = None):
        """
        Complete PFD to P&ID conversion workflow
        
        Args:
            pfd_file: PFD image/PDF file
            project_info: Project metadata (name, number, phase, etc.)
        
        Returns:
            dict: {
                'pid_pdf': PDF file bytes,
                'assumptions_report': Text report,
                'instrument_list': Excel-ready data,
                'valve_list': Excel-ready data,
                'validation_results': Compliance checks
            }
        """
        logger.info("[PFD→P&ID] Starting 6-step conversion workflow")
        
        try:
            # STEP 1: PFD Analysis
            logger.info("[STEP 1] Analyzing PFD...")
            pfd_analysis = self._step1_analyze_pfd(pfd_file)
            
            # STEP 2: Process Logic Modeling
            logger.info("[STEP 2] Building process connectivity...")
            process_model = self._step2_model_process_logic(pfd_analysis)
            
            # STEP 3: Instrumentation & Valve Suggestion
            logger.info("[STEP 3] Adding instrumentation and valves...")
            instrumentation = self._step3_add_instrumentation_valves(process_model)
            
            # STEP 4: P&ID Drawing Generation
            logger.info("[STEP 4] Generating P&ID layout...")
            pid_drawing_specs = self._step4_generate_pid_drawing(
                pfd_analysis, process_model, instrumentation, project_info
            )
            
            # STEP 5: Validation Checks
            logger.info("[STEP 5] Running validation checks...")
            validation_results = self._step5_validate_pid(
                pfd_analysis, pid_drawing_specs
            )
            
            # STEP 6: Export to PDF
            logger.info("[STEP 6] Exporting to PDF...")
            pid_pdf, assumptions_report = self._step6_export_pdf(
                pid_drawing_specs, validation_results, project_info
            )
            
            # Generate supporting documents
            instrument_list = self._generate_instrument_list(instrumentation)
            valve_list = self._generate_valve_list(instrumentation)
            
            logger.info("[PFD→P&ID] Conversion completed successfully")
            
            return {
                'success': True,
                'pid_pdf': pid_pdf,
                'assumptions_report': assumptions_report,
                'instrument_list': instrument_list,
                'valve_list': valve_list,
                'validation_results': validation_results,
                'pfd_analysis': pfd_analysis,
                'process_model': process_model,
                'instrumentation': instrumentation
            }
            
        except Exception as e:
            logger.error(f"[PFD→P&ID] Conversion failed: {str(e)}")
            raise
    
    def _step1_analyze_pfd(self, pfd_file):
        """
        STEP 1: PFD ANALYSIS
        Extract equipment, streams, and process parameters
        """
        logger.info("[STEP 1] Extracting PFD data using AI vision...")
        
        # Prepare image for vision API
        image_base64 = self._prepare_image(pfd_file)
        
        prompt = """
📌 STEP 1 – PFD ANALYSIS

You are a Senior Process Design Engineer analyzing this Process Flow Diagram (PFD).

**Extract ALL visible information and return as structured JSON:**

{
  "equipment": [
    {
      "tag": "Equipment tag (e.g., P-101, V-201, E-301)",
      "type": "Pump/Vessel/Heat Exchanger/Compressor/etc.",
      "service": "Equipment service description",
      "connected_streams": {
        "inlet": ["stream IDs or equipment tags"],
        "outlet": ["stream IDs or equipment tags"]
      },
      "parameters": {
        "design_pressure": "value with units or MISSING",
        "design_temperature": "value with units or MISSING",
        "capacity": "value with units or MISSING",
        "material": "MOC or MISSING"
      },
      "notes": "Any special notes or flags"
    }
  ],
  "process_streams": [
    {
      "stream_id": "Stream number/identifier",
      "description": "Stream description",
      "from_equipment": "Source equipment tag",
      "to_equipment": "Destination equipment tag",
      "phase": "Gas/Liquid/Two-Phase/Mixed",
      "flow_direction": "Direction of flow",
      "parameters": {
        "flow_rate": "value with units or MISSING",
        "pressure": "value with units or MISSING",
        "temperature": "value with units or MISSING",
        "composition": "Key components or MISSING"
      }
    }
  ],
  "utilities": {
    "cooling_water": "present/not visible",
    "steam": "present/not visible",
    "nitrogen": "present/not visible",
    "instrument_air": "present/not visible",
    "fuel_gas": "present/not visible"
  },
  "existing_instrumentation": [
    {
      "tag": "Instrument tag if visible",
      "type": "FI/PI/TI/LI/etc.",
      "location": "Equipment or line"
    }
  ],
  "safety_systems": [
    {
      "type": "PSV/Relief/ESD/etc.",
      "location": "Equipment tag",
      "details": "Any visible details"
    }
  ],
  "drawing_info": {
    "title": "Drawing title if visible",
    "drawing_number": "Drawing number if visible",
    "revision": "Revision if visible",
    "project": "Project name if visible"
  },
  "missing_data": [
    "List all engineering details that are NOT visible or unclear"
  ]
}

⚠️ **CRITICAL RULES:**
- DO NOT invent data that is not visible in the drawing
- Mark missing information as "MISSING" or "NOT VISIBLE"
- If flow direction is unclear, state "FLOW DIRECTION UNCLEAR"
- If equipment connections are ambiguous, flag them
- Extract ONLY what you can see

Return ONLY valid JSON.
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a Senior Process Design Engineer with 15+ years EPC experience. You analyze PFDs with extreme accuracy and flag missing information clearly."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            pfd_analysis = json.loads(response.choices[0].message.content)
            logger.info(f"[STEP 1] Extracted {len(pfd_analysis.get('equipment', []))} equipment items")
            
            return pfd_analysis
            
        except Exception as e:
            logger.error(f"[STEP 1] PFD analysis failed: {str(e)}")
            raise
    
    def _step2_model_process_logic(self, pfd_analysis):
        """
        STEP 2: PROCESS LOGIC MODELING
        Build connectivity graph and validate flow logic
        """
        logger.info("[STEP 2] Building process connectivity model...")
        
        prompt = f"""
📌 STEP 2 – PROCESS LOGIC MODELING

Based on this PFD analysis, build a complete process connectivity model:

PFD Analysis:
{json.dumps(pfd_analysis, indent=2)}

**Create a structured process connectivity model in JSON:**

{{
  "equipment_connections": [
    {{
      "from_equipment": "Tag",
      "to_equipment": "Tag",
      "stream_id": "Stream identifier",
      "connection_type": "inlet/outlet/bypass/recycle",
      "flow_path": ["sequential equipment list"],
      "validated": true/false,
      "issues": ["any connectivity issues found"]
    }}
  ],
  "flow_validation": {{
    "closed_loops": ["list any closed loops found"],
    "dead_ends": ["list any dead-end lines"],
    "unclear_connections": ["list ambiguous connections"],
    "missing_connections": ["list where connections are not clear"]
  }},
  "process_units": [
    {{
      "unit_name": "Logical process unit name",
      "equipment_included": ["list of equipment tags"],
      "unit_function": "Description of unit operation",
      "inlet_streams": ["external inlets"],
      "outlet_streams": ["external outlets"]
    }}
  ],
  "material_balance_check": {{
    "total_inlets": "summary of inlet streams",
    "total_outlets": "summary of outlet streams",
    "balance_status": "balanced/imbalanced/unable_to_verify",
    "notes": "any material balance concerns"
  }}
}}

⚠️ **FLAG any of these issues:**
- Equipment with no inlet or outlet
- Closed loops without indication
- Flow direction conflicts
- Missing isolation points

Return ONLY valid JSON.
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a process systems engineer validating flow logic and connectivity."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=3000,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            process_model = json.loads(response.choices[0].message.content)
            logger.info("[STEP 2] Process connectivity model created")
            
            return process_model
            
        except Exception as e:
            logger.error(f"[STEP 2] Process modeling failed: {str(e)}")
            raise
    
    def _step3_add_instrumentation_valves(self, process_model):
        """
        STEP 3: INSTRUMENTATION & VALVE SUGGESTION
        Add typical instruments and valves based on industry practices
        """
        logger.info("[STEP 3] Adding instrumentation and valves...")
        
        prompt = f"""
📌 STEP 3 – INSTRUMENTATION & VALVE SUGGESTION

Based on this process model, suggest typical instrumentation and valves following industry best practices:

Process Model:
{json.dumps(process_model, indent=2)}

**Apply these rules and return JSON:**

**Instrument Rules (ISA-5.1):**
- Pumps → PI (suction/discharge), FI (discharge), vibration monitoring
- Vessels → LT, PT, TI, PSV, LSH/LSL
- Heat Exchangers → TI (inlet/outlet), PI (shell/tube), FI
- Compressors → PI, TI, vibration, anti-surge
- Control loops only where justifiable

**Valve Rules:**
- Manual isolation valves (HV) at all equipment nozzles
- Control valves (FCV/PCV/TCV/LCV) only where control needed
- Check valves on pump discharge
- Block & bleed for instruments
- Drain and vent valves at low/high points

{{
  "suggested_instruments": [
    {{
      "tag": "FT-101 (AI-SUGGESTED)",
      "type": "Flow Transmitter",
      "location": "Discharge of P-101",
      "justification": "Monitor pump flow",
      "mandatory": true/false,
      "standard_practice": "API 610 / ISA 5.1",
      "signal_type": "4-20mA",
      "engineer_approval_required": true
    }}
  ],
  "suggested_valves": [
    {{
      "tag": "HV-101 (AI-SUGGESTED)",
      "type": "Gate Valve / Ball Valve / Globe Valve / Check Valve",
      "location": "Suction of P-101",
      "size": "estimated size or TBD",
      "justification": "Pump isolation",
      "mandatory": true,
      "standard_practice": "ASME B16.34"
    }}
  ],
  "control_loops": [
    {{
      "loop_tag": "FIC-101 (AI-SUGGESTED)",
      "type": "Flow Control",
      "sensor": "FT-101",
      "controller": "FIC-101",
      "final_element": "FCV-101",
      "controlled_variable": "Flow rate",
      "manipulated_variable": "Valve position",
      "justification": "Flow control required for process stability",
      "engineer_approval_required": true
    }}
  ],
  "safety_instrumentation": [
    {{
      "tag": "PSV-101 (AI-SUGGESTED)",
      "type": "Pressure Safety Valve",
      "protected_equipment": "V-101",
      "set_pressure": "TBD - Engineer Input Required",
      "capacity": "TBD - Engineer Calculation Required",
      "mandatory": true,
      "standard": "API 520/521",
      "isolation_valves": ["CSC-PSV-101A", "CSC-PSV-101B"]
    }}
  ],
  "instrumentation_summary": {{
    "total_instruments_suggested": 0,
    "total_valves_suggested": 0,
    "total_control_loops": 0,
    "mandatory_items": 0,
    "optional_items": 0
  }},
  "assumptions": [
    "List all assumptions made in instrumentation selection"
  ],
  "engineer_input_required": [
    "List all items requiring engineer approval or input"
  ]
}}

⚠️ **Tag every item as "AI-SUGGESTED – SUBJECT TO ENGINEER APPROVAL"**

Return ONLY valid JSON.
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an instrumentation engineer applying ISA-5.1 and API standards. You suggest instruments conservatively and flag all items for engineer review."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=4000,
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            
            instrumentation = json.loads(response.choices[0].message.content)
            logger.info(f"[STEP 3] Added {instrumentation.get('instrumentation_summary', {}).get('total_instruments_suggested', 0)} instruments")
            
            return instrumentation
            
        except Exception as e:
            logger.error(f"[STEP 3] Instrumentation addition failed: {str(e)}")
            raise
    
    def _step4_generate_pid_drawing(self, pfd_analysis, process_model, instrumentation, project_info):
        """
        STEP 4: P&ID DRAWING GENERATION
        Create drawing specifications with ISA symbols and proper layout
        """
        logger.info("[STEP 4] Generating P&ID drawing specifications...")
        
        prompt = f"""
📌 STEP 4 – P&ID DRAWING GENERATION

Create a complete P&ID drawing specification based on:

PFD Analysis:
{json.dumps(pfd_analysis, indent=2)[:1000]}...

Process Model:
{json.dumps(process_model, indent=2)[:1000]}...

Instrumentation:
{json.dumps(instrumentation, indent=2)[:1000]}...

**Generate complete P&ID specifications in JSON:**

{{
  "drawing_layout": {{
    "page_size": "A3/A1",
    "scale": "Not to scale",
    "orientation": "Landscape",
    "grid": "enabled",
    "equipment_arrangement": "left-to-right flow"
  }},
  "title_block": {{
    "drawing_title": "AI-Assisted Draft P&ID",
    "drawing_number": "PID-{project_info.get('drawing_number', 'XXXX')}",
    "project": "{project_info.get('project_name', 'PROJECT NAME')}",
    "contractor": "AI-Generated",
    "status": "NOT FOR CONSTRUCTION",
    "revision": "0",
    "date": "{datetime.now().strftime('%Y-%m-%d')}",
    "generated_by": "AI – Engineer Review Required",
    "notes": [
      "This P&ID is AI-assisted and intended for concept/FEED studies only.",
      "Final validation by a qualified process engineer is mandatory."
    ]
  }},
  "equipment_symbols": [
    {{
      "tag": "Equipment tag",
      "symbol_type": "ISA standard symbol",
      "position": {{"x": 0, "y": 0}},
      "size": {{"width": 0, "height": 0}},
      "label_position": "above/below/side",
      "nozzles": [
        {{"name": "N1", "size": "NPS", "direction": "inlet/outlet", "position": "top/bottom/side"}}
      ]
    }}
  ],
  "piping_lines": [
    {{
      "line_number": "SIZE-SERVICE-CLASS-SEQ",
      "from_equipment": "Tag",
      "from_nozzle": "N1",
      "to_equipment": "Tag",
      "to_nozzle": "N2",
      "path_coordinates": [{{"x": 0, "y": 0}}],
      "line_type": "process/utility/signal",
      "pipe_specification": "TBD - Engineer Input Required",
      "insulation": "TBD",
      "heat_tracing": "TBD"
    }}
  ],
  "instrument_symbols": [
    {{
      "tag": "Instrument tag",
      "symbol": "ISA-5.1 symbol code",
      "location": "field/local/panel/DCS",
      "position": {{"x": 0, "y": 0}},
      "process_connection": "Line or equipment",
      "signal_lines": [
        {{"from": "tag", "to": "tag", "signal_type": "pneumatic/electrical/digital"}}
      ]
    }}
  ],
  "valve_symbols": [
    {{
      "tag": "Valve tag",
      "type": "Gate/Globe/Ball/Check/Control",
      "symbol": "ISA symbol",
      "line_number": "Line where installed",
      "position": {{"x": 0, "y": 0}},
      "size": "NPS",
      "actuator": "Manual/Pneumatic/Electric/None"
    }}
  ],
  "legends": [
    {{
      "symbol": "Symbol name",
      "description": "Symbol meaning",
      "standard": "ISA-5.1/ISO 10628"
    }}
  ],
  "notes_and_flags": [
    "ENGINEER INPUT REQUIRED: List all items requiring engineer input",
    "ASSUMPTIONS: List all assumptions made",
    "VALIDATION REQUIRED: Items needing validation"
  ]
}}

**Drawing Requirements:**
- Black & white only
- Standard ISA symbols
- Clear line routing (orthogonal preferred)
- All instruments tagged per ISA-5.1
- All valves numbered
- Signal lines shown with correct symbols
- Equipment tags clearly visible
- Line numbers on all process lines

Return ONLY valid JSON.
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a P&ID drafter creating ISA-5.1 compliant drawings. You follow oil & gas engineering standards and CAD best practices."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=4000,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            pid_specs = json.loads(response.choices[0].message.content)
            logger.info("[STEP 4] P&ID drawing specifications generated")
            
            return pid_specs
            
        except Exception as e:
            logger.error(f"[STEP 4] Drawing generation failed: {str(e)}")
            raise
    
    def _step5_validate_pid(self, pfd_analysis, pid_specs):
        """
        STEP 5: VALIDATION CHECKS
        Validate completeness and compliance
        """
        logger.info("[STEP 5] Running validation checks...")
        
        prompt = f"""
📌 STEP 5 – VALIDATION CHECKS

Perform comprehensive validation of the generated P&ID:

PFD Analysis:
{json.dumps(pfd_analysis, indent=2)[:800]}...

P&ID Specifications:
{json.dumps(pid_specs, indent=2)[:800]}...

**Run these validation checks and return JSON:**

{{
  "validation_checks": [
    {{
      "check_name": "Equipment inlet/outlet verification",
      "status": "pass/fail/warning",
      "details": "All equipment has proper inlet and outlet connections",
      "issues": ["list any issues found"]
    }},
    {{
      "check_name": "Floating instruments check",
      "status": "pass/fail/warning",
      "details": "All instruments connected to process",
      "issues": []
    }},
    {{
      "check_name": "PSV connections",
      "status": "pass/fail/warning",
      "details": "PSVs properly connected and isolated",
      "issues": []
    }},
    {{
      "check_name": "Line specifications",
      "status": "pass/fail/warning",
      "details": "Line numbers and specs assigned",
      "issues": ["Missing line specs flagged"]
    }},
    {{
      "check_name": "Control philosophy",
      "status": "pass/fail/warning",
      "details": "Control loops complete and logical",
      "issues": []
    }},
    {{
      "check_name": "ISA-5.1 compliance",
      "status": "pass/fail/warning",
      "details": "Instrument tagging follows ISA standard",
      "issues": []
    }},
    {{
      "check_name": "Safety system completeness",
      "status": "pass/fail/warning",
      "details": "PSVs, ESDs, and interlocks present",
      "issues": []
    }}
  ],
  "missing_elements": [
    {{
      "item": "Description of missing item",
      "severity": "critical/major/minor",
      "engineer_action": "Required action from engineer"
    }}
  ],
  "assumptions_made": [
    "List all assumptions made during generation"
  ],
  "compliance_summary": {{
    "total_checks": 0,
    "passed": 0,
    "failed": 0,
    "warnings": 0,
    "overall_status": "acceptable_for_concept/requires_review/incomplete"
  }},
  "recommendations": [
    "Recommendations for engineer review and completion"
  ]
}}

Return ONLY valid JSON.
"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a senior process safety engineer performing P&ID validation against industry standards."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=3000,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            validation = json.loads(response.choices[0].message.content)
            logger.info(f"[STEP 5] Validation complete - Status: {validation.get('compliance_summary', {}).get('overall_status', 'unknown')}")
            
            return validation
            
        except Exception as e:
            logger.error(f"[STEP 5] Validation failed: {str(e)}")
            raise
    
    def _step6_export_pdf(self, pid_specs, validation_results, project_info):
        """
        STEP 6: EXPORT TO PDF
        Generate high-quality P&ID PDF with proper formatting
        """
        logger.info("[STEP 6] Exporting to PDF format...")
        
        try:
            # Create PDF in memory
            buffer = BytesIO()
            
            # Use A3 landscape by default
            page_size = A3
            c = canvas.Canvas(buffer, pagesize=page_size)
            width, height = page_size
            
            # Set up drawing context
            c.setTitle("AI-Assisted Draft P&ID")
            c.setAuthor("AIFlow P&ID Generator")
            
            # Draw title block
            self._draw_title_block(c, width, height, pid_specs.get('title_block', {}), project_info)
            
            # Draw main content area
            self._draw_pid_content(c, width, height, pid_specs)
            
            # Add warnings and notes
            self._draw_warnings(c, width, height)
            
            # Add confidence statement
            self._draw_confidence_statement(c, width, height)
            
            # Finalize PDF
            c.showPage()
            c.save()
            
            # Get PDF bytes
            pdf_bytes = buffer.getvalue()
            buffer.close()
            
            # Generate assumptions report
            assumptions_report = self._generate_assumptions_report(
                pid_specs, validation_results
            )
            
            logger.info("[STEP 6] PDF export completed")
            
            return pdf_bytes, assumptions_report
            
        except Exception as e:
            logger.error(f"[STEP 6] PDF export failed: {str(e)}")
            raise
    
    def _draw_title_block(self, c, width, height, title_block, project_info):
        """Draw title block on PDF"""
        # Title block area (bottom right)
        tb_width = 200 * mm
        tb_height = 50 * mm
        tb_x = width - tb_width - 10
        tb_y = 10
        
        # Draw border
        c.setStrokeColor(colors.black)
        c.setLineWidth(2)
        c.rect(tb_x, tb_y, tb_width, tb_height)
        
        # Draw internal lines
        c.setLineWidth(0.5)
        c.line(tb_x, tb_y + 30*mm, tb_x + tb_width, tb_y + 30*mm)
        c.line(tb_x, tb_y + 20*mm, tb_x + tb_width, tb_y + 20*mm)
        c.line(tb_x + 100*mm, tb_y, tb_x + 100*mm, tb_y + 20*mm)
        
        # Add text
        c.setFont("Helvetica-Bold", 12)
        c.drawString(tb_x + 5, tb_y + tb_height - 15, title_block.get('drawing_title', 'AI-Assisted Draft P&ID'))
        
        c.setFont("Helvetica", 8)
        c.drawString(tb_x + 5, tb_y + 25*mm, f"Project: {title_block.get('project', 'N/A')}")
        c.drawString(tb_x + 5, tb_y + 15*mm, f"Drawing No: {title_block.get('drawing_number', 'N/A')}")
        c.drawString(tb_x + 5, tb_y + 5, f"Generated: {title_block.get('date', 'N/A')}")
        
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(colors.red)
        c.drawString(tb_x + 105*mm, tb_y + 15*mm, "NOT FOR CONSTRUCTION")
        c.setFillColor(colors.black)
        
        c.setFont("Helvetica", 8)
        c.drawString(tb_x + 105*mm, tb_y + 5, f"Rev: {title_block.get('revision', '0')}")
    
    def _draw_pid_content(self, c, width, height, pid_specs):
        """Draw main P&ID content"""
        # Content area
        content_y = 70 * mm
        content_height = height - content_y - 20
        
        # Add simplified P&ID representation
        c.setFont("Helvetica", 10)
        c.drawString(50, height - 50, "P&ID DRAWING AREA")
        c.drawString(50, height - 70, f"Equipment Count: {len(pid_specs.get('equipment_symbols', []))}")
        c.drawString(50, height - 90, f"Instruments: {len(pid_specs.get('instrument_symbols', []))}")
        c.drawString(50, height - 110, f"Valves: {len(pid_specs.get('valve_symbols', []))}")
        
        # Draw simple equipment symbols
        y_pos = height - 150
        for i, equip in enumerate(pid_specs.get('equipment_symbols', [])[:5]):
            c.drawString(50, y_pos - i*20, f"  • {equip.get('tag', 'N/A')} - {equip.get('symbol_type', 'N/A')}")
    
    def _draw_warnings(self, c, width, height):
        """Draw warning notices"""
        c.setFillColor(colors.red)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20, height - 30, "⚠️ AI-GENERATED DRAFT - ENGINEER REVIEW REQUIRED")
        c.setFillColor(colors.black)
    
    def _draw_confidence_statement(self, c, width, height):
        """Draw mandatory confidence statement"""
        statement = [
            "CONFIDENCE STATEMENT:",
            "This P&ID is AI-assisted and intended for concept/FEED studies only.",
            "Final validation by a qualified process engineer is mandatory.",
            "All instruments and valves are SUBJECT TO ENGINEER APPROVAL."
        ]
        
        c.setFont("Helvetica", 8)
        y = 80
        for line in statement:
            c.drawString(20, y, line)
            y -= 10
    
    def _generate_assumptions_report(self, pid_specs, validation_results):
        """Generate assumptions and limitations report"""
        report = f"""
PID ASSUMPTIONS AND FLAGS
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'='*80}

DRAWING INFORMATION:
- Drawing Number: {pid_specs.get('title_block', {}).get('drawing_number', 'N/A')}
- Project: {pid_specs.get('title_block', {}).get('project', 'N/A')}
- Status: {pid_specs.get('title_block', {}).get('status', 'NOT FOR CONSTRUCTION')}

VALIDATION SUMMARY:
{json.dumps(validation_results.get('compliance_summary', {}), indent=2)}

MISSING ELEMENTS REQUIRING ENGINEER INPUT:
"""
        
        for item in validation_results.get('missing_elements', []):
            report += f"\n- [{item.get('severity', 'unknown')}] {item.get('item', 'N/A')}"
            report += f"\n  Action: {item.get('engineer_action', 'Review required')}\n"
        
        report += "\n\nASSUMPTIONS MADE:\n"
        for assumption in validation_results.get('assumptions_made', []):
            report += f"- {assumption}\n"
        
        report += "\n\nRECOMMENDATIONS:\n"
        for rec in validation_results.get('recommendations', []):
            report += f"- {rec}\n"
        
        report += f"\n\n{'='*80}\n"
        report += "This document must be reviewed and approved by a qualified process engineer.\n"
        
        return report
    
    def _generate_instrument_list(self, instrumentation):
        """Generate Excel-ready instrument list"""
        instruments = []
        for inst in instrumentation.get('suggested_instruments', []):
            instruments.append({
                'Tag': inst.get('tag', ''),
                'Type': inst.get('type', ''),
                'Location': inst.get('location', ''),
                'Signal': inst.get('signal_type', ''),
                'Mandatory': inst.get('mandatory', False),
                'Standard': inst.get('standard_practice', ''),
                'Status': 'AI-SUGGESTED - ENGINEER APPROVAL REQUIRED'
            })
        return instruments
    
    def _generate_valve_list(self, instrumentation):
        """Generate Excel-ready valve list"""
        valves = []
        for valve in instrumentation.get('suggested_valves', []):
            valves.append({
                'Tag': valve.get('tag', ''),
                'Type': valve.get('type', ''),
                'Location': valve.get('location', ''),
                'Size': valve.get('size', 'TBD'),
                'Mandatory': valve.get('mandatory', False),
                'Standard': valve.get('standard_practice', ''),
                'Status': 'AI-SUGGESTED - ENGINEER APPROVAL REQUIRED'
            })
        return valves
    
    def _prepare_image(self, image_file):
        """Convert image to base64 for API"""
        try:
            image = Image.open(image_file)
            
            # Resize if too large
            max_size = 2048
            if max(image.size) > max_size:
                ratio = max_size / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)
            
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            buffer = BytesIO()
            image.save(buffer, format='JPEG', quality=90)
            buffer.seek(0)
            
            return base64.b64encode(buffer.read()).decode('utf-8')
            
        except Exception as e:
            logger.error(f"Image preparation failed: {str(e)}")
            raise
