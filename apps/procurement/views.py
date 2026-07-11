"""
Procurement Management Views
API endpoints for procurement workflows
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination

# RBAC - Module-level access control (soft-coded)
from apps.rbac.permissions import HasModuleAccess
from django.db.models import Q, Count, Sum, Avg
from django.utils import timezone
from datetime import timedelta

from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db import models as django_models

from .models import (
    Vendor, 
    PurchaseRequisition, 
    PurchaseOrder, 
    Receipt, 
    PODocument, 
    PROCUREMENT_CATEGORIES,
    # Master database models
    Project,
    Budget,
    CostCenter,
)
from .serializers import (
    VendorSerializer,
    PurchaseRequisitionSerializer,
    PurchaseOrderSerializer,
    ReceiptSerializer,
    ProcurementCategorySerializer,
    PODocumentSerializer,
    # Master database serializers
    ProjectListSerializer,
    ProjectDetailSerializer,
    CostCenterSerializer,
    BudgetSerializer,
)


# Soft-coded pagination for vendor list - supports large page_size
class VendorPagination(PageNumberPagination):
    """
    Custom pagination for vendors allowing larger page sizes
    Supports page_size query parameter up to 10000 records
    """
    page_size = 100  # Default page size
    page_size_query_param = 'page_size'  # Allow client to set page_size via query param
    max_page_size = 10000  # Maximum allowed page_size


class VendorViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Vendor management
    
    🔐 SECURITY: Requires 'procurement_vendors' module access (soft-coded from rbac_config.py)
    """
    
    queryset = Vendor.objects.all().order_by('-created_at')
    serializer_class = VendorSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'procurement_vendors'
    pagination_class = VendorPagination  # Use custom pagination
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by rating
        rating_filter = self.request.query_params.get('rating', None)
        if rating_filter:
            queryset = queryset.filter(rating=rating_filter)
        
        # Search
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(vendor_code__icontains=search) |
                Q(email__icontains=search)
            )
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def rate_vendor(self, request, pk=None):
        """Update vendor rating"""
        vendor = self.get_object()
        rating = request.data.get('rating')
        notes = request.data.get('notes', '')
        
        if rating not in [1, 2, 3, 4, 5]:
            return Response(
                {'error': 'Rating must be between 1 and 5'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        vendor.rating = rating
        if notes:
            vendor.performance_notes = notes
        vendor.save()
        
        return Response({'message': 'Vendor rating updated successfully'})
    
    @action(detail=False, methods=['get'])
    def top_vendors(self, request):
        """Get top-rated vendors"""
        top_vendors = self.get_queryset().filter(
            status='active',
            rating__gte=4
        ).order_by('-rating', 'name')[:10]
        
        serializer = self.get_serializer(top_vendors, many=True)
        return Response(serializer.data)


class PurchaseRequisitionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Purchase Requisition management
    
    Features:
    - Multi-part file upload to S3
    - Two-tier approval workflow (PM → VP)
    - Advanced filtering and search
    - PDF generation aligned with template
    
    🔐 SECURITY: Requires 'procurement_requisitions' module access (soft-coded from rbac_config.py)
    """
    
    queryset = PurchaseRequisition.objects.all().order_by('-created_at')
    serializer_class = PurchaseRequisitionSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'procurement_requisitions'
    parser_classes = [FormParser, MultiPartParser, JSONParser]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by priority
        priority_filter = self.request.query_params.get('priority', None)
        if priority_filter:
            queryset = queryset.filter(priority=priority_filter)
        
        # Filter by department
        department = self.request.query_params.get('department', None)
        if department:
            queryset = queryset.filter(department=department)
        
        # Filter by project
        project_filter = self.request.query_params.get('project', None)
        if project_filter:
            queryset = queryset.filter(project__icontains=project_filter)
        
        # Filter by approval status
        pm_approval = self.request.query_params.get('pm_approval_status', None)
        if pm_approval:
            queryset = queryset.filter(pm_approval_status=pm_approval)
        
        vp_approval = self.request.query_params.get('vp_op_approval_status', None)
        if vp_approval:
            queryset = queryset.filter(vp_op_approval_status=vp_approval)
        
        # Search across multiple fields
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(pr_number__icontains=search) |
                Q(title__icontains=search) |
                Q(product_service__icontains=search) |
                Q(description_reason__icontains=search) |
                Q(supplier_name__icontains=search) |
                Q(project_department__icontains=search)
            )
        
        return queryset
    
    def create(self, request, *args, **kwargs):
        """Create PR with file upload support"""
        # Handle file uploads from request.FILES
        files = request.FILES.getlist('attachments_files', [])
        
        # Add files to serializer context
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Add files to validated data
        if files:
            serializer.validated_data['attachments_files'] = files
        
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
    @action(detail=True, methods=['post'])
    def pm_approve(self, request, pk=None):
        """
        Project Manager approval (first tier)
        Requires: signature (optional)
        """
        pr = self.get_object()
        
        # Validate PR can be approved
        if pr.pm_approval_status == 'approved':
            return Response(
                {'error': 'PR already approved by PM'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update PM approval
        pr.pm_name = request.user
        pr.pm_signature = request.data.get('signature', '')
        pr.pm_approval_status = 'approved'
        pr.pm_approved_at = timezone.now()
        
        # Update overall status
        if pr.status == 'submitted' or pr.status == 'draft':
            pr.status = 'pm_approved'
        
        pr.save()
        
        serializer = self.get_serializer(pr)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def pm_reject(self, request, pk=None):
        """Project Manager rejection"""
        pr = self.get_object()
        
        pr.pm_approval_status = 'not_approved'
        pr.status = 'rejected'
        pr.rejection_reason = request.data.get('reason', 'Rejected by PM')
        pr.save()
        
        serializer = self.get_serializer(pr)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def vp_approve(self, request, pk=None):
        """
        VP Operations approval (second tier)
        Requires: PM approval first, signature (optional)
        """
        pr = self.get_object()
        
        # Validate PM has approved first
        if pr.pm_approval_status != 'approved':
            return Response(
                {'error': 'PM must approve before VP approval'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate VP hasn't already approved
        if pr.vp_op_approval_status == 'approved':
            return Response(
                {'error': 'PR already approved by VP'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update VP approval
        pr.vp_op_name = request.user
        pr.vp_op_signature = request.data.get('signature', '')
        pr.vp_op_approval_status = 'approved'
        pr.vp_op_approved_at = timezone.now()
        
        # Update overall status to fully approved
        pr.status = 'fully_approved'
        pr.approved_by = request.user
        pr.approved_at = timezone.now()
        
        pr.save()
        
        serializer = self.get_serializer(pr)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def vp_reject(self, request, pk=None):
        """VP Operations rejection"""
        pr = self.get_object()
        
        pr.vp_op_approval_status = 'not_approved'
        pr.status = 'rejected'
        pr.rejection_reason = request.data.get('reason', 'Rejected by VP Operations')
        pr.save()
        
        serializer = self.get_serializer(pr)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """Submit PR for approval (move from draft to submitted)"""
        pr = self.get_object()
        
        if pr.status != 'draft':
            return Response(
                {'error': 'Only draft PRs can be submitted'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        pr.status = 'submitted'
        pr.save()
        
        serializer = self.get_serializer(pr)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def upload_attachment(self, request, pk=None):
        """Upload additional attachment to existing PR"""
        pr = self.get_object()
        files = request.FILES.getlist('files', [])
        
        if not files:
            return Response(
                {'error': 'No files provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Use serializer's upload method
        serializer = self.get_serializer(pr)
        serializer._upload_attachments(pr, files)
        
        return Response({
            'message': f'{len(files)} file(s) uploaded successfully',
            'attachments': pr.attachments
        })
    
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """Get PR dashboard statistics"""
        queryset = self.get_queryset()
        
        total = queryset.count()
        draft = queryset.filter(status='draft').count()
        submitted = queryset.filter(status='submitted').count()
        pm_approved = queryset.filter(status='pm_approved').count()
        fully_approved = queryset.filter(status='fully_approved').count()
        rejected = queryset.filter(status='rejected').count()
        converted = queryset.filter(status='converted').count()
        
        # Approval pending counts
        pending_pm_approval = queryset.filter(
            status='submitted', pm_approval_status='pending'
        ).count()
        
        pending_vp_approval = queryset.filter(
            status='pm_approved', vp_op_approval_status='pending'
        ).count()
        
        by_priority = queryset.values('priority').annotate(
            count=Count('id')
        )
        
        recent = queryset[:10]
        recent_serializer = self.get_serializer(recent, many=True)
        
        return Response({
            'totals': {
                'total': total,
                'draft': draft,
                'submitted': submitted,
                'pm_approved': pm_approved,
                'fully_approved': fully_approved,
                'rejected': rejected,
                'converted': converted,
                'pending_pm_approval': pending_pm_approval,
                'pending_vp_approval': pending_vp_approval,
            },
            'by_priority': list(by_priority),
            'recent': recent_serializer.data
        })
    
    # Legacy approve/reject endpoints (kept for backward compatibility)
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Legacy approve endpoint - redirects to VP approve"""
        return self.vp_approve(request, pk)
    
    # Legacy approve/reject endpoints (kept for backward compatibility)
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Legacy approve endpoint - redirects to VP approve"""
        return self.vp_approve(request, pk)
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject a purchase requisition"""
        pr = self.get_object()
        reason = request.data.get('reason', '')
        
        if pr.status != 'submitted':
            return Response(
                {'error': 'Only submitted requisitions can be rejected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        pr.status = 'rejected'
        pr.rejection_reason = reason
        pr.save()
        
        serializer = self.get_serializer(pr)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """Get PR dashboard statistics"""
        total = self.get_queryset().count()
        pending = self.get_queryset().filter(status='submitted').count()
        approved = self.get_queryset().filter(status='approved').count()
        rejected = self.get_queryset().filter(status='rejected').count()
        
        by_priority = self.get_queryset().values('priority').annotate(
            count=Count('id')
        )
        
        recent = self.get_queryset()[:5]
        recent_serializer = self.get_serializer(recent, many=True)
        
        return Response({
            'totals': {
                'total': total,
                'pending': pending,
                'approved': approved,
                'rejected': rejected
            },
            'by_priority': list(by_priority),
            'recent': recent_serializer.data
        })


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Purchase Order management
    
    🔐 SECURITY: Requires 'procurement_orders' module access (soft-coded from rbac_config.py)
    """
    
    queryset = PurchaseOrder.objects.all().select_related('vendor').order_by('-created_at')
    serializer_class = PurchaseOrderSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'procurement_orders'
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by vendor
        vendor_id = self.request.query_params.get('vendor', None)
        if vendor_id:
            queryset = queryset.filter(vendor_id=vendor_id)
        
        # Search
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(po_number__icontains=search) |
                Q(title__icontains=search) |
                Q(vendor__name__icontains=search)
            )
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def send_to_vendor(self, request, pk=None):
        """Send PO to vendor"""
        po = self.get_object()
        
        if po.status != 'draft':
            return Response(
                {'error': 'Only draft POs can be sent'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        po.status = 'sent'
        po.save()
        
        serializer = self.get_serializer(po)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        """Vendor acknowledges PO"""
        po = self.get_object()
        
        if po.status != 'sent':
            return Response(
                {'error': 'Only sent POs can be acknowledged'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        po.status = 'acknowledged'
        po.save()
        
        serializer = self.get_serializer(po)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """Get PO dashboard statistics"""
        total = self.get_queryset().count()
        draft = self.get_queryset().filter(status='draft').count()
        sent = self.get_queryset().filter(status='sent').count()
        acknowledged = self.get_queryset().filter(status='acknowledged').count()
        completed = self.get_queryset().filter(status='completed').count()
        
        total_value = self.get_queryset().aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        
        by_vendor = self.get_queryset().values('vendor__name').annotate(
            count=Count('id'),
            total=Sum('total_amount')
        ).order_by('-total')[:5]
        
        recent = self.get_queryset()[:5]
        recent_serializer = self.get_serializer(recent, many=True)
        
        return Response({
            'totals': {
                'total': total,
                'draft': draft,
                'sent': sent,
                'acknowledged': acknowledged,
                'completed': completed,
                'total_value': float(total_value)
            },
            'top_vendors': list(by_vendor),
            'recent': recent_serializer.data
        })


class ReceiptViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Goods Receipt management
    
    🔐 SECURITY: Requires 'procurement_receipts' module access (soft-coded from rbac_config.py)
    """
    
    queryset = Receipt.objects.all().select_related('purchase_order').order_by('-created_at')
    serializer_class = ReceiptSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'procurement_receipts'
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by quality check
        quality_filter = self.request.query_params.get('quality_check', None)
        if quality_filter == 'passed':
            queryset = queryset.filter(quality_check_passed=True)
        elif quality_filter == 'failed':
            queryset = queryset.filter(quality_check_passed=False)
        
        # Search
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(receipt_number__icontains=search) |
                Q(purchase_order__po_number__icontains=search) |
                Q(delivery_note_number__icontains=search)
            )
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        """Accept received goods"""
        receipt = self.get_object()
        
        if receipt.status != 'pending':
            return Response(
                {'error': 'Only pending receipts can be accepted'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        receipt.status = 'accepted'
        receipt.quality_check_passed = True
        
        # Update PO status to completed
        po = receipt.purchase_order
        po.status = 'completed'
        po.actual_delivery = timezone.now()
        po.save()
        
        receipt.save()
        
        serializer = self.get_serializer(receipt)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def reject_delivery(self, request, pk=None):
        """Reject received goods"""
        receipt = self.get_object()
        notes = request.data.get('notes', '')
        
        if receipt.status != 'pending':
            return Response(
                {'error': 'Only pending receipts can be rejected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        receipt.status = 'rejected'
        receipt.quality_check_passed = False
        receipt.inspection_notes = notes
        receipt.save()
        
        serializer = self.get_serializer(receipt)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """Get receipt dashboard statistics"""
        total = self.get_queryset().count()
        pending = self.get_queryset().filter(status='pending').count()
        accepted = self.get_queryset().filter(status='accepted').count()
        rejected = self.get_queryset().filter(status='rejected').count()
        
        quality_passed = self.get_queryset().filter(quality_check_passed=True).count()
        quality_failed = self.get_queryset().filter(quality_check_passed=False).count()
        
        recent = self.get_queryset()[:5]
        recent_serializer = self.get_serializer(recent, many=True)
        
        return Response({
            'totals': {
                'total': total,
                'pending': pending,
                'accepted': accepted,
                'rejected': rejected,
                'quality_passed': quality_passed,
                'quality_failed': quality_failed
            },
            'recent': recent_serializer.data
        })


@action(detail=False, methods=['get'])
def get_categories(request):
    """Get all procurement categories"""
    categories = [
        {'code': code, **data}
        for code, data in PROCUREMENT_CATEGORIES.items()
    ]
    serializer = ProcurementCategorySerializer(categories, many=True)
    return Response(serializer.data)


class PODocumentViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for PODocument — read-only list/detail plus the AI extraction action.
    The `extract_from_pdf` action is the primary entry point: it accepts a PDF
    upload, stores it in S3, runs the AI extractor, and returns the result.
    
    🔐 SECURITY: Requires 'procurement_orders' module access (soft-coded from rbac_config.py)
    """

    queryset = PODocument.objects.all().order_by('-created_at')
    serializer_class = PODocumentSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'procurement_orders'
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return super().get_queryset().filter(uploaded_by=self.request.user)

    @action(detail=False, methods=['post'], url_path='extract_from_pdf',
            parser_classes=[MultiPartParser, FormParser])
    def extract_from_pdf(self, request):
        """
        POST /api/v1/procurement/po-documents/extract_from_pdf/

        Multipart form fields:
            file  — PDF file (required)

        Returns structured JSON with all extractable PO/PR fields plus
        S3 storage reference and extraction metadata.
        """
        import logging
        logger = logging.getLogger(__name__)

        pdf_file = request.FILES.get('file')
        if not pdf_file:
            return Response(
                {'error': 'No file provided. Send a PDF as multipart/form-data field "file".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate content type
        allowed_types = ['application/pdf', 'application/x-pdf']
        content_type = getattr(pdf_file, 'content_type', '') or ''
        if content_type not in allowed_types and not pdf_file.name.lower().endswith('.pdf'):
            return Response(
                {'error': 'Only PDF files are supported.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create a pending PODocument record
        doc = PODocument.objects.create(
            original_filename=pdf_file.name,
            file_size_bytes=pdf_file.size,
            extraction_status='processing',
            uploaded_by=request.user,
        )

        try:
            from .services.po_ai_extractor import extract_po_from_pdf

            pdf_bytes = pdf_file.read()
            result = extract_po_from_pdf(
                pdf_bytes=pdf_bytes,
                original_filename=pdf_file.name,
                user_id=str(request.user.id),
            )

            # Update PODocument record with results
            doc.s3_key = result.get('s3_key', '')
            doc.s3_url = result.get('s3_url', '')
            doc.extraction_status = result.get('extraction_status', 'failed')
            doc.extraction_error = result.get('error', '') or ''

            extracted = result.get('extracted_data', {})
            doc.extracted_data = extracted
            doc.document_type = extracted.get('document_type', 'unknown')
            doc.save()

            if not result.get('success'):
                return Response(
                    {
                        'success': False,
                        'document_id': str(doc.id),
                        'error': result.get('error', 'Extraction failed'),
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

            return Response(
                {
                    'success': True,
                    'document_id': str(doc.id),
                    's3_key': doc.s3_key,
                    's3_url': doc.s3_url,
                    'extraction_status': doc.extraction_status,
                    'document_type': doc.document_type,
                    'raw_text_length': result.get('raw_text_length', 0),
                    'extracted_data': extracted,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception('[PODocumentViewSet] Unhandled extraction error')
            doc.extraction_status = 'failed'
            doc.extraction_error = str(exc)
            doc.save()
            return Response(
                {'success': False, 'document_id': str(doc.id), 'error': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=['post'], url_path='confirm_po')
    def confirm_po(self, request, pk=None):
        """
        POST /api/v1/procurement/po-documents/{id}/confirm_po/
        Link this document to an existing PurchaseOrder (after the user saves it).
        Body: { "purchase_order_id": "<uuid>" }
        """
        doc = self.get_object()
        po_id = request.data.get('purchase_order_id')
        if not po_id:
            return Response({'error': 'purchase_order_id required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            po = PurchaseOrder.objects.get(pk=po_id)
        except PurchaseOrder.DoesNotExist:
            return Response({'error': 'PurchaseOrder not found'}, status=status.HTTP_404_NOT_FOUND)

        doc.confirmed_po = po
        doc.save(update_fields=['confirmed_po'])
        return Response({'success': True, 'document_id': str(doc.id), 'purchase_order_id': str(po.id)})


# ------------------------------------------------------------------------------
# MASTER DATABASE API VIEWSETS - Professional Project-Based Procurement
# ------------------------------------------------------------------------------

class CostCenterViewSet(viewsets.ModelViewSet):
    """
    Cost Center API - Master organizational cost center registry.
    Soft-coded for departmental budget tracking and financial reporting.
    
    🔐 SECURITY: Requires 'procurement' module access (soft-coded from rbac_config.py)
    """
    queryset = CostCenter.objects.all()
    serializer_class = CostCenterSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'procurement'
    filterset_fields = ['department', 'division', 'is_active']
    search_fields = ['code', 'name', 'department', 'division']
    ordering_fields = ['code', 'name', 'department', 'created_at']
    ordering = ['code']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Soft-coded filter: active only
        if self.request.query_params.get('active_only') == 'true':
            queryset = queryset.filter(is_active=True)
        
        # Soft-coded filter: parent cost center
        parent_id = self.request.query_params.get('parent')
        if parent_id:
            queryset = queryset.filter(parent_id=parent_id)
        
        return queryset.select_related('parent', 'manager')


class BudgetViewSet(viewsets.ModelViewSet):
    """
    Budget Allocation API - Project budget lines with spend tracking.
    Soft-coded for professional financial control and variance analysis.
    
    🔐 SECURITY: Requires 'procurement' module access (soft-coded from rbac_config.py)
    """
    queryset = Budget.objects.all()
    serializer_class = BudgetSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'procurement'
    filterset_fields = ['project', 'category', 'fiscal_year', 'is_approved']
    search_fields = ['project__project_number', 'project__project_name', 'description']
    ordering_fields = ['category', 'allocated_amount', 'fiscal_year', 'created_at']
    ordering = ['project', 'category']
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Soft-coded filter: approved only
        if self.request.query_params.get('approved_only') == 'true':
            queryset = queryset.filter(is_approved=True)
        
        # Soft-coded filter: current fiscal year
        if self.request.query_params.get('current_year') == 'true':
            from django.utils import timezone
            current_year = timezone.now().year
            queryset = queryset.filter(fiscal_year=current_year)
        
        # Soft-coded filter: over budget
        if self.request.query_params.get('over_budget') == 'true':
            # This requires custom filtering - will add annotation
            pass
        
        return queryset.select_related('project', 'cost_center', 'approved_by')
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve budget allocation (soft-coded approval workflow)"""
        budget = self.get_object()
        budget.is_approved = True
        budget.approved_by = request.user
        budget.approved_at = timezone.now()
        budget.save(update_fields=['is_approved', 'approved_by', 'approved_at'])
        return Response({'success': True, 'budget_id': str(budget.id)})


class ProjectViewSet(viewsets.ModelViewSet):
    """
    Project Master Registry API - Central project database.
    Professional project-based procurement with budget tracking.
    
    Features:
      - List/Detail views with different serializers
      - Project-based PO aggregation
      - Budget tracking and variance analysis
      - Invoice reconciliation (A/P + A/R)
      - Soft-coded filtering and search
    
    🔐 SECURITY: Requires 'procurement' module access (soft-coded from rbac_config.py)
    """
    queryset = Project.objects.all()
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'procurement'
    filterset_fields = ['status', 'project_type', 'is_active', 'is_billable', 'health_status']
    search_fields = ['project_number', 'project_name', 'client_name', 'description']
    ordering_fields = ['project_number', 'project_name', 'start_date', 'created_at', 'status']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        """Use detailed serializer for retrieve, lightweight for list"""
        if self.action == 'retrieve':
            return ProjectDetailSerializer
        return ProjectListSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Soft-coded filter: active only (default for most users)
        if self.request.query_params.get('active_only', 'true') == 'true':
            queryset = queryset.filter(is_active=True)
        
        # Soft-coded filter: my projects only
        if self.request.query_params.get('my_projects') == 'true':
            queryset = queryset.filter(
                Q(project_manager=self.request.user) |
                Q(lead_engineer=self.request.user) |
                Q(team_members=self.request.user)
            ).distinct()
        
        # Soft-coded filter: health status
        health = self.request.query_params.get('health')
        if health:
            queryset = queryset.filter(health_status=health)
        
        return queryset.select_related(
            'cost_center', 'project_manager', 'lead_engineer'
        ).prefetch_related('team_members', 'budgets')
    
    @action(detail=True, methods=['get'])
    def purchase_orders(self, request, pk=None):
        """Get all purchase orders for this project"""
        project = self.get_object()
        pos = project.purchase_orders.all().order_by('-created_at')
        serializer = PurchaseOrderSerializer(pos, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def financial_summary(self, request, pk=None):
        """
        Get comprehensive financial summary for project.
        Soft-coded aggregations for budget, PO, and invoice tracking.
        """
        project = self.get_object()
        
        # Aggregate purchase orders
        po_stats = project.purchase_orders.aggregate(
            total_count=Count('id'),
            total_value=Sum('total_amount'),
            invoiced_value=Sum('total_invoiced_amount'),
        )
        
        # Budget summary
        budget_stats = project.budgets.aggregate(
            total_allocated=Sum('allocated_amount'),
            approved_count=Count('id', filter=Q(is_approved=True)),
        )
        
        summary = {
            'project_number': project.project_number,
            'project_name': project.project_name,
            'contract_value': float(project.contract_value or 0),
            'contract_currency': project.contract_currency,
            'budgets': {
                'total_allocated': float(budget_stats['total_allocated'] or 0),
                'approved_budget_lines': budget_stats['approved_count'],
                'total_spent': float(project.get_total_spent()),
                'remaining': float(project.get_total_budget() - project.get_total_spent()),
                'utilization_percentage': float(project.get_budget_utilization()),
                'is_over_budget': project.is_over_budget(),
            },
            'purchase_orders': {
                'count': po_stats['total_count'],
                'total_value': float(po_stats['total_value'] or 0),
                'invoiced_value': float(po_stats['invoiced_value'] or 0),
                'pending_invoice': float((po_stats['total_value'] or 0) - (po_stats['invoiced_value'] or 0)),
            },
            'health_status': project.health_status,
            'progress_percentage': float(project.progress_percentage),
        }
        
        return Response(summary)
    
    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """
        Dashboard statistics for project overview.
        Soft-coded KPIs for project portfolio management.
        """
        queryset = self.filter_queryset(self.get_queryset())
        
        stats = {
            'total_projects': queryset.count(),
            'active_projects': queryset.filter(status='active').count(),
            'completed_projects': queryset.filter(status='completed').count(),
            'projects_on_hold': queryset.filter(status='on_hold').count(),
            'health_breakdown': {
                'green': queryset.filter(health_status='green').count(),
                'yellow': queryset.filter(health_status='yellow').count(),
                'red': queryset.filter(health_status='red').count(),
            },
            'total_contract_value': queryset.aggregate(
                total=Sum('contract_value')
            )['total'] or 0,
        }
        
        return Response(stats)
