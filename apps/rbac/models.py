"""
RBAC Models - Enterprise Role-Based Access Control
Designed for regulated Oil & Gas environment
"""
import os
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from apps.core.models import TimeStampedModel


def get_profile_photo_storage():
    """Return AvatarStorage (S3) when USE_S3 is enabled, otherwise local FileSystemStorage.
    Used as a callable for the profile_photo ImageField so the correct backend is
    selected at runtime without a hard dependency on boto3.
    """
    if os.environ.get('USE_S3', 'False').lower() == 'true':
        try:
            from apps.core.storage_backends import AvatarStorage
            return AvatarStorage()
        except Exception:
            pass
    from django.core.files.storage import FileSystemStorage
    from django.conf import settings
    return FileSystemStorage(location=str(getattr(settings, 'MEDIA_ROOT', 'media')))

User = get_user_model()


class Organization(TimeStampedModel):
    """
    Multi-tenant organization model
    Each user belongs to one organization
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    
    # Contact information
    primary_contact_name = models.CharField(max_length=255, blank=True)
    primary_contact_email = models.EmailField(blank=True)
    primary_contact_phone = models.CharField(max_length=20, blank=True)
    
    # Address
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    
    # S3 storage configuration
    s3_bucket_name = models.CharField(max_length=255, blank=True)
    s3_region = models.CharField(max_length=50, default='us-east-1')
    
    class Meta:
        db_table = 'rbac_organizations'
        verbose_name = 'Organization'
        verbose_name_plural = 'Organizations'
        ordering = ['name']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name


class Module(TimeStampedModel):
    """
    Application modules/features that can be enabled/disabled per role
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    icon = models.CharField(max_length=50, blank=True)
    order = models.IntegerField(default=0)
    
    class Meta:
        db_table = 'rbac_modules'
        verbose_name = 'Module'
        verbose_name_plural = 'Modules'
        ordering = ['order', 'name']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name


class Permission(TimeStampedModel):
    """
    Granular permissions for actions within modules
    """
    ACTION_CHOICES = [
        ('create', 'Create'),
        ('read', 'Read'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('approve', 'Approve'),
        ('export', 'Export'),
        ('import', 'Import'),
        ('execute', 'Execute'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='permissions')
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'rbac_permissions'
        verbose_name = 'Permission'
        verbose_name_plural = 'Permissions'
        ordering = ['module', 'action']
        unique_together = ['module', 'code']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['module', 'action']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.module.name}: {self.name}"


class Role(TimeStampedModel):
    """
    User roles with hierarchical structure
    """
    ROLE_LEVEL_CHOICES = [
        (1, 'Super Administrator'),
        (2, 'Admin'),
        (3, 'Manager'),
        (4, 'Engineer'),
        (5, 'Reviewer'),
        (6, 'Viewer'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    level = models.IntegerField(choices=ROLE_LEVEL_CHOICES, default=6)
    is_active = models.BooleanField(default=True)
    is_system_role = models.BooleanField(default=False)  # Cannot be deleted
    # False once an admin manually edits this role's modules/permissions via the UI —
    # excludes it from the destructive deploy-time ROLE_MODULE_POLICY resync (sync_role_modules).
    auto_sync_enabled = models.BooleanField(default=True)
    
    # Permissions
    permissions = models.ManyToManyField(
        Permission,
        through='RolePermission',
        related_name='roles'
    )
    
    # Module access
    modules = models.ManyToManyField(
        Module,
        through='RoleModule',
        related_name='roles'
    )
    
    class Meta:
        db_table = 'rbac_roles'
        verbose_name = 'Role'
        verbose_name_plural = 'Roles'
        ordering = ['level', 'name']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['level']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name
    
    def has_permission(self, permission_code):
        """Check if role has specific permission"""
        return self.permissions.filter(code=permission_code, is_active=True).exists()
    
    def has_module_access(self, module_code):
        """Check if role has access to module"""
        return self.modules.filter(code=module_code, is_active=True).exists()


class RolePermission(TimeStampedModel):
    """
    Many-to-many relationship between roles and permissions
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        db_table = 'rbac_role_permissions'
        unique_together = ['role', 'permission']
        indexes = [
            models.Index(fields=['role', 'permission']),
        ]
    
    def __str__(self):
        return f"{self.role.name} - {self.permission.name}"


class RoleModule(TimeStampedModel):
    """
    Many-to-many relationship between roles and modules
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    module = models.ForeignKey(Module, on_delete=models.CASCADE)
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        db_table = 'rbac_role_modules'
        unique_together = ['role', 'module']
        indexes = [
            models.Index(fields=['role', 'module']),
        ]
    
    def __str__(self):
        return f"{self.role.name} - {self.module.name}"


class UserProfile(TimeStampedModel):
    """
    Extended user profile with organization and RBAC
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('suspended', 'Suspended'),
        ('pending', 'Pending'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='rbac_profile')
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name='users'
    )
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    is_mfa_enabled = models.BooleanField(default=False)
    
    # Roles
    roles = models.ManyToManyField(
        Role,
        through='UserRole',
        related_name='user_profiles'
    )
    
    # Metadata
    employee_id = models.CharField(max_length=50, blank=True)
    department = models.CharField(max_length=100, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)  # For email verification tokens, etc.
    manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subordinates'
    )
    
    # Login tracking
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    failed_login_attempts = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    
    # Password policy
    must_change_password = models.BooleanField(
        default=False, 
        help_text="User must change password on next login"
    )
    
    # Profile customization
    profile_photo = models.ImageField(
        upload_to='profile_photos/',
        storage=get_profile_photo_storage,
        null=True,
        blank=True,
        help_text="User profile photo — stored in S3 (production) or local media (dev)"
    )
    phone = models.CharField(max_length=20, blank=True)
    bio = models.TextField(blank=True, max_length=500)
    location = models.CharField(max_length=100, blank=True)
    
    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deleted_profiles'
    )
    
    class Meta:
        db_table = 'rbac_user_profiles'
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['employee_id']),
            models.Index(fields=['is_deleted']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.organization.name}"

    def save(self, *args, **kwargs):
        # Normalise employee_id so it matches the biometric employee_code
        # stored in TimesheetEvent / DailyAttendanceSummary / EmployeeLeaveRecord.
        # Prevents lookup misses caused by trailing spaces entered via the UI.
        if self.employee_id:
            try:
                from apps.timesheet.identity import norm_code
                self.employee_id = norm_code(self.employee_id)
            except Exception:
                self.employee_id = str(self.employee_id).strip()
        super().save(*args, **kwargs)
    
    def is_super_admin(self):
        """True for Django is_superuser or an active super_admin role — bypasses all module/permission gating."""
        if getattr(self.user, 'is_superuser', False):
            return True
        from apps.rbac.models import UserRole
        return UserRole.objects.filter(
            user_profile=self, role__code='super_admin', role__is_active=True
        ).exists()

    def has_permission(self, permission_code):
        """Check if user has specific permission through any active role"""
        if self.is_super_admin():
            return True
        from apps.rbac.models import UserRole
        user_role_ids = UserRole.objects.filter(
            user_profile=self,
            role__is_active=True
        ).values_list('role_id', flat=True)
        return Permission.objects.filter(
            roles__id__in=user_role_ids,
            code=permission_code,
            is_active=True
        ).exists()
    
    def has_module_access(self, module_code):
        """
        Check if user has access to module through any active role
        SOFT-CODED: Returns False if module is disabled by MODULE_FEATURE_FLAGS
        """
        from apps.rbac.rbac_config import is_module_enabled
        
        # Check if module is globally disabled
        if not is_module_enabled(module_code):
            return False

        if self.is_super_admin():
            return True
        
        from apps.rbac.models import UserRole
        user_role_ids = UserRole.objects.filter(
            user_profile=self,
            role__is_active=True
        ).values_list('role_id', flat=True)
        return Module.objects.filter(
            roles__id__in=user_role_ids,
            code=module_code,
            is_active=True
        ).exists()
    
    def get_all_permissions(self):
        """Get all permissions from all assigned roles (with caching)"""
        from django.core.cache import cache
        cache_key = f'user_permissions_{self.id}'
        permissions = cache.get(cache_key)
        
        if permissions is None:
            if self.is_super_admin():
                permissions = list(Permission.objects.filter(is_active=True))
            else:
                permissions = list(Permission.objects.filter(
                    roles__in=self.roles.filter(is_active=True),
                    is_active=True
                ).distinct())
            # Cache for 5 minutes
            cache.set(cache_key, permissions, 300)
        
        return permissions
    
    def get_all_modules(self):
        """
        Get all accessible modules from all assigned roles (with caching)
        SOFT-CODED: Filters out modules disabled by MODULE_FEATURE_FLAGS
        """
        from django.core.cache import cache
        from apps.rbac.rbac_config import is_module_enabled
        
        cache_key = f'user_modules_{self.id}'
        modules = cache.get(cache_key)
        
        if modules is None:
            if self.is_super_admin():
                # Bypass role-based gating entirely — super admin sees every active module
                # regardless of how sparsely RoleModule rows are seeded in this environment's DB.
                modules = list(Module.objects.filter(is_active=True))
            else:
                # Get role IDs through UserRole relationship — only active roles
                user_role_ids = UserRole.objects.filter(
                    user_profile=self,
                    role__is_active=True
                ).values_list('role_id', flat=True)
                
                # Get modules linked to these roles through RoleModule
                modules = list(Module.objects.filter(
                    rolemodule__role_id__in=user_role_ids,
                    is_active=True
                ).distinct())

            # Soft-coded global access modules (for all authenticated users)
            try:
                from apps.rbac.discipline_config import DisciplineAccessConfig
                global_codes = DisciplineAccessConfig.get_globally_enabled_module_codes()
                if global_codes:
                    global_modules = list(Module.objects.filter(code__in=global_codes, is_active=True))
                    existing_ids = {m.id for m in modules}
                    for mod in global_modules:
                        if mod.id not in existing_ids:
                            modules.append(mod)
            except Exception:
                # Non-fatal: keep role-based modules if config resolution fails
                pass
            
            # Filter out disabled modules based on feature flags
            modules = [m for m in modules if is_module_enabled(m.code)]

            # Cache for 60 seconds — short TTL so role changes propagate quickly
            cache.set(cache_key, modules, 60)
        
        return modules


class UserRole(TimeStampedModel):
    """
    Many-to-many relationship between users and roles
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    is_primary = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'rbac_user_roles'
        unique_together = ['user_profile', 'role']
        indexes = [
            models.Index(fields=['user_profile', 'role']),
            models.Index(fields=['is_primary']),
        ]
    
    def __str__(self):
        return f"{self.user_profile.user.email} - {self.role.name}"


class UserStorage(TimeStampedModel):
    """
    Track user file storage in S3
    """
    FILE_TYPE_CHOICES = [
        ('document', 'Document'),
        ('image', 'Image'),
        ('drawing', 'P&ID Drawing'),
        ('report', 'Report'),
        ('model', 'AI Model'),
        ('other', 'Other'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='files')
    
    # File metadata
    filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=20, choices=FILE_TYPE_CHOICES)
    file_size = models.BigIntegerField()  # Size in bytes
    mime_type = models.CharField(max_length=100)
    
    # S3 path
    s3_bucket = models.CharField(max_length=255)
    s3_key = models.CharField(max_length=1024)  # Full S3 path
    s3_region = models.CharField(max_length=50)
    
    # Checksum for integrity
    md5_checksum = models.CharField(max_length=32, blank=True)
    
    # Access tracking
    download_count = models.IntegerField(default=0)
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    
    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'rbac_user_storage'
        verbose_name = 'User Storage'
        verbose_name_plural = 'User Storage'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user_profile', 'file_type']),
            models.Index(fields=['s3_bucket', 's3_key']),
            models.Index(fields=['is_deleted']),
        ]
    
    def __str__(self):
        return f"{self.filename} - {self.user_profile.user.email}"
    
    @property
    def s3_path(self):
        """Full S3 path"""
        return f"s3://{self.s3_bucket}/{self.s3_key}"


class EngineerProfile(TimeStampedModel):
    """
    Dedicated engineering competency & project-assignment profile for each user.
    One-to-one with UserProfile — stored in its own DB table (rbac_engineer_profiles).
    """
    EXPERTISE_CHOICES = [
        ('junior',    'Junior'),
        ('mid',       'Mid-Level'),
        ('senior',    'Senior'),
        ('principal', 'Principal'),
        ('lead',      'Lead'),
        ('manager',   'Engineering Manager'),
    ]
    AVAILABILITY_CHOICES = [
        ('available',  'Available'),
        ('partial',    'Partially Available'),
        ('busy',       'Fully Committed'),
        ('on_leave',   'On Leave'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.OneToOneField(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='engineer_profile',
    )

    # Competency
    expertise_level           = models.CharField(max_length=20, choices=EXPERTISE_CHOICES, blank=True)
    years_experience          = models.PositiveIntegerField(default=0)
    engineering_disciplines   = models.JSONField(default=list, blank=True)   # ["Process", "Piping", …]
    technical_skills          = models.JSONField(default=list, blank=True)   # [{"name": "HYSYS", "proficiency": 4}, …]
    languages                 = models.JSONField(default=list, blank=True)   # ["English", "Arabic"]
    certifications            = models.JSONField(default=list, blank=True)   # [{name, issuer, year, expiry_date, id}, …]

    # Availability
    availability_status       = models.CharField(max_length=20, choices=AVAILABILITY_CHOICES, default='available')
    availability_percentage   = models.PositiveIntegerField(default=100)
    next_available_date       = models.DateField(null=True, blank=True)
    max_concurrent_projects   = models.PositiveIntegerField(default=2)
    preferred_project_types   = models.JSONField(default=list, blank=True)  # ["FEED", "Greenfield …"]

    # Current project assignments (management visibility)
    current_projects          = models.JSONField(default=list, blank=True)  # [{name, client, role, allocation, …}, …]

    class Meta:
        db_table = 'rbac_engineer_profiles'
        verbose_name = 'Engineer Profile'
        verbose_name_plural = 'Engineer Profiles'
        indexes = [
            models.Index(fields=['expertise_level']),
            models.Index(fields=['availability_status']),
        ]

    def __str__(self):
        return f"EngineerProfile({self.user_profile.user.email})"

    def to_dict(self):
        """Serialise to the same shape the frontend expects."""
        return {
            'expertise_level':          self.expertise_level,
            'years_experience':         self.years_experience,
            'engineering_disciplines':  self.engineering_disciplines,
            'technical_skills':         self.technical_skills,
            'languages':                self.languages,
            'certifications':           self.certifications,
            'availability_status':      self.availability_status,
            'availability_percentage':  self.availability_percentage,
            'next_available_date':      str(self.next_available_date) if self.next_available_date else '',
            'max_concurrent_projects':  self.max_concurrent_projects,
            'preferred_project_types':  self.preferred_project_types,
            'current_projects':         self.current_projects,
        }


class AuditLog(TimeStampedModel):
    """
    Comprehensive audit logging for compliance
    """
    ACTION_CHOICES = [
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('create', 'Create'),
        ('read', 'Read'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('role_assign', 'Role Assign'),
        ('role_revoke', 'Role Revoke'),
        ('permission_grant', 'Permission Grant'),
        ('permission_revoke', 'Permission Revoke'),
        ('file_upload', 'File Upload'),
        ('file_download', 'File Download'),
        ('file_delete', 'File Delete'),
        ('password_change', 'Password Change'),
        ('password_reset', 'Password Reset'),
        ('mfa_enable', 'MFA Enable'),
        ('mfa_disable', 'MFA Disable'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Who
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    user_email = models.EmailField()  # Denormalized for historical record
    
    # What
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    resource_type = models.CharField(max_length=100)  # Model name
    resource_id = models.UUIDField(null=True, blank=True)
    resource_repr = models.CharField(max_length=255, blank=True)  # String representation
    
    # When & Where
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    # Details
    changes = models.JSONField(default=dict, blank=True)  # Before/after values
    metadata = models.JSONField(default=dict, blank=True)  # Additional context
    
    # Result
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    
    class Meta:
        db_table = 'rbac_audit_logs'
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action', 'timestamp']),
            models.Index(fields=['resource_type', 'resource_id']),
            models.Index(fields=['ip_address']),
            models.Index(fields=['-timestamp']),
        ]
    
    def __str__(self):
        return f"{self.user_email} - {self.action} - {self.timestamp}"


class AccessRequest(TimeStampedModel):
    """
    User-initiated request for additional module access.

    Workflow:
      1. User submits a request (any authenticated user, via POST /rbac/access-requests/)
      2. Super Administrator reviews the pending list (/admin/access-requests)
      3. Admin approves → UserRole for the corresponding module's role is assigned
         OR Admin denies → status set to 'denied' with an optional admin_note

    All status values are soft-coded in STATUS_CHOICES below.
    """

    # Soft-coded status labels — update here only
    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_DENIED   = 'denied'

    STATUS_CHOICES = [
        (STATUS_PENDING,  'Pending Review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_DENIED,   'Denied'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='access_requests',
    )
    module = models.ForeignKey(
        Module,
        on_delete=models.CASCADE,
        related_name='access_requests',
    )
    reason = models.TextField(
        blank=True,
        help_text="Why does the user need access to this module?",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    # Review tracking
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_access_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_note  = models.TextField(blank=True, help_text="Admin response or reason for denial")

    class Meta:
        db_table    = 'rbac_access_requests'
        ordering    = ['-created_at']
        verbose_name = 'Access Request'
        verbose_name_plural = 'Access Requests'
        indexes = [
            models.Index(fields=['user_profile', 'status']),
            models.Index(fields=['module',        'status']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user_profile.user.email} → {self.module.name} ({self.status})"

# ═════════════════════════════════════════════════════════════════════════════
# Enhanced Profile Models — Achievements, Experience, Social Media
# ═════════════════════════════════════════════════════════════════════════════

class Achievement(TimeStampedModel):
    """
    User achievements and milestones — sports, academic, professional, genius records.
    Soft-coded categories defined in rbac.profile_config.ACHIEVEMENT_CATEGORIES
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='achievements'
    )
    
    # Achievement details
    title = models.CharField(max_length=200, help_text="Achievement title or award name")
    category = models.CharField(
        max_length=50,
        help_text="Category code from profile_config.ACHIEVEMENT_CATEGORIES (sports, academic, professional, etc.)"
    )
    description = models.TextField(blank=True, help_text="Detailed description of the achievement")
    
    # Achievement level (bronze, silver, gold, platinum, legendary)
    level = models.CharField(max_length=20, blank=True, help_text="Achievement level/tier")
    
    # Date & location
    achieved_date = models.DateField(null=True, blank=True, help_text="Date when achievement was earned")
    location = models.CharField(max_length=200, blank=True, help_text="Location or event where achieved")
    organization = models.CharField(max_length=200, blank=True, help_text="Issuing organization or institution")
    
    # Supporting data
    certificate_url = models.URLField(blank=True, max_length=500, help_text="Link to certificate or evidence")
    media_url = models.URLField(blank=True, max_length=500, help_text="Link to photo/video evidence")
    
    # Visibility
    is_public = models.BooleanField(default=True, help_text="Show on public profile")
    is_verified = models.BooleanField(default=False, help_text="Verified by admin/system")
    
    # Display order
    display_order = models.IntegerField(default=0, help_text="Order for displaying achievements")
    
    class Meta:
        db_table = 'rbac_user_achievements'
        ordering = ['-achieved_date', '-created_at']
        verbose_name = 'User Achievement'
        verbose_name_plural = 'User Achievements'
        indexes = [
            models.Index(fields=['user_profile', 'category']),
            models.Index(fields=['user_profile', 'is_public']),
            models.Index(fields=['category', '-achieved_date']),
        ]
    
    def __str__(self):
        return f"{self.user_profile.user.email} - {self.title} ({self.category})"


class WorkExperience(TimeStampedModel):
    """
    Professional work experience entries for user timeline.
    Shows career progression and previous roles.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='work_experience'
    )
    
    # Company details
    company_name = models.CharField(max_length=200, help_text="Company or organization name")
    company_logo_url = models.URLField(blank=True, max_length=500, help_text="URL to company logo")
    
    # Role details
    job_title = models.CharField(max_length=200, help_text="Job title or position held")
    employment_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="Employment type from profile_config.EMPLOYMENT_TYPES (full_time, contract, etc.)"
    )
    
    # Industry & location
    industry = models.CharField(max_length=200, blank=True, help_text="Industry sector")
    location = models.CharField(max_length=200, blank=True, help_text="Work location (city, country)")
    
    # Duration
    start_date = models.DateField(help_text="Employment start date")
    end_date = models.DateField(null=True, blank=True, help_text="Employment end date (null = current)")
    is_current = models.BooleanField(default=False, help_text="Currently working here")
    
    # Description
    description = models.TextField(blank=True, help_text="Role description and responsibilities")
    achievements_text = models.TextField(
        blank=True,
        help_text="Key achievements and accomplishments in this role"
    )
    
    # Skills used (JSON array)
    skills_used = models.JSONField(
        default=list,
        blank=True,
        help_text="List of technical skills/tools used in this role"
    )
    
    # Visibility
    is_public = models.BooleanField(default=True, help_text="Show on public profile")
    
    # Display order
    display_order = models.IntegerField(default=0, help_text="Order for displaying experience")
    
    class Meta:
        db_table = 'rbac_work_experience'
        ordering = ['-is_current', '-start_date']
        verbose_name = 'Work Experience'
        verbose_name_plural = 'Work Experience Entries'
        indexes = [
            models.Index(fields=['user_profile', '-start_date']),
            models.Index(fields=['user_profile', 'is_current']),
            models.Index(fields=['industry']),
        ]
    
    def __str__(self):
        current = " (Current)" if self.is_current else ""
        return f"{self.user_profile.user.email} - {self.job_title} at {self.company_name}{current}"
    
    @property
    def duration_years(self):
        """Calculate duration in years."""
        from datetime import date
        end = self.end_date if self.end_date else date.today()
        delta = end - self.start_date
        return round(delta.days / 365.25, 1)


class SocialMediaLink(TimeStampedModel):
    """
    User social media and professional network links.
    Platform codes defined in rbac.profile_config.SOCIAL_MEDIA_PLATFORMS
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='social_links'
    )
    
    # Platform & URL
    platform = models.CharField(
        max_length=50,
        help_text="Platform code from profile_config.SOCIAL_MEDIA_PLATFORMS (linkedin, github, twitter, etc.)"
    )
    url = models.URLField(max_length=500, help_text="Full URL to the user's profile on this platform")
    
    # Display metadata
    username = models.CharField(max_length=200, blank=True, help_text="Username or handle on the platform")
    is_verified = models.BooleanField(default=False, help_text="Link has been verified")
    
    # Visibility
    is_public = models.BooleanField(default=True, help_text="Show on public profile")
    
    # Display order
    display_order = models.IntegerField(default=0, help_text="Order for displaying social links")
    
    class Meta:
        db_table = 'rbac_social_media_links'
        ordering = ['display_order', 'platform']
        verbose_name = 'Social Media Link'
        verbose_name_plural = 'Social Media Links'
        unique_together = ['user_profile', 'platform']
        indexes = [
            models.Index(fields=['user_profile', 'is_public']),
            models.Index(fields=['platform']),
        ]
    
    def __str__(self):
        return f"{self.user_profile.user.email} - {self.platform}"


class ProfileDocument(TimeStampedModel):
    """
    User profile documents (Emirates ID, Driving License, Country ID, etc.)
    Soft-coded document types defined in rbac.profile_config.DOCUMENT_TYPES
    Files stored in AWS S3 bucket.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='documents'
    )
    
    # Document type & file
    document_type = models.CharField(
        max_length=50,
        help_text="Document type code from profile_config.DOCUMENT_TYPES (emirates_id, driving_license, country_id, passport, visa)"
    )
    document_file = models.FileField(
        upload_to='profile_documents/',
        max_length=500,
        help_text="Uploaded document file (stored in S3)"
    )
    
    # Document metadata
    document_number = models.CharField(max_length=100, blank=True, help_text="ID/License/Passport number")
    issue_date = models.DateField(null=True, blank=True, help_text="Date of issue")
    expiry_date = models.DateField(null=True, blank=True, help_text="Expiry date")
    issuing_authority = models.CharField(max_length=200, blank=True, help_text="Issuing authority/country")
    
    # Verification
    verification_status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending Review'),
            ('verified', 'Verified'),
            ('rejected', 'Rejected'),
            ('expired', 'Expired'),
        ],
        default='pending',
        help_text="Document verification status"
    )
    verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='profile_documents_verified',
        help_text="Admin who verified this document"
    )
    verified_at = models.DateTimeField(null=True, blank=True, help_text="Verification timestamp")
    rejection_reason = models.TextField(blank=True, help_text="Reason if rejected")
    
    # Notes
    notes = models.TextField(blank=True, help_text="Additional notes or comments")
    
    # Visibility
    is_active = models.BooleanField(default=True, help_text="Document is active (not replaced)")
    
    class Meta:
        db_table = 'rbac_profile_documents'
        ordering = ['-created_at']
        verbose_name = 'Profile Document'
        verbose_name_plural = 'Profile Documents'
        indexes = [
            models.Index(fields=['user_profile', 'document_type', 'is_active']),
            models.Index(fields=['verification_status']),
            models.Index(fields=['expiry_date']),
        ]
    
    def __str__(self):
        return f"{self.user_profile.user.email} - {self.document_type}"
    
    @property
    def is_expired(self):
        """Check if document has expired."""
        if not self.expiry_date:
            return False
        from django.utils import timezone
        return self.expiry_date < timezone.now().date()
    
    @property
    def expires_soon(self, days=30):
        """Check if document expires within specified days."""
        if not self.expiry_date:
            return False
        from django.utils import timezone
        from datetime import timedelta
        threshold = timezone.now().date() + timedelta(days=days)
        return self.expiry_date <= threshold and not self.is_expired
