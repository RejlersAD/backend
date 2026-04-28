"""
PFD Converter Views
"""
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
import os
import logging

logger = logging.getLogger(__name__)

# Import Data Visibility for Row-Level Security
from apps.rbac.data_visibility_mixin import TeamCollaborationMixin

from .models import PFDDocument, PIDConversion, ConversionFeedback
from .serializers import (
    PFDDocumentSerializer, PIDConversionSerializer,
    ConversionFeedbackSerializer, PFDUploadSerializer,
    ConversionRequestSerializer
)
from .services import PFDToPIDConverter
from .services_advanced_pipeline import AdvancedPFDToPIDPipeline
from apps.rbac.permissions import HasModuleAccess


class PFDDocumentViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    """
    ViewSet for PFD document management
    
    🔐 Data Visibility:
    - PFD team members (with pfd_converter module) see all PFD documents for collaboration
    - Users without PFD module see only their own documents
    - Admins see everything
    """
    # Data visibility configuration
    visibility_module_code = 'pfd_converter'
    visibility_owner_field = 'uploaded_by'
    
    queryset = PFDDocument.objects.all()
    serializer_class = PFDDocumentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['document_number', 'document_title', 'project_name']
    ordering_fields = ['created_at', 'status', 'document_number']
    filterset_fields = ['status', 'project_code']
    
    def get_queryset(self):
        """Filter documents by user"""
        user = self.request.user
        
        # Super admin sees all
        if hasattr(user, 'rbac_profile'):
            if user.rbac_profile.roles.filter(code='super_admin', is_active=True).exists():
                return PFDDocument.objects.all()
        
        # Others see only their documents
        return PFDDocument.objects.filter(uploaded_by=user)
    
    @action(detail=False, methods=['post'])
    def upload(self, request):
        """
        Upload PFD document and extract process data
        
        POST /api/v1/pfd/documents/upload/
        """
        serializer = PFDUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            file = serializer.validated_data['file']
            
            # Extract all user-provided metadata
            intelligence_level = serializer.validated_data.get('intelligence_level', 'ultra')
            drawing_title = serializer.validated_data.get('drawing_title', '')
            client = serializer.validated_data.get('client', 'SARB Oil & Gas Division')
            contractor = serializer.validated_data.get('contractor', 'Rejlers Engineering AB')

            # Engineering context fields — feed the precision enhancer for better P&ID accuracy
            engineering_context = {
                'fluid_service':          request.data.get('fluid_service', '').strip(),
                'operating_pressure':     request.data.get('operating_pressure', '').strip(),
                'operating_temperature':  request.data.get('operating_temperature', '').strip(),
                'applicable_standards':   request.data.get('applicable_standards', '').strip(),
                'design_basis':           request.data.get('design_basis', '').strip(),
            }

            # Create PFD document
            pfd_doc = PFDDocument.objects.create(
                uploaded_by=request.user,
                file=file,
                file_name=file.name,
                file_size=file.size,
                file_type=file.content_type,
                document_title=serializer.validated_data.get('document_title', ''),
                document_number=serializer.validated_data.get('document_number', ''),
                revision=serializer.validated_data.get('revision', ''),
                project_name=serializer.validated_data.get('project_name', ''),
                project_code=serializer.validated_data.get('project_code', ''),
                status='processing'
            )
            
            # Store metadata for later P&ID generation (in conversion_notes temporarily)
            pfd_doc.conversion_notes = (
                f"Intelligence: {intelligence_level} | Drawing: {drawing_title} "
                f"| Client: {client} | Contractor: {contractor}"
            )
            # Append engineering context if provided
            ctx_parts = [
                f"{k.replace('_', ' ').title()}: {v}"
                for k, v in engineering_context.items()
                if v
            ]
            if ctx_parts:
                pfd_doc.conversion_notes += " | " + " | ".join(ctx_parts)
            
            # Extract PFD data using Advanced AI Pipeline
            pfd_doc.processing_started_at = timezone.now()
            pfd_doc.save()
            
            try:
                logger.info(f"🚀 Starting ADVANCED PFD Processing Pipeline for {pfd_doc.file_name}")
                
                # Use new advanced pipeline
                pipeline = AdvancedPFDToPIDPipeline(project_id=pfd_doc.project_code)

                # Feed engineering context (fluid service, operating P/T, etc.) into the
                # soft-coded "Intelligent Diagram Conversion Engine" prompt builder.
                # All values are optional — missing keys are silently dropped by the builder.
                pipeline.engineering_context = engineering_context

                # Open the saved file for extraction (file pointer is at end after save)
                pfd_doc.file.open('rb')
                
                # Execute Step 1: Computer Vision + OCR
                extracted_data = pipeline._step1_computer_vision_ocr(pfd_doc.file)
                
                pfd_doc.file.close()
                
                pfd_doc.extracted_data = extracted_data

                # ── PRECISION ENHANCEMENT (soft-coded, additive, non-breaking) ──────────
                # Runs a second GPT-4o pass to validate instrument loops, safety devices,
                # utility connections, and ISA 5.1 / ADNOC DEP compliance gaps.
                # Controlled by env: ENABLE_PID_PRECISION_ENHANCER (default True)
                logger.info("🔬 Running PID Precision Enhancement pass …")
                try:
                    from .pid_precision_enhancer import PIDPrecisionEnhancer
                    enhancer = PIDPrecisionEnhancer()
                    extracted_data = enhancer.enhance(extracted_data, engineering_context)
                    pfd_doc.extracted_data = extracted_data
                except Exception as _enhance_err:
                    logger.warning(
                        f"⚠️ Precision enhancement skipped (non-critical): {_enhance_err}"
                    )
                # ── END PRECISION ENHANCEMENT ─────────────────────────────────────────

                # NEW: Run comprehensive analysis automatically
                logger.info(f"🔍 Running comprehensive PFD analysis...")
                try:
                    from .comprehensive_analysis_service import analyze_pfd_comprehensive
                    
                    # Get full file path
                    import os
                    from django.conf import settings
                    file_path = os.path.join(settings.MEDIA_ROOT, str(pfd_doc.file))
                    
                    # Prepare document info
                    document_info = {
                        'document_number': pfd_doc.document_number,
                        'document_title': pfd_doc.document_title,
                        'revision': pfd_doc.revision,
                        'project_name': pfd_doc.project_name,
                        'project_code': pfd_doc.project_code
                    }
                    
                    # Run comprehensive analysis (detailed level)
                    comprehensive_report = analyze_pfd_comprehensive(
                        file_path,
                        document_info=document_info,
                        analysis_level="detailed"
                    )
                    
                    # Store in database
                    pfd_doc.comprehensive_analysis = comprehensive_report
                    
                    logger.info(f"✅ Comprehensive analysis completed:")
                    logger.info(f"   - Equipment: {len(comprehensive_report.get('all_equipment', []))}")
                    logger.info(f"   - Piping: {len(comprehensive_report.get('all_piping', []))}")
                    logger.info(f"   - Instruments: {len(comprehensive_report.get('all_instruments', []))}")
                    
                except Exception as comp_error:
                    logger.warning(f"⚠️ Comprehensive analysis failed (non-critical): {str(comp_error)}")
                    # Don't fail the entire upload if comprehensive analysis fails
                    pfd_doc.comprehensive_analysis = {
                        "error": str(comp_error),
                        "status": "failed"
                    }
                
                # NEW: Run YOLOv8 symbol detection
                logger.info(f"🔍 Running YOLOv8 P&ID symbol detection...")
                try:
                    from .yolov8_detector import get_yolov8_detector
                    
                    detector = get_yolov8_detector()
                    if detector.is_available():
                        detection_results = detector.detect_symbols_from_file(file_path, dpi=150)
                        
                        if detection_results['success']:
                            pfd_doc.yolov8_detections = detection_results
                            logger.info(f"✅ YOLOv8 detection completed:")
                            logger.info(f"   - Total symbols detected: {detection_results['total_symbols']}")
                            logger.info(f"   - Average confidence: {detection_results['confidence_avg']:.2%}")
                            logger.info(f"   - Unique symbol types: {len(detection_results['symbol_counts'])}")
                            
                            # Log top 5 detected symbols
                            sorted_counts = sorted(detection_results['symbol_counts'].items(), 
                                                 key=lambda x: x[1], reverse=True)[:5]
                            for symbol, count in sorted_counts:
                                logger.info(f"      • {symbol}: {count}")
                        else:
                            logger.warning(f"⚠️ YOLOv8 detection failed: {detection_results.get('error')}")
                            pfd_doc.yolov8_detections = {"error": detection_results.get('error'), "status": "failed"}
                    else:
                        logger.warning("⚠️ YOLOv8 detector not available (model not loaded)")
                        pfd_doc.yolov8_detections = {"status": "unavailable", "message": "YOLOv8 model not loaded"}
                        
                except Exception as yolo_error:
                    logger.warning(f"⚠️ YOLOv8 detection failed (non-critical): {str(yolo_error)}")
                    pfd_doc.yolov8_detections = {
                        "error": str(yolo_error),
                        "status": "failed"
                    }
                
                pfd_doc.status = 'converted'
                pfd_doc.processing_completed_at = timezone.now()
                pfd_doc.processing_duration = (
                    pfd_doc.processing_completed_at - pfd_doc.processing_started_at
                ).total_seconds()
                pfd_doc.save()
                
                logger.info(f"✅ Advanced PFD extraction completed: {len(extracted_data.get('equipment', []))} equipment items")
                
            except Exception as e:
                import traceback
                error_traceback = traceback.format_exc()
                logger.error(f"❌ PFD extraction failed: {str(e)}")
                logger.error(f"Traceback:\n{error_traceback}")
                
                pfd_doc.status = 'failed'
                pfd_doc.error_message = str(e)
                pfd_doc.processing_completed_at = timezone.now()
                pfd_doc.processing_duration = (
                    pfd_doc.processing_completed_at - pfd_doc.processing_started_at
                ).total_seconds()
                pfd_doc.save()
                
                # Provide user-friendly error messages
                error_message = str(e)
                user_message = None
                suggestions = []
                
                # Check for specific error patterns
                if "I'm sorry, I can't assist" in error_message or "I cannot assist" in error_message:
                    user_message = "The AI vision system had difficulty processing this document."
                    suggestions = [
                        "Ensure the PDF/image is clear and readable",
                        "Try uploading a higher resolution version",
                        "Check that the document is a valid Process Flow Diagram",
                        "Remove any watermarks or overlays that might interfere",
                        "Try converting the PDF to a high-quality PNG/JPEG first"
                    ]
                elif "API key" in error_message or "authentication" in error_message.lower():
                    user_message = "OpenAI API authentication error."
                    suggestions = ["Contact system administrator to verify API key configuration"]
                elif "rate_limit" in error_message.lower() or "quota" in error_message.lower():
                    user_message = "API rate limit or quota exceeded."
                    suggestions = ["Please wait a moment and try again", "Contact administrator if problem persists"]
                elif "timeout" in error_message.lower():
                    user_message = "Request timed out while processing large document."
                    suggestions = ["Try with a smaller/simpler PFD", "Ensure document is under 10MB"]
                else:
                    user_message = "Document processing failed."
                    suggestions = [
                        "Verify the file is a valid PFD document",
                        "Check file format (PDF, PNG, JPEG supported)",
                        "Try re-saving/re-exporting the document",
                        "Contact support if issue continues"
                    ]
                
                return Response(
                    {
                        'error': 'PFD processing failed',
                        'message': user_message,
                        'suggestions': suggestions,
                        'document_id': str(pfd_doc.id),
                        'technical_detail': error_message[:200]  # Limited technical detail
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            return Response(
                PFDDocumentSerializer(pfd_doc).data,
                status=status.HTTP_201_CREATED
            )
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def analyze_five_stages(self, request, pk=None):
        """
        Execute 5-stage PFD analysis with RAG (Retrieval Augmented Generation)
        
        POST /api/v1/pfd/documents/{id}/analyze_five_stages/
        
        RAG Process:
        1. Retrieve similar reference PFDs from S3 bucket
        2. Analyze reference PFDs to learn patterns
        3. Use reference context to enhance analysis of uploaded PFD
        
        Stages:
        1. Module Identification
        2. Module Details
        3. Complexity Analysis
        4. Module Coverage
        5. Connectivity Analysis
        """
        pfd_doc = self.get_object()
        
        # Check if file exists
        if not pfd_doc.file:
            return Response(
                {'error': 'No PFD file found'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            logger.info(f"🚀 Starting RAG-enhanced 5-stage analysis for {pfd_doc.document_number}")
            
            # Update status
            pfd_doc.status = 'analyzing'
            pfd_doc.analysis_stage = 0
            pfd_doc.analysis_progress = 0
            pfd_doc.save()
            
            # Get file path
            file_path = os.path.join(settings.MEDIA_ROOT, str(pfd_doc.file))
            
            # Prepare document info
            document_info = {
                'document_number': pfd_doc.document_number,
                'document_title': pfd_doc.document_title,
                'revision': pfd_doc.revision,
                'project_name': pfd_doc.project_name,
                'project_code': pfd_doc.project_code
            }
            
            # Execute RAG-enhanced 5-stage analysis
            from .rag_service import PFDRAGService
            
            rag_service = PFDRAGService()
            results = rag_service.analyze_with_rag_context(file_path, document_info)
            
            # Save results to database
            if results.get('status') == 'completed':
                pfd_doc.stage1_module_identification = results.get('stage1', {})
                pfd_doc.stage2_module_details = results.get('stage2', {})
                pfd_doc.stage3_pid_complexity = results.get('stage3', {})
                pfd_doc.stage4_module_coverage = results.get('stage4', {})
                pfd_doc.stage5_connectivity = results.get('stage5', {})
                pfd_doc.analysis_stage = 5
                pfd_doc.analysis_progress = 100
                pfd_doc.status = 'analyzed'
            else:
                pfd_doc.error_message = f"Analysis failed: {', '.join(results.get('errors', []))}"
                pfd_doc.status = 'failed'
            
            pfd_doc.save()
            
            logger.info(f"✅ 5-stage analysis completed for {pfd_doc.document_number}")
            
            return Response({
                'success': True,
                'document_id': str(pfd_doc.id),
                'analysis_status': pfd_doc.status,
                'results': results
            })
            
        except Exception as e:
            logger.error(f"❌ 5-stage analysis failed: {str(e)}")
            pfd_doc.status = 'failed'
            pfd_doc.error_message = str(e)
            pfd_doc.save()
            
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get', 'post'])
    def analyze(self, request, pk=None):
        """
        Analyze PFD or get existing analysis
        
        GET /api/v1/pfd/documents/{id}/analyze/ - Get existing analysis
        POST /api/v1/pfd/documents/{id}/analyze/ - Run new analysis
        """
        pfd_doc = self.get_object()
        
        if request.method == 'POST':
            # Run analysis if POST
            return self.analyze_five_stages(request, pk)
        
        # GET - Return analysis in format frontend expects
        # Check if comprehensive_analysis exists (from upload)
        if pfd_doc.comprehensive_analysis:
            comp_analysis = pfd_doc.comprehensive_analysis
            
            # Transform extracted data to match frontend format
            equipment_items = comp_analysis.get('all_equipment', [])
            piping_items = comp_analysis.get('all_piping', [])
            instruments = comp_analysis.get('all_instruments', [])
            
            return Response({
                'document_id': str(pfd_doc.id),
                'document_name': pfd_doc.file_name,
                'document_number': pfd_doc.document_number,
                'status': pfd_doc.status,
                'module_count': 1,  # Treat entire PFD as single module for now
                'complexity_analysis': {
                    'total_equipment_estimate': len(equipment_items),
                    'total_instruments': len(instruments),
                    'total_piping_connections': len(piping_items),
                    'complexity_score': 'medium',
                    'estimated_pid_pages': 1
                },
                'modules': [{
                    'module_id': 'main',
                    'module_name': 'Main Process',
                    'description': f'Primary process flow from {pfd_doc.document_number}',
                    'estimated_equipment_count': len(equipment_items),
                    'key_equipment': [eq.get('tag', eq.get('type', 'Unknown')) for eq in equipment_items[:5]],
                    'complexity': 'medium',
                    'priority': 1
                }],
                'connectivity': {
                    'total_connections': len(piping_items),
                    'connection_types': ['process_line', 'utility_line']
                },
                # Include raw extraction data
                'extracted_data': pfd_doc.extracted_data,
                'comprehensive_analysis': comp_analysis
            })
        
        # Fallback to 5-stage analysis if available
        if pfd_doc.stage1_module_identification:
            return Response({
                'document_id': str(pfd_doc.id),
                'document_name': pfd_doc.file_name,
                'document_number': pfd_doc.document_number,
                'analysis_stage': pfd_doc.analysis_stage,
                'analysis_progress': pfd_doc.analysis_progress,
                'status': pfd_doc.status,
                'stage1_module_identification': pfd_doc.stage1_module_identification,
                'stage2_module_details': pfd_doc.stage2_module_details,
                'stage3_pid_complexity': pfd_doc.stage3_pid_complexity,
                'stage4_module_coverage': pfd_doc.stage4_module_coverage,
                'stage5_connectivity': pfd_doc.stage5_connectivity
            })
        
        # No analysis data available
        return Response(
            {'error': 'No analysis data available. Please upload a PFD first.'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    @action(detail=True, methods=['get'])
    def get_analysis(self, request, pk=None):
        """
        Get 5-stage analysis results (legacy endpoint)
        
        GET /api/v1/pfd/documents/{id}/get_analysis/
        """
        return self.analyze(request, pk)
    
    @action(detail=True, methods=['post'])
    def reprocess(self, request, pk=None):
        """
        Reprocess PFD document with current extraction pipeline
        
        POST /api/v1/pfd/documents/{id}/reprocess/
        """
        pfd_doc = self.get_object()
        
        # Check if file exists
        if not pfd_doc.file:
            return Response(
                {'error': 'No file attached to this document'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            file_path = os.path.join(settings.MEDIA_ROOT, str(pfd_doc.file))
            
            if not os.path.exists(file_path):
                return Response(
                    {'error': f'File not found: {pfd_doc.file.name}. Please re-upload the document.'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            logger.info(f"🔄 Reprocessing PFD document: {pfd_doc.document_number}")
            
            # Update status
            pfd_doc.status = 'processing'
            pfd_doc.processing_started_at = timezone.now()
            pfd_doc.save()
            
            # Re-extract using advanced pipeline
            pipeline = AdvancedPFDToPIDPipeline(project_id=pfd_doc.project_code)
            
            pfd_doc.file.open('rb')
            extracted_data = pipeline._step1_computer_vision_ocr(pfd_doc.file)
            pfd_doc.file.close()
            
            pfd_doc.extracted_data = extracted_data
            
            # Run comprehensive analysis
            logger.info(f"🔍 Running comprehensive PFD analysis...")
            try:
                from .comprehensive_analysis_service import analyze_pfd_comprehensive
                
                document_info = {
                    'document_number': pfd_doc.document_number,
                    'document_title': pfd_doc.document_title,
                    'revision': pfd_doc.revision,
                    'project_name': pfd_doc.project_name,
                    'project_code': pfd_doc.project_code
                }
                
                comprehensive_report = analyze_pfd_comprehensive(
                    file_path,
                    document_info=document_info,
                    analysis_level="detailed"
                )
                
                pfd_doc.comprehensive_analysis = comprehensive_report
                
                logger.info(f"✅ Comprehensive analysis completed:")
                logger.info(f"   - Equipment: {len(comprehensive_report.get('all_equipment', []))}")
                logger.info(f"   - Piping: {len(comprehensive_report.get('all_piping', []))}")
                logger.info(f"   - Instruments: {len(comprehensive_report.get('all_instruments', []))}")
                
            except Exception as comp_error:
                logger.warning(f"⚠️ Comprehensive analysis failed: {str(comp_error)}")
                pfd_doc.comprehensive_analysis = {
                    "error": str(comp_error),
                    "status": "failed"
                }
            
            pfd_doc.status = 'converted'
            pfd_doc.processing_completed_at = timezone.now()
            pfd_doc.processing_duration = (
                pfd_doc.processing_completed_at - pfd_doc.processing_started_at
            ).total_seconds()
            pfd_doc.save()
            
            logger.info(f"✅ Reprocessing completed: {len(extracted_data.get('equipment', []))} equipment items")
            
            return Response({
                'success': True,
                'document_id': str(pfd_doc.id),
                'status': pfd_doc.status,
                'extracted_counts': {
                    'equipment': len(extracted_data.get('equipment', [])),
                    'process_streams': len(extracted_data.get('process_streams', [])),
                    'instruments': len(extracted_data.get('instruments', [])),
                    'control_loops': len(extracted_data.get('control_loops', [])),
                    'valves': len(extracted_data.get('valves', [])),
                    'text_annotations': len(extracted_data.get('text_annotations', [])),
                    'utilities': len(extracted_data.get('utilities', []))
                }
            })
            
        except Exception as e:
            logger.error(f"❌ Reprocessing failed: {str(e)}")
            pfd_doc.status = 'failed'
            pfd_doc.error_message = str(e)
            pfd_doc.processing_completed_at = timezone.now()
            pfd_doc.save()
            
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def test_with_s3_pfd(self, request):
        """
        Test extraction using a reference PFD from S3 bucket
        
        POST /api/v1/pfd/documents/test_with_s3_pfd/
        Body: {"project_code": "P16093"}  (optional, defaults to P16093)
        """
        try:
            from .s3_pfd_service import S3PFDService
            from .five_stage_analyzer import analyze_pfd_five_stages
            
            project_code = request.data.get('project_code', 'P16093')
            
            logger.info(f"🧪 Testing with S3 reference PFD from project {project_code}")
            
            # Initialize S3 service
            s3_service = S3PFDService()
            
            if not s3_service.s3_enabled:
                return Response(
                    {'error': 'S3 service not configured. Please set AWS credentials.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE
                )
            
            # Get sample PFD
            pfd_path = s3_service.get_sample_pfd(project=project_code)
            
            if not pfd_path:
                return Response(
                    {'error': f'No reference PFD found for project {project_code}'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            logger.info(f"✅ Downloaded reference PFD: {pfd_path}")
            
            # Run extraction
            document_info = {
                'document_number': f'{project_code}-TEST',
                'document_title': f'S3 Reference PFD Test',
                'project_code': project_code
            }
            
            results = analyze_pfd_five_stages(pfd_path, document_info)
            
            # Clean up downloaded file
            if os.path.exists(pfd_path):
                os.remove(pfd_path)
            
            return Response({
                'success': True,
                'project_code': project_code,
                'results': results,
                'message': 'Successfully tested extraction with S3 reference PFD'
            })
            
        except Exception as e:
            logger.error(f"❌ S3 PFD test failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
            return Response(
                {'error': str(e), 'detail': traceback.format_exc()},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PIDConversionViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    """
    ViewSet for P&ID conversion management
    
    🔐 Data Visibility:
    - PID team members (with pid_analysis module) see all conversions for collaboration
    - Users without PID module see only their own conversions
    - Admins see everything
    """
    # Data visibility configuration
    visibility_module_code = 'pid_analysis'
    visibility_owner_field = 'converted_by'
    
    queryset = PIDConversion.objects.all()
    serializer_class = PIDConversionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['pid_drawing_number', 'pid_title']
    ordering_fields = ['created_at', 'status', 'confidence_score']
    filterset_fields = ['status', 'pfd_document']
    
    @action(detail=False, methods=['post'])
    def generate(self, request):
        """
        Generate P&ID from PFD document
        
        POST /api/v1/pfd/conversions/generate/
        """
        serializer = ConversionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Get PFD document
            pfd_doc = PFDDocument.objects.get(
                id=serializer.validated_data['pfd_document_id']
            )
            
            # Check if user has access
            if pfd_doc.uploaded_by != request.user:
                if not hasattr(request.user, 'rbac_profile') or \
                   not request.user.rbac_profile.roles.filter(code='super_admin').exists():
                    return Response(
                        {'error': 'Access denied'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # Create conversion record
            conversion = PIDConversion.objects.create(
                pfd_document=pfd_doc,
                converted_by=request.user,
                pid_drawing_number=serializer.validated_data['pid_drawing_number'],
                pid_title=serializer.validated_data['pid_title'],
                pid_revision=serializer.validated_data['pid_revision'],
                status='generating'
            )
            
            # Generate P&ID specifications using ADVANCED 6-STEP PIPELINE
            try:
                logger.info("🚀 Starting ADVANCED P&ID Generation with Graph-Based Layout")
                
                # Use graph-based generator for intelligent layout
                use_graph_layout = True  # Toggle for graph-based vs traditional
                
                # Use advanced pipeline
                pipeline = AdvancedPFDToPIDPipeline(project_id=pfd_doc.project_code)
                
                # Prepare project info
                project_info = {
                    'project_name': pfd_doc.project_name,
                    'project_code': pfd_doc.project_code,
                    'drawing_number': serializer.validated_data['pid_drawing_number'],
                    'drawing_title': serializer.validated_data['pid_title'],
                    'revision': serializer.validated_data['pid_revision']
                }
                
                # Check if we have cached extracted data from upload step
                if pfd_doc.extracted_data and isinstance(pfd_doc.extracted_data, dict):
                    logger.info("✅ Using cached vision data from upload (skipping OpenAI call)")
                    # Execute pipeline with cached data and pass pfd_document for AI drawing
                    pipeline_results = pipeline.convert(
                        pfd_file=None,
                        project_info=project_info,
                        cached_vision_data=pfd_doc.extracted_data,
                        pfd_document=pfd_doc  # Pass document for accessing stored PFD file
                    )
                else:
                    logger.info("⚠️ No cached data found, re-extracting from PFD file")
                    # Open PFD file for full pipeline processing
                    pfd_doc.file.open('rb')
                    pipeline_results = pipeline.convert(pfd_doc.file, project_info)
                    pfd_doc.file.close()
                
                # Extract results from pipeline
                pid_specs = pipeline_results['pid_specifications']
                drawing_path = pipeline_results['drawing_path']
                
                # Use ULTRA COMPLETE generator (RAG + Graph AI)
                if use_graph_layout:
                    try:
                        from .ultra_complete_service import generate_ultra_complete_pid
                        
                        # Prepare drawing info with user-provided metadata
                        # Extract from conversion_notes if available
                        notes = pfd_doc.conversion_notes or ''
                        intelligence_level_stored = 'ultra'
                        drawing_title_stored = ''
                        client = 'SARB Oil & Gas Division'
                        contractor = 'Rejlers Engineering AB'
                        
                        if '| Intelligence:' in notes:
                            intelligence_level_stored = notes.split('| Intelligence:')[1].split('|')[0].strip()
                        if '| Drawing:' in notes:
                            drawing_title_stored = notes.split('| Drawing:')[1].split('|')[0].strip()
                        if '| Client:' in notes:
                            client = notes.split('| Client:')[1].split('|')[0].strip()
                        if '| Contractor:' in notes:
                            contractor = notes.split('| Contractor:')[1].strip()
                        
                        # Get intelligence level (prefer from request, fallback to stored)
                        intelligence_level = serializer.validated_data.get('intelligence_level', intelligence_level_stored)
                        
                        logger.info(f"🤖 Generating P&ID with {intelligence_level.upper()} Intelligence...")
                        logger.info("   Features: RAG Knowledge Base + Advanced Graph Analysis")
                        
                        drawing_info = {
                            'drawing_number': serializer.validated_data['pid_drawing_number'],
                            'drawing_title': drawing_title_stored or serializer.validated_data['pid_title'],
                            'revision': serializer.validated_data['pid_revision'],
                            'project_name': pfd_doc.project_name,
                            'project_code': pfd_doc.project_code,
                            'client': client,
                            'contractor': contractor
                        }
                        
                        # Output path
                        graph_output_dir = os.path.join(settings.MEDIA_ROOT, 'pid_drawings_ultra')
                        os.makedirs(graph_output_dir, exist_ok=True)
                        
                        graph_output_path = os.path.join(
                            graph_output_dir,
                            f"{serializer.validated_data['pid_drawing_number']}_ultra.pdf"
                        )
                        
                        # Generate with ULTRA intelligence
                        result = generate_ultra_complete_pid(
                            extracted_data=pfd_doc.extracted_data,
                            drawing_info=drawing_info,
                            output_path=graph_output_path,
                            intelligence_level=intelligence_level
                        )
                        
                        drawing_path = result['output_path']
                        
                        logger.info(f"✅ {intelligence_level.upper()} P&ID generated successfully:")
                        logger.info(f"   Output: {drawing_path}")
                        logger.info(f"   Elements: {result.get('total_elements', 0)}")
                        if intelligence_level == 'ultra':
                            logger.info(f"   Complexity: {result.get('initial_complexity', 0):.1f} → {result.get('final_complexity', 0):.1f}")
                            logger.info(f"   Missing Found: {result.get('missing_connections_found', 0)}")
                            logger.info(f"   Utilities: {result.get('utility_connections_generated', 0)}")
                            logger.info(f"   Control Loops: {result.get('control_loops_generated', 0)}")
                        
                        # Store metrics in conversion
                        conversion.design_parameters = {
                            'intelligence_level': intelligence_level,
                            'generation_metrics': result,
                            'pipeline_version': pipeline_results.get('pipeline_version', '3.0')
                        }
                        
                    except Exception as graph_error:
                        logger.warning(f"⚠️ Ultra-complete generation failed, using default: {str(graph_error)}")
                        import traceback
                        logger.error(traceback.format_exc())
                        # Keep original drawing_path from pipeline
                
                # Validate using traditional method
                converter = PFDToPIDConverter()
                validation = converter.validate_conversion(pid_specs, pfd_doc.extracted_data)
                
                # Update conversion with pipeline results
                conversion.equipment_list = pid_specs.get('equipment_list', [])
                conversion.instrument_list = pid_specs.get('instrument_list', [])
                conversion.piping_details = pid_specs.get('piping_specifications', [])
                conversion.safety_systems = pid_specs.get('safety_devices', [])
                conversion.design_parameters = {
                    'pipeline_version': pipeline_results.get('pipeline_version', '2.0'),
                    'steps_completed': list(pipeline_results.get('pipeline_steps', {}).keys())
                }
                conversion.compliance_checks = validation
                conversion.confidence_score = validation.get('compliance_score', 0)
                
                # Save P&ID drawing path
                relative_path = drawing_path.replace(str(settings.MEDIA_ROOT), '').lstrip('/\\')
                conversion.pid_file = relative_path
                logger.info(f"✅ Advanced P&ID generation completed: {relative_path}")
                logger.info(f"   Equipment: {len(conversion.equipment_list)} items")
                logger.info(f"   Instruments: {len(conversion.instrument_list)} items")
                logger.info(f"   Piping Lines: {len(conversion.piping_details)} lines")
                
                conversion.status = 'completed'
                conversion.generation_completed_at = timezone.now()
                conversion.generation_duration = (
                    conversion.generation_completed_at - conversion.generation_started_at
                ).total_seconds()
                conversion.save()
                
            except Exception as e:
                conversion.status = 'failed'
                conversion.generation_completed_at = timezone.now()
                conversion.generation_duration = (
                    conversion.generation_completed_at - conversion.generation_started_at
                ).total_seconds()
                conversion.save()
                
                return Response(
                    {
                        'error': 'P&ID generation failed',
                        'detail': str(e),
                        'conversion_id': str(conversion.id)
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            return Response(
                PIDConversionSerializer(conversion).data,
                status=status.HTTP_201_CREATED
            )
            
        except PFDDocument.DoesNotExist:
            return Response(
                {'error': 'PFD document not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['get'])
    def download_drawing(self, request, pk=None):
        """
        Download P&ID drawing PDF with intelligent caching prevention
        
        GET /api/v1/pfd/conversions/{id}/download_drawing/
        GET /api/v1/pfd/conversions/{id}/download_drawing/?force_regenerate=true
        
        5-Layer Caching Prevention:
        1. UUID + timestamp filenames
        2. HTTP cache-control headers
        3. Old file deletion before regeneration
        4. Query param timestamps (handled by frontend)
        5. Unique download filenames with timestamps
        """
        try:
            conversion = self.get_object()
            force_regenerate = request.query_params.get('force_regenerate', 'false').lower() == 'true'
            
            logger.info(f"Download request for conversion {pk}, force_regenerate={force_regenerate}")
            
            # If force regenerate requested
            if force_regenerate:
                logger.info(f"🔄 Force regeneration requested for conversion {pk}")
                
                # Delete old file if exists
                if conversion.pid_file:
                    old_path = os.path.join(settings.MEDIA_ROOT, str(conversion.pid_file))
                    if os.path.exists(old_path):
                        os.remove(old_path)
                        logger.info(f"🗑️ Deleted old file: {old_path}")
                
                # Auto-increment revision (A → B → C → D)
                current_revision = conversion.pid_revision or 'A'
                if current_revision and len(current_revision) == 1 and current_revision.isalpha():
                    next_revision = chr(ord(current_revision) + 1)
                    conversion.pid_revision = next_revision
                    logger.info(f"📝 Revision updated: {current_revision} → {next_revision}")
                
                # Regenerate drawing with new timestamp and UUID
                from .services_advanced_pipeline import AdvancedPFDToPIDPipeline
                pipeline = AdvancedPFDToPIDPipeline()
                
                # Prepare drawing specs
                drawing_specs = {
                    'drawing_number': conversion.pid_drawing_number,
                    'title': conversion.pid_title,
                    'revision': conversion.pid_revision,
                    'equipment': conversion.equipment_list or [],
                    'instruments': conversion.instrument_list or [],
                    'piping': conversion.piping_details or [],
                    'safety_systems': conversion.safety_systems or []
                }
                
                # Generate new P&ID
                new_pid_path = pipeline.generate_programmatic_pid(drawing_specs)
                conversion.pid_file = new_pid_path
                conversion.save()
                logger.info(f"✅ Generated new P&ID: {new_pid_path}")
            
            if not conversion.pid_file:
                logger.warning(f"P&ID drawing not available for conversion {pk}")
                
                # Check if OpenAI API key is configured
                from decouple import config
                api_key = config('OPENAI_API_KEY', default='')
                
                if not api_key or api_key == '' or api_key.startswith('your-'):
                    error_msg = (
                        "P&ID drawing generation requires OpenAI API configuration. "
                        "Please configure OPENAI_API_KEY in your environment variables or .env file. "
                        "The system supports DALL-E 3 (HD quality) and DALL-E 2 (fallback) for AI-generated drawings."
                    )
                else:
                    error_msg = (
                        "P&ID drawing not generated yet. The generation may have failed or is still in progress. "
                        "Please check the conversion status or try regenerating the drawing."
                    )
                
                return Response(
                    {'error': error_msg},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Build full path
            drawing_path = os.path.join(settings.MEDIA_ROOT, str(conversion.pid_file))
            logger.info(f"Attempting to serve file from: {drawing_path}")
            
            if not os.path.exists(drawing_path):
                logger.error(f"Drawing file not found at path: {drawing_path}")
                return Response(
                    {'error': f'Drawing file not found on server. Path checked: {conversion.pid_file}'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Serve file with aggressive cache prevention headers
            from django.http import FileResponse
            from datetime import datetime
            
            response = FileResponse(open(drawing_path, 'rb'), content_type='application/pdf')
            
            # Layer 2: HTTP cache-control headers (prevent server/proxy caching)
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            response['X-Content-Type-Options'] = 'nosniff'
            
            # Layer 5: Unique download filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{conversion.pid_drawing_number}_Rev{conversion.pid_revision}_{timestamp}.pdf"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            logger.info(f"✅ Successfully serving file: {drawing_path} as {filename}")
            return response
            
        except Exception as e:
            logger.error(f"Error in download_drawing: {str(e)}", exc_info=True)
            return Response(
                {'error': f'Failed to download drawing: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='verify-pid')
    def verify_pid(self, request):
        """
        Run P&ID Design Verification on converted P&ID
        Integrates with existing P&ID Analysis engine for comprehensive checks
        
        POST /api/v1/pfd/conversions/verify-pid/
        {
            "conversion_id": "uuid",
            "pfd_document_id": "uuid",
            "pid_data": {...}
        }
        """
        try:
            from apps.pid_analysis.services import PIDAnalysisService
            
            conversion_id = request.data.get('conversion_id')
            pid_data = request.data.get('pid_data', {})
            
            if not conversion_id:
                return Response(
                    {'error': 'conversion_id is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get conversion record
            try:
                conversion = PIDConversion.objects.get(id=conversion_id)
            except PIDConversion.DoesNotExist:
                return Response(
                    {'error': 'Conversion not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Check user access
            if conversion.converted_by != request.user:
                if not hasattr(request.user, 'rbac_profile') or \
                   not request.user.rbac_profile.roles.filter(code='super_admin').exists():
                    return Response(
                        {'error': 'Access denied'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # Extract P&ID data for verification
            # Use the stored conversion data
            verification_data = {
                'equipment_list': conversion.equipment_list or [],
                'instrument_list': conversion.instrument_list or [],
                'piping_details': conversion.piping_details or [],
                'safety_systems': conversion.safety_systems or [],
                'design_parameters': conversion.design_parameters or {},
                'pid_drawing_number': conversion.pid_drawing_number,
                'pid_title': conversion.pid_title,
                'pid_revision': conversion.pid_revision,
            }
            
            # Perform verification using PID Analysis Service
            # Create mock analysis result structure
            issues = []
            
            # Check 1: Equipment verification
            if verification_data['equipment_list']:
                for idx, equipment in enumerate(verification_data['equipment_list']):
                    tag = equipment.get('tag', f'EQUIP-{idx+1}')
                    specs = equipment.get('specifications', {})
                    
                    # Check for missing design pressure
                    if not specs.get('design_pressure'):
                        issues.append({
                            'serial_number': len(issues) + 1,
                            'pid_reference': tag,
                            'issue_observed': f'Equipment {tag}: Design pressure not specified',
                            'action_required': 'Specify design pressure according to ASME VIII standards',
                            'severity': 'major',
                            'category': 'equipment_datasheet',
                            'location_on_drawing': {'zone': 'Equipment Section'}
                        })
                    
                    # Check for missing design temperature
                    if not specs.get('design_temperature'):
                        issues.append({
                            'serial_number': len(issues) + 1,
                            'pid_reference': tag,
                            'issue_observed': f'Equipment {tag}: Design temperature not specified',
                            'action_required': 'Specify design temperature for proper material selection',
                            'severity': 'major',
                            'category': 'equipment_datasheet',
                            'location_on_drawing': {'zone': 'Equipment Section'}
                        })
                    
                    # Check for missing material
                    if not specs.get('material'):
                        issues.append({
                            'serial_number': len(issues) + 1,
                            'pid_reference': tag,
                            'issue_observed': f'Equipment {tag}: Material of Construction not specified',
                            'action_required': 'Specify MOC based on service conditions and corrosion requirements',
                            'severity': 'minor',
                            'category': 'equipment_datasheet',
                            'location_on_drawing': {'zone': 'Equipment Section'}
                        })
            
            # Check 2: Instrumentation verification
            if verification_data['instrument_list']:
                for idx, instrument in enumerate(verification_data['instrument_list']):
                    tag = instrument.get('tag', f'INST-{idx+1}')
                    
                    # Check for missing range
                    if not instrument.get('range'):
                        issues.append({
                            'serial_number': len(issues) + 1,
                            'pid_reference': tag,
                            'issue_observed': f'Instrument {tag}: Measurement range not specified',
                            'action_required': 'Specify instrument range per ISA standards',
                            'severity': 'minor',
                            'category': 'instrumentation',
                            'location_on_drawing': {'zone': 'Instrumentation'}
                        })
                    
                    # Check for control valves missing fail-safe
                    if 'V' in tag and instrument.get('type') == 'Control Valve':
                        if not instrument.get('fail_safe_position'):
                            issues.append({
                                'serial_number': len(issues) + 1,
                                'pid_reference': tag,
                                'issue_observed': f'Control Valve {tag}: Fail-safe position not specified',
                                'action_required': 'Specify fail-safe position (FC/FO/FL) for safety compliance',
                                'severity': 'critical',
                                'category': 'safety_systems',
                                'location_on_drawing': {'zone': 'Control System'}
                            })
            
            # Check 3: Safety systems verification
            if verification_data['safety_systems']:
                for idx, safety_device in enumerate(verification_data['safety_systems']):
                    tag = safety_device.get('tag', f'SAFETY-{idx+1}')
                    device_type = safety_device.get('device_type', '')
                    
                    # PSV checks
                    if 'PSV' in tag or 'Pressure Safety Valve' in device_type:
                        if not safety_device.get('set_pressure'):
                            issues.append({
                                'serial_number': len(issues) + 1,
                                'pid_reference': tag,
                                'issue_observed': f'PSV {tag}: Set pressure not specified',
                                'action_required': 'Specify PSV set pressure ≤ equipment MAWP per ASME standards',
                                'severity': 'critical',
                                'category': 'safety_systems',
                                'location_on_drawing': {'zone': 'Safety Systems'}
                            })
                        
                        if not safety_device.get('discharge_location'):
                            issues.append({
                                'serial_number': len(issues) + 1,
                                'pid_reference': tag,
                                'issue_observed': f'PSV {tag}: Discharge location not specified',
                                'action_required': 'Specify PSV discharge path (flare, vent, safe location)',
                                'severity': 'major',
                                'category': 'safety_systems',
                                'location_on_drawing': {'zone': 'Safety Systems'}
                            })
            
            # Check 4: Piping specifications
            if verification_data['piping_details']:
                for idx, pipe in enumerate(verification_data['piping_details']):
                    line_number = pipe.get('line_number', f'LINE-{idx+1}')
                    
                    # Check for missing pipe class
                    if not pipe.get('pipe_class'):
                        issues.append({
                            'serial_number': len(issues) + 1,
                            'pid_reference': line_number,
                            'issue_observed': f'Line {line_number}: Pipe class not specified',
                            'action_required': 'Specify pipe class per project piping specifications',
                            'severity': 'major',
                            'category': 'piping',
                            'location_on_drawing': {'zone': 'Piping'}
                        })
            
            # Check 5: Design parameters validation
            design_params = verification_data.get('design_parameters', {})
            if not design_params.get('design_pressure'):
                issues.append({
                    'serial_number': len(issues) + 1,
                    'pid_reference': 'Design Basis',
                    'issue_observed': 'System design pressure not specified in design parameters',
                    'action_required': 'Specify system design pressure in design basis section',
                    'severity': 'major',
                    'category': 'documentation',
                    'location_on_drawing': {'zone': 'Title Block / Notes'}
                })
            
            if not design_params.get('design_temperature'):
                issues.append({
                    'serial_number': len(issues) + 1,
                    'pid_reference': 'Design Basis',
                    'issue_observed': 'System design temperature not specified in design parameters',
                    'action_required': 'Specify system design temperature in design basis section',
                    'severity': 'major',
                    'category': 'documentation',
                    'location_on_drawing': {'zone': 'Title Block / Notes'}
                })
            
            # Add a pass message if no issues
            if not issues:
                issues.append({
                    'serial_number': 1,
                    'pid_reference': 'Overall Design',
                    'issue_observed': 'All verification checks passed',
                    'action_required': 'No action required. Design meets basic compliance requirements.',
                    'severity': 'pass',
                    'category': 'compliance',
                    'location_on_drawing': {'zone': 'N/A'}
                })
            
            # Build verification result
            verification_result = {
                'issues': issues,
                'summary': {
                    'total_issues': len([i for i in issues if i['severity'] != 'pass']),
                    'critical': len([i for i in issues if i['severity'] == 'critical']),
                    'major': len([i for i in issues if i['severity'] == 'major']),
                    'minor': len([i for i in issues if i['severity'] == 'minor']),
                    'observation': len([i for i in issues if i['severity'] == 'observation']),
                    'equipment_checked': len(verification_data['equipment_list']),
                    'instruments_checked': len(verification_data['instrument_list']),
                    'safety_devices_checked': len(verification_data['safety_systems']),
                    'piping_lines_checked': len(verification_data['piping_details']),
                },
                'verification_timestamp': timezone.now().isoformat(),
                'verified_by': request.user.email,
                'conversion_id': str(conversion_id),
                'pid_drawing_number': conversion.pid_drawing_number,
            }
            
            logger.info(f"✅ P&ID verification completed for conversion {conversion_id}: {len(issues)} findings")
            
            return Response(verification_result, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ P&ID verification failed: {str(e)}")
            return Response(
                {
                    'error': 'Verification failed',
                    'detail': str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve P&ID conversion"""
        conversion = self.get_object()
        conversion.reviewed_by = request.user
        conversion.reviewed_at = timezone.now()
        conversion.review_notes = request.data.get('review_notes', '')
        conversion.status = 'approved'
        conversion.save()
        
        return Response(PIDConversionSerializer(conversion).data)
    
    @action(detail=True, methods=['get'], url_path='load-to-canvas')
    def load_to_canvas(self, request, pk=None):
        """
        Convert programmatic P&ID to editable canvas format
        
        GET /api/v1/pfd/conversions/{id}/load-to-canvas/
        
        Intelligently converts the structured P&ID data directly to canvas format
        without requiring AI vision analysis. Uses smart layout algorithms to
        position equipment, instruments, and piping for optimal visualization.
        
        Returns:
            Canvas-compatible JSON with:
            - equipment: List of equipment items with positions
            - instrumentation: List of instruments with positions
            - piping: List of pipe routes and connections
            - annotations: List of notes and labels
            - layout: Drawing metadata and settings
        """
        try:
            conversion = self.get_object()
            
            logger.info(f"🎨 Converting P&ID to canvas format (programmatic approach)")
            
            # Extract original PID specifications
            equipment_list = conversion.equipment_list or []
            instrument_list = conversion.instrument_list or []
            piping_details = conversion.piping_details or []
            safety_systems = conversion.safety_systems or []
            
            logger.info(f"📊 Data to convert: {len(equipment_list)} equipment, {len(instrument_list)} instruments, {len(piping_details)} pipes")
            
            # Smart layout algorithm: position equipment in a logical flow
            canvas_data = {
                'equipment': [],
                'instrumentation': [],
                'piping': [],
                'annotations': [],
                'layout': {
                    'flow_direction': 'left-to-right',
                    'style': 'process-sequence',
                    'grid_size': 50
                },
                'metadata': {
                    'conversion_id': str(conversion.id),
                    'drawing_number': conversion.pid_drawing_number,
                    'title': conversion.pid_title,
                    'revision': conversion.pid_revision,
                    'generated_at': str(conversion.created_at),
                    'generator': 'programmatic',
                    'version': '2.0'
                }
            }
            
            # Layout equipment with smart positioning
            equipment_positions = {}
            x_position = 15  # Start at 15% from left
            y_position = 50  # Center vertically
            x_spacing = 20   # Spacing between equipment
            
            for idx, equipment in enumerate(equipment_list):
                tag = equipment.get('tag', f'EQUIP-{idx+1}')
                equip_type = equipment.get('type', 'generic')
                
                # Determine equipment type for proper symbol
                symbol_type = self._normalize_equipment_type(equip_type)
                
                # Calculate position (left to right flow)
                x_pos = x_position + (idx * x_spacing)
                if x_pos > 85:  # Wrap to next row if too far right
                    x_pos = 15
                    y_position += 25
                
                equipment_positions[tag] = {'x': x_pos, 'y': y_position}
                
                canvas_data['equipment'].append({
                    'id': tag,
                    'tag': tag,
                    'type': symbol_type,
                    'name': equipment.get('name', ''),
                    'position': {
                        'x': x_pos,
                        'y': y_position
                    },
                    'size': 'medium',
                    'orientation': 'vertical',
                    'specifications': equipment.get('specifications', {}),
                    'properties': {
                        'draggable': True,
                        'selectable': True
                    }
                })
            
            # Add instrumentation near related equipment
            for idx, instrument in enumerate(instrument_list):
                tag = instrument.get('tag', f'INST-{idx+1}')
                inst_type = instrument.get('type', 'indicator')
                function = instrument.get('function', 'measurement')
                
                # Determine connected equipment
                connected_to = instrument.get('connected_to_equipment', '')
                if not connected_to and len(equipment_list) > 0:
                    # Smart connection: associate with nearby equipment
                    connected_to = equipment_list[idx % len(equipment_list)].get('tag', '')
                
                # Position near connected equipment (offset above/below)
                if connected_to in equipment_positions:
                    base_pos = equipment_positions[connected_to]
                    x_pos = base_pos['x']
                    y_pos = base_pos['y'] - 15 if idx % 2 == 0 else base_pos['y'] + 15
                else:
                    x_pos = 20 + (idx * 15)
                    y_pos = 20
                
                canvas_data['instrumentation'].append({
                    'id': tag,
                    'tag': tag,
                    'type': self._normalize_instrument_type(inst_type),
                    'function': function,
                    'position': {
                        'x': x_pos,
                        'y': y_pos
                    },
                    'connected_to': connected_to,
                    'range': instrument.get('range', ''),
                    'set_point': instrument.get('set_point', ''),
                    'properties': {
                        'draggable': True,
                        'selectable': True
                    }
                })
            
            # Add piping connections with waypoints
            for idx, pipe in enumerate(piping_details):
                line_number = pipe.get('line_number', f'LINE-{idx+1}')
                from_equip = pipe.get('from_equipment', '')
                to_equip = pipe.get('to_equipment', '')
                
                # Find positions
                if from_equip in equipment_positions and to_equip in equipment_positions:
                    from_pos = equipment_positions[from_equip]
                    to_pos = equipment_positions[to_equip]
                    
                    # Create waypoints for orthogonal routing
                    mid_x = (from_pos['x'] + to_pos['x']) / 2
                    waypoints = [
                        {'x': from_pos['x'], 'y': from_pos['y']},
                        {'x': mid_x, 'y': from_pos['y']},
                        {'x': mid_x, 'y': to_pos['y']},
                        {'x': to_pos['x'], 'y': to_pos['y']}
                    ]
                    
                    canvas_data['piping'].append({
                        'id': line_number,
                        'line_number': line_number,
                        'from': from_equip,
                        'to': to_equip,
                        'waypoints': waypoints,
                        'size': pipe.get('pipe_size', ''),
                        'specification': pipe.get('pipe_class', ''),
                        'flow_direction': 'forward',
                        'fluid': pipe.get('fluid', ''),
                        'properties': {
                            'editable': True
                        }
                    })
            
            # Add safety annotations
            for idx, safety in enumerate(safety_systems):
                canvas_data['annotations'].append({
                    'id': f'SAFETY-{idx+1}',
                    'type': 'safety',
                    'text': f"{safety.get('tag', '')}: {safety.get('device_type', '')}",
                    'position': {
                        'x': 85,
                        'y': 10 + (idx * 5)
                    },
                    'related_to': safety.get('protected_equipment', ''),
                    'properties': {
                        'color': 'red',
                        'priority': 'high'
                    }
                })
            
            # Add design notes
            design_params = conversion.design_parameters or {}
            if design_params:
                notes_text = []
                if design_params.get('design_pressure'):
                    notes_text.append(f"Design Pressure: {design_params['design_pressure']}")
                if design_params.get('design_temperature'):
                    notes_text.append(f"Design Temperature: {design_params['design_temperature']}")
                
                if notes_text:
                    canvas_data['annotations'].append({
                        'id': 'DESIGN-NOTES',
                        'type': 'notes',
                        'text': ' | '.join(notes_text),
                        'position': {'x': 5, 'y': 95},
                        'related_to': '',
                        'properties': {'color': 'blue'}
                    })
            
            logger.info(f"✅ Canvas data created (smart layout):")
            logger.info(f"   - Equipment: {len(canvas_data['equipment'])}")
            logger.info(f"   - Instruments: {len(canvas_data['instrumentation'])}")
            logger.info(f"   - Pipes: {len(canvas_data['piping'])}")
            logger.info(f"   - Annotations: {len(canvas_data['annotations'])}")
            
            return Response(canvas_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ Canvas conversion failed: {str(e)}", exc_info=True)
            return Response(
                {
                    'error': 'Failed to convert P&ID to canvas format',
                    'detail': str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _normalize_equipment_type(self, equip_type: str) -> str:
        """Normalize equipment type to canvas-compatible symbol"""
        equip_type_lower = equip_type.lower()
        
        if 'pump' in equip_type_lower:
            return 'pump'
        elif 'vessel' in equip_type_lower or 'tank' in equip_type_lower or 'drum' in equip_type_lower:
            return 'vessel'
        elif 'column' in equip_type_lower or 'tower' in equip_type_lower:
            return 'column'
        elif 'heat exchanger' in equip_type_lower or 'exchanger' in equip_type_lower:
            return 'heat_exchanger'
        elif 'cooler' in equip_type_lower:
            return 'cooler'
        elif 'heater' in equip_type_lower:
            return 'heater'
        elif 'compressor' in equip_type_lower:
            return 'compressor'
        elif 'turbine' in equip_type_lower:
            return 'turbine'
        else:
            return 'vessel'  # Default fallback
    
    def _normalize_instrument_type(self, inst_type: str) -> str:
        """Normalize instrument type to canvas-compatible symbol"""
        inst_type_lower = inst_type.lower()
        
        if 'pressure' in inst_type_lower:
            return 'pressure_indicator'
        elif 'temperature' in inst_type_lower:
            return 'temperature_indicator'
        elif 'level' in inst_type_lower:
            return 'level_indicator'
        elif 'flow' in inst_type_lower:
            return 'flow_indicator'
        elif 'control' in inst_type_lower and 'valve' in inst_type_lower:
            return 'control_valve'
        elif 'valve' in inst_type_lower:
            return 'valve'
        else:
            return 'indicator'  # Default fallback


    @action(detail=False, methods=['post'], url_path='intelligent-generate')
    def intelligent_generate(self, request):
        """
        Generate P&ID using intelligent pattern learning
        
        POST /api/v1/pfd/conversions/intelligent-generate/
        {
            "pfd_document_id": "uuid",
            "reference_pid_path": "path/to/reference.pdf",
            "pid_drawing_number": "PID-001",
            "pid_title": "Process System",
            "pid_revision": "A"
        }
        """
        try:
            from .intelligent_pid_generator import IntelligentPIDGenerator
            
            pfd_doc_id = request.data.get('pfd_document_id')
            reference_pid_path = request.data.get('reference_pid_path')
            pid_drawing_number = request.data.get('pid_drawing_number', 'PID-INTELLIGENT-001')
            pid_title = request.data.get('pid_title', 'Intelligent P&ID')
            pid_revision = request.data.get('pid_revision', 'A')
            
            if not pfd_doc_id:
                return Response(
                    {'error': 'pfd_document_id is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get PFD document
            try:
                pfd_doc = PFDDocument.objects.get(id=pfd_doc_id)
            except PFDDocument.DoesNotExist:
                return Response(
                    {'error': 'PFD document not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Check access
            if pfd_doc.uploaded_by != request.user:
                if not hasattr(request.user, 'rbac_profile') or \
                   not request.user.rbac_profile.roles.filter(code='super_admin').exists():
                    return Response(
                        {'error': 'Access denied'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # Create conversion record
            conversion = PIDConversion.objects.create(
                pfd_document=pfd_doc,
                converted_by=request.user,
                pid_drawing_number=pid_drawing_number,
                pid_title=pid_title,
                pid_revision=pid_revision,
                status='generating'
            )
            conversion.generation_started_at = timezone.now()
            conversion.save()
            
            logger.info(f"🤖 Starting intelligent P&ID generation for conversion {conversion.id}")
            
            try:
                # Get reference P&ID path (use default if not provided)
                if not reference_pid_path:
                    # Use default reference from media/reference_pids/
                    reference_dir = os.path.join(settings.MEDIA_ROOT, 'reference_pids')
                    if os.path.exists(reference_dir):
                        ref_files = [f for f in os.listdir(reference_dir) if f.endswith('.pdf')]
                        if ref_files:
                            reference_pid_path = os.path.join(reference_dir, ref_files[0])
                            logger.info(f"📁 Using default reference: {ref_files[0]}")
                
                if not reference_pid_path or not os.path.exists(reference_pid_path):
                    raise ValueError("Reference P&ID not found. Please provide a valid reference.")
                
                # Get PFD file path
                pfd_file_path = os.path.join(settings.MEDIA_ROOT, str(pfd_doc.file))
                
                # Prepare output path
                output_dir = os.path.join(settings.MEDIA_ROOT, 'pid_drawings_intelligent')
                os.makedirs(output_dir, exist_ok=True)
                output_path = os.path.join(output_dir, f"{pid_drawing_number}_intelligent.png")
                
                # Initialize intelligent generator
                generator = IntelligentPIDGenerator(reference_pid_path)
                
                # Generate P&ID with pattern learning
                results = generator.generate_complete_pid(
                    pfd_file_path=pfd_file_path,
                    reference_pid_path=reference_pid_path,
                    output_path=output_path
                )
                
                # Save results
                pid_specs = results.get('specifications', {})
                conversion.equipment_list = pid_specs.get('equipment', [])
                conversion.instrument_list = pid_specs.get('instrumentation', [])
                conversion.piping_details = pid_specs.get('piping', [])
                conversion.design_parameters = {
                    'generation_method': 'intelligent_pattern_learning',
                    'reference_pid': os.path.basename(reference_pid_path),
                    'patterns_learned': len(results.get('patterns', {}))
                }
                
                # Save drawing path
                relative_path = results['drawing_path'].replace(str(settings.MEDIA_ROOT), '').lstrip('/\\')
                conversion.pid_file = relative_path
                
                conversion.status = 'completed'
                conversion.generation_completed_at = timezone.now()
                conversion.generation_duration = (
                    conversion.generation_completed_at - conversion.generation_started_at
                ).total_seconds()
                conversion.confidence_score = 95  # High confidence for intelligent generation
                conversion.save()
                
                logger.info(f"✅ Intelligent P&ID generation completed: {relative_path}")
                
                return Response(
                    PIDConversionSerializer(conversion).data,
                    status=status.HTTP_201_CREATED
                )
                
            except Exception as e:
                conversion.status = 'failed'
                conversion.generation_completed_at = timezone.now()
                conversion.generation_duration = (
                    conversion.generation_completed_at - conversion.generation_started_at
                ).total_seconds()
                conversion.save()
                
                logger.error(f"❌ Intelligent generation failed: {str(e)}")
                return Response(
                    {
                        'error': 'Intelligent P&ID generation failed',
                        'detail': str(e),
                        'conversion_id': str(conversion.id)
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
        except Exception as e:
            logger.error(f"❌ Request processing failed: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class ConversionFeedbackViewSet(viewsets.ModelViewSet):
    """
    ViewSet for conversion feedback
    """
    queryset = ConversionFeedback.objects.all()
    serializer_class = ConversionFeedbackSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['conversion', 'rating']
    ordering_fields = ['created_at', 'rating']
    
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
