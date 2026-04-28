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

# Soft-coded post-extraction validator (additive — never modifies analyzer logic)
try:
    from .pressure_instrument_validator import validate_instruments as _validate_instruments
except Exception:  # pragma: no cover - validator is best-effort
    _validate_instruments = None

# Import ALL analyzers - use V3 by default, fallback to V2, then original
logger = logging.getLogger(__name__)

# Try to import V3 (Advanced with deep pressure extraction and AI recommendations)
USE_V3_ANALYZER = False
USE_V2_ANALYZER = False
USE_ORIGINAL_ANALYZER = False

try:
    from .pressure_instrument_service_v3 import AdvancedPressureInstrumentAnalyzer
    USE_V3_ANALYZER = True
    logger.info("[PressureInstrument] ✅ V3 Advanced Analyzer Available (Deep Extraction + AI Recommendations)")
except ImportError as e:
    logger.info(f"[PressureInstrument] V3 not available: {e}")

# Try V2 if V3 not available
if not USE_V3_ANALYZER:
    try:
        from .pressure_instrument_service_v2 import EnhancedPressureInstrumentAnalyzer
        USE_V2_ANALYZER = True
        logger.info("[PressureInstrument] ✅ V2 Enhanced Analyzer Available (Multi-Engine OCR)")
    except ImportError as e:
        logger.info(f"[PressureInstrument] V2 not available: {e}")

# Fallback to original
if not USE_V3_ANALYZER and not USE_V2_ANALYZER:
    try:
        from .pressure_instrument_service import PressureInstrumentAnalyzer
        USE_ORIGINAL_ANALYZER = True
        logger.warning("[PressureInstrument] ⚠️ Using original Vision-only analyzer (fallback)")
    except ImportError as e:
        logger.error(f"[PressureInstrument] ❌ No analyzer available: {e}")

# Soft-coded configuration
PRESSURE_INSTRUMENT_CONFIG = {
    'max_file_size_mb': 50,
    'allowed_extensions': ['pdf', 'png', 'jpg', 'jpeg', 'dwg', 'tif', 'tiff'],
    'require_authentication': False,  # Set to True for production
    'enable_detailed_logging': True,
    'default_project_name': 'Default Project',
    'default_revision': 'A',
    'analyzer_version': 'v3',  # 'v3' (recommended), 'v2', 'original'
    'use_enhanced_ocr': True,  # Enable/disable enhanced OCR
    'use_ai_recommendations': True  # Enable AI recommendations for missing data
}

def get_analyzer():
    """
    Factory function to get the appropriate analyzer.
    Priority: V3 > V2 > Original
    
    V3: Advanced with deep pressure extraction + AI-powered recommendations
    V2: Enhanced with multi-engine OCR
    Original: Vision-only analyzer
    """
    config_version = PRESSURE_INSTRUMENT_CONFIG.get('analyzer_version', 'v3')
    
    # If V3 requested and available
    if config_version == 'v3' and USE_V3_ANALYZER:
        logger.info("[PressureInstrument] 🚀 Using V3 Advanced Analyzer (Deep + AI)")
        return AdvancedPressureInstrumentAnalyzer()
    
    # If V2 requested or V3 not available
    if (config_version in ['v2', 'v3']) and USE_V2_ANALYZER:
        logger.info("[PressureInstrument] 📊 Using V2 Enhanced Analyzer (Multi-OCR)")
        return EnhancedPressureInstrumentAnalyzer()
    
    # Fallback to original
    if USE_ORIGINAL_ANALYZER:
        logger.info("[PressureInstrument] 👁️ Using Original Analyzer (Vision-only)")
        from .pressure_instrument_service import PressureInstrumentAnalyzer
        return PressureInstrumentAnalyzer()
    
    # No analyzer available
    raise ImportError("No pressure instrument analyzer available")

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
        
        # ✅ FIXED: Extract P&ID No from filename (same as MOV/SDV logic)
        pid_filename = pid_file.name
        pid_no_from_filename = pid_filename.rsplit('.', 1)[0]  # Remove extension
        
        # Extract drawing information
        drawing_info = {
            'drawing_number': request.data.get('drawing_number', pid_no_from_filename),  # Use filename if not provided
            'drawing_title': request.data.get('drawing_title', 'Pressure Instrument Analysis'),
            'revision': request.data.get('revision', 'A'),
            'project_name': request.data.get('project_name', 'Default Project'),
            'area': request.data.get('area', ''),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'pid_no': pid_no_from_filename  # ✅ Kept for backwards compatibility
        }
        
        # Auto-generate drawing number if not provided or set to AUTO
        if not drawing_info['drawing_number'] or drawing_info['drawing_number'] == 'AUTO':
            drawing_info['drawing_number'] = pid_no_from_filename  # Use filename instead of generated ID
        
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
        
        # Initialize analyzer (enhanced or original)
        analyzer = get_analyzer()
        
        # For enhanced analyzer, use the new method
        if hasattr(analyzer, 'analyze_pid_with_enhanced_ocr'):
            logger.info("[PressureInstrumentAPI] Using Enhanced OCR Analysis")
            # Get file bytes
            pid_bytes = pid_file.read()
            
            # Analyze with enhanced OCR
            instruments = analyzer.analyze_pid_with_enhanced_ocr(pid_bytes, drawing_info)
            
            # Generate Excel from instruments
            if instruments:
                excel_file = analyzer.populate_excel_datasheet(instruments, drawing_info) if hasattr(analyzer, 'populate_excel_datasheet') else None
                message = f"Successfully extracted {len(instruments)} instruments"
            else:
                excel_file = None
                message = "No pressure instruments detected in P&ID"
        else:
            # Original analyzer
            excel_file, instruments, message = analyzer.generate_datasheet_from_pid(
            pid_file,
            drawing_info
        )

        # ─── Soft-coded validation pass (anti-hallucination + ISA-5.1 tag rules) ───
        validation_summary = None
        validation_audit = []
        if _validate_instruments and instruments:
            try:
                vres = _validate_instruments(instruments, drawing_info)
                if vres.get('enabled'):
                    instruments = vres['instruments']
                    validation_summary = vres['summary']
                    validation_audit = vres['audit']
                    logger.info(
                        "[PressureInstrumentAPI] Validator kept=%s dropped=%s",
                        validation_summary['kept'], validation_summary['dropped'],
                    )
                    # Re-build Excel from sanitised list when possible
                    if instruments and hasattr(analyzer, 'populate_excel_datasheet'):
                        try:
                            excel_file = analyzer.populate_excel_datasheet(instruments, drawing_info)
                        except Exception as rebuild_err:
                            logger.warning(
                                "[PressureInstrumentAPI] Excel rebuild after validation failed: %s",
                                rebuild_err,
                            )
            except Exception as val_err:  # never let validator break extraction
                logger.warning("[PressureInstrumentAPI] Validator skipped: %s", val_err)

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
                'excel_generated': True,
                'validation': validation_summary,
                'validation_audit': validation_audit,
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

        # Soft-coded validation pass — sanitise list before Excel build
        if _validate_instruments:
            try:
                vres = _validate_instruments(instruments, drawing_info)
                if vres.get('enabled'):
                    instruments = vres['instruments']
                    logger.info(
                        "[PressureInstrumentAPI] Validator (download): kept=%s dropped=%s",
                        vres['summary']['kept'], vres['summary']['dropped'],
                    )
            except Exception as val_err:
                logger.warning("[PressureInstrumentAPI] Validator skipped (download): %s", val_err)

        logger.info(f"[PressureInstrumentAPI] 📊 Generating Excel for {len(instruments)} instruments")
        logger.info(f"[PressureInstrumentAPI] Drawing: {drawing_info.get('drawing_number', 'N/A')}")
        
        # Initialize analyzer (V3 > V2 > Original)
        analyzer = get_analyzer()
        logger.info(f"[PressureInstrumentAPI] Using analyzer: {analyzer.__class__.__name__}")
        
        # Generate Excel (returns BytesIO object)
        excel_buffer = analyzer.populate_excel_datasheet(instruments, drawing_info)
        
        if excel_buffer is None:
            raise ValueError("Excel generation returned None - check analyzer logs")
        
        # Validate BytesIO object
        if not hasattr(excel_buffer, 'getvalue'):
            raise TypeError(f"Expected BytesIO object, got {type(excel_buffer).__name__}")
        
        # Return Excel file with standardized filename
        response = HttpResponse(
            excel_buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
        # Generate filename with timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        drawing_num = drawing_info.get('drawing_number', 'AUTO').replace('/', '-')
        filename = f'Pressure_Instruments_{drawing_num}_{timestamp}.xlsx'
        
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        
        logger.info(f"[PressureInstrumentAPI] ✅ Excel generated: {filename} ({len(excel_buffer.getvalue())} bytes)")
        return response
        
    except Exception as e:
        logger.error(f"[PressureInstrumentAPI] ❌ Excel generation error: {str(e)}", exc_info=True)
        return Response(
            {
                'error': 'Excel generation failed',
                'details': str(e),
                'analyzer': analyzer.__class__.__name__ if 'analyzer' in locals() else 'Unknown'
            },
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
        analyzer = get_analyzer()
        
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
