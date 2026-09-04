"""
HR Core Models - Unified Employee Master System

This module consolidates employee data from:
- apps.users.models.User
- retired apps.users.models.UserProfile rows
- finance and payroll domain extensions
- onboarding and offboarding workflow extensions

DESIGN PRINCIPLE: Single Source of Truth
- Employee identity and organization data lives here
- Domain-specific tables reference this canonical record
- Legacy identifiers (employee_code, emp_code) indexed for backward compatibility
"""
import uuid
from django.db import models
from django.utils import timezone
from apps.core.models import TimeStampedModel


class EmployeeMaster(TimeStampedModel):
    """
    Central Employee Master Record - Single Source of Truth
    
    Consolidates employee data from multiple legacy tables while maintaining
    backward compatibility through indexed legacy identifiers.
    
    Migration Strategy:
    - Phase 1: Create this table (parallel to existing tables)
    - Phase 2: Dual-write (update both old + new tables)
    - Phase 3: Migrate historical data
    - Phase 4: Switch reads to this table
    - Phase 5: Deprecate old tables
    """
    
    # ========================================
    # PRIMARY IDENTITY (UUID for global uniqueness)
    # ========================================
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text='Globally unique employee identifier'
    )
    
    # ========================================
    # LEGACY IDENTIFIERS (Indexed for backward compatibility)
    # ========================================
    employee_number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text='Employee number from user_profiles table (primary legacy ID)'
    )
    
    employee_code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text='Employee code from finance tables (salary, payroll)'
    )
    
    emp_code = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text='Employee code from timesheet/biometric system (truncated)'
    )
    
    # ========================================
    # AUTHENTICATION LINK
    # ========================================
    user = models.OneToOneField(
        'users.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='employee_master',
        help_text='Optional authentication account; historical employees and contractors may not have one'
    )
    
    email = models.EmailField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text='Employee email; optional for historical employees without an account'
    )
    
    # ========================================
    # PERSONAL INFORMATION
    # ========================================
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    preferred_given_name = models.CharField(
        max_length=100,
        blank=True,
        help_text='Preferred first name for daily use'
    )
    initials = models.CharField(max_length=10, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    
    # ========================================
    # PHOTO (Single S3 source - replaces avatar + photo_url duplication)
    # ========================================
    photo_file_path = models.CharField(
        max_length=500,
        blank=True,
        help_text='S3 key: media/employee_photos/{uuid}.{ext}'
    )
    
    photo_url = models.URLField(
        max_length=1000,
        blank=True,
        help_text='Presigned S3 URL (auto-refreshed every 7 days)'
    )
    
    photo_file_size = models.IntegerField(
        null=True,
        blank=True,
        help_text='File size in bytes'
    )
    
    photo_mime_type = models.CharField(
        max_length=100,
        blank=True,
        help_text='MIME type (image/jpeg, image/png)'
    )
    
    photo_uploaded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When photo was last uploaded'
    )
    
    # ========================================
    # ORGANIZATIONAL HIERARCHY
    # ========================================
    manager = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='direct_reports',
        help_text='Direct reporting manager'
    )
    
    department = models.CharField(max_length=100, blank=True, db_index=True)
    division = models.CharField(max_length=100, blank=True, db_index=True)
    business_unit = models.CharField(max_length=100, blank=True)
    business_area = models.CharField(max_length=100, blank=True)
    office = models.CharField(max_length=100, blank=True)
    
    BRANCH_CHOICES = [
        ('RAD', 'Rejlers Abu Dhabi (RAD)'),
        ('RIN', 'Rejlers India (RIN)'),
    ]
    
    branch = models.CharField(
        max_length=20,
        choices=BRANCH_CHOICES,
        blank=True,
        db_index=True,
        help_text='Operating branch/entity'
    )
    
    # ========================================
    # JOB INFORMATION
    # ========================================
    job_title_uae = models.CharField(
        max_length=200,
        blank=True,
        help_text='Job title for UAE operations'
    )
    
    job_title_finland = models.CharField(
        max_length=200,
        blank=True,
        help_text='Job title for Finland operations'
    )
    
    designation = models.CharField(
        max_length=100,
        blank=True,
        help_text='Official designation/position'
    )
    
    # ========================================
    # EMPLOYMENT LIFECYCLE
    # ========================================
    join_date = models.DateField(
        help_text='Date of joining the organization'
    )
    
    probation_end_date = models.DateField(
        null=True,
        blank=True,
        help_text='Expected end of probation period'
    )
    
    confirmation_date = models.DateField(
        null=True,
        blank=True,
        help_text='Date of employment confirmation'
    )
    
    exit_date = models.DateField(
        null=True,
        blank=True,
        help_text='Last working day (if exited)'
    )
    
    EMPLOYMENT_STATUS_CHOICES = [
        ('active', 'Active'),
        ('probation', 'On Probation'),
        ('notice_period', 'Notice Period'),
        ('exited', 'Exited'),
        ('suspended', 'Suspended'),
    ]
    
    employment_status = models.CharField(
        max_length=20,
        choices=EMPLOYMENT_STATUS_CHOICES,
        default='active',
        db_index=True,
        help_text='Current employment status'
    )
    
    # ========================================
    # SALARY (Basic info - detailed records in EmployeeSalary model)
    # ========================================
    current_base_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Current base salary (denormalized for quick access)'
    )
    
    currency = models.CharField(
        max_length=3,
        default='AED',
        help_text='Salary currency code (ISO 4217)'
    )
    
    # ========================================
    # CONTACT INFORMATION
    # ========================================
    phone_number = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    
    # ========================================
    # BANKING & PAYROLL
    # ========================================
    bank_account_number = models.CharField(
        max_length=50,
        blank=True,
        help_text='Bank account number for salary transfer'
    )
    
    bank_name = models.CharField(max_length=100, blank=True)
    
    iban = models.CharField(
        max_length=34,
        blank=True,
        help_text='International Bank Account Number'
    )
    
    swift_code = models.CharField(
        max_length=11,
        blank=True,
        help_text='SWIFT/BIC code'
    )
    
    # ========================================
    # TAX & COMPLIANCE
    # ========================================
    pan_number = models.CharField(
        max_length=20,
        blank=True,
        help_text='Permanent Account Number (India tax ID)'
    )
    
    uan_number = models.CharField(
        max_length=20,
        blank=True,
        help_text='Universal Account Number (India PF)'
    )
    
    tax_id = models.CharField(
        max_length=50,
        blank=True,
        help_text='Tax identification number'
    )
    
    # ========================================
    # HR SYSTEM INTEGRATION IDs
    # ========================================
    employment_id = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text='Employment ID from external HRM system'
    )
    
    candidate_id = models.CharField(
        max_length=50,
        blank=True,
        help_text='Candidate ID from recruitment system'
    )
    
    account_name = models.CharField(
        max_length=100,
        blank=True,
        help_text='Active Directory account name'
    )
    
    # ========================================
    # METADATA & AUDIT
    # ========================================
    created_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='employees_created',
        help_text='User who created this employee record'
    )
    
    last_updated_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='employees_updated',
        help_text='User who last updated this record'
    )
    
    # ========================================
    # FLAGS
    # ========================================
    is_test_person = models.BooleanField(
        default=False,
        help_text='Test/demo employee flag (exclude from reports)'
    )
    
    protected_identity = models.BooleanField(
        default=False,
        help_text='Protected identity flag (restrict access)'
    )
    
    not_signed = models.BooleanField(
        default=False,
        help_text='Contract not yet signed flag'
    )
    
    # ========================================
    # ENGINEERING PROFILE (JSON)
    # ========================================
    engineer_profile = models.JSONField(
        null=True,
        blank=True,
        default=dict,
        help_text='Engineering expertise data: disciplines, skills, certifications, projects, availability'
    )
    
    class Meta:
        db_table = 'hr_employee_master'
        verbose_name = 'Employee Master Record'
        verbose_name_plural = 'Employee Master Records'
        ordering = ['-created_at']
        
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['employee_number']),
            models.Index(fields=['employee_code']),
            models.Index(fields=['emp_code']),
            models.Index(fields=['employment_status', 'join_date']),
            models.Index(fields=['department', 'division']),
            models.Index(fields=['branch', 'employment_status']),
        ]
    
    def __str__(self):
        return f"{self.employee_number} - {self.first_name} {self.last_name}"
    
    def get_full_name(self):
        """Return full name."""
        return f"{self.first_name} {self.last_name}".strip()
    
    def get_display_name(self):
        """Return preferred name or full name."""
        if self.preferred_given_name:
            return f"{self.preferred_given_name} {self.last_name}".strip()
        return self.get_full_name()
    
    def is_active_employee(self):
        """Check if employee is currently active."""
        return self.employment_status == 'active'
    
    def days_employed(self):
        """Calculate days of employment."""
        end_date = self.exit_date or timezone.now().date()
        return (end_date - self.join_date).days
    
    def refresh_photo_url(self):
        """
        Refresh presigned S3 URL for photo.
        Called by Celery task daily to maintain valid URLs.
        """
        if self.photo_file_path:
            from apps.core.s3_service import S3Service
            s3_service = S3Service()
            self.photo_url = s3_service.generate_presigned_url(
                self.photo_file_path,
                expiration=604800  # 7 days
            )
            self.save(update_fields=['photo_url'])


class EmployeeIdentityAlias(TimeStampedModel):
    """Cross-system identifier mapped to one canonical EmployeeMaster UUID."""

    SOURCE_CHOICES = [
        ('radai', 'RADAI'),
        ('rbac', 'RBAC Profile'),
        ('payroll', 'Payroll'),
        ('timesheet', 'Timesheet / Biometric'),
        ('onboarding', 'Onboarding'),
        ('external', 'External System'),
    ]
    TYPE_CHOICES = [
        ('uuid', 'Canonical UUID'),
        ('user_id', 'User ID'),
        ('email', 'Email'),
        ('employee_number', 'Employee Number'),
        ('employee_code', 'Employee Code'),
        ('account_name', 'Account Name'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        EmployeeMaster, on_delete=models.CASCADE, related_name='identity_aliases'
    )
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, db_index=True)
    identifier_type = models.CharField(max_length=30, choices=TYPE_CHOICES, db_index=True)
    value = models.CharField(max_length=255)
    normalized_value = models.CharField(max_length=255, db_index=True)
    is_primary = models.BooleanField(default=False)
    verified_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'hr_employee_identity_alias'
        ordering = ['source', 'identifier_type']
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'identifier_type', 'normalized_value'],
                name='hr_identity_alias_unique',
            ),
        ]
        indexes = [
            models.Index(
                fields=['identifier_type', 'normalized_value'],
                name='hr_identity_lookup_idx',
            ),
        ]

    def save(self, *args, **kwargs):
        self.value = str(self.value or '').strip()
        self.normalized_value = self.normalize(self.identifier_type, self.value)
        super().save(*args, **kwargs)

    @staticmethod
    def normalize(identifier_type, value):
        value = str(value or '').strip()
        if identifier_type in {'email', 'account_name'}:
            return value.casefold()
        if identifier_type in {'employee_number', 'employee_code'}:
            return ''.join(value.upper().split())
        return value

    def __str__(self):
        return f'{self.employee_id}: {self.source}/{self.identifier_type}={self.value}'


class HRWorkflowDefinition(TimeStampedModel):
    """Versioned, reusable approval workflow configured by HR administrators."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=80, db_index=True)
    name = models.CharField(max_length=160)
    version = models.PositiveIntegerField(default=1)
    description = models.TextField(blank=True)
    subject_type = models.CharField(max_length=80, help_text='For example: payroll.leave_request')
    is_active = models.BooleanField(default=True, db_index=True)
    configuration = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='hr_workflow_definitions_created',
    )

    class Meta:
        db_table = 'hr_workflow_definition'
        ordering = ['code', '-version']
        constraints = [
            models.UniqueConstraint(fields=['code', 'version'], name='hr_workflow_code_version_unique'),
        ]

    def __str__(self):
        return f'{self.name} v{self.version}'


class HRWorkflowStage(TimeStampedModel):
    """Ordered stage within a reusable workflow definition."""

    APPROVER_CHOICES = [
        ('employee_manager', 'Employee Manager'),
        ('role', 'RBAC Role'),
        ('user', 'Named User'),
        ('requester', 'Requester'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition = models.ForeignKey(
        HRWorkflowDefinition, on_delete=models.CASCADE, related_name='stages'
    )
    code = models.SlugField(max_length=80)
    name = models.CharField(max_length=160)
    sequence = models.PositiveIntegerField()
    approver_type = models.CharField(max_length=30, choices=APPROVER_CHOICES)
    approver_value = models.CharField(
        max_length=160, blank=True,
        help_text='Role code or user ID when required by approver_type.',
    )
    due_after_hours = models.PositiveIntegerField(default=48)
    escalate_after_hours = models.PositiveIntegerField(default=72)
    escalation_role_code = models.CharField(max_length=80, blank=True)
    require_comment_on_reject = models.BooleanField(default=True)
    configuration = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'hr_workflow_stage'
        ordering = ['definition', 'sequence']
        constraints = [
            models.UniqueConstraint(fields=['definition', 'code'], name='hr_workflow_stage_code_unique'),
            models.UniqueConstraint(fields=['definition', 'sequence'], name='hr_workflow_stage_seq_unique'),
        ]

    def __str__(self):
        return f'{self.definition.code}: {self.sequence}. {self.name}'


class HRWorkflowInstance(TimeStampedModel):
    """Runtime workflow attached to an HR business record."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition = models.ForeignKey(
        HRWorkflowDefinition, on_delete=models.PROTECT, related_name='instances'
    )
    employee = models.ForeignKey(
        EmployeeMaster, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='workflow_instances',
    )
    subject_type = models.CharField(max_length=80, db_index=True)
    subject_id = models.CharField(max_length=80, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    current_stage = models.ForeignKey(
        HRWorkflowStage, null=True, blank=True, on_delete=models.PROTECT,
        related_name='active_instances',
    )
    requested_by = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='hr_workflows_requested',
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'hr_workflow_instance'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['definition', 'subject_type', 'subject_id'],
                name='hr_workflow_subject_unique',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'current_stage'], name='hr_workflow_queue_idx'),
        ]

    def __str__(self):
        return f'{self.definition.code}/{self.subject_id}: {self.status}'


class HRWorkflowTask(TimeStampedModel):
    """Actionable approval task for the current workflow stage."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instance = models.ForeignKey(
        HRWorkflowInstance, on_delete=models.CASCADE, related_name='tasks'
    )
    stage = models.ForeignKey(HRWorkflowStage, on_delete=models.PROTECT, related_name='tasks')
    assigned_to = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='hr_workflow_tasks',
    )
    assigned_role_code = models.CharField(max_length=80, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='hr_workflow_tasks_decided',
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        db_table = 'hr_workflow_task'
        ordering = ['status', 'due_at']
        constraints = [
            models.UniqueConstraint(fields=['instance', 'stage'], name='hr_workflow_task_stage_unique'),
        ]

    def __str__(self):
        return f'{self.instance_id}/{self.stage.code}: {self.status}'


class HRWorkflowEvent(models.Model):
    """Immutable event stream for workflow audit and compliance reporting."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instance = models.ForeignKey(
        HRWorkflowInstance, on_delete=models.CASCADE, related_name='events'
    )
    event_type = models.CharField(max_length=50, db_index=True)
    actor = models.ForeignKey(
        'users.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='hr_workflow_events',
    )
    stage_code = models.CharField(max_length=80, blank=True)
    note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'hr_workflow_event'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.instance_id}: {self.event_type}'


# =============================================================================
# Performance, goals, and talent management
# =============================================================================

class PerformanceCycle(TimeStampedModel):
    STATUS_CHOICES = [('draft', 'Draft'), ('active', 'Active'), ('calibration', 'Calibration'), ('closed', 'Closed')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    start_date = models.DateField()
    end_date = models.DateField()
    self_review_due = models.DateField(null=True, blank=True)
    manager_review_due = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    goal_weight = models.DecimalField(max_digits=5, decimal_places=2, default=60)
    competency_weight = models.DecimalField(max_digits=5, decimal_places=2, default=40)
    configuration = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'hr_performance_cycle'
        ordering = ['-start_date']
        constraints = [models.UniqueConstraint(fields=['name', 'start_date'], name='hr_perf_cycle_name_start_unique')]

    def __str__(self):
        return self.name


class PerformanceGoal(TimeStampedModel):
    TYPE_CHOICES = [('individual', 'Individual'), ('team', 'Team'), ('kpi', 'KPI'), ('development', 'Development')]
    STATUS_CHOICES = [('draft', 'Draft'), ('pending', 'Pending Approval'), ('active', 'Active'), ('completed', 'Completed'), ('cancelled', 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cycle = models.ForeignKey(PerformanceCycle, on_delete=models.CASCADE, related_name='goals')
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='performance_goals')
    parent_goal = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='aligned_goals')
    goal_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='individual')
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True)
    metric_name = models.CharField(max_length=160, blank=True)
    target_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    current_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=40, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    progress = models.PositiveSmallIntegerField(default=0)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    success_criteria = models.TextField(blank=True)
    created_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='performance_goals_created')
    approved_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='performance_goals_approved')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hr_performance_goal'
        ordering = ['cycle', 'employee', 'due_date']
        indexes = [models.Index(fields=['employee', 'cycle', 'status'], name='hr_goal_employee_cycle_idx')]

    def __str__(self):
        return f'{self.employee}: {self.title}'


class GoalCheckIn(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    goal = models.ForeignKey(PerformanceGoal, on_delete=models.CASCADE, related_name='check_ins')
    progress = models.PositiveSmallIntegerField()
    current_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    note = models.TextField(blank=True)
    evidence = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'hr_goal_check_in'
        ordering = ['-created_at']


class PerformanceReview(TimeStampedModel):
    TYPE_CHOICES = [('self', 'Self Assessment'), ('manager', 'Manager Review'), ('peer', 'Peer Review'), ('direct_report', 'Direct Report'), ('calibration', 'Calibration')]
    STATUS_CHOICES = [('draft', 'Draft'), ('submitted', 'Submitted'), ('acknowledged', 'Acknowledged'), ('reopened', 'Reopened')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cycle = models.ForeignKey(PerformanceCycle, on_delete=models.CASCADE, related_name='reviews')
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='performance_reviews')
    reviewer = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='performance_reviews_given')
    review_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    goal_score = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    competency_score = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    overall_score = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    ratings = models.JSONField(default=dict, blank=True)
    key_achievements = models.TextField(blank=True)
    strengths = models.TextField(blank=True)
    areas_for_improvement = models.TextField(blank=True)
    goals_next_period = models.TextField(blank=True)
    overall_comments = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hr_performance_review'
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['cycle', 'employee', 'reviewer', 'review_type'], name='hr_review_rater_unique')]
        indexes = [models.Index(fields=['employee', 'cycle', 'review_type'], name='hr_review_employee_cycle_idx')]


class ContinuousFeedback(TimeStampedModel):
    TYPE_CHOICES = [('recognition', 'Recognition'), ('coaching', 'Coaching'), ('general', 'General Feedback')]
    VISIBILITY_CHOICES = [('employee', 'Employee and Management'), ('management', 'Management Only'), ('private', 'Author Only')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='continuous_feedback')
    author = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='hr_feedback_given')
    feedback_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='general')
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='employee')
    content = models.TextField()
    related_goal = models.ForeignKey(PerformanceGoal, null=True, blank=True, on_delete=models.SET_NULL, related_name='feedback')
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hr_continuous_feedback'
        ordering = ['-created_at']


class DevelopmentPlan(TimeStampedModel):
    STATUS_CHOICES = [('draft', 'Draft'), ('active', 'Active'), ('completed', 'Completed'), ('cancelled', 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='development_plans')
    cycle = models.ForeignKey(PerformanceCycle, null=True, blank=True, on_delete=models.SET_NULL, related_name='development_plans')
    title = models.CharField(max_length=220)
    career_aspiration = models.TextField(blank=True)
    target_role = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    start_date = models.DateField()
    target_date = models.DateField(null=True, blank=True)
    owner = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'hr_development_plan'
        ordering = ['-start_date']


class DevelopmentAction(TimeStampedModel):
    TYPE_CHOICES = [('training', 'Training'), ('mentoring', 'Mentoring'), ('assignment', 'Stretch Assignment'), ('certification', 'Certification'), ('coaching', 'Coaching')]
    STATUS_CHOICES = [('planned', 'Planned'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('cancelled', 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(DevelopmentPlan, on_delete=models.CASCADE, related_name='actions')
    action_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True)
    provider = models.CharField(max_length=160, blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='planned')
    completion_evidence = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = 'hr_development_action'
        ordering = ['due_date', 'created_at']


class TalentAssessment(TimeStampedModel):
    PERFORMANCE_CHOICES = [(1, 'Low'), (2, 'Moderate'), (3, 'High')]
    POTENTIAL_CHOICES = [(1, 'Low'), (2, 'Moderate'), (3, 'High')]
    RISK_CHOICES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High')]
    READINESS_CHOICES = [('ready_now', 'Ready Now'), ('one_year', 'Ready in 1 Year'), ('two_plus_years', 'Ready in 2+ Years'), ('not_applicable', 'Not Applicable')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cycle = models.ForeignKey(PerformanceCycle, on_delete=models.CASCADE, related_name='talent_assessments')
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='talent_assessments')
    performance = models.PositiveSmallIntegerField(choices=PERFORMANCE_CHOICES)
    potential = models.PositiveSmallIntegerField(choices=POTENTIAL_CHOICES)
    retention_risk = models.CharField(max_length=20, choices=RISK_CHOICES, default='low')
    readiness = models.CharField(max_length=30, choices=READINESS_CHOICES, default='not_applicable')
    critical_role = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    assessed_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'hr_talent_assessment'
        ordering = ['cycle', 'employee']
        constraints = [models.UniqueConstraint(fields=['cycle', 'employee'], name='hr_talent_cycle_employee_unique')]


class SuccessionPlan(TimeStampedModel):
    STATUS_CHOICES = [('draft', 'Draft'), ('active', 'Active'), ('closed', 'Closed')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role_title = models.CharField(max_length=180)
    department = models.CharField(max_length=120, blank=True)
    incumbent = models.ForeignKey(EmployeeMaster, null=True, blank=True, on_delete=models.SET_NULL, related_name='succession_plans_as_incumbent')
    criticality = models.CharField(max_length=20, choices=[('standard', 'Standard'), ('critical', 'Critical')], default='standard')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    risk_notes = models.TextField(blank=True)
    owner = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'hr_succession_plan'
        ordering = ['department', 'role_title']


class SuccessionCandidate(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(SuccessionPlan, on_delete=models.CASCADE, related_name='candidates')
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='succession_candidates')
    readiness = models.CharField(max_length=30, choices=TalentAssessment.READINESS_CHOICES)
    rank = models.PositiveSmallIntegerField(default=1)
    development_gaps = models.TextField(blank=True)

    class Meta:
        db_table = 'hr_succession_candidate'
        ordering = ['rank']
        constraints = [models.UniqueConstraint(fields=['plan', 'employee'], name='hr_successor_plan_employee_unique')]


class PromotionCase(TimeStampedModel):
    STATUS_CHOICES = [('draft', 'Draft'), ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='promotion_cases')
    current_title = models.CharField(max_length=160)
    proposed_title = models.CharField(max_length=160)
    proposed_grade = models.CharField(max_length=50, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    justification = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    requested_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL)
    workflow_instance = models.OneToOneField(HRWorkflowInstance, null=True, blank=True, on_delete=models.SET_NULL, related_name='promotion_case')

    class Meta:
        db_table = 'hr_promotion_case'
        ordering = ['-created_at']


# =============================================================================
# Shift, roster, and approved overtime management
# =============================================================================

class WorkShift(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_minutes = models.PositiveSmallIntegerField(default=60)
    crosses_midnight = models.BooleanField(default=False)
    color = models.CharField(max_length=20, default='#2563EB')
    is_active = models.BooleanField(default=True, db_index=True)
    configuration = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'hr_work_shift'
        ordering = ['start_time', 'code']

    def __str__(self):
        return f'{self.code} — {self.name}'


class ShiftRoster(TimeStampedModel):
    STATUS_CHOICES = [('draft', 'Draft'), ('published', 'Published'), ('locked', 'Locked'), ('cancelled', 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    department = models.CharField(max_length=120, blank=True)
    location = models.CharField(max_length=120, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'hr_shift_roster'
        ordering = ['-start_date']


class ShiftAssignment(TimeStampedModel):
    STATUS_CHOICES = [('scheduled', 'Scheduled'), ('worked', 'Worked'), ('absent', 'Absent'), ('leave', 'Leave'), ('cancelled', 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    roster = models.ForeignKey(ShiftRoster, on_delete=models.CASCADE, related_name='assignments')
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='shift_assignments')
    shift = models.ForeignKey(WorkShift, on_delete=models.PROTECT, related_name='assignments')
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    location = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'hr_shift_assignment'
        ordering = ['date', 'shift__start_time']
        constraints = [models.UniqueConstraint(fields=['employee', 'date'], name='hr_shift_employee_date_unique')]
        indexes = [models.Index(fields=['employee', 'date', 'status'], name='hr_shift_employee_date_idx')]


class OvertimeRequest(TimeStampedModel):
    STATUS_CHOICES = [('draft', 'Draft'), ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='overtime_requests')
    assignment = models.ForeignKey(ShiftAssignment, null=True, blank=True, on_delete=models.SET_NULL, related_name='overtime_requests')
    work_date = models.DateField(db_index=True)
    requested_hours = models.DecimalField(max_digits=5, decimal_places=2)
    approved_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    requested_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='overtime_requests_created')
    workflow_instance = models.OneToOneField(HRWorkflowInstance, null=True, blank=True, on_delete=models.SET_NULL, related_name='overtime_request')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hr_overtime_request'
        ordering = ['-work_date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'work_date'], condition=models.Q(status__in=['pending', 'approved']), name='hr_overtime_active_day_unique'),
        ]
        indexes = [models.Index(fields=['status', 'work_date'], name='hr_overtime_queue_idx')]


# =============================================================================
# Unified employee service requests
# =============================================================================

class EmployeeServiceRequest(TimeStampedModel):
    TYPE_CHOICES = [
        ('expense', 'Expense Reimbursement'), ('travel', 'Business Travel'),
        ('asset', 'Asset Request'), ('hr_helpdesk', 'HR Helpdesk'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'), ('pending', 'Pending Approval'),
        ('approved', 'Approved'), ('rejected', 'Rejected'),
        ('in_progress', 'In Progress'), ('fulfilled', 'Fulfilled'),
        ('cancelled', 'Cancelled'),
    ]
    PRIORITY_CHOICES = [('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('urgent', 'Urgent')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_number = models.CharField(max_length=32, unique=True, db_index=True, blank=True)
    request_type = models.CharField(max_length=24, choices=TYPE_CHOICES, db_index=True)
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='service_requests')
    title = models.CharField(max_length=220)
    description = models.TextField()
    category = models.CharField(max_length=100, blank=True)
    priority = models.CharField(max_length=16, choices=PRIORITY_CHOICES, default='normal')
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='AED')
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    destination = models.CharField(max_length=180, blank=True)
    cost_center = models.CharField(max_length=80, blank=True)
    details = models.JSONField(default=dict, blank=True)
    attachments = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='pending', db_index=True)
    requested_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='employee_service_requests')
    assigned_to = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='employee_service_requests_assigned')
    workflow_instance = models.OneToOneField(HRWorkflowInstance, null=True, blank=True, on_delete=models.SET_NULL, related_name='service_request')
    submitted_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)

    class Meta:
        db_table = 'hr_employee_service_request'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['employee', 'request_type', 'status'], name='hr_service_employee_idx'),
            models.Index(fields=['status', 'priority'], name='hr_service_queue_idx'),
        ]

    def save(self, *args, **kwargs):
        if not self.request_number:
            prefix = {'expense': 'EXP', 'travel': 'TRV', 'asset': 'AST', 'hr_helpdesk': 'HRD'}.get(self.request_type, 'REQ')
            self.request_number = f'{prefix}-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}'
        super().save(*args, **kwargs)


class EmployeeServiceRequestComment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(EmployeeServiceRequest, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey('users.User', null=True, on_delete=models.SET_NULL)
    body = models.TextField()
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'hr_employee_service_request_comment'
        ordering = ['created_at']


# =============================================================================
# Microsoft 365, grounded HR assistance, and data governance
# =============================================================================

class MicrosoftGraphConnection(TimeStampedModel):
    """Non-secret Microsoft Graph tenant configuration.

    Client secrets/certificates are deliberately environment-backed and are never
    written to the database or returned by the API.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, default='Microsoft 365')
    tenant_id = models.CharField(max_length=100)
    client_id = models.CharField(max_length=100)
    enabled = models.BooleanField(default=False, db_index=True)
    entra_sync_enabled = models.BooleanField(default=True)
    outlook_enabled = models.BooleanField(default=False)
    teams_enabled = models.BooleanField(default=False)
    sharepoint_enabled = models.BooleanField(default=False)
    sharepoint_site_id = models.CharField(max_length=220, blank=True)
    sharepoint_drive_id = models.CharField(max_length=220, blank=True)
    sharepoint_policy_folder = models.CharField(max_length=500, default='HR Policies')
    teams_app_id = models.CharField(max_length=100, blank=True)
    default_team_id = models.CharField(max_length=100, blank=True)
    mail_sender = models.EmailField(blank=True)
    last_health_check_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=24, default='not_configured')
    last_error = models.TextField(blank=True)
    created_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        db_table = 'hr_microsoft_graph_connection'
        ordering = ['name']


class MicrosoftGraphUserLink(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.OneToOneField(EmployeeMaster, on_delete=models.CASCADE, related_name='microsoft_graph_link')
    entra_object_id = models.CharField(max_length=100, unique=True, db_index=True)
    user_principal_name = models.EmailField(blank=True, db_index=True)
    account_enabled = models.BooleanField(default=True)
    raw_profile = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hr_microsoft_graph_user_link'


class HRPolicyDocument(TimeStampedModel):
    VISIBILITY_CHOICES = [('employees', 'All Employees'), ('managers', 'Managers and HR'), ('hr', 'HR Only')]
    STATUS_CHOICES = [('draft', 'Draft'), ('published', 'Published'), ('retired', 'Retired')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=240)
    category = models.CharField(max_length=100, db_index=True)
    jurisdiction = models.CharField(max_length=100, blank=True, db_index=True)
    version = models.CharField(max_length=40, default='1.0')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft', db_index=True)
    visibility = models.CharField(max_length=16, choices=VISIBILITY_CHOICES, default='employees', db_index=True)
    allowed_role_codes = models.JSONField(default=list, blank=True)
    content = models.TextField()
    source_url = models.URLField(max_length=1000, blank=True)
    sharepoint_item_id = models.CharField(max_length=220, blank=True, db_index=True)
    checksum = models.CharField(max_length=64, blank=True, db_index=True)
    effective_date = models.DateField(null=True, blank=True)
    review_date = models.DateField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    owner = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='owned_hr_policies')

    class Meta:
        db_table = 'hr_policy_document'
        ordering = ['category', 'title', '-version']
        constraints = [models.UniqueConstraint(fields=['title', 'version', 'jurisdiction'], name='hr_policy_title_version_jurisdiction_unique')]


class HRAssistantInteraction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('users.User', null=True, on_delete=models.SET_NULL, related_name='hr_assistant_interactions')
    employee = models.ForeignKey(EmployeeMaster, null=True, blank=True, on_delete=models.SET_NULL, related_name='assistant_interactions')
    question = models.TextField()
    answer = models.TextField()
    citations = models.JSONField(default=list, blank=True)
    model_name = models.CharField(max_length=80, default='extractive-grounded')
    grounded = models.BooleanField(default=False)
    refusal_reason = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'hr_assistant_interaction'
        ordering = ['-created_at']


class HRAuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    action = models.CharField(max_length=100, db_index=True)
    object_type = models.CharField(max_length=120, blank=True, db_index=True)
    object_id = models.CharField(max_length=120, blank=True, db_index=True)
    employee = models.ForeignKey(EmployeeMaster, null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_events')
    outcome = models.CharField(max_length=20, default='success', db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    previous_hash = models.CharField(max_length=64, blank=True)
    event_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'hr_audit_event'
        ordering = ['-created_at']


class HRConsentRecord(TimeStampedModel):
    STATUS_CHOICES = [('granted', 'Granted'), ('withdrawn', 'Withdrawn')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.CASCADE, related_name='consents')
    purpose = models.CharField(max_length=120, db_index=True)
    policy_version = models.CharField(max_length=40)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='granted')
    recorded_by = models.ForeignKey('users.User', null=True, on_delete=models.SET_NULL, related_name='+')
    evidence = models.JSONField(default=dict, blank=True)
    granted_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hr_consent_record'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['employee', 'purpose', 'status'], name='hr_consent_employee_idx')]


class HRPrivacyRequest(TimeStampedModel):
    TYPE_CHOICES = [('access', 'Access'), ('correction', 'Correction'), ('deletion', 'Deletion'), ('restriction', 'Restriction'), ('export', 'Portable Export')]
    STATUS_CHOICES = [('submitted', 'Submitted'), ('verified', 'Identity Verified'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('rejected', 'Rejected')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_number = models.CharField(max_length=32, unique=True, db_index=True, blank=True)
    employee = models.ForeignKey(EmployeeMaster, on_delete=models.PROTECT, related_name='privacy_requests')
    request_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    details = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='submitted', db_index=True)
    due_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_hr_privacy_requests')
    resolution = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'hr_privacy_request'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.request_number:
            self.request_number = f'PRV-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}'
        super().save(*args, **kwargs)


class HRRetentionPolicy(TimeStampedModel):
    ACTION_CHOICES = [('review', 'Review'), ('anonymize', 'Anonymize'), ('delete', 'Delete')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    data_category = models.CharField(max_length=100, unique=True)
    legal_basis = models.CharField(max_length=240)
    retention_days = models.PositiveIntegerField()
    disposition_action = models.CharField(max_length=16, choices=ACTION_CHOICES, default='review')
    legal_hold = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    updated_by = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        db_table = 'hr_retention_policy'
        ordering = ['data_category']


class LegacyEmployeeArchive(models.Model):
    """Read-only migration evidence retained after a legacy identity table retires."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_table = models.CharField(max_length=120, db_index=True)
    source_pk = models.CharField(max_length=120)
    canonical_employee = models.ForeignKey(
        EmployeeMaster, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='legacy_archives',
    )
    payload = models.JSONField(default=dict)
    retired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'hr_legacy_employee_archive'
        ordering = ['source_table', 'source_pk']
        constraints = [
            models.UniqueConstraint(fields=['source_table', 'source_pk'], name='hr_legacy_archive_source_unique'),
        ]
