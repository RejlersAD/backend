"""
Process Datasheet Models
Database models for equipment datasheets with soft-coded configuration support
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.postgres.fields import ArrayField
import uuid
import json

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
