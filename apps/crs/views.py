from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.conf import settings
from io import BytesIO
import json
import os

from .models import CRSDocument, CRSComment, CRSActivity, GoogleSheetConfig
from .serializers import (
    CRSDocumentSerializer,
    CRSDocumentDetailSerializer,
    CRSCommentSerializer,
    CRSActivitySerializer,
    GoogleSheetConfigSerializer,
    PDFExtractRequestSerializer,
    GoogleSheetExportSerializer,
    CRSCommentBulkCreateSerializer
)
from .pdf_extractor import PDFCommentExtractor
from .google_sheets_service import GoogleSheetsService

# Import helpers from crs_documents for PDF processing
try:
    from apps.crs_documents.helpers.comment_extractor import extract_reviewer_comments, get_comment_statistics
    from apps.crs_documents.helpers.template_populator import populate_crs_template
    from apps.crs_documents.helpers.template_manager import get_crs_template, get_template_info
    HELPERS_AVAILABLE = True
except ImportError:
    HELPERS_AVAILABLE = False


class CRSDocumentViewSet(viewsets.ModelViewSet):
    """ViewSet for CRS Document management"""
    queryset = CRSDocument.objects.all()
    permission_classes = [IsAuthenticated]
    
    def get_permissions(self):
        """
        Enhanced permission handling to ensure JWT authentication works properly
        """
        # For all actions, require authentication
        return [IsAuthenticated()]
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return CRSDocumentDetailSerializer
        return CRSDocumentSerializer
    
    def perform_create(self, serializer):
        """Set uploaded_by to current user"""
        serializer.save(uploaded_by=self.request.user)
        
        # Log activity
        document = serializer.instance
        CRSActivity.objects.create(
            document=document,
            action='created',
            description=f'Document "{document.document_name}" created',
            performed_by=self.request.user
        )
    
    def perform_update(self, serializer):
        """Log update activity"""
        old_instance = self.get_object()
        old_status = old_instance.status
        
        serializer.save()
        
        new_instance = serializer.instance
        
        # Log status change
        if old_status != new_instance.status:
            CRSActivity.objects.create(
                document=new_instance,
                action='status_changed',
                description=f'Status changed from {old_status} to {new_instance.status}',
                performed_by=self.request.user,
                old_value={'status': old_status},
                new_value={'status': new_instance.status}
            )
        else:
            CRSActivity.objects.create(
                document=new_instance,
                action='updated',
                description=f'Document "{new_instance.document_name}" updated',
                performed_by=self.request.user
            )
    
    @action(detail=True, methods=['post'])
    def extract_pdf_comments(self, request, pk=None):
        """
        Extract comments from uploaded PDF file
        POST /api/v1/crs/documents/{id}/extract_pdf_comments/
        Body: { "auto_create_comments": true, "debug_mode": false }
        """
        document = self.get_object()
        
        serializer = PDFExtractRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        if not document.pdf_file:
            return Response(
                {"error": "No PDF file uploaded for this document"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Get PDF file path
            pdf_path = document.pdf_file.path
            
            # Initialize extractor
            debug_mode = serializer.validated_data.get('debug_mode', False)
            extractor = PDFCommentExtractor(debug_mode=debug_mode)
            
            # Extract comments
            raw_comments = extractor.extract_comments_from_pdf(pdf_path)
            
            # Process comments
            processed_comments = extractor.process_extracted_comments(raw_comments)
            
            # Auto-create comments if requested
            auto_create = serializer.validated_data.get('auto_create_comments', True)
            
            if auto_create:
                with transaction.atomic():
                    # Delete existing comments for this document
                    document.comments.all().delete()
                    
                    # Create new comments
                    created_comments = []
                    for idx, comment_data in enumerate(processed_comments, start=1):
                        comment = CRSComment.objects.create(
                            document=document,
                            serial_number=idx,
                            page_number=comment_data['page'],
                            clause_number=comment_data.get('clause', ''),
                            comment_text=comment_data['text'],
                            comment_type=comment_data.get('type', 'other'),
                            color_rgb=comment_data.get('color'),
                            bbox=comment_data.get('bbox'),
                            status='open'
                        )
                        created_comments.append(comment)
                    
                    # Update document stats
                    document.total_comments = len(created_comments)
                    document.pending_comments = len(created_comments)
                    document.resolved_comments = 0
                    document.status = 'processing'
                    document.processed_at = timezone.now()
                    document.save()
                    
                    # Log activity
                    CRSActivity.objects.create(
                        document=document,
                        action='imported',
                        description=f'Imported {len(created_comments)} comments from PDF',
                        performed_by=request.user,
                        new_value={'comment_count': len(created_comments)}
                    )
            
            return Response({
                "success": True,
                "message": f"Extracted {len(processed_comments)} comments from PDF",
                "data": {
                    "total_comments": len(processed_comments),
                    "red_comments": sum(1 for c in processed_comments if c['type'] == 'red_comment'),
                    "yellow_boxes": sum(1 for c in processed_comments if c['type'] == 'yellow_box'),
                    "other_annotations": sum(1 for c in processed_comments if c['type'] not in ['red_comment', 'yellow_box']),
                    "comments": processed_comments[:10],  # Return first 10 for preview
                    "auto_created": auto_create
                }
            }, status=status.HTTP_200_OK)
        
        except FileNotFoundError as e:
            return Response(
                {"error": f"PDF file not found: {str(e)}"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Error extracting comments: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def export_to_google_sheets(self, request, pk=None):
        """
        Export CRS data to Google Sheets
        POST /api/v1/crs/documents/{id}/export_to_google_sheets/
        Body: { "sheet_id": "optional_sheet_id", "start_row": 9, "auto_export": true }
        """
        document = self.get_object()
        
        serializer = GoogleSheetExportSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        # Get active Google Sheet config
        try:
            config = GoogleSheetConfig.objects.filter(is_active=True).first()
            if not config:
                return Response(
                    {"error": "No active Google Sheets configuration found. Please configure Google Sheets API first."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return Response(
                {"error": f"Error loading Google Sheets configuration: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Get sheet ID
        sheet_id = serializer.validated_data.get('sheet_id')
        if not sheet_id:
            sheet_id = document.google_sheet_id
        
        if not sheet_id:
            return Response(
                {"error": "No Google Sheet ID provided and document has no linked sheet"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Initialize Google Sheets service
            gs_service = GoogleSheetsService(
                credentials_json=config.credentials_json,
                token_json=config.token_json
            )
            
            # Authenticate
            if not gs_service.authenticate():
                return Response(
                    {"error": "Failed to authenticate with Google Sheets API"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Get comments
            comments = document.comments.all().order_by('serial_number')
            
            if not comments.exists():
                return Response(
                    {"error": "No comments to export"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Prepare data
            export_data = []
            for comment in comments:
                export_data.append({
                    'page': comment.page_number,
                    'clause': comment.clause_number or '',
                    'text': comment.comment_text,
                    'contractor_response': comment.contractor_response or '',
                    'company_response': comment.company_response or ''
                })
            
            # Export to sheet
            start_row = serializer.validated_data.get('start_row', 9)
            success, result = gs_service.export_to_sheet(
                spreadsheet_id=sheet_id,
                data=export_data,
                start_row=start_row
            )
            
            if success:
                # Update document
                document.google_sheet_id = sheet_id
                document.google_sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
                document.save()
                
                # Save updated token if available
                new_token = gs_service.get_token_json()
                if new_token and new_token != config.token_json:
                    config.token_json = new_token
                    config.save()
                
                # Log activity
                CRSActivity.objects.create(
                    document=document,
                    action='exported',
                    description=f'Exported {len(export_data)} comments to Google Sheets',
                    performed_by=request.user,
                    new_value={'sheet_id': sheet_id, 'rows_exported': len(export_data)}
                )
                
                return Response({
                    "success": True,
                    "message": f"Successfully exported {len(export_data)} comments to Google Sheets",
                    "data": result
                }, status=status.HTTP_200_OK)
            else:
                return Response(
                    {"error": f"Export failed: {result.get('error', 'Unknown error')}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        
        except Exception as e:
            return Response(
                {"error": f"Error exporting to Google Sheets: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def statistics(self, request):
        """
        Get CRS statistics
        GET /api/v1/crs/documents/statistics/
        """
        try:
            total_documents = CRSDocument.objects.count()
            total_comments = CRSComment.objects.count()
            
            documents_by_status = {}
            for choice in CRSDocument.STATUS_CHOICES:
                documents_by_status[choice[0]] = CRSDocument.objects.filter(status=choice[0]).count()
            
            comments_by_status = {}
            for choice in CRSComment.STATUS_CHOICES:
                comments_by_status[choice[0]] = CRSComment.objects.filter(status=choice[0]).count()
            
            return Response({
                "total_documents": total_documents,
                "total_comments": total_comments,
                "documents_by_status": documents_by_status,
                "comments_by_status": comments_by_status,
                "recent_documents": CRSDocumentSerializer(
                    CRSDocument.objects.all()[:5],
                    many=True,
                    context={'request': request}
                ).data
            })
        except Exception as e:
            return Response({
                'error': f'Statistics error: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'], url_path='upload-and-process')
    def upload_and_process(self, request):
        """
        UNIFIED ENDPOINT: Upload CRS file (PDF or Excel) and process it
        
        POST /api/v1/crs/documents/upload-and-process/
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
                
                # Load CRS template using smart template manager
                try:
                    template_buffer = get_crs_template()
                except FileNotFoundError as e:
                    return Response({
                        'error': 'CRS template not available',
                        'success': False,
                        'message': str(e),
                        'suggestion': 'Ensure CRS template is available locally or on AWS S3'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
                # Populate template (first arg is template_buffer, not template_path)
                output_buffer = populate_crs_template(
                    template_buffer,
                    comments,
                    metadata
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


class CRSCommentViewSet(viewsets.ModelViewSet):
    """ViewSet for CRS Comment management"""
    queryset = CRSComment.objects.all()
    serializer_class = CRSCommentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter by document_id if provided"""
        queryset = super().get_queryset()
        document_id = self.request.query_params.get('document_id')
        if document_id:
            queryset = queryset.filter(document_id=document_id)
        return queryset
    
    def perform_update(self, serializer):
        """Log update and recalculate document stats"""
        old_instance = self.get_object()
        old_status = old_instance.status
        
        serializer.save()
        
        new_instance = serializer.instance
        document = new_instance.document
        
        # Log status change
        if old_status != new_instance.status:
            CRSActivity.objects.create(
                document=document,
                comment=new_instance,
                action='status_changed',
                description=f'Comment #{new_instance.serial_number} status changed from {old_status} to {new_instance.status}',
                performed_by=self.request.user,
                old_value={'status': old_status},
                new_value={'status': new_instance.status}
            )
            
            # Set resolved_at timestamp
            if new_instance.status in ['resolved', 'closed'] and not new_instance.resolved_at:
                new_instance.resolved_at = timezone.now()
                new_instance.save()
        
        # Recalculate document stats
        self.update_document_stats(document)
    
    def update_document_stats(self, document):
        """Update document statistics based on comments"""
        total = document.comments.count()
        resolved = document.comments.filter(status__in=['resolved', 'closed']).count()
        pending = total - resolved
        
        document.total_comments = total
        document.resolved_comments = resolved
        document.pending_comments = pending
        
        # Update status
        if total > 0 and resolved == total:
            document.status = 'completed'
        elif resolved > 0:
            document.status = 'in_review'
        
        document.save()
    
    @action(detail=True, methods=['post'])
    def add_contractor_response(self, request, pk=None):
        """
        Add contractor response to comment
        POST /api/v1/crs/comments/{id}/add_contractor_response/
        Body: { "response": "text" }
        """
        comment = self.get_object()
        response_text = request.data.get('response', '').strip()
        
        if not response_text:
            return Response(
                {"error": "Response text is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        comment.contractor_response = response_text
        comment.contractor_response_date = timezone.now()
        comment.contractor_responder = request.user
        
        if comment.status == 'open':
            comment.status = 'in_progress'
        
        comment.save()
        
        # Log activity
        CRSActivity.objects.create(
            document=comment.document,
            comment=comment,
            action='response_added',
            description=f'Contractor response added to comment #{comment.serial_number}',
            performed_by=request.user
        )
        
        return Response({
            "success": True,
            "message": "Contractor response added successfully",
            "data": CRSCommentSerializer(comment).data
        })
    
    @action(detail=True, methods=['post'])
    def add_company_response(self, request, pk=None):
        """
        Add company response to comment
        POST /api/v1/crs/comments/{id}/add_company_response/
        Body: { "response": "text" }
        """
        comment = self.get_object()
        response_text = request.data.get('response', '').strip()
        
        if not response_text:
            return Response(
                {"error": "Response text is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        comment.company_response = response_text
        comment.company_response_date = timezone.now()
        comment.company_responder = request.user
        
        # If both responses exist, mark as resolved
        if comment.contractor_response and comment.company_response:
            comment.status = 'resolved'
            comment.resolved_at = timezone.now()
        elif comment.status == 'open':
            comment.status = 'in_progress'
        
        comment.save()
        
        # Update document stats
        self.update_document_stats(comment.document)
        
        # Log activity
        CRSActivity.objects.create(
            document=comment.document,
            comment=comment,
            action='response_added',
            description=f'Company response added to comment #{comment.serial_number}',
            performed_by=request.user
        )
        
        return Response({
            "success": True,
            "message": "Company response added successfully",
            "data": CRSCommentSerializer(comment).data
        })
    
    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        """
        Bulk create comments
        POST /api/v1/crs/comments/bulk_create/
        Body: { "document_id": 1, "comments": [{...}, {...}] }
        """
        serializer = CRSCommentBulkCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        document_id = serializer.validated_data['document_id']
        comments_data = serializer.validated_data['comments']
        
        try:
            document = CRSDocument.objects.get(id=document_id)
        except CRSDocument.DoesNotExist:
            return Response(
                {"error": "Document not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        with transaction.atomic():
            created_comments = []
            for idx, comment_data in enumerate(comments_data, start=1):
                comment = CRSComment.objects.create(
                    document=document,
                    serial_number=idx,
                    page_number=comment_data.get('page', 1),
                    clause_number=comment_data.get('clause', ''),
                    comment_text=comment_data.get('text', ''),
                    comment_type=comment_data.get('type', 'other'),
                    status='open'
                )
                created_comments.append(comment)
            
            # Update document stats
            document.total_comments = len(created_comments)
            document.pending_comments = len(created_comments)
            document.resolved_comments = 0
            document.save()
        
        return Response({
            "success": True,
            "message": f"Created {len(created_comments)} comments",
            "data": CRSCommentSerializer(created_comments, many=True).data
        }, status=status.HTTP_201_CREATED)


class CRSActivityViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for CRS Activity logs (read-only)"""
    queryset = CRSActivity.objects.all()
    serializer_class = CRSActivitySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter by document_id if provided"""
        queryset = super().get_queryset()
        document_id = self.request.query_params.get('document_id')
        if document_id:
            queryset = queryset.filter(document_id=document_id)
        return queryset


class GoogleSheetConfigViewSet(viewsets.ModelViewSet):
    """ViewSet for Google Sheet Configuration"""
    queryset = GoogleSheetConfig.objects.all()
    serializer_class = GoogleSheetConfigSerializer
    permission_classes = [IsAuthenticated]
    
    def perform_create(self, serializer):
        """Set created_by to current user"""
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def test_connection(self, request, pk=None):
        """
        Test Google Sheets connection
        POST /api/v1/crs/google-configs/{id}/test_connection/
        """
        config = self.get_object()
        
        try:
            gs_service = GoogleSheetsService(
                credentials_json=config.credentials_json,
                token_json=config.token_json
            )
            
            if gs_service.authenticate():
                # Save updated token
                new_token = gs_service.get_token_json()
                if new_token:
                    config.token_json = new_token
                    config.save()
                
                return Response({
                    "success": True,
                    "message": "Successfully connected to Google Sheets API"
                })
            else:
                return Response({
                    "success": False,
                    "message": "Failed to authenticate with Google Sheets API"
                }, status=status.HTTP_400_BAD_REQUEST)
        
        except Exception as e:
            return Response({
                "success": False,
                "message": f"Error testing connection: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
