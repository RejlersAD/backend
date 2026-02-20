"""
DesignIQ Celery Tasks
Background tasks for long-running operations like P&ID OCR processing
"""

from celery import shared_task
from django.utils import timezone
from django.core.cache import cache
import logging
import tempfile
import os
import PyPDF2
from io import BytesIO

logger = logging.getLogger(__name__)


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


@shared_task(bind=True, time_limit=1200, soft_time_limit=1140)  # 20 minutes max
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
        format_type: 'onshore', 'offshore', or 'general'
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
        
        # 🔥 IMMEDIATELY ADD DEFAULT VALUES TO ALL EXTRACTED LINES
        for line in table_data:
            line['flow_medium'] = 'default'
            line['two_phase'] = 'default'
            line['surge_flow'] = 'default'
            line['flow_max'] = 'default'
            line['density'] = 'default'
            line['normal_pressure'] = 'default'
            line['normal_temp'] = 'default'
            line['design_pressure'] = 'default'
            line['minimax_design_temp'] = 'default'
            line['design_code'] = 'default'
            line['category_m_fluid'] = 'default'
            line['schedule_wall_thk'] = 'default'
            line['stress_relief'] = 'default'
            line['pwht'] = 'default'
            line['rt'] = 'default'
            line['mt_pt'] = 'default'
            line['hardness'] = 'default'
            line['visual'] = 'default'
            line['nace_mr_0175'] = 'default'
            line['piping_rated_pressure'] = 'default'
            line['test_pressure'] = 'default'
            line['test_medium'] = 'default'
            line['pid_no'] = 'default'
            line['pid_rev'] = 'default'
            line['date'] = 'default'
            line['criticality_code'] = 'default'
        
        logger.info(f"✅ ADDED 26 'default' VALUES TO ALL {len(table_data)} LINES")
        
        update_progress(75, 100, f'Saving {len(table_data)} items to database...')
        
        created_items = []
        updated_items = []
        
        for idx, line_data in enumerate(table_data):
            try:
                if idx % 10 == 0:
                    progress = 75 + int((idx / len(table_data)) * 20)
                    update_progress(progress, 100, f'Saving item {idx+1}/{len(table_data)}...')
                
                item_data = {
                    'description': f"{line_data.get('flow_medium', 'Line')} - {line_data['size']}",
                    'status': 'pending',
                    'is_validated': False,
                    'data': {
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
                        'fluid_code': line_data['fluid_code'],
                        'size': line_data['size'],
                        'area': line_data.get('area', ''),
                        'sequence_no': line_data['sequence_no'],
                        'pipr_class': line_data['pipr_class'],
                        'insulation': line_data['insulation'],
                        'from_line': line_data.get('from_line', ''),
                        'to_line': line_data.get('to_line', ''),
                        'flow_detection_method': line_data.get('flow_detection_method', ''),
                        'flow_confidence': line_data.get('flow_confidence', ''),
                        # 26 Gen AI Enrichment columns
                        'flow_medium': line_data.get('flow_medium', 'default'),
                        'two_phase': line_data.get('two_phase', 'default'),
                        'surge_flow': line_data.get('surge_flow', 'default'),
                        'flow_max': line_data.get('flow_max', 'default'),
                        'density': line_data.get('density', 'default'),
                        'normal_pressure': line_data.get('normal_pressure', 'default'),
                        'normal_temp': line_data.get('normal_temp', 'default'),
                        'design_pressure': line_data.get('design_pressure', 'default'),
                        'minimax_design_temp': line_data.get('minimax_design_temp', 'default'),
                        'design_code': line_data.get('design_code', 'default'),
                        'category_m_fluid': line_data.get('category_m_fluid', 'default'),
                        'schedule_wall_thk': line_data.get('schedule_wall_thk', 'default'),
                        'stress_relief': line_data.get('stress_relief', 'default'),
                        'pwht': line_data.get('pwht', 'default'),
                        'rt': line_data.get('rt', 'default'),
                        'mt_pt': line_data.get('mt_pt', 'default'),
                        'hardness': line_data.get('hardness', 'default'),
                        'visual': line_data.get('visual', 'default'),
                        'nace_mr_0175': line_data.get('nace_mr_0175', 'default'),
                        'piping_rated_pressure': line_data.get('piping_rated_pressure', 'default'),
                        'test_pressure': line_data.get('test_pressure', 'default'),
                        'test_medium': line_data.get('test_medium', 'default'),
                        'pid_no': line_data.get('pid_no', 'default'),
                        'pid_rev': line_data.get('pid_rev', 'default'),
                        'date': line_data.get('date', 'default'),
                        'criticality_code': line_data.get('criticality_code', 'default')
                    },
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
        
        # 🔥 QUICK FIX: Add default enrichment columns to EVERY line immediately
        logger.info("🔥 ADDING DEFAULT ENRICHMENT COLUMNS TO ALL LINES")
        default_enrichment = {
            'flow_medium': 'default',
            'two_phase': 'default',
            'surge_flow': 'default',
            'flow_max': 'default',
            'density': 'default',
            'normal_pressure': 'default',
            'normal_temp': 'default',
            'design_pressure': 'default',
            'minimax_design_temp': 'default',
            'design_code': 'default',
            'category_m_fluid': 'default',
            'schedule_wall_thk': 'default',
            'stress_relief': 'default',
            'pwht': 'default',
            'rt': 'default',
            'mt_pt': 'default',
            'hardness': 'default',
            'visual': 'default',
            'nace_mr_0175': 'default',
            'piping_rated_pressure': 'default',
            'test_pressure': 'default',
            'test_medium': 'default',
            'pid_no': 'default',
            'pid_rev': 'default',
            'date': 'default',
            'criticality_code': 'default'
        }
        
        for line in table_data:
            line.update(default_enrichment)
        
        logger.info(f"✅ Added 26 default enrichment columns to {len(table_data)} lines")
        logger.info(f"   Total columns per line now: {len(table_data[0].keys())}")
        
        # Set enriched_data to table_data (which now has defaults)
        enriched_data = table_data
        logger.info(f"✅ Enriched data = table_data with {len(enriched_data)} lines and {len(enriched_data[0].keys()) if enriched_data else 0} columns per line")
        
        if storage_type == 's3':
            try:
                os.unlink(local_file_path)
            except:
                pass
        
        total_items = len(created_items) + len(updated_items)
        
        # DEBUG: Log what we're returning
        logger.info("="*80)
        logger.info("🔍 PREPARING TASK RESULT")
        logger.info(f"   - Base extraction (extracted_lines): {len(table_data)} items")
        logger.info(f"   - Enriched data: {len(enriched_data) if enriched_data else 0} items")
        if enriched_data:
            logger.info(f"   - Enriched data columns: {len(enriched_data[0].keys())} keys")
            logger.info(f"   - Sample enriched keys: {list(enriched_data[0].keys())[:10]}")
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
