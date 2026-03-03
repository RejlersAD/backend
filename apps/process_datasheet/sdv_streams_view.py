"""
SDV Streams Extraction View
Handles P&ID + HMB upload and generates filled datasheets
"""
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.core.files.base import ContentFile
from django.http import FileResponse
import tempfile
import os

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def extract_sdv_streams(request):
    """
    AI-Orchestrated SDV Datasheet Generation
    
    POST /api/v1/process-datasheet/datasheets/extract-sdv-streams/
    
    Body (multipart/form-data):
        - pid_file: P&ID PDF file (required)
        - hmb_file: HMB PDF file (optional but recommended)
        - equipment_type: 'sdv_streams' (required)
    
    Returns:
        - Immediate: Filled Excel datasheet download
        OR
        - Job ID for background processing
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
        
        if hmb_file and not hmb_file.name.lower().endswith('.pdf'):
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
        
        if hmb_file and hmb_file.size > 50 * 1024 * 1024:
            return Response(
                {'error': 'HMB file exceeds 50MB limit'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logger.info("[SDV Streams] ✅ Validation passed, starting extraction...")
        
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
            from apps.process_datasheet.sdv_excel_generator import SDVExcelGenerator
            from apps.process_datasheet.mock_extractors import (
                MockPIDExtractor, 
                MockHMBExtractor,
                match_lines_to_streams
            )
            
            # STEP 1: Extract P&ID data
            logger.info("[SDV Streams] STEP 1: Extracting P&ID data...")
            # TODO: Replace with real extraction
            pid_extractor = MockPIDExtractor()
            pid_data = pid_extractor.extract_from_pdf(pid_temp_path)
            logger.info(f"[SDV Streams] ✅ Extracted {len(pid_data.get('valves', []))} valves from P&ID")
            
            # STEP 2: Extract HMB data (if provided)
            hmb_data = None
            if hmb_temp_path:
                logger.info("[SDV Streams] STEP 2: Extracting HMB data...")
                # TODO: Replace with real extraction
                hmb_extractor = MockHMBExtractor()
                hmb_data = hmb_extractor.extract_from_pdf(hmb_temp_path)
                logger.info(f"[SDV Streams] ✅ Extracted {len(hmb_data.get('streams', []))} streams from HMB")
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
            
            # STEP 5: Generate Excel datasheet
            logger.info("[SDV Streams] STEP 5: Generating Excel datasheet...")
            generator = SDVExcelGenerator()
            excel_file = generator.generate_datasheet(mapped_data)
            logger.info("[SDV Streams] ✅ Excel datasheet generated")
            
            # Return Excel file as download
            response = FileResponse(
                excel_file,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                filename=f'SDV_Datasheet_{pid_data.get("valves", [{}])[0].get("tag", "Unknown")}.xlsx'
            )
            
            logger.info("[SDV Streams] ✅ SUCCESS - Returning filled datasheet")
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
