"""
PFD Converter Views
"""
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
import os

from .models import PFDDocument, PIDConversion, ConversionFeedback
from .serializers import (
    PFDDocumentSerializer, PIDConversionSerializer,
    ConversionFeedbackSerializer, PFDUploadSerializer,
    ConversionRequestSerializer
)
from .services import PFDToPIDConverter
from apps.rbac.permissions import HasModuleAccess


class PFDDocumentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for PFD document management
    """
    queryset = PFDDocument.objects.all()
    serializer_class = PFDDocumentSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'pfd_to_pid'
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['document_number', 'document_title', 'project_name']
    ordering_fields = ['created_at', 'status', 'document_number']
    filterset_fields = ['status', 'project_code']
    
    def get_queryset(self):
        """Filter documents by user"""
        user = self.request.user
        
        # Super admin sees all
        if hasattr(user, 'rbac_profile'):
            if user.rbac_profile.roles.filter(code='super_admin', is_active=True).exists():
                return PFDDocument.objects.all()
        
        # Others see only their documents
        return PFDDocument.objects.filter(uploaded_by=user)
    
    @action(detail=False, methods=['post'])
    def upload(self, request):
        """
        Upload PFD document and extract process data
        
        POST /api/v1/pfd/documents/upload/
        """
        serializer = PFDUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            file = serializer.validated_data['file']
            
            # Create PFD document
            pfd_doc = PFDDocument.objects.create(
                uploaded_by=request.user,
                file=file,
                file_name=file.name,
                file_size=file.size,
                file_type=file.content_type,
                document_title=serializer.validated_data.get('document_title', ''),
                document_number=serializer.validated_data.get('document_number', ''),
                revision=serializer.validated_data.get('revision', ''),
                project_name=serializer.validated_data.get('project_name', ''),
                project_code=serializer.validated_data.get('project_code', ''),
                status='processing'
            )
            
            # Extract PFD data using AI
            pfd_doc.processing_started_at = timezone.now()
            pfd_doc.save()
            
            try:
                converter = PFDToPIDConverter()
                extracted_data = converter.extract_pfd_data(file)
                
                pfd_doc.extracted_data = extracted_data
                pfd_doc.status = 'converted'
                pfd_doc.processing_completed_at = timezone.now()
                pfd_doc.processing_duration = (
                    pfd_doc.processing_completed_at - pfd_doc.processing_started_at
                ).total_seconds()
                pfd_doc.save()
                
            except Exception as e:
                pfd_doc.status = 'failed'
                pfd_doc.error_message = str(e)
                pfd_doc.processing_completed_at = timezone.now()
                pfd_doc.processing_duration = (
                    pfd_doc.processing_completed_at - pfd_doc.processing_started_at
                ).total_seconds()
                pfd_doc.save()
                
                return Response(
                    {
                        'error': 'PFD extraction failed',
                        'detail': str(e),
                        'document_id': str(pfd_doc.id)
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            return Response(
                PFDDocumentSerializer(pfd_doc).data,
                status=status.HTTP_201_CREATED
            )
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class PIDConversionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for P&ID conversion management
    """
    queryset = PIDConversion.objects.all()
    serializer_class = PIDConversionSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'pfd_to_pid'
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['pid_drawing_number', 'pid_title']
    ordering_fields = ['created_at', 'status', 'confidence_score']
    filterset_fields = ['status', 'pfd_document']
    
    def get_queryset(self):
        """Filter conversions by user"""
        user = self.request.user
        
        # Super admin sees all
        if hasattr(user, 'rbac_profile'):
            if user.rbac_profile.roles.filter(code='super_admin', is_active=True).exists():
                return PIDConversion.objects.all()
        
        # Others see only their conversions
        return PIDConversion.objects.filter(converted_by=user)
    
    @action(detail=False, methods=['post'])
    def generate(self, request):
        """
        Generate P&ID from PFD document
        
        POST /api/v1/pfd/conversions/generate/
        """
        serializer = ConversionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Get PFD document
            pfd_doc = PFDDocument.objects.get(
                id=serializer.validated_data['pfd_document_id']
            )
            
            # Check if user has access
            if pfd_doc.uploaded_by != request.user:
                if not hasattr(request.user, 'rbac_profile') or \
                   not request.user.rbac_profile.roles.filter(code='super_admin').exists():
                    return Response(
                        {'error': 'Access denied'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # Create conversion record
            conversion = PIDConversion.objects.create(
                pfd_document=pfd_doc,
                converted_by=request.user,
                pid_drawing_number=serializer.validated_data['pid_drawing_number'],
                pid_title=serializer.validated_data['pid_title'],
                pid_revision=serializer.validated_data['pid_revision'],
                status='generating'
            )
            
            # Generate P&ID specifications
            try:
                converter = PFDToPIDConverter()
                
                # Generate specifications
                pid_specs = converter.generate_pid_specifications(pfd_doc.extracted_data)
                
                # Validate conversion
                validation = converter.validate_conversion(pid_specs, pfd_doc.extracted_data)
                
                # Update conversion
                conversion.equipment_list = pid_specs.get('equipment_list', [])
                conversion.instrument_list = pid_specs.get('instrumentation_list', [])
                conversion.piping_details = pid_specs.get('piping_specifications', [])
                conversion.safety_systems = pid_specs.get('safety_devices', [])
                conversion.design_parameters = pid_specs.get('design_basis', {})
                conversion.compliance_checks = validation
                conversion.confidence_score = validation.get('compliance_score', 0)
                conversion.status = 'completed'
                conversion.generation_completed_at = timezone.now()
                conversion.generation_duration = (
                    conversion.generation_completed_at - conversion.generation_started_at
                ).total_seconds()
                conversion.save()
                
            except Exception as e:
                conversion.status = 'failed'
                conversion.generation_completed_at = timezone.now()
                conversion.generation_duration = (
                    conversion.generation_completed_at - conversion.generation_started_at
                ).total_seconds()
                conversion.save()
                
                return Response(
                    {
                        'error': 'P&ID generation failed',
                        'detail': str(e),
                        'conversion_id': str(conversion.id)
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            return Response(
                PIDConversionSerializer(conversion).data,
                status=status.HTTP_201_CREATED
            )
            
        except PFDDocument.DoesNotExist:
            return Response(
                {'error': 'PFD document not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve P&ID conversion"""
        conversion = self.get_object()
        conversion.reviewed_by = request.user
        conversion.reviewed_at = timezone.now()
        conversion.review_notes = request.data.get('review_notes', '')
        conversion.status = 'approved'
        conversion.save()
        
        return Response(PIDConversionSerializer(conversion).data)


class ConversionFeedbackViewSet(viewsets.ModelViewSet):
    """
    ViewSet for conversion feedback
    """
    queryset = ConversionFeedback.objects.all()
    serializer_class = ConversionFeedbackSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['conversion', 'rating']
    ordering_fields = ['created_at', 'rating']
    
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
