"""
Onboarding & Offboarding Views
Provides REST API endpoints for managing employee lifecycle
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Count, Prefetch
from django.utils import timezone
from datetime import date

from .models import (
    OnboardingRecord, OffboardingRecord, Equipment,
    Document, AccessProvisioning, Checklist
)
from .serializers import (
    OnboardingRecordSerializer, OnboardingRecordListSerializer,
    OffboardingRecordSerializer, OffboardingRecordListSerializer,
    EquipmentSerializer, DocumentSerializer,
    AccessProvisioningSerializer, ChecklistSerializer
)


class OnboardingRecordViewSet(viewsets.ModelViewSet):
    """
    API endpoint for onboarding records
    Supports CRUD + custom actions: statistics, mark_completed
    """
    permission_classes = [IsAuthenticated]
    queryset = OnboardingRecord.objects.all()
    
    def get_serializer_class(self):
        """Use lightweight serializer for list, full serializer for detail"""
        if self.action == 'list':
            return OnboardingRecordListSerializer
        return OnboardingRecordSerializer
    
    def get_queryset(self):
        """
        Filter by status, branch, department, search query
        Annotate with counts for list view
        """
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by branch
        branch = self.request.query_params.get('branch')
        if branch:
            queryset = queryset.filter(branch=branch)
        
        # Filter by department
        department = self.request.query_params.get('department')
        if department:
            queryset = queryset.filter(department__icontains=department)
        
        # Search by employee name or email
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(employee_name__icontains=search) |
                Q(employee_email__icontains=search) |
                Q(employee_id__icontains=search)
            )
        
        # Annotate counts for list view
        if self.action == 'list':
            queryset = queryset.annotate(
                equipment_count=Count('equipment', distinct=True),
                documents_count=Count('documents', distinct=True),
                access_count=Count('access_records', distinct=True),
                checklist_count=Count('checklist_items', distinct=True),
                checklist_completed_count=Count('checklist_items', filter=Q(checklist_items__completed=True), distinct=True)
            )
        else:
            # Prefetch related for detail view
            queryset = queryset.prefetch_related(
                'equipment', 'documents', 'access_records', 'checklist_items'
            )
        
        return queryset.select_related('created_by', 'assigned_to', 'user')
    
    def perform_create(self, serializer):
        """Set created_by to current user"""
        serializer.save(created_by=self.request.user)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """
        Get onboarding statistics
        Returns counts by status, upcoming joiners, overdue, etc.
        """
        queryset = self.get_queryset()
        
        total = queryset.count()
        by_status = {}
        for record in queryset.values('status').annotate(count=Count('id')):
            by_status[record['status']] = record['count']
        
        # Upcoming joiners (next 30 days)
        from datetime import timedelta
        upcoming_threshold = date.today() + timedelta(days=30)
        upcoming = queryset.filter(
            joining_date__gte=date.today(),
            joining_date__lte=upcoming_threshold,
            status__in=['initiated', 'documentation', 'equipment', 'access_provisioning', 'training']
        ).count()
        
        # Overdue (joining date passed but not completed)
        overdue = queryset.filter(
            joining_date__lt=date.today(),
            status__in=['initiated', 'documentation', 'equipment', 'access_provisioning', 'training']
        ).count()
        
        # Completed this month
        now = timezone.now()
        completed_this_month = queryset.filter(
            status='completed',
            actual_completion_date__year=now.year,
            actual_completion_date__month=now.month
        ).count()
        
        # By branch
        by_branch = {}
        for record in queryset.values('branch').annotate(count=Count('id')):
            by_branch[record['branch']] = record['count']
        
        return Response({
            'total': total,
            'by_status': by_status,
            'upcoming_joiners': upcoming,
            'overdue': overdue,
            'completed_this_month': completed_this_month,
            'by_branch': by_branch
        })
    
    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Mark onboarding as completed"""
        record = self.get_object()
        record.status = 'completed'
        record.progress_percentage = 100
        record.actual_completion_date = timezone.now()
        record.save()
        
        serializer = self.get_serializer(record)
        return Response(serializer.data)


class OffboardingRecordViewSet(viewsets.ModelViewSet):
    """
    API endpoint for offboarding records
    Supports CRUD + custom actions: statistics, mark_completed
    """
    permission_classes = [IsAuthenticated]
    queryset = OffboardingRecord.objects.all()
    
    def get_serializer_class(self):
        """Use lightweight serializer for list, full serializer for detail"""
        if self.action == 'list':
            return OffboardingRecordListSerializer
        return OffboardingRecordSerializer
    
    def get_queryset(self):
        """
        Filter by status, branch, department, exit_reason, search query
        Annotate with counts for list view
        """
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by branch
        branch = self.request.query_params.get('branch')
        if branch:
            queryset = queryset.filter(branch=branch)
        
        # Filter by department
        department = self.request.query_params.get('department')
        if department:
            queryset = queryset.filter(department__icontains=department)
        
        # Filter by exit reason
        exit_reason = self.request.query_params.get('exit_reason')
        if exit_reason:
            queryset = queryset.filter(exit_reason=exit_reason)
        
        # Search by employee name or email
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(employee_name__icontains=search) |
                Q(employee_email__icontains=search) |
                Q(employee_id__icontains=search)
            )
        
        # Annotate counts for list view
        if self.action == 'list':
            queryset = queryset.annotate(
                equipment_count=Count('equipment', distinct=True),
                documents_count=Count('documents', distinct=True),
                access_count=Count('access_records', distinct=True),
                checklist_count=Count('checklist_items', distinct=True),
                checklist_completed_count=Count('checklist_items', filter=Q(checklist_items__completed=True), distinct=True)
            )
        else:
            # Prefetch related for detail view
            queryset = queryset.prefetch_related(
                'equipment', 'documents', 'access_records', 'checklist_items'
            )
        
        return queryset.select_related('created_by', 'assigned_to', 'user')
    
    def perform_create(self, serializer):
        """Set created_by to current user"""
        serializer.save(created_by=self.request.user)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """
        Get offboarding statistics
        Returns counts by status, upcoming exits, exit reasons, etc.
        """
        queryset = self.get_queryset()
        
        total = queryset.count()
        by_status = {}
        for record in queryset.values('status').annotate(count=Count('id')):
            by_status[record['status']] = record['count']
        
        # Upcoming exits (next 30 days)
        from datetime import timedelta
        upcoming_threshold = date.today() + timedelta(days=30)
        upcoming = queryset.filter(
            last_working_day__gte=date.today(),
            last_working_day__lte=upcoming_threshold,
            status__in=['initiated', 'access_revocation', 'equipment_return', 'exit_interview', 'final_settlement']
        ).count()
        
        # Overdue (last working day passed but not completed)
        overdue = queryset.filter(
            last_working_day__lt=date.today(),
            status__in=['initiated', 'access_revocation', 'equipment_return', 'exit_interview', 'final_settlement']
        ).count()
        
        # Completed this month
        now = timezone.now()
        completed_this_month = queryset.filter(
            status='completed',
            actual_completion_date__year=now.year,
            actual_completion_date__month=now.month
        ).count()
        
        # By exit reason
        by_exit_reason = {}
        for record in queryset.values('exit_reason').annotate(count=Count('id')):
            by_exit_reason[record['exit_reason']] = record['count']
        
        # By branch
        by_branch = {}
        for record in queryset.values('branch').annotate(count=Count('id')):
            by_branch[record['branch']] = record['count']
        
        return Response({
            'total': total,
            'by_status': by_status,
            'upcoming_exits': upcoming,
            'overdue': overdue,
            'completed_this_month': completed_this_month,
            'by_exit_reason': by_exit_reason,
            'by_branch': by_branch
        })
    
    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Mark offboarding as completed"""
        record = self.get_object()
        record.status = 'completed'
        record.progress_percentage = 100
        record.actual_completion_date = timezone.now()
        record.save()
        
        serializer = self.get_serializer(record)
        return Response(serializer.data)


class EquipmentViewSet(viewsets.ModelViewSet):
    """API endpoint for equipment tracking"""
    permission_classes = [IsAuthenticated]
    queryset = Equipment.objects.all()
    serializer_class = EquipmentSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by onboarding record
        onboarding_id = self.request.query_params.get('onboarding_record')
        if onboarding_id:
            queryset = queryset.filter(onboarding_record_id=onboarding_id)
        
        # Filter by offboarding record
        offboarding_id = self.request.query_params.get('offboarding_record')
        if offboarding_id:
            queryset = queryset.filter(offboarding_record_id=offboarding_id)
        
        return queryset


class DocumentViewSet(viewsets.ModelViewSet):
    """API endpoint for document tracking"""
    permission_classes = [IsAuthenticated]
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by onboarding record
        onboarding_id = self.request.query_params.get('onboarding_record')
        if onboarding_id:
            queryset = queryset.filter(onboarding_record_id=onboarding_id)
        
        # Filter by offboarding record
        offboarding_id = self.request.query_params.get('offboarding_record')
        if offboarding_id:
            queryset = queryset.filter(offboarding_record_id=offboarding_id)
        
        return queryset.select_related('verified_by')


class AccessProvisioningViewSet(viewsets.ModelViewSet):
    """API endpoint for access provisioning tracking"""
    permission_classes = [IsAuthenticated]
    queryset = AccessProvisioning.objects.all()
    serializer_class = AccessProvisioningSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by onboarding record
        onboarding_id = self.request.query_params.get('onboarding_record')
        if onboarding_id:
            queryset = queryset.filter(onboarding_record_id=onboarding_id)
        
        # Filter by offboarding record
        offboarding_id = self.request.query_params.get('offboarding_record')
        if offboarding_id:
            queryset = queryset.filter(offboarding_record_id=offboarding_id)
        
        return queryset.select_related('assigned_by')


class ChecklistViewSet(viewsets.ModelViewSet):
    """API endpoint for checklist items"""
    permission_classes = [IsAuthenticated]
    queryset = Checklist.objects.all()
    serializer_class = ChecklistSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by onboarding record
        onboarding_id = self.request.query_params.get('onboarding_record')
        if onboarding_id:
            queryset = queryset.filter(onboarding_record_id=onboarding_id)
        
        # Filter by offboarding record
        offboarding_id = self.request.query_params.get('offboarding_record')
        if offboarding_id:
            queryset = queryset.filter(offboarding_record_id=offboarding_id)
        
        return queryset.select_related('completed_by')
