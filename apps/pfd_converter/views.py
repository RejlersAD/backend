"""
PFD Converter Views
"""
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
import os
import logging

logger = logging.getLogger(__name__)

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
    permission_classes = [IsAuthenticated]
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
                
                # Open the saved file for extraction (file pointer is at end after save)
                pfd_doc.file.open('rb')
                extracted_data = converter.extract_pfd_data(pfd_doc.file)
                pfd_doc.file.close()
                
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
    permission_classes = [IsAuthenticated]
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
                
                # 🎨 Generate actual P&ID drawing PDF
                try:
                    logger.info("🎨 Generating P&ID visual drawing...")
                    drawing_path = converter.generate_pid_drawing(
                        pfd_data=pfd_doc.extracted_data,
                        pid_specs={
                            'pid_drawing_number': conversion.pid_drawing_number,
                            'pid_title': conversion.pid_title,
                            'pid_revision': conversion.pid_revision,
                            'equipment_list': conversion.equipment_list,
                            'instrument_list': conversion.instrument_list,
                            'piping_specifications': conversion.piping_details,
                            'safety_devices': conversion.safety_systems,
                        }
                    )
                    
                    # Save drawing path to conversion record
                    relative_path = drawing_path.replace(str(settings.MEDIA_ROOT), '').lstrip('/\\')
                    conversion.pid_file = relative_path
                    logger.info(f"✅ P&ID drawing saved: {relative_path}")
                    
                except Exception as draw_error:
                    logger.warning(f"⚠️ P&ID drawing generation failed: {str(draw_error)}")
                    # Continue without drawing - specs are still valid
                
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
    
    @action(detail=True, methods=['get'])
    def download_drawing(self, request, pk=None):
        """
        Download P&ID drawing PDF
        
        GET /api/v1/pfd/conversions/{id}/download_drawing/
        """
        try:
            conversion = self.get_object()
            
            logger.info(f"Download request for conversion {pk}, pid_file: {conversion.pid_file}")
            
            if not conversion.pid_file:
                logger.warning(f"P&ID drawing not available for conversion {pk}")
                
                # Check if OpenAI API key is configured
                from decouple import config
                api_key = config('OPENAI_API_KEY', default='')
                
                if not api_key or api_key == '' or api_key.startswith('your-'):
                    error_msg = (
                        "P&ID drawing generation requires OpenAI API configuration. "
                        "Please configure OPENAI_API_KEY in your environment variables or .env file. "
                        "The system supports DALL-E 3 (HD quality) and DALL-E 2 (fallback) for AI-generated drawings."
                    )
                else:
                    error_msg = (
                        "P&ID drawing not generated yet. The generation may have failed or is still in progress. "
                        "Please check the conversion status or try regenerating the drawing."
                    )
                
                return Response(
                    {'error': error_msg},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Build full path
            drawing_path = os.path.join(settings.MEDIA_ROOT, str(conversion.pid_file))
            logger.info(f"Attempting to serve file from: {drawing_path}")
            
            if not os.path.exists(drawing_path):
                logger.error(f"Drawing file not found at path: {drawing_path}")
                return Response(
                    {'error': f'Drawing file not found on server. Path checked: {conversion.pid_file}'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Serve file
            from django.http import FileResponse
            response = FileResponse(open(drawing_path, 'rb'), content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{conversion.pid_drawing_number}.pdf"'
            logger.info(f"Successfully serving file: {drawing_path}")
            return response
            
        except Exception as e:
            logger.error(f"Error in download_drawing: {str(e)}", exc_info=True)
            return Response(
                {'error': f'Failed to download drawing: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
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
