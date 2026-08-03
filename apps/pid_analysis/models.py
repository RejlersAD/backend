from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator
import uuid


def pid_drawing_upload_path(instance, filename):
    """
    Generate S3-compatible upload path for P&ID drawings
    Format: pid_drawings/{user_id}/{year}/{month}/{filename}
    """
    from datetime import datetime
    now = datetime.now()
    user_id = instance.uploaded_by.id if instance.uploaded_by else 'unknown'
    return f'pid_drawings/{user_id}/{now.year}/{now.month:02d}/{filename}'


def pid_report_upload_path(instance, filename):
    """
    Generate S3-compatible upload path for P&ID reports
    Format: pid_reports/{drawing_id}/{filename}
    """
    drawing_id = instance.pid_drawing.id if instance.pid_drawing else 'unknown'
    return f'pid_reports/{drawing_id}/{filename}'


def reference_document_upload_path(instance, filename):
    """
    Generate S3-compatible upload path for reference documents
    Format: reference_docs/{category}/{filename}
    """
    category = instance.category.lower().replace(' ', '_') if instance.category else 'general'
    return f'reference_docs/{category}/{filename}'


class PIDProject(models.Model):
    """
    P&ID Project - Contains P&ID drawings organized by project
    Role-based S3 storage: pid_drawings/{organization}/{project_id}/
    """
    project_id = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
        help_text="Auto-generated unique project ID"
    )
    project_name = models.CharField(
        max_length=255,
        help_text="User-defined project name"
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Optional project description"
    )
    
    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pid_projects'
    )
    organization = models.CharField(
        max_length=255,
        blank=True,
        help_text="Organization name for RBAC S3 path"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'pid_projects'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project_id']),
            models.Index(fields=['created_by', '-created_at']),
            models.Index(fields=['organization']),
        ]
    
    def save(self, *args, **kwargs):
        """Generate project_id and set organization if not exists"""
        if not self.project_id:
            from django.utils import timezone
            date_str = timezone.now().strftime('%Y%m%d')
            unique_id = str(uuid.uuid4())[:8].upper()
            self.project_id = f"PID-{date_str}-{unique_id}"
        
        # Set organization from user profile if available
        if not self.organization and hasattr(self.created_by, 'organization'):
            self.organization = self.created_by.organization.name
        
        super().save(*args, **kwargs)
    
    def get_s3_path(self):
        """Get S3 path for this project - role-based organization"""
        org = self.organization or 'default'
        return f"pid_drawings/{org}/{self.project_id}/"
    
    def __str__(self):
        return f"{self.project_id} - {self.project_name}"


class ReferenceDocument(models.Model):
    """Reference documents and standards for RAG-enhanced P&ID analysis"""
    
    CATEGORY_CHOICES = [
        ('standard', 'Industry Standard'),
        ('guideline', 'Design Guideline'),
        ('specification', 'Technical Specification'),
        ('best_practice', 'Best Practice'),
        ('company_standard', 'Company Standard'),
        ('regulation', 'Regulation/Code'),
        ('other', 'Other'),
    ]
    
    # Document information
    title = models.CharField(max_length=255, help_text='Document title')
    description = models.TextField(blank=True, help_text='Document description')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='other')
    
    # File storage
    file = models.FileField(
        upload_to=reference_document_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'txt', 'docx', 'doc'])],
        help_text='Reference document file',
        storage=None
    )
    original_filename = models.CharField(max_length=255)
    file_size = models.IntegerField(help_text='File size in bytes')
    
    # Content (extracted text)
    content_text = models.TextField(blank=True, help_text='Extracted text content for RAG')
    chunk_count = models.IntegerField(default=0, help_text='Number of chunks created for RAG')
    
    # Vector database tracking
    vector_db_ids = models.JSONField(default=list, blank=True, help_text='Vector database document IDs')
    embedding_status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('processing', 'Processing'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
        ],
        default='pending'
    )
    
    # Metadata
    author = models.CharField(max_length=255, blank=True)
    version = models.CharField(max_length=50, blank=True)
    published_date = models.DateField(null=True, blank=True)
    tags = models.JSONField(default=list, blank=True, help_text='Searchable tags')
    
    # User tracking
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reference_documents'
    )
    
    # Status
    is_active = models.BooleanField(default=True, help_text='Include in RAG context retrieval')
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Reference Document'
        verbose_name_plural = 'Reference Documents'
        indexes = [
            models.Index(fields=['category', 'is_active']),
            models.Index(fields=['embedding_status']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.category})"


class PIDDrawing(models.Model):
    """P&ID Drawing uploaded for analysis (S3-ready with project association)"""
    
    STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    # Project association (nullable for backward compatibility)
    project = models.ForeignKey(
        PIDProject,
        on_delete=models.CASCADE,
        related_name='pid_drawings',
        null=True,
        blank=True,
        help_text='Associated P&ID project'
    )
    
    # File information (S3-compatible)
    file = models.FileField(
        upload_to=pid_drawing_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        help_text='P&ID drawing in PDF format (stored in S3 if USE_S3=True)',
        storage=None  # Uses DEFAULT_FILE_STORAGE from settings
    )
    original_filename = models.CharField(max_length=255)
    file_size = models.IntegerField(help_text='File size in bytes')
    
    # Drawing metadata
    drawing_number = models.CharField(max_length=100, blank=True, help_text='P&ID number (e.g., 16-01-08-1678-1)')
    drawing_title = models.CharField(max_length=255, blank=True)
    revision = models.CharField(max_length=20, blank=True)
    project_name = models.CharField(max_length=255, blank=True)
    
    # Structured Drawing Number Components (Smart Format)
    area = models.CharField(max_length=2, blank=True, help_text='Area code (2 digits)')
    p_area = models.CharField(max_length=2, blank=True, help_text='P/Area code (2 digits)')
    doc_code = models.CharField(max_length=2, blank=True, help_text='Document code (2 digits)')
    serial_number = models.CharField(max_length=4, blank=True, help_text='Serial number (4 digits)')
    rev = models.CharField(max_length=1, blank=True, help_text='Revision (1 digit)')
    sheet_number = models.CharField(max_length=1, blank=True, default='1', help_text='Sheet number')
    total_sheets = models.CharField(max_length=1, blank=True, default='1', help_text='Total sheets')
    
    def get_formatted_drawing_number(self):
        """Generate formatted drawing number from components"""
        if all([self.area, self.p_area, self.doc_code, self.serial_number]):
            rev_part = f"-{self.rev}" if self.rev else ""
            sheet_part = f"-{self.sheet_number}/{self.total_sheets}" if self.sheet_number and self.total_sheets else ""
            return f"{self.area}-{self.p_area}-{self.doc_code}-{self.serial_number}{rev_part}{sheet_part}"
        return self.drawing_number or 'N/A'
    
    # Analysis status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='uploaded')
    analysis_started_at = models.DateTimeField(null=True, blank=True)
    analysis_completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True, help_text='Error message if analysis failed')
    
    # User tracking
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pid_drawings'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'P&ID Drawing'
        verbose_name_plural = 'P&ID Drawings'
    
    def __str__(self):
        return f"{self.drawing_number or 'Unnamed'} - {self.original_filename}"


class PIDAnalysisReport(models.Model):
    """Analysis report generated for a P&ID drawing"""
    
    pid_drawing = models.OneToOneField(
        PIDDrawing,
        on_delete=models.CASCADE,
        related_name='analysis_report'
    )
    
    # Report summary
    total_issues = models.IntegerField(default=0)
    approved_count = models.IntegerField(default=0)
    ignored_count = models.IntegerField(default=0)
    pending_count = models.IntegerField(default=0)
    
    # Generated reports
    report_data = models.JSONField(help_text='Full analysis report in JSON format')
    pdf_report = models.FileField(
        upload_to='pid_reports/%Y/%m/%d/',
        null=True,
        blank=True,
        help_text='Generated PDF report'
    )
    excel_report = models.FileField(
        upload_to='pid_reports/%Y/%m/%d/',
        null=True,
        blank=True,
        help_text='Generated Excel report'
    )
    
    # Timestamps
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'P&ID Analysis Report'
        verbose_name_plural = 'P&ID Analysis Reports'
    
    def __str__(self):
        return f"Report for {self.pid_drawing.drawing_number or 'Unnamed'}"


class PIDIssue(models.Model):
    """Individual issue identified in P&ID analysis"""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('ignored', 'Ignored'),
    ]
    
    SEVERITY_CHOICES = [
        ('critical', 'Critical'),
        ('major', 'Major'),
        ('minor', 'Minor'),
        ('observation', 'Observation'),
    ]
    
    report = models.ForeignKey(
        PIDAnalysisReport,
        on_delete=models.CASCADE,
        related_name='issues'
    )
    
    # Issue details
    serial_number = models.IntegerField(help_text='Sequential issue number')
    pid_reference = models.CharField(max_length=200, help_text='P&ID element reference (tag, line number, etc.)')
    issue_observed = models.TextField(help_text='Detailed description of the issue')
    action_required = models.TextField(help_text='Recommended corrective action')
    evidence = models.TextField(blank=True, default='', help_text='AI justification: VISUAL → GAP → STANDARD evidence chain')
    
    # Classification
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='observation')
    category = models.CharField(max_length=100, blank=True, help_text='Equipment, Instrumentation, Piping, etc.')
    
    # Location on drawing (for direction/navigation)
    location_on_drawing = models.JSONField(
        null=True,
        blank=True,
        help_text='Location information: zone, drawing_section, proximity_description, visual_cues'
    )
    
    # Review status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    approval = models.CharField(max_length=50, default='Pending')
    remark = models.TextField(blank=True, default='Pending')
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['serial_number']
        verbose_name = 'P&ID Issue'
        verbose_name_plural = 'P&ID Issues'
    
    def __str__(self):
        return f"Issue #{self.serial_number} - {self.pid_reference}"


# ── Equipment Type Classification (seeded from equipment_type_config.json) ──

class PIDEquipmentType(models.Model):
    """
    Engineering equipment type catalogue — seeded from designation_codes in
    equipment_type_config.json.  Acts as a reference / lookup table so the
    frontend can show consistent type names and icons without touching Python.
    """

    CATEGORY_CHOICES = [
        ('VESSEL',         'Vessel / Drum'),
        ('HEAT_EXCHANGER', 'Heat Exchanger'),
        ('HEATER_COOLER',  'Heater / Cooler'),
        ('ROTATING',       'Rotating Equipment'),
        ('REACTOR',        'Reactor'),
        ('PACKAGE',        'Package Equipment'),
        ('MISC',           'Miscellaneous'),
    ]

    code        = models.CharField(max_length=10, primary_key=True,
                                   help_text='Designation code, e.g. PC, HE, VV')
    name        = models.CharField(max_length=120,
                                   help_text='Human-readable type name')
    category    = models.CharField(max_length=20, choices=CATEGORY_CHOICES,
                                   default='MISC')
    is_rotating = models.BooleanField(default=False)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table            = 'pid_equipment_types'
        ordering            = ['category', 'code']
        verbose_name        = 'PID Equipment Type'
        verbose_name_plural = 'PID Equipment Types'

    def __str__(self):
        return f'{self.code} – {self.name}'


# ── Extracted Equipment Items (persisted from analysis) ───────────────────────

class PIDEquipmentItem(models.Model):
    """
    One extracted equipment row from a P&ID analysis run.
    All process parameters are stored in the JSONField `data` for maximum
    flexibility.  The frequently-queried scalars (tag, drawing_ref, rev) have
    dedicated columns for indexing.
    """

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4,
                                       editable=False)
    upload_id       = models.CharField(max_length=40, db_index=True,
                                       help_text='upload_id from the analysis session')
    drawing_ref     = models.CharField(max_length=120, blank=True, db_index=True,
                                       help_text='Drawing / DWG NO extracted from title block')
    tag             = models.CharField(max_length=60, db_index=True,
                                       help_text='Equipment tag number e.g. V-803-TF')
    equipment_type  = models.ForeignKey(
        PIDEquipmentType,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='items',
        help_text='Classified equipment type (FK to designation code table)',
    )
    revision        = models.CharField(max_length=10, blank=True)
    description     = models.TextField(blank=True)
    extraction_mode = models.CharField(max_length=30, blank=True,
                                       help_text='pid_drawing | equipment_register')

    # All extracted process parameters in one flexible JSON column.
    # Keys match equipment_type_config.json → equip_register_fields keys.
    data = models.JSONField(
        default=dict, blank=True,
        help_text=(
            'Extracted process parameters: oper_pressure, oper_temperature, '
            'design_pressure_max, design_temp_max, moc, insulation, '
            'dimension_length, dimension_diameter, motor_rating, etc.'
        ),
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pid_equipment_items',
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table            = 'pid_equipment_items'
        ordering            = ['drawing_ref', 'tag']
        unique_together     = [('upload_id', 'tag')]
        verbose_name        = 'PID Equipment Item'
        verbose_name_plural = 'PID Equipment Items'
        indexes             = [
            models.Index(fields=['drawing_ref', 'tag']),
            models.Index(fields=['upload_id']),
        ]

    def __str__(self):
        return f'{self.tag} ({self.drawing_ref or "no drawing"})'
