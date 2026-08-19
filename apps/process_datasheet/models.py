"""
Process Datasheet Models
Database models for equipment datasheets with soft-coded configuration support
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.postgres.fields import ArrayField
import uuid
import json
from decimal import Decimal

User = get_user_model()


class EquipmentType(models.Model):
    """
    Equipment Type Configuration (Soft-Coded)
    Defines structure and validation rules for each equipment type
    """
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('deprecated', 'Deprecated'),
        ('draft', 'Draft'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    icon = models.CharField(max_length=10, default='📄')
    description = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    
    # Soft-coded configuration (JSON)
    configuration = models.JSONField(
        default=dict,
        help_text='Complete configuration including sections, fields, validations, calculations'
    )
    
    # Template references
    template_file = models.CharField(max_length=255, blank=True)
    calculation_module = models.CharField(max_length=255, blank=True)
    
    # Status and versioning
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    version = models.CharField(max_length=20, default='1.0')
    
    # Standards and references
    applicable_standards = ArrayField(
        models.CharField(max_length=100),
        default=list,
        blank=True
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_equipment_types'
    )
    
    class Meta:
        db_table = 'process_equipment_types'
        ordering = ['name']
        verbose_name = 'Equipment Type'
        verbose_name_plural = 'Equipment Types'
    
    def __str__(self):
        return f"{self.icon} {self.name}"
    
    def get_field_config(self, field_id):
        """Get configuration for a specific field"""
        for section in self.configuration.get('sections', []):
            for field in section.get('fields', []):
                if field.get('id') == field_id:
                    return field
        return None
    
    def get_validation_rules(self):
        """Get all validation rules for this equipment type"""
        return self.configuration.get('validationRules', [])
    
    def get_calculations(self):
        """Get calculation definitions"""
        return self.configuration.get('calculations', [])


class ProcessDatasheet(models.Model):
    """
    Main Process Datasheet Model
    Stores actual datasheet data with full audit trail
    """
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('ifr', 'Issued for Review'),
        ('ifa', 'Issued for Approval'),
        ('ifc', 'Issued for Construction'),
        ('afc', 'Approved for Construction'),
        ('cancelled', 'Cancelled'),
        ('superseded', 'Superseded'),
    ]
    
    DOCUMENT_CLASS_CHOICES = [
        ('1', 'Class 1'),
        ('2', 'Class 2'),
        ('3', 'Class 3'),
        ('4', 'Class 4'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Document identification
    document_number = models.CharField(max_length=100, unique=True, db_index=True)
    contractor_document_number = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=500)
    
    # Equipment identification
    equipment_type = models.ForeignKey(
        EquipmentType,
        on_delete=models.PROTECT,
        related_name='datasheets'
    )
    tag_number = models.CharField(max_length=100, db_index=True)
    service_description = models.TextField()
    location = models.CharField(max_length=200, blank=True)
    
    # Project information
    project_name = models.CharField(max_length=300)
    project_number = models.CharField(max_length=100)
    unit_number = models.CharField(max_length=100, blank=True)
    area = models.CharField(max_length=100, blank=True)
    
    # Datasheet data (Soft-coded - stores all field values)
    data = models.JSONField(
        default=dict,
        help_text='All datasheet field values based on equipment type configuration'
    )
    
    # Calculated fields (auto-computed)
    calculated_values = models.JSONField(default=dict, blank=True)
    
    # Validation results
    validation_status = models.CharField(max_length=20, default='not_validated')
    validation_results = models.JSONField(default=dict, blank=True)
    validation_score = models.FloatField(default=0.0)
    
    # AI extraction metadata
    extraction_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text='AI extraction confidence scores and sources'
    )
    
    # Document status and revision
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    document_class = models.CharField(
        max_length=1,
        choices=DOCUMENT_CLASS_CHOICES,
        default='1'
    )
    revision = models.IntegerField(default=0)
    
    # References
    pid_drawing_number = models.CharField(max_length=100, blank=True)
    line_number = models.CharField(max_length=100, blank=True)
    material_spec = models.CharField(max_length=100, blank=True)
    related_documents = ArrayField(
        models.CharField(max_length=200),
        default=list,
        blank=True
    )
    
    # Attachments
    source_files = ArrayField(
        models.CharField(max_length=500),
        default=list,
        blank=True,
        help_text='Source files (P&ID, specs, etc.) used for extraction'
    )
    generated_pdf = models.CharField(max_length=500, blank=True)
    
    # Holds and comments
    holds = models.JSONField(default=list, blank=True)
    comments = models.JSONField(default=list, blank=True)
    
    # Workflow
    prepared_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='prepared_datasheets'
    )
    checked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checked_datasheets'
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_datasheets'
    )
    
    # Timestamps
    date_prepared = models.DateField(null=True, blank=True)
    date_checked = models.DateField(null=True, blank=True)
    date_approved = models.DateField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'process_datasheets'
        ordering = ['-updated_at']
        verbose_name = 'Process Datasheet'
        verbose_name_plural = 'Process Datasheets'
        indexes = [
            models.Index(fields=['tag_number', 'equipment_type']),
            models.Index(fields=['project_number', 'status']),
            models.Index(fields=['document_number', 'revision']),
        ]
    
    def __str__(self):
        return f"{self.tag_number} - {self.title}"
    
    def get_field_value(self, field_id):
        """Get value for a specific field"""
        return self.data.get(field_id)
    
    def set_field_value(self, field_id, value):
        """Set value for a specific field"""
        self.data[field_id] = value
    
    def get_calculated_value(self, calc_id):
        """Get calculated value"""
        return self.calculated_values.get(calc_id)
    
    def increment_revision(self, user, description):
        """Create new revision"""
        self.revision += 1
        DatasheetRevision.objects.create(
            datasheet=self,
            revision_number=self.revision,
            description=description,
            revised_by=user,
            data_snapshot=self.data.copy()
        )
    
    def add_hold(self, section, description, user):
        """Add a hold to the datasheet"""
        if not isinstance(self.holds, list):
            self.holds = []
        
        self.holds.append({
            'serial_number': len(self.holds) + 1,
            'section': section,
            'description': description,
            'status': 'open',
            'created_by': user.get_full_name(),
            'created_at': str(models.DateTimeField().value_from_object(self))
        })
        self.save(update_fields=['holds'])
    
    def add_comment(self, section, comment, user, company_response=''):
        """Add a comment/CRS entry"""
        if not isinstance(self.comments, list):
            self.comments = []
        
        self.comments.append({
            'serial_number': len(self.comments) + 1,
            'section': section,
            'comment': comment,
            'company_response': company_response,
            'contractor_response': '',
            'created_by': user.get_full_name(),
            'created_at': str(models.DateTimeField().value_from_object(self))
        })
        self.save(update_fields=['comments'])


class DatasheetRevision(models.Model):
    """
    Datasheet Revision History
    Tracks all changes to datasheets
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    datasheet = models.ForeignKey(
        ProcessDatasheet,
        on_delete=models.CASCADE,
        related_name='revisions'
    )
    
    revision_number = models.IntegerField()
    description = models.TextField()
    
    # Snapshot of data at this revision
    data_snapshot = models.JSONField(default=dict)
    
    # Changes from previous revision
    changes = models.JSONField(default=dict, blank=True)
    pages_affected = ArrayField(
        models.IntegerField(),
        default=list,
        blank=True
    )
    
    # Metadata
    revised_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )
    revision_date = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'process_datasheet_revisions'
        ordering = ['-revision_number']
        unique_together = [['datasheet', 'revision_number']]
        verbose_name = 'Datasheet Revision'
        verbose_name_plural = 'Datasheet Revisions'
    
    def __str__(self):
        return f"{self.datasheet.document_number} Rev. {self.revision_number}"


class DatasheetTemplate(models.Model):
    """
    Datasheet Templates
    Stores reusable templates for quick datasheet creation
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    
    equipment_type = models.ForeignKey(
        EquipmentType,
        on_delete=models.CASCADE,
        related_name='templates'
    )
    
    # Template data (pre-filled values)
    template_data = models.JSONField(default=dict)
    
    # Usage tracking
    usage_count = models.IntegerField(default=0)
    
    # Ownership
    is_global = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='datasheet_templates'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'process_datasheet_templates'
        ordering = ['-usage_count', 'name']
        verbose_name = 'Datasheet Template'
        verbose_name_plural = 'Datasheet Templates'
    
    def __str__(self):
        return f"{self.name} ({self.equipment_type.name})"
    
    def use_template(self):
        """Increment usage counter"""
        self.usage_count += 1
        self.save(update_fields=['usage_count'])


class DatasheetValidationRule(models.Model):
    """
    Custom Validation Rules
    Allows adding project-specific or client-specific validation rules
    """
    
    SEVERITY_CHOICES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('error', 'Error'),
        ('critical', 'Critical'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    rule_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField()
    
    equipment_type = models.ForeignKey(
        EquipmentType,
        on_delete=models.CASCADE,
        related_name='custom_validation_rules'
    )
    
    # Rule logic
    condition = models.TextField(help_text='Python expression or formula')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='error')
    message = models.TextField()
    
    # Applicability
    is_active = models.BooleanField(default=True)
    applies_to_projects = ArrayField(
        models.CharField(max_length=100),
        default=list,
        blank=True,
        help_text='Empty = applies to all projects'
    )
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'process_datasheet_validation_rules'
        ordering = ['equipment_type', 'severity', 'name']
        verbose_name = 'Validation Rule'
        verbose_name_plural = 'Validation Rules'
    
    def __str__(self):
        return f"{self.rule_id} - {self.name}"


class DatasheetExtractionJob(models.Model):
    """
    AI Extraction Job Tracking
    Tracks background jobs for AI-powered data extraction
    """
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Equipment type being extracted
    equipment_type = models.ForeignKey(
        EquipmentType,
        on_delete=models.CASCADE,
        related_name='extraction_jobs',
        null=True,
        blank=True
    )
    
    # PDF file
    pdf_file = models.FileField(upload_to='extraction_pdfs/', null=True, blank=True)
    
    datasheet = models.ForeignKey(
        ProcessDatasheet,
        on_delete=models.CASCADE,
        related_name='extraction_jobs',
        null=True,
        blank=True
    )
    
    # Job details
    job_type = models.CharField(max_length=50)  # 'pdf_extraction_complete', 'quick_extraction', etc.
    extraction_mode = models.CharField(max_length=20, default='hybrid')  # 'hybrid', 'ai_only', 'ocr_only'
    source_files = ArrayField(
        models.CharField(max_length=500),
        default=list,
        blank=True
    )
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    progress = models.FloatField(default=0.0)
    
    # Results
    extracted_data = models.JSONField(default=dict, blank=True)
    confidence_scores = models.JSONField(default=dict, blank=True)
    
    # Error tracking
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    
    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'process_datasheet_extraction_jobs'
        ordering = ['-created_at']
        verbose_name = 'Extraction Job'
        verbose_name_plural = 'Extraction Jobs'
    
    def __str__(self):
        return f"{self.job_type} - {self.status}"


class PumpCalculationData(models.Model):
    """
    Pump Hydraulic Calculation Data
    Comprehensive model for pump calculation datasheets
    """
    
    ELECTRICAL_CLASS_CHOICES = [
        ('Class I, Division 1', 'Class I, Division 1'),
        ('Class I, Division 2', 'Class I, Division 2'),
        ('Class II, Division 1', 'Class II, Division 1'),
        ('Class II, Division 2', 'Class II, Division 2'),
        ('Non-Hazardous', 'Non-Hazardous'),
        ('General Purpose', 'General Purpose'),
    ]
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('ifr', 'Issued for Review'),
        ('ifa', 'Issued for Approval'),
        ('ifc', 'Issued for Construction'),
        ('approved', 'Approved'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Project Information (13 specific fields requested by user)
    agreement_no = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Agreement No',
        help_text='Project agreement number'
    )
    project_no = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Project No',
        help_text='Unique project identifier'
    )
    document_no = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name='Document No',
        help_text='Document identification number'
    )
    revision = models.CharField(
        max_length=10,
        default='0',
        verbose_name='Revision',
        help_text='Document revision level'
    )
    document_class = models.CharField(
        max_length=50,
        blank=True,
        verbose_name='Document Class',
        help_text='Document classification'
    )
    tag_no = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name='Tag No',
        help_text='Equipment tag number'
    )
    service = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Service',
        help_text='Service description'
    )
    motor_classification = models.CharField(
        max_length=50,
        choices=ELECTRICAL_CLASS_CHOICES,
        blank=True,
        verbose_name='Motor Classification',
        help_text='Electrical classification for motor'
    )
    temperature = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Temperature',
        help_text='Operating temperature (°C)'
    )
    fluid_viscosity_at_temp = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name='Fluid Viscosity @ Temp',
        help_text='Fluid viscosity at operating temperature (cP)'
    )
    hp = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='HP',
        help_text='Horsepower rating'
    )
    pump_centerline_elevation = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Pump Central Line Elevation From Grade',
        help_text='Pump centerline elevation from grade (m)'
    )
    elevation_source_btl = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Elevation of Source BTL From Pump Central Line',
        help_text='Elevation of source bottom tank level from pump centerline (m)'
    )
    
    # ==================== NEW: Template Data Sheet Fields ====================
    # Project and Equipment Info
    company_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Company Name',
        help_text='Company or client name'
    )
    site = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Site',
        help_text='Site or facility name'
    )
    unit = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Unit',
        help_text='Unit or plant designation'
    )
    manufacturer = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Manufacturer',
        help_text='Pump manufacturer name'
    )
    model = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Model',
        help_text='Pump model designation'
    )
    
    # Liquid Characteristics (Max/Min values)
    liquid_type = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Liquid Type / Service',
        help_text='Type of liquid being pumped'
    )
    vapor_pressure_max = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Vapor Pressure (Max)',
        help_text='Maximum vapor pressure in bar'
    )
    vapor_pressure_min = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Vapor Pressure (Min)',
        help_text='Minimum vapor pressure in bar'
    )
    density_max = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Density (Max)',
        help_text='Maximum density in kg/m³'
    )
    density_min = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Density (Min)',
        help_text='Minimum density in kg/m³'
    )
    viscosity_max = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Viscosity (Max)',
        help_text='Maximum viscosity in cP'
    )
    viscosity_min = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Viscosity (Min)',
        help_text='Minimum viscosity in cP'
    )
    temperature_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Temperature (Max)',
        help_text='Maximum operating temperature in °C'
    )
    temperature_min = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Temperature (Min)',
        help_text='Minimum operating temperature in °C'
    )
    
    # Operating Conditions (Max/Normal/Min values)
    flow_rate_max = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Flow Rate (Max)',
        help_text='Maximum flow rate in m³/h'
    )
    flow_rate_normal = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Flow Rate (Normal)',
        help_text='Normal flow rate in m³/h'
    )
    flow_rate_min = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Flow Rate (Min)',
        help_text='Minimum flow rate in m³/h'
    )
    suction_pressure_max = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Suction Pressure (Max)',
        help_text='Maximum suction pressure in bar'
    )
    suction_pressure_normal = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Suction Pressure (Normal)',
        help_text='Normal suction pressure in bar'
    )
    suction_pressure_min = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Suction Pressure (Min)',
        help_text='Minimum suction pressure in bar'
    )
    discharge_pressure_max = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Discharge Pressure (Max)',
        help_text='Maximum discharge pressure in bar'
    )
    discharge_pressure_normal = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Discharge Pressure (Normal)',
        help_text='Normal discharge pressure in bar'
    )
    discharge_pressure_min = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Discharge Pressure (Min)',
        help_text='Minimum discharge pressure in bar'
    )
    differential_pressure_max = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Differential Pressure (Max)',
        help_text='Maximum differential pressure in bar'
    )
    differential_pressure_normal = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Differential Pressure (Normal)',
        help_text='Normal differential pressure in bar'
    )
    differential_pressure_min = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Differential Pressure (Min)',
        help_text='Minimum differential pressure in bar'
    )
    differential_head_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Differential Head (Max)',
        help_text='Maximum differential head in m'
    )
    differential_head_normal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Differential Head (Normal)',
        help_text='Normal differential head in m'
    )
    differential_head_min = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Differential Head (Min)',
        help_text='Minimum differential head in m'
    )
    
    # NPSH VALUES (Max/Min)
    npsh_available_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='NPSH Available (Max)',
        help_text='Maximum NPSH available in m'
    )
    npsh_available_min = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='NPSH Available (Min)',
        help_text='Minimum NPSH available in m'
    )
    npsh_required = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='NPSH Required',
        help_text='NPSH required by pump in m'
    )
    
    # Pump Performance (Max/Normal/Min)
    pump_efficiency_max = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Pump Efficiency (Max)',
        help_text='Maximum pump efficiency in %'
    )
    pump_efficiency_normal = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Pump Efficiency (Normal)',
        help_text='Normal pump efficiency in %'
    )
    pump_efficiency_min = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Pump Efficiency (Min)',
        help_text='Minimum pump efficiency in %'
    )
    bhp_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='BHP (Max)',
        help_text='Maximum brake horsepower in HP'
    )
    bhp_normal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='BHP (Normal)',
        help_text='Normal brake horsepower in HP'
    )
    bhp_min = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='BHP (Min)',
        help_text='Minimum brake horsepower in HP'
    )
    absorbed_power_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Absorbed Power (Max)',
        help_text='Maximum absorbed power in kW'
    )
    absorbed_power_normal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Absorbed Power (Normal)',
        help_text='Normal absorbed power in kW'
    )
    absorbed_power_min = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Absorbed Power (Min)',
        help_text='Minimum absorbed power in kW'
    )
    
    # Driver/Motor Data
    driver_type = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Driver Type',
        help_text='Type of driver (e.g., Electric Motor, Steam Turbine)'
    )
    motor_rating = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Motor Rating',
        help_text='Motor power rating in kW'
    )
    motor_voltage = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Motor Voltage',
        help_text='Motor voltage in V'
    )
    motor_speed = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Motor Speed',
        help_text='Motor speed in RPM'
    )
    
    # Construction Materials
    casing = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Casing Material',
        help_text='Pump casing material specification'
    )
    impeller = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Impeller Material',
        help_text='Pump impeller material specification'
    )
    shaft = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Shaft Material',
        help_text='Pump shaft material specification'
    )
    bearings = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        verbose_name='Bearings Type',
        help_text='Bearing type and material specification'
    )
    mechanical_seal = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Mechanical Seal Type',
        help_text='Mechanical seal type and specification'
    )
    # ==================== END: Template Data Sheet Fields ====================
    
    # Discharge Pressure Calculations (11 fields)
    destination_description = models.CharField(
        max_length=300,
        default='Cooling Water Tank (06.5- T - 2307)',
        verbose_name='Destination Description',
        help_text='Description of destination equipment or location'
    )
    flow_type = models.CharField(
        max_length=20,
        choices=[
            ('Max', 'Max'),
            ('Normal', 'Normal'), 
            ('Min', 'Min')
        ],
        blank=True,
        verbose_name='Flow',
        help_text='Flow type selection'
    )
    destination_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Destination Pressure',
        help_text='Destination pressure in barg'
    )
    destination_elevation = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Destination EL from Pump C/L',
        help_text='Destination elevation from pump centerline in meters'
    )
    line_friction_loss = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Line Friction Loss',
        help_text='Line friction loss in bar'
    )
    flow_meter_del_p = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Flow meter Del P',
        help_text='Flow meter differential pressure in bar'
    )
    other_losses = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Other Losses',
        help_text='Other pressure losses in bar'
    )
    control_valve = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Control Valve',
        help_text='Control valve pressure drop in bar'
    )
    misc_item = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Misc Item',
        help_text='Miscellaneous pressure losses in bar'
    )
    contingency = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Contingency',
        help_text='Contingency pressure allowance in bar'
    )
    total_discharge_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Total Discharge Pressure',
        help_text='Auto-calculated total discharge pressure in bar'
    )
    
    # Control Valve Delta P Check (10 fields)
    density = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Density',
        help_text='Fluid density in kg/m³'
    )
    cv_max = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='CV Max',
        help_text='Maximum control valve coefficient'
    )
    cv_min = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='CV Min',
        help_text='Minimum control valve coefficient'
    )
    cv_ratio = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='CVRatio (Max/Min)',
        help_text='Auto-calculated ratio of maximum to minimum CV'
    )
    total_frictional_losses = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Total Frictional Losses @ Normal Flow',
        help_text='Total frictional losses at normal flow in bar'
    )
    dynamic_losses_30_percent = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='30% Dynamic Losses',
        help_text='Auto-calculated 30% of total frictional losses in bar'
    )
    cv_pressure_drop = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='CV Pr. drop @ Normal Flow',
        help_text='Control valve pressure drop at normal flow in bar'
    )
    cv_rangeability = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='CV Rangeability',
        help_text='Control valve rangeability factor'
    )
    cv_ratio_within_range = models.CharField(
        max_length=10,
        choices=[
            ('Yes', 'Yes'),
            ('No', 'No')
        ],
        blank=True,
        verbose_name='A. CV Ratio Within Range',
        help_text='Whether CV ratio is within acceptable range'
    )
    cv_pressure_drop_check = models.CharField(
        max_length=10,
        choices=[
            ('Yes', 'Yes'),
            ('No', 'No')
        ],
        blank=True,
        verbose_name='B. CV Pr. drop@Normal Flow > 30% Fric Pr. Loss',
        help_text='Whether CV pressure drop exceeds 30% of frictional losses'
    )
    
    # Suction Pressure Calculations (8 fields)
    source_op_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Source Op. Pressure',
        help_text='Source operating pressure in bar(g)'
    )
    suction_elm = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Suction ELm',
        help_text='Suction elevation in meters'
    )
    inline_inst_losses = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Inline Inst. Losses',
        help_text='Inline instrument losses in bar'
    )
    line_fric_losses = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Line Fric Losses',
        help_text='Line friction losses in bar'
    )
    control_valve_suction = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Control Valve',
        help_text='Control valve losses in suction line in bar'
    )
    misc_items_suction = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Misc Items',
        help_text='Miscellaneous items losses in suction line in bar'
    )
    total_suction_losses = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Total Suction Losses',
        help_text='Auto-calculated total suction losses in bar'
    )
    total_suction_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Total Suction Pressure',
        help_text='Auto-calculated total suction pressure in bar(g)'
    )
    
    # Power Consumption Per Pump fields
    hydraulic_power = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Hydraulic Power',
        help_text='Hydraulic power required for pumping in kW'
    )
    pump_efficiency = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Pump Efficiency',
        help_text='Pump efficiency percentage'
    )
    break_horse_power = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Break Horse Power',
        help_text='Auto-calculated break horse power in kW'
    )
    motor_rating = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Motor Rating',
        help_text='Motor rating in kW'
    )
    motor_efficiency = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Motor Efficiency',
        help_text='Motor efficiency percentage'
    )
    power_consumption = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Power Consumption',
        help_text='Auto-calculated total power consumption in kW'
    )
    type_of_motor = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Type of Motor',
        help_text='Motor type (AC Induction, VFD, Synchronous, DC Motor)'
    )
    
    # NPSH Availability fields
    suction_pressure_npsh = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Suction Pressure (NPSH)',
        help_text='Suction pressure for NPSH calculation in bar(g)'
    )
    vapor_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Vapor Pressure',
        help_text='Vapor pressure of fluid in bar(g)'
    )
    npsha = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='NPSHA',
        help_text='Auto-calculated Net Positive Suction Head Available in m'
    )
    safety_margin_npsha = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Safety Margin for NPSHA',
        help_text='Safety margin for NPSHA calculation in m'
    )
    npsha_with_safety_margin = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='NPSHA (With Safety Margin)',
        help_text='Auto-calculated NPSHA with safety margin in m'
    )
    
    # Additional pump data (comprehensive from Excel analysis)
    general_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='General pump information and specifications'
    )
    control_valve_delta_p = models.JSONField(
        default=dict,
        blank=True,
        help_text='Control valve delta P check calculations'
    )
    suction_pressure_calculations = models.JSONField(
        default=dict,
        blank=True,
        help_text='Suction pressure calculations parameters'
    )
    power_consumption_per_pump = models.JSONField(
        default=dict,
        blank=True,
        help_text='Power consumption per pump calculations'
    )
    npsh_availability = models.JSONField(
        default=dict,
        blank=True,
        help_text='NPSH availability calculations and parameters'
    )
    
    # Pump Calculation Results (Replacing General Notes & Design Basis)
    discharge_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Discharge Pressure',
        help_text='Calculated discharge pressure in bar(g)'
    )
    suction_pressure_result = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Suction Pressure',
        help_text='Calculated suction pressure in bar(g)'
    )
    differential_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Differential Pressure',
        help_text='Calculated differential pressure (discharge - suction) in bar'
    )
    differential_head = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Differential Head',
        help_text='Calculated differential head in m'
    )
    npsha_result = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='NPSHA',
        help_text='Calculated Net Positive Suction Head Available in m'
    )
    
    # Max Suction Pressure Max Density section fields
    suction_vessel_max_op_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Suction Vessel Max Op. Pressure',
        help_text='Maximum operating pressure of suction vessel in bar(g)'
    )
    suction_el_m = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Suction EL,m',
        help_text='Suction elevation in meters'
    )
    tl_to_hhll_m = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='TL to HHLL, m',
        help_text='Tank level to High High Liquid Level in meters'
    )
    max_suction_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Max Suction Pressure',
        help_text='Calculated maximum suction pressure in bar(g)'
    )
    
    # Minimum Flow Line Control Valve Calculation Fields
    pump_minimum_flow = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Pump Minimum Flow',
        help_text='Pump minimum flow rate'
    )
    fluid_density_mcf = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Fluid Density',
        help_text='Fluid density for MCF calculation'
    )
    pump_discharge_pressure_min_flow = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Pump Discharge Pressure at Min Flow',
        help_text='Pump discharge pressure at minimum flow'
    )
    destination_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Destination Pressure',
        help_text='Destination pressure'
    )
    el_destination_pump_cl = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='EL of Destination from Pump C/L',
        help_text='Elevation of destination from pump centerline'
    )
    mcf_line_friction_losses = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='MCF Line Friction Losses',
        help_text='Minimum flow line friction losses'
    )
    flow_meter_losses = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Flow Meter Losses',
        help_text='Flow meter pressure losses'
    )
    misc_pressure_drop_mcf = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Misc. Pressure Drop',
        help_text='Miscellaneous pressure drop for MCF'
    )
    mcf_cv_pressure_drop = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='MCF CV Pressure Drop',
        help_text='Calculated MCF control valve pressure drop'
    )
    
    # Max Discharge Pressure at Max Density Fields
    api_610_tolerance_used = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name='API 610 Tolerance used',
        help_text='API 610 Tolerance specification used'
    )
    api_tolerance_factor = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='API Tolerance factor',
        help_text='API tolerance factor value'
    )
    shut_off_pressure_factor = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Shut off pressure factor',
        help_text='Shut off pressure factor'
    )
    shut_off_differential_pressure = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Shut off Differential Pressure',
        help_text='Calculated shut off differential pressure'
    )
    
    # Option for Max Discharge Pressure Fields
    maximum_discharge_pressure_option_1 = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Maximum Discharge Pressure (Option 1)',
        help_text='Calculated maximum discharge pressure using Option 1 formula'
    )
    maximum_discharge_pressure_option_2 = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name='Maximum Discharge Pressure (Option 2)',
        help_text='Calculated maximum discharge pressure using Option 2 formula'
    )
    
    general_notes = models.JSONField(
        default=dict,
        blank=True,
        help_text='General notes and requirements'
    )
    
    # Calculation results
    calculation_results = models.JSONField(
        default=dict,
        blank=True,
        help_text='Calculated values and results'
    )
    
    # Status and workflow
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft'
    )
    
    # Relationships
    prepared_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='prepared_pump_calculations'
    )
    checked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checked_pump_calculations'
    )
    
    # Source files and references
    source_files = ArrayField(
        models.CharField(max_length=500),
        default=list,
        blank=True,
        help_text='Source files used for calculation'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pump_calculation_data'
        ordering = ['-updated_at']
        verbose_name = 'Pump Calculation'
        verbose_name_plural = 'Pump Calculations'
        indexes = [
            models.Index(fields=['tag_no', 'project_no']),
            models.Index(fields=['document_no', 'revision']),
            models.Index(fields=['status', 'updated_at']),
        ]
    
    def __str__(self):
        return f"{self.tag_no or 'New'} - {self.document_no or 'Draft'}"
    
    def save(self, *args, **kwargs):
        """Auto-generate document number and calculate total discharge pressure"""
        if not self.document_no:
            # Generate document number based on project and tag
            if self.project_no and self.tag_no:
                self.document_no = f"{self.project_no}-{self.tag_no}-PUMP-001"
            else:
                # Fallback to UUID-based number
                self.document_no = f"PUMP-{str(uuid.uuid4())[:8].upper()}"
        
        # AI-powered Control Valve Delta P Check calculations
        # CV Ratio = Max/Min
        if self.cv_max and self.cv_min and self.cv_min > 0:
            self.cv_ratio = self.cv_max / self.cv_min
        
        # 30% Dynamic Losses = 30% of Total Frictional Losses
        if self.total_frictional_losses:
            self.dynamic_losses_30_percent = self.total_frictional_losses * Decimal('0.30')

        # AI-powered automatic calculation of Total Discharge Pressure
        pressure_components = [
            self.destination_pressure or 0,
            self.destination_elevation or 0,
            self.line_friction_loss or 0,
            self.flow_meter_del_p or 0,
            self.other_losses or 0,
            self.control_valve or 0,
            self.misc_item or 0,
            self.contingency or 0
        ]
        
        # Calculate total only if at least one pressure component is provided
        if any(component > 0 for component in pressure_components):
            self.total_discharge_pressure = sum(pressure_components)
        
        # AI-powered Suction Pressure Calculations
        # Total Suction Losses = sum of all loss components
        suction_loss_components = [
            self.inline_inst_losses or 0,
            self.line_fric_losses or 0,
            self.control_valve_suction or 0,
            self.misc_items_suction or 0
        ]
        
        if any(loss > 0 for loss in suction_loss_components):
            self.total_suction_losses = sum(suction_loss_components)
        
        # Total Suction Pressure = Source Op. Pressure + Suction Elevation - Total Losses
        if self.source_op_pressure and self.suction_elm and self.total_suction_losses:
            self.total_suction_pressure = self.source_op_pressure + self.suction_elm - self.total_suction_losses
        
        # AI-powered Power Consumption Per Pump calculations
        # Break Horse Power = Hydraulic Power / (Pump Efficiency / 100)
        if self.hydraulic_power and self.pump_efficiency and self.pump_efficiency > 0:
            self.break_horse_power = self.hydraulic_power / (self.pump_efficiency / 100)
        
        # Power Consumption = Break Horse Power / (Motor Efficiency / 100)
        if self.break_horse_power and self.motor_efficiency and self.motor_efficiency > 0:
            self.power_consumption = self.break_horse_power / (self.motor_efficiency / 100)
        
        # AI-powered NPSH Availability calculations
        # NPSHA = (Suction Pressure - Vapor Pressure) * conversion factor from bar to meters
        # Using 1 bar = 10.2 m conversion factor (approximate for water)
        if self.suction_pressure_npsh is not None and self.vapor_pressure is not None:
            pressure_diff_bar = self.suction_pressure_npsh - self.vapor_pressure
            self.npsha = pressure_diff_bar * Decimal('10.2')  # Convert bar to meters
        
        # NPSHA (With Safety Margin) = NPSHA - Safety Margin
        if self.npsha and self.safety_margin_npsha:
            self.npsha_with_safety_margin = self.npsha - self.safety_margin_npsha
        
        super().save(*args, **kwargs)
    
    @property
    def calculation_summary(self):
        """Get summary of key calculated values"""
        results = self.calculation_results
        return {
            'total_head': results.get('total_head', 0),
            'power_required': results.get('power_required', 0),
            'efficiency': results.get('efficiency', 0),
            'npsh_required': results.get('npsh_required', 0),
            'npsh_available': results.get('npsh_available', 0),
        }

# ─── Pump Hydraulic Snapshot ─────────────────────────────────────────────
# Cloud-backed history for the Pump Hydraulic Calculation tabbed workspace.
# Stores opaque form-state JSON keyed by user + soft-coded project bucket.
# NOTE: heavy/binary artefacts (rendered .xlsx) are intentionally NOT stored
# here — the frontend regenerates them on demand from `form_state`. This
# keeps the row size small and the export layer fully soft-coded.

class PumpHydraulicSnapshot(models.Model):
    """
    Versioned snapshot of a user's Pump Hydraulic Calculation form state.
    Mirrors the client-side localStorage model so a sync is a 1:1 push.
    """

    SOURCE_AI_EXTRACTION = "ai_extraction"
    SOURCE_MANUAL        = "manual"
    SOURCE_AUTO          = "auto"
    SOURCE_CHOICES = [
        (SOURCE_AI_EXTRACTION, "AI Extracted"),
        (SOURCE_MANUAL,        "Manual Save"),
        (SOURCE_AUTO,          "Auto Save"),
    ]

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user        = models.ForeignKey(
                      User, on_delete=models.CASCADE,
                      related_name="pump_hydraulic_snapshots",
                      db_index=True,
                  )
    # Soft-coded project bucket — derived from form_state on the client
    # (job_no / contract_no / project_title / client_job_no fallback chain).
    project_key = models.CharField(max_length=255, db_index=True)
    label       = models.CharField(max_length=255, blank=True, default="")
    source      = models.CharField(max_length=32, choices=SOURCE_CHOICES,
                                   default=SOURCE_MANUAL, db_index=True)

    # Surfaced columns for fast listing / filtering — kept in sync with the
    # frontend `HISTORY_META_FIELDS` config. Not exhaustive; full data lives
    # in `form_state`.
    project_title  = models.CharField(max_length=255, blank=True, default="")
    job_no         = models.CharField(max_length=255, blank=True, default="", db_index=True)
    client_name    = models.CharField(max_length=255, blank=True, default="")
    pump_tag_no    = models.CharField(max_length=255, blank=True, default="", db_index=True)
    calculation_no = models.CharField(max_length=255, blank=True, default="")

    # Opaque payloads
    form_state = models.JSONField(default=dict, help_text="Full client form state")
    context    = models.JSONField(default=dict, blank=True,
                                  help_text="Snapshot context (e.g. extraction summary)")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Pump Hydraulic Snapshot"
        verbose_name_plural = "Pump Hydraulic Snapshots"
        indexes = [
            models.Index(fields=["user", "project_key", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.project_key} — {self.label or self.source} ({self.created_at:%Y-%m-%d %H:%M})"
