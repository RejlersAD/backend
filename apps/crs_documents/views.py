"""
CRS Documents API Views
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import CRSDocument, CRSDocumentVersion
from .serializers import CRSDocumentSerializer, CRSDocumentVersionSerializer


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
