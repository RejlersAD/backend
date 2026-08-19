"""
Electrical Checklist API Views
Professional project-based system with AWS S3 integration
"""
import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import FileResponse, HttpResponse
from django.utils import timezone
import io

from .models import ChecklistExtractionJob, ChecklistProject
from .extraction_service import ChecklistExtractionService
from .excel_export import generate_excel_export
from .s3_service import get_s3_service
from .handwriting_extractor import HandwritingExtractor
from .template_mapper import map_extractions_to_template

logger = logging.getLogger(__name__)


class ChecklistExtractionViewSet(viewsets.ViewSet):
    """
    API endpoints for electrical checklist extraction
    Enhanced with project context and S3 storage
    
    Endpoints:
    - POST /electrical-checklist/extract/ - Upload and extract (requires project_id)
    - GET /electrical-checklist/{job_id}/status/ - Check status
    - GET /electrical-checklist/{job_id}/result/ - Get results
    - GET /electrical-checklist/{job_id}/download-excel/ - Download Excel
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['post'], url_path='extract')
    def extract_checklist(self, request):
        """
        Upload PDF checklist files and start AI extraction
        PROFESSIONAL VERSION: Requires project context, uploads to S3
        
        Request Body (multipart/form-data):
        - file_0, file_1, ... : PDF files
        - project_id: Project ID (REQUIRED)
        - template_id: Checklist template identifier (optional)
        - extract_signatures: Boolean (optional, default True)
        - requires_approval: Boolean (optional, default False)
        
        Returns:
        - job_id: Extraction job ID for polling
        - project_code: Project code
        - success: Boolean
        - message: Status message
        """
        try:
            # VALIDATE PROJECT ID (REQUIRED)
            project_id = request.data.get('project_id')
            if not project_id:
                return Response({
                    'success': False,
                    'message': 'project_id is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get and validate project
            try:
                project = ChecklistProject.objects.get(
                    id=project_id,
                    is_deleted=False
                )
                
                # Verify user has access to project
                if project.owner != request.user and not project.members.filter(id=request.user.id).exists():
                    return Response({
                        'success': False,
                        'message': 'You don\'t have access to this project'
                    }, status=status.HTTP_403_FORBIDDEN)
                    
            except ChecklistProject.DoesNotExist:
                return Response({
                    'success': False,
                    'message': 'Project not found'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Get uploaded files
            files = []
            file_index = 0
            while f'file_{file_index}' in request.FILES:
                files.append(request.FILES[f'file_{file_index}'])
                file_index += 1
            
            if not files:
                return Response({
                    'success': False,
                    'message': 'No files uploaded'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get parameters
            template_id = request.data.get('template_id', 'ups_battery_inspection')
            extract_signatures = request.data.get('extract_signatures', 'true').lower() == 'true'
            requires_approval = request.data.get('requires_approval', 'false').lower() == 'true'
            
            # Create extraction job with PROJECT CONTEXT
            job = ChecklistExtractionJob.objects.create(
                project=project,  # ✅ Professional: linked to project
                user=request.user,
                template_id=template_id,
                file_count=len(files),
                requires_approval=requires_approval,
                status='processing'
            )
            
            logger.info(f"[ChecklistAPI] Created job {job.id} for project {project.project_code}")
            
            # UPLOAD PDFs TO S3 (if enabled)
            s3_service = get_s3_service()
            pdf_s3_keys = []
            
            if project.settings.get('s3_storage', True):
                for idx, file in enumerate(files):
                    try:
                        upload_result = s3_service.upload_pdf(
                            file,
                            file.name,
                            project.project_code
                        )
                        
                        if upload_result['success']:
                            pdf_s3_keys.append(upload_result['s3_key'])
                            logger.info(f"[ChecklistAPI] ✅ Uploaded {file.name} to S3")
                        else:
                            logger.warning(f"[ChecklistAPI] ⚠️ S3 upload failed for {file.name}: {upload_result.get('error')}")
                            
                    except Exception as e:
                        logger.error(f"[ChecklistAPI] S3 upload error: {e}")
                
                # Save S3 keys to job
                job.pdf_s3_keys = pdf_s3_keys
                job.save()
            
            # Process files (extraction)
            try:
                service = ChecklistExtractionService()
                all_results = []
                
                for idx, file in enumerate(files):
                    logger.info(f"[ChecklistAPI] Processing file {idx+1}/{len(files)}")
                    job.progress = int((idx / len(files)) * 80)
                    job.save()
                    
                    # Reset file pointer
                    file.seek(0)
                    
                    result = service.extract_from_pdf(
                        file,
                        template_id=template_id,
                        extract_signatures=extract_signatures
                    )
                    all_results.append(result)
                
                # Merge results from multiple files
                merged_result = self._merge_extraction_results(all_results)
                
                # Update job with results
                job.status = 'completed'
                job.progress = 100
                job.fields_extracted = merged_result['fields_extracted']
                job.signatures_found = merged_result['signatures_found']
                job.confidence_score = merged_result['confidence_score']
                job.extracted_data = merged_result
                job.completed_at = timezone.now()
                job.save()
                
                # Update project statistics
                project.update_statistics()
                
                logger.info(f"[ChecklistAPI] ✅ Job {job.id} completed successfully")
                
                return Response({
                    'success': True,
                    'job_id': job.id,
                    'project_code': project.project_code,
                    'message': f'Extraction complete - {merged_result["fields_extracted"]} fields extracted'
                })
                
            except Exception as e:
                logger.error(f"[ChecklistAPI] Extraction failed: {e}", exc_info=True)
                job.status = 'failed'
                job.error_message = str(e)
                job.save()
                
                return Response({
                    'success': False,
                    'message': f'Extraction failed: {str(e)}'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        except Exception as e:
            logger.error(f"[ChecklistAPI] Request handling failed: {e}", exc_info=True)
            return Response({
                'success': False,
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['get'], url_path='status')
    def extraction_status(self, request, pk=None):
        """
        Check extraction job status
        
        Returns:
        - status: pending/processing/completed/failed
        - progress: 0-100
        - result: Extraction results (if completed)
        """
        try:
            job = ChecklistExtractionJob.objects.get(id=pk, user=request.user)
            
            response_data = {
                'job_id': job.id,
                'status': job.status,
                'progress': job.progress,
                'created_at': job.created_at.isoformat(),
                'updated_at': job.updated_at.isoformat()
            }
            
            if job.status == 'completed':
                response_data['result'] = {
                    'fields_extracted': job.fields_extracted,
                    'signatures_found': job.signatures_found,
                    'confidence_score': job.confidence_score,
                    'sections_completed': job.extracted_data.get('sections_completed', 0)
                }
            elif job.status == 'failed':
                response_data['error'] = job.error_message
            
            return Response(response_data)
            
        except ChecklistExtractionJob.DoesNotExist:
            return Response({
                'error': 'Job not found'
            }, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=True, methods=['get'], url_path='result')
    def extraction_result(self, request, pk=None):
        """
        Get full extraction results

        Returns complete extracted data including all fields and signatures.

        Access: any member of the job's project (owner, manager, engineer,
        viewer) may view it — not just the engineer who originally uploaded
        it. This matches the permission model used by download_excel so an
        inspection engineer can revisit any project teammate's past checklist.
        """
        try:
            job = ChecklistExtractionJob.objects.select_related('project').get(id=pk)

            if job.project_id and job.project.owner_id != request.user.id and not job.project.members.filter(id=request.user.id).exists():
                return Response({
                    'error': 'Access denied'
                }, status=status.HTTP_403_FORBIDDEN)
            elif not job.project_id and job.user_id != request.user.id:
                return Response({
                    'error': 'Access denied'
                }, status=status.HTTP_403_FORBIDDEN)

            if job.status != 'completed':
                return Response({
                    'error': 'Extraction not yet completed',
                    'status': job.status
                }, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'success': True,
                'job_id': job.id,
                'result': job.extracted_data
            })

        except ChecklistExtractionJob.DoesNotExist:
            return Response({
                'error': 'Job not found'
            }, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=True, methods=['get'], url_path='download-excel')
    def download_excel(self, request, pk=None):
        """
        Download extracted data as Excel file
        PROFESSIONAL VERSION: Uses S3 storage if enabled
        
        Generates Excel with all extracted fields and embedded signature images
        Uploads to S3 and returns presigned download URL
        """
        try:
            job = ChecklistExtractionJob.objects.get(id=pk)
            
            # Verify user has access to this job's project
            if job.project.owner != request.user and not job.project.members.filter(id=request.user.id).exists():
                return Response({
                    'error': 'Access denied'
                }, status=status.HTTP_403_FORBIDDEN)
            
            if job.status != 'completed':
                return Response({
                    'error': 'Extraction not yet completed'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Generate Excel file
            excel_file_path = generate_excel_export(job)
            
            # Upload to S3 if enabled
            s3_service = get_s3_service()
            use_s3 = job.project.settings.get('s3_storage', True)
            
            if use_s3:
                try:
                    # Read Excel file
                    with open(excel_file_path, 'rb') as f:
                        excel_data = f.read()
                    
                    # Upload to S3
                    upload_result = s3_service.upload_excel(
                        io.BytesIO(excel_data),
                        job.project.project_code,
                        job.id
                    )
                    
                    if upload_result['success']:
                        # Save S3 key to job
                        job.excel_s3_key = upload_result['s3_key']
                        job.excel_file_size = upload_result['size']
                        job.save()
                        
                        logger.info(f"[ChecklistAPI] ✅ Excel uploaded to S3: {upload_result['s3_key']}")
                        
                        # Return presigned download URL
                        return Response({
                            'success': True,
                            'download_url': upload_result['url'],
                            's3_key': upload_result['s3_key'],
                            'file_size': upload_result['size'],
                            'expires_in': 300  # 5 minutes
                        })
                    
                except Exception as s3_error:
                    logger.error(f"[ChecklistAPI] S3 upload failed, falling back to direct download: {s3_error}")
                    # Fall back to direct download
            
            # Direct download (if S3 disabled or failed)
            response = FileResponse(
                open(excel_file_path, 'rb'),
                as_attachment=True,
                filename=f'{job.project.project_code}_checklist_{job.id}.xlsx',
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            return response
            
        except ChecklistExtractionJob.DoesNotExist:
            return Response({
                'error': 'Job not found'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"[ChecklistAPI] Excel generation failed: {e}", exc_info=True)
            return Response({
                'error': f'Failed to generate Excel: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    # ─────────────────────────────────────────────────────────────────────────────────
    # HANDWRITING EXTRACTION (new pipeline)
    # ─────────────────────────────────────────────────────────────────────────────────
    # COST-OPTIMISED: Tesseract OCR first (FREE), OpenAI Vision only on escalation.
    # SUPPORTS BYOK: Accepts optional user-supplied OpenAI API key from the request.
    #                Key is NEVER logged or persisted — held in memory only.
    # DOES NOT MODIFY: extraction_service.ChecklistExtractionService (core logic preserved)
    # ─────────────────────────────────────────────────────────────────────────────────
    @action(detail=False, methods=['post'], url_path='extract-handwriting')
    def extract_handwriting(self, request):
        """
        Extract handwritten inspection data from PDF(s) and map to the 6-column template.

        Request Body (multipart/form-data):
            - file_0, file_1, ...     : PDF files (one per inspection engineer visit)
            - project_id              : REQUIRED
            - openai_api_key          : OPTIONAL — user's own key for BYOK Vision fallback
            - engineer_name           : OPTIONAL — attribution for extracted data
            - template_id             : OPTIONAL — defaults to 'ups_battery_inspection'
            - requires_approval       : OPTIONAL — defaults to False

        Returns:
            - job_id, project_code, method_used, checklist_data (6-col dict),
              summary { fields_extracted, sections_completed, confidence_score, ... }
        """
        try:
            # ── Validate project ────────────────────────────────────────────────
            project_id = request.data.get('project_id')
            if not project_id:
                return Response({'success': False, 'message': 'project_id is required'},
                                status=status.HTTP_400_BAD_REQUEST)
            try:
                project = ChecklistProject.objects.get(id=project_id, is_deleted=False)
            except ChecklistProject.DoesNotExist:
                return Response({'success': False, 'message': 'Project not found'},
                                status=status.HTTP_404_NOT_FOUND)

            if project.owner != request.user and not project.members.filter(id=request.user.id).exists():
                return Response({'success': False, 'message': "You don't have access to this project"},
                                status=status.HTTP_403_FORBIDDEN)

            # ── Collect files ───────────────────────────────────────────────────
            files = []
            file_index = 0
            while f'file_{file_index}' in request.FILES:
                files.append(request.FILES[f'file_{file_index}'])
                file_index += 1
            if not files:
                return Response({'success': False, 'message': 'No files uploaded'},
                                status=status.HTTP_400_BAD_REQUEST)

            # ── Read params (BYOK key is stripped from logs) ───────────────────────
            user_api_key   = (request.data.get('openai_api_key') or '').strip() or None
            engineer_name  = (request.data.get('engineer_name')  or request.user.get_full_name()
                              or request.user.username or '').strip()
            template_id    = request.data.get('template_id', 'ups_battery_inspection')
            requires_approval = str(request.data.get('requires_approval', 'false')).lower() == 'true'
            # Optional user-supplied label to tell checklists apart in history
            # (e.g. "Q1 Site Visit"). Falls back to an auto-generated name
            # (Checklist #<id> — <date>) if left blank — see serializers.py.
            checklist_name = (request.data.get('checklist_name') or '').strip() or None
            # Soft-coded extraction mode: fast | balanced | deep | vision_only.
            # Unknown / missing values fall back to the module default inside the extractor.
            extraction_mode = (request.data.get('extraction_mode') or '').strip().lower() or None

            # ── Create job ──────────────────────────────────────────────────────
            job = ChecklistExtractionJob.objects.create(
                project=project,
                user=request.user,
                template_id=template_id,
                file_count=len(files),
                requires_approval=requires_approval,
                status='processing',
            )
            logger.info(
                "[Handwriting] Job %s created for project %s (files=%d, engineer=%s, key=%s, mode=%s)",
                job.id, project.project_code, len(files), engineer_name,
                'user_supplied' if user_api_key else 'platform',
                extraction_mode or 'default',
            )

            # ── Upload PDFs to S3 (best-effort) ─────────────────────────────────────
            s3_service = get_s3_service()
            pdf_s3_keys = []
            if project.settings.get('s3_storage', True):
                for f in files:
                    try:
                        upload_result = s3_service.upload_pdf(f, f.name, project.project_code)
                        if upload_result.get('success'):
                            pdf_s3_keys.append(upload_result['s3_key'])
                    except Exception as s3_exc:
                        logger.warning("[Handwriting] S3 upload failed for %s: %s", f.name, s3_exc)
                job.pdf_s3_keys = pdf_s3_keys
                job.save(update_fields=['pdf_s3_keys'])

            # ── Run extraction (OCR first, Vision escalation with BYOK) ──────────────
            try:
                extractor = HandwritingExtractor(
                    user_openai_api_key=user_api_key,
                    extraction_mode=extraction_mode,
                )
                extraction_results = []
                for idx, f in enumerate(files):
                    job.progress = int((idx / max(1, len(files))) * 80)
                    job.save(update_fields=['progress'])
                    f.seek(0)
                    extraction_results.append(extractor.extract(f, source_file_name=f.name))

                mapped = map_extractions_to_template(
                    extraction_results,
                    engineer_name=engineer_name,
                )

                # Persist — note: user_api_key is NEVER written to the job.
                extracted_data = {
                    'template_id':      template_id,
                    'checklist_data':   mapped['checklist_data'],
                    'summary':          mapped['summary'],
                    'sources':          mapped['sources'],
                    'engineer_name':    engineer_name,
                    'checklist_name':   checklist_name,
                    'key_source':       extractor.key_source,
                    'key_status':       extractor.key_status,
                    'method':           'handwriting',
                    # Exact $ cost of this job's OpenAI Vision API usage, computed
                    # from real token counts × soft-coded per-model pricing table
                    # (template_v2_config.OPENAI_VISION_PRICING_PER_1M_TOKENS).
                    # 0.0 when extraction stayed on free OCR only (e.g. "fast" mode).
                    'cost_usd':         extractor.usage_cost_usd,
                    'usage_tokens':     extractor.usage_tokens,
                }
                job.status              = 'completed'
                job.progress            = 100
                job.fields_extracted    = mapped['summary']['fields_extracted']
                job.signatures_found    = mapped['summary'].get('signatures_found', 0)
                job.confidence_score    = mapped['summary']['confidence_score']
                job.extracted_data      = extracted_data
                job.completed_at        = timezone.now()
                job.save()

                project.update_statistics()

                logger.info(
                    "[Handwriting] Job %s completed — %d/%d fields, %d sections, avg conf %d",
                    job.id,
                    mapped['summary']['fields_extracted'],
                    mapped['summary']['total_fields'],
                    mapped['summary']['sections_completed'],
                    mapped['summary']['confidence_score'],
                )

                return Response({
                    'success':         True,
                    'job_id':          job.id,
                    'project_code':    project.project_code,
                    'checklist_data':  mapped['checklist_data'],
                    'summary':         mapped['summary'],
                    'sources':         mapped['sources'],
                    'key_source':      extractor.key_source,
                    'key_status':      extractor.key_status,
                    'extraction_mode': extractor.extraction_mode,
                    'message':         f"Extracted {mapped['summary']['fields_extracted']} fields",
                })

            except Exception as exc:
                logger.error("[Handwriting] Extraction failed for job %s: %s", job.id, exc, exc_info=True)
                job.status = 'failed'
                job.error_message = str(exc)
                job.save(update_fields=['status', 'error_message'])
                return Response({'success': False, 'message': f'Extraction failed: {exc}'},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as exc:
            logger.error("[Handwriting] Request failed: %s", exc, exc_info=True)
            return Response({'success': False, 'message': str(exc)},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _merge_extraction_results(self, results):
        """Merge results from multiple PDF files"""
        if not results:
            return {}
        
        if len(results) == 1:
            return results[0]
        
        # Merge logic: take highest confidence value for each field
        merged = {
            'template_id': results[0]['template_id'],
            'template_name': results[0]['template_name'],
            'fields_extracted': 0,
            'sections_completed': 0,
            'signatures_found': 0,
            'confidence_score': 0,
            'extracted_data': {},
            'signatures': [],
            'metadata': {
                'files_processed': len(results),
                'pages_processed': sum(r['metadata']['pages_processed'] for r in results)
            }
        }
        
        # Merge fields (keep highest confidence)
        for result in results:
            for field_key, field_data in result['extracted_data'].items():
                if field_key not in merged['extracted_data']:
                    merged['extracted_data'][field_key] = field_data
                elif field_data.get('confidence', 0) > merged['extracted_data'][field_key].get('confidence', 0):
                    merged['extracted_data'][field_key] = field_data
        
        # Merge signatures
        for result in results:
            merged['signatures'].extend(result.get('signatures', []))
        
        # Recalculate stats
        merged['fields_extracted'] = len(merged['extracted_data'])
        merged['signatures_found'] = len(merged['signatures'])
        
        confidences = [f.get('confidence', 0) for f in merged['extracted_data'].values()]
        merged['confidence_score'] = round(sum(confidences) / len(confidences), 1) if confidences else 0
        
        return merged
