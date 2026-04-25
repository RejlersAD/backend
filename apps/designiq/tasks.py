"""
DesignIQ Celery Tasks
Background tasks for long-running operations like P&ID OCR processing
"""

from celery import shared_task
from django.utils import timezone
from django.core.cache import cache
from django.conf import settings
import logging
import tempfile
import os
import PyPDF2
from io import BytesIO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded Celery time limits for long-running DesignIQ tasks.
# These accommodate large P&IDs (100+ lines) where each line triggers a
# serial OpenAI enrichment call (~3–5s per line) on top of OCR + parsing.
#
# HARD limit  = worker process is SIGKILL'd (TimeLimitExceeded).
# SOFT limit  = raises SoftTimeLimitExceeded so the task can finalise/save.
#
# Keep frontend POLL_MAX_ATTEMPTS × POLL_INTERVAL_MS  >=  DESIGNIQ_TASK_HARD_LIMIT
# or users will see a timeout while the task is still processing.
# ---------------------------------------------------------------------------
DESIGNIQ_TASK_HARD_LIMIT = 2700  # 45 minutes
DESIGNIQ_TASK_SOFT_LIMIT = 2580  # 43 minutes



def extract_text_from_file(file_data):
    """Extract text from PDF, Excel, or Word file for enrichment"""
    try:
        content = file_data['content']
        filename = file_data['filename'].lower()
        
        # PDF
        if filename.endswith('.pdf'):
            pdf_file = BytesIO(content)
            reader = PyPDF2.PdfReader(pdf_file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        
        # Excel
        elif filename.endswith(('.xlsx', '.xls')):
            import openpyxl
            excel_file = BytesIO(content)
            workbook = openpyxl.load_workbook(excel_file)
            text = ""
            for sheet in workbook:
                for row in sheet.iter_rows(values_only=True):
                    text += " ".join(str(cell) for cell in row if cell) + "\n"
            return text
        
        # Word
        elif filename.endswith('.docx'):
            from docx import Document
            doc_file = BytesIO(content)
            doc = Document(doc_file)
            text = "\n".join([para.text for para in doc.paragraphs])
            return text
        
        else:
            logger.warning(f"Unsupported file type: {filename}")
            return ""
            
    except Exception as e:
        logger.error(f"Error extracting text from {file_data['filename']}: {e}")
        return ""


def extract_section_7(document_text):
    """
    Extract Section 7 from stress criticality document
    Looks for patterns like 'Section 7', '7.', 'SECTION 7', etc.
    """
    import re
    
    # Try different patterns for Section 7
    patterns = [
        r'(?i)section\s*7[:\.\s]+(.*?)(?=section\s*8|section\s*\d+|\Z)',
        r'(?i)7\.\s*(.*?)(?=8\.|(?:^\d+\.|\Z))',
        r'(?i)SECTION\s*7[:\.\s]+(.*?)(?=SECTION\s*8|SECTION\s*\d+|\Z)',
        r'(?i)7\s*[-–—]\s*(.*?)(?=8\s*[-–—]|\Z)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, document_text, re.DOTALL | re.MULTILINE)
        if match:
            section_text = match.group(1).strip()
            if len(section_text) > 50:  # Ensure we got meaningful content
                logger.info(f"   ✅ Found Section 7 using pattern: {pattern[:30]}...")
                return section_text
    
    # If no section found, return first 2000 chars as fallback
    logger.warning("   ⚠️ Could not find Section 7, using first 2000 chars")
    return document_text[:2000]


def determine_stress_criticality_table_7_1(size_str, max_design_temp_str):
    """
    Determine stress criticality per Table 7.1 (Section 7.6.1 - Piping Criticality Diagram)
    "Criteria for Flexibility Analysis of Piping"

    Column headers = "Design Temperature (°C) Less than or equal to":
    NPS  | ≤-30 | ≤30 | ≤50 | ≤65 | ≤85 | ≤120 | ≤150 | >150
     1"  |  L1  |  L3 |  L3 |  L3 |  L3 |  L2  |  L2  |  L1
     2"  |  L1  |  L3 |  L3 |  L3 |  L3 |  L2  |  L1  |  L1
     3"  |  L1  |  L3 |  L3 |  L3 |  L2 |  L2  |  L1  |  L1
     4"  |  L1  |  L3 |  L3 |  L3 |  L2 |  L1  |  L1  |  L1
     6"  |  L1  |  L2 |  L2 |  L2 |  L2 |  L1  |  L1  |  L1
     8"  |  L1  |  L2 |  L2 |  L2 |  L2 |  L1  |  L1  |  L1
    10"  |  L1  |  L2 |  L2 |  L2 |  L1 |  L1  |  L1  |  L1
    12"  |  L1  |  L2 |  L2 |  L2 |  L1 |  L1  |  L1  |  L1
    14"  |  L1  |  L2 |  L2 |  L2 |  L1 |  L1  |  L1  |  L1
    16"  |  L1  |  L2 |  L2 |  L1 |  L1 |  L1  |  L1  |  L1
    18"  |  L1  |  L2 |  L2 |  L1 |  L1 |  L1  |  L1  |  L1
    20"  |  L1  |  L1 |  L1 |  L1 |  L1 |  L1  |  L1  |  L1
    ≥24" |  L1  |  L1 |  L1 |  L1 |  L1 |  L1  |  L1  |  L1

    Args:
        size_str: Pipe size string (e.g., "2", "6", "12")
        max_design_temp_str: MAX design temperature in °C (e.g., "150°C", "230", "-29")

    Returns:
        str: "1" (L1-Comprehensive), "2" (L2-Simplified), or "3" (L3-Visual)
    """
    import re

    # Parse NPS (pipe size)
    try:
        size_match = re.search(r'(\d+)', str(size_str))
        nps = int(size_match.group(1)) if size_match else 0
    except:
        nps = 0

    # Parse MAX design temperature in °C
    # Input should already be Celsius (enrichment service returns °C only)
    try:
        temp_matches = re.findall(r'(-?\d+(?:\.\d+)?)', str(max_design_temp_str))
        if temp_matches:
            temps = [float(t) for t in temp_matches]
            temp = max(temps)  # Use maximum (most conservative for stress analysis)
        else:
            temp = None
    except:
        temp = None

    # No valid temperature → conservative default L1
    if temp is None:
        return "1"

    # Rule: temp ≤ -30°C → L1 for ALL pipe sizes (column "-30 and below")
    if temp <= -30:
        return "1"

    # Table 7.1 lookup by NPS ("less than or equal to" column boundaries)
    if nps == 1:
        # L3: ≤85 | L2: 85<temp≤150 | L1: >150
        if temp <= 85:    return "3"
        elif temp <= 150: return "2"
        else:             return "1"

    elif nps == 2:
        # L3: ≤85 | L2: 85<temp≤120 | L1: >120
        if temp <= 85:    return "3"
        elif temp <= 120: return "2"
        else:             return "1"

    elif nps == 3:
        # L3: ≤65 | L2: 65<temp≤120 | L1: >120
        if temp <= 65:    return "3"
        elif temp <= 120: return "2"
        else:             return "1"

    elif nps == 4:
        # L3: ≤65 | L2: 65<temp≤85 | L1: >85
        if temp <= 65:    return "3"
        elif temp <= 85:  return "2"
        else:             return "1"

    elif nps in [6, 8]:
        # L2: ≤85 | L1: >85
        if temp <= 85:    return "2"
        else:             return "1"

    elif nps in [10, 12, 14]:
        # L2: ≤65 | L1: >65
        if temp <= 65:    return "2"
        else:             return "1"

    elif nps in [16, 18]:
        # L2: ≤50 | L1: >50
        if temp <= 50:    return "2"
        else:             return "1"

    else:
        # NPS 20, ≥24, or any unrecognised size → All L1 (most conservative)
        return "1"


def build_stress_criticality_prompt(lines_context, section_7_text):
    """
    Build intelligent prompt for OpenAI to determine stress criticality codes
    Uses temperature data + Section 7 criteria
    """
    prompt = f"""You are an expert piping stress engineer applying stress criticality selection criteria from Section 7 of the project specification.

SECTION 7 - STRESS CRITICALITY SELECTION CRITERIA:
{section_7_text[:2500]}

YOUR TASK:
Analyze each pipeline line below and assign the correct stress criticality code based on Section 7 criteria.

CRITICAL ANALYSIS INSTRUCTIONS:
1. READ Section 7 above CAREFULLY - it defines the exact stress criticality codes and selection criteria
2. The criteria typically uses TEMPERATURE ranges as the primary factor (e.g., "Code 1: >400°C", "Code 2: 200-400°C", "Code 3: <200°C")
3. Also consider: fluid type, pressure class, material specification, and any other factors mentioned in Section 7
4. Extract temperature from "Normal Temp" or "Min/Max Design Temp" columns
5. Parse temperature values correctly (e.g., "150°C", "150 C", "150", "-20/200") and use the highest value for selection
6. Output the EXACT code format as specified in Section 7 (numeric like "1", "2", "3" or alphanumeric like "SC1", "SC2", "C1", etc.)

PIPELINE LINES TO ANALYZE:
"""
    
    for line in lines_context:
        prompt += f"\n- Line {line['line_number']}:"
        prompt += f"\n  * Normal Temp (°C): {line.get('normal_temp', 'N/A')}"
        prompt += f"\n  * Min Design Temp (°C): {line.get('min_design_temp', 'N/A')}"
        prompt += f"\n  * Max Design Temp (°C): {line.get('max_design_temp', 'N/A')}"
        prompt += f"\n  * Fluid: {line['fluid_code']}"
        prompt += f"\n  * Pipe Class: {line['pipr_class']}"
        prompt += f"\n  * Design Pressure: {line.get('design_pressure', 'N/A')}"
        prompt += f"\n  * Category-M Fluid: {line.get('category_m_fluid', 'N/A')}"
        prompt += f"\n  * Size (NPS): {line.get('size', 'N/A')}"
    
    prompt += """

OUTPUT FORMAT (JSON array with exact codes from Section 7):
[
  {"criticality_stress": "1"},
  {"criticality_stress": "2"},
  {"criticality_stress": "1"},
  ...
]

RULES:
✅ Apply Section 7 criteria EXACTLY as written in the document
✅ Use temperature as the PRIMARY selection factor (parse numeric values correctly)
✅ If both Normal Temp and Design Temp are available, use the higher value for conservative selection
✅ If temperature is completely missing (N/A), analyze based on other factors (fluid, pressure, class)
✅ Match the exact code format used in Section 7 (e.g., "1", "2", "3" or "SC1", "SC2", etc.)
✅ Return codes in the SAME ORDER as the lines above
❌ Do NOT add explanations, only return the JSON array
❌ Do NOT invent codes - use only what's defined in Section 7

Analyze each line intelligently and return the JSON array with stress criticality codes in order."""
    
    return prompt


def call_openai_stress_criticality_batch(lines_data, section_7_text):
    """
    Call OpenAI GPT-4o to determine stress criticality for ALL lines in one batch.
    Uses the Section 7 spec text to supplement Table 7.1 with special L1 override criteria.

    Special L1 override criteria from AGES-SP-09-004 include:
    - Category M fluids (toxic/lethal service)
    - High-pressure / High-temperature beyond standard thresholds
    - Rotating equipment connections
    - Lines connected to pressure vessels
    - Buried/underground piping
    - Steam tracing / jacketed piping
    - Any other special criteria listed in Section 7

    Args:
        lines_data: List of line dicts (must have line_number, size, fluid_code,
                    pipr_class, min_design_temp, max_design_temp, etc.)
        section_7_text: Full Section 7 text extracted from the stress criticality spec document

    Returns:
        dict mapping line_number → int (1, 2, or 3) — 1=L1 (most critical)
        On failure, returns empty dict (Table 7.1 result will be used as-is)
    """
    import json
    try:
        from decouple import config
        from openai import OpenAI

        api_key = config('OPENAI_API_KEY', default=None)
        if not api_key:
            logger.warning("⚠️ OPENAI_API_KEY not set – skipping doc-based stress criticality supplement")
            return {}

        client = OpenAI(api_key=api_key)

        # Build the context for each line (compact)
        lines_context = []
        for line in lines_data:
            lines_context.append({
                'line_number': line.get('line_number', line.get('item_tag', f'Line_{len(lines_context)+1}')),
                'size': line.get('size', ''),
                'fluid_code': line.get('fluid_code', ''),
                'pipr_class': line.get('pipr_class', ''),
                'normal_temp': line.get('normal_temp', ''),
                'min_design_temp': line.get('min_design_temp', ''),
                'max_design_temp': line.get('max_design_temp', ''),
                'design_pressure': line.get('design_pressure', ''),
                'category_m_fluid': line.get('category_m_fluid', ''),
            })

        prompt = build_stress_criticality_prompt(lines_context, section_7_text)

        logger.info(f"   🤖 Calling OpenAI for batch stress criticality on {len(lines_context)} lines...")

        response = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.1,
            max_tokens=3000
        )

        result_text = response.choices[0].message.content.strip()
        logger.info(f"   ✅ OpenAI responded with {len(result_text)} chars")

        # Parse JSON array response
        # Strip markdown code fences if present
        if '```' in result_text:
            result_text = result_text.split('```')[1]
            if result_text.startswith('json'):
                result_text = result_text[4:]

        result_list = json.loads(result_text)

        # Build mapping: line_number → criticality int
        criticality_map = {}
        for i, item in enumerate(result_list):
            if i < len(lines_context):
                line_id = lines_context[i]['line_number']
                raw_code = str(item.get('criticality_stress', '1')).strip()
                # Normalise: "L1" → 1, "1" → 1, "SC1" → 1, etc.
                import re
                num_match = re.search(r'(\d)', raw_code)
                code_int = int(num_match.group(1)) if num_match else 1
                # Clamp to valid range 1-3
                code_int = max(1, min(3, code_int))
                criticality_map[line_id] = code_int

        logger.info(f"   ✅ Parsed criticality map for {len(criticality_map)} lines from document")
        return criticality_map

    except Exception as e:
        logger.error(f"   ❌ call_openai_stress_criticality_batch failed: {e}")
        logger.error(f"   → Falling back to Table 7.1 only")
        return {}


@shared_task(bind=True, time_limit=DESIGNIQ_TASK_HARD_LIMIT, soft_time_limit=DESIGNIQ_TASK_SOFT_LIMIT)
def process_pid_upload_async(
    self, 
    file_path, 
    filename, 
    list_type, 
    user_id, 
    project_id,
    document_id,
    storage_type='local',
    s3_url=None,
    include_area=False,
    format_type='onshore',
    enrichment_files=None  # ENRICHMENT LAYER: Optional HMB/PMS/NACE
):
    """
    Background task to process P&ID PDF upload with OCR (ASYNC)
    
    ENRICHMENT LAYER: If enrichment_files provided, runs enrichment after base extraction
    
    This task handles the entire P&ID processing pipeline asynchronously:
    1. OCR extraction (Multi-Engine: Tesseract + EasyOCR + PaddleOCR) - UNCHANGED
    2. FROM-TO detection (Geometric + OpenAI Vision) - UNCHANGED
    3. Line item parsing and database storage - UNCHANGED
    4. ENRICHMENT (NEW): If HMB/PMS/NACE provided, add columns via AI
    5. Progress tracking via Celery state
    
    Args:
        file_path: Path to uploaded PDF (local or S3 key)
        filename: Original filename
        list_type: Engineering list type ('line_list', etc.)
        user_id: User ID who uploaded the file
        project_id: Project ID to associate items with
        document_id: Unique document ID (e.g., "0001-drawing.pdf")
        storage_type: 'local' or 's3'
        s3_url: S3 URL if stored in S3
        include_area: Include area field in line number format
        format_type: 'onshore', 'offshore', 'general', or 'adnoc' (Abu Dhabi Oil Co. Ltd)
        enrichment_files: Optional dict with HMB/PMS/NACE file data
        
    Returns:
        dict: Processing results with extracted lines and enriched data
    """
    from .pid_ocr_extractor_v2 import PIDLineExtractorV2
    from .models import DesignProject, EngineeringListItem
    from django.contrib.auth import get_user_model
    
    User = get_user_model()
    task_id = self.request.id
    cache_key = f'pid_upload_progress_{task_id}'
    
    def update_progress(current, total, status_message):
        """Update task progress in Celery and cache"""
        progress_data = {
            'state': 'PROCESSING',
            'current': current,
            'total': total,
            'status': status_message,
            'percent': int((current / total) * 100) if total > 0 else 0
        }
        self.update_state(state='PROGRESS', meta=progress_data)
        cache.set(cache_key, progress_data, timeout=300)
        logger.info(f"[Task {task_id}] {progress_data['percent']}% - {status_message}")
    
    try:
        # SYSTEMATIC PROCESSING: Log the workflow
        has_enrichment = enrichment_files and len(enrichment_files) == 3
        if has_enrichment:
            logger.info("=" * 80)
            logger.info("🚀 SYSTEMATIC 4-DOCUMENT PROCESSING:")
            logger.info("   STEP 1: Extract 8 base columns from P&ID (LOCKED OLD LOGIC)")
            logger.info("   STEP 2: Extract text from HMB/PMS/NACE documents")
            logger.info("   STEP 3: Run AI enrichment to add 26 columns")
            logger.info("   STEP 4: Return 34-column enriched table (8 base + 26 enriched)")
            logger.info("=" * 80)
        else:
            logger.info(f"📄 Standard P&ID processing: 8 base columns only")
        
        update_progress(5, 100, 'Initializing OCR engines...')
        
        user = User.objects.get(id=user_id)
        project = DesignProject.objects.get(id=project_id) if project_id else None
        
        update_progress(15, 100, f'Loading PDF: {filename}...')
        
        # Handle S3 or local file
        if storage_type == 's3':
            from .s3_utils import s3_storage
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                content = s3_storage.get_document(file_path)
                if not content:
                    raise Exception(f"Failed to download PDF from S3: {file_path}")
                tmp.write(content)
                tmp.flush()
                local_file_path = tmp.name
        else:
            # Build full path: file_path is relative to MEDIA_ROOT
            from django.conf import settings
            import os
            local_file_path = os.path.join(settings.MEDIA_ROOT, file_path)
        
        update_progress(25, 100, 'Running Multi-Engine OCR...')
        
        extractor = PIDLineExtractorV2()
        line_items = extractor.extract_from_pdf(local_file_path, include_area=include_area, format_type=format_type)
        
        update_progress(70, 100, f'OCR complete: Found {len(line_items)} line numbers')
        
        table_data = extractor.format_as_table_data(line_items)
        logger.info(f"[Task {task_id}] Extracted {len(table_data)} lines from {filename}")
        
        # ✅ STEP 1 COMPLETE: Base extraction (9 columns from LOCKED logic)
        logger.info("=" * 80)
        logger.info(f"✅ STEP 1 COMPLETE: Base extraction with {len(table_data)} lines")
        logger.info(f"   Base columns: {list(table_data[0].keys()) if table_data else []}")
        logger.info("=" * 80)
        
        # 🚀 STEP 2: INTELLIGENT ENRICHMENT (26 additional columns from commit 8f82346)
        # This adds enrichment WITHOUT modifying the locked base extraction logic
        enriched_data = table_data  # Start with base data
        
        # Extract enrichment files from the enrichment_files dict
        hmb_file = enrichment_files.get('hmb') if enrichment_files else None
        pms_file = enrichment_files.get('pms') if enrichment_files else None
        nace_file = enrichment_files.get('nace') if enrichment_files else None
        stress_criticality_file = enrichment_files.get('stress_criticality') if enrichment_files else None
        
        if hmb_file or pms_file or nace_file:
            try:
                logger.info("=" * 80)
                logger.info("🚀 STEP 2: Running intelligent enrichment (commit 8f82346 logic)")
                logger.info("=" * 80)
                
                # Import enrichment service (from commit 8f82346)
                import sys
                import os
                # Add the project root to Python path to import from designiq folder
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                if project_root not in sys.path:
                    sys.path.insert(0, project_root)
                
                from designiq.services.enrichment_service import EnrichmentService
                enrichment_service = EnrichmentService()
                logger.info("✅ EnrichmentService imported successfully")
                
                # Extract text from enrichment documents and clean null bytes
                hmb_text = extract_text_from_file(hmb_file) if hmb_file else None
                pms_text = extract_text_from_file(pms_file) if pms_file else None
                nace_text = extract_text_from_file(nace_file) if nace_file else None
                
                # Clean null bytes from extracted text (prevents "source code string cannot contain null bytes" error)
                if hmb_text:
                    hmb_text = hmb_text.replace('\x00', '')
                if pms_text:
                    pms_text = pms_text.replace('\x00', '')
                if nace_text:
                    nace_text = nace_text.replace('\x00', '')
                
                logger.info(f"   📄 HMB text: {len(hmb_text) if hmb_text else 0} chars")
                logger.info(f"   📄 PMS text: {len(pms_text) if pms_text else 0} chars")
                logger.info(f"   📄 NACE text: {len(nace_text) if nace_text else 0} chars")
                
                # Get current date for enrichment
                from datetime import datetime
                upload_date = datetime.now().strftime('%Y-%m-%d')
                
                # Enrich with 26 additional columns
                enriched_data = enrichment_service.enrich_lines(
                    base_lines=table_data,  # LOCKED base 9 columns
                    hmb_text=hmb_text,
                    pms_text=pms_text,
                    nace_text=nace_text,
                    pid_filename=filename,  # P&ID filename for pid_no column
                    upload_date=upload_date  # Current date for date column
                )
                
                logger.info("=" * 80)
                logger.info(f"✅ STEP 2 COMPLETE: Enrichment added {len(enriched_data[0].keys()) - len(table_data[0].keys())} columns")
                logger.info(f"   Total columns: {len(enriched_data[0].keys())} (9 base + 26 enriched)")
                logger.info("=" * 80)
                
            except Exception as enrich_err:
                logger.error(f"❌ Enrichment failed: {enrich_err}")
                logger.error(f"Error type: {type(enrich_err).__name__}")
                import traceback
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                logger.info("→ Continuing with base 17 columns only")
                enriched_data = table_data
        
        # 🚀 STEP 3: STRESS CRITICALITY PROCESSING
        # Strategy: DUAL-SOURCE — Table 7.1 (deterministic) PLUS optional 5th-document AI supplement
        # Both sources run independently; the MOST CONSERVATIVE (lowest L number) wins per line.
        # L1 = most critical (Comprehensive Analysis)
        # L2 = Simplified Analysis
        # L3 = Visual Inspection (least critical)
        try:
            logger.info("=" * 80)
            logger.info("⚡ STEP 3: Dual-Source Stress Criticality Selection")
            logger.info("   Source A: Table 7.1 deterministic lookup (Size × Max Design Temp)")
            logger.info("   Source B: 5th document AI supplement (Section 7 special L1 criteria)")
            logger.info("   Merge rule: most conservative (min L-number) wins per line")
            logger.info("=" * 80)

            # ── SOURCE A: Table 7.1 deterministic lookup ──────────────────────────────
            table71_levels = {}  # line_number → int (1/2/3)
            for line_item in enriched_data:
                pipe_size = line_item.get('size', '')
                max_design_temp = line_item.get('max_design_temp', '')
                normal_temp = line_item.get('normal_temp', '')
                design_temp = max_design_temp or normal_temp or ''
                criticality_str = determine_stress_criticality_table_7_1(pipe_size, design_temp)
                table71_levels[line_item.get('line_number', '')] = int(criticality_str) if criticality_str else 1

            logger.info(f"   ✅ Table 7.1 complete: {len(table71_levels)} lines scored")

            # ── SOURCE B: 5th document AI supplement (only if file uploaded) ──────────
            doc_levels = {}  # line_number → int (1/2/3)  — empty if no doc
            if stress_criticality_file:
                try:
                    logger.info("   📄 5th document provided — extracting Section 7 for AI supplement...")
                    sc_text = extract_text_from_file(stress_criticality_file)
                    if sc_text:
                        sc_text = sc_text.replace('\x00', '')  # clean null bytes
                    section_7_text = extract_section_7(sc_text) if sc_text else ''
                    logger.info(f"   📄 Section 7 extracted: {len(section_7_text)} chars")

                    if section_7_text and len(section_7_text) > 50:
                        doc_levels = call_openai_stress_criticality_batch(
                            lines_data=enriched_data,
                            section_7_text=section_7_text
                        )
                        logger.info(f"   ✅ Doc AI supplement returned {len(doc_levels)} codes")
                    else:
                        logger.warning("   ⚠️ Section 7 text too short — skipping AI supplement")
                except Exception as doc_err:
                    logger.error(f"   ❌ 5th document AI supplement failed: {doc_err}")
                    logger.info("   → Using Table 7.1 result only for all lines")
                    doc_levels = {}
            else:
                logger.info("   ⚠️ No 5th document uploaded — using Table 7.1 result only")

            # ── MERGE: most conservative (lower L number = more critical) wins ────────
            for line_item in enriched_data:
                line_id = line_item.get('line_number', '')
                t71 = table71_levels.get(line_id, 1)
                doc = doc_levels.get(line_id, None)  # None means doc didn't score this line

                if doc is not None:
                    # Take most conservative (L1 < L2 < L3, so take minimum number)
                    final_level = min(t71, doc)
                    if final_level != t71:
                        logger.info(f"   🔺 {line_id}: Table7.1=L{t71} → Doc override=L{doc} → Final=L{final_level}")
                else:
                    final_level = t71

                line_item['criticality_stress'] = f"L{final_level}"

            logger.info("=" * 80)
            logger.info("✅ STEP 3 COMPLETE: Dual-Source Stress Criticality Applied")
            logger.info(f"   Total lines: {len(enriched_data)}")
            logger.info(f"   Doc overrides applied: {sum(1 for lid, dv in doc_levels.items() if dv < table71_levels.get(lid, 3))}")
            if enriched_data:
                logger.info(f"   Sample criticality_stress values: {[item.get('criticality_stress') for item in enriched_data[:5]]}")
            logger.info("=" * 80)

        except Exception as criticality_err:
            logger.error(f"❌ Stress criticality processing failed: {criticality_err}")
            import traceback
            logger.error(f"Traceback:\n{traceback.format_exc()}")
            logger.info("→ Filling all lines with N/A")
            for line_item in enriched_data:
                line_item['criticality_stress'] = 'N/A'
        
        # Use enriched data for database saving
        table_data = enriched_data
        
        logger.info("=" * 80)
        logger.info("📊 FINAL DATA SUMMARY BEFORE DATABASE SAVE:")
        logger.info(f"   Total lines: {len(table_data)}")
        logger.info(f"   Total columns: {len(table_data[0].keys()) if table_data else 0}")
        if table_data:
            logger.info(f"   All column names: {list(table_data[0].keys())}")
            logger.info(f"   Has criticality_stress? {'criticality_stress' in table_data[0]}")
        logger.info("=" * 80)
        
        update_progress(75, 100, f'Saving {len(table_data)} items to database...')
        
        created_items = []
        updated_items = []
        
        for idx, line_data in enumerate(table_data):
            try:
                if idx % 10 == 0:
                    progress = 75 + int((idx / len(table_data)) * 20)
                    update_progress(progress, 100, f'Saving item {idx+1}/{len(table_data)}...')
                
                # Build data dict with base columns + enrichment columns dynamically
                data_dict = {
                    'source': 'pid_ocr_async',
                    'filename': filename,
                    'document_id': document_id,
                    'document_path': file_path,
                    'storage_type': storage_type,
                    's3_url': s3_url,
                    'upload_timestamp': timezone.now().isoformat(),
                    'format_type': format_type,
                    'include_area': include_area,
                    'page_number': line_data.get('page', 1),
                    # Base 9 columns (LOCKED - from base extraction)
                    'fluid_code': line_data['fluid_code'],
                    'fluid_description': line_data['fluid_description'],
                    'size': line_data['size'],
                    'area': line_data.get('area', ''),
                    'sequence_no': line_data['sequence_no'],
                    'pipr_class': line_data['pipr_class'],
                    'insulation': line_data['insulation'],
                    'from_equipment': line_data.get('from_equipment', ''),
                    'to_equipment': line_data.get('to_equipment', ''),
                    'from_line': line_data.get('from_line', ''),
                    'to_line': line_data.get('to_line', ''),
                    'flow_detection_method': line_data.get('flow_detection_method', ''),
                    'flow_confidence': line_data.get('flow_confidence', '')
                }
                
                # Add enrichment columns dynamically if present (27 additional columns including stress)
                # CRITICAL: Always initialize ALL columns to ensure 35 total (8 base + 27 enrichment)
                enrichment_keys = [
                    'flow_medium', 'two_phase', 'surge_flow', 'flow_max', 'density',
                    'normal_pressure', 'normal_temp', 'design_pressure',
                    'min_design_temp', 'max_design_temp',  # Split from minimax_design_temp
                    'design_code', 'category_m_fluid', 'schedule_wall_thk', 'stress_relief',
                    'pwht', 'rt', 'mt_pt', 'hardness', 'visual', 'nace_mr_0175',
                    'piping_rated_pressure', 'test_pressure', 'test_medium',
                    'pid_no', 'pid_rev', 'date', 'criticality_code', 'criticality_stress'
                ]
                # Initialize ALL enrichment columns (even if empty) to guarantee 35-column structure
                for key in enrichment_keys:
                    data_dict[key] = line_data.get(key, '')
                
                item_data = {
                    'description': f"{line_data['fluid_description']} Line - {line_data['size']}",
                    'status': 'pending',
                    'is_validated': False,
                    'data': data_dict,
                    'attachments': [{
                        'type': 'pid_pdf',
                        'filename': filename,
                        'document_id': document_id,
                        'path': file_path,
                        'storage_type': storage_type,
                        's3_url': s3_url,
                        'uploaded_at': timezone.now().isoformat()
                    }]
                }
                
                item, created = EngineeringListItem.objects.update_or_create(
                    list_type=list_type,
                    project=project,
                    item_tag=line_data['line_number'],
                    defaults=item_data
                )
                
                if created and not item.created_by:
                    item.created_by = user
                    item.save(update_fields=['created_by'])
                
                (created_items if created else updated_items).append(item.id)
                    
            except Exception as item_err:
                logger.error(f"[Task {task_id}] Failed to save item {idx+1}: {str(item_err)}")
                continue
        
        update_progress(95, 100, 'Base extraction complete!')
        
        # ✅ STEP 1 COMPLETE: Base 8 columns extracted from P&ID using OLD LOCKED LOGIC
        logger.info("=" * 80)
        logger.info(f"✅ STEP 1 COMPLETE: Extracted {len(table_data)} lines with 8 base columns from P&ID")
        logger.info(f"   Base columns: Line Number, Size, Fluid Code, Area, Sequence, PIPR Class, Insulation, From-To")
        logger.info("=" * 80)
        
        # Initialize enriched_data as base table_data (8 columns only)
        enriched_data = table_data
        logger.info(f"✅ Base extraction ready: {len(enriched_data)} lines with {len(enriched_data[0].keys()) if enriched_data else 0} columns per line")
        
        if storage_type == 's3':
            try:
                os.unlink(local_file_path)
            except:
                pass
        
        total_items = len(created_items) + len(updated_items)
        
        # 📥 SAVE EXCEL OUTPUT FOR HISTORICAL DOWNLOAD (Enhancement - No core logic change)
        excel_file_path = None
        try:
            import pandas as pd
            from django.core.files.base import ContentFile
            from .models import ProcessedPIDOutput
            
            # Generate Excel file from enriched data
            df = pd.DataFrame(enriched_data)
            excel_buffer = BytesIO()
            df.to_excel(excel_buffer, index=False, engine='openpyxl')
            excel_buffer.seek(0)
            
            # Extract P&ID number and revision from document_id or first line
            pid_number = document_id.split('-')[0] if '-' in document_id else filename.replace('.pdf', '')
            pid_revision = enriched_data[0].get('pid_rev', '') if enriched_data and 'pid_rev' in enriched_data[0] else ''
            
            # Generate Excel filename
            excel_filename = f"LineList_{pid_number}_Rev{pid_revision}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            
            # Save ProcessedPIDOutput record
            output_record = ProcessedPIDOutput.objects.create(
                project=project,
                pid_number=pid_number,
                pid_revision=pid_revision,
                list_type=list_type,
                document_id=document_id,
                processed_by=user,
                excel_filename=excel_filename,
                file_size=len(excel_buffer.getvalue()),
                total_lines=len(enriched_data),
                total_columns=len(enriched_data[0].keys()) if enriched_data else 0,
                processing_time_seconds=0,  # Can add timing if needed
                format_type=format_type,
                include_area=include_area,
                enrichment_enabled=bool(enrichment_files and len(enrichment_files) > 0)
            )
            
            # Save Excel file to FileField
            output_record.excel_file.save(
                excel_filename,
                ContentFile(excel_buffer.getvalue()),
                save=True
            )
            
            excel_file_path = output_record.excel_file.name
            logger.info(f"📥 Saved historical output: {excel_filename} (ID: {output_record.id})")
            
        except Exception as excel_err:
            logger.warning(f"⚠️ Could not save Excel output for history: {excel_err}")
            # Don't fail the entire process if Excel save fails
        
        # DEBUG: Log what we're returning
        logger.info("="*80)
        logger.info("🔍 PREPARING TASK RESULT")
        logger.info(f"   - Base extraction (extracted_lines): {len(table_data)} items")
        logger.info(f"   - Enriched data: {len(enriched_data) if enriched_data else 0} items")
        if enriched_data:
            logger.info(f"   - Enriched data columns: {len(enriched_data[0].keys())} keys")
            logger.info(f"   - Sample enriched keys: {list(enriched_data[0].keys())[:10]}")
        if excel_file_path:
            logger.info(f"   - Excel output saved: {excel_file_path}")
        logger.info("="*80)
        
        result = {
            'success': True,
            'filename': filename,
            'document_id': document_id,
            'document_path': file_path,
            'storage_type': storage_type,
            's3_url': s3_url,
            'items_created': len(created_items),
            'items_updated': len(updated_items),
            'total_items': total_items,
            'extracted_lines': table_data,  # Base extraction (8 columns - ALWAYS)
            'enriched_data': enriched_data if enriched_data else [],  # Enriched data (20+ columns - if all 3 docs)
            'format_type': format_type,
            'include_area': include_area,
            'message': (
                f'✅ P&ID processed with full enrichment: {len(enriched_data[0].keys())} columns' 
                if enriched_data 
                else '✅ P&ID processed: Base 8 columns (provide HMB+PMS+NACE for full enrichment)'
            )
        }
        
        logger.info("="*80)
        logger.info("🚀 TASK RESULT PREPARED - RETURNING TO VIEW")
        logger.info(f"   - enriched_data in result: {len(result.get('enriched_data', []))} items")
        logger.info(f"   - enriched_data columns: {len(result.get('enriched_data', [{}])[0].keys()) if result.get('enriched_data') else 0}")
        if result.get('enriched_data'):
            logger.info(f"   - Has criticality_stress in first item? {'criticality_stress' in result['enriched_data'][0]}")
            logger.info(f"   - Sample criticality_stress value: {result['enriched_data'][0].get('criticality_stress', 'MISSING')}")
        logger.info("="*80)
        
        cache.set(cache_key, {
            'state': 'SUCCESS',
            'result': result,
            'percent': 100,
            'status': 'Processing complete!'
        }, timeout=3600)
        
        logger.info(f"✅ [Task {task_id}] Success: {total_items} items ({len(created_items)} created, {len(updated_items)} updated)")
        update_progress(100, 100, 'Complete!')
        
        return result
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ [Task {task_id}] Error: {error_msg}", exc_info=True)
        
        cache.set(cache_key, {
            'state': 'FAILURE',
            'error': error_msg,
            'percent': 0,
            'status': f'Error: {error_msg}'
        }, timeout=3600)
        
        raise


@shared_task(bind=True, time_limit=DESIGNIQ_TASK_HARD_LIMIT, soft_time_limit=DESIGNIQ_TASK_SOFT_LIMIT)
def base_extract_lines_async(self, file_path, filename, include_area=False, format_type='onshore'):
    """
    🎯 Async Celery task for Line List base extraction (P&ID only)

    Fixes production timeout: Railway's reverse proxy cuts long HTTP requests.
    This task runs OCR in the background and stores results in cache for polling.

    Args:
        file_path: Absolute path to the temporary PDF file
        filename: Original uploaded filename (for logging)
        include_area: Include area code in line number format
        format_type: 'onshore', 'offshore', 'general', or 'adnoc' (Abu Dhabi Oil Co. Ltd)

    Returns:
        dict with success, total_lines, data, columns, message
    """
    from .pid_ocr_extractor_v2 import PIDLineExtractorV2

    task_id = self.request.id
    cache_key = f'base_extraction_progress_{task_id}'

    def update_progress(percent, status_message, **extra):
        """Write progress to cache so the status endpoint can return it.

        `**extra` lets us stream richer fields (current_page, total_pages,
        lines_so_far, phase) without breaking existing callers.
        """
        progress_data = {
            'task_id': task_id,
            'state': 'PROGRESS',
            'status': status_message,
            'percent': percent,
        }
        if extra:
            progress_data.update(extra)
        self.update_state(state='PROGRESS', meta=progress_data)
        cache.set(cache_key, progress_data, timeout=3600)
        logger.info(f"[base_extract {task_id}] {percent}% – {status_message}")

    # Progress band reserved for the per-page extraction loop.
    # Soft-coded: edit these two constants if you want to reshape the curve.
    PAGE_PROGRESS_START = 20   # after OCR engine warmup
    PAGE_PROGRESS_END   = 80   # before final formatting (leaves 80-100 for post-processing)

    def _page_progress(page_num, total_pages, lines_so_far, phase):
        """Callback passed into extract_from_pdf — runs on every page boundary."""
        span = PAGE_PROGRESS_END - PAGE_PROGRESS_START
        frac = max(0.0, min(1.0, (page_num - 1) / max(1, total_pages)))
        percent = int(PAGE_PROGRESS_START + span * frac)
        update_progress(
            percent,
            f'Page {page_num}/{total_pages} — {lines_so_far} lines extracted so far…',
            current_page=page_num,
            total_pages=total_pages,
            lines_so_far=lines_so_far,
            phase=phase,
        )

    try:
        logger.info("=" * 70)
        logger.info(f"🎯 BASE EXTRACTION TASK STARTED  task_id={task_id}")
        logger.info(f"   file: {filename}  format: {format_type}  area: {include_area}")
        logger.info("=" * 70)

        update_progress(5, 'Initializing OCR engine…')

        extractor = PIDLineExtractorV2()

        update_progress(15, f'Loaded OCR engine. Running extraction on {filename}…')

        extracted_lines = extractor.extract_from_pdf(
            file_path,
            include_area=include_area,
            format_type=format_type,
            progress_callback=_page_progress,
        )

        update_progress(85, f'OCR complete: {len(extracted_lines)} lines found. Formatting…')

        # ── Soft-coded breaker / page-connector inference ───────────────────
        # Fills empty from_line / to_line by spatial proximity to breaker tags
        # on each page. Patterns + thresholds live in
        # apps/designiq/breaker_inference.py.  Pure post-processing — never
        # overwrites existing values produced by earlier detectors.
        try:
            from apps.designiq.breaker_inference import infer_breakers_for_lines
            update_progress(88, 'Inferring From/To from page-connector breakers…')
            infer_breakers_for_lines(extracted_lines, file_path)
        except Exception as _be:
            logger.warning(f'[base_extract {task_id}] breaker inference skipped: {_be}')

        # Build 8-column output structure with EXPLICIT field mapping
        # Column order: Original Detection, Fluid Code, Size, Sequence No, PIPR Class, Insulation, From, To
        # Note: For offshore format (AREA-FluidCode-LineSize-PipeClass-SequenceNo-Insulation),
        #       area is parsed internally but NOT exported as a separate column
        base_data = []
        for line in extracted_lines:
            base_data.append({
                'original_detection': line.get('original_detection', line.get('line_number', '')),
                'fluid_code': line.get('fluid_code', ''),
                'size': line.get('size', ''),
                'sequence_no': line.get('sequence_no', ''),
                'pipr_class': line.get('pipr_class', ''),
                'insulation': line.get('insulation', ''),
                # Explicit from_line / to_line keys (frontend prefers these).
                # Plus the legacy from / to / *_equipment keys for back-compat.
                'from_line': line.get('from_line', ''),
                'to_line': line.get('to_line', ''),
                'from_equipment': line.get('from_equipment', ''),
                'to_equipment': line.get('to_equipment', ''),
                'from': line.get('from_line', line.get('from_equipment', '')),
                'to': line.get('to_line', line.get('to_equipment', '')),
            })
        
        logger.info(f'[base_extract {task_id}] Formatted {len(base_data)} rows with 8 explicit columns (area excluded from export)')

        # Clean up temporary file
        if os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except Exception as cleanup_err:
                logger.warning(f"Could not delete temp file {file_path}: {cleanup_err}")

        result = {
            'success': True,
            'total_lines': len(base_data),
            'data': base_data,
            'columns': 8,
            'message': f'Successfully extracted {len(base_data)} lines from {filename}',
        }

        cache.set(cache_key, {
            'task_id': task_id,
            'state': 'SUCCESS',
            'status': 'Extraction complete!',
            'percent': 100,
            'result': result,
        }, timeout=3600)

        logger.info(f"✅ BASE EXTRACTION COMPLETE: {len(base_data)} lines  task_id={task_id}")
        return result

    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ base_extract_lines_async failed: {error_msg}", exc_info=True)

        # Clean up temp file on failure
        if os.path.exists(file_path):
            try:
                os.unlink(file_path)
            except Exception:
                pass

        cache.set(cache_key, {
            'task_id': task_id,
            'state': 'FAILURE',
            'status': 'Extraction failed',
            'percent': 0,
            'error': error_msg,
        }, timeout=3600)

        raise
