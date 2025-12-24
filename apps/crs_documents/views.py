"""
CRS Documents API Views
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import HttpResponse
from django.conf import settings
from io import BytesIO
import os

from .models import CRSDocument, CRSDocumentVersion
from .serializers import CRSDocumentSerializer, CRSDocumentVersionSerializer

# NEW: Import helper modules for PDF extraction and CRS population
try:
    from .helpers.comment_extractor import extract_reviewer_comments, get_comment_statistics
    from .helpers.template_populator import populate_crs_template, validate_template
    from .helpers.template_manager import get_crs_template, get_template_info
    HELPERS_AVAILABLE = True
except ImportError:
    HELPERS_AVAILABLE = False


class CRSDocumentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for CRS Documents
    Provides CRUD operations and custom actions
    """
    queryset = CRSDocument.objects.all()
    serializer_class = CRSDocumentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter documents based on user permissions"""
        queryset = super().get_queryset()
        
        # Filter by department if specified
        department = self.request.query_params.get('department')
        if department:
            queryset = queryset.filter(department=department)
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset
    
    def perform_create(self, serializer):
        """Set uploaded_by to current user"""
        serializer.save(uploaded_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a document"""
        document = self.get_object()
        document.status = 'approved'
        document.approved_by = request.user
        document.save()
        
        serializer = self.get_serializer(document)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def versions(self, request, pk=None):
        """Get all versions of a document"""
        document = self.get_object()
        versions = document.versions.all()
        serializer = CRSDocumentVersionSerializer(versions, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get document statistics"""
        total = self.get_queryset().count()
        by_status = {}
        by_department = {}
        
        for status_choice, _ in CRSDocument.STATUS_CHOICES:
            count = self.get_queryset().filter(status=status_choice).count()
            by_status[status_choice] = count
        
        for dept_choice, _ in CRSDocument.DEPARTMENT_CHOICES:
            count = self.get_queryset().filter(department=dept_choice).count()
            by_department[dept_choice] = count
        
        return Response({
            'total': total,
            'by_status': by_status,
            'by_department': by_department
        })
    
    # ==================== NEW ACTIONS - SAFE EXTENSIONS ====================
    # These actions extend functionality WITHOUT modifying existing code
    # ========================================================================
    
    @action(detail=True, methods=['post'], url_path='process-pdf-comments')
    def process_pdf_comments(self, request, pk=None):
        """
        NEW ACTION: Extract comments from PDF and populate CRS template
        
        Workflow:
        1. Extract comments from uploaded PDF
        2. Load CRS template
        3. Populate template with comments
        4. Return populated file for download
        
        POST /api/v1/crs-documents/{id}/process-pdf-comments/
        Body: {
            "pdf_file": <file>,  # ConsolidatedComments PDF
            "template_file": <file>,  # CRS Excel template (optional, uses default)
            "metadata": {  # Optional document metadata
                "project_name": "...",
                "document_number": "...",
                "revision": "...",
                "contractor": "..."
            }
        }
        
        Returns: Populated CRS Excel file (SAME FORMAT as template)
        """
        if not HELPERS_AVAILABLE:
            return Response({
                'error': 'PDF processing helpers not available. Install required packages: PyPDF2, openpyxl'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        try:
            document = self.get_object()
            
            # Get PDF file from request
            pdf_file = request.FILES.get('pdf_file')
            if not pdf_file:
                return Response({
                    'error': 'PDF file is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get template file or use smart template manager
            template_file = request.FILES.get('template_file')
            if not template_file:
                # Use smart template manager (checks local + AWS S3)
                try:
                    template_buffer = get_crs_template()
                except FileNotFoundError as e:
                    return Response({
                        'error': 'CRS template not available',
                        'details': str(e),
                        'suggestion': 'Upload template_file in request or ensure local/S3 template is available'
                    }, status=status.HTTP_400_BAD_REQUEST)
            else:
                template_buffer = BytesIO(template_file.read())
            
            # Validate template
            validation = validate_template(BytesIO(template_buffer.getvalue()))
            if not validation.get('valid'):
                return Response({
                    'error': f"Invalid template: {validation.get('error')}"
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Step 1: Extract comments from PDF
            pdf_buffer = BytesIO(pdf_file.read())
            comments = extract_reviewer_comments(pdf_buffer)
            
            if not comments:
                return Response({
                    'error': 'No reviewer comments found in PDF',
                    'suggestion': 'Ensure PDF contains reviewer comments with patterns like "Name – Callout:" or directive statements'
                }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
            
            # Step 2: Get metadata
            metadata = request.data.get('metadata', {})
            if not metadata or not isinstance(metadata, dict):
                metadata = {
                    'project_name': document.title,
                    'document_number': document.document_number,
                    'version': document.version,
                }
            
            # Step 3: Populate CRS template
            populated_buffer = populate_crs_template(
                BytesIO(template_buffer.getvalue()),
                comments,
                metadata
            )
            
            # Step 4: Generate statistics
            stats = get_comment_statistics(comments)
            
            # Step 5: Return populated file for download
            response = HttpResponse(
                populated_buffer.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="CRS_Populated_{document.document_number}.xlsx"'
            
            # Add stats in headers for frontend to display
            response['X-Comment-Count'] = str(stats['total'])
            response['X-Processing-Status'] = 'success'
            
            return response
        
        except Exception as e:
            return Response({
                'error': f'Error processing PDF: {str(e)}',
                'details': 'Check PDF format and template compatibility'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'], url_path='extract-comments-only')
    def extract_comments_only(self, request, pk=None):
        """
        NEW ACTION: Extract comments from PDF and return as JSON
        Does not modify template, just extracts and returns data
        
        POST /api/v1/crs-documents/{id}/extract-comments-only/
        Body: { "pdf_file": <file> }
        
        Returns: JSON with extracted comments and statistics
        """
        if not HELPERS_AVAILABLE:
            return Response({
                'error': 'PDF processing helpers not available'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        try:
            pdf_file = request.FILES.get('pdf_file')
            if not pdf_file:
                return Response({
                    'error': 'PDF file is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Extract comments
            pdf_buffer = BytesIO(pdf_file.read())
            comments = extract_reviewer_comments(pdf_buffer)
            
            # Get statistics
            stats = get_comment_statistics(comments)
            
            # Convert comments to JSON-serializable format
            comments_data = [
                {
                    'index': idx + 1,
                    'reviewer_name': comment.reviewer_name,
                    'comment_text': comment.comment_text,
                    'discipline': comment.discipline,
                    'section_reference': comment.section_reference,
                    'page_number': comment.page_number,
                    'comment_type': comment.comment_type,
                }
                for idx, comment in enumerate(comments)
            ]
            
            return Response({
                'success': True,
                'comments': comments_data,
                'statistics': stats,
                'message': f'Successfully extracted {len(comments)} comments'
            })
        
        except Exception as e:
            return Response({
                'error': f'Error extracting comments: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'], url_path='template-status')
    def check_template_status(self, request):
        """
        NEW ACTION: Check CRS template availability
        
        GET /api/v1/crs-documents/template-status/
        
        Returns: Template availability information
        """
        if not HELPERS_AVAILABLE:
            return Response({
                'available': False,
                'error': 'Template manager not available'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        try:
            template_info = get_template_info()
            return Response({
                'available': len(template_info['local_templates']) > 0 or template_info['s3_accessible'],
                'info': template_info,
                'status': 'ready' if template_info['local_templates'] else 'needs_download'
            })
        except Exception as e:
            return Response({
                'available': False,
                'error': f'Error checking template status: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'], url_path='validate-template')
    def validate_crs_template(self, request):
        """
        NEW ACTION: Validate CRS template file
        
        POST /api/v1/crs-documents/validate-template/
        Body: { "template_file": <file> }
        
        Returns: Validation report
        """
        if not HELPERS_AVAILABLE:
            return Response({
                'error': 'Template validation not available'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        try:
            template_file = request.FILES.get('template_file')
            if not template_file:
                return Response({
                    'error': 'Template file is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            template_buffer = BytesIO(template_file.read())
            validation = validate_template(template_buffer)
            
            return Response(validation)
        
        except Exception as e:
            return Response({
                'error': f'Error validating template: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'], url_path='upload-and-process')
    def upload_and_process(self, request):
        """
        UNIFIED ENDPOINT: Upload CRS file (PDF or Excel) and process it
        
        POST /api/v1/crs-documents/upload-and-process/
        Body (multipart/form-data):
            - file: PDF or Excel file (required)
            - project_name: string (optional)
            - document_number: string (optional)
            - revision: string (optional)
            - contractor: string (optional)
        
        Returns: Populated CRS template Excel file for download
        """
        if not HELPERS_AVAILABLE:
            return Response({
                'error': 'PDF/Excel processing helpers not available. Install PyPDF2 and openpyxl.',
                'success': False
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        try:
            # Get uploaded file
            uploaded_file = request.FILES.get('file')
            if not uploaded_file:
                return Response({
                    'error': 'File is required',
                    'success': False
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get metadata
            metadata = {
                'project_name': request.POST.get('project_name', ''),
                'document_number': request.POST.get('document_number', ''),
                'revision': request.POST.get('revision', ''),
                'contractor': request.POST.get('contractor', ''),
            }
            
            # Determine file type
            file_name = uploaded_file.name.lower()
            is_pdf = file_name.endswith('.pdf')
            is_excel = file_name.endswith(('.xlsx', '.xls'))
            
            if not (is_pdf or is_excel):
                return Response({
                    'error': 'Invalid file type. Only PDF and Excel files are supported.',
                    'success': False
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Process based on file type
            if is_pdf:
                # Extract comments from PDF
                pdf_buffer = BytesIO(uploaded_file.read())
                comments = extract_reviewer_comments(pdf_buffer)
                
                if not comments:
                    return Response({
                        'error': 'No comments found in the PDF file.',
                        'success': False,
                        'message': 'The PDF does not contain any extractable reviewer comments.'
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                # Load CRS template
                template_path = os.path.join(settings.BASE_DIR, 'apps', 'crs_documents', 'helpers', 'CRS template.xlsx')
                if not os.path.exists(template_path):
                    return Response({
                        'error': 'CRS template not found',
                        'success': False,
                        'message': 'Server configuration error: CRS template.xlsx is missing.'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
                # Populate template
                output_buffer = populate_crs_template(
                    template_path=template_path,
                    comments=comments,
                    metadata=metadata
                )
                
                # Get statistics
                stats = get_comment_statistics(comments)
                
                # Create response with Excel file
                response = HttpResponse(
                    output_buffer.getvalue(),
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                response['Content-Disposition'] = f'attachment; filename="CRS_Populated_{metadata.get("document_number", "output")}.xlsx"'
                response['X-Comment-Count'] = str(stats['total'])
                response['X-Processing-Status'] = 'success'
                
                return response
            
            elif is_excel:
                # Excel file uploaded - validate and standardize format
                excel_buffer = BytesIO(uploaded_file.read())
                
                # Try to read the Excel file to check if it's already in correct format
                # If not, we could extract data and repopulate template
                # For now, let's assume it needs standardization
                
                # TODO: Add logic to extract data from Excel and repopulate template
                # For MVP, return error asking for PDF or standard template
                
                return Response({
                    'error': 'Excel processing coming soon',
                    'success': False,
                    'message': 'Please upload a PDF file for now. Excel input will be supported soon.'
                }, status=status.HTTP_501_NOT_IMPLEMENTED)
            
        except Exception as e:
            import traceback
            return Response({
                'error': f'Error processing file: {str(e)}',
                'success': False,
                'details': traceback.format_exc()
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
