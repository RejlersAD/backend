"""
Onboarding & Offboarding Models
Tracks employee lifecycle: joining, exit, equipment, documents, access provisioning
All soft-coded: status choices, equipment types, document types via config
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

# ═══════════════════════════════════════════════════════════════════════════
# Soft-Coded Configuration Constants
# ═══════════════════════════════════════════════════════════════════════════

# Onboarding Status
STATUS_INITIATED = 'initiated'
STATUS_DOCUMENTATION = 'documentation'
STATUS_EQUIPMENT = 'equipment'
STATUS_ACCESS_PROVISIONING = 'access_provisioning'
STATUS_TRAINING = 'training'
STATUS_COMPLETED = 'completed'
STATUS_CANCELLED = 'cancelled'

ONBOARDING_STATUS_CHOICES = [
    (STATUS_INITIATED, 'Initiated'),
    (STATUS_DOCUMENTATION, 'Documentation Collection'),
    (STATUS_EQUIPMENT, 'Equipment Assignment'),
    (STATUS_ACCESS_PROVISIONING, 'Access Provisioning'),
    (STATUS_TRAINING, 'Training & Orientation'),
    (STATUS_COMPLETED, 'Completed'),
    (STATUS_CANCELLED, 'Cancelled'),
]

# Offboarding Status
OFFBOARDING_STATUS_INITIATED = 'initiated'
OFFBOARDING_STATUS_ACCESS_REVOCATION = 'access_revocation'
OFFBOARDING_STATUS_EQUIPMENT_RETURN = 'equipment_return'
OFFBOARDING_STATUS_EXIT_INTERVIEW = 'exit_interview'
OFFBOARDING_STATUS_FINAL_SETTLEMENT = 'final_settlement'
OFFBOARDING_STATUS_COMPLETED = 'completed'
OFFBOARDING_STATUS_CANCELLED = 'cancelled'

OFFBOARDING_STATUS_CHOICES = [
    (OFFBOARDING_STATUS_INITIATED, 'Initiated'),
    (OFFBOARDING_STATUS_ACCESS_REVOCATION, 'Access Revocation'),
    (OFFBOARDING_STATUS_EQUIPMENT_RETURN, 'Equipment Return'),
    (OFFBOARDING_STATUS_EXIT_INTERVIEW, 'Exit Interview'),
    (OFFBOARDING_STATUS_FINAL_SETTLEMENT, 'Final Settlement'),
    (OFFBOARDING_STATUS_COMPLETED, 'Completed'),
    (OFFBOARDING_STATUS_CANCELLED, 'Cancelled'),
]

# Equipment Types
EQUIPMENT_TYPES = [
    ('laptop', 'Laptop'),
    ('desktop', 'Desktop Computer'),
    ('monitor', 'Monitor'),
    ('keyboard', 'Keyboard'),
    ('mouse', 'Mouse'),
    ('headset', 'Headset'),
    ('mobile', 'Mobile Phone'),
    ('access_card', 'Access Card'),
    ('badge', 'ID Badge'),
    ('keys', 'Office Keys'),
    ('other', 'Other'),
]

# Document Types
DOCUMENT_TYPES = [
    ('passport', 'Passport Copy'),
    ('visa', 'Visa'),
    ('emirates_id', 'Emirates ID'),
    ('driving_license', 'Driving License'),
    ('degree', 'Educational Certificates'),
    ('certificate', 'Professional Certificate'),
    ('experience', 'Experience Letters'),
    ('offer_letter', 'Signed Offer Letter'),
    ('contract', 'Employment Contract'),
    ('confidentiality', 'Confidentiality Agreement'),
    ('policy_acknowledgment', 'Policy Acknowledgment'),
    ('bank_details', 'Bank Account Details'),
    ('emergency_contact', 'Emergency Contact Form'),
    ('medical', 'Medical/Insurance Forms'),
    ('vaccination', 'Vaccination Certificate'),
    ('police_clearance', 'Police Clearance Certificate'),
    ('resignation', 'Resignation Letter'),
    ('clearance', 'Exit Clearance Form'),
    ('other', 'Other'),
]

# Access Types
ACCESS_TYPES = [
    ('email', 'Email Account'),
    ('active_directory', 'Active Directory'),
    ('erp', 'ERP System'),
    ('hr_system', 'HR System'),
    ('project_tools', 'Project Management Tools'),
    ('cloud_storage', 'Cloud Storage'),
    ('vpn', 'VPN Access'),
    ('building_access', 'Building Access'),
    ('parking', 'Parking Access'),
    ('other', 'Other'),
]

# Exit Reasons
EXIT_REASONS = [
    ('resignation', 'Voluntary Resignation'),
    ('termination', 'Termination'),
    ('contract_end', 'Contract Completion'),
    ('retirement', 'Retirement'),
    ('relocation', 'Relocation'),
    ('health', 'Health Reasons'),
    ('performance', 'Performance Issues'),
    ('redundancy', 'Redundancy'),
    ('other', 'Other'),
]


# ═══════════════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════════════

class OnboardingRecord(models.Model):
    """
    Onboarding tracker for new joiners
    Workflow: Initiated → Documentation → Equipment → Access → Training → Completed
    """
    # Employee Info
    employee_name = models.CharField(max_length=255)
    employee_email = models.EmailField(unique=True)
    employee_id = models.CharField(max_length=100, blank=True, null=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='onboarding_records')
    
    # Passport Photo (stored in S3)
    photo_file_path = models.CharField(max_length=500, blank=True, null=True, help_text='S3 key for passport photo')
    photo_url = models.URLField(max_length=1000, blank=True, null=True, help_text='Presigned S3 URL for photo')
    photo_file_size = models.IntegerField(null=True, blank=True, help_text='Photo file size in bytes')
    photo_mime_type = models.CharField(max_length=100, blank=True, null=True, help_text='Photo MIME type')
    photo_original_filename = models.CharField(max_length=255, blank=True, null=True, help_text='Original photo filename')
    
    # Position Info
    position = models.CharField(max_length=255)
    department = models.CharField(max_length=255)
    reporting_manager = models.CharField(max_length=255, blank=True, null=True)
    branch = models.CharField(max_length=50, default='RAD', choices=[('RAD', 'Rejlers Abu Dhabi'), ('RIN', 'Rejlers India')])
    
    # Timeline
    joining_date = models.DateField()
    initiated_date = models.DateTimeField(default=timezone.now)
    target_completion_date = models.DateField()
    actual_completion_date = models.DateTimeField(null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=50, choices=ONBOARDING_STATUS_CHOICES, default=STATUS_INITIATED)
    progress_percentage = models.IntegerField(default=0)
    
    # Tracking
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='onboarding_created')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='onboarding_assigned')
    
    # Notes
    notes = models.TextField(blank=True, null=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_record'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['joining_date']),
            models.Index(fields=['employee_email']),
        ]
    
    def __str__(self):
        return f"{self.employee_name} - {self.position} ({self.status})"


class OffboardingRecord(models.Model):
    """
    Offboarding tracker for exiting employees
    Workflow: Initiated → Access Revocation → Equipment Return → Exit Interview → Settlement → Completed
    """
    # Employee Info
    employee_name = models.CharField(max_length=255)
    employee_email = models.EmailField()
    employee_id = models.CharField(max_length=100, blank=True, null=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='offboarding_records')
    
    # Position Info
    position = models.CharField(max_length=255)
    department = models.CharField(max_length=255)
    reporting_manager = models.CharField(max_length=255, blank=True, null=True)
    branch = models.CharField(max_length=50, default='RAD', choices=[('RAD', 'Rejlers Abu Dhabi'), ('RIN', 'Rejlers India')])
    
    # Exit Info
    exit_reason = models.CharField(max_length=50, choices=EXIT_REASONS)
    exit_reason_detail = models.TextField(blank=True, null=True)
    last_working_day = models.DateField()
    notice_period_days = models.IntegerField(default=30)
    
    # Timeline
    initiated_date = models.DateTimeField(default=timezone.now)
    target_completion_date = models.DateField()
    actual_completion_date = models.DateTimeField(null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=50, choices=OFFBOARDING_STATUS_CHOICES, default=OFFBOARDING_STATUS_INITIATED)
    progress_percentage = models.IntegerField(default=0)
    
    # Tracking
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='offboarding_created')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='offboarding_assigned')
    
    # Notes
    notes = models.TextField(blank=True, null=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'offboarding_record'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['last_working_day']),
            models.Index(fields=['employee_email']),
        ]
    
    def __str__(self):
        return f"{self.employee_name} - Exit on {self.last_working_day} ({self.status})"


class Equipment(models.Model):
    """
    Equipment assigned to employees (onboarding) or returned (offboarding)
    """
    onboarding_record = models.ForeignKey(OnboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='equipment')
    offboarding_record = models.ForeignKey(OffboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='equipment')
    
    equipment_type = models.CharField(max_length=50, choices=EQUIPMENT_TYPES)
    item_name = models.CharField(max_length=255)
    serial_number = models.CharField(max_length=255, blank=True, null=True)
    asset_tag = models.CharField(max_length=100, blank=True, null=True)
    
    assigned_date = models.DateField(null=True, blank=True)
    returned_date = models.DateField(null=True, blank=True)
    
    condition = models.CharField(max_length=50, choices=[
        ('new', 'New'),
        ('good', 'Good'),
        ('fair', 'Fair'),
        ('damaged', 'Damaged'),
    ], default='good')
    
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_equipment'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.equipment_type} - {self.item_name}"


class Document(models.Model):
    """
    Documents collected during onboarding or offboarding
    Supports file uploads to AWS S3 (certificates, Emirates ID, driving license, etc.)
    """
    onboarding_record = models.ForeignKey(OnboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    offboarding_record = models.ForeignKey(OffboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    
    document_type = models.CharField(max_length=50, choices=DOCUMENT_TYPES)
    document_name = models.CharField(max_length=255)
    
    # S3 storage fields
    file_path = models.CharField(max_length=500, blank=True, null=True, help_text="S3 key path for the uploaded file")
    file_url = models.URLField(max_length=1000, blank=True, null=True, help_text="Presigned URL or public URL")
    file_size = models.IntegerField(null=True, blank=True, help_text="File size in bytes")
    file_mime_type = models.CharField(max_length=100, blank=True, null=True)
    original_filename = models.CharField(max_length=255, blank=True, null=True)
    
    submitted = models.BooleanField(default=False)
    verified = models.BooleanField(default=False)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='onboarding_documents_verified')
    verified_date = models.DateTimeField(null=True, blank=True)
    
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_document'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.document_type} - {self.document_name}"


class AccessProvisioning(models.Model):
    """
    IT/System access provisioned or revoked
    """
    onboarding_record = models.ForeignKey(OnboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='access_records')
    offboarding_record = models.ForeignKey(OffboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='access_records')
    
    access_type = models.CharField(max_length=50, choices=ACCESS_TYPES)
    access_name = models.CharField(max_length=255)
    account_username = models.CharField(max_length=255, blank=True, null=True)
    
    provisioned = models.BooleanField(default=False)
    provisioned_date = models.DateTimeField(null=True, blank=True)
    
    revoked = models.BooleanField(default=False)
    revoked_date = models.DateTimeField(null=True, blank=True)
    
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='access_assigned')
    
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_access'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.access_type} - {self.access_name}"


class Checklist(models.Model):
    """
    Custom checklist items for onboarding/offboarding
    """
    onboarding_record = models.ForeignKey(OnboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='checklist_items')
    offboarding_record = models.ForeignKey(OffboardingRecord, on_delete=models.CASCADE, null=True, blank=True, related_name='checklist_items')
    
    task_name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    
    completed = models.BooleanField(default=False)
    completed_date = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='checklist_completed')
    
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=20, choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], default='medium')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'onboarding_checklist'
        ordering = ['due_date', '-priority', 'task_name']
    
    def __str__(self):
        return f"{self.task_name} ({'Completed' if self.completed else 'Pending'})"


# ═══════════════════════════════════════════════════════════════════════════
# EXIT/RESIGNATION WORKFLOW MODELS (Smart Exit Management System)
# ═══════════════════════════════════════════════════════════════════════════

# Exit Request Types (Soft-coded)
EXIT_REQUEST_TYPES = [
    ('resignation', 'Voluntary Resignation'),
    ('termination', 'Termination'),
    ('contract_end', 'Contract Completion'),
    ('retirement', 'Retirement'),
    ('mutual_separation', 'Mutual Separation'),
    ('absconding', 'Absconding'),
]

# Approval Status (Soft-coded)
APPROVAL_STATUS = [
    ('pending', 'Pending'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]

# Exit Overall Status (Soft-coded)
EXIT_OVERALL_STATUS = [
    ('pending_manager', 'Pending Manager Approval'),
    ('pending_hr', 'Pending HR Approval'),
    ('approved', 'Approved - Exit Process Can Begin'),
    ('rejected', 'Rejected'),
    ('withdrawn', 'Withdrawn by Employee'),
    ('processing', 'Exit Process in Progress'),
    ('completed', 'Exit Completed'),
]

# Exit Process Status (Soft-coded)
EXIT_PROCESS_STATUS = [
    ('not_started', 'Not Started'),
    ('access_revocation', 'Access Revocation in Progress'),
    ('equipment_return', 'Equipment Return in Progress'),
    ('exit_interview', 'Exit Interview Scheduled/Completed'),
    ('clearance', 'Department Clearances in Progress'),
    ('settlement', 'Final Settlement Processing'),
    ('completed', 'All Exit Activities Completed'),
]

# Activity Types (Soft-coded)
EXIT_ACTIVITY_TYPES = [
    ('request_submitted', 'Exit Request Submitted'),
    ('manager_notified', 'Manager Notified'),
    ('manager_approved', 'Manager Approved'),
    ('manager_rejected', 'Manager Rejected'),
    ('manager_commented', 'Manager Added Comments'),
    ('hr_notified', 'HR Notified'),
    ('hr_approved', 'HR Approved'),
    ('hr_rejected', 'HR Rejected'),
    ('hr_commented', 'HR Added Comments'),
    ('lwd_adjusted', 'Last Working Day Adjusted'),
    ('process_initiated', 'Exit Process Initiated'),
    ('access_revoked', 'System Access Revoked'),
    ('equipment_returned', 'Equipment Returned'),
    ('exit_interview_scheduled', 'Exit Interview Scheduled'),
    ('exit_interview_completed', 'Exit Interview Completed'),
    ('clearance_completed', 'Department Clearance Completed'),
    ('settlement_processed', 'Final Settlement Processed'),
    ('request_withdrawn', 'Request Withdrawn'),
    ('offboarding_created', 'Offboarding Record Created'),
    ('status_changed', 'Status Changed'),
    ('document_uploaded', 'Document Uploaded'),
    ('email_sent', 'Email Notification Sent'),
    ('other', 'Other Activity'),
]

# Clearance Departments (Soft-coded)
CLEARANCE_DEPARTMENTS = [
    ('IT', 'Information Technology'),
    ('HR', 'Human Resources'),
    ('Finance', 'Finance & Accounts'),
    ('Admin', 'Administration'),
    ('Security', 'Security'),
    ('Library', 'Library'),
    ('Facilities', 'Facilities Management'),
    ('Project', 'Project Department'),
]

# Clearance Status (Soft-coded)
CLEARANCE_STATUS = [
    ('pending', 'Pending'),
    ('cleared', 'Cleared'),
    ('pending_action', 'Pending Employee Action'),
]


class ExitRequest(models.Model):
    """
    Smart Exit/Resignation Request with Multi-Level Approval Workflow
    
    Workflow: Employee → Manager → HR → Exit Process
    Features:
    - Flexible notice period management
    - Multi-level approval (Manager + HR)
    - Activity tracking
    - Integration with OffboardingRecord
    """
    # Employee Information
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='exit_requests',
        help_text='Employee requesting exit'
    )
    employee_name = models.CharField(max_length=255, help_text='Employee full name (cached)')
    employee_email = models.EmailField(help_text='Employee email (cached)')
    employee_id = models.CharField(max_length=100, blank=True, null=True, help_text='Employee ID (cached)')
    position = models.CharField(max_length=255, help_text='Job position (cached)')
    department = models.CharField(max_length=255, help_text='Department (cached)')
    reporting_manager = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='managed_exit_requests',
        help_text='Direct reporting manager'
    )
    
    # Exit Request Details
    request_type = models.CharField(
        max_length=50, 
        choices=EXIT_REQUEST_TYPES, 
        default='resignation',
        help_text='Type of exit request'
    )
    exit_reason = models.CharField(max_length=100, help_text='Primary reason for exit')
    exit_reason_detail = models.TextField(
        blank=True, 
        null=True, 
        help_text='Detailed explanation (optional)'
    )
    resignation_letter = models.CharField(
        max_length=500, 
        blank=True, 
        null=True, 
        help_text='S3 path to resignation letter file'
    )
    resignation_letter_url = models.URLField(
        max_length=1000, 
        blank=True, 
        null=True, 
        help_text='Presigned URL for resignation letter'
    )
    
    # Notice Period Management
    proposed_last_working_day = models.DateField(
        help_text='Employee\'s proposed last working day'
    )
    notice_period_days = models.IntegerField(
        help_text='Actual notice period in days (flexible)'
    )
    standard_notice_period = models.IntegerField(
        default=30, 
        help_text='Standard notice period per company policy'
    )
    notice_period_buyout = models.BooleanField(
        default=False, 
        help_text='Whether company is buying out notice period'
    )
    notice_period_buyout_days = models.IntegerField(
        default=0, 
        help_text='Number of days being bought out'
    )
    
    # Manager Approval
    manager_approval_status = models.CharField(
        max_length=20, 
        choices=APPROVAL_STATUS, 
        default='pending',
        help_text='Manager\'s approval status'
    )
    manager_approved_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='exit_requests_manager_approved',
        help_text='Manager who approved/rejected'
    )
    manager_approval_date = models.DateTimeField(
        null=True, 
        blank=True, 
        help_text='When manager approved/rejected'
    )
    manager_comments = models.TextField(
        blank=True, 
        null=True, 
        help_text='Manager\'s comments/feedback'
    )
    
    # HR Approval
    hr_approval_status = models.CharField(
        max_length=20, 
        choices=APPROVAL_STATUS, 
        default='pending',
        help_text='HR\'s approval status'
    )
    hr_approved_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='exit_requests_hr_approved',
        help_text='HR person who approved/rejected'
    )
    hr_approval_date = models.DateTimeField(
        null=True, 
        blank=True, 
        help_text='When HR approved/rejected'
    )
    hr_comments = models.TextField(
        blank=True, 
        null=True, 
        help_text='HR\'s comments/feedback'
    )
    final_approved_lwd = models.DateField(
        null=True, 
        blank=True, 
        help_text='Final LWD approved by HR (can differ from proposed)'
    )
    
    # Overall Status
    overall_status = models.CharField(
        max_length=30, 
        choices=EXIT_OVERALL_STATUS, 
        default='pending_manager',
        help_text='Overall request status',
        db_index=True
    )
    exit_process_status = models.CharField(
        max_length=30, 
        choices=EXIT_PROCESS_STATUS, 
        default='not_started',
        help_text='Status of exit process activities'
    )
    
    # Exit Interview
    exit_interview_completed = models.BooleanField(
        default=False, 
        help_text='Whether exit interview was completed'
    )
    exit_interview_date = models.DateTimeField(
        null=True, 
        blank=True, 
        help_text='When exit interview was conducted'
    )
    exit_interview_conducted_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='exit_interviews_conducted',
        help_text='HR person who conducted interview'
    )
    exit_interview_feedback = models.TextField(
        blank=True, 
        null=True, 
        help_text='Feedback from exit interview'
    )
    
    # Withdrawal
    withdrawn_at = models.DateTimeField(
        null=True, 
        blank=True, 
        help_text='When request was withdrawn'
    )
    withdrawal_reason = models.TextField(
        blank=True, 
        null=True, 
        help_text='Reason for withdrawal'
    )
    
    # Integration
    offboarding_record = models.OneToOneField(
        'OffboardingRecord',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='exit_request',
        help_text='Linked offboarding record (created when approved)'
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'exit_request'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['overall_status']),
            models.Index(fields=['proposed_last_working_day']),
            models.Index(fields=['user']),
            models.Index(fields=['reporting_manager']),
            models.Index(fields=['manager_approval_status']),
            models.Index(fields=['hr_approval_status']),
        ]
    
    def __str__(self):
        return f"{self.employee_name} - {self.get_request_type_display()} ({self.overall_status})"
    
    def calculate_notice_days(self):
        """Calculate actual notice period days from submission to proposed LWD"""
        from datetime import datetime
        created_date = self.created_at.date() if isinstance(self.created_at, datetime) else self.created_at
        return (self.proposed_last_working_day - created_date).days
    
    def is_pending_manager_approval(self):
        """Check if waiting for manager approval"""
        return self.manager_approval_status == 'pending' and self.overall_status == 'pending_manager'
    
    def is_pending_hr_approval(self):
        """Check if waiting for HR approval"""
        return self.hr_approval_status == 'pending' and self.overall_status == 'pending_hr'
    
    def can_withdraw(self):
        """Check if request can be withdrawn"""
        return self.overall_status in ['pending_manager', 'pending_hr'] and not self.withdrawn_at


class ExitActivity(models.Model):
    """
    Activity Log for Exit Requests
    Tracks all actions and status changes throughout the exit workflow
    """
    exit_request = models.ForeignKey(
        ExitRequest,
        on_delete=models.CASCADE,
        related_name='activities',
        help_text='Related exit request'
    )
    activity_type = models.CharField(
        max_length=50,
        choices=EXIT_ACTIVITY_TYPES,
        help_text='Type of activity'
    )
    activity_description = models.TextField(
        help_text='Description of what happened'
    )
    performed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='exit_activities_performed',
        help_text='User who performed this activity'
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text='Additional flexible data (e.g., old/new values, file references)'
    )
    activity_date = models.DateTimeField(
        auto_now_add=True,
        help_text='When this activity occurred'
    )
    
    class Meta:
        db_table = 'exit_activity'
        ordering = ['-activity_date']
        indexes = [
            models.Index(fields=['exit_request', '-activity_date']),
            models.Index(fields=['activity_type']),
        ]
    
    def __str__(self):
        return f"{self.exit_request.employee_name} - {self.get_activity_type_display()}"


class ExitClearance(models.Model):
    """
    Department Clearance Tracking
    Tracks clearance from different departments (IT, Finance, HR, etc.)
    """
    exit_request = models.ForeignKey(
        ExitRequest,
        on_delete=models.CASCADE,
        related_name='clearances',
        help_text='Related exit request'
    )
    department = models.CharField(
        max_length=50,
        choices=CLEARANCE_DEPARTMENTS,
        help_text='Department providing clearance'
    )
    clearance_status = models.CharField(
        max_length=20,
        choices=CLEARANCE_STATUS,
        default='pending',
        help_text='Current clearance status'
    )
    cleared_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clearances_provided',
        help_text='Department representative who cleared'
    )
    clearance_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When clearance was provided'
    )
    pending_items = models.TextField(
        blank=True,
        null=True,
        help_text='Items pending (e.g., "Return laptop, Clear dues")'
    )
    comments = models.TextField(
        blank=True,
        null=True,
        help_text='Additional comments from department'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'exit_clearance'
        ordering = ['department']
        unique_together = ['exit_request', 'department']
        indexes = [
            models.Index(fields=['exit_request', 'clearance_status']),
            models.Index(fields=['department']),
        ]
    
    def __str__(self):
        return f"{self.exit_request.employee_name} - {self.get_department_display()} ({self.clearance_status})"


class NoticePeriodPolicy(models.Model):
    """
    Notice Period Policy Configuration
    Defines standard notice periods based on designation, department, etc.
    """
    designation_level = models.CharField(
        max_length=100,
        help_text='Designation level (e.g., junior, senior, manager, director)'
    )
    department = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Department-specific policy (leave blank for company-wide)'
    )
    standard_notice_days = models.IntegerField(
        default=30,
        help_text='Standard notice period in days'
    )
    minimum_notice_days = models.IntegerField(
        default=15,
        help_text='Minimum acceptable notice period'
    )
    buyout_allowed = models.BooleanField(
        default=False,
        help_text='Whether company allows notice period buyout'
    )
    buyout_calculation = models.CharField(
        max_length=50,
        choices=[
            ('full_salary', 'Full Salary'),
            ('basic_salary', 'Basic Salary Only'),
            ('pro_rata', 'Pro-rata Based on Days'),
        ],
        default='full_salary',
        help_text='How to calculate buyout amount'
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this policy is currently active'
    )
    effective_from = models.DateField(
        help_text='Date from which this policy is effective'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'notice_period_policy'
        ordering = ['-effective_from', 'designation_level']
        indexes = [
            models.Index(fields=['designation_level']),
            models.Index(fields=['department']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        dept_info = f" ({self.department})" if self.department else " (Company-wide)"
        return f"{self.designation_level}{dept_info} - {self.standard_notice_days} days"
