"""
P&ID Analysis Service
AI-powered P&ID verification using OpenAI GPT-4 Vision with RAG support
"""
import os
import base64
import io
from datetime import datetime
from typing import Dict, List, Any, Optional
from django.conf import settings
from openai import OpenAI
import fitz  # PyMuPDF
from PIL import Image
from .rag_service import RAGService


class PIDAnalysisService:
    """Service for analyzing P&ID drawings using OpenAI"""
    
    # Complete P&ID analysis prompt with enhanced data extraction
    ANALYSIS_PROMPT = """🔹 ROLE & CONTEXT

You are an AI-Powered P&ID Design Verification Engine acting as a Senior Process Engineer + Piping Engineer + Instrumentation Engineer with expertise in:

- Oil & Gas P&ID standards (ADNOC / DEP / API / ISA / ISO practices)
- Equipment datasheets, instrument schedules, line lists
- Compressor packages, suction drums, flare systems, chemical injection, antisurge systems
- Engineering review, HAZOP readiness, and design compliance

**CRITICAL INSTRUCTIONS:**
1. ANALYZE THE ACTUAL DRAWING PROVIDED - Do NOT generate generic or placeholder issues
2. READ all visible text, tags, line numbers, equipment labels, and data tables
3. Extract EXACT values from equipment boxes, instrument tags, datasheets, and schedules
4. Identify REAL issues based on what you SEE in the drawing
5. Each P&ID drawing is UNIQUE - your analysis must reflect the specific content of THIS drawing

🔹 MANDATORY EXTRACTION FROM DRAWING

BEFORE analyzing, you MUST extract and verify ALL visible data:

**Drawing Information:**
- Drawing number (top right or title block)
- Drawing title (title block)
- Revision number and date (title block)
- Project name/client (if visible)
- Drawing scale and sheet size

**Equipment Data Extraction (CRITICAL):**
- ALL equipment tags (e.g., V-3610-01, P-1001, E-2001, T-3001)
- Equipment datasheets if visible in drawing
- Design pressure and temperature for EACH equipment
- Equipment dimensions (height, diameter, T/T, T/L)
- Material specifications (CS, SS304, SS316L, etc.)
- Nozzle sizes, ratings, and orientations
- Equipment capacity, volume, or flow rates
- Any equipment notes or specifications shown

**Instrument Alarm & Trip Schedule (CRITICAL):**
- ALL instrument tags (e.g., LIT-001, PIT-002, TIT-003, FIT-004)
- Instrument types (transmitters, switches, indicators, controllers)
- Alarm setpoints: HH (High-High), H (High), L (Low), LL (Low-Low)
- Trip setpoints and shutdown logic
- Instrument ranges and units (e.g., 0-100 barg, 0-200°C)
- Fail-safe positions (FC=Fail Close, FO=Fail Open, FL=Fail Lock)
- Interlock descriptions if shown
- Safety Instrumented Functions (SIF) if marked
- Instrument connection sizes and types

**Line Number & Sizing (CRITICAL):**
- ALL line numbers shown (format: Size-Service-Number-Material, e.g., 6"-P-001-CS)
- Pipe sizes for EACH line segment
- Line materials (CS, SS, LTCS, etc.)
- Insulation requirements (marked as INS, HT, CT, etc.)
- Line pressure ratings/classes (150#, 300#, 600#, etc.)
- Flow directions (arrows)
- Slope requirements and drainage points
- Line specifications references

**PID/PFD Development Guidelines Compliance:**
- Compliance with drawing legends and symbols
- Adherence to company/project standards noted on drawing
- Valve nomenclature and symbology
- Instrumentation symbols per ISA-5.1
- Equipment numbering conventions
- Notes regarding design basis or guidelines
- References to process flow diagrams (PFDs)

🔹 SCOPE OF VERIFICATION

Perform COMPREHENSIVE analysis on ACTUAL content visible in the drawing:

**1️⃣ EQUIPMENT DATASHEET VERIFICATION**
For EACH equipment visible on drawing:
- Extract equipment tag number
- Record design pressure and temperature
- Document material of construction
- Note dimensions (height, diameter, volume)
- List all nozzle connections with sizes
- Identify any missing datasheet information
- Cross-check values against visible equipment box
- Flag discrepancies in equipment specifications

**2️⃣ INSTRUMENT ALARM & TRIP SCHEDULE EXTRACTION**
For EACH instrument visible on drawing:
- Extract instrument tag (e.g., LIT-3610-01A)
- Identify instrument type (transmitter/switch/indicator)
- Record alarm setpoints:
  * LSHH/LAHH (Low/High-High alarms)
  * LSH/LAH (Low/High alarms)
  * LSL/LAL (Low-Low/Low alarms)
  * LSLL/LALL (Low-Low-Low alarms)
- Document trip conditions and shutdown logic
- Note instrument ranges (e.g., 0-100%, 0-10 barg)
- Record fail-safe positions (FC/FO/FL/FI)
- Identify safety critical instruments (SIS/SIL rated)
- Flag missing alarm/trip information

**3️⃣ LINE NUMBER & SIZING VERIFICATION**
For EACH piping line visible:
- Extract complete line number (Size-Service-SeqNo-Material)
- Example: "6"-P-001-CS" = 6 inch, Process, Line 001, Carbon Steel
- Verify line size consistency throughout routing
- Check material specification codes
- Document pressure class/rating (150#, 300#, etc.)
- Identify insulation requirements
- Verify slope requirements and drainage
- Check reducer sizes at equipment connections
- Flag incorrect or missing line sizing

**4️⃣ PID/PFD GUIDELINES COMPLIANCE**
- Check adherence to drawing legends
- Verify instrument symbols match ISA-5.1 standard
- Confirm equipment numbering follows project conventions
- Review valve symbols and specifications
- Check for proper use of line break symbols
- Verify notes comply with company standards
- Cross-reference with PFD if mentioned
- Identify deviations from standard practices

**5️⃣ SAFETY SYSTEMS & INTERLOCKS**
- Identify all PSVs and their set pressures
- Check emergency shutdown (ESD) valve positions
- Verify fire protection systems if shown
- Document all interlocks and shutdown logic
- Check for proper isolation valve arrangements
- Verify blowdown/depressuring systems

**6️⃣ DOCUMENTATION & COMPLETENESS**
- Check for complete title block information
- Verify all revision clouds are marked
- Confirm drawing notes are clear and complete
- Check for proper references to other drawings
- Identify any "TBC" (To Be Confirmed) items
- Flag missing or incomplete information

🔹 ANALYSIS QUALITY REQUIREMENTS

**You MUST:**
✓ Extract and list ALL equipment datasheets from drawing
✓ Create complete instrument alarm/trip schedule table
✓ Document every line number with full sizing details
✓ Reference SPECIFIC tags and values from THIS drawing
✓ Provide 15-30 specific observations for typical P&ID
✓ Include EXACT values, not generic descriptions
✓ Cross-reference PFD guidelines mentioned on drawing

**You MUST NOT:**
✗ Skip equipment datasheet extraction
✗ Omit instrument alarm/trip schedules
✗ Ignore line sizing and numbering details
✗ Generate generic issues without specific references
✗ Assume values not visible on drawing

🔹 OUTPUT FORMAT (MANDATORY - JSON WITH ENHANCED DATA)

Return ONLY a valid JSON object with COMPREHENSIVE data extraction:

{
  "drawing_info": {
    "drawing_number": "EXACT number from drawing",
    "drawing_title": "EXACT title from drawing",
    "revision": "EXACT revision from drawing",
    "project_name": "Project name if visible",
    "analysis_date": "current date in ISO format"
  },
  "equipment_datasheets": [
    {
      "equipment_tag": "V-3610-01",
      "equipment_type": "Vessel/Tank/Pump/etc",
      "design_pressure": "Value with unit",
      "design_temperature": "Value with unit",
      "material": "CS/SS316L/etc",
      "dimensions": "Details visible",
      "capacity_volume": "If shown",
      "nozzles": "List of connections",
      "notes": "Special requirements"
    }
  ],
  "instrument_schedule": [
    {
      "instrument_tag": "LIT-3610-01A",
      "instrument_type": "Level Transmitter/etc",
      "service": "What it measures",
      "range": "0-100% or 0-10 barg",
      "alarm_high_high": "HH value if shown",
      "alarm_high": "H value if shown",
      "alarm_low": "L value if shown",
      "alarm_low_low": "LL value if shown",
      "trip_setpoint": "Trip value if shown",
      "fail_position": "FC/FO/FL/FI",
      "safety_critical": "Yes/No SIS rated",
      "connection_size": "Size if visible"
    }
  ],
  "line_list": [
    {
      "line_number": "6\"-P-001-CS",
      "line_size": "6 inch",
      "service_code": "P=Process",
      "sequence_number": "001",
      "material_spec": "CS=Carbon Steel",
      "pressure_class": "If shown",
      "insulation": "Yes/No/Type",
      "from_equipment": "Start tag",
      "to_equipment": "End tag",
      "special_requirements": "Notes"
    }
  ],
  "pfd_guidelines_compliance": {
    "legend_adherence": "Status with notes",
    "isa_symbol_compliance": "Status",
    "numbering_convention": "Compliance notes",
    "notes_completeness": "Assessment",
    "referenced_documents": "List of references"
  },
  "summary": {
    "total_equipment": <count>,
    "total_instruments": <count>,
    "total_lines": <count>,
    "total_issues": <count>,
    "critical_count": <count>,
    "major_count": <count>,
    "minor_count": <count>,
    "observation_count": <count>
  },
  "issues": [
    {
      "serial_number": 1,
      "pid_reference": "EXACT tag from drawing",
      "category": "Equipment Datasheet | Instrument Schedule | Line Sizing | PFD Guidelines | Safety Systems | Documentation",
      "severity": "critical | major | minor | observation",
      "issue_observed": "Detailed description with EXACT values",
      "action_required": "Specific corrective action",
      "approval": "Pending",
      "remark": "Pending",
      "status": "pending"
    }
  ]
}

🔹 SEVERITY CLASSIFICATION
- **critical**: Safety issues (PSV blocked, no ESD, fire protection gaps)
- **major**: Operational issues (wrong specs, missing instruments)
- **minor**: Documentation (unclear labels, missing notes)
- **observation**: Improvement suggestions

🔹 EXAMPLE OF GOOD vs BAD ANALYSIS

**BAD (Generic):**
"V-3610-01 dimensions incorrect" ← No specific values, could apply to any drawing

**GOOD (Specific):**
"V-3610-01 equipment box shows HEIGHT = 6000 mm (T/T), but nozzle arrangement suggests vessel height should be approximately 4000 mm based on visible nozzle spacing"

Now analyze THIS specific P&ID drawing and return detailed JSON based on what you actually SEE."""

    def __init__(self):
        """Initialize OpenAI client with proper error handling"""
        # Try multiple sources for API key (soft-coded approach)
        api_key = None
        
        # Priority 1: Django settings
        if hasattr(settings, 'OPENAI_API_KEY'):
            api_key = settings.OPENAI_API_KEY
            print(f"[DEBUG] API key from Django settings: {api_key[:15] if api_key else 'None'}...")
        
        # Priority 2: Environment variable
        if not api_key or api_key == '':
            api_key = os.getenv('OPENAI_API_KEY')
            print(f"[DEBUG] API key from environment: {api_key[:15] if api_key else 'None'}...")
        
        # Validate API key
        if not api_key or api_key.strip() == '' or api_key == 'your-openai-api-key-here':
            error_msg = (
                "❌ CRITICAL: OpenAI API key is NOT configured!\n"
                "This must be set in Railway environment variables.\n"
                "Instructions:\n"
                "1. Go to Railway Dashboard → Your Project → Variables\n"
                "2. Add: OPENAI_API_KEY = your-actual-key\n"
                "3. Get key from: https://platform.openai.com/api-keys\n"
                "Contact administrator immediately!"
            )
            print(f"[ERROR] {error_msg}")
            raise ValueError("OpenAI API key not configured - contact administrator")
        
        try:
            self.client = OpenAI(api_key=api_key)
            print(f"[INFO] ✅ OpenAI client initialized successfully (key: {api_key[:12]}...{api_key[-4:]})")
        except Exception as e:
            error_msg = f"Failed to initialize OpenAI client: {str(e)}"
            print(f"[ERROR] {error_msg}")
            raise ValueError(error_msg)
    
    def pdf_to_images(self, pdf_file, dpi: int = 300) -> List[str]:
        """
        Convert PDF pages to high-quality base64-encoded images
        
        Args:
            pdf_file: Either a file path (str) or a file-like object (Django FileField)
            dpi: DPI for image conversion (increased to 300 for better text recognition)
        
        Returns:
            List of base64-encoded PNG images
        """
        images_base64 = []
        
        # Handle both file paths and file objects (S3/Django FileField)
        if isinstance(pdf_file, str):
            # Local file path
            pdf_document = fitz.open(pdf_file)
        else:
            # File object (from S3 or Django FileField)
            # Read file content into memory
            pdf_file.seek(0)  # Ensure we're at the start
            pdf_bytes = pdf_file.read()
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        try:
            # Convert each page to high-quality image
            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                
                # Render page to image with higher DPI for better text recognition
                zoom = dpi / 72  # 72 is default DPI
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)  # No alpha channel for smaller size
                
                # Convert to PIL Image for potential preprocessing
                img_data = pix.tobytes("png")
                
                # Encode to base64
                img_base64 = base64.b64encode(img_data).decode('utf-8')
                images_base64.append(img_base64)
        
        finally:
            pdf_document.close()
        
        return images_base64
    
    def analyze_pid_drawing(self, pdf_file, drawing_number: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze P&ID drawing using OpenAI GPT-4 Vision with optional RAG context
        
        Args:
            pdf_file: Either a file path (str) or a Django FileField object
            drawing_number: Optional drawing number for RAG context retrieval
        
        Returns:
            Dictionary containing analysis results
        """
        try:
            # Convert PDF pages to images
            images_base64 = self.pdf_to_images(pdf_file)
            
            if not images_base64:
                raise ValueError("Failed to convert PDF to images")
            
            # Get RAG context if available
            rag_context = ""
            rag_enabled = os.environ.get('RAG_ENABLED', 'false').lower() == 'true'
            
            if rag_enabled and drawing_number:
                try:
                    print(f"[INFO] RAG enabled - retrieving context for drawing: {drawing_number}")
                    rag_service = RAGService()
                    rag_context = rag_service.retrieve_context(drawing_number)
                    if rag_context:
                        print(f"[INFO] Retrieved RAG context ({len(rag_context)} chars)")
                except Exception as rag_error:
                    print(f"[WARNING] RAG context retrieval failed: {str(rag_error)}")
                    # Continue without RAG context - not critical for analysis
            
            # Build prompt with optional RAG context
            analysis_prompt = self.ANALYSIS_PROMPT
            if rag_context:
                analysis_prompt = f"""**REFERENCE CONTEXT FROM STANDARDS AND DOCUMENTATION:**

{rag_context}

---

{self.ANALYSIS_PROMPT}

**Important:** Use the reference context above to enhance your analysis with specific standards, guidelines, and best practices. Cross-reference equipment specifications and design requirements with the provided documentation."""
                print(f"[INFO] Enhanced prompt with RAG context")
            
            # Build message content with all page images
            message_content = [
                {
                    "type": "text",
                    "text": analysis_prompt
                }
            ]
            
            # Add each page image
            for idx, img_base64 in enumerate(images_base64):
                message_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_base64}",
                        "detail": "high"
                    }
                })
            
            # Call OpenAI API with vision capabilities
            print(f"[INFO] Calling OpenAI API (model: gpt-4o) with {len(images_base64)} page(s)...")
            print(f"[INFO] Request timestamp: {datetime.now().isoformat()}")
            
            try:
                response = self.client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a senior oil & gas engineering expert specializing in P&ID verification and design review. You must analyze each drawing uniquely based on its specific content, extracting actual values and equipment tags visible in the image."
                        },
                        {
                            "role": "user",
                            "content": message_content
                        }
                    ],
                    max_tokens=16000,  # Increased for more detailed analysis
                    temperature=0.2,  # Slightly increased for more varied, detailed responses
                    response_format={"type": "json_object"}  # Force JSON response
                )
                print(f"[INFO] OpenAI API call successful")
                print(f"[INFO] Tokens used: {response.usage.total_tokens if hasattr(response, 'usage') else 'Unknown'}")
            except Exception as api_error:
                error_details = str(api_error)
                print(f"[ERROR] OpenAI API call failed: {error_details}")
                
                # Check for common API errors
                if "invalid_api_key" in error_details.lower():
                    raise Exception("Invalid OpenAI API key. Please check your OPENAI_API_KEY in .env file")
                elif "insufficient_quota" in error_details.lower():
                    raise Exception("OpenAI API quota exceeded. Please check your account billing")
                elif "rate_limit" in error_details.lower():
                    raise Exception("OpenAI API rate limit reached. Please wait a moment and try again")
                else:
                    raise Exception(f"OpenAI API error: {error_details}")
            
            # Extract and parse JSON response
            result_text = response.choices[0].message.content
            
            # Log raw response for debugging
            print(f"[DEBUG] OpenAI Raw Response length: {len(result_text) if result_text else 0} chars")
            print(f"[DEBUG] OpenAI Raw Response (first 500 chars): {result_text[:500] if result_text else 'EMPTY'}")
            
            if not result_text or not result_text.strip():
                raise ValueError(
                    "OpenAI returned empty response. This may be due to:\n"
                    "1. Invalid or expired API key\n"
                    "2. Insufficient quota/credits\n"
                    "3. PDF image quality issues\n"
                    "Please check your OpenAI account and API key configuration."
                )
            
            # Clean potential markdown code blocks
            original_text = result_text
            if result_text.startswith("```json"):
                result_text = result_text.split("```json")[1].split("```")[0].strip()
                print(f"[DEBUG] Cleaned markdown ```json wrapper")
            elif result_text.startswith("```"):
                result_text = result_text.split("```")[1].split("```")[0].strip()
                print(f"[DEBUG] Cleaned markdown ``` wrapper")
            
            import json
            try:
                analysis_result = json.loads(result_text)
                print(f"[INFO] Successfully parsed JSON response")
            except json.JSONDecodeError as e:
                print(f"[ERROR] JSON Decode Error: {str(e)}")
                print(f"[ERROR] Error at position: {e.pos}")
                print(f"[ERROR] Problematic text (first 1000 chars): {result_text[:1000]}")
                print(f"[ERROR] Original response (first 1000 chars): {original_text[:1000]}")
                
                # Try to provide helpful error message
                raise ValueError(
                    f"OpenAI returned invalid JSON format.\n"
                    f"Error: {str(e)}\n"
                    f"This usually means:\n"
                    f"1. API returned an error message instead of analysis\n"
                    f"2. API key is invalid or has insufficient permissions\n"
                    f"3. Response was truncated due to token limits\n"
                    f"Response preview: {original_text[:200]}..."
                )
            
            # Validate and enrich response
            if 'issues' not in analysis_result:
                analysis_result['issues'] = []
            
            if 'summary' not in analysis_result:
                analysis_result['summary'] = {
                    'total_issues': len(analysis_result.get('issues', [])),
                    'critical_count': 0,
                    'major_count': 0,
                    'minor_count': 0,
                    'observation_count': 0
                }
            
            if 'drawing_info' not in analysis_result:
                analysis_result['drawing_info'] = {
                    'drawing_number': 'Unknown',
                    'drawing_title': 'Unknown',
                    'revision': 'Unknown',
                    'analysis_date': datetime.now().isoformat()
                }
            
            return analysis_result
            
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON parsing failed: {str(e)}")
            raise Exception(f"P&ID analysis failed: AI returned invalid JSON format. {str(e)}")
        except Exception as e:
            print(f"[ERROR] P&ID analysis exception: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            raise Exception(f"P&ID analysis failed: {str(e)}")
    
    def generate_report_summary(self, issues: List[Dict]) -> Dict[str, int]:
        """Generate summary statistics from issues"""
        summary = {
            'total_issues': len(issues),
            'approved_count': 0,
            'ignored_count': 0,
            'pending_count': 0,
            'critical_count': 0,
            'major_count': 0,
            'minor_count': 0,
            'observation_count': 0
        }
        
        for issue in issues:
            # Count by status
            status = issue.get('status', 'pending').lower()
            if status == 'approved':
                summary['approved_count'] += 1
            elif status == 'ignored':
                summary['ignored_count'] += 1
            else:
                summary['pending_count'] += 1
            
            # Count by severity
            severity = issue.get('severity', 'observation').lower()
            if severity == 'critical':
                summary['critical_count'] += 1
            elif severity == 'major':
                summary['major_count'] += 1
            elif severity == 'minor':
                summary['minor_count'] += 1
            else:
                summary['observation_count'] += 1
        
        return summary
