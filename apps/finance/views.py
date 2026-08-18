"""
Finance API Views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

# RBAC - Module-level access control (soft-coded)
from apps.rbac.permissions import HasModuleAccess
from django.core.files.storage import default_storage
from django.shortcuts import get_object_or_404
from django.views.decorators.clickjacking import xframe_options_exempt
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
import os
import uuid

# Import Data Visibility for Row-Level Security
from apps.rbac.data_visibility_mixin import TeamCollaborationMixin

from .models import Invoice, Approval, ApprovalRoute, InvoiceStatus, InvoiceOCRJob
from .serializers import (
    InvoiceListSerializer, InvoiceDetailSerializer, InvoiceUploadSerializer,
    ApprovalSerializer, ApprovalRouteSerializer, ApprovalDecisionSerializer,
    InvoiceExportFilterSerializer, InvoiceOCRJobSerializer, PayablePaymentSerializer,
)
from .services.workflow_service import FinanceWorkflowService
from .services.export_service import InvoiceExportService
import logging

logger = logging.getLogger(__name__)


class InvoiceViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    """
    Invoice management API
    
    🔐 Data Visibility:
    - Finance team members (with finance module) see all invoices for collaboration
    - Users without finance module have no access
    - Admins see everything
    
    🔐 SECURITY: Requires 'finance' module access (soft-coded from rbac_config.py)
    """
    # Data visibility configuration
    visibility_module_code = 'finance'
    visibility_owner_field = 'created_by'
    
    queryset = Invoice.objects.all().order_by('-created_at')
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'finance'
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @staticmethod
    def _read_invoice_pdf(request):
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'file': 'Select a supplier invoice PDF.'})
        return uploaded_file, uploaded_file.read()
    
    def get_serializer_class(self):
        if self.action == 'list':
            return InvoiceListSerializer
        elif self.action == 'upload':
            return InvoiceUploadSerializer
        return InvoiceDetailSerializer
    

    def get_permissions(self):
        # Preview action doesn't require authentication (token validated inside method)
        if self.action == 'preview':
            return []
        return super().get_permissions()

    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by type
        type_filter = self.request.query_params.get('type')
        if type_filter:
            queryset = queryset.filter(invoice_type=type_filter)
        
        # Search
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                invoice_number__icontains=search
            ) | queryset.filter(
                vendor_name__icontains=search
            )
        
        return queryset
    
    @action(detail=False, methods=['post'])
    def upload(self, request):
        """
        Upload and process invoice
        """
        invoice = None
        try:
            logger.info(f"📥 UPLOAD REQUEST RECEIVED from user: {request.user}")
            logger.info(f"📦 Files: {list(request.FILES.keys())}")
            logger.info(f"📋 Data: {dict(request.data)}")
            
            uploaded_file = request.FILES.get('file')
            if not uploaded_file:
                logger.error("❌ No file in request")
                return Response(
                    {'error': 'No file provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate file type
            if not uploaded_file.name.lower().endswith('.pdf'):
                return Response(
                    {'error': 'Only PDF files are supported'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate file size (20MB max)
            if uploaded_file.size > 20 * 1024 * 1024:
                return Response(
                    {'error': 'File size exceeds 20MB limit'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Generate unique filename
            ext = os.path.splitext(uploaded_file.name)[1]
            filename = f"{uuid.uuid4()}{ext}"
            
            # Save file
            file_path = default_storage.save(f'invoices/{filename}', uploaded_file)
            # Use relative path (works with both filesystem and S3 storage)
            # Don't use default_storage.path() - it fails with S3 backend
            
            # Create invoice record
            invoice = Invoice.objects.create(
                invoice_number=request.data.get('invoice_number', f'INV-{uuid.uuid4().hex[:8].upper()}'),
                vendor_name=request.data.get('vendor_name'),
                total_amount=request.data.get('total_amount'),
                currency=request.data.get('currency', 'AED'),
                original_filename=uploaded_file.name[:500],  # Truncate to field limit
                file_path=file_path[:1000],  # Use relative path (works with both S3 and filesystem)
                status=InvoiceStatus.PENDING_EXTRACTION,
                submitted_by=request.user
            )
            
            logger.info(f"Invoice {invoice.id} created, starting processing workflow")
            
            # Process invoice asynchronously (or synchronously for now)
            workflow_service = FinanceWorkflowService()
            success = workflow_service.process_invoice(invoice.id)
            
            # Reload invoice to get latest status
            invoice.refresh_from_db()
            serializer = InvoiceDetailSerializer(invoice)
            
            # Always return the invoice data directly, but add metadata about processing status
            response_data = serializer.data
            if not success:
                response_data['_processing_warning'] = 'Invoice uploaded but processing encountered issues'
            
            return Response(response_data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Invoice upload failed: {e}", exc_info=True)
            
            # If invoice was created, return it with error metadata
            if invoice:
                try:
                    invoice.refresh_from_db()
                    serializer = InvoiceDetailSerializer(invoice)
                    response_data = serializer.data
                    response_data['_error'] = 'Processing failed'
                    response_data['_error_details'] = str(e)
                    return Response(response_data, status=status.HTTP_201_CREATED)
                except:
                    pass
            
            return Response(
                {'error': 'Upload failed', 'details': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'], url_path='import-preview')
    def import_preview(self, request):
        """Queue OCR and return immediately with a durable polling identifier."""
        import hashlib
        from django.core.files.base import ContentFile
        from .services.vendor_invoice_import import VendorInvoiceImportService
        from .tasks import process_vendor_invoice_ocr

        uploaded_file, pdf_bytes = self._read_invoice_pdf(request)
        service = VendorInvoiceImportService()
        service.validate_pdf(pdf_bytes, uploaded_file.name)
        source_hash = hashlib.sha256(pdf_bytes).hexdigest()
        duplicate = Invoice.objects.filter(source_file_sha256=source_hash).first()
        if duplicate:
            return Response({
                'file': 'This exact PDF has already been recorded.',
                'duplicate_invoice_id': duplicate.pk,
                'duplicate_invoice_number': duplicate.invoice_number,
            }, status=status.HTTP_400_BAD_REQUEST)
        staged_path = default_storage.save(
            f'invoice_ocr_queue/{uuid.uuid4()}.pdf', ContentFile(pdf_bytes),
        )
        job = InvoiceOCRJob.objects.create(
            original_filename=uploaded_file.name[:500], file_path=staged_path[:1000],
            source_file_sha256=source_hash, requested_by=request.user,
        )
        try:
            process_vendor_invoice_ocr.delay(str(job.id))
        except Exception:
            default_storage.delete(staged_path)
            job.delete()
            raise
        return Response({
            'job_id': str(job.id), 'status': job.status,
            'message': 'Invoice OCR queued for background processing.',
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=['get'], url_path=r'ocr-jobs/(?P<job_id>[^/.]+)')
    def ocr_job(self, request, job_id=None):
        job = get_object_or_404(InvoiceOCRJob, pk=job_id, requested_by=request.user)
        return Response(InvoiceOCRJobSerializer(job).data)

    @action(detail=False, methods=['post'], url_path='import-reviewed')
    def import_reviewed(self, request):
        """Persist manually reviewed OCR fields and an explicitly confirmed PO match."""
        from .services.vendor_invoice_import import (
            VendorInvoiceImportService,
            parse_reviewed_data,
        )

        uploaded_file, pdf_bytes = self._read_invoice_pdf(request)
        reviewed_data = parse_reviewed_data(request.data.get('reviewed_data'))
        invoice = VendorInvoiceImportService().save_reviewed(
            pdf_bytes=pdf_bytes,
            filename=uploaded_file.name,
            reviewed_data=reviewed_data,
            user=request.user,
            expected_sha256=request.data.get('source_file_sha256', ''),
        )
        return Response(
            {
                'message': f'Successfully recorded {invoice.invoice_number}!',
                'invoice': InvoiceDetailSerializer(invoice).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['get'], url_path='combined-summary')
    def combined_summary(self, request):
        """Read-only A/R + A/P summary; each currency remains isolated."""
        from .services.combined_invoice_summary import build_combined_invoice_summary

        return Response(build_combined_invoice_summary(payables=self.get_queryset()))

    @action(detail=True, methods=['post'], url_path='reconcile-status')
    def reconcile_status(self, request, pk=None):
        from .services.payables import reconcile_invoice_status
        invoice = self.get_object()
        changed = reconcile_invoice_status(invoice, user=request.user)
        invoice.refresh_from_db()
        return Response({'changed_fields': changed, 'invoice': InvoiceDetailSerializer(invoice).data})

    @action(detail=True, methods=['post'], url_path='run-three-way-match')
    def run_three_way_match(self, request, pk=None):
        from .services.payables import evaluate_three_way_match
        invoice = self.get_object()
        allocations = [
            evaluate_three_way_match(allocation, user=request.user)
            for allocation in invoice.po_allocations.select_related('purchase_order').all()
        ]
        invoice.refresh_from_db()
        return Response({
            'allocations_checked': len(allocations),
            'invoice': InvoiceDetailSerializer(invoice).data,
        })

    @action(detail=True, methods=['post'], url_path='payment-operations')
    def payment_operations(self, request, pk=None):
        from .services.payables import record_payment_operation
        invoice = self.get_object()
        entry = record_payment_operation(invoice, request.data, request.user)
        invoice.refresh_from_db()
        return Response({
            'operation': PayablePaymentSerializer(entry).data,
            'invoice': InvoiceDetailSerializer(invoice).data,
        }, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get'])
    @method_decorator(xframe_options_exempt)
    def preview(self, request, pk=None):
        '''Preview invoice PDF inline - supports query param token for iframe access'''
        from rest_framework_simplejwt.tokens import AccessToken
        from rest_framework_simplejwt.exceptions import TokenError
        from django.contrib.auth import get_user_model
        from apps.finance.models import Invoice
        from django.http import FileResponse
        
        User = get_user_model()

        # Get token from query param
        token = request.GET.get('token')
        if not token:
            return Response(
                {'error': 'Token required'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Validate token
        try:
            access_token = AccessToken(token)
            user = User.objects.get(id=access_token['user_id'])
        except TokenError as e:
            logger.error(f'Invalid token: {e}')
            return Response(
                {'error': 'Invalid or expired token'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        except User.DoesNotExist:
            logger.error(f'User not found for token')
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        except Exception as e:
            logger.error(f'Token validation error: {e}', exc_info=True)
            return Response(
                {'error': 'Authentication failed'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Get invoice
        try:
            invoice = Invoice.objects.get(id=pk)
        except Invoice.DoesNotExist:
            return Response(
                {'error': 'Invoice not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        if not invoice.file_path or not default_storage.exists(invoice.file_path):
            return Response(
                {'error': 'PDF file not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            return FileResponse(
                default_storage.open(invoice.file_path, 'rb'),
                content_type='application/pdf'
            )
        except Exception as e:
            logger.error(f'Error serving PDF: {e}', exc_info=True)
            return Response(
                {'error': 'Failed to load PDF'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        invoice = self.get_object()
        
        if not default_storage.exists(invoice.file_path):
            return Response(
                {'error': 'File not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        from django.http import FileResponse
        return FileResponse(
            default_storage.open(invoice.file_path, 'rb'),
            content_type='application/pdf',
            as_attachment=True,
            filename=invoice.original_filename
        )
    
    @action(detail=True, methods=['post'])
    def process(self, request, pk=None):
        """
        Process or reprocess an invoice through the workflow
        """
        invoice = self.get_object()
        
        logger.info(f"Processing invoice {invoice.id} (current status: {invoice.status})")
        
        workflow_service = FinanceWorkflowService()
        success = workflow_service.process_invoice(invoice.id)
        
        # Reload to get latest data
        invoice.refresh_from_db()
        serializer = self.get_serializer(invoice)
        
        if success:
            return Response({
                'message': 'Invoice processed successfully',
                'invoice': serializer.data
            })
        else:
            return Response({
                'warning': 'Processing completed with issues',
                'invoice': serializer.data
            }, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def reprocess(self, request, pk=None):
        """Reprocess failed invoice - alias for process action"""
        return self.process(request, pk)
    
    @action(detail=False, methods=['post'], parser_classes=[JSONParser])
    def export(self, request):
        """
        Export invoices to Excel or PDF with smart filtering
        
        POST body (all optional):
        {
            "format": "excel" | "pdf",
            "status": ["pending_approval", "approved"],
            "invoice_type": ["finance", "it"],
            "date_from": "2024-01-01",
            "date_to": "2024-12-31",
            "min_amount": 1000,
            "max_amount": 50000,
            "search": "vendor name"
        }
        """
        try:
            # Validate filters
            serializer = InvoiceExportFilterSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
            filters = serializer.validated_data
            export_format = filters.pop('format', 'excel')
            
            # Initialize export service
            export_service = InvoiceExportService()
            
            # Generate export
            if export_format == 'excel':
                file_buffer = export_service.export_to_excel(filters)
                content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            else:  # pdf
                file_buffer = export_service.export_to_pdf(filters)
                content_type = 'application/pdf'
            
            # Generate filename
            filename = export_service.get_export_filename(
                format='xlsx' if export_format == 'excel' else 'pdf',
                filters=filters
            )
            
            # Return file response
            from django.http import HttpResponse
            response = HttpResponse(file_buffer.read(), content_type=content_type)
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            return response
            
        except Exception as e:
            logger.error(f"Export failed: {e}")
            return Response(
                {'error': f'Export failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ApprovalRouteViewSet(viewsets.ModelViewSet):
    """
    Approval route configuration API
    
    🔐 SECURITY: Requires 'finance' module access (soft-coded from rbac_config.py)
    """
    queryset = ApprovalRoute.objects.all().order_by('-priority', 'invoice_type')
    serializer_class = ApprovalRouteSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'finance'


@api_view(['GET'])
@permission_classes([AllowAny])
def get_approval_details(request, token):
    """
    Get approval details for frontend form (no authentication required - token-based)
    Used by RAD AI approval page
    """
    try:
        approval = get_object_or_404(Approval, approval_token=token)
        invoice = approval.invoice
        
        # Check if already processed
        already_decided = approval.status != 'pending'
        
        return Response({
            'approval': {
                'id': approval.id,
                'approval_token': str(approval.approval_token),
                'approver_name': approval.approver_name,
                'approver_email': approval.approver_email,
                'approval_level': approval.approval_level,
                'level_name': approval.level_name,
                'status': approval.status,
                'already_decided': already_decided,
                'decision_date': approval.decision_date,
                'comments': approval.comments
            },
            'invoice': {
                'id': invoice.id,
                'invoice_number': invoice.invoice_number,
                'vendor_name': invoice.vendor_name,
                'total_amount': str(invoice.total_amount) if invoice.total_amount else '0',
                'currency': invoice.currency,
                'invoice_type': invoice.invoice_type,
                'invoice_type_display': invoice.get_invoice_type_display(),
                'invoice_date': invoice.invoice_date,
                'status': invoice.status,
                'file_url': request.build_absolute_uri(invoice.file_path.url) if (invoice.file_path and hasattr(invoice.file_path, 'url')) else None
            }
        })
    except Exception as e:
        logger.error(f"Error fetching approval details: {e}", exc_info=True)
        return Response(
            {'error': 'Invalid or expired approval link'},
            status=status.HTTP_404_NOT_FOUND
        )

@api_view(['POST'])
@permission_classes([AllowAny])
def submit_approval_decision(request, token):
    """
    Submit approval decision from RAD AI frontend form
    Returns JSON response for frontend to handle
    """
    try:
        approval = get_object_or_404(Approval, approval_token=token)
        invoice = approval.invoice
        
        # Check if already processed
        if approval.status != 'pending':
            return Response(
                {
                    'error': 'already_processed',
                    'message': f'This approval has already been {approval.status}.',
                    'status': approval.status
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get decision and comments from request
        decision = request.data.get('decision')  # 'approve' or 'reject'
        comments = request.data.get('comments', '')
        
        if decision not in ['approve', 'reject']:
            return Response(
                {'error': 'Invalid decision. Must be approve or reject.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Process the approval
        workflow_service = FinanceWorkflowService()
        success = workflow_service.process_approval_decision(
            str(token),
            decision,
            comments or f"{decision.title()}d via RAD AI by {approval.approver_name}"
        )
        
        if success:
            approval.refresh_from_db()
            invoice.refresh_from_db()
            
            # Determine next step info
            next_level_exists = invoice.approvals.filter(
                approval_level=approval.approval_level + 1
            ).exists()
            
            if decision == 'approve':
                if next_level_exists:
                    next_approvals = invoice.approvals.filter(
                        approval_level=approval.approval_level + 1,
                        status='pending'
                    )
                    next_level_names = [na.approver_name for na in next_approvals]
                    next_step = f"Email sent to {', '.join(next_level_names)}"
                else:
                    next_step = "Invoice fully approved - All levels complete"
            else:
                next_step = "Invoice rejected - Vendor will be notified"
            
            return Response({
                'success': True,
                'message': f"Invoice {decision}d successfully",
                'decision': decision,
                'next_step': next_step,
                'invoice_status': invoice.status,
                'approval_status': approval.status
            })
        else:
            return Response(
                {'error': 'Failed to process approval decision'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            
    except Exception as e:
        logger.error(f"Error processing approval: {e}", exc_info=True)
        return Response(
            {'error': 'An error occurred while processing your decision'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])  # Email links don't have auth
def approval_action(request, token):
    """
    Legacy endpoint for direct email link approval (now redirects to frontend)
    Returns HTML page that redirects to RAD AI approval page
    """
    try:
        approval = get_object_or_404(Approval, approval_token=token)
        invoice = approval.invoice
        
        # Get frontend URL from settings
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
        redirect_url = f"{frontend_url}/finance/approve/{token}"
        
        # Return HTML redirect page
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Opening RAD AI...</title>
            <meta http-equiv="refresh" content="0;url={redirect_url}">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    margin: 0;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                }}
                .container {{
                    text-align: center;
                    padding: 40px;
                }}
                .spinner {{
                    border: 4px solid rgba(255,255,255,0.3);
                    border-radius: 50%;
                    border-top: 4px solid white;
                    width: 40px;
                    height: 40px;
                    animation: spin 1s linear infinite;
                    margin: 20px auto;
                }}
                @keyframes spin {{
                    0% {{ transform: rotate(0deg); }}
                    100% {{ transform: rotate(360deg); }}
                }}
                h1 {{
                    font-size: 28px;
                    margin-bottom: 20px;
                }}
                p {{
                    font-size: 16px;
                    opacity: 0.9;
                }}
                a {{
                    color: white;
                    text-decoration: underline;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="spinner"></div>
                <h1>🔍 Opening RAD AI Finance...</h1>
                <p>Redirecting you to the approval form...</p>
                <p style="margin-top: 30px; font-size: 14px;">
                    If you're not redirected automatically,<br>
                    <a href="{redirect_url}">click here to open the approval form</a>
                </p>
            </div>
        </body>
        </html>
        """
        return HttpResponse(html)
        
    except Exception as e:
        logger.error(f"Error in approval_action: {e}", exc_info=True)
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Error</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    margin: 0;
                    background: #f5f5f5;
                }}
                .message {{
                    background: #f8d7da;
                    border: 3px solid #dc3545;
                    color: #721c24;
                    padding: 40px;
                    border-radius: 10px;
                    text-align: center;
                    max-width: 500px;
                }}
            </style>
        </head>
        <body>
            <div class="message">
                <h1>❌ Invalid Link</h1>
                <p>This approval link is invalid or has expired.</p>
            </div>
        </body>
        </html>
        """
        return HttpResponse(html, status=404)
        
        # Get action from query params
        action = request.GET.get('action')
        
        if action and action in ['approve', 'reject']:
            # Process the action
            if approval.status != 'pending':
                # Already processed
                html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Already Processed</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center; }}
                        .message {{ background: #fff3cd; border: 2px solid #ffc107; padding: 30px; border-radius: 10px; }}
                        h1 {{ color: #856404; }}
                        .info {{ margin: 20px 0; font-size: 16px; color: #555; }}
                        .button {{ display: inline-block; margin-top: 20px; padding: 12px 30px; background: #007bff; color: white; text-decoration: none; border-radius: 5px; }}
                    </style>
                </head>
                <body>
                    <div class="message">
                        <h1>⚠️ Already Processed</h1>
                        <p class="info">This approval has already been {approval.status}.</p>
                        <p class="info"><strong>Invoice:</strong> {invoice.invoice_number}</p>
                        <p class="info"><strong>Status:</strong> {approval.get_status_display()}</p>
                        <a href="http://localhost:5173/finance/invoices/{invoice.id}" class="button">View Invoice Details</a>
                    </div>
                </body>
                </html>
                """
                return HttpResponse(html)
            
            # Process the approval/rejection
            workflow_service = FinanceWorkflowService()
            success = workflow_service.process_approval_decision(
                str(token),
                action,
                f"{action.title()}d via email by {approval.approver_name}"
            )
            
            if success:
                approval.refresh_from_db()
                
                # Determine next step message
                if action == 'approve':
                    next_step_msg = "Email has been sent to the next approver in the chain."
                    icon = "✅"
                    bg_color = "#d4edda"
                    border_color = "#28a745"
                    text_color = "#155724"
                else:
                    next_step_msg = "The invoice has been rejected and will not proceed further."
                    icon = "❌"
                    bg_color = "#f8d7da"
                    border_color = "#dc3545"
                    text_color = "#721c24"
                
                html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Invoice {action.title()}d</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background: #f5f5f5; }}
                        .message {{ background: {bg_color}; border: 3px solid {border_color}; color: {text_color}; padding: 50px; border-radius: 10px; text-align: center; max-width: 500px; }}
                        .icon {{ font-size: 72px; margin-bottom: 20px; }}
                        h1 {{ margin: 20px 0; font-size: 28px; }}
                        .invoice {{ font-weight: bold; font-size: 20px; margin: 20px 0; }}
                        .info {{ font-size: 18px; line-height: 1.8; margin: 25px 0; }}
                        .close-note {{ margin-top: 35px; font-size: 14px; opacity: 0.8; }}
                    </style>
                </head>
                <body>
                    <div class="message">
                        <div class="icon">{icon}</div>
                        <h1>Invoice {action.title()}d</h1>
                        <div class="invoice">{invoice.invoice_number}</div>
                        <div class="info">{next_step_msg}</div>
                        <div class="close-note">You can close this window now.</div>
                    </div>
                </body>
                </html>
                """
                return HttpResponse(html)
            else:
                html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Error</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center; }}
                        .message {{ background: #f8d7da; border: 2px solid #dc3545; padding: 30px; border-radius: 10px; }}
                        h1 {{ color: #721c24; }}
                    </style>
                </head>
                <body>
                    <div class="message">
                        <h1>❌ Error Processing Approval</h1>
                        <p>Failed to process your decision. Please contact support.</p>
                    </div>
                </body>
                </html>
                """
                return HttpResponse(html, status=500)
        
        # No action specified - show approval page
        elif request.method == 'GET':
            return Response({
                'invoice': InvoiceDetailSerializer(invoice).data,
                'approval': ApprovalSerializer(approval).data,
                'already_decided': approval.status != 'pending'
            })
        
        elif request.method == 'POST':
            if approval.status != 'pending':
                return Response(
                    {'error': 'This approval has already been processed'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            serializer = ApprovalDecisionSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
            # Process decision
            workflow_service = FinanceWorkflowService()
            success = workflow_service.process_approval_decision(
                str(token),
                serializer.validated_data['action'],
                serializer.validated_data.get('comments', '')
            )
            
            if success:
                approval.refresh_from_db()
                return Response({
                    'success': True,
                    'message': f'Invoice {serializer.validated_data["action"]}d successfully',
                    'approval': ApprovalSerializer(approval).data
                })
            else:
                return Response(
                    {'error': 'Failed to process approval'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
    
    except Exception as e:
        logger.error(f"Approval action failed: {e}", exc_info=True)
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Error</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; text-align: center; }}
                .message {{ background: #f8d7da; border: 2px solid #dc3545; padding: 30px; border-radius: 10px; }}
                h1 {{ color: #721c24; }}
            </style>
        </head>
        <body>
            <div class="message">
                <h1>❌ Error</h1>
                <p>An error occurred: {str(e)}</p>
            </div>
        </body>
        </html>
        """
        return HttpResponse(html, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_stats(request):
    """Get dashboard statistics"""
    try:
        stats = {
            'total_invoices': Invoice.objects.count(),
            'pending_approval': Invoice.objects.filter(status=InvoiceStatus.PENDING_APPROVAL).count(),
            'approved': Invoice.objects.filter(status=InvoiceStatus.APPROVED).count(),
            'rejected': Invoice.objects.filter(status=InvoiceStatus.REJECTED).count(),
            'by_type': {
                'finance': Invoice.objects.filter(invoice_type='finance').count(),
                'it': Invoice.objects.filter(invoice_type='it').count(),
                'project': Invoice.objects.filter(invoice_type='project').count(),
                'admin': Invoice.objects.filter(invoice_type='admin').count(),
            },
            'recent_invoices': InvoiceListSerializer(
                Invoice.objects.all()[:5],
                many=True
            ).data
        }
        
        return Response(stats)
    
    except Exception as e:
        logger.error(f"Dashboard stats failed: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
