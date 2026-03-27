from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.utils import timezone
from django.shortcuts import get_object_or_404
from .models import PIDDrawing, PIDAnalysisReport, PIDIssue, ReferenceDocument
from .serializers import (
    PIDDrawingSerializer,
    PIDDrawingUploadSerializer,
    PIDAnalysisReportSerializer,
    PIDIssueSerializer,
    IssueUpdateSerializer,
    ReferenceDocumentSerializer,
    ReferenceDocumentUploadSerializer
)
from .services import PIDAnalysisService
from .rag_service import RAGService
from .document_processor import DocumentProcessor


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def import_linelist_from_designiq(request):
    """
    SOFT-CODED: Import line list (or critical stress list) data from DesignIQ for P&ID cross-checking.
    GET /api/v1/pid/import-linelist/
      ?project_id=<int>            — return line list items for a specific DesignIQ project
      ?project_id=<int>&list_type=critical_stress — return critical stress line list items
      (no params)                  — return list of DesignIQ projects that have line list items
    """
    try:
        from apps.designiq.models import EngineeringListItem, DesignProject
    except ImportError:
        return Response({'error': 'DesignIQ module not available'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    project_id = request.query_params.get('project_id')
    list_type = request.query_params.get('list_type', 'line_list')
    # Whitelist allowed list types to prevent unintended data exposure
    allowed_list_types = {'line_list', 'critical_stress', 'equipment_list', 'tie_in_list', 'alarm_trip_list'}
    if list_type not in allowed_list_types:
        list_type = 'line_list'

    if not project_id:
        # Return list of projects that have line list items (scoped to current user's org)
        projects_with_lists = (
            DesignProject.objects
            .filter(items__list_type=list_type)
            .distinct()
            .values('id', 'name', 'description', 'created_at')
            .order_by('-created_at')
        )
        return Response({
            'projects': list(projects_with_lists),
            'count': len(projects_with_lists)
        })

    # Return serialised list items for the given project and list type
    try:
        project = DesignProject.objects.get(id=project_id)
    except DesignProject.DoesNotExist:
        return Response({'error': 'Project not found'}, status=status.HTTP_404_NOT_FOUND)

    items = EngineeringListItem.objects.filter(
        project=project,
        list_type=list_type
    ).values('item_tag', 'data', 'status', 'version')

    line_list = [
        {
            'item_tag': item['item_tag'],
            'data': item['data'] or {},
            'status': item['status'],
            'version': item['version'],
        }
        for item in items
    ]

    return Response({
        'project_id': project.id,
        'project_name': project.name,
        'list_type': list_type,
        'line_list': line_list,
        'count': len(line_list),
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def export_report(request, pk):
    """
    Standalone export view for P&ID reports
    GET /api/v1/pid/drawings/{id}/export/?format=pdf|excel|csv
    """
    from .export_service import PIDReportExportService
    
    print(f"[EXPORT] ===== STANDALONE EXPORT REQUEST =====")
    print(f"[EXPORT] User: {request.user} (authenticated: {request.user.is_authenticated})")
    print(f"[EXPORT] Drawing ID: {pk}")
    
    # Get drawing - filter by user for security
    drawing = get_object_or_404(PIDDrawing, id=pk, uploaded_by=request.user)
    print(f"[EXPORT] Drawing found: {drawing.drawing_number}")
    
    # Check if report exists
    if not hasattr(drawing, 'analysis_report'):
        print(f"[EXPORT ERROR] No analysis report for drawing {pk}")
        return Response(
            {'error': 'No analysis report available'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    print(f"[EXPORT] Report found: {drawing.analysis_report.id}")
    
    export_format = request.query_params.get('format', 'pdf')
    print(f"[EXPORT] Format: {export_format}")
    
    export_service = PIDReportExportService()
    
    try:
        print(f"[EXPORT] Generating {export_format}...")
        if export_format == 'pdf':
            response = export_service.export_pdf(drawing)
        elif export_format == 'excel':
            response = export_service.export_excel(drawing)
        elif export_format == 'csv':
            response = export_service.export_csv(drawing)
        else:
            return Response(
                {'error': 'Invalid format. Use pdf, excel, or csv'},
                status=status.HTTP_400_BAD_REQUEST
            )
        print(f"[EXPORT] Success - returning file")
        return response
    except Exception as e:
        print(f"[EXPORT ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return Response(
            {'error': f'Export failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


class PIDDrawingViewSet(viewsets.ModelViewSet):
    """ViewSet for P&ID drawings"""
    
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PIDDrawingSerializer
    parser_classes = [MultiPartParser, FormParser]  # Enable multipart parsing
    
    def get_queryset(self):
        """Return drawings for current user"""
        return PIDDrawing.objects.filter(uploaded_by=self.request.user)
    
    def perform_create(self, serializer):
        """Create drawing with current user"""
        serializer.save(uploaded_by=self.request.user)
    
    @action(detail=False, methods=['post', 'options'], permission_classes=[permissions.AllowAny])
    def upload(self, request):
        """
        Upload P&ID drawing and optionally start analysis
        
        POST /api/v1/pid/drawings/upload/
        """
        print(f"[UPLOAD_VIEW] === UPLOAD REQUEST RECEIVED ===")
        print(f"[UPLOAD_VIEW] Method: {request.method}")
        print(f"[UPLOAD_VIEW] User: {request.user} (authenticated: {request.user.is_authenticated})")
        print(f"[UPLOAD_VIEW] Auth header: {request.META.get('HTTP_AUTHORIZATION', 'MISSING')}")
        print(f"[UPLOAD_VIEW] Origin: {request.META.get('HTTP_ORIGIN', 'NO ORIGIN')}")
        print(f"[UPLOAD_VIEW] Content-Type: {request.content_type}")
        print(f"[UPLOAD_VIEW] Files: {list(request.FILES.keys())}")
        
        # Handle OPTIONS request for CORS preflight
        if request.method == 'OPTIONS':
            print("[UPLOAD_VIEW] HANDLING OPTIONS PREFLIGHT")
            from django.http import HttpResponse
            response = HttpResponse()
            response['Access-Control-Allow-Origin'] = '*'
            response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
            response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            return response
        
        # CRITICAL: Verify authentication manually since we used AllowAny
        if not request.user.is_authenticated:
            print("[ERROR] User not authenticated - rejecting request")
            return Response(
                {'detail': 'Authentication credentials were not provided or are invalid.'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        print(f"[DEBUG] ===== UPLOAD REQUEST RECEIVED =====")
        print(f"[DEBUG] User: {request.user} (authenticated: {request.user.is_authenticated})")
        print(f"[DEBUG] Content-Type: {request.content_type}")
        print(f"[DEBUG] Request data keys: {list(request.data.keys())}")
        print(f"[DEBUG] Request FILES: {list(request.FILES.keys())}")
        print(f"[DEBUG] Request encoding: {getattr(request, 'encoding', 'Unknown')}")
        
        # Debug all form fields
        for key, value in request.data.items():
            if key == 'file':
                file_obj = request.FILES.get('file')
                if file_obj:
                    print(f"[DEBUG]   {key}: [File: {file_obj.name}, {file_obj.size} bytes, {file_obj.content_type}]")
                else:
                    print(f"[DEBUG]   {key}: {value} (NOT A FILE OBJECT!)")
            else:
                print(f"[DEBUG]   {key}: '{value}' (type: {type(value).__name__})")
        
        # Check if file is in FILES instead of data
        file_in_files = request.FILES.get('file')
        if file_in_files:
            print(f"[DEBUG] ✓ File found in request.FILES: {file_in_files.name}")
        else:
            print(f"[DEBUG] ✗ No file found in request.FILES")
        
        # Prepare data for serializer - DON'T use .copy() on request.data as it may deepcopy file objects
        # Instead, create a new dict with only the non-file fields
        serializer_data = {}
        for key, value in request.data.items():
            # Skip file fields - we'll add them from request.FILES
            if not key.startswith('reference_') and key != 'file':
                serializer_data[key] = value
        
        # Add the main file from request.FILES (not from request.data)
        if file_in_files:
            serializer_data['file'] = file_in_files
        
        print(f"[DEBUG] Serializer data keys: {list(serializer_data.keys())}")
        
        # SOFT-CODED: Extract reference documents if present (5 key documents only)
        reference_documents = {}
        ref_doc_keys = ['equipment_list', 'line_list', 'alarm_trip_schedule', 
                        'instrument_datasheet', 'legends_symbols']
        
        for key in ref_doc_keys:
            ref_key = f'reference_{key}'
            if ref_key in request.FILES:
                reference_documents[key] = request.FILES[ref_key]
                print(f"[DEBUG] Reference document found: {key} - {reference_documents[key].name}")
        
        # SOFT-CODED: Handle JSON line list imported from DesignIQ (no file upload needed)
        line_list_json_raw = request.data.get('line_list_json')
        if line_list_json_raw:
            import json as _json
            try:
                parsed = _json.loads(line_list_json_raw) if isinstance(line_list_json_raw, str) else line_list_json_raw
                reference_documents['line_list_json'] = parsed
                print(f"[DEBUG] DesignIQ line list JSON received: {len(parsed) if isinstance(parsed, list) else 'dict'} items")
            except Exception as _e:
                print(f"[WARNING] Failed to parse line_list_json: {_e}")

        # SOFT-CODED: Handle JSON critical stress line list imported from DesignIQ
        critical_stress_json_raw = request.data.get('critical_stress_json')
        if critical_stress_json_raw:
            import json as _json
            try:
                parsed_css = _json.loads(critical_stress_json_raw) if isinstance(critical_stress_json_raw, str) else critical_stress_json_raw
                reference_documents['critical_stress_json'] = parsed_css
                print(f"[DEBUG] DesignIQ critical stress JSON received: {len(parsed_css) if isinstance(parsed_css, list) else 'dict'} items")
            except Exception as _e:
                print(f"[WARNING] Failed to parse critical_stress_json: {_e}")

        # ── Analysis mode: 'standard' (drawing-only) vs 'premium' (with RAD/external refs) ──
        # In Standard mode, reference documents are intentionally cleared so Pass 2
        # is skipped and the AI report is based solely on the uploaded drawing.
        # In Premium mode, all uploaded reference files and DesignIQ JSON are used.
        analysis_mode = (request.data.get('analysis_mode') or 'standard').lower()
        if analysis_mode not in ('standard', 'premium'):
            analysis_mode = 'standard'

        if analysis_mode == 'standard' and reference_documents:
            print(f"[INFO] Standard mode selected — clearing {len(reference_documents)} reference doc(s) (drawing-only analysis)")
            reference_documents = {}
        elif analysis_mode == 'premium':
            print(f"[INFO] Premium mode selected — {len(reference_documents)} reference doc(s) will be used")

        if reference_documents:
            print(f"[DEBUG] Total reference documents: {len(reference_documents)}")
        
        # Validate request data
        serializer = PIDDrawingUploadSerializer(data=serializer_data)
        
        if not serializer.is_valid():
            print(f"[ERROR] Validation failed: {serializer.errors}")
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )
        
        print(f"[DEBUG] Serializer validated successfully")
        print(f"[DEBUG] Validated data: {serializer.validated_data.keys()}")
        
        # Create or reuse PIDDrawing
        # If the same user uploads the same filename, reuse the existing drawing record
        # so the report URL stays stable and the latest analysis always overwrites the previous one.
        file = serializer.validated_data['file']
        existing_drawing = PIDDrawing.objects.filter(
            uploaded_by=request.user,
            original_filename=file.name
        ).order_by('-created_at').first()

        if existing_drawing:
            print(f'[UPLOAD_VIEW] Existing drawing found (ID: {existing_drawing.id}) for filename "{file.name}" — reusing record')
            # Delete previous report and issues so a fresh analysis is saved
            if hasattr(existing_drawing, 'analysis_report'):
                existing_drawing.analysis_report.issues.all().delete()
                existing_drawing.analysis_report.delete()
            # Update the drawing record with the new file
            existing_drawing.file = file
            existing_drawing.file_size = file.size
            existing_drawing.status = 'uploaded'
            existing_drawing.error_message = ''
            existing_drawing.analysis_started_at = None
            existing_drawing.analysis_completed_at = None
            existing_drawing.save()
            drawing = existing_drawing
        else:
            drawing = PIDDrawing.objects.create(
                file=file,
                original_filename=file.name,
                file_size=file.size,
                drawing_number=serializer.validated_data.get('drawing_number', ''),
                drawing_title=serializer.validated_data.get('drawing_title', ''),
                revision=serializer.validated_data.get('revision', ''),
                project_name=serializer.validated_data.get('project_name', ''),
                uploaded_by=request.user,
                status='uploaded'
            )
        
        # SOFT-CODED: Save reference documents if provided and pass to analysis
        saved_reference_docs = {}
        saved_reference_files = {}  # Store file objects/paths separately
        if reference_documents:
            print(f"[DEBUG] Saving {len(reference_documents)} reference documents")
            for doc_type, doc_file in reference_documents.items():
                try:
                    ref_doc = ReferenceDocument.objects.create(
                        title=f"{doc_type.replace('_', ' ').title()} for {drawing.drawing_number}",
                        description=f"Reference document for P&ID {drawing.drawing_number}",
                        category='guideline',
                        file=doc_file,
                        original_filename=doc_file.name,
                        file_size=doc_file.size,
                        uploaded_by=request.user
                    )
                    saved_reference_docs[doc_type] = ref_doc
                    # Store the file PATH STRING for processing (pickle-safe)
                    saved_reference_files[doc_type] = ref_doc.file.path  # String path is pickle-safe
                    print(f"[DEBUG] Saved reference document: {ref_doc.title} (ID: {ref_doc.id}, Path: {ref_doc.file.path})")
                except Exception as e:
                    print(f"[ERROR] Failed to save reference document {doc_type}: {e}")
        
        # Auto-analyze if requested (WITH REFERENCE DOCUMENTS)
        if serializer.validated_data.get('auto_analyze', True):
            try:
                print(f"[DEBUG] Starting auto-analysis for drawing ID: {drawing.id}")
                # Start analysis
                drawing.status = 'processing'
                drawing.analysis_started_at = timezone.now()
                drawing.save()
                
                # Perform analysis with reference documents
                print(f"[DEBUG] Initializing PIDAnalysisService with {len(saved_reference_docs)} reference documents")
                analysis_service = PIDAnalysisService()
                print(f"[DEBUG] Calling analyze_pid_drawing with file path: {drawing.file.path}")
                # Pass the file PATH STRING (not FieldFile) and reference file paths for pickle-safe processing
                analysis_result = analysis_service.analyze_pid_drawing(
                    drawing.file.path,  # STRING path, not FieldFile
                    drawing_number=drawing.drawing_number,
                    reference_documents=saved_reference_files,  # Dict of STRING paths
                    analysis_mode=analysis_mode,
                )
                print(f"[DEBUG] Analysis completed with reference verification, result keys: {list(analysis_result.keys())}")
                
                # Create report
                report = PIDAnalysisReport.objects.create(
                    pid_drawing=drawing,
                    report_data=analysis_result,
                    total_issues=len(analysis_result.get('issues', [])),
                )
                
                # Create issues
                for issue_data in analysis_result.get('issues', []):
                    PIDIssue.objects.create(
                        report=report,
                        serial_number=issue_data.get('serial_number', 0),
                        pid_reference=issue_data.get('pid_reference', ''),
                        issue_observed=issue_data.get('issue_observed', ''),
                        action_required=issue_data.get('action_required', ''),
                        evidence=issue_data.get('evidence', ''),
                        severity=issue_data.get('severity', 'observation'),
                        category=issue_data.get('category', ''),
                        location_on_drawing=issue_data.get('location_on_drawing'),
                        status=issue_data.get('status', 'pending'),
                        approval=issue_data.get('approval', 'Pending'),
                        remark=issue_data.get('remark', 'Pending'),
                    )
                
                # Update report summary
                summary = analysis_service.generate_report_summary(analysis_result.get('issues', []))
                report.approved_count = summary['approved_count']
                report.ignored_count = summary['ignored_count']
                report.pending_count = summary['pending_count']
                report.save()
                
                # Update drawing
                drawing.status = 'completed'
                drawing.analysis_completed_at = timezone.now()
                
                # Update drawing metadata from analysis if available
                if 'drawing_info' in analysis_result:
                    drawing_info = analysis_result['drawing_info']
                    if not drawing.drawing_number and drawing_info.get('drawing_number'):
                        drawing.drawing_number = drawing_info['drawing_number']
                    if not drawing.drawing_title and drawing_info.get('drawing_title'):
                        drawing.drawing_title = drawing_info['drawing_title']
                    if not drawing.revision and drawing_info.get('revision'):
                        drawing.revision = drawing_info['revision']
                
                drawing.save()
                
            except Exception as e:
                print(f"[ERROR] Analysis failed: {type(e).__name__}: {str(e)}")
                import traceback
                traceback.print_exc()
                
                drawing.status = 'failed'
                drawing.error_message = str(e)[:500]  # Store error for debugging
                drawing.save()
                
                # Provide detailed error response
                error_message = str(e)
                error_type = type(e).__name__
                
                # Check for specific error types and provide helpful messages
                if "OPENAI_API_KEY" in error_message or "API key" in error_message:
                    user_message = "OpenAI API key is not configured or invalid. Please contact administrator."
                elif "quota" in error_message.lower():
                    user_message = "OpenAI API quota exceeded. Please contact administrator."
                elif "rate_limit" in error_message.lower():
                    user_message = "Too many requests. Please wait a moment and try again."
                elif "invalid JSON" in error_message:
                    user_message = f"Analysis processing error: {error_message}"
                else:
                    user_message = f"Analysis failed: {error_message}"
                
                return Response(
                    {
                        'success': False,
                        'error': user_message,
                        'error_type': error_type,
                        'drawing_id': drawing.id,
                        'details': error_message if request.user.is_staff else None  # Full details only for staff
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        
        # Return created drawing
        response_data = PIDDrawingSerializer(drawing).data
        response_data['success'] = True
        print(f"[DEBUG] Upload successful, drawing ID: {drawing.id}, status: {drawing.status}")
        
        return Response(
            response_data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['post'])
    def analyze(self, request, pk=None):
        """
        Trigger analysis for a specific drawing
        
        POST /api/v1/pid/drawings/{id}/analyze/
        """
        drawing = self.get_object()
        
        if drawing.status == 'processing':
            return Response(
                {'error': 'Analysis already in progress'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Start analysis
            drawing.status = 'processing'
            drawing.analysis_started_at = timezone.now()
            drawing.save()
            
            # Perform analysis
            analysis_service = PIDAnalysisService()
            # Pass the file object directly (works with both S3 and local storage)
            analysis_result = analysis_service.analyze_pid_drawing(drawing.file)
            
            # Delete existing report if any
            if hasattr(drawing, 'analysis_report'):
                drawing.analysis_report.delete()
            
            # Create new report
            report = PIDAnalysisReport.objects.create(
                pid_drawing=drawing,
                report_data=analysis_result,
                total_issues=len(analysis_result.get('issues', [])),
            )
            
            # Create issues
            for issue_data in analysis_result.get('issues', []):
                PIDIssue.objects.create(
                    report=report,
                    serial_number=issue_data.get('serial_number', 0),
                    pid_reference=issue_data.get('pid_reference', ''),
                    issue_observed=issue_data.get('issue_observed', ''),
                    action_required=issue_data.get('action_required', ''),
                    evidence=issue_data.get('evidence', ''),
                    severity=issue_data.get('severity', 'observation'),
                    category=issue_data.get('category', ''),
                    location_on_drawing=issue_data.get('location_on_drawing'),
                    status=issue_data.get('status', 'pending'),
                    approval=issue_data.get('approval', 'Pending'),
                    remark=issue_data.get('remark', 'Pending'),
                )
            
            # Update report summary
            summary = analysis_service.generate_report_summary(analysis_result.get('issues', []))
            report.approved_count = summary['approved_count']
            report.ignored_count = summary['ignored_count']
            report.pending_count = summary['pending_count']
            report.save()
            
            # Update drawing status
            drawing.status = 'completed'
            drawing.analysis_completed_at = timezone.now()
            drawing.save()
            
            return Response(
                PIDDrawingSerializer(drawing).data,
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            drawing.status = 'failed'
            drawing.save()
            return Response(
                {'error': f'Analysis failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def report(self, request, pk=None):
        """
        Get analysis report for a drawing
        
        GET /api/v1/pid/drawings/{id}/report/
        """
        import logging
        logger = logging.getLogger(__name__)
        
        drawing = self.get_object()
        logger.debug(f"[report] Fetching report for drawing {drawing.id} (status: {drawing.status})")
        
        if not hasattr(drawing, 'analysis_report'):
            logger.warning(f"[report] No analysis_report found for drawing {drawing.id}")
            return Response(
                {'error': 'No analysis report available'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        report = drawing.analysis_report
        logger.debug(f"[report] Found report {report.id}, total_issues: {report.total_issues}")
        logger.debug(f"[report] Report has report_data: {bool(report.report_data)}")
        logger.debug(f"[report] DB issues count: {report.issues.count()}")
        
        if isinstance(report.report_data, dict):
            logger.debug(f"[report] report_data keys: {list(report.report_data.keys())}")
            issues_in_data = report.report_data.get('issues', [])
            logger.debug(f"[report] Issues in report_data: {len(issues_in_data)}")
        
        serialized_data = PIDAnalysisReportSerializer(report).data
        logger.debug(f"[report] Serialized issues count: {len(serialized_data.get('issues', []))}")
        
        return Response(
            serialized_data,
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['get'], url_path='export', permission_classes=[permissions.AllowAny])
    def export(self, request, pk=None):
        """
        Export report in different formats (PDF, Excel, CSV)
        
        GET /api/v1/pid/drawings/{id}/export/?format=pdf|excel|csv
        """
        import sys
        from .export_service import PIDReportExportService
        
        # EXTREMELY VERBOSE LOGGING
        print(f"\n{'='*80}", file=sys.stderr)
        print(f"[EXPORT ACTION] ===== EXPORT REQUEST RECEIVED =====", file=sys.stderr)
        print(f"[EXPORT ACTION] Request path: {request.path}", file=sys.stderr)
        print(f"[EXPORT ACTION] Request method: {request.method}", file=sys.stderr)
        print(f"[EXPORT ACTION] User: {request.user} (authenticated: {request.user.is_authenticated})", file=sys.stderr)
        print(f"[EXPORT ACTION] Drawing ID (pk): {pk}", file=sys.stderr)
        print(f"[EXPORT ACTION] Query params: {dict(request.query_params)}", file=sys.stderr)
        print(f"{'='*80}\n", file=sys.stderr)
        
        print(f"[EXPORT] ===== EXPORT REQUEST RECEIVED =====")
        print(f"[EXPORT] User: {request.user} (authenticated: {request.user.is_authenticated})")
        print(f"[EXPORT] Drawing ID: {pk}")
        
        # Get drawing without user filter for testing
        try:
            drawing = PIDDrawing.objects.get(id=pk)
            print(f"[EXPORT] Drawing found: {drawing.drawing_number}")
        except PIDDrawing.DoesNotExist:
            print(f"[EXPORT ERROR] Drawing {pk} does not exist")
            return Response(
                {'error': f'Drawing {pk} not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        if not hasattr(drawing, 'analysis_report'):
            print(f"[EXPORT ERROR] No analysis report available for drawing {pk}")
            return Response(
                {'error': 'No analysis report available'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        print(f"[EXPORT] Report found: {drawing.analysis_report.id}")
        
        export_format = request.query_params.get('format', 'pdf')
        print(f"[EXPORT] Format requested: {export_format}")
        
        export_service = PIDReportExportService()
        
        try:
            print(f"[EXPORT] Starting export as {export_format}...")
            if export_format == 'pdf':
                response = export_service.export_pdf(drawing)
            elif export_format == 'excel':
                response = export_service.export_excel(drawing)
            elif export_format == 'csv':
                response = export_service.export_csv(drawing)
            else:
                print(f"[EXPORT ERROR] Invalid format: {export_format}")
                return Response(
                    {'error': 'Invalid format. Use pdf, excel, or csv'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            print(f"[EXPORT] Export successful, returning {export_format} file")
            return response
        except Exception as e:
            print(f"[EXPORT ERROR] Export failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {'error': f'Export failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PIDAnalysisReportViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for P&ID analysis reports (read-only)"""
    
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PIDAnalysisReportSerializer
    
    def get_queryset(self):
        """Return reports for current user's drawings"""
        return PIDAnalysisReport.objects.filter(
            pid_drawing__uploaded_by=self.request.user
        )


class PIDIssueViewSet(viewsets.ModelViewSet):
    """ViewSet for P&ID issues"""
    
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PIDIssueSerializer
    
    def get_queryset(self):
        """Return issues for current user's reports"""
        return PIDIssue.objects.filter(
            report__pid_drawing__uploaded_by=self.request.user
        )
    
    def get_serializer_class(self):
        """Use different serializer for updates"""
        if self.action in ['update', 'partial_update']:
            return IssueUpdateSerializer
        return PIDIssueSerializer
    
    def partial_update(self, request, *args, **kwargs):
        """Handle PATCH requests for updating issue fields"""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        
        # Update report counts after any change
        self._update_report_counts(instance.report)
        
        return Response(PIDIssueSerializer(instance).data)
    
    def update(self, request, *args, **kwargs):
        """Handle PUT requests for updating issue"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        
        # Update report counts after any change
        self._update_report_counts(instance.report)
        
        return Response(PIDIssueSerializer(instance).data)
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        Approve an issue
        
        POST /api/v1/pid/issues/{id}/approve/
        """
        issue = self.get_object()
        issue.status = 'approved'
        issue.approval = 'Approved'
        if 'remark' in request.data:
            issue.remark = request.data['remark']
        issue.save()
        
        # Update report counts
        self._update_report_counts(issue.report)
        
        return Response(
            PIDIssueSerializer(issue).data,
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def ignore(self, request, pk=None):
        """
        Ignore an issue
        
        POST /api/v1/pid/issues/{id}/ignore/
        """
        issue = self.get_object()
        issue.status = 'ignored'
        issue.approval = 'Ignored'
        if 'remark' in request.data:
            issue.remark = request.data['remark']
        issue.save()
        
        # Update report counts
        self._update_report_counts(issue.report)
        
        return Response(
            PIDIssueSerializer(issue).data,
            status=status.HTTP_200_OK
        )
    
    def _update_report_counts(self, report):
        """Update report summary counts"""
        issues = report.issues.all()
        report.approved_count = issues.filter(status='approved').count()
        report.ignored_count = issues.filter(status='ignored').count()
        report.pending_count = issues.filter(status='pending').count()
        report.save()


class ReferenceDocumentViewSet(viewsets.ModelViewSet):
    """ViewSet for reference documents used in RAG"""
    
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ReferenceDocumentSerializer
    parser_classes = [MultiPartParser, FormParser]  # Enable multipart parsing
    
    def get_queryset(self):
        """Return reference documents for current user"""
        return ReferenceDocument.objects.filter(uploaded_by=self.request.user)
    
    def perform_create(self, serializer):
        """Create reference document with current user"""
        serializer.save(uploaded_by=self.request.user)
    
    @action(detail=False, methods=['post', 'options'])
    def upload(self, request):
        """
        Upload reference document and process for RAG
        
        POST /api/v1/pid/reference-documents/upload/
        """
        # Handle OPTIONS request for CORS preflight
        if request.method == 'OPTIONS':
            from django.http import HttpResponse
            response = HttpResponse()
            response['Access-Control-Allow-Origin'] = '*'
            response['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
            response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            return response
        print(f"[INFO] Reference document upload request from user: {request.user}")
        
        # Validate request data
        serializer = ReferenceDocumentUploadSerializer(data=request.data)
        
        if not serializer.is_valid():
            print(f"[ERROR] Validation failed: {serializer.errors}")
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Create reference document
            file = serializer.validated_data['file']
            ref_doc = ReferenceDocument.objects.create(
                title=serializer.validated_data['title'],
                description=serializer.validated_data.get('description', ''),
                category=serializer.validated_data['category'],
                file=file,
                uploaded_by=request.user,
                embedding_status='pending'
            )
            
            print(f"[INFO] Created reference document: {ref_doc.title} (ID: {ref_doc.id})")
            
            # Process document in background
            try:
                # Extract text from document
                processor = DocumentProcessor()
                # Soft-coded: Pass file object directly (works with both S3 and local storage)
                content_text = processor.extract_text(ref_doc.file, ref_doc.original_filename)
                
                if not content_text or len(content_text.strip()) < 50:
                    ref_doc.embedding_status = 'failed'
                    ref_doc.save()
                    return Response(
                        {'error': 'Failed to extract meaningful text from document'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                ref_doc.content_text = content_text
                ref_doc.save()
                
                print(f"[INFO] Extracted {len(content_text)} characters from document")
                
                # Add to RAG system (generate embeddings and store)
                rag_service = RAGService()
                chunk_data = rag_service.add_reference_document(
                    document_id=str(ref_doc.id),
                    content=content_text,
                    metadata={
                        'title': ref_doc.title,
                        'category': ref_doc.category,
                        'filename': file.name
                    }
                )
                
                # Update document with embedding data (stored as JSON)
                ref_doc.vector_db_ids = chunk_data  # This will be JSONified by Django
                ref_doc.chunk_count = len(chunk_data)
                ref_doc.embedding_status = 'completed'
                ref_doc.save()
                
                print(f"[INFO] Document embedded successfully with {len(chunk_data)} chunks")
                
            except Exception as e:
                print(f"[ERROR] Document processing failed: {str(e)}")
                import traceback
                traceback.print_exc()
                
                ref_doc.embedding_status = 'failed'
                ref_doc.save()
                
                return Response(
                    {'error': f'Document processing failed: {str(e)}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Return success response
            return Response(
                ReferenceDocumentSerializer(ref_doc, context={'request': request}).data,
                status=status.HTTP_201_CREATED
            )
            
        except Exception as e:
            print(f"[ERROR] Upload failed: {str(e)}")
            import traceback
            traceback.print_exc()
            
            return Response(
                {'error': f'Upload failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        """
        Activate a reference document for use in RAG
        
        POST /api/v1/pid/reference-documents/{id}/activate/
        """
        ref_doc = self.get_object()
        ref_doc.is_active = True
        ref_doc.save()
        
        return Response(
            ReferenceDocumentSerializer(ref_doc, context={'request': request}).data,
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        """
        Deactivate a reference document (won't be used in RAG)
        
        POST /api/v1/pid/reference-documents/{id}/deactivate/
        """
        ref_doc = self.get_object()
        ref_doc.is_active = False
        ref_doc.save()
        
        return Response(
            ReferenceDocumentSerializer(ref_doc, context={'request': request}).data,
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def reprocess(self, request, pk=None):
        """
        Reprocess a failed document
        
        POST /api/v1/pid/reference-documents/{id}/reprocess/
        """
        ref_doc = self.get_object()
        
        try:
            # Extract text from document
            processor = DocumentProcessor()
            # Soft-coded: Pass file object directly (works with both S3 and local storage)
            content_text = processor.extract_text(ref_doc.file, ref_doc.original_filename)
            
            if not content_text or len(content_text.strip()) < 50:
                return Response(
                    {'error': 'Failed to extract meaningful text from document'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            ref_doc.content_text = content_text
            ref_doc.embedding_status = 'processing'
            ref_doc.save()
            
            # Add to RAG system
            rag_service = RAGService()
            chunk_data = rag_service.add_reference_document(
                document_id=str(ref_doc.id),
                content=content_text,
                metadata={
                    'title': ref_doc.title,
                    'category': ref_doc.category,
                    'filename': ref_doc.file.name
                }
            )
            
            # Update document
            ref_doc.vector_db_ids = chunk_data
            ref_doc.chunk_count = len(chunk_data)
            ref_doc.embedding_status = 'completed'
            ref_doc.save()
            
            return Response(
                ReferenceDocumentSerializer(ref_doc, context={'request': request}).data,
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            ref_doc.embedding_status = 'failed'
            ref_doc.save()
            
            return Response(
                {'error': f'Reprocessing failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
