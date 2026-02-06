"""
PFD Project Models
Manages PFD projects with reference documents
"""

from django.db import models
from django.contrib.auth import get_user_model
import uuid

User = get_user_model()


class PFDProject(models.Model):
    """
    PFD Project - Contains reference documents and multiple PFD uploads
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
    
    # Reference documents (stored as file paths or URLs)
    reference_documents = models.JSONField(
        default=dict,
        help_text="""
        Reference documents for this project:
        {
            'bfd': 'path/to/file',
            'process_description': 'path/to/file',
            'process_design_basis': 'path/to/file',
            'operation_control_philosophy': 'path/to/file',
            'scope_of_work': 'path/to/file',
            'legends_symbols': 'path/to/file',
            'equipment_datasheet': 'path/to/file',
            'other': 'path/to/file'
        }
        """
    )
    
    # Metadata
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='pfd_projects'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'pfd_projects'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project_id']),
            models.Index(fields=['created_by', '-created_at']),
        ]
    
    def save(self, *args, **kwargs):
        """Generate project_id if not exists"""
        if not self.project_id:
            # Generate format: PFD-YYYYMMDD-XXXX
            from django.utils import timezone
            date_str = timezone.now().strftime('%Y%m%d')
            unique_id = str(uuid.uuid4())[:8].upper()
            self.project_id = f"PFD-{date_str}-{unique_id}"
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.project_id} - {self.project_name}"


class PFDUpload(models.Model):
    """
    Individual PFD uploads within a project
    Multiple PFDs can be uploaded to the same project
    """
    project = models.ForeignKey(
        PFDProject,
        on_delete=models.CASCADE,
        related_name='pfd_uploads'
    )
    upload_id = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
        help_text="Auto-generated unique upload ID"
    )
    
    # PFD Document details
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=1000)
    file_size = models.BigIntegerField(help_text="File size in bytes")
    
    # Drawing metadata
    drawing_number = models.CharField(max_length=100, blank=True)
    drawing_revision = models.CharField(max_length=50, blank=True)
    drawing_title = models.CharField(max_length=500, blank=True)
    project_name_field = models.CharField(max_length=255, blank=True, help_text="Drawing project name")
    
    # Processing status
    STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='uploaded'
    )
    
    # Verification results
    verification_results = models.JSONField(
        default=dict,
        blank=True,
        help_text="AI verification results"
    )
    
    # Metadata
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='pfd_uploads'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'pfd_uploads'
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['project', '-uploaded_at']),
            models.Index(fields=['upload_id']),
            models.Index(fields=['status']),
        ]
    
    def save(self, *args, **kwargs):
        """Generate upload_id if not exists"""
        if not self.upload_id:
            # Generate format: PFDU-YYYYMMDD-XXXX
            from django.utils import timezone
            date_str = timezone.now().strftime('%Y%m%d')
            unique_id = str(uuid.uuid4())[:8].upper()
            self.upload_id = f"PFDU-{date_str}-{unique_id}"
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.upload_id} - {self.file_name}"


class PFDVerificationReport(models.Model):
    """
    Verification report generated for a PFD upload
    Contains AI-generated issues and analysis results
    """
    pfd_upload = models.OneToOneField(
        PFDUpload,
        on_delete=models.CASCADE,
        related_name='verification_report'
    )
    
    # Report summary
    total_issues = models.IntegerField(default=0)
    critical_count = models.IntegerField(default=0)
    major_count = models.IntegerField(default=0)
    minor_count = models.IntegerField(default=0)
    observation_count = models.IntegerField(default=0)
    
    # Approval tracking
    approved_count = models.IntegerField(default=0)
    ignored_count = models.IntegerField(default=0)
    pending_count = models.IntegerField(default=0)
    
    # Full report data
    report_data = models.JSONField(
        default=dict,
        help_text="Complete AI analysis results in JSON format"
    )
    
    # Extracted drawing info
    extracted_drawing_number = models.CharField(max_length=100, blank=True)
    extracted_revision = models.CharField(max_length=50, blank=True)
    extracted_project_name = models.CharField(max_length=255, blank=True)
    extracted_client_name = models.CharField(max_length=255, blank=True)
    
    # Timestamps
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pfd_verification_reports'
        ordering = ['-generated_at']
    
    def __str__(self):
        return f"Verification Report for {self.pfd_upload.upload_id}"


class PFDIssue(models.Model):
    """
    Individual issue identified in PFD verification
    """
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
    
    CATEGORY_CHOICES = [
        ('Equipment', 'Equipment'),
        ('Streams', 'Streams'),
        ('Control', 'Control'),
        ('Documentation', 'Documentation'),
        ('Safety', 'Safety'),
        ('Material Balance', 'Material Balance'),
        ('Other', 'Other'),
    ]
    
    report = models.ForeignKey(
        PFDVerificationReport,
        on_delete=models.CASCADE,
        related_name='issues'
    )
    
    # Issue details
    serial_number = models.IntegerField(help_text='Sequential issue number')
    issue_found = models.TextField(help_text='Description of the issue observed')
    action_required = models.TextField(help_text='Recommended corrective action')
    
    # Classification
    severity = models.CharField(
        max_length=20,
        choices=SEVERITY_CHOICES,
        default='observation'
    )
    category = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES,
        default='Other'
    )
    
    # Review status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    approval = models.CharField(
        max_length=50,
        default='Pending',
        help_text='Approval decision (Approved/Rejected/Pending)'
    )
    remark = models.TextField(
        blank=True,
        default='Pending',
        help_text='Engineer remarks or comments'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pfd_issues'
        ordering = ['serial_number']
        indexes = [
            models.Index(fields=['report', 'serial_number']),
            models.Index(fields=['status']),
            models.Index(fields=['severity']),
        ]
    
    def __str__(self):
        return f"Issue #{self.serial_number} - {self.severity} - {self.category}"
        if not self.upload_id:
            from django.utils import timezone
            date_str = timezone.now().strftime('%Y%m%d%H%M%S')
            unique_id = str(uuid.uuid4())[:6].upper()
            self.upload_id = f"PFD-UP-{date_str}-{unique_id}"
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.upload_id} - {self.file_name}"
