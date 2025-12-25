"""
Enhanced PFD Converter Views
API endpoints for AI-assisted PFD to P&ID conversion
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from io import BytesIO
import json
import logging

from .services_enhanced import EnhancedPFDToPIDConverter
from .models import PFDDocument, PIDConversion
from apps.rbac.permissions import HasModuleAccess

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ai_assisted_conversion(request):
    """
    AI-Assisted PFD to P&ID Conversion (6-Step Workflow)
    
    POST /api/v1/pfd/ai-assisted-conversion/
    
    Request:
        - pfd_file: PFD image/PDF file
        - project_name: Project name
        - project_number: Project number
        - drawing_number: Drawing number
        - engineering_phase: CONCEPT/FEED/DETAILED
        - standards: ISA-5.1, ISO 10628, etc.
        - units: SI/Imperial
        
    Response:
        - conversion_id: UUID
        - pid_pdf_url: Download URL for P&ID PDF
        - assumptions_report: Text report
        - instrument_list: JSON array
        - valve_list: JSON array
        - validation_results: JSON object
        - status: success/failed
    """
    try:
        logger.info(f"[AI-CONVERSION] Request from user: {request.user.username}")
        
        # Validate request
        if 'pfd_file' not in request.FILES:
            return Response({
                'error': 'PFD file is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        pfd_file = request.FILES['pfd_file']
        
        # Get project info
        project_info = {
            'project_name': request.data.get('project_name', 'Unnamed Project'),
            'project_number': request.data.get('project_number', 'N/A'),
            'drawing_number': request.data.get('drawing_number', 'AUTO-GENERATED'),
            'engineering_phase': request.data.get('engineering_phase', 'CONCEPT'),
            'standards': request.data.get('standards', 'ISA-5.1, ISO 10628'),
            'units': request.data.get('units', 'SI'),
            'language': request.data.get('language', 'English')
        }
        
        logger.info(f"[AI-CONVERSION] Project: {project_info['project_name']}")
        
        # Initialize converter
        converter = EnhancedPFDToPIDConverter()
        
        # Run 6-step conversion
        logger.info("[AI-CONVERSION] Starting 6-step conversion workflow...")
        result = converter.convert_pfd_to_pid(pfd_file, project_info)
        
        if not result['success']:
            return Response({
                'error': 'Conversion failed',
                'details': result.get('error', 'Unknown error')
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Save to database
        pfd_document = PFDDocument.objects.create(
            document_number=project_info['drawing_number'],
            document_title=f"{project_info['project_name']} - PFD",
            project_name=project_info['project_name'],
            project_code=project_info['project_number'],
            uploaded_file=pfd_file,
            uploaded_by=request.user,
            status='processed'
        )
        
        pid_conversion = PIDConversion.objects.create(
            pfd_document=pfd_document,
            conversion_method='ai_assisted_6step',
            status='completed',
            conversion_data={
                'pfd_analysis': result['pfd_analysis'],
                'process_model': result['process_model'],
                'instrumentation': result['instrumentation'],
                'validation_results': result['validation_results']
            },
            assumptions_report=result['assumptions_report'],
            instrument_list=json.dumps(result['instrument_list']),
            valve_list=json.dumps(result['valve_list'])
        )
        
        # Store PDF (in production, upload to S3)
        pid_conversion.pid_pdf = result['pid_pdf']
        pid_conversion.save()
        
        logger.info(f"[AI-CONVERSION] Conversion completed - ID: {pid_conversion.id}")
        
        # Return response
        return Response({
            'success': True,
            'conversion_id': str(pid_conversion.id),
            'pfd_document_id': str(pfd_document.id),
            'project_info': project_info,
            'conversion_summary': {
                'equipment_count': len(result['pfd_analysis'].get('equipment', [])),
                'instruments_suggested': result['instrumentation'].get('instrumentation_summary', {}).get('total_instruments_suggested', 0),
                'valves_suggested': result['instrumentation'].get('instrumentation_summary', {}).get('total_valves_suggested', 0),
                'validation_status': result['validation_results'].get('compliance_summary', {}).get('overall_status', 'unknown')
            },
            'downloads': {
                'pid_pdf': f'/api/v1/pfd/download-pid/{pid_conversion.id}/',
                'assumptions_report': f'/api/v1/pfd/download-assumptions/{pid_conversion.id}/',
                'instrument_list': f'/api/v1/pfd/download-instruments/{pid_conversion.id}/',
                'valve_list': f'/api/v1/pfd/download-valves/{pid_conversion.id}/'
            },
            'instrument_list': result['instrument_list'],
            'valve_list': result['valve_list'],
            'validation_results': result['validation_results'],
            'assumptions_report': result['assumptions_report']
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        logger.error(f"[AI-CONVERSION] Error: {str(e)}")
        return Response({
            'error': 'Conversion failed',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_pid_pdf(request, conversion_id):
    """
    Download generated P&ID PDF
    
    GET /api/v1/pfd/download-pid/{conversion_id}/
    """
    try:
        conversion = PIDConversion.objects.get(id=conversion_id)
        
        # Check permissions
        if not request.user.is_staff and conversion.pfd_document.uploaded_by != request.user:
            return Response({
                'error': 'Permission denied'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Return PDF
        if hasattr(conversion, 'pid_pdf') and conversion.pid_pdf:
            response = HttpResponse(conversion.pid_pdf, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="Draft_PID_{conversion.pfd_document.document_number}.pdf"'
            return response
        else:
            return Response({
                'error': 'P&ID PDF not found'
            }, status=status.HTTP_404_NOT_FOUND)
            
    except PIDConversion.DoesNotExist:
        return Response({
            'error': 'Conversion not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"[DOWNLOAD-PID] Error: {str(e)}")
        return Response({
            'error': 'Download failed',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_assumptions_report(request, conversion_id):
    """
    Download assumptions and flags report
    
    GET /api/v1/pfd/download-assumptions/{conversion_id}/
    """
    try:
        conversion = PIDConversion.objects.get(id=conversion_id)
        
        # Check permissions
        if not request.user.is_staff and conversion.pfd_document.uploaded_by != request.user:
            return Response({
                'error': 'Permission denied'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Return report
        response = HttpResponse(conversion.assumptions_report, content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename="PID_Assumptions_{conversion.pfd_document.document_number}.txt"'
        return response
        
    except PIDConversion.DoesNotExist:
        return Response({
            'error': 'Conversion not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"[DOWNLOAD-ASSUMPTIONS] Error: {str(e)}")
        return Response({
            'error': 'Download failed',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_instrument_list(request, conversion_id):
    """
    Download instrument list as JSON/CSV
    
    GET /api/v1/pfd/download-instruments/{conversion_id}/?format=json|csv
    """
    try:
        conversion = PIDConversion.objects.get(id=conversion_id)
        
        # Check permissions
        if not request.user.is_staff and conversion.pfd_document.uploaded_by != request.user:
            return Response({
                'error': 'Permission denied'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Get format
        format_type = request.GET.get('format', 'json')
        
        instruments = json.loads(conversion.instrument_list or '[]')
        
        if format_type == 'csv':
            import csv
            from io import StringIO
            
            output = StringIO()
            if instruments:
                writer = csv.DictWriter(output, fieldnames=instruments[0].keys())
                writer.writeheader()
                writer.writerows(instruments)
            
            response = HttpResponse(output.getvalue(), content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="Instrument_List_{conversion.pfd_document.document_number}.csv"'
            return response
        else:
            response = HttpResponse(json.dumps(instruments, indent=2), content_type='application/json')
            response['Content-Disposition'] = f'attachment; filename="Instrument_List_{conversion.pfd_document.document_number}.json"'
            return response
        
    except PIDConversion.DoesNotExist:
        return Response({
            'error': 'Conversion not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"[DOWNLOAD-INSTRUMENTS] Error: {str(e)}")
        return Response({
            'error': 'Download failed',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_valve_list(request, conversion_id):
    """
    Download valve list as JSON/CSV
    
    GET /api/v1/pfd/download-valves/{conversion_id}/?format=json|csv
    """
    try:
        conversion = PIDConversion.objects.get(id=conversion_id)
        
        # Check permissions
        if not request.user.is_staff and conversion.pfd_document.uploaded_by != request.user:
            return Response({
                'error': 'Permission denied'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Get format
        format_type = request.GET.get('format', 'json')
        
        valves = json.loads(conversion.valve_list or '[]')
        
        if format_type == 'csv':
            import csv
            from io import StringIO
            
            output = StringIO()
            if valves:
                writer = csv.DictWriter(output, fieldnames=valves[0].keys())
                writer.writeheader()
                writer.writerows(valves)
            
            response = HttpResponse(output.getvalue(), content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="Valve_List_{conversion.pfd_document.document_number}.csv"'
            return response
        else:
            response = HttpResponse(json.dumps(valves, indent=2), content_type='application/json')
            response['Content-Disposition'] = f'attachment; filename="Valve_List_{conversion.pfd_document.document_number}.json"'
            return response
        
    except PIDConversion.DoesNotExist:
        return Response({
            'error': 'Conversion not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"[DOWNLOAD-VALVES] Error: {str(e)}")
        return Response({
            'error': 'Download failed',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def conversion_status(request, conversion_id):
    """
    Get conversion status and details
    
    GET /api/v1/pfd/conversion-status/{conversion_id}/
    """
    try:
        conversion = PIDConversion.objects.get(id=conversion_id)
        
        # Check permissions
        if not request.user.is_staff and conversion.pfd_document.uploaded_by != request.user:
            return Response({
                'error': 'Permission denied'
            }, status=status.HTTP_403_FORBIDDEN)
        
        return Response({
            'conversion_id': str(conversion.id),
            'pfd_document': {
                'id': str(conversion.pfd_document.id),
                'document_number': conversion.pfd_document.document_number,
                'project_name': conversion.pfd_document.project_name
            },
            'status': conversion.status,
            'conversion_method': conversion.conversion_method,
            'created_at': conversion.created_at.isoformat(),
            'completed_at': conversion.completed_at.isoformat() if conversion.completed_at else None,
            'validation_results': conversion.conversion_data.get('validation_results', {}) if conversion.conversion_data else {},
            'has_pid_pdf': bool(conversion.pid_pdf),
            'has_assumptions_report': bool(conversion.assumptions_report),
            'has_instrument_list': bool(conversion.instrument_list),
            'has_valve_list': bool(conversion.valve_list),
            'downloads': {
                'pid_pdf': f'/api/v1/pfd/download-pid/{conversion.id}/',
                'assumptions_report': f'/api/v1/pfd/download-assumptions/{conversion.id}/',
                'instrument_list': f'/api/v1/pfd/download-instruments/{conversion.id}/',
                'valve_list': f'/api/v1/pfd/download-valves/{conversion.id}/'
            }
        })
        
    except PIDConversion.DoesNotExist:
        return Response({
            'error': 'Conversion not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"[CONVERSION-STATUS] Error: {str(e)}")
        return Response({
            'error': 'Failed to get status',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
