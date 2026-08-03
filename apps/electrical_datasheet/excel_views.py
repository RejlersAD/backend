"""
ViewSets for Excel Quality Checker API
Handles upload, parsing, validation, and reporting of Excel datasheets
"""

from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone
from django.db.models import Count, Q
from django.core.files.storage import default_storage
from django.conf import settings
import os
import hashlib
import logging
from typing import Dict, Any

from .excel_document_models import (
    UploadedExcelDocument,
    ValidationIssue,
    SheetMetadata,
    ParsedItem
)
from .excel_serializers import (
    UploadedExcelDocumentListSerializer,
    UploadedExcelDocumentDetailSerializer,
    UploadedExcelDocumentUploadSerializer,
    ValidationIssueSerializer,
    ValidationIssueSummarySerializer,
)
from .excel_parser_service import ExcelParserService
from .excel_validation_framework import ValidationEngine

logger = logging.getLogger(__name__)


class ExcelDocumentViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for managing uploaded Excel documents
    
    Endpoints:
    - GET /api/documents/ - List all documents
    - GET /api/documents/{id}/ - Get document details
    - POST /api/documents/upload/ - Upload and validate new document
    - GET /api/documents/{id}/issues/ - Get validation issues for document
    - GET /api/documents/{id}/parsed_data/ - Get parsed technical data
    - POST /api/documents/{id}/acknowledge_issue/ - Acknowledge an issue
    """
    
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['equipment_type', 'status']
    search_fields = ['filename', 'company_doc_number', 'document_title', 'project_name']
    ordering_fields = ['uploaded_at', 'validation_score', 'error_count']
    ordering = ['-uploaded_at']
    
    def get_queryset(self):
        """Get queryset filtered by user permissions and non-deleted"""
        queryset = UploadedExcelDocument.objects.filter(is_deleted=False)
        
        # Filter by current user's uploads (optional - remove if you want all users to see all docs)
        # queryset = queryset.filter(uploaded_by=self.request.user)
        
        return queryset
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return UploadedExcelDocumentListSerializer
        elif self.action == 'upload':
            return UploadedExcelDocumentUploadSerializer
        else:
            return UploadedExcelDocumentDetailSerializer
    
    @action(detail=False, methods=['post'])
    def upload(self, request):
        """
        Upload and validate an Excel datasheet
        
        Process:
        1. Validate file upload
        2. Save file to storage
        3. Parse Excel file
        4. Run validation engine
        5. Store results in database
        6. Return summary
        """
        serializer = UploadedExcelDocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        uploaded_file = serializer.validated_data['file']
        
        try:
            # Calculate file hash for duplicate detection
            file_hash = self._calculate_file_hash(uploaded_file)
            
            # Check for duplicate
            existing_doc = UploadedExcelDocument.objects.filter(
                file_hash=file_hash,
                is_deleted=False
            ).first()
            
            if existing_doc:
                return Response({
                    'error': 'Duplicate file',
                    'message': 'This file has already been uploaded.',
                    'existing_document_id': existing_doc.id,
                    'existing_document': UploadedExcelDocumentListSerializer(existing_doc).data
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Save file to storage
            file_path = self._save_uploaded_file(uploaded_file)
            
            # Create document record
            document = UploadedExcelDocument.objects.create(
                filename=uploaded_file.name,
                file_path=file_path,
                file_size=uploaded_file.size,
                file_hash=file_hash,
                status='processing',
                uploaded_by=request.user,
                processing_started_at=timezone.now()
            )
            
            # Parse and validate
            try:
                self._parse_and_validate(document, file_path)
                
                # Return success response
                return Response({
                    'success': True,
                    'message': 'File uploaded and validated successfully',
                    'document': UploadedExcelDocumentDetailSerializer(document).data
                }, status=status.HTTP_201_CREATED)
                
            except Exception as e:
                logger.error(f"Error processing document {document.id}: {str(e)}", exc_info=True)
                
                # Update document with error
                document.status = 'error'
                document.processing_error = str(e)
                document.processing_completed_at = timezone.now()
                document.save()
                
                return Response({
                    'error': 'Processing failed',
                    'message': str(e),
                    'document_id': document.id
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        except Exception as e:
            logger.error(f"Error uploading file: {str(e)}", exc_info=True)
            return Response({
                'error': 'Upload failed',
                'message': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _calculate_file_hash(self, file) -> str:
        """Calculate SHA-256 hash of file"""
        hasher = hashlib.sha256()
        
        # Reset file pointer
        file.seek(0)
        
        # Read in chunks
        for chunk in file.chunks():
            hasher.update(chunk)
        
        # Reset file pointer
        file.seek(0)
        
        return hasher.hexdigest()
    
    def _save_uploaded_file(self, file) -> str:
        """
        Save uploaded file to storage
        
        Returns:
            Path to saved file
        """
        # Create directory structure: electrical_datasheets/YYYY/MM/
        upload_dir = os.path.join(
            'electrical_datasheets',
            timezone.now().strftime('%Y'),
            timezone.now().strftime('%m')
        )
        
        # Create full path
        file_path = os.path.join(upload_dir, file.name)
        
        # Save file
        saved_path = default_storage.save(file_path, file)
        
        return saved_path
    
    def _parse_and_validate(self, document: UploadedExcelDocument, file_path: str):
        """
        Parse Excel file and run validation
        
        Args:
            document: UploadedExcelDocument instance
            file_path: Path to Excel file
        """
        # Get absolute file path
        if hasattr(settings, 'MEDIA_ROOT'):
            absolute_path = os.path.join(settings.MEDIA_ROOT, file_path)
        else:
            absolute_path = default_storage.path(file_path)
        
        # Parse Excel file
        logger.info(f"Parsing Excel file: {absolute_path}")
        parser = ExcelParserService(absolute_path)
        parsed_data = parser.parse()
        
        # Extract document control info
        doc_control = parser.extract_document_control_summary()
        
        # Update document with parsed data
        document.equipment_type = parsed_data.get('equipment_type', 'unknown')
        document.parsed_data = parsed_data
        document.sheet_names = parsed_data.get('sheet_names', [])
        document.company_doc_number = doc_control.get('company_doc_number', '')
        document.contractor_doc_number = doc_control.get('contractor_doc_number', '')
        document.rejlers_doc_number = doc_control.get('rejlers_doc_number', '')
        document.document_title = doc_control.get('document_title', '')
        document.classification_code = doc_control.get('classification_code', '')
        document.revision = doc_control.get('revision', '')
        document.doc_status = doc_control.get('doc_status', '')
        document.doc_purpose = doc_control.get('doc_purpose', '')
        document.project_name = doc_control.get('project_name', '')
        document.project_location = doc_control.get('project_location', '')
        document.agreement_number = doc_control.get('agreement_number', '')
        document.save()
        
        # Create sheet metadata
        self._create_sheet_metadata(document, parsed_data)
        
        # Create parsed items
        self._create_parsed_items(document, parsed_data)
        
        # Run validation
        logger.info(f"Running validation for document {document.id}")
        validation_engine = ValidationEngine(parsed_data)
        issues = validation_engine.run_validation()
        summary = validation_engine.get_summary()
        
        # Store validation issues
        for issue_dto in issues:
            ValidationIssue.objects.create(
                document=document,
                sheet_name=issue_dto.sheet_name,
                section=issue_dto.section,
                item=issue_dto.item,
                row_number=issue_dto.row_number,
                column_name=issue_dto.column_name,
                severity=issue_dto.severity,
                code=issue_dto.code,
                message=issue_dto.message,
                expected_value=issue_dto.expected,
                actual_value=issue_dto.actual,
                rule_name=issue_dto.rule_name,
                category=issue_dto.category,
            )
        
        # Update document with validation results
        document.status = 'validated' if summary['status'] == 'passed' else 'failed'
        document.validation_score = summary['validation_score']
        document.error_count = summary['error_count']
        document.warning_count = summary['warning_count']
        document.info_count = summary['info_count']
        document.processing_completed_at = timezone.now()
        document.save()
        
        logger.info(f"Validation complete for document {document.id}: Score={document.validation_score}, "
                   f"Errors={document.error_count}, Warnings={document.warning_count}")
    
    def _create_sheet_metadata(self, document: UploadedExcelDocument, parsed_data: Dict[str, Any]):
        """Create SheetMetadata records"""
        sheets_info = parsed_data.get('sheets', {})
        sheet_names = parsed_data.get('sheet_names', [])
        
        for idx, sheet_name in enumerate(sheet_names):
            sheet_info = sheets_info.get(sheet_name, {})
            
            SheetMetadata.objects.create(
                document=document,
                sheet_name=sheet_name,
                sheet_index=idx,
                sheet_type=sheet_info.get('type', 'other'),
                row_count=sheet_info.get('max_row', 0),
                column_count=sheet_info.get('max_column', 0),
                has_data=sheet_info.get('max_row', 0) > 0,
            )
    
    def _create_parsed_items(self, document: UploadedExcelDocument, parsed_data: Dict[str, Any]):
        """Create ParsedItem records"""
        technical_data = parsed_data.get('technical_data', {})
        
        items_to_create = []
        
        for sheet_name, sheet_data in technical_data.items():
            items = sheet_data.get('items', [])
            
            for item_data in items:
                items_to_create.append(ParsedItem(
                    document=document,
                    sheet_name=sheet_name,
                    section=item_data.get('section', ''),
                    sl_no=item_data.get('sl_no', ''),
                    description=item_data.get('description', ''),
                    unit=item_data.get('unit', ''),
                    specified_design_data=item_data.get('specified_design_data', ''),
                    vendor_data=item_data.get('vendor_data', ''),
                    row_number=item_data.get('row_number', 0),
                    is_section_header=item_data.get('is_section_header', False),
                    is_empty=not bool(item_data.get('description', '').strip()),
                ))
        
        # Bulk create
        if items_to_create:
            ParsedItem.objects.bulk_create(items_to_create, batch_size=1000)
    
    @action(detail=True, methods=['get'])
    def issues(self, request, pk=None):
        """
        Get validation issues for a document
        Supports filtering by severity, category, section
        """
        document = self.get_object()
        
        queryset = ValidationIssue.objects.filter(document=document)
        
        # Filter by severity
        severity = request.query_params.get('severity')
        if severity:
            queryset = queryset.filter(severity=severity)
        
        # Filter by category
        category = request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        
        # Filter by section
        section = request.query_params.get('section')
        if section:
            queryset = queryset.filter(section__icontains=section)
        
        # Filter by sheet
        sheet_name = request.query_params.get('sheet_name')
        if sheet_name:
            queryset = queryset.filter(sheet_name=sheet_name)
        
        serializer = ValidationIssueSerializer(queryset, many=True)
        
        return Response({
            'document_id': document.id,
            'total_issues': queryset.count(),
            'issues': serializer.data
        })
    
    @action(detail=True, methods=['get'])
    def issues_summary(self, request, pk=None):
        """
        Get summary of validation issues grouped by severity and category
        """
        document = self.get_object()
        
        # Group by severity
        by_severity = ValidationIssue.objects.filter(document=document).values('severity').annotate(
            count=Count('id')
        )
        
        # Group by category
        by_category = ValidationIssue.objects.filter(document=document).values('category').annotate(
            count=Count('id')
        )
        
        # Group by severity and category
        by_severity_category = ValidationIssue.objects.filter(document=document).values(
            'severity', 'category'
        ).annotate(count=Count('id'))
        
        return Response({
            'document_id': document.id,
            'by_severity': list(by_severity),
            'by_category': list(by_category),
            'by_severity_category': list(by_severity_category),
        })
    
    @action(detail=True, methods=['get'])
    def parsed_data(self, request, pk=None):
        """
        Get parsed technical data for a document
        Optionally filter by sheet name or section
        """
        document = self.get_object()
        
        sheet_name = request.query_params.get('sheet_name')
        section = request.query_params.get('section')
        
        queryset = ParsedItem.objects.filter(document=document)
        
        if sheet_name:
            queryset = queryset.filter(sheet_name=sheet_name)
        
        if section:
            queryset = queryset.filter(section__icontains=section)
        
        # Group by section if requested
        group_by_section = request.query_params.get('group_by_section', 'false').lower() == 'true'
        
        if group_by_section:
            from collections import defaultdict
            grouped = defaultdict(list)
            
            for item in queryset:
                grouped[item.section].append({
                    'id': item.id,
                    'sl_no': item.sl_no,
                    'description': item.description,
                    'unit': item.unit,
                    'specified_design_data': item.specified_design_data,
                    'vendor_data': item.vendor_data,
                    'row_number': item.row_number,
                    'is_section_header': item.is_section_header,
                })
            
            return Response({
                'document_id': document.id,
                'grouped_data': dict(grouped)
            })
        else:
            items = queryset.values(
                'id', 'sheet_name', 'section', 'sl_no', 'description', 'unit',
                'specified_design_data', 'vendor_data', 'row_number', 'is_section_header'
            )
            
            return Response({
                'document_id': document.id,
                'total_items': queryset.count(),
                'items': list(items)
            })
    
    @action(detail=True, methods=['post'])
    def acknowledge_issue(self, request, pk=None):
        """
        Acknowledge a validation issue
        """
        document = self.get_object()
        issue_id = request.data.get('issue_id')
        resolution_notes = request.data.get('resolution_notes', '')
        
        if not issue_id:
            return Response({
                'error': 'issue_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            issue = ValidationIssue.objects.get(id=issue_id, document=document)
            issue.is_acknowledged = True
            issue.acknowledged_by = request.user
            issue.acknowledged_at = timezone.now()
            issue.resolution_notes = resolution_notes
            issue.save()
            
            return Response({
                'success': True,
                'message': 'Issue acknowledged successfully',
                'issue': ValidationIssueSerializer(issue).data
            })
        
        except ValidationIssue.DoesNotExist:
            return Response({
                'error': 'Issue not found'
            }, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """
        Get overall statistics for uploaded documents
        """
        queryset = self.get_queryset()
        
        stats = {
            'total_documents': queryset.count(),
            'by_equipment_type': list(queryset.values('equipment_type').annotate(count=Count('id'))),
            'by_status': list(queryset.values('status').annotate(count=Count('id'))),
            'total_issues': ValidationIssue.objects.filter(document__in=queryset).count(),
            'average_validation_score': queryset.aggregate(
                avg_score=models.Avg('validation_score')
            )['avg_score'],
        }
        
        return Response(stats)


# Import models at the end to avoid circular imports
from django.db import models
