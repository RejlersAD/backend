from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import get_object_or_404
from .models import PIDDrawing, PIDAnalysisReport, PIDIssue
from .serializers import (
    PIDDrawingSerializer,
    PIDDrawingUploadSerializer,
    PIDAnalysisReportSerializer,
    PIDIssueSerializer,
    IssueUpdateSerializer
)
from .services import PIDAnalysisService


class PIDDrawingViewSet(viewsets.ModelViewSet):
    """ViewSet for P&ID drawings"""
    
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PIDDrawingSerializer
    
    def get_queryset(self):
        """Return drawings for current user"""
        return PIDDrawing.objects.filter(uploaded_by=self.request.user)
    
    def perform_create(self, serializer):
        """Create drawing with current user"""
        serializer.save(uploaded_by=self.request.user)
    
    @action(detail=False, methods=['post'])
    def upload(self, request):
        """
        Upload P&ID drawing and optionally start analysis
        
        POST /api/v1/pid/drawings/upload/
        """
        print(f"[DEBUG] ===== UPLOAD REQUEST RECEIVED =====")
        print(f"[DEBUG] User: {request.user} (authenticated: {request.user.is_authenticated})")
        print(f"[DEBUG] Content-Type: {request.content_type}")
        print(f"[DEBUG] Request data keys: {list(request.data.keys())}")
        print(f"[DEBUG] Request FILES: {list(request.FILES.keys())}")
        
        # Debug all form fields
        for key, value in request.data.items():
            if key == 'file':
                file_obj = request.FILES.get('file')
                if file_obj:
                    print(f"[DEBUG]   {key}: [File: {file_obj.name}, {file_obj.size} bytes, {file_obj.content_type}]")
                else:
                    print(f"[DEBUG]   {key}: {value}")
            else:
                print(f"[DEBUG]   {key}: '{value}' (type: {type(value).__name__})")
        
        # Validate request data
        serializer = PIDDrawingUploadSerializer(data=request.data)
        
        if not serializer.is_valid():
            print(f"[ERROR] Validation failed: {serializer.errors}")
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )
        
        print(f"[DEBUG] Serializer validated successfully")
        print(f"[DEBUG] Validated data: {serializer.validated_data.keys()}")
        
        # Create PIDDrawing
        file = serializer.validated_data['file']
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
        
        # Auto-analyze if requested
        if serializer.validated_data.get('auto_analyze', True):
            try:
                print(f"[DEBUG] Starting auto-analysis for drawing ID: {drawing.id}")
                # Start analysis
                drawing.status = 'processing'
                drawing.analysis_started_at = timezone.now()
                drawing.save()
                
                # Perform analysis
                print(f"[DEBUG] Initializing PIDAnalysisService")
                analysis_service = PIDAnalysisService()
                print(f"[DEBUG] Calling analyze_pid_drawing with file: {drawing.file.name}")
                # Pass the file object directly (works with both S3 and local storage)
                analysis_result = analysis_service.analyze_pid_drawing(drawing.file)
                print(f"[DEBUG] Analysis completed, result keys: {list(analysis_result.keys())}")
                
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
                        severity=issue_data.get('severity', 'observation'),
                        category=issue_data.get('category', ''),
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
                drawing.save()
                
                return Response(
                    {
                        'success': False,
                        'error': f'Analysis failed: {str(e)}',
                        'error_type': type(e).__name__,
                        'drawing_id': drawing.id
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
                    severity=issue_data.get('severity', 'observation'),
                    category=issue_data.get('category', ''),
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
        drawing = self.get_object()
        
        if not hasattr(drawing, 'analysis_report'):
            return Response(
                {'error': 'No analysis report available'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        return Response(
            PIDAnalysisReportSerializer(drawing.analysis_report).data,
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, pk=None):
        """
        Export report in different formats (PDF, Excel, CSV)
        
        GET /api/v1/pid/drawings/{id}/export/?format=pdf|excel|csv
        """
        from .export_service import PIDReportExportService
        
        drawing = self.get_object()
        
        if not hasattr(drawing, 'analysis_report'):
            return Response(
                {'error': 'No analysis report available'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        export_format = request.query_params.get('format', 'pdf')
        export_service = PIDReportExportService()
        
        try:
            if export_format == 'pdf':
                return export_service.export_pdf(drawing)
            elif export_format == 'excel':
                return export_service.export_excel(drawing)
            elif export_format == 'csv':
                return export_service.export_csv(drawing)
            else:
                return Response(
                    {'error': 'Invalid format. Use pdf, excel, or csv'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
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
