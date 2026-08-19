"""
Smart Datasheet View
API endpoint for unified smart datasheet generation
"""

import logging
import uuid
import threading
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from .smart_datasheet_orchestrator import SmartDatasheetOrchestrator, process_smart_datasheet_async

logger = logging.getLogger(__name__)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_datasheet_upload(request):
    """
    Smart unified datasheet upload endpoint
    User selects datasheet type, then uploads appropriate files
    """
    try:
        logger.info("[Smart Datasheet] Received upload request")
        
        # Get selected datasheet type (user choice)
        selected_type = request.POST.get('datasheet_type', '')
        logger.info(f"[Smart Datasheet] User selected type: {selected_type}")
        
        if not selected_type:
            return JsonResponse({
                'success': False,
                'error': 'Please select a datasheet type.'
            }, status=400)
        
        # Validate datasheet type
        valid_types = ['mov_equipment', 'sdv_streams', 'pressure_instrument', 'pump_hydraulic']
        if selected_type not in valid_types:
            return JsonResponse({
                'success': False,
                'error': f'Invalid datasheet type. Must be one of: {", ".join(valid_types)}'
            }, status=400)
        
        # Get uploaded files based on selected type
        pid_file = request.FILES.get('pid_file')
        hmb_file = request.FILES.get('hmb_file')
        linelist_file = request.FILES.get('linelist_file')
        other_file = request.FILES.get('other_file')
        
        # Validate files based on datasheet type
        if selected_type in ['mov_equipment', 'sdv_streams']:
            if not pid_file:
                return JsonResponse({
                    'success': False,
                    'error': 'P&ID file is required for this datasheet type.'
                }, status=400)
            if not hmb_file:
                return JsonResponse({
                    'success': False,
                    'error': 'HMB file is required for this datasheet type.'
                }, status=400)
                
        elif selected_type == 'pressure_instrument':
            if not pid_file:
                return JsonResponse({
                    'success': False,
                    'error': 'P&ID file is required for Pressure Instrument datasheet.'
                }, status=400)
                
        elif selected_type == 'pump_hydraulic':
            if not other_file:
                return JsonResponse({
                    'success': False,
                    'error': 'Pump data file is required for Pump Hydraulic calculation.'
                }, status=400)
        
        logger.info(f"[Smart Datasheet] Files received - P&ID: {bool(pid_file)}, HMB: {bool(hmb_file)}, Line List: {bool(linelist_file)}, Other: {bool(other_file)}")
        
        # Get user preferences
        user_preferences = {
            'selected_type': selected_type,
            'datasheet_types': [selected_type],  # Only process selected type
            'auto_detect': False  # User manually selected, no auto-detection
        }
        
        # Generate job ID
        job_id = str(uuid.uuid4())
        
        # Initialize cache
        cache.set(f'smart_job_{job_id}', {
            'status': 'processing',
            'progress': 0,
            'stage': 'Starting...'
        }, timeout=3600)
        
        # Save files to temporary location for processing
        temp_files = []
        import tempfile
        
        if pid_file:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            temp_file.write(pid_file.read())
            temp_file.close()
            temp_files.append({
                'path': temp_file.name,
                'name': pid_file.name,
                'type': 'pid'
            })
        
        if hmb_file:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            temp_file.write(hmb_file.read())
            temp_file.close()
            temp_files.append({
                'path': temp_file.name,
                'name': hmb_file.name,
                'type': 'hmb'
            })
        
        if linelist_file:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            temp_file.write(linelist_file.read())
            temp_file.close()
            temp_files.append({
                'path': temp_file.name,
                'name': linelist_file.name,
                'type': 'linelist'
            })
            
        if other_file:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            temp_file.write(other_file.read())
            temp_file.close()
            temp_files.append({
                'path': temp_file.name,
                'name': other_file.name,
                'type': 'other'
            })
        
        # Start async processing
        def process_async():
            # Pass temp file paths and names directly
            files_info = {}
            for temp_info in temp_files:
                files_info[temp_info['type']] = {
                    'path': temp_info['path'],
                    'name': temp_info['name']
                }
            
            process_smart_datasheet_async(files_info, user_preferences, job_id)
        
        thread = threading.Thread(target=process_async)
        thread.start()
        
        logger.info(f"[Smart Datasheet] Processing started with job_id: {job_id}")
        
        return JsonResponse({
            'success': True,
            'job_id': job_id,
            'message': 'Smart datasheet processing started',
            'datasheet_type': selected_type,
            'file_count': len(temp_files)
        })
        
    except Exception as e:
        logger.error(f"[Smart Datasheet] Upload error: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def smart_datasheet_status(request, job_id):
    """
    Check status of smart datasheet generation job
    """
    try:
        job_data = cache.get(f'smart_job_{job_id}')
        
        if not job_data:
            return JsonResponse({
                'success': False,
                'error': 'Job not found or expired'
            }, status=404)
        
        return JsonResponse({
            'success': True,
            'status': job_data.get('status'),
            'progress': job_data.get('progress', 0),
            'stage': job_data.get('stage', ''),
            'result': job_data.get('result'),
            'error': job_data.get('error')
        })
        
    except Exception as e:
        logger.error(f"[Smart Datasheet] Status check error: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@csrf_exempt
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def smart_datasheet_preview(request):
    """
    Get preview of what datasheets will be generated based on uploaded files
    (without actually processing them)
    """
    try:
        uploaded_files = []
        for key in request.FILES:
            file = request.FILES[key]
            uploaded_files.append(file)
        
        if not uploaded_files:
            return JsonResponse({
                'success': False,
                'error': 'No files uploaded'
            }, status=400)
        
        orchestrator = SmartDatasheetOrchestrator()
        detected_docs = orchestrator.detect_document_types(uploaded_files)
        datasheet_types = orchestrator.determine_datasheet_types(detected_docs)
        
        type_names = {
            'mov_equipment': 'MOV Equipment Datasheets',
            'sdv_streams': 'SDV Stream Datasheets',
            'pressure_instrument': 'Pressure Instrument Datasheets',
            'pump_hydraulic': 'Pump Hydraulic Calculation'
        }
        
        return JsonResponse({
            'success': True,
            'detected_docs': detected_docs,
            'will_generate': [type_names.get(t, t) for t in datasheet_types],
            'datasheet_types': datasheet_types
        })
        
    except Exception as e:
        logger.error(f"[Smart Datasheet] Preview error: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
