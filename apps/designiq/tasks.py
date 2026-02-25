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
        
        if hmb_file or pms_file or nace_file:
            try:
                logger.info("=" * 80)
                logger.info("🚀 STEP 2: Running intelligent enrichment (commit 8f82346 logic)")
                logger.info("=" * 80)
                
                # Import enrichment service (from commit 8f82346)
                from designiq.services.enrichment_service import EnrichmentService
                enrichment_service = EnrichmentService()
                
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
                
                # Enrich with 26 additional columns
                enriched_data = enrichment_service.enrich_lines(
                    base_lines=table_data,  # LOCKED base 9 columns
                    hmb_text=hmb_text,
                    pms_text=pms_text,
                    nace_text=nace_text
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
        
        # Use enriched data for database saving
        table_data = enriched_data
        
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
                
                # Add enrichment columns dynamically if present (26 additional columns)
                enrichment_keys = [
                    'flow_medium', 'two_phase', 'surge_flow', 'flow_max', 'density',
                    'normal_pressure', 'normal_temp', 'design_pressure', 'minimax_design_temp',
                    'design_code', 'category_m_fluid', 'schedule_wall_thk', 'stress_relief',
                    'pwht', 'rt', 'mt_pt', 'hardness', 'visual', 'nace_mr_0175',
                    'piping_rated_pressure_ambient', 'test_pressure', 'test_medium',
                    'pid_no', 'pid_rev', 'date', 'criticality_code'
                ]
                for key in enrichment_keys:
                    if key in line_data:
                        data_dict[key] = line_data[key]
                
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
