from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator


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
    """P&ID Drawing uploaded for analysis (S3-ready)"""
    
    STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
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
