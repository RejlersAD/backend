"""
Procurement Management Views
API endpoints for procurement workflows
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied, ValidationError

# RBAC - Module-level access control (soft-coded)
from apps.rbac.permissions import HasModuleAccess
from django.db.models import Q, Count, Sum, Avg
from django.utils import timezone
from datetime import timedelta

from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db import models as django_models
from django.http import HttpResponse
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
import io

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
from .services.requisition_workflow import RequisitionWorkflowService
from .services.requisition_conversion import RequisitionConversionService
from .services.purchase_order_numbering import PurchaseOrderNumberService
from .services.requisition_status import canonicalize_pr_status, stored_values_for

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
    
    ≡ƒöÉ SECURITY: Requires 'procurement_vendors' module access (soft-coded from rbac_config.py)
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
    - Two-tier approval workflow (PM ΓåÆ VP)
    - Advanced filtering and search
    - PDF generation aligned with template
    
    ≡ƒöÉ SECURITY: Requires 'procurement_requisitions' module access (soft-coded from rbac_config.py)
    """
    
    queryset = PurchaseRequisition.objects.all().order_by('-created_at')
    serializer_class = PurchaseRequisitionSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'procurement_requisitions'
    parser_classes = [FormParser, MultiPartParser, JSONParser]

    def get_permissions(self):
        # Conversion creates a Purchase Order and therefore requires order
        # module access in addition to authentication.
        if getattr(self, 'action', None) == 'convert_to_po':
            self.module_required = 'procurement_orders'
        return super().get_permissions()

    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status__in=stored_values_for(status_filter))
        
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

    def _enforce_owner_mutation(self, pr, *, deletable=False):
        if (
            str(pr.issued_by_id) != str(self.request.user.id)
            and not RequisitionWorkflowService._is_super_admin(self.request.user)
        ):
            raise PermissionDenied('Only the requisition issuer may modify this requisition.')

        allowed_statuses = {'draft', 'rejected', 'cancelled'} if deletable else {'draft'}
        if canonicalize_pr_status(pr.status) not in allowed_statuses:
            action = 'deleted' if deletable else 'edited'
            raise ValidationError({
                'error': f'Only {", ".join(sorted(allowed_statuses))} requisitions can be {action}.'
            })

    def update(self, request, *args, **kwargs):
        self._enforce_owner_mutation(self.get_object())
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._enforce_owner_mutation(self.get_object(), deletable=True)
        return super().destroy(request, *args, **kwargs)
    
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

    def _build_requisition_response(self, pr):
        """Ensure API response contains both status and approval_hierarchy."""
        serializer = self.get_serializer(pr)
        payload = dict(serializer.data)
        payload['status'] = canonicalize_pr_status(pr.status)
        payload['approval_hierarchy'] = pr.approval_workflow_config if isinstance(pr.approval_workflow_config, list) else []
        payload['convert_to_po_enabled'] = canonicalize_pr_status(pr.status) == 'approved'
        return payload

    @action(detail=False, methods=['get'], url_path='pending-for-me')
    def pending_for_me(self, request):
        """Return only PRs whose current workflow stage is assigned to this approver."""
        active_statuses = set()
        for workflow_status in RequisitionWorkflowService.ACTIVE_REVIEW_STATUSES:
            active_statuses.update(stored_values_for(workflow_status))

        queryset = self.get_queryset().filter(status__in=active_statuses)
        is_super_admin = RequisitionWorkflowService._is_super_admin(request.user)
        assigned = []

        for pr in queryset:
            workflow = pr.approval_workflow_config
            if not isinstance(workflow, list):
                continue
            current_stage = next(
                (
                    stage for stage in workflow
                    if isinstance(stage, dict)
                    and str(stage.get('status', 'pending')).lower() != 'approved'
                ),
                None,
            )
            if not current_stage:
                continue
            assigned_user_id = current_stage.get('user_id') or current_stage.get('approver_id')
            if is_super_admin or str(assigned_user_id) == str(request.user.id):
                assigned.append(pr)

        count = len(assigned)
        if str(request.query_params.get('count_only', '')).lower() == 'true':
            return Response({'count': count, 'pending_count': count})

        try:
            limit = max(1, min(int(request.query_params.get('limit', 50)), 100))
        except (TypeError, ValueError):
            limit = 50

        return Response({
            'count': count,
            'results': self.get_serializer(assigned[:limit], many=True).data,
        })

    @action(detail=True, methods=['post'])
    def pm_approve(self, request, pk=None):
        pr = RequisitionWorkflowService.approve(
            pk,
            request.user,
            signature=request.data.get('signature', ''),
            expected_stage_key='pm',
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def pm_reject(self, request, pk=None):
        pr = RequisitionWorkflowService.reject(
            pk,
            request.user,
            request.data.get('reason', ''),
            expected_stage_key='pm',
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def vp_approve(self, request, pk=None):
        pr = RequisitionWorkflowService.approve(
            pk,
            request.user,
            signature=request.data.get('signature', ''),
            expected_stage_key='vp',
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def vp_reject(self, request, pk=None):
        pr = RequisitionWorkflowService.reject(
            pk,
            request.user,
            request.data.get('reason', ''),
            expected_stage_key='vp',
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def eng_manager_approve(self, request, pk=None):
        pr = RequisitionWorkflowService.approve(
            pk,
            request.user,
            signature=request.data.get('signature', ''),
            expected_stage_key='eng_manager',
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def eng_manager_reject(self, request, pk=None):
        pr = RequisitionWorkflowService.reject(
            pk,
            request.user,
            request.data.get('reason', ''),
            expected_stage_key='eng_manager',
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def manager_projects_approve(self, request, pk=None):
        pr = RequisitionWorkflowService.approve(
            pk,
            request.user,
            signature=request.data.get('signature', ''),
            expected_stage_key='manager_projects',
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def manager_projects_reject(self, request, pk=None):
        pr = RequisitionWorkflowService.reject(
            pk,
            request.user,
            request.data.get('reason', ''),
            expected_stage_key='manager_projects',
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def process_dynamic_approval(self, request, pk=None):
        pr = RequisitionWorkflowService.approve(
            pk,
            request.user,
            signature=request.data.get('signature', ''),
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def recommend_vendors(self, request, pk=None):
        """
        AI-powered vendor recommendation based on PR details and historical data
        """
        pr = self.get_object()
        
        # Get all active vendors
        from .models import Vendor
        vendors = Vendor.objects.filter(status='active', rating__gte=3)
        
        # Get historical POs/PRs for vendor performance analysis
        recommendations = []
        
        for vendor in vendors:
            # Calculate vendor score based on multiple factors (soft-coded scoring)
            score = 0.0
            reasons = []
            
            # Factor 1: Rating (40% weight)
            if vendor.rating:
                score += (vendor.rating / 5.0) * 0.4
                reasons.append(f"Rating: {vendor.rating}/5")
            
            # Factor 2: Past orders count (30% weight)
            past_orders = vendor.purchase_orders.count() + vendor.purchase_requisitions.count()
            if past_orders > 0:
                score += min(past_orders / 10.0, 1.0) * 0.3
                reasons.append(f"{past_orders} past orders")
            
            # Factor 3: ICV certification for Abu Dhabi market (15% weight)
            if vendor.is_icv_certified:
                score += 0.15
                reasons.append(f"ICV certified: {vendor.icv_percentage}%")
            
            # Factor 4: ADNOC approval (15% weight)
            if vendor.adnoc_approved:
                score += 0.15
                reasons.append("ADNOC approved")
            
            # Only recommend vendors with score > 0.5
            if score > 0.5:
                recommendations.append({
                    'vendor_id': str(vendor.id),
                    'vendor_code': vendor.vendor_code,
                    'vendor_name': vendor.name,
                    'score': round(score, 2),
                    'rating': vendor.rating,
                    'past_orders': past_orders,
                    'icv_certified': vendor.is_icv_certified,
                    'icv_percentage': float(vendor.icv_percentage) if vendor.icv_percentage else None,
                    'adnoc_approved': vendor.adnoc_approved,
                    'reasons': reasons,
                })
        
        # Sort by score descending
        recommendations.sort(key=lambda x: x['score'], reverse=True)
        
        # Update PR with recommendations
        pr.ai_vendor_recommendations = recommendations[:5]  # Top 5
        pr.save()
        
        return Response({
            'message': f'Generated {len(recommendations)} vendor recommendations',
            'recommendations': recommendations[:5]
        })
    
    @action(detail=False, methods=['get'], url_path='vendor-options')
    def vendor_options(self, request):
        """
        Get vendor options for dropdown selection in PR form
        Supports search by name/code and filtering by ID
        """
        from .models import Vendor
        
        # Get query parameters
        search_query = request.query_params.get('q', '').strip()
        vendor_id = request.query_params.get('id', '').strip()
        try:
            limit = int(request.query_params.get('limit', 30))
            limit = min(max(1, limit), 100)  # Clamp between 1 and 100
        except (TypeError, ValueError):
            limit = 30
        
        # Start with active vendors only
        queryset = Vendor.objects.filter(status='active')
        
        # Filter by specific vendor ID if provided
        if vendor_id:
            try:
                queryset = queryset.filter(id=vendor_id)
            except (ValueError, TypeError):
                pass
        # Otherwise filter by search query
        elif search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) |
                Q(vendor_code__icontains=search_query) |
                Q(email__icontains=search_query)
            )
        
        # Order by rating and name
        queryset = queryset.order_by('-rating', 'name')[:limit]
        
        # Format response for frontend dropdown
        suggestions = []
        for vendor in queryset:
            suggestions.append({
                'id': vendor.id,
                'name': vendor.name,
                'vendor_code': vendor.vendor_code,
                'email': vendor.email,
                'rating': vendor.rating,
                'status': vendor.status,
                'icv_percentage': float(vendor.icv_percentage) if vendor.icv_percentage else None,
                'icv_expiry_date': vendor.icv_expiry_date.strftime('%Y-%m-%d') if vendor.icv_expiry_date else None,
                'is_icv_certified': vendor.is_icv_certified,
                'adnoc_approved': vendor.adnoc_approved,
            })
        
        return Response({
            'suggestions': suggestions,
            'count': len(suggestions)
        })
    
    @action(detail=False, methods=['get'])
    def get_approvers(self, request):
        """
        Get list of users available for approval workflow by role/job title
        Soft-coded: searches by job_title in RBAC UserProfile
        
        Query params:
        - role: 'project_manager', 'engineering_manager', 'manager_projects', 'vp_operations'
        """
        from apps.rbac.models import UserProfile
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        role = request.query_params.get('role', '').lower()
        
        # Soft-coded role-to-title mapping (flexible for different organizations)
        role_title_mapping = {
            'project_manager': ['Project Manager', 'PM', 'Manager - Projects'],
            'engineering_manager': ['Engineering Manager', 'Manager - Engineering', 'Eng Manager'],
            'manager_projects': ['Manager of Projects', 'Manager - Projects', 'Projects Manager'],
            'vp_operations': ['VP Operations', 'Vice President Operations', 'VP - Operations', 'Vice President of Operation'],
        }
        # Soft-coded: the dropdown must always let the requester pick ANY active
        # user as the approver ΓÇö job_title is only used to surface the most
        # relevant users FIRST, never to hide the rest of the user base.
        matched_user_ids = set()

        if role and role in role_title_mapping:
            job_titles = role_title_mapping[role]
            # Exact match first
            matched_qs = UserProfile.objects.filter(
                status='active',
                is_deleted=False,
                job_title__in=job_titles
            ).select_related('user')

            # Fall back to partial/contains match if no exact match found
            if matched_qs.count() == 0:
                q_objects = Q()
                for title in job_titles:
                    q_objects |= Q(job_title__icontains=title)
                matched_qs = UserProfile.objects.filter(
                    q_objects,
                    status='active',
                    is_deleted=False
                ).select_related('user')

            matched_user_ids = set(matched_qs.values_list('user_id', flat=True))

        matched_by_job_title = len(matched_user_ids) > 0

        # Always return the full active user list so the field lets the
        # requester choose/select any user, with job-title matches (if any)
        # surfaced first for convenience.
        # Resolve RBAC eligibility in SQL. Calling has_module_access() once for
        # every profile caused hundreds of remote database queries and left the
        # approver dropdowns appearing empty while they waited.
        profiles = UserProfile.objects.filter(
            Q(user__is_superuser=True)
            | Q(
                roles__is_active=True,
                roles__modules__code='procurement_requisitions',
                roles__modules__is_active=True,
            ),
            status='active',
            is_deleted=False,
            user__is_active=True,
        ).select_related('user').distinct()

        # Sort so job-title matches appear first, then alphabetically
        profiles = sorted(
            profiles,
            key=lambda p: (
                p.user_id not in matched_user_ids,
                (p.user.first_name or '').lower(),
                (p.user.last_name or '').lower(),
            )
        )

        # Format response
        users_list = []
        for profile in profiles:
            users_list.append({
                'id': str(profile.user.id),
                'email': profile.user.email,
                'full_name': profile.user.get_full_name(),
                'first_name': profile.user.first_name,
                'last_name': profile.user.last_name,
                'job_title': profile.job_title,
                'department': profile.department,
                'employee_id': profile.employee_id,
                'job_title_match': profile.user_id in matched_user_ids,
            })
        
        return Response({
            'role': role,
            'count': len(users_list),
            'matched_by_job_title': matched_by_job_title,
            'users': users_list
        })
    
    @action(detail=False, methods=['get'])
    def get_product_services(self, request):
        """
        Get autocomplete suggestions for Product/Service field
        Returns distinct values from existing PRs
        Soft-coded: no hardcoded product list, learns from historical data
        
        Query params:
        - q: search query (optional)
        - limit: max results (default 50)
        """
        search_query = request.query_params.get('q', '').strip()
        limit = int(request.query_params.get('limit', 50))
        
        # Get distinct products from existing PRs
        products_qs = PurchaseRequisition.objects.filter(
            product_service__isnull=False
        ).exclude(
            product_service__exact=''
        ).values_list('product_service', flat=True).distinct()
        
        # Apply search filter if provided
        if search_query:
            products_qs = PurchaseRequisition.objects.filter(
                product_service__icontains=search_query
            ).values_list('product_service', flat=True).distinct()
        
        products_list = list(products_qs[:limit])
        
        return Response({
            'count': len(products_list),
            'suggestions': products_list
        })
    
    @action(detail=False, methods=['get'])
    def get_projects_departments(self, request):
        """
        Get autocomplete suggestions for Project/Department field
        Returns distinct values from existing PRs and Project master table
        
        Query params:
        - q: search query (optional)
        - limit: max results (default 50)
        """
        search_query = request.query_params.get('q', '').strip()
        limit = int(request.query_params.get('limit', 50))
        
        suggestions = []
        
        # Source 1: Project master table
        try:
            projects = Project.objects.exclude(
                status__in=['cancelled', 'archived']
            ).select_related('cost_center')
            if search_query:
                projects = projects.filter(
                    Q(project_name__icontains=search_query) |
                    Q(project_number__icontains=search_query) |
                    Q(cost_center__name__icontains=search_query) |
                    Q(cost_center__code__icontains=search_query) |
                    Q(cost_center__department__icontains=search_query)
                )
            
            for project in projects[:limit]:
                department = ''
                if project.cost_center:
                    department = project.cost_center.department or project.cost_center.name
                suggestions.append({
                    'project_id': str(project.id),
                    'value': f"{project.project_name} ({project.project_number})",
                    'label': f"{project.project_number} - {project.project_name}",
                    'project_number': project.project_number,
                    'department': department,
                    'source': 'master'
                })
        except Exception:
            pass  # Project table may not exist in all environments
        
        # Source 2: Historical PRs
        pr_projects = PurchaseRequisition.objects.filter(
            project_department__isnull=False
        ).exclude(
            project_department__exact=''
        )
        
        if search_query:
            pr_projects = pr_projects.filter(project_department__icontains=search_query)
        
        pr_projects = pr_projects.values_list('project_department', flat=True).distinct()[:limit]
        
        for project in pr_projects:
            if project not in [s['value'] for s in suggestions]:
                suggestions.append({
                    'value': project,
                    'label': project,
                    'source': 'historical'
                })
        
        return Response({
            'count': len(suggestions),
            'suggestions': suggestions[:limit]
        })
    
    @action(detail=False, methods=['get'])
    def get_suppliers(self, request):
        """
        Get autocomplete suggestions for Supplier Name and Business ID
        Returns distinct values from existing PRs and Vendor master table
        
        Query params:
        - q: search query (optional)
        - limit: max results (default 50)
        """
        search_query = request.query_params.get('q', '').strip()
        limit = int(request.query_params.get('limit', 50))
        
        suggestions = []
        
        # Source 1: Vendor master table
        vendors = Vendor.objects.filter(status='active')
        if search_query:
            vendors = vendors.filter(
                Q(name__icontains=search_query) |
                Q(vendor_code__icontains=search_query) |
                Q(trade_license_number__icontains=search_query) |
                Q(tax_id__icontains=search_query) |
                Q(vat_number__icontains=search_query)
            )
        
        for vendor in vendors[:limit]:
            suggestions.append({
                'vendor_id': str(vendor.id),
                'supplier_name': vendor.name,
                'supplier_business_id': (
                    vendor.trade_license_number
                    or vendor.tax_id
                    or vendor.vat_number
                    or vendor.vendor_code
                ),
                'vendor_code': vendor.vendor_code,
                'rating': vendor.rating,
                'source': 'master'
            })
        
        # Source 2: Historical PRs (if not in vendor master)
        if len(suggestions) < limit:
            pr_suppliers = PurchaseRequisition.objects.filter(
                supplier_name__isnull=False
            ).exclude(
                supplier_name__exact=''
            )
            
            if search_query:
                pr_suppliers = pr_suppliers.filter(
                    Q(supplier_name__icontains=search_query) |
                    Q(supplier_business_id__icontains=search_query)
                )
            
            # Get distinct combinations
            pr_suppliers = pr_suppliers.values('supplier_name', 'supplier_business_id').distinct()[:limit]
            
            for supplier in pr_suppliers:
                if supplier['supplier_name'] not in [s['supplier_name'] for s in suggestions]:
                    suggestions.append({
                        'supplier_name': supplier['supplier_name'],
                        'supplier_business_id': supplier['supplier_business_id'],
                        'source': 'historical'
                    })
        
        return Response({
            'count': len(suggestions),
            'suggestions': suggestions[:limit]
        })
    
    @action(detail=False, methods=['get'])
    def get_po_numbers(self, request):
        """
        Get autocomplete suggestions for PO Number reference field
        Returns existing PO numbers from PurchaseOrder table
        
        Query params:
        - q: search query (optional)
        - limit: max results (default 50)
        - status: filter by PO status (optional)
        """
        search_query = request.query_params.get('q', '').strip()
        limit = int(request.query_params.get('limit', 50))
        status_filter = request.query_params.get('status', None)
        
        suggestions = []
        
        # Get PO numbers from PurchaseOrder table
        pos = PurchaseOrder.objects.select_related('vendor').order_by('-created_at')
        
        if status_filter:
            pos = pos.filter(status=status_filter)
        
        if search_query:
            pos = pos.filter(
                Q(po_number__icontains=search_query) |
                Q(vendor__name__icontains=search_query) |
                Q(title__icontains=search_query) |
                Q(description__icontains=search_query)
            )

        for po in pos[:limit]:
            suggestions.append({
                'po_number': po.po_number,
                'supplier_name': po.vendor.name,
                'total_amount': str(po.total_amount) if po.total_amount is not None else None,
                'currency': po.currency,
                'status': po.status,
            })
        
        return Response({
            'count': len(suggestions),
            'suggestions': suggestions
        })
    
    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """Submit a draft into its configured approval workflow."""
        pr = RequisitionWorkflowService.submit(pk, request.user)
        return Response(self._build_requisition_response(pr))

    @action(detail=True, methods=['post'])
    def convert_to_po(self, request, pk=None):
        """Atomically convert an approved requisition into one draft PO."""
        pr, po = RequisitionConversionService.convert(pk, request.user)
        return Response({
            'message': f'Requisition converted to purchase order {po.po_number}.',
            'requisition': self._build_requisition_response(pr),
            'purchase_order': PurchaseOrderSerializer(po, context=self.get_serializer_context()).data,
        }, status=status.HTTP_201_CREATED)
    
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
        uploaded = serializer._upload_attachments(pr, files)
        
        return Response({
            'message': f'{len(uploaded)} file(s) uploaded successfully',
            'attachments': pr.attachments
        })

    @action(detail=True, methods=['get'])
    def export_pdf(self, request, pk=None):
        """Export approved requisition as an industry-standard PDF document download."""
        pr = self.get_object()

        if canonicalize_pr_status(pr.status) != 'approved':
            return Response(
                {'error': 'Only approved requisitions can be exported as PDF.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        safe_pr_number = (pr.pr_number or str(pr.id)).replace('/', '-').replace(' ', '_')
        filename = f"{safe_pr_number}_Approved.pdf"

        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        story = []
        styles = getSampleStyleSheet()

        # Brand palette
        PRIMARY_BLUE = colors.HexColor('#2563EB')
        SLATE_DARK = colors.HexColor('#0F172A')
        TEXT_MUTED = colors.HexColor('#64748B')
        BG_LIGHT = colors.HexColor('#F8FAFC')
        BORDER_COLOR = colors.HexColor('#E2E8F0')
        APPROVED_GREEN = colors.HexColor('#16A34A')

        # Typography
        style_company = ParagraphStyle('CompanyTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=PRIMARY_BLUE)
        style_doc_title = ParagraphStyle('DocTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=14, leading=18, textColor=SLATE_DARK, alignment=2)
        style_badge = ParagraphStyle('Badge', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, leading=12, textColor=APPROVED_GREEN, alignment=2)
        style_label = ParagraphStyle('Label', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=TEXT_MUTED)
        style_value = ParagraphStyle('Value', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=12, textColor=SLATE_DARK)
        style_table_header = ParagraphStyle('TableHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.white)
        style_table_cell = ParagraphStyle('TableCell', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=11, textColor=SLATE_DARK)

        # Header section
        header_data = [
            [
                Paragraph('<b>RADAI PROCUREMENT</b><br/><font size="8" color="#64748B">Oil &amp; Gas Standard Compliance</font>', style_company),
                [
                    Paragraph('<b>PURCHASE REQUISITION</b>', style_doc_title),
                    Paragraph('STATUS: <b>APPROVED</b>', style_badge),
                ],
            ]
        ]
        header_table = Table(header_data, colWidths=[3.25 * inch, 4.25 * inch])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 10))
        story.append(HRFlowable(width='100%', thickness=1.5, color=PRIMARY_BLUE, spaceBefore=0, spaceAfter=12))

        requester_name = pr.issued_by.get_full_name() if pr.issued_by else 'N/A'
        project_reference = pr.project or pr.project_department or 'N/A'
        department_name = pr.department or pr.project_department or 'N/A'

        # Metadata grid
        meta_data = [
            [
                Paragraph('REQUISITION DETAILS', style_label),
                Paragraph('PROJECT &amp; VENDOR INFO', style_label),
            ],
            [
                Paragraph(
                    f'<b>PR Number:</b> {pr.pr_number or "N/A"}<br/>'
                    f'<b>Date Issued:</b> {pr.issued_date or "N/A"}<br/>'
                    f'<b>Issued By:</b> {requester_name}<br/>'
                    f'<b>Department:</b> {department_name}',
                    style_value,
                ),
                Paragraph(
                    f'<b>Project Reference:</b> {project_reference}<br/>'
                    f'<b>Supplier/Vendor:</b> {pr.supplier_name or "RAD Internal"}<br/>'
                    f'<b>Business ID / License:</b> {pr.supplier_business_id or "N/A"}',
                    style_value,
                ),
            ],
        ]
        meta_table = Table(meta_data, colWidths=[3.75 * inch, 3.75 * inch])
        meta_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BG_LIGHT),
            ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 14))

        # Line items table
        items_data = [[
            Paragraph('Item', style_table_header),
            Paragraph('Description', style_table_header),
            Paragraph('API / ASME Standard', style_table_header),
            Paragraph('Qty', style_table_header),
            Paragraph('UOM', style_table_header),
            Paragraph('Unit Price', style_table_header),
            Paragraph('Discount', style_table_header),
            Paragraph('Line Total', style_table_header),
        ]]

        raw_items = pr.items if isinstance(pr.items, list) else []

        if not raw_items and pr.product_service:
            raw_items = [{
                'description': pr.product_service,
                'tag': 'Standard',
                'qty': 1,
                'uom': 'LOT',
                'unit_price': float(pr.total_price or 0),
                'discount': 0,
                'total': float(pr.total_price or 0),
            }]

        def _to_float(value, default=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        normalized_items = []
        for item in raw_items:
            description = item.get('description') or item.get('desc') or item.get('item') or 'N/A'
            standards = item.get('tag') or item.get('standard') or item.get('standards') or item.get('api_asme_standard')
            if isinstance(standards, list):
                standards = ', '.join([str(s) for s in standards if s])
            standards = standards or 'Standard'

            qty = _to_float(item.get('quantity', item.get('qty', 1)), 1)
            uom = item.get('uom') or item.get('unit_of_measure') or item.get('unit') or 'EA'
            unit_price = _to_float(item.get('unit_price', item.get('price', 0)), 0)
            discount = _to_float(item.get('discount', item.get('line_discount', 0)), 0)
            line_total = _to_float(item.get('line_total', item.get('total', (qty * unit_price) - discount)), (qty * unit_price) - discount)

            normalized_items.append({
                'description': description,
                'standards': standards,
                'qty': qty,
                'uom': uom,
                'unit_price': unit_price,
                'discount': discount,
                'line_total': line_total,
            })

        for idx, item in enumerate(normalized_items, start=1):
            items_data.append([
                Paragraph(str(idx), style_table_cell),
                Paragraph(item['description'], style_table_cell),
                Paragraph(f"<font color='#2563EB'><b>{item['standards']}</b></font>", style_table_cell),
                Paragraph(f"{item['qty']:,.2f}".rstrip('0').rstrip('.'), style_table_cell),
                Paragraph(str(item['uom']), style_table_cell),
                Paragraph(f"{item['unit_price']:,.2f}", style_table_cell),
                Paragraph(f"{item['discount']:,.2f}", style_table_cell),
                Paragraph(f"<b>{item['line_total']:,.2f}</b>", style_table_cell),
            ])

        items_table = Table(items_data, colWidths=[0.35 * inch, 2.15 * inch, 1.6 * inch, 0.5 * inch, 0.5 * inch, 0.85 * inch, 0.8 * inch, 0.9 * inch])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), PRIMARY_BLUE),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, BG_LIGHT]),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(items_table)
        story.append(Spacer(1, 12))

        subtotal = sum(item['line_total'] for item in normalized_items)
        vat_value = _to_float(pr.total_price, subtotal) - _to_float(pr.net_total_excl_vat, subtotal)
        if vat_value == 0:
            vat_value = subtotal * 0.05
        grand_total = _to_float(pr.total_price, subtotal + vat_value)
        currency = pr.currency or 'AED'

        totals_data = [
            [Paragraph('Subtotal (excl. VAT):', style_label), Paragraph(f"{subtotal:,.2f} {currency}", style_value)],
            [Paragraph('VAT:', style_label), Paragraph(f"{vat_value:,.2f} {currency}", style_value)],
            [Paragraph('<b>Grand Total:</b>', style_label), Paragraph(f"<b>{grand_total:,.2f} {currency}</b>", style_value)],
        ]
        totals_table = Table(totals_data, colWidths=[2.0 * inch, 1.5 * inch])
        totals_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
            ('LINEABOVE', (0, 2), (-1, 2), 1, PRIMARY_BLUE),
            ('PADDING', (0, 0), (-1, -1), 4),
        ]))

        story.append(Table([["", totals_table]], colWidths=[3.9 * inch, 3.6 * inch]))
        story.append(Spacer(1, 12))

        # Approval block
        pm_name = pr.pm_name.get_full_name() if pr.pm_name else 'N/A'
        vp_name = pr.vp_op_name.get_full_name() if pr.vp_op_name else 'N/A'
        pm_date = pr.pm_approved_at.strftime('%Y-%m-%d %H:%M UTC') if pr.pm_approved_at else 'N/A'
        vp_date = pr.vp_op_approved_at.strftime('%Y-%m-%d %H:%M UTC') if pr.vp_op_approved_at else 'N/A'

        approval_data = [
            [
                Paragraph('<b>PROJECT MANAGER SIGN-OFF</b>', style_label),
                Paragraph('<b>EXECUTIVE / VP SIGN-OFF</b>', style_label),
            ],
            [
                Paragraph(
                    f'<b>Approver:</b> {pm_name}<br/>'
                    f'<b>Status:</b> <font color="#16A34A"><b>{pr.pm_approval_status.upper()}</b></font><br/>'
                    f'<b>Date:</b> {pm_date}<br/>'
                    f'<i>Comments: Technical and standards verification completed.</i>',
                    style_value,
                ),
                Paragraph(
                    f'<b>Approver:</b> {vp_name}<br/>'
                    f'<b>Status:</b> <font color="#16A34A"><b>{pr.vp_op_approval_status.upper()}</b></font><br/>'
                    f'<b>Date:</b> {vp_date}<br/>'
                    f'<i>Comments: Commercial terms and budget approved.</i>',
                    style_value,
                ),
            ],
        ]
        approval_table = Table(approval_data, colWidths=[3.75 * inch, 3.75 * inch])
        approval_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F1F5F9')),
            ('BOX', (0, 0), (-1, -1), 1, BORDER_COLOR),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ('PADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(approval_table)

        doc.build(story)
        buffer.seek(0)
        response.write(buffer.read())
        return response
    
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """Return counts for the canonical PR lifecycle."""
        queryset = self.get_queryset()

        totals = {
            lifecycle_status: queryset.filter(
                status__in=stored_values_for(lifecycle_status)
            ).count()
            for lifecycle_status in (
                'draft', 'submitted', 'in_review', 'approved',
                'rejected', 'cancelled', 'converted',
            )
        }
        totals['total'] = queryset.count()
        totals['pending'] = totals['submitted'] + totals['in_review']
        
        by_priority = queryset.values('priority').annotate(
            count=Count('id')
        )
        
        recent = queryset[:10]
        recent_serializer = self.get_serializer(recent, many=True)
        
        return Response({
            'totals': {
                **totals,
            },
            'by_priority': list(by_priority),
            'recent': recent_serializer.data
        })
    
    # Legacy approve/reject endpoints (kept for backward compatibility)
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Legacy endpoint: approve the current configured stage."""
        pr = RequisitionWorkflowService.approve(
            pk,
            request.user,
            signature=request.data.get('signature', ''),
        )
        return Response(self._build_requisition_response(pr))
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Legacy endpoint: reject the current configured stage."""
        pr = RequisitionWorkflowService.reject(
            pk,
            request.user,
            request.data.get('reason', ''),
        )
        return Response(self._build_requisition_response(pr))


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Purchase Order management
    
    ≡ƒöÉ SECURITY: Requires 'procurement_orders' module access (soft-coded from rbac_config.py)
    """
    
    queryset = PurchaseOrder.objects.all().select_related('vendor', 'pr_reference').order_by('-created_at')
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

        verified, message = PurchaseOrderNumberService.verify(
            po.po_number,
            po.pr_reference.pr_number if po.pr_reference_id else None,
        )
        if not verified:
            return Response(
                {'error': f'PO number verification failed: {message}'},
                status=status.HTTP_400_BAD_REQUEST,
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
    
    ≡ƒöÉ SECURITY: Requires 'procurement_receipts' module access (soft-coded from rbac_config.py)
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
    ViewSet for PODocument ΓÇö read-only list/detail plus the AI extraction action.
    The `extract_from_pdf` action is the primary entry point: it accepts a PDF
    upload, stores it in S3, runs the AI extractor, and returns the result.
    
    ≡ƒöÉ SECURITY: Requires 'procurement_orders' module access (soft-coded from rbac_config.py)
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
            file  ΓÇö PDF file (required)

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
    
    ≡ƒöÉ SECURITY: Requires 'procurement' module access (soft-coded from rbac_config.py)
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
    
    ≡ƒöÉ SECURITY: Requires 'procurement' module access (soft-coded from rbac_config.py)
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
    
    ≡ƒöÉ SECURITY: Requires 'procurement' module access (soft-coded from rbac_config.py)
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
