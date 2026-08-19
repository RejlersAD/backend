"""
QHSE Models - Soft-coded database models for Quality, Health, Safety & Environment
Migrated from Excel/Google Sheets to PostgreSQL
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone

User = get_user_model()


class QHSERunningProject(models.Model):
    """
    Running Projects Model - Tracks QHSE aspects of ongoing projects
    Soft-coded field configuration for easy modifications
    """
    
    # Primary Identification
    sr_no = models.IntegerField(verbose_name="Serial Number", unique=True)
    project_no = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="Project Number")
    project_title = models.TextField(verbose_name="Project Title")
    project_title_key = models.CharField(max_length=255, blank=True, null=True, verbose_name="Project Title Key")
    client = models.CharField(max_length=255, verbose_name="Client Name")
    
    # Project Management
    project_manager = models.CharField(max_length=255, verbose_name="Project Manager")
    project_quality_eng = models.CharField(max_length=255, blank=True, default='', verbose_name="Project Quality Engineer")
    
    # Project Timeline
    project_starting_date = models.DateField(blank=True, null=True, verbose_name="Project Starting Date")
    project_closing_date = models.DateField(blank=True, null=True, verbose_name="Project Closing Date")
    project_extension = models.DateField(blank=True, null=True, verbose_name="Project Extension Date")
    
    # Manhours Management
    man_hour_for_quality = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Manhours Allocated for Quality"
    )
    manhours_used = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Manhours Used"
    )
    manhours_balance = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0,
        verbose_name="Manhours Balance"
    )
    quality_billability_percent = models.CharField(
        max_length=20, 
        default="0%",
        verbose_name="Quality Billability Percentage"
    )
    
    # Quality Plan Status
    project_quality_plan_status_rev = models.CharField(
        max_length=50, 
        blank=True, 
        null=True,
        verbose_name="Quality Plan Status - Revision"
    )
    project_quality_plan_status_issue_date = models.DateField(
        blank=True, 
        null=True,
        verbose_name="Quality Plan Issue Date"
    )
    
    # Project Audits (Soft-coded for up to 4 audits)
    project_audit_1 = models.DateField(blank=True, null=True, verbose_name="Project Audit 1")
    project_audit_2 = models.DateField(blank=True, null=True, verbose_name="Project Audit 2")
    project_audit_3 = models.DateField(blank=True, null=True, verbose_name="Project Audit 3")
    project_audit_4 = models.DateField(blank=True, null=True, verbose_name="Project Audit 4")
    
    # Client Audits
    client_audit_1 = models.DateField(blank=True, null=True, verbose_name="Client Audit 1")
    client_audit_2 = models.DateField(blank=True, null=True, verbose_name="Client Audit 2")
    
    # Delays & Issues
    delay_in_audits_no_days = models.IntegerField(
        default=0, 
        validators=[MinValueValidator(0)],
        verbose_name="Delay in Audits (Days)"
    )
    
    # CARs (Corrective Action Requests)
    cars_open = models.IntegerField(
        default=0, 
        validators=[MinValueValidator(0)],
        verbose_name="CARs Open"
    )
    cars_delayed_closing_no_days = models.IntegerField(
        default=0, 
        validators=[MinValueValidator(0)],
        verbose_name="CARs Delayed Closing (Days)"
    )
    cars_closed = models.IntegerField(
        default=0, 
        validators=[MinValueValidator(0)],
        verbose_name="CARs Closed"
    )
    
    # Observations
    obs_open = models.IntegerField(
        default=0, 
        validators=[MinValueValidator(0)],
        verbose_name="Observations Open"
    )
    obs_delayed_closing_no_days = models.IntegerField(
        default=0, 
        validators=[MinValueValidator(0)],
        verbose_name="Observations Delayed Closing (Days)"
    )
    obs_closed = models.IntegerField(
        default=0, 
        validators=[MinValueValidator(0)],
        verbose_name="Observations Closed"
    )
    
    # Project Performance Metrics
    project_kpis_achieved_percent = models.CharField(
        max_length=20, 
        default="0%",
        verbose_name="Project KPIs Achieved (%)"
    )
    project_completion_percent = models.CharField(
        max_length=20, 
        default="0%",
        verbose_name="Project Completion (%)"
    )
    rejection_of_deliverables_percent = models.CharField(
        max_length=20, 
        blank=True, 
        null=True,
        verbose_name="Rejection of Deliverables (%)"
    )
    cost_of_poor_quality_aed = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(0)],
        verbose_name="Cost of Poor Quality (AED)"
    )
    
    # Additional Information
    remarks = models.TextField(blank=True, null=True, verbose_name="Remarks")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='qhse_projects_created',
        verbose_name="Created By"
    )
    updated_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='qhse_projects_updated',
        verbose_name="Updated By"
    )
    is_active = models.BooleanField(default=True, verbose_name="Is Active")
    
    class Meta:
        db_table = 'qhse_running_projects'
        verbose_name = 'QHSE Running Project'
        verbose_name_plural = 'QHSE Running Projects'
        ordering = ['sr_no']
        indexes = [
            models.Index(fields=['project_no']),
            models.Index(fields=['client']),
            models.Index(fields=['project_manager']),
            models.Index(fields=['is_active']),
            models.Index(fields=['-updated_at']),
        ]
    
    def __str__(self):
        return f"{self.project_no} - {self.project_title[:50]}"
    
    def save(self, *args, **kwargs):
        """Override save to auto-calculate manhours_balance"""
        if self.man_hour_for_quality is not None and self.manhours_used is not None:
            self.manhours_balance = self.man_hour_for_quality - self.manhours_used
        super().save(*args, **kwargs)
    
    @property
    def is_overdue(self):
        """Check if project is overdue"""
        closing_date = self.project_extension or self.project_closing_date
        return closing_date < timezone.now().date() if closing_date else False
    
    @property
    def total_cars(self):
        """Total CARs (open + closed)"""
        return self.cars_open + self.cars_closed
    
    @property
    def total_obs(self):
        """Total Observations (open + closed)"""
        return self.obs_open + self.obs_closed


class QHSESpotCheckRegister(models.Model):
    """
    Spot Check Register Model - Tracks quality spot checks
    Soft-coded for flexible configuration
    """
    
    # CATEGORY_CHOICES - Soft-coded enum
    CATEGORY_CHOICES = [
        ('OBSERVATION', 'Observation'),
        ('CAR', 'Corrective Action Request'),
        ('NCR', 'Non-Conformance Report'),
        ('COMPLIANT', 'Compliant'),
        ('MINOR', 'Minor Issue'),
        ('MAJOR', 'Major Issue'),
    ]
    
    # Primary Identification
    sr_no = models.IntegerField(verbose_name="Serial Number", db_index=True)
    project_no = models.CharField(max_length=50, db_index=True, verbose_name="Project Number")
    project_title = models.TextField(verbose_name="Project Title")
    client = models.CharField(max_length=255, verbose_name="Client Name")
    
    # Spot Check Details
    qhse_engineer = models.CharField(max_length=255, verbose_name="QHSE Engineer")
    date_of_spot_check = models.DateField(verbose_name="Date of Spot Check")
    time = models.TimeField(blank=True, null=True, verbose_name="Time")
    
    # Document Details
    document_no = models.CharField(max_length=255, blank=True, null=True, verbose_name="Document Number")
    document_title = models.TextField(blank=True, null=True, verbose_name="Document Title")
    originator_lead = models.CharField(max_length=255, blank=True, null=True, verbose_name="Originator/Lead")
    
    # Findings
    comments = models.TextField(blank=True, null=True, verbose_name="Comments")
    category = models.CharField(
        max_length=50, 
        choices=CATEGORY_CHOICES,
        blank=True, 
        null=True,
        verbose_name="Category"
    )
    remarks = models.TextField(blank=True, null=True, verbose_name="Remarks")
    
    # Status & Resolution
    status = models.CharField(
        max_length=50,
        choices=[
            ('OPEN', 'Open'),
            ('IN_PROGRESS', 'In Progress'),
            ('RESOLVED', 'Resolved'),
            ('CLOSED', 'Closed'),
        ],
        default='OPEN',
        verbose_name="Status"
    )
    resolution_date = models.DateField(blank=True, null=True, verbose_name="Resolution Date")
    resolution_comments = models.TextField(blank=True, null=True, verbose_name="Resolution Comments")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='qhse_spot_checks_created',
        verbose_name="Created By"
    )
    updated_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='qhse_spot_checks_updated',
        verbose_name="Updated By"
    )
    is_active = models.BooleanField(default=True, verbose_name="Is Active")
    
    class Meta:
        db_table = 'qhse_spot_check_register'
        verbose_name = 'QHSE Spot Check'
        verbose_name_plural = 'QHSE Spot Check Register'
        ordering = ['-date_of_spot_check', 'sr_no']
        indexes = [
            models.Index(fields=['project_no']),
            models.Index(fields=['date_of_spot_check']),
            models.Index(fields=['qhse_engineer']),
            models.Index(fields=['category']),
            models.Index(fields=['status']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"Spot Check #{self.sr_no} - {self.project_no} - {self.date_of_spot_check}"
    
    @property
    def is_overdue(self):
        """Check if spot check resolution is overdue (> 30 days for open items)"""
        if self.status in ['RESOLVED', 'CLOSED']:
            return False
        days_open = (timezone.now().date() - self.date_of_spot_check).days
        return days_open > 30


class QHSEAudit(models.Model):
    """
    QHSE Audit Model - Tracks project audits
    """
    project = models.ForeignKey(
        QHSERunningProject, 
        on_delete=models.CASCADE,
        related_name='audits',
        verbose_name="Project"
    )
    audit_type = models.CharField(
        max_length=50,
        choices=[
            ('PROJECT', 'Project Audit'),
            ('CLIENT', 'Client Audit'),
            ('INTERNAL', 'Internal Audit'),
            ('EXTERNAL', 'External Audit'),
        ],
        verbose_name="Audit Type"
    )
    audit_number = models.IntegerField(verbose_name="Audit Number")
    audit_date = models.DateField(verbose_name="Audit Date")
    auditor = models.CharField(max_length=255, verbose_name="Auditor")
    findings = models.TextField(blank=True, null=True, verbose_name="Findings")
    status = models.CharField(
        max_length=50,
        choices=[
            ('SCHEDULED', 'Scheduled'),
            ('COMPLETED', 'Completed'),
            ('DELAYED', 'Delayed'),
            ('CANCELLED', 'Cancelled'),
        ],
        default='SCHEDULED',
        verbose_name="Status"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'qhse_audits'
        verbose_name = 'QHSE Audit'
        verbose_name_plural = 'QHSE Audits'
        ordering = ['-audit_date']
        unique_together = ['project', 'audit_type', 'audit_number']
    
    def __str__(self):
        return f"{self.get_audit_type_display()} {self.audit_number} - {self.project.project_no}"
