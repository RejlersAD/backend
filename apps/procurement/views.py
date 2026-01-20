"""
Procurement Management Views
API endpoints for procurement workflows
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Count, Sum, Avg
from django.utils import timezone
from datetime import timedelta

from .models import Vendor, PurchaseRequisition, PurchaseOrder, Receipt, PROCUREMENT_CATEGORIES
from .serializers import (
    VendorSerializer,
    PurchaseRequisitionSerializer,
    PurchaseOrderSerializer,
    ReceiptSerializer,
    ProcurementCategorySerializer
)


class VendorViewSet(viewsets.ModelViewSet):
    """ViewSet for Vendor management"""
    
    queryset = Vendor.objects.all().order_by('-created_at')
    serializer_class = VendorSerializer
    permission_classes = [IsAuthenticated]
    
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
    """ViewSet for Purchase Requisition management"""
    
    queryset = PurchaseRequisition.objects.all().order_by('-created_at')
    serializer_class = PurchaseRequisitionSerializer
    permission_classes = [IsAuthenticated]
    
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
        
        # Search
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(pr_number__icontains=search) |
                Q(title__icontains=search) |
                Q(description__icontains=search)
            )
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a purchase requisition"""
        pr = self.get_object()
        
        if pr.status != 'submitted':
            return Response(
                {'error': 'Only submitted requisitions can be approved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        pr.status = 'approved'
        pr.approved_by = request.user
        pr.approved_at = timezone.now()
        pr.save()
        
        serializer = self.get_serializer(pr)
        return Response(serializer.data)
    
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
    """ViewSet for Purchase Order management"""
    
    queryset = PurchaseOrder.objects.all().select_related('vendor').order_by('-created_at')
    serializer_class = PurchaseOrderSerializer
    permission_classes = [IsAuthenticated]
    
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
    """ViewSet for Goods Receipt management"""
    
    queryset = Receipt.objects.all().select_related('purchase_order').order_by('-created_at')
    serializer_class = ReceiptSerializer
    permission_classes = [IsAuthenticated]
    
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
