"""
DesignIQ Models - AI-Powered Engineering Design Analysis
Intelligent design verification, optimization, and recommendation system
"""

from django.db import models
from django.contrib.auth import get_user_model
from apps.core.models import TimeStampedModel
import uuid

User = get_user_model()


# List Type Configuration (Soft-coded approach)
LIST_TYPES = {
    'line_list': {
        'name': 'Line List',
        'icon': 'ViewColumnsIcon',
        'description': 'Piping line specifications and attributes',
        'default_fields': ['line_number', 'service', 'size', 'rating', 'material']
    },
    'critical_stress': {
        'name': 'Critical Stress Line List',
        'icon': 'ExclamationTriangleIcon',
        'description': 'Stress critical piping line specifications',
        'default_fields': ['line_number', 'service', 'size', 'rating', 'material']
    },
    'equipment_list': {
        'name': 'Equipment List',
        'icon': 'CubeIcon',
        'description': 'Equipment specifications and details',
        'default_fields': ['tag_number', 'description', 'type', 'capacity', 'duty']
    },
    'tie_in_list': {
        'name': 'Tie-In List',
        'icon': 'LinkIcon',
        'description': 'Connection points and tie-in specifications',
        'default_fields': ['tie_in_number', 'location', 'size', 'type', 'connection_details']
    },
    'alarm_trip_list': {
        'name': 'Alarm/Trip List',
        'icon': 'BellAlertIcon',
        'description': 'Safety alarms and trip setpoints',
        'default_fields': ['tag', 'description', 'alarm_type', 'setpoint', 'action']
    }
}


class DesignProject(TimeStampedModel):
    """
    Main design project container
    Tracks engineering design analysis sessions
    """
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('analyzing', 'Analyzing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    DESIGN_TYPE_CHOICES = [
        ('process_flow', 'Process Flow Design'),
        ('equipment', 'Equipment Design'),
        ('piping', 'Piping & Instrumentation'),
        ('heat_exchanger', 'Heat Exchanger Design'),
        ('vessel', 'Pressure Vessel Design'),
        ('pump', 'Pump Selection & Design'),
        ('valve', 'Valve Sizing & Selection'),
        ('safety', 'Safety System Design'),
        ('other', 'Other Design Type'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project_name = models.CharField(max_length=300)
    design_type = models.CharField(max_length=50, choices=DESIGN_TYPE_CHOICES)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    
    # User and organization
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='designiq_projects')
    organization = models.CharField(max_length=300, blank=True)
    
    # Design parameters (JSON field for flexibility)
    design_parameters = models.JSONField(default=dict, blank=True)
    
    # AI Analysis results
    ai_analysis_results = models.JSONField(default=dict, blank=True)
    ai_confidence_score = models.FloatField(default=0.0, help_text='AI confidence score (0-100)')
    ai_recommendations = models.JSONField(default=list, blank=True)
    
    # Files
    input_file = models.FileField(upload_to='designiq/inputs/%Y/%m/%d/', null=True, blank=True)
    output_file = models.FileField(upload_to='designiq/outputs/%Y/%m/%d/', null=True, blank=True)
    
    # Metadata
    processing_time = models.FloatField(null=True, blank=True, help_text='Processing time in seconds')
    error_message = models.TextField(blank=True)
    
    class Meta:
        db_table = 'designiq_projects'
        ordering = ['-created_at']
        verbose_name = 'DesignIQ Project'
        verbose_name_plural = 'DesignIQ Projects'
        indexes = [
            models.Index(fields=['created_by', 'status']),
            models.Index(fields=['design_type']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"{self.project_name} ({self.get_design_type_display()})"


class DesignAnalysis(TimeStampedModel):
    """
    Individual design analysis within a project
    Stores detailed analysis results and AI insights
    """
    
    SEVERITY_CHOICES = [
        ('critical', 'Critical'),
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
        ('info', 'Informational'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(DesignProject, on_delete=models.CASCADE, related_name='analyses')
    
    # Analysis details
    analysis_type = models.CharField(max_length=100)
    title = models.CharField(max_length=300)
    description = models.TextField()
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='info')
    
    # AI insights
    ai_finding = models.TextField(blank=True)
    ai_recommendation = models.TextField(blank=True)
    ai_confidence = models.FloatField(default=0.0)
    
    # References
    standard_reference = models.CharField(max_length=200, blank=True, help_text='e.g., ASME, API, ISO')
    code_section = models.CharField(max_length=200, blank=True)
    
    # Status
    is_resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_analyses')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)
    
    class Meta:
        db_table = 'designiq_analyses'
        ordering = ['severity', '-created_at']
        verbose_name = 'Design Analysis'
        verbose_name_plural = 'Design Analyses'
        indexes = [
            models.Index(fields=['project', 'severity']),
            models.Index(fields=['is_resolved']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.get_severity_display()})"


class DesignOptimization(TimeStampedModel):
    """
    AI-powered design optimization suggestions
    """
    
    IMPACT_CHOICES = [
        ('high', 'High Impact'),
        ('medium', 'Medium Impact'),
        ('low', 'Low Impact'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(DesignProject, on_delete=models.CASCADE, related_name='optimizations')
    
    # Optimization details
    category = models.CharField(max_length=100)
    title = models.CharField(max_length=300)
    description = models.TextField()
    impact = models.CharField(max_length=20, choices=IMPACT_CHOICES, default='medium')
    
    # Benefits
    estimated_cost_savings = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estimated_efficiency_gain = models.FloatField(null=True, blank=True, help_text='Percentage improvement')
    
    # Implementation
    implementation_effort = models.CharField(max_length=100, blank=True)
    implementation_notes = models.TextField(blank=True)
    
    # Status
    is_implemented = models.BooleanField(default=False)
    implemented_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    implemented_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'designiq_optimizations'
        ordering = ['impact', '-created_at']
        verbose_name = 'Design Optimization'
        verbose_name_plural = 'Design Optimizations'
    
    def __str__(self):
        return f"{self.title} ({self.get_impact_display()})"


class DesignTemplate(TimeStampedModel):
    """
    Reusable design templates and best practices
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    design_type = models.CharField(max_length=50, choices=DesignProject.DESIGN_TYPE_CHOICES)
    description = models.TextField()
    
    # Template content
    template_data = models.JSONField(default=dict)
    parameters_schema = models.JSONField(default=dict, help_text='JSON schema for parameters')
    
    # Metadata
    is_public = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    usage_count = models.IntegerField(default=0)
    
    class Meta:
        db_table = 'designiq_templates'
        ordering = ['-usage_count', 'name']
        verbose_name = 'Design Template'
        verbose_name_plural = 'Design Templates'
    
    def __str__(self):
        return f"{self.name} ({self.get_design_type_display()})"


class EngineeringListItem(TimeStampedModel):
    """
    Generic model for engineering lists (Line List, Equipment List, etc.)
    Soft-coded approach for maximum flexibility
    """
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        DesignProject, 
        on_delete=models.CASCADE, 
        related_name='list_items',
        null=True,
        blank=True
    )
    
    # List type and identification
    list_type = models.CharField(max_length=50, db_index=True)  # line_list, equipment_list, etc.
    item_tag = models.CharField(max_length=200, db_index=True)  # Unique identifier (line number, equipment tag, etc.)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    
    # Flexible data storage (JSON field for any attributes)
    data = models.JSONField(default=dict, blank=True)
    # Example structure for different types:
    # Line List: {'service': 'Steam', 'size': '6"', 'rating': '150#', 'material': 'CS', 'fluid': 'Steam'}
    # Equipment: {'type': 'Pump', 'capacity': '100 m3/h', 'duty': 'Transfer', 'power': '15 kW'}
    # Tie-In: {'location': 'N-101', 'connection_type': 'Welded', 'size': '4"', 'elevation': '2.5m'}
    # Alarm/Trip: {'alarm_type': 'High', 'setpoint': '95%', 'action': 'Shutdown', 'priority': 'Critical'}
    
    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='list_items_created')
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='list_items_updated', blank=True)
    notes = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)  # File URLs/paths
    
    # Validation and compliance
    is_validated = models.BooleanField(default=False)
    validation_notes = models.TextField(blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='list_items_validated', blank=True)
    
    # Version control
    version = models.IntegerField(default=1)
    revision_history = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'designiq_engineering_list_items'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['list_type', 'status']),
            models.Index(fields=['project', 'list_type']),
            models.Index(fields=['item_tag']),
            models.Index(fields=['-created_at']),
        ]
        unique_together = [['project', 'list_type', 'item_tag']]
    
    def __str__(self):
        list_name = LIST_TYPES.get(self.list_type, {}).get('name', self.list_type)
        return f"{list_name}: {self.item_tag}"
    
    def get_list_type_display(self):
        """Get human-readable list type name"""
        return LIST_TYPES.get(self.list_type, {}).get('name', self.list_type.replace('_', ' ').title())
    
    def increment_version(self, user, notes=''):
        """Increment version and save to revision history"""
        self.revision_history.append({
            'version': self.version,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'updated_by': user.email if user else None,
            'notes': notes,
            'data_snapshot': self.data.copy()
        })
        self.version += 1
        self.updated_by = user
        self.save()


class ProcessedPIDOutput(TimeStampedModel):
    """
    Store historical P&ID processing outputs with generated Excel files
    Allows users to download previously processed results without reprocessing
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        DesignProject,
        on_delete=models.CASCADE,
        related_name='processed_outputs',
        null=True,
        blank=True
    )
    
    # P&ID Identification
    pid_number = models.CharField(max_length=200, db_index=True)
    pid_revision = models.CharField(max_length=50, blank=True)
    list_type = models.CharField(max_length=50, db_index=True, default='line_list')
    
    # Processing metadata
    document_id = models.CharField(max_length=500, unique=True, db_index=True)
    processing_date = models.DateTimeField(auto_now_add=True)
    processed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    # File storage
    excel_file = models.FileField(upload_to='designiq/processed_outputs/%Y/%m/', null=True, blank=True)
    excel_filename = models.CharField(max_length=500)
    file_size = models.BigIntegerField(default=0)  # in bytes
    
    # Processing statistics
    total_lines = models.IntegerField(default=0)
    total_columns = models.IntegerField(default=0)
    processing_time_seconds = models.FloatField(default=0)
    
    # Additional metadata
    format_type = models.CharField(max_length=50, default='general')  # onshore, offshore, general, adnoc
    include_area = models.BooleanField(default=False)
    enrichment_enabled = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'designiq_processed_pid_outputs'
        ordering = ['-processing_date']
        indexes = [
            models.Index(fields=['pid_number', '-processing_date']),
            models.Index(fields=['list_type', '-processing_date']),
            models.Index(fields=['document_id']),
        ]
    
    def __str__(self):
        return f"PID: {self.pid_number} - Rev {self.pid_revision} ({self.processing_date.strftime('%Y-%m-%d')})"

