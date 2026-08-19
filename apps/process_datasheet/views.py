"""
Process Datasheet Views
API endpoints for datasheet management
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db.models import Q, Count, Avg
from django.utils import timezone
from django.http import HttpResponse
import logging

from .models import (
    EquipmentType,
    ProcessDatasheet,
    DatasheetTemplate,
    DatasheetValidationRule,
    DatasheetExtractionJob,
    PumpCalculationData
)
from .services import CalculationService, ValidationService
from .services.datasheet_generator import AIDatasheetGenerator
from .services.pump_datasheet_generator import PumpDataSheetGenerator
from .serializers import (
    EquipmentTypeSerializer,
    EquipmentTypeListSerializer,
    ProcessDatasheetSerializer,
    ProcessDatasheetListSerializer,
    ProcessDatasheetCreateSerializer,
    DatasheetTemplateSerializer,
    DatasheetValidationRuleSerializer,
    DatasheetExtractionJobSerializer,
    PumpCalculationDataSerializer,
    PumpCalculationDataListSerializer,
    PumpCalculationDataCreateSerializer
)
from .equipment_configs import get_equipment_config, list_equipment_types
from .tasks import extract_datasheet_from_pdf

logger = logging.getLogger(__name__)


class EquipmentTypeViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Equipment Types
    """
    
    permission_classes = [IsAuthenticated]
    queryset = EquipmentType.objects.all()
    
    def get_serializer_class(self):
        if self.action == 'list':
            return EquipmentTypeListSerializer
        return EquipmentTypeSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by category
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def available(self, request):
        """Get list of available equipment types from configuration"""
        types = list_equipment_types()
        return Response(types)
    
    @action(detail=True, methods=['get'])
    def configuration(self, request, pk=None):
        """Get complete configuration for an equipment type"""
        equipment_type = self.get_object()
        return Response(equipment_type.configuration)
    
    @action(detail=True, methods=['get'])
    def fields(self, request, pk=None):
        """Get field definitions for an equipment type"""
        equipment_type = self.get_object()
        sections = equipment_type.configuration.get('sections', [])
        
        # Flatten fields from all sections
        all_fields = []
        for section in sections:
            for field in section.get('fields', []):
                field_with_section = field.copy()
                field_with_section['section'] = section['id']
                field_with_section['section_name'] = section['name']
                all_fields.append(field_with_section)
        
        return Response(all_fields)
    
    @action(detail=True, methods=['get'])
    def sections(self, request, pk=None):
        """Get section definitions for an equipment type"""
        equipment_type = self.get_object()
        sections = equipment_type.configuration.get('sections', [])
        return Response(sections)
    
    @action(detail=True, methods=['get'])
    def calculations(self, request, pk=None):
        """Get calculation rules for an equipment type"""
        equipment_type = self.get_object()
        calculations = equipment_type.configuration.get('calculations', [])
        return Response(calculations)
    
    @action(detail=True, methods=['get'])
    def validations(self, request, pk=None):
        """Get validation rules for an equipment type"""
        equipment_type = self.get_object()
        validations = equipment_type.configuration.get('validationRules', [])
        return Response(validations)
    
    @action(detail=True, methods=['get'])
    def validation_rules(self, request, pk=None):
        """Get validation rules for an equipment type (legacy endpoint)"""
        equipment_type = self.get_object()
        rules = equipment_type.get_validation_rules()
        return Response(rules)


class ProcessDatasheetViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Process Datasheets
    """
    
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return ProcessDatasheetListSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return ProcessDatasheetCreateSerializer
        return ProcessDatasheetSerializer
    
    def get_queryset(self):
        queryset = ProcessDatasheet.objects.select_related(
            'equipment_type', 'prepared_by', 'checked_by', 'approved_by'
        ).prefetch_related('revisions')
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by equipment type
        equipment_type = self.request.query_params.get('equipment_type')
        if equipment_type:
            queryset = queryset.filter(equipment_type_id=equipment_type)
        
        # Filter by project
        project = self.request.query_params.get('project')
        if project:
            queryset = queryset.filter(project_number=project)
        
        # Search
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(document_number__icontains=search) |
                Q(tag_number__icontains=search) |
                Q(title__icontains=search) |
                Q(service_description__icontains=search)
            )
        
        return queryset
    
    def perform_create(self, serializer):
        """Set prepared_by when creating datasheet"""
        serializer.save(prepared_by=self.request.user)
        logger.info(f"Datasheet created: {serializer.instance.document_number} by {self.request.user.email}")
    
    @action(detail=True, methods=['post'])
    def validate(self, request, pk=None):
        """Validate datasheet against rules"""
        datasheet = self.get_object()
        equipment_config = datasheet.equipment_type.configuration
        
        # Get custom validation rules if any
        custom_rules_qs = DatasheetValidationRule.objects.filter(
            Q(equipment_type=datasheet.equipment_type) | Q(equipment_type__isnull=True),
            is_active=True
        )
        custom_rules = [{
            'id': rule.rule_id,
            'check': rule.condition,
            'message': rule.description,
            'severity': rule.severity
        } for rule in custom_rules_qs]
        
        # Run validation
        try:
            validation_results = ValidationService.validate_all(
                datasheet.datasheet_data,
                equipment_config,
                custom_rules
            )
            
            # Check completeness
            completeness = ValidationService.validate_completeness(
                datasheet.datasheet_data,
                equipment_config
            )
            validation_results['completeness'] = completeness
            
            # Check consistency
            consistency_issues = ValidationService.validate_consistency(
                datasheet.datasheet_data,
                equipment_config
            )
            if consistency_issues:
                validation_results['consistency_issues'] = consistency_issues
            
            # Update datasheet
            datasheet.validation_status = 'valid' if validation_results['valid'] else 'invalid'
            datasheet.validation_results = validation_results
            datasheet.validation_score = validation_results['score']
            datasheet.save()
            
            logger.info(f"Datasheet {datasheet.document_number} validated: Score {validation_results['score']}")
            
            return Response(validation_results)
            
        except Exception as e:
            logger.error(f"Validation error for {datasheet.document_number}: {str(e)}")
            return Response(
                {'error': f'Validation failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def calculate(self, request, pk=None):
        """Run calculations for datasheet"""
        datasheet = self.get_object()
        equipment_config = datasheet.equipment_type.configuration
        
        try:
            # Run all calculations defined in equipment config
            calculated_values = CalculationService.calculate_all(
                datasheet.datasheet_data,
                equipment_config
            )
            
            # Update datasheet with calculated values
            datasheet.calculated_values = calculated_values
            datasheet.save()
            
            # Log successful calculations
            success_count = sum(1 for v in calculated_values.values() if v.get('success'))
            total_count = len(calculated_values)
            logger.info(f"Calculations for {datasheet.document_number}: {success_count}/{total_count} successful")
            
            return Response({
                'success': True,
                'calculated_values': calculated_values,
                'summary': {
                    'total': total_count,
                    'successful': success_count,
                    'failed': total_count - success_count
                }
            })
            
        except Exception as e:
            logger.error(f"Calculation error for {datasheet.document_number}: {str(e)}")
            return Response(
                {'error': f'Calculation failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def extract(self, request):
        """
        Upload PDF and extract datasheet data using AI
        
        POST /api/v1/process-datasheet/datasheets/extract/
        Body (multipart/form-data):
            - pdf_file: PDF file (required)
            - equipment_type: Equipment type ID (required)
        
        Returns: Extraction job details with job_id
        """
        try:
            # Validate required fields
            pdf_file = request.FILES.get('pdf_file')
            equipment_type_id = request.data.get('equipment_type')
            
            if not pdf_file:
                return Response(
                    {'error': 'pdf_file is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not equipment_type_id:
                return Response(
                    {'error': 'equipment_type is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate PDF file
            if not pdf_file.name.lower().endswith('.pdf'):
                return Response(
                    {'error': 'Only PDF files are supported'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate file size (50MB max)
            if pdf_file.size > 50 * 1024 * 1024:
                return Response(
                    {'error': 'File size exceeds 50MB limit'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get equipment type
            try:
                equipment_type = EquipmentType.objects.get(id=equipment_type_id)
            except EquipmentType.DoesNotExist:
                return Response(
                    {'error': 'Invalid equipment_type'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create extraction job
            job = DatasheetExtractionJob.objects.create(
                equipment_type=equipment_type,
                pdf_file=pdf_file,
                created_by=request.user,
                status='pending'
            )
            
            # Trigger background extraction task
            logger.info(f"✅ Created extraction job {job.id} for user {request.user.email}")
            extract_datasheet_from_pdf.delay(
                job_id=str(job.id),
                pdf_path=job.pdf_file.path,
                equipment_type_id=str(equipment_type.id)
            )
            
            return Response({
                'success': True,
                'message': 'PDF uploaded successfully. Extraction in progress.',
                'job_id': str(job.id),
                'status': 'pending',
                'equipment_type': equipment_type.name
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"❌ Extract endpoint error: {str(e)}", exc_info=True)
            return Response(
                {'error': f'Upload failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def add_hold(self, request, pk=None):
        """Add a hold to the datasheet"""
        datasheet = self.get_object()
        
        section = request.data.get('section')
        description = request.data.get('description')
        
        if not section or not description:
            return Response(
                {'error': 'section and description are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        datasheet.add_hold(section, description, request.user)
        
        return Response({'message': 'Hold added successfully'})
    
    @action(detail=True, methods=['post'])
    def add_comment(self, request, pk=None):
        """Add a comment to the datasheet"""
        datasheet = self.get_object()
        
        section = request.data.get('section')
        comment = request.data.get('comment')
        company_response = request.data.get('company_response', '')
        
        if not section or not comment:
            return Response(
                {'error': 'section and comment are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        datasheet.add_comment(section, comment, request.user, company_response)
        
        return Response({'message': 'Comment added successfully'})
    
    @action(detail=True, methods=['post'])
    def create_revision(self, request, pk=None):
        """Create a new revision"""
        datasheet = self.get_object()
        
        description = request.data.get('description', 'Revised datasheet')
        datasheet.increment_revision(request.user, description)
        
        serializer = self.get_serializer(datasheet)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def change_status(self, request, pk=None):
        """Change datasheet status"""
        datasheet = self.get_object()
        
        new_status = request.data.get('status')
        if not new_status:
            return Response(
                {'error': 'status is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        datasheet.status = new_status
        datasheet.save()
        
        logger.info(f"Datasheet {datasheet.document_number} status changed to {new_status}")
        
        return Response({'message': 'Status updated successfully', 'status': new_status})
    
    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """Get statistics for datasheets"""
        queryset = self.get_queryset()
        
        stats = {
            'total': queryset.count(),
            'by_status': {},
            'by_equipment_type': {},
            'average_validation_score': 0.0
        }
        
        # Count by status
        for item in queryset.values('status').annotate(count=Count('status')):
            stats['by_status'][item['status']] = item['count']
        
        # Count by equipment type
        for item in queryset.values('equipment_type__name').annotate(count=Count('equipment_type')):
            stats['by_equipment_type'][item['equipment_type__name']] = item['count']
        
        # Average validation score
        validated = queryset.filter(validation_status='validated')
        if validated.exists():
            stats['average_validation_score'] = validated.aggregate(
                avg_score=Avg('validation_score')
            )['avg_score']
        
        return Response(stats)
    
    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        """Alias for stats endpoint (for backwards compatibility)"""
        return self.stats(request)


class DatasheetTemplateViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Datasheet Templates
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = DatasheetTemplateSerializer
    
    def get_queryset(self):
        queryset = DatasheetTemplate.objects.select_related('equipment_type', 'created_by')
        
        # Show global templates or user's own templates
        queryset = queryset.filter(
            Q(is_global=True) | Q(created_by=self.request.user)
        )
        
        # Filter by equipment type
        equipment_type = self.request.query_params.get('equipment_type')
        if equipment_type:
            queryset = queryset.filter(equipment_type_id=equipment_type)
        
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def use(self, request, pk=None):
        """Use template to create a new datasheet"""
        template = self.get_object()
        template.use_template()
        
        # Create datasheet from template
        datasheet_data = request.data.copy()
        datasheet_data['data'] = template.template_data.copy()
        datasheet_data['equipment_type'] = template.equipment_type.id
        
        serializer = ProcessDatasheetCreateSerializer(
            data=datasheet_data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        datasheet = serializer.save()
        
        return Response(
            ProcessDatasheetSerializer(datasheet).data,
            status=status.HTTP_201_CREATED
        )


class DatasheetValidationRuleViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Validation Rules
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = DatasheetValidationRuleSerializer
    queryset = DatasheetValidationRule.objects.select_related('equipment_type', 'created_by')
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by equipment type
        equipment_type = self.request.query_params.get('equipment_type')
        if equipment_type:
            queryset = queryset.filter(equipment_type_id=equipment_type)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class DatasheetExtractionJobViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Extraction Jobs
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = DatasheetExtractionJobSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    queryset = DatasheetExtractionJob.objects.select_related('datasheet', 'equipment_type', 'created_by')
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by user's jobs
        my_jobs = self.request.query_params.get('my_jobs')
        if my_jobs and my_jobs.lower() == 'true':
            queryset = queryset.filter(created_by=self.request.user)
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset
    
    def perform_create(self, serializer):
        """Create job and trigger Celery task"""
        job = serializer.save(created_by=self.request.user)
        
        # Trigger Celery task
        if job.pdf_file:
            logger.info(f"Triggering extraction task for job {job.id}")
            extract_datasheet_from_pdf.delay(
                job_id=str(job.id),
                pdf_path=job.pdf_file.path,
                equipment_type_id=str(job.equipment_type.id)
            )
        else:
            logger.warning(f"Job {job.id} created without PDF file")
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel an extraction job"""
        job = self.get_object()
        
        if job.status not in ['pending', 'processing']:
            return Response(
                {'error': 'Job cannot be cancelled in current status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        job.status = 'cancelled'
        job.save()
        
        return Response({'message': 'Job cancelled successfully'})
    
    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        """Retry a failed extraction job"""
        job = self.get_object()
        
        if job.status != 'failed':
            return Response(
                {'error': 'Only failed jobs can be retried'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        job.status = 'pending'
        job.retry_count += 1
        job.error_message = ''
        job.save()
        
        # TODO: Trigger background task
        
        return Response({'message': 'Job queued for retry'})


class PumpCalculationDataViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Pump Calculation Data
    """
    
    permission_classes = [IsAuthenticated]
    queryset = PumpCalculationData.objects.all()
    
    def get_serializer_class(self):
        if self.action == 'list':
            return PumpCalculationDataListSerializer
        elif self.action == 'create':
            return PumpCalculationDataCreateSerializer
        return PumpCalculationDataSerializer
    
    def get_queryset(self):
        """Filter pump calculations by query parameters"""
        queryset = PumpCalculationData.objects.all()
        
        # Filter parameters
        project_no = self.request.query_params.get('project_no')
        tag_no = self.request.query_params.get('tag_no')
        status = self.request.query_params.get('status')
        prepared_by = self.request.query_params.get('prepared_by')
        
        if project_no:
            queryset = queryset.filter(project_no__icontains=project_no)
        
        if tag_no:
            queryset = queryset.filter(tag_no__icontains=tag_no)
        
        if status:
            queryset = queryset.filter(status=status)
        
        if prepared_by:
            queryset = queryset.filter(prepared_by__id=prepared_by)
        
        return queryset.select_related('prepared_by', 'checked_by')
    
    def perform_create(self, serializer):
        """Set prepared_by when creating"""
        serializer.save(prepared_by=self.request.user)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get statistics for pump calculations"""
        from django.db.models import Count, Avg
        
        queryset = self.get_queryset()
        
        stats = {
            'total_count': queryset.count(),
            'by_status': dict(queryset.values('status').annotate(count=Count('id')).values_list('status', 'count')),
            'by_project': list(queryset.values('project_no').annotate(count=Count('id')).values('project_no', 'count')[:10]),
            'average_power': queryset.filter(hp__isnull=False).aggregate(avg=Avg('hp'))['avg'],
            'recent_count': queryset.filter(created_at__gte=timezone.now() - timezone.timedelta(days=30)).count(),
        }
        
        return Response(stats)
    
    @action(detail=False, methods=['get'])
    def field_recommendations(self, request):
        """
        Intelligent field recommendations based on historical data
        Provides: recent values, common values, averages, and context-aware suggestions
        Soft-coded for easy expansion to new fields
        """
        from django.db.models import Count, Avg, Max
        from collections import Counter
        
        # Get context filters from query params
        project_no = request.query_params.get('project_no')
        tag_prefix = request.query_params.get('tag_prefix')
        limit = int(request.query_params.get('limit', 5))
        
        # Build base queryset with filters (NO SLICING YET - Django ORM requirement)
        base_queryset = PumpCalculationData.objects.all()
        
        # Apply context filters for smarter recommendations
        if project_no:
            base_queryset = base_queryset.filter(project_no__icontains=project_no)
        
        if tag_prefix:
            base_queryset = base_queryset.filter(tag_no__istartswith=tag_prefix)
        
        # Order by most recent
        base_queryset = base_queryset.order_by('-created_at')
        
        # Limit count for performance (but don't slice yet!)
        max_records = 50 if (project_no or tag_prefix) else 100
        record_count = min(base_queryset.count(), max_records)
        
        # Define fields to analyze (soft-coded for easy expansion)
        TEXT_FIELDS = ['agreement_no', 'project_no', 'revision', 'document_class', 
                       'service', 'motor_classification', 'flow_type', 'destination_description',
                       'type_of_motor', 'cv_ratio_within_range', 'cv_pressure_drop_check']
        
        NUMERIC_FIELDS = ['temperature', 'fluid_viscosity_at_temp', 'hp', 
                         'pump_centerline_elevation', 'density', 'pump_efficiency',
                         'motor_efficiency', 'safety_margin_npsha']
        
        recommendations = {
            'text_fields': {},
            'numeric_fields': {},
            'context': {
                'total_records_analyzed': record_count,
                'project_context': project_no if project_no else 'all_projects',
                'tag_context': tag_prefix if tag_prefix else 'all_tags'
            }
        }
        
        # Analyze text fields - most common and recent values
        for field in TEXT_FIELDS:
            # Query only what we need, limit in the query
            values = list(base_queryset.values_list(field, flat=True).exclude(
                **{f'{field}__isnull': True}
            )[:max_records])
            
            if values:
                # Get most common values
                value_counts = Counter(values)
                most_common = [{'value': v, 'count': c} for v, c in value_counts.most_common(limit)]
                
                # Get most recent unique values
                recent_unique = []
                seen = set()
                for val in values:
                    if val and val not in seen and len(recent_unique) < limit:
                        recent_unique.append(val)
                        seen.add(val)
                
                recommendations['text_fields'][field] = {
                    'most_common': most_common,
                    'recent_values': recent_unique,
                    'total_unique': len(set(values))
                }
        
        # Analyze numeric fields - averages, ranges, common values
        for field in NUMERIC_FIELDS:
            # Create a separate queryset for each field to avoid slicing issues
            field_qs = base_queryset.filter(**{f'{field}__isnull': False})
            
            if field_qs.exists():
                # Aggregate on non-sliced queryset
                agg_data = field_qs.aggregate(
                    avg=Avg(field),
                    max=Max(field)
                )
                
                # Get values with limit
                values = list(field_qs.values_list(field, flat=True)[:max_records])
                value_counts = Counter(values)
                
                recommendations['numeric_fields'][field] = {
                    'average': round(float(agg_data['avg']), 2) if agg_data['avg'] else None,
                    'max': float(agg_data['max']) if agg_data['max'] else None,
                    'most_common': [{'value': float(v), 'count': c} for v, c in value_counts.most_common(3)],
                    'suggested_default': round(float(agg_data['avg']), 2) if agg_data['avg'] else None
                }
        
        # Add smart combinations (common motor classification + efficiency pairs)
        motor_combos = base_queryset.filter(
            motor_classification__isnull=False,
            motor_efficiency__isnull=False
        ).values('motor_classification', 'motor_efficiency').annotate(
            count=Count('id')
        ).order_by('-count')[:limit]
        
        recommendations['smart_combinations'] = {
            'motor_classification_efficiency': [
                {
                    'motor_classification': combo['motor_classification'],
                    'typical_efficiency': float(combo['motor_efficiency']),
                    'usage_count': combo['count']
                }
                for combo in motor_combos
            ]
        }
        
        return Response(recommendations)
    
    @action(detail=True, methods=['post'])
    def calculate(self, request, pk=None):
        """Perform hydraulic calculations"""
        pump_data = self.get_object()
        
        try:
            # TODO: Implement actual hydraulic calculations
            # This is a placeholder for the calculation logic
            calculations = {
                'total_head': 150.0,  # meters
                'power_required': pump_data.hp or 0,
                'efficiency': 85.0,  # percentage
                'npsh_required': 3.5,  # meters
                'npsh_available': 8.0,  # meters
                'pump_speed': 1780,  # rpm
                'flow_rate': 100.0,  # m3/hr
                'calculated_at': timezone.now().isoformat(),
                'calculation_method': 'hydraulic_analysis_v1'
            }
            
            pump_data.calculation_results = calculations
            pump_data.save(update_fields=['calculation_results', 'updated_at'])
            
            return Response({
                'message': 'Calculations completed successfully',
                'results': calculations
            })
            
        except Exception as e:
            logger.error(f"Calculation error for pump {pump_data.id}: {str(e)}")
            return Response(
                {'error': f'Calculation failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """Update pump calculation status"""
        pump_data = self.get_object()
        new_status = request.data.get('status')
        
        if new_status not in dict(PumpCalculationData.STATUS_CHOICES):
            return Response(
                {'error': 'Invalid status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        pump_data.status = new_status
        
        if new_status in ['ifr', 'ifa', 'ifc'] and not pump_data.checked_by:
            pump_data.checked_by = request.user
        
        pump_data.save(update_fields=['status', 'checked_by', 'updated_at'])
        
        return Response({
            'message': f'Status updated to {new_status}',
            'status': pump_data.status
        })
    
    @action(detail=True, methods=['get'])
    def export_data(self, request, pk=None):
        """Export pump calculation data in Excel format"""
        pump_data = self.get_object()
        
        # TODO: Implement Excel export functionality
        # This would export the data to match the original Excel format
        
        return Response({
            'message': 'Export functionality will be implemented',
            'pump_id': str(pump_data.id),
            'document_no': pump_data.document_no
        })
    
    @action(detail=True, methods=['get'])
    def generate_datasheet(self, request, pk=None):
        """
        Generate professional pump datasheet using template-based approach.
        
        Uses actual Pump Data Sheet.xlsx template to preserve professional
        formatting and structure. Smart field mapping with soft coding.
        """
        pump_data = self.get_object()
        
        try:
            logger.info(f"📊 Generating template-based datasheet for pump {pump_data.id}")
            
            # Initialize template-based datasheet generator
            generator = PumpDataSheetGenerator()
            
            # Generate datasheet using template mapping
            excel_buffer = generator.generate_datasheet(pump_data)
            
            # Generate intelligent filename
            filename = generator.generate_filename(pump_data)
            
            # Create HTTP response with Excel file
            response = HttpResponse(
                excel_buffer.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Access-Control-Expose-Headers'] = 'Content-Disposition'
            
            logger.info(f"Successfully generated datasheet: {filename}")
            
            return response
            
        except Exception as e:
            logger.error(f"Error generating datasheet for pump {pump_data.id}: {str(e)}")
            return Response(
                {
                    'error': 'Failed to generate datasheet',
                    'details': str(e),
                    'pump_id': str(pump_data.id)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], url_path='generate_pdf')
    def generate_pdf_datasheet(self, request, pk=None):
        """
        Generate professional AI-powered PDF pump datasheet.
        
        This endpoint uses advanced AI intelligence to create a comprehensive
        PDF datasheet with professional formatting and insights.
        """
        pump_data = self.get_object()
        
        try:
            logger.info(f"Generating AI PDF datasheet for pump {pump_data.id}")
            
            # Initialize AI datasheet generator
            generator = AIDatasheetGenerator()
            
            # Generate PDF datasheet using AI intelligence
            pdf_buffer = generator.generate_pdf_datasheet(pump_data)
            
            # Generate intelligent filename
            filename = generator.generate_pdf_filename(pump_data)
            
            # Create HTTP response with PDF file
            response = HttpResponse(
                pdf_buffer.getvalue(),
                content_type='application/pdf'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Access-Control-Expose-Headers'] = 'Content-Disposition'
            
            logger.info(f"Successfully generated PDF datasheet: {filename}")
            
            return response
            
        except Exception as e:
            logger.error(f"Error generating PDF datasheet for pump {pump_data.id}: {str(e)}")
            return Response(
                {
                    'error': 'Failed to generate PDF datasheet',
                    'details': str(e),
                    'pump_id': str(pump_data.id)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        """Create multiple pump calculations from imported data"""
        pump_data_list = request.data.get('pump_calculations', [])
        
        if not pump_data_list:
            return Response(
                {'error': 'No pump calculation data provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        created_pumps = []
        errors = []
        
        for i, pump_data in enumerate(pump_data_list):
            try:
                serializer = PumpCalculationDataCreateSerializer(
                    data=pump_data,
                    context={'request': request}
                )
                if serializer.is_valid():
                    pump_obj = serializer.save()
                    created_pumps.append({
                        'index': i,
                        'id': str(pump_obj.id),
                        'document_no': pump_obj.document_no,
                        'tag_no': pump_obj.tag_no
                    })
                else:
                    errors.append({
                        'index': i,
                        'errors': serializer.errors
                    })
            except Exception as e:
                errors.append({
                    'index': i,
                    'errors': str(e)
                })
        
        return Response({
            'created_count': len(created_pumps),
            'error_count': len(errors),
            'created_pumps': created_pumps,
            'errors': errors
        })
    
    @action(detail=False, methods=['get'])
    def templates(self, request):
        """Get pump calculation templates"""
        templates = [
            {
                'name': 'Centrifugal Pump - Standard',
                'description': 'Standard centrifugal pump calculation template',
                'fields': {
                    'service': 'Process Water',
                    'motor_classification': 'General Purpose',
                    'general_data': {
                        'pump_type': 'Centrifugal',
                        'stages': 1,
                        'orientation': 'Horizontal'
                    }
                }
            },
            {
                'name': 'Positive Displacement Pump',
                'description': 'Positive displacement pump calculation template',
                'fields': {
                    'service': 'Chemical Transfer',
                    'general_data': {
                        'pump_type': 'Positive Displacement',
                        'pump_design': 'Gear Pump'
                    }
                }
            }
        ]
        
        return Response(templates)
