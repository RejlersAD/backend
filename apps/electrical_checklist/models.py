"""
Django models for Electrical Checklist Extraction
PROFESSIONAL PROJECT-BASED SYSTEM with AWS S3 Integration
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
import uuid

User = get_user_model()


class ChecklistProject(models.Model):
    """
    Professional Checklist Project Management
    - Organizes multiple checklists under one project
    - Stores all data in database + AWS S3
    - Multi-user collaboration with role-based access
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('on_hold', 'On Hold'),
        ('completed', 'Completed'),
        ('archived', 'Archived'),
        ('cancelled', 'Cancelled')
    ]
    
    # Auto-generated project ID
    project_code = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
        help_text="Auto-generated unique project code (e.g., ELEC-20260716-A1B2C3D4)"
    )
    
    # Project details
    project_name = models.CharField(
        max_length=200,
        help_text="User-defined project name"
    )
    description = models.TextField(
        blank=True,
        help_text="Project description and scope"
    )
    location = models.CharField(
        max_length=500,
        blank=True,
        help_text="Site location or facility name"
    )
    client_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Client or company name"
    )
    
    # Project status and timeline
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active'
    )
    start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Project start date"
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Project end date"
    )
    
    # Template and settings
    template_id = models.CharField(
        max_length=100,
        default='ups_battery_standard',
        help_text="Project template identifier"
    )
    settings = models.JSONField(
        default=dict,
        help_text="""
        Project settings:
        {
            "extract_signatures": true,
            "require_approval": true,
            "auto_generate_excel": true,
            "s3_storage": true,
            "notification_enabled": true
        }
        """
    )
    
    # Tags and categorization
    tags = models.JSONField(
        default=list,
        help_text="Project tags for categorization ['UPS', 'Battery', 'Commissioning']"
    )
    
    # S3 folder path (soft-coded)
    s3_folder = models.CharField(
        max_length=500,
        editable=False,
        help_text="S3 folder path for this project's files"
    )
    
    # Team and permissions
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='owned_checklist_projects',
        help_text="Project owner"
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_checklist_projects',
        help_text="User who created the project"
    )
    members = models.ManyToManyField(
        User,
        through='ChecklistProjectMember',
        through_fields=('project', 'user'),  # Specify which fields to use
        related_name='checklist_projects',
        blank=True
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    
    # Statistics (cached)
    total_checklists = models.IntegerField(default=0)
    total_fields_extracted = models.IntegerField(default=0)
    total_signatures_found = models.IntegerField(default=0)
    avg_confidence_score = models.FloatField(default=0.0)
    
    class Meta:
        db_table = 'electrical_checklist_projects'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project_code']),
            models.Index(fields=['owner', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]
    
    def save(self, *args, **kwargs):
        """Generate project_code and s3_folder if not exists"""
        if not self.project_code:
            # Format: ELEC-YYYYMMDD-XXXXXXXX (8 chars UUID)
            date_str = timezone.now().strftime('%Y%m%d')
            unique_id = str(uuid.uuid4())[:8].upper()
            self.project_code = f"ELEC-{date_str}-{unique_id}"
            
        if not self.s3_folder:
            # Format: electrical_checklist/{project_code}/
            self.s3_folder = f"electrical_checklist/{self.project_code}/"
            
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.project_code} - {self.project_name}"
    
    def update_statistics(self):
        """Update cached statistics from child jobs"""
        jobs = self.checklist_jobs.filter(status='completed')
        self.total_checklists = jobs.count()
        self.total_fields_extracted = sum(job.fields_extracted or 0 for job in jobs)
        self.total_signatures_found = sum(job.signatures_found or 0 for job in jobs)
        
        if self.total_checklists > 0:
            self.avg_confidence_score = sum(job.confidence_score or 0 for job in jobs) / self.total_checklists
        else:
            self.avg_confidence_score = 0.0
            
        self.save(update_fields=[
            'total_checklists',
            'total_fields_extracted',
            'total_signatures_found',
            'avg_confidence_score'
        ])


class ChecklistProjectMember(models.Model):
    """
    Project team members with role-based access
    """
    ROLE_CHOICES = [
        ('owner', 'Project Owner'),
        ('manager', 'Project Manager'),
        ('engineer', 'Engineer'),
        ('viewer', 'Viewer')
    ]
    
    project = models.ForeignKey(
        ChecklistProject,
        on_delete=models.CASCADE,
        related_name='project_members'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='checklist_memberships'
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='viewer'
    )
    added_at = models.DateTimeField(auto_now_add=True)
    added_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='added_checklist_members'
    )
    
    class Meta:
        db_table = 'electrical_checklist_project_members'
        unique_together = ['project', 'user']
        ordering = ['-added_at']
    
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.role} in {self.project.project_code}"


class ChecklistExtractionJob(models.Model):
    """
    Track extraction jobs with S3 integration
    Enhanced with project context and AWS storage
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed')
    ]
    
    id = models.AutoField(primary_key=True)
    
    # Project context (REQUIRED for professional app, nullable for migration)
    project = models.ForeignKey(
        ChecklistProject,
        on_delete=models.CASCADE,
        related_name='checklist_jobs',
        null=True,  # Nullable during migration
        blank=True,
        help_text="Parent project for this checklist"
    )
    
    # User and template
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='checklist_jobs')
    template_id = models.CharField(max_length=100, default='ups_battery_inspection')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    progress = models.IntegerField(default=0)
    
    # File info with S3 paths
    file_count = models.IntegerField(default=0)
    total_pages = models.IntegerField(default=0)
    pdf_s3_keys = models.JSONField(
        default=list,
        help_text="List of S3 object keys for uploaded PDFs"
    )
    
    # Results
    fields_extracted = models.IntegerField(default=0)
    signatures_found = models.IntegerField(default=0)
    confidence_score = models.FloatField(default=0.0)
    extracted_data = models.JSONField(null=True, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    
    # Excel export with S3 storage
    excel_s3_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="S3 object key for generated Excel file"
    )
    excel_file_size = models.IntegerField(
        default=0,
        help_text="Excel file size in bytes"
    )
    
    # Approval workflow (optional)
    requires_approval = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_checklists'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'electrical_checklist_jobs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project', '-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"Job {self.id} - {self.project.project_code} - {self.status}"
    
    def mark_completed(self):
        """Mark job as completed and update project statistics"""
        self.status = 'completed'
        self.completed_at = timezone.now()
        self.save()
        
        # Update parent project statistics
        if self.project:
            self.project.update_statistics()

