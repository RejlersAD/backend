"""
RBAC Models - Enterprise Role-Based Access Control
Designed for regulated Oil & Gas environment
"""
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from apps.core.models import TimeStampedModel

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
        (1, 'Super Admin'),
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
    
    def has_permission(self, permission_code):
        """Check if user has specific permission through any role"""
        from apps.rbac.models import UserRole
        user_role_ids = UserRole.objects.filter(user_profile=self).values_list('role_id', flat=True)
        return Permission.objects.filter(
            roles__id__in=user_role_ids,
            code=permission_code,
            is_active=True
        ).exists()
    
    def has_module_access(self, module_code):
        """Check if user has access to module through any role"""
        from apps.rbac.models import UserRole
        user_role_ids = UserRole.objects.filter(user_profile=self).values_list('role_id', flat=True)
        return Module.objects.filter(
            roles__id__in=user_role_ids,
            code=module_code,
            is_active=True
        ).exists()
    
    def get_all_permissions(self):
        """Get all permissions from all assigned roles"""
        return Permission.objects.filter(
            roles__in=self.roles.all(),
            is_active=True
        ).distinct()
    
    def get_all_modules(self):
        """Get all accessible modules from all assigned roles"""
        # Get role IDs through UserRole relationship
        user_role_ids = UserRole.objects.filter(user_profile=self).values_list('role_id', flat=True)
        
        # Get modules linked to these roles through RoleModule
        return Module.objects.filter(
            rolemodule__role_id__in=user_role_ids,
            is_active=True
        ).distinct()


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
