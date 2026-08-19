"""
SDV Streams Extraction View
Handles P&ID + HMB upload and generates filled datasheets (ASYNC)
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
def extract_sdv_streams(request):
    """
    AI-Orchestrated SDV Datasheet Generation (ASYNC)
    
    POST /api/v1/process-datasheet/datasheets/extract-sdv-streams/
    
    Body (multipart/form-data):
        - pid_file: P&ID PDF file (required)
        - hmb_file: HMB PDF file (required)
        - equipment_type: 'sdv_streams' (required)
    
    Returns:
        - Job ID for background processing (immediate response)
    """
    try:
        logger.info(f"[SDV Streams] Request from user: {request.user.email}")
        
        # Get uploaded files
        pid_file = request.FILES.get('pid_file')
        hmb_file = request.FILES.get('hmb_file')
        equipment_type = request.data.get('equipment_type', '')
        
        logger.info(f"[SDV Streams] Files received - P&ID: {bool(pid_file)}, HMB: {bool(hmb_file)}")
        
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
        if equipment_type != 'sdv_streams':
            return Response(
                {'error': 'equipment_type must be "sdv_streams"'},
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
        
        logger.info("[SDV Streams] ✅ Validation passed, starting async processing...")
        
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
        from apps.process_datasheet.sdv_threading_processor import start_async_processing
        
        job_id = start_async_processing(
            pid_file_path=pid_temp_path,
            hmb_file_path=hmb_temp_path,
            pid_filename=pid_file.name,
            user_email=request.user.email if hasattr(request.user, 'email') else 'anonymous'
        )
        
        logger.info(f"[SDV Streams] ✅ Job started: {job_id}")
        
        # Return job ID immediately
        return JsonResponse({
            'success': True,
            'job_id': job_id,
            'status': 'processing',
            'message': 'Processing started. This may take 2-5 minutes for Vision AI extraction.'
        })
        
    except Exception as e:
        logger.error(f"[SDV Streams] ❌ Error: {e}", exc_info=True)
        return Response(
            {'error': f'SDV streams extraction failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        
        # Save files temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as pid_temp:
            for chunk in pid_file.chunks():
                pid_temp.write(chunk)
            pid_temp_path = pid_temp.name
        
        hmb_temp_path = None
        if hmb_file:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as hmb_temp:
                for chunk in hmb_file.chunks():
                    hmb_temp.write(chunk)
                hmb_temp_path = hmb_temp.name
        
        try:
            # Import extraction and mapping services
            from apps.designiq.pid_ocr_extractor_v2 import PIDLineExtractorV2
            from apps.process_datasheet.sdv_ai_mapper import SDVDatasheetAIMapper
            from apps.process_datasheet.sdv_excel_generator_dynamic import SDVExcelGeneratorDynamic
            from apps.process_datasheet.mock_extractors import (
                MockPIDExtractor, 
                match_lines_to_streams
            )
            from apps.process_datasheet.hmb_vision_extractor import HMBVisionExtractor
            
            # STEP 1: Extract P&ID data
            logger.info("[SDV Streams] STEP 1: Extracting P&ID data...")
            logger.info(f"[SDV Streams] P&ID filename: {pid_file.name}")
            # TODO: Replace with real extraction
            pid_extractor = MockPIDExtractor()
            pid_data = pid_extractor.extract_from_pdf(pid_temp_path, original_filename=pid_file.name)
            logger.info(f"[SDV Streams] ✅ Extracted {len(pid_data.get('valves', []))} valves from P&ID")
            logger.info(f"[SDV Streams] ✅ P&ID Number: {pid_data.get('drawing_info', {}).get('pid_no', 'Unknown')}")
            
            # STEP 2: Extract HMB data using Vision model (STRICT ACCURACY MODE)
            hmb_data = None
            if hmb_temp_path:
                logger.info("[SDV Streams] STEP 2: Extracting HMB data using Vision model...")
                try:
                    vision_extractor = HMBVisionExtractor()
                    hmb_data = vision_extractor.extract_from_pdf(hmb_temp_path)
                    logger.info(f"[SDV Streams] ✅ Extracted {len(hmb_data.get('streams', []))} streams from HMB")
                except Exception as e:
                    logger.warning(f"[SDV Streams] ⚠️ Vision extraction failed, using fallback: {e}")
                    # Fallback to mock if vision fails
                    from apps.process_datasheet.mock_extractors import MockHMBExtractor
                    hmb_extractor = MockHMBExtractor()
                    hmb_data = hmb_extractor.extract_from_pdf(hmb_temp_path)
                    logger.info(f"[SDV Streams] ✅ Extracted {len(hmb_data.get('streams', []))} streams from HMB (fallback)")
            else:
                logger.info("[SDV Streams] STEP 2: No HMB file provided, using P&ID data only")
                # Create minimal HMB data structure
                hmb_data = {
                    'streams': [],
                    'process_conditions': {}
                }
            
            # STEP 3: Pre-match lines to streams
            logger.info("[SDV Streams] STEP 3: Matching lines to streams...")
            line_context = match_lines_to_streams(pid_data, hmb_data)
            logger.info(f"[SDV Streams] ✅ Matched {len(line_context)} valve-stream associations")
            
            # STEP 4: AI Intelligent Mapping
            logger.info("[SDV Streams] STEP 4: AI intelligent mapping...")
            mapper = SDVDatasheetAIMapper()
            mapped_data = mapper.map_pid_hmb_to_datasheet(
                pid_data=pid_data,
                hmb_data=hmb_data,
                line_context=line_context
            )
            logger.info(f"[SDV Streams] ✅ Mapped {len(mapped_data.get('valves', []))} valves")
            
            # STEP 5: Generate Excel datasheet (dynamically created from scratch - NO TEMPLATE)
            logger.info("[SDV Streams] STEP 5: Generating Excel datasheet dynamically...")
            generator = SDVExcelGeneratorDynamic()
            excel_file = generator.generate_datasheet(mapped_data)
            logger.info("[SDV Streams] ✅ Excel datasheet generated")
            
            # Convert Excel to base64 for embedding in response
            excel_file.seek(0)
            excel_base64 = base64.b64encode(excel_file.read()).decode('utf-8')
            excel_file.seek(0)
            
            # Generate HTML preview table
            valve_data = mapped_data.get('valves', [{}])[0]
            html_preview = generate_html_preview(valve_data)
            
            # Return JSON with HTML preview and Excel data
            response_data = {
                'success': True,
                'html_preview': html_preview,
                'excel_file': excel_base64,
                'filename': f'SDV_Datasheet_{valve_data.get("tag_no", "Unknown")}.xlsx'
            }
            response = JsonResponse(response_data, safe=False)
            response['Content-Type'] = 'application/json'
            return response
            
        finally:
            # Cleanup temp files
            try:
                os.unlink(pid_temp_path)
                if hmb_temp_path:
                    os.unlink(hmb_temp_path)
            except Exception as e:
                logger.warning(f"[SDV Streams] Cleanup warning: {e}")
    
    except Exception as e:
        logger.error(f"[SDV Streams] ❌ Error: {str(e)}", exc_info=True)
        return Response(
            {'error': f'SDV streams extraction failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def generate_html_preview(valve_data: dict) -> str:
    """Generate HTML table preview matching the image structure"""
    
    # Extract first valve if valve_data contains 'valves' array
    if 'valves' in valve_data and isinstance(valve_data['valves'], list) and valve_data['valves']:
        valve_data = valve_data['valves'][0]
    
    # Clean all values - remove None and convert to string
    cleaned_data = {}
    for key, value in valve_data.items():
        if value is None:
            cleaned_data[key] = ''
        else:
            cleaned_data[key] = str(value)
    
    html = """
    <style>
        .sdv-table {{
            width: 100%;
            border-collapse: collapse;
            font-family: Arial, sans-serif;
            font-size: 12px;
            margin: 20px 0;
        }}
        .sdv-table th, .sdv-table td {{
            border: 1px solid #000;
            padding: 8px;
            text-align: left;
        }}
        .sdv-header {{
            background-color: #f0f0f0;
            font-weight: bold;
            text-align: center;
            font-size: 16px;
        }}
        .section-label {{
            background-color: #e8e8e8;
            font-weight: bold;
            text-align: center;
            vertical-align: middle;
        }}
        .row-num {{
            text-align: center;
            font-weight: bold;
            width: 30px;
        }}
        .field-label {{
            font-weight: bold;
            width: 150px;
        }}
        .sub-field {{
            font-weight: bold;
            width: 80px;
        }}
    </style>
    
    <table class="sdv-table">
        <!-- Header -->
        <tr>
            <th colspan="2" class="field-label">COMPANY Doc No.:</th>
            <th colspan="10"></th>
            <th class="field-label">Rev. No.:</th>
            <th>{rev_no}</th>
        </tr>
        <tr>
            <th colspan="12" rowspan="2" class="sdv-header">PROCESS DATA SHEET<br/>SHUTDOWN VALVE</th>
            <th class="field-label">Date:</th>
            <th>{date}</th>
        </tr>
        <tr>
            <th colspan="2">Page No: 2 Of 2</th>
        </tr>
        <tr>
            <th colspan="2" class="field-label">Document Class:</th>
            <th colspan="12"></th>
        </tr>
        
        <!-- General Data Section -->
        <tr>
            <td rowspan="6" class="section-label">General<br/>Data</td>
            <td class="row-num">1</td>
            <td class="field-label">Tag No.</td>
            <td colspan="11">{tag_no}</td>
        </tr>
        <tr>
            <td class="row-num">2</td>
            <td class="field-label">Service</td>
            <td colspan="11">{service}</td>
        </tr>
        <tr>
            <td class="row-num">3</td>
            <td class="field-label">P&ID No.</td>
            <td colspan="11">{pid_no}</td>
        </tr>
        <tr>
            <td class="row-num">4</td>
            <td class="field-label">Line No.</td>
            <td colspan="4">{line_no}</td>
            <td class="sub-field">Piping class</td>
            <td colspan="5">{piping_class}</td>
        </tr>
        <tr>
            <td class="row-num">5</td>
            <td class="field-label">Sour Service</td>
            <td colspan="4">{sour_service}</td>
            <td class="sub-field">Special Service</td>
            <td colspan="5">{special_service}</td>
        </tr>
        <tr>
            <td class="row-num">6</td>
            <td class="field-label">Ambient Temp</td>
            <td class="sub-field">Min</td>
            <td>{ambient_temp_min}</td>
            <td class="sub-field">Max.</td>
            <td>{ambient_temp_max}</td>
            <td class="sub-field">Unit</td>
            <td colspan="5">{ambient_temp_unit}</td>
        </tr>
        
        <!-- Operating Conditions Section -->
        <tr>
            <td rowspan="5" class="section-label">Operating<br/>Conditions</td>
            <td class="row-num">7</td>
            <td class="field-label">Fluid</td>
            <td colspan="2">{fluid}</td>
            <td class="sub-field">Phase</td>
            <td>{phase}</td>
            <td class="sub-field">State</td>
            <td colspan="6">{state}</td>
        </tr>
        <tr>
            <td class="row-num">8</td>
            <td class="field-label">Press.</td>
            <td class="sub-field">Normal</td>
            <td>{pressure_normal}</td>
            <td class="sub-field">Design</td>
            <td>{pressure_design}</td>
            <td class="sub-field">Unit</td>
            <td colspan="6">{pressure_unit}</td>
        </tr>
        <tr>
            <td class="row-num">9</td>
            <td class="field-label">Temperature</td>
            <td class="sub-field">Min</td>
            <td>{temp_min}</td>
            <td class="sub-field">Max.</td>
            <td>{temp_max}</td>
            <td class="sub-field">Unit</td>
            <td colspan="6">{temp_unit}</td>
        </tr>
        <tr>
            <td class="row-num">10</td>
            <td class="field-label">Design Temp.</td>
            <td class="sub-field">Min</td>
            <td>{design_temp_min}</td>
            <td class="sub-field">Max.</td>
            <td>{design_temp_max}</td>
            <td class="sub-field">Unit</td>
            <td colspan="6">{design_temp_unit}</td>
        </tr>
        <tr>
            <td class="row-num">11</td>
            <td class="field-label">Shut Off Pressure</td>
            <td colspan="11">{shut_off_pressure}</td>
        </tr>
        
        <!-- Valve Details Section -->
        <tr>
            <td rowspan="2" class="section-label">Valve<br/>Details</td>
            <td class="row-num">12</td>
            <td class="field-label">Bore Detail</td>
            <td colspan="11">{bore_detail}</td>
        </tr>
        <tr>
            <td class="row-num">13</td>
            <td class="field-label">Mech. Handwheel</td>
            <td colspan="11">{mech_handwheel}</td>
        </tr>
        
        <!-- Actuator Details Section -->
        <tr>
            <td rowspan="4" class="section-label">Actuator<br/>Details</td>
            <td class="row-num">14</td>
            <td class="field-label">Air Fail position</td>
            <td colspan="11">{fail_position}</td>
        </tr>
        <tr>
            <td class="row-num">15</td>
            <td class="field-label">Valve Close Time</td>
            <td colspan="4">{valve_close_time}</td>
            <td class="sub-field">Valve Open Time</td>
            <td colspan="6">{valve_open_time}</td>
        </tr>
        <tr>
            <td class="row-num">16</td>
            <td class="field-label">Design Pressure</td>
            <td colspan="11">{design_pressure}</td>
        </tr>
        <tr>
            <td class="row-num">17</td>
            <td class="field-label">Seat Leakage Class</td>
            <td colspan="11">{seat_leakage_class}</td>
        </tr>
        
        <!-- Accessories Section -->
        <tr>
            <td class="section-label">Accessories</td>
            <td class="row-num">18</td>
            <td class="field-label">NACE Requirement</td>
            <td colspan="11">{nace_requirement}</td>
        </tr>
        
        <!-- Notes -->
        <tr>
            <td colspan="14" class="field-label">Notes:</td>
        </tr>
    </table>
    """.format(
        rev_no=cleaned_data.get('rev_no', 'A'),
        date=cleaned_data.get('date', 'N/A'),
        tag_no=cleaned_data.get('tag_no', ''),
        service=cleaned_data.get('service', ''),
        pid_no=cleaned_data.get('pid_no', ''),
        line_no=cleaned_data.get('line_no', ''),
        piping_class=cleaned_data.get('piping_class', ''),
        sour_service=cleaned_data.get('sour_service', ''),
        special_service=cleaned_data.get('special_service', ''),
        ambient_temp_min=cleaned_data.get('ambient_temp_min', ''),
        ambient_temp_max=cleaned_data.get('ambient_temp_max', ''),
        ambient_temp_unit=cleaned_data.get('ambient_temp_unit', '°C'),
        fluid=cleaned_data.get('fluid', ''),
        phase=cleaned_data.get('phase', ''),
        state=cleaned_data.get('state', ''),
        pressure_normal=cleaned_data.get('operating_pressure_normal', ''),
        pressure_design=cleaned_data.get('operating_pressure_design', ''),
        pressure_unit=cleaned_data.get('pressure_unit', 'barg'),
        temp_min=cleaned_data.get('operating_temp_min', ''),
        temp_max=cleaned_data.get('operating_temp_max', ''),
        temp_unit=cleaned_data.get('operating_temp_unit', '°C'),
        design_temp_min=cleaned_data.get('design_temp_min', ''),
        design_temp_max=cleaned_data.get('design_temp_max', ''),
        design_temp_unit=cleaned_data.get('design_temp_unit', '°C'),
        shut_off_pressure=cleaned_data.get('shut_off_pressure', ''),
        bore_detail=cleaned_data.get('bore_detail', ''),
        mech_handwheel=cleaned_data.get('mech_handwheel', ''),
        fail_position=cleaned_data.get('fail_position', ''),
        valve_close_time=cleaned_data.get('valve_close_time', ''),
        valve_open_time=cleaned_data.get('valve_open_time', ''),
        design_pressure=cleaned_data.get('design_pressure', ''),
        seat_leakage_class=cleaned_data.get('seat_leakage_class', ''),
        nace_requirement=cleaned_data.get('nace_requirement', '')
    )
    
    return html


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_sdv_job_status(request, job_id):
    """
    Check status of async SDV processing job
    
    GET /api/v1/process-datasheet/datasheets/sdv-job-status/<job_id>/
    
    Returns:
        - progress (0-100)
        - stage (current processing stage)
        - result (if complete)
    """
    try:
        # Get cached progress and result
        progress = cache.get(f'sdv_task_{job_id}_progress', 0)
        stage = cache.get(f'sdv_task_{job_id}_stage', 'Initializing...')
        result = cache.get(f'sdv_task_{job_id}_result')
        
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
        logger.error(f"[SDV Job Status] Error: {e}")
        return Response(
            {'error': f'Failed to check job status: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
