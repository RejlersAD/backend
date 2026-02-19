"""
Pressure Instrument P&ID Analysis API Views

Handles P&ID uploads, analysis, and Excel datasheet generation for pressure instruments.
Uses soft coding techniques for easy configuration and extensibility.
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.http import HttpResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
import logging
import traceback
from datetime import datetime

from .pressure_instrument_service import PressureInstrumentAnalyzer

logger = logging.getLogger(__name__)

# Soft-coded configuration
PRESSURE_INSTRUMENT_CONFIG = {
    'max_file_size_mb': 50,
    'allowed_extensions': ['pdf', 'png', 'jpg', 'jpeg', 'dwg', 'tif', 'tiff'],
    'require_authentication': False,  # Set to True for production
    'enable_detailed_logging': True,
    'default_project_name': 'Default Project',
    'default_revision': 'A'
}

def safe_execute(func):
    """Decorator for comprehensive error handling"""
    def wrapper(*args, **kwargs):
        try:
            logger.info(f"[PressureInstrument] Executing {func.__name__}")
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"[PressureInstrument] Error in {func.__name__}: {str(e)}")
            logger.error(f"[PressureInstrument] Traceback: {traceback.format_exc()}")
            request = args[0] if args else None
            return Response({
                'error': 'Internal server error',
                'message': str(e),
                'details': traceback.format_exc() if settings.DEBUG else None
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return wrapper


@api_view(['POST'])
@permission_classes([AllowAny])  # Flexible authentication
@csrf_exempt
@safe_execute
def analyze_pid_for_pressure_instruments(request):
    """
    Analyze P&ID diagram and extract pressure instrument data.
    
    Expected multipart/form-data:
    - file: P&ID file (PDF, PNG, JPG, etc.)
    - drawing_number: Drawing identification number
    - drawing_title: Optional drawing title
    - revision: Optional revision number
    - project_name: Optional project name
    - area: Optional process area
    - download_excel: Optional boolean to download Excel directly
    
    Returns:
    - instruments: Array of detected instruments
    - excel_url: URL to download Excel (if requested)
    - message: Success message
    """
    try:
        # Validate request
        if 'file' not in request.FILES:
            return Response(
                {'error': 'No P&ID file provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        pid_file = request.FILES['file']
        
        # Extract drawing information
        drawing_info = {
            'drawing_number': request.data.get('drawing_number', ''),
            'drawing_title': request.data.get('drawing_title', 'Pressure Instrument Analysis'),
            'revision': request.data.get('revision', 'A'),
            'project_name': request.data.get('project_name', 'Default Project'),
            'area': request.data.get('area', ''),
            'date': datetime.now().strftime('%Y-%m-%d')
        }
        
        # Auto-generate drawing number if not provided
        if not drawing_info['drawing_number'] or drawing_info['drawing_number'] == 'AUTO':
            drawing_info['drawing_number'] = f"PI-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        # Validate file type
        allowed_extensions = ['pdf', 'png', 'jpg', 'jpeg', 'dwg']
        file_extension = pid_file.name.split('.')[-1].lower()
        if file_extension not in allowed_extensions:
            return Response(
                {'error': f'Unsupported file format. Allowed: {", ".join(allowed_extensions)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate file size (max 50MB)
        max_size = 50 * 1024 * 1024  # 50MB
        if pid_file.size > max_size:
            return Response(
                {'error': 'File size exceeds 50MB limit'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logger.info(f"[PressureInstrumentAPI] Processing P&ID: {pid_file.name} ({pid_file.size} bytes)")
        logger.info(f"[PressureInstrumentAPI] Drawing: {drawing_info['drawing_number']}")
        
        # Initialize analyzer
        analyzer = PressureInstrumentAnalyzer()
        
        # Generate datasheet
        excel_file, instruments, message = analyzer.generate_datasheet_from_pid(
            pid_file,
            drawing_info
        )
        
        if not excel_file:
            return Response(
                {
                    'warning': message,
                    'instruments': [],
                    'instruments_detected': 0
                },
                status=status.HTTP_200_OK
            )
        
        # Check if direct download is requested
        download_excel = request.data.get('download_excel', 'false').lower() == 'true'
        
        if download_excel:
            # Return Excel file directly
            response = HttpResponse(
                excel_file.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            filename = f"Pressure_Instruments_{drawing_info['drawing_number']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            logger.info(f"[PressureInstrumentAPI] Returning Excel file: {filename}")
            return response
        else:
            # Return JSON response with instrument data
            # Store Excel temporarily and provide download link
            response_data = {
                'success': True,
                'message': message,
                'instruments': instruments,
                'instruments_detected': len(instruments),
                'drawing_info': drawing_info,
                'excel_generated': True
            }
            
            logger.info(f"[PressureInstrumentAPI] Analysis complete: {len(instruments)} instruments detected")
            return Response(response_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"[PressureInstrumentAPI] Error: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Analysis failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([AllowAny])
@safe_execute
def download_pressure_instrument_excel(request):
    """
    Generate and download Excel datasheet from provided instrument data.
    
    Expected JSON body:
    - instruments: Array of instrument data
    - drawing_info: Drawing metadata
    
    Returns:
    - Excel file download
    """
    try:
        instruments = request.data.get('instruments', [])
        drawing_info = request.data.get('drawing_info', {})
        
        if not instruments:
            return Response(
                {'error': 'No instrument data provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logger.info(f"[PressureInstrumentAPI] Generating Excel for {len(instruments)} instruments")
        
        # Initialize analyzer
        analyzer = PressureInstrumentAnalyzer()
        
        # Generate Excel
        excel_file = analyzer.populate_excel_datasheet(instruments, drawing_info)
        
        # Return Excel file with standardized filename
        response = HttpResponse(
            excel_file.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        filename = 'Pressure Instrument Data Sheet.xlsx'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        logger.info(f"[PressureInstrumentAPI] Excel generated: {filename}")
        return response
        
    except Exception as e:
        logger.error(f"[PressureInstrumentAPI] Excel generation error: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Excel generation failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([AllowAny])
@safe_execute
def get_instrument_types(request):
    """
    Get list of supported pressure instrument types.
    
    Returns:
    - instrument_types: Dictionary of instrument type configurations
    """
    try:
        analyzer = PressureInstrumentAnalyzer()
        
        response_data = {
            'instrument_types': analyzer.INSTRUMENT_TYPES,
            'total_types': len(analyzer.INSTRUMENT_TYPES)
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"[PressureInstrumentAPI] Error retrieving instrument types: {str(e)}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
