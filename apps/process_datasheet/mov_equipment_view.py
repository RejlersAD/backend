"""
MOV Equipment Extraction View
Handles P&ID + HMB upload and generates filled datasheets for Motor Operated Valves (ASYNC)
"""
import logging
import json
import base64
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.core.files.base import ContentFile
from django.http import FileResponse, JsonResponse
from django.core.cache import cache
import tempfile
import os

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def extract_mov_equipment(request):
    """
    AI-Orchestrated MOV Datasheet Generation (ASYNC)
    
    POST /api/v1/process-datasheet/datasheets/extract-mov-equipment/
    
    Body (multipart/form-data):
        - pid_file: P&ID PDF file (required)
        - hmb_file: HMB PDF file (required)
        - other_doc: Optional additional document
        - equipment_type: 'mov_equipment' (required)
    
    Returns:
        - Job ID for background processing (immediate response)
    """
    try:
        logger.info(f"[MOV Equipment] Request from user: {request.user.email}")
        
        # Get uploaded files
        pid_file = request.FILES.get('pid_file')
        hmb_file = request.FILES.get('hmb_file')
        other_doc = request.FILES.get('other_doc')  # Optional
        equipment_type = request.data.get('equipment_type', '')
        
        logger.info(f"[MOV Equipment] Files received - P&ID: {bool(pid_file)}, HMB: {bool(hmb_file)}, Other: {bool(other_doc)}")
        
        # Validate P&ID file (required)
        if not pid_file:
            return Response(
                {'error': 'P&ID file (pid_file) is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate HMB file (required)
        if not hmb_file:
            return Response(
                {'error': 'HMB file (hmb_file) is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate equipment type
        if equipment_type != 'mov_equipment':
            return Response(
                {'error': 'equipment_type must be "mov_equipment"'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate file types
        if not pid_file.name.lower().endswith('.pdf'):
            return Response(
                {'error': 'P&ID file must be PDF'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not hmb_file.name.lower().endswith('.pdf'):
            return Response(
                {'error': 'HMB file must be PDF'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if other_doc and not other_doc.name.lower().endswith('.pdf'):
            return Response(
                {'error': 'Other document must be PDF'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate file sizes (50MB max each)
        if pid_file.size > 50 * 1024 * 1024:
            return Response(
                {'error': 'P&ID file exceeds 50MB limit'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if hmb_file.size > 50 * 1024 * 1024:
            return Response(
                {'error': 'HMB file exceeds 50MB limit'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if other_doc and other_doc.size > 50 * 1024 * 1024:
            return Response(
                {'error': 'Other document exceeds 50MB limit'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logger.info("[MOV Equipment] ✅ Validation passed, starting async processing...")
        
        # Save files to temp location
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as pid_temp:
            for chunk in pid_file.chunks():
                pid_temp.write(chunk)
            pid_temp_path = pid_temp.name
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as hmb_temp:
            for chunk in hmb_file.chunks():
                hmb_temp.write(chunk)
            hmb_temp_path = hmb_temp.name
        
        # Start async task using threading (works with or without Celery)
        from apps.process_datasheet.mov_threading_processor import start_async_processing
        
        job_id = start_async_processing(
            pid_file_path=pid_temp_path,
            hmb_file_path=hmb_temp_path,
            pid_filename=pid_file.name,
            user_email=request.user.email if hasattr(request.user, 'email') else 'anonymous'
        )
        
        logger.info(f"[MOV Equipment] ✅ Job started: {job_id}")
        
        # Return job ID immediately
        return JsonResponse({
            'success': True,
            'job_id': job_id,
            'status': 'processing',
            'message': 'Processing started. This may take 2-5 minutes for Vision AI extraction.'
        })
        
    except Exception as e:
        logger.error(f"[MOV Equipment] ❌ Error: {e}", exc_info=True)
        return Response(
            {'error': f'MOV equipment extraction failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_mov_job_status(request, job_id):
    """
    Check status of async MOV processing job
    
    GET /api/v1/process-datasheet/mov-job-status/<job_id>/
    
    Returns:
        - progress (0-100)
        - stage (current processing stage)
        - result (if complete)
    """
    try:
        # Get cached progress and result
        progress = cache.get(f'mov_task_{job_id}_progress', 0)
        stage = cache.get(f'mov_task_{job_id}_stage', 'Initializing...')
        result = cache.get(f'mov_task_{job_id}_result')
        
        if result:
            # Task complete
            return JsonResponse({
                'status': 'completed' if result.get('success') else 'failed',
                'progress': 100 if result.get('success') else 0,
                'stage': 'Complete' if result.get('success') else 'Error',
                'result': result
            })
        elif progress > 0:
            # Task in progress
            return JsonResponse({
                'status': 'processing',
                'progress': progress,
                'stage': stage
            })
        else:
            # No progress yet or invalid job_id
            return JsonResponse({
                'status': 'not_found',
                'progress': 0,
                'stage': 'Job not found or not started',
                'error': 'Invalid job_id or job expired'
            })
            
    except Exception as e:
        logger.error(f"[MOV Job Status] Error: {e}")
        return Response(
            {'error': f'Failed to check job status: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
