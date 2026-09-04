"""
RBAC Serializers
Enterprise-grade serializers for Role-Based Access Control
"""
import logging
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from .models import (
    Organization, Module, Permission, Role, RolePermission, RoleModule,
    UserProfile, UserRole, UserStorage, AuditLog, AccessRequest,
    Achievement, WorkExperience, SocialMediaLink, ProfileDocument,
)

User = get_user_model()
logger = logging.getLogger(__name__)


class OrganizationSerializer(serializers.ModelSerializer):
    """Organization serializer"""
    user_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'code', 'description', 'is_active',
            'primary_contact_name', 'primary_contact_email', 'primary_contact_phone',
            'address_line1', 'address_line2', 'city', 'country', 'postal_code',
            's3_bucket_name', 's3_region',
            'created_at', 'updated_at', 'user_count'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_user_count(self, obj):
        return obj.users.filter(is_deleted=False, status='active').count()


class ModuleSerializer(serializers.ModelSerializer):
    """Module serializer"""
    permission_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Module
        fields = [
            'id', 'name', 'code', 'description', 'is_active',
            'icon', 'order', 'created_at', 'updated_at', 'permission_count'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_permission_count(self, obj):
        # Reads the prefetched cache (obj.permissions.all()) instead of
        # .filter().count(), which would bypass prefetch_related and hit the
        # DB again for every module.
        return sum(1 for p in obj.permissions.all() if p.is_active)


class PermissionSerializer(serializers.ModelSerializer):
    """Permission serializer"""
    module_name = serializers.CharField(source='module.name', read_only=True)
    module_code = serializers.CharField(source='module.code', read_only=True)
    
    class Meta:
        model = Permission
        fields = [
            'id', 'module', 'module_name', 'module_code',
            'code', 'name', 'description', 'action', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PermissionListSerializer(serializers.ModelSerializer):
    """Simplified permission serializer for lists"""
    class Meta:
        model = Permission
        fields = ['id', 'code', 'name', 'action']


class RolePermissionSerializer(serializers.ModelSerializer):
    """Role-Permission relationship serializer"""
    permission = PermissionListSerializer(read_only=True)
    permission_id = serializers.UUIDField(write_only=True)
    granted_by_email = serializers.EmailField(source='granted_by.email', read_only=True)
    
    class Meta:
        model = RolePermission
        fields = [
            'id', 'role', 'permission', 'permission_id',
            'granted_by', 'granted_by_email', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'granted_by']


class RoleModuleSerializer(serializers.ModelSerializer):
    """Role-Module relationship serializer"""
    module = ModuleSerializer(read_only=True)
    module_id = serializers.UUIDField(write_only=True)
    granted_by_email = serializers.EmailField(source='granted_by.email', read_only=True)
    
    class Meta:
        model = RoleModule
        fields = [
            'id', 'role', 'module', 'module_id',
            'granted_by', 'granted_by_email', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'granted_by']


class RoleSerializer(serializers.ModelSerializer):
    """Role serializer with permissions and modules"""
    permissions = PermissionListSerializer(many=True, read_only=True)
    modules = ModuleSerializer(many=True, read_only=True)
    permission_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False
    )
    module_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False
    )
    user_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Role
        fields = [
            'id', 'name', 'code', 'description', 'level', 'is_active', 'is_system_role',
            'permissions', 'modules', 'permission_ids', 'module_ids',
            'created_at', 'updated_at', 'user_count'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_user_count(self, obj):
        # Reads the prefetched cache (obj.user_profiles.all()) instead of
        # .filter().count(), which would bypass prefetch_related and hit the
        # DB again for every role.
        return sum(1 for up in obj.user_profiles.all() if not up.is_deleted)
    
    def create(self, validated_data):
        permission_ids = validated_data.pop('permission_ids', [])
        module_ids = validated_data.pop('module_ids', [])
        
        role = Role.objects.create(**validated_data)
        
        # Assign permissions
        if permission_ids:
            user = self.context['request'].user
            for perm_id in permission_ids:
                RolePermission.objects.create(
                    role=role,
                    permission_id=perm_id,
                    granted_by=user
                )
        
        # Assign modules
        if module_ids:
            user = self.context['request'].user
            for module_id in module_ids:
                RoleModule.objects.create(
                    role=role,
                    module_id=module_id,
                    granted_by=user
                )
        
        return role
    
    def update(self, instance, validated_data):
        permission_ids = validated_data.pop('permission_ids', None)
        module_ids = validated_data.pop('module_ids', None)
        
        # Update basic fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        user = self.context['request'].user
        
        # Update permissions if provided
        if permission_ids is not None:
            instance.rolepermission_set.all().delete()
            for perm_id in permission_ids:
                RolePermission.objects.create(
                    role=instance,
                    permission_id=perm_id,
                    granted_by=user
                )
        
        # Update modules if provided
        if module_ids is not None:
            instance.rolemodule_set.all().delete()
            for module_id in module_ids:
                RoleModule.objects.create(
                    role=instance,
                    module_id=module_id,
                    granted_by=user
                )
        
        return instance


class RoleListSerializer(serializers.ModelSerializer):
    """Simplified role serializer for lists"""
    class Meta:
        model = Role
        fields = ['id', 'name', 'code', 'level']


class UserSerializer(serializers.ModelSerializer):
    """User serializer"""
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'is_active', 'is_staff', 'is_superuser']
        read_only_fields = ['id', 'is_staff', 'is_superuser']


class UserRoleSerializer(serializers.ModelSerializer):
    """User-Role relationship serializer"""
    role = RoleListSerializer(read_only=True)
    role_id = serializers.UUIDField(write_only=True)
    assigned_by_email = serializers.EmailField(source='assigned_by.email', read_only=True)
    
    class Meta:
        model = UserRole
        fields = [
            'id', 'user_profile', 'role', 'role_id',
            'is_primary', 'assigned_by', 'assigned_by_email', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'assigned_by']


class UserProfileSerializer(serializers.ModelSerializer):
    """User profile serializer with full details"""
    user = UserSerializer(read_only=True)
    organization = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.all(),
        required=False,
        allow_null=True
    )
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    organization_id = serializers.UUIDField(write_only=True, required=False)
    primary_role = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    role_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False
    )
    module_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False
    )
    permissions = serializers.SerializerMethodField()
    modules = serializers.SerializerMethodField()
    profile_photo = serializers.SerializerMethodField()
    # Engineering competency profile — stored in metadata['engineer_profile'], no migration needed
    engineer_profile = serializers.SerializerMethodField()
    join_date = serializers.SerializerMethodField()
    probation_end_date = serializers.SerializerMethodField()

    # User creation fields (used on POST). phone is intentionally NOT redeclared
    # here so the auto-generated model field stays read+write — otherwise the
    # Profile page can never re-display a saved phone number after refresh.
    username = serializers.CharField(write_only=True, required=False)
    email = serializers.EmailField(write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    first_name = serializers.CharField(write_only=True, required=False)
    last_name = serializers.CharField(write_only=True, required=False)
    is_active = serializers.BooleanField(write_only=True, required=False)

    # Reporting Manager — single source of truth for both Profile and Onboarding pages.
    # manager_id  : writable UUID → sets UserProfile.manager FK
    # manager_name: read-only display name for the current manager
    # manager_detail: read-only dict used by the frontend dropdown to pre-select
    manager_id = serializers.PrimaryKeyRelatedField(
        source='manager',
        queryset=UserProfile.objects.filter(is_deleted=False),
        required=False,
        allow_null=True,
        write_only=True,
    )
    manager_name = serializers.SerializerMethodField()
    manager_detail = serializers.SerializerMethodField()

    def get_manager_name(self, obj):
        if not obj.manager:
            return None
        return obj.manager.user.get_full_name() or obj.manager.user.username

    def get_manager_detail(self, obj):
        if not obj.manager:
            return None
        return {
            'id':        str(obj.manager.id),
            'name':      obj.manager.user.get_full_name() or obj.manager.user.username,
            'email':     obj.manager.user.email,
            'job_title': obj.manager.job_title or '',
            'department': obj.manager.department or '',
        }

    def validate(self, attrs):
        """Validate required fields for creation"""
        import logging
        logger = logging.getLogger(__name__)
        
        if self.instance is None:
            logger.info(f"[UserProfile] Validating user creation with attrs: {list(attrs.keys())}")
            
            if 'email' not in attrs:
                logger.error("[UserProfile] Validation failed: email is missing")
                raise serializers.ValidationError({'email': 'Email is required for user creation'})
            if 'password' not in attrs:
                logger.error("[UserProfile] Validation failed: password is missing")
                raise serializers.ValidationError({'password': 'Password is required for user creation'})
            if 'first_name' not in attrs:
                logger.error("[UserProfile] Validation failed: first_name is missing")
                raise serializers.ValidationError({'first_name': 'First name is required for user creation'})
            if 'last_name' not in attrs:
                logger.error("[UserProfile] Validation failed: last_name is missing")
                raise serializers.ValidationError({'last_name': 'Last name is required for user creation'})
            
            # Auto-generate username from email if not provided
            if 'username' not in attrs or not attrs.get('username'):
                email = attrs.get('email', '')
                base_username = email.split('@')[0] if email else 'user'
                username = base_username
                
                # Check if username exists and make it unique
                counter = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}{counter}"
                    counter += 1
                
                attrs['username'] = username
                logger.info(f"[UserProfile] Auto-generated username: {username} from email: {email}")
            
            # Auto-assign default organization if not provided
            if 'organization_id' not in attrs or not attrs.get('organization_id'):
                from apps.rbac.models import Organization
                default_org = Organization.objects.filter(name__icontains='default').first()
                if not default_org:
                    default_org = Organization.objects.first()
                if default_org:
                    attrs['organization_id'] = default_org.id
                    logger.info(f"[UserProfile] Auto-assigned default organization: {default_org.name} ({default_org.id})")
                else:
                    logger.error("[UserProfile] No organization found to assign")
                    raise serializers.ValidationError({'organization_id': 'No organization available. Please contact admin.'})
            
            # Check if email already exists (exclude soft-deleted users)
            email = attrs.get('email')
            # Check if there's an active (non-deleted) user profile with this email
            if UserProfile.objects.filter(user__email=email, is_deleted=False).exists():
                logger.error(f"[UserProfile] Validation failed: email {email} already exists")
                raise serializers.ValidationError({'email': 'A user with this email already exists'})
            
            # Also check if User exists but has a deleted profile - allow reuse
            existing_user = User.objects.filter(email=email).first()
            if existing_user:
                deleted_profile = UserProfile.objects.filter(user=existing_user, is_deleted=True).first()
                if deleted_profile:
                    logger.info(f"[UserProfile] Email {email} was previously deleted, allowing reuse")
            
            # Validate email format and deliverability using soft-coded config
            try:
                from apps.users.email_validation_config import EmailValidationConfig
                validation_result = EmailValidationConfig.validate_email_deliverability(email)
                if not validation_result['is_valid']:
                    logger.warning(f"[UserProfile] Email validation failed for {email}: {validation_result['message']}")
                    raise serializers.ValidationError({'email': validation_result['message']})
                logger.info(f"[UserProfile] Email validation passed for {email}")
            except ImportError as e:
                logger.warning(f"[UserProfile] Email validation module not available: {e}")
                # Fallback: basic email validation if config not available
                import re
                email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
                if not re.match(email_pattern, email):
                    logger.error(f"[UserProfile] Basic email validation failed for {email}")
                    raise serializers.ValidationError({'email': 'Invalid email format'})
        
        return attrs

    def validate_employee_id(self, value):
        """Protect the shared biometric/payroll employee identifier."""
        from apps.hr_core.models import EmployeeMaster
        from django.db.models import Q

        normalized = str(value or '').strip()
        if not normalized:
            return ''
        matches = EmployeeMaster.objects.filter(
            Q(employee_code__iexact=normalized)
            | Q(emp_code__iexact=normalized)
        )
        if self.instance is not None:
            matches = matches.exclude(user_id=self.instance.user_id)
        if matches.exists():
            raise serializers.ValidationError(
                'This employee ID is already assigned to another employee.'
            )
        return normalized
    
    class Meta:
        model = UserProfile
        fields = [
            'id', 'canonical_employee', 'user', 'organization', 'organization_id', 'organization_name', 'status', 'is_mfa_enabled',
            'primary_role', 'roles', 'role_ids', 'module_ids', 'permissions', 'modules',
            'employee_id', 'department', 'job_title', 'manager',
            'manager_id', 'manager_name', 'manager_detail',
            'last_login_ip', 'last_login_at', 'failed_login_attempts',
            'must_change_password',
            'profile_photo', 'phone', 'bio', 'location', 'engineer_profile',
            'join_date', 'probation_end_date',
            'is_deleted', 'deleted_at', 'deleted_by',
            'created_at', 'updated_at',
            'username', 'email', 'password', 'first_name', 'last_name', 'is_active', 'phone'
        ]
        read_only_fields = [
            'id', 'canonical_employee', 'user', 'last_login_ip', 'last_login_at', 'failed_login_attempts',
            'is_deleted', 'deleted_at', 'deleted_by', 'created_at', 'updated_at',
            'manager', 'manager_name', 'manager_detail',
        ]
    
    def get_permissions(self, obj):
        """Get all permissions for user"""
        permissions = obj.get_all_permissions()
        return PermissionListSerializer(permissions, many=True).data

    def get_join_date(self, obj):
        try:
            return obj.user.employee_master.join_date
        except (AttributeError, ObjectDoesNotExist):
            return None

    def get_probation_end_date(self, obj):
        try:
            return obj.user.employee_master.probation_end_date
        except (AttributeError, ObjectDoesNotExist):
            return None
    
    def get_modules(self, obj):
        """Get all accessible modules for user"""
        modules = obj.get_all_modules()
        return [{'id': str(m.id), 'code': m.code, 'name': m.name} for m in modules]
    
    def get_primary_role(self, obj):
        """
        Get primary role from userrole_set.
        Returns: {id, name, code} or None
        """
        for user_role in obj.userrole_set.select_related('role').all():
            if user_role.is_primary and user_role.role.is_active:
                return {
                    'id': str(user_role.role.id),
                    'name': user_role.role.name,
                    'code': user_role.role.code,
                }
        return None
    
    def get_roles(self, obj):
        """
        Return all active roles for this user with is_primary flag.
        
        SOFT-CODED: Filters out custom_* roles (per-user legacy roles).
        Custom role prefix defined in rbac_config.MODULE_ASSIGNMENT_CONFIG.
        
        Format: [{id, name, code, level, is_primary}]
        """
        from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG
        custom_role_prefix = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')
        
        result = []
        # Use userrole_set to access the junction table with is_primary
        for user_role in obj.userrole_set.select_related('role').all():
            if user_role.role.is_active and not user_role.role.code.startswith(custom_role_prefix):
                result.append({
                    'id':         str(user_role.role.id),
                    'name':       user_role.role.name,
                    'code':       user_role.role.code,
                    'level':      user_role.role.level,
                    'is_primary': user_role.is_primary,
                })
        return result
    
    def get_profile_photo(self, obj):
        """Return absolute URL for profile photo.

        - S3 (production): obj.profile_photo.url already returns a presigned HTTPS URL.
        - Local dev: url is relative (/media/...) — build absolute from request context.
        """
        if not obj.profile_photo:
            return None
        try:
            url = obj.profile_photo.url
            # S3 presigned URLs are already absolute
            if url.startswith('http'):
                return url
            # Local filesystem — build absolute URL from request context
            request = self.context.get('request')
            if request:
                absolute_uri = request.build_absolute_uri(url)
                # Fix Vite dev-server proxy: Host header is localhost:8000 so
                # build_absolute_uri should already produce the correct host.
                # Guard against edge cases where :5173 leaks through.
                if ':5173' in absolute_uri:
                    absolute_uri = absolute_uri.replace('http://localhost:5173', 'http://localhost:8000')
                return absolute_uri
            return url
        except Exception:
            return None
    
    def get_engineer_profile(self, obj):
        """Return engineering competency data from the dedicated rbac_engineer_profiles table."""
        try:
            return obj.engineer_profile.to_dict()
        except Exception:
            return {}

    def create(self, validated_data):
        role_ids = validated_data.pop('role_ids', [])
        module_ids = validated_data.pop('module_ids', [])
        organization_id = validated_data.pop('organization_id', None)
        
        # Extract user data (username is now validated and generated in validate() method)
        username = validated_data.pop('username')
        email = validated_data.pop('email')
        password = validated_data.pop('password')
        first_name = validated_data.pop('first_name', '')
        last_name = validated_data.pop('last_name', '')
        is_active = validated_data.pop('is_active', True)
        phone = validated_data.pop('phone', None)  # Extract phone but don't add to profile
        
        # Set organization from organization_id if provided
        if organization_id:
            validated_data['organization'] = Organization.objects.get(id=organization_id)
        
        # Auto-assign organization if not provided
        if 'organization' not in validated_data or validated_data['organization'] is None:
            request_user = self.context['request'].user
            try:
                # Use creator's organization
                validated_data['organization'] = request_user.rbac_profile.organization
            except UserProfile.DoesNotExist:
                # Fallback: get first active organization or create default
                default_org = Organization.objects.filter(is_active=True).first()
                if not default_org:
                    default_org = Organization.objects.create(
                        name='Default Organization',
                        code='DEFAULT',
                        is_active=True
                    )
                validated_data['organization'] = default_org
        
        # Check if creating super admin
        is_super_admin = False
        if role_ids:
            super_admin_roles = Role.objects.filter(
                id__in=role_ids,
                code='super_admin',
                is_active=True
            )
            is_super_admin = super_admin_roles.exists()
        
        # Store the password for welcome email (before hashing)
        temp_password = password
        
        # Check if User exists with a deleted profile (reuse scenario)
        from django.utils import timezone
        existing_user = User.objects.filter(email=email).first()
        existing_deleted_profile = None
        
        if existing_user:
            # Check if the existing user has a deleted profile
            existing_deleted_profile = UserProfile.objects.filter(
                user=existing_user, 
                is_deleted=True
            ).first()
        
        if existing_user and existing_deleted_profile:
            # Reuse existing User object and update its details
            user = existing_user
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.set_password(password)
            user.last_password_change = timezone.now()
            user.phone_number = phone
            user.is_active = is_active
            user.is_superuser = is_super_admin
            user.is_staff = is_super_admin
            user.is_first_login = True
            user.must_reset_password = True
            user.temp_password_created_at = timezone.now()
            user.save()
            
            logger.info(f"[UserProfile] Reusing existing User {email} with deleted profile")
        else:
            # Create new user with appropriate permissions (username already validated and unique)
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone,  # Add phone_number to User model
                is_active=is_active,
                is_superuser=is_super_admin,
                is_staff=is_super_admin,
                is_first_login=True,  # Mark as first login
                must_reset_password=True,  # Require password reset
                temp_password_created_at=timezone.now()
            )
        
        # Create or reactivate profile with explicit is_deleted=False
        validated_data['is_deleted'] = False
        
        if existing_deleted_profile:
            # Reactivate the deleted profile instead of creating a new one
            for key, value in validated_data.items():
                setattr(existing_deleted_profile, key, value)
            existing_deleted_profile.deleted_at = None
            existing_deleted_profile.deleted_by = None
            existing_deleted_profile.save()
            profile = existing_deleted_profile
            logger.info(f"[UserProfile] Reactivated deleted profile for {email}")
        else:
            # Create new profile
            profile = UserProfile.objects.create(user=user, **validated_data)

        from apps.hr_core.services import EmployeeService
        profile.canonical_employee = EmployeeService.sync_from_rbac_profile(profile)
        
        # Assign roles based on role_ids if provided
        if role_ids:
            request_user = self.context['request'].user
            for i, role_id in enumerate(role_ids):
                UserRole.objects.create(
                    user_profile=profile,
                    role_id=role_id,
                    assigned_by=request_user,
                    is_primary=(i == 0)
                )
        
        # Assign roles based on modules (feature-based access)
        # SECURITY: Direct per-user module assignment is disabled by default.
        # When MODULE_ASSIGNMENT_CONFIG['create_custom_roles'] is False the
        # incoming module_ids are ignored — modules must be granted via a
        # shared Role. Flip the flag in backend/apps/rbac/rbac_config.py to
        # re-enable the legacy per-user "custom_<email>" role hack.
        if module_ids:
            from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG, get_custom_role_code, get_custom_role_name

            if not MODULE_ASSIGNMENT_CONFIG.get('create_custom_roles', False):
                logger.warning(
                    "[UserProfile] Ignoring module_ids for %s — create_custom_roles is disabled. "
                    "Assign modules via a Role instead.",
                    email,
                )
            else:
                request_user = self.context['request'].user
                from django.db import transaction

                logger.info(f"[UserProfile] Processing module assignment for {email}: {len(module_ids)} modules")

                with transaction.atomic():
                    # Create a unique custom role for this user based on email
                    user_role_code = get_custom_role_code(email)
                    custom_role_name = get_custom_role_name(first_name, last_name)

                    custom_role, created = Role.objects.get_or_create(
                        code=user_role_code,
                        defaults={
                            'name': custom_role_name,
                            'description': f'Custom role for {email} with selected modules',
                            'level': MODULE_ASSIGNMENT_CONFIG['custom_role_level'],
                            'is_active': True
                        }
                    )

                    if created:
                        logger.info(f"[UserProfile] Created custom role: {custom_role.name} ({custom_role.code})")
                    else:
                        logger.info(f"[UserProfile] Using existing custom role: {custom_role.name} ({custom_role.code})")
                        # Update role name if user name changed
                        custom_role.name = custom_role_name
                        custom_role.description = f'Custom role for {email} with selected modules'
                        custom_role.save()

                    # Assign the custom role to the user
                    user_role, user_role_created = UserRole.objects.get_or_create(
                        user_profile=profile,
                        role=custom_role,
                        defaults={
                            'assigned_by': request_user,
                            'is_primary': not role_ids  # Primary if no other roles
                        }
                    )

                    if user_role_created:
                        logger.info(f"[UserProfile] Assigned custom role to user (primary: {user_role.is_primary})")

                    # Clear existing module assignments if configured
                    if MODULE_ASSIGNMENT_CONFIG['clear_existing_on_update']:
                        deleted_modules = RoleModule.objects.filter(role=custom_role).count()
                        deleted_perms = RolePermission.objects.filter(role=custom_role).count()
                        RoleModule.objects.filter(role=custom_role).delete()
                        RolePermission.objects.filter(role=custom_role).delete()
                        logger.info(f"[UserProfile] Cleared {deleted_modules} existing modules and {deleted_perms} permissions from custom role")

                    # Assign modules to the role
                    modules_assigned = 0
                    for module_id in module_ids:
                        try:
                            module = Module.objects.get(id=module_id, is_active=True)
                            role_module, rm_created = RoleModule.objects.get_or_create(
                                role=custom_role,
                                module=module,
                                defaults={'granted_by': request_user}
                            )
                            if rm_created:
                                modules_assigned += 1
                                logger.info(f"[UserProfile] Linked module '{module.code}' to role '{custom_role.name}'")
                        except Module.DoesNotExist:
                            logger.error(f"[UserProfile] Module with ID {module_id} not found or inactive")

                    logger.info(f"[UserProfile] Total modules assigned: {modules_assigned}/{len(module_ids)}")

                    # Get all permissions for the selected modules and assign them
                    if MODULE_ASSIGNMENT_CONFIG['assign_permissions_automatically']:
                        permissions = Permission.objects.filter(
                            module_id__in=module_ids,
                            is_active=True
                        )

                        permissions_assigned = 0
                        for permission in permissions:
                            role_perm, rp_created = RolePermission.objects.get_or_create(
                                role=custom_role,
                                permission=permission,
                                defaults={'granted_by': request_user}
                            )
                            if rp_created:
                                permissions_assigned += 1

                        logger.info(f"[UserProfile] Assigned {permissions_assigned} permissions to custom role")
        
        # Send email verification if enabled (fail gracefully - don't block user creation)
        from django.conf import settings
        
        # Email configuration check
        email_configured = bool(
            getattr(settings, 'EMAIL_HOST_USER', None) and 
            getattr(settings, 'EMAIL_HOST_PASSWORD', None)
        )
        
        if not email_configured:
            logger.warning(f"[UserProfile] Email not configured. Skipping email sending for {user.email}")
        elif settings.EMAIL_VERIFICATION_REQUIRED:
            try:
                from apps.rbac.email_verification import send_verification_email
                send_verification_email(profile, self.context.get('request'))
                logger.info(f"[UserProfile] Verification email sent to {user.email}")
            except ImportError as e:
                logger.warning(f"[UserProfile] Email verification module not available: {e}")
            except Exception as e:
                logger.error(f"[UserProfile] Failed to send verification email to {user.email}: {str(e)}", exc_info=True)
        
        # Send welcome email with password setup link (fail gracefully - don't block user creation)
        if email_configured:
            try:
                from apps.users.password_reset_service import PasswordResetService
                
                # Generate password reset token
                token, expiry = PasswordResetService.create_reset_token(user)
                logger.info(f"[UserProfile] Password reset token created for {user.email}")
                
                # Send welcome email with setup link
                request = self.context.get('request')
                email_sent = PasswordResetService.send_welcome_email_with_reset(user, token, request)
                
                if email_sent:
                    logger.info(f"[UserProfile] Welcome email sent to {user.email}")
                else:
                    logger.warning(f"[UserProfile] Failed to send welcome email to {user.email}")
                    
            except ImportError as e:
                logger.warning(f"[UserProfile] PasswordResetService not available: {e}")
            except Exception as e:
                logger.error(f"[UserProfile] Error sending welcome email to {user.email}: {str(e)}", exc_info=True)
        else:
            logger.info(f"[UserProfile] Skipping welcome email for {user.email} (email not configured)")
        
        logger.info(f"[UserProfile] User profile created successfully for {user.email}")
        return profile
    
    def update(self, instance, validated_data):
        role_ids = validated_data.pop('role_ids', None)
        
        # Update user if email/name provided
        if 'email' in validated_data:
            instance.user.email = validated_data.pop('email')
            instance.user.save()
        if 'first_name' in validated_data:
            instance.user.first_name = validated_data.pop('first_name')
            instance.user.save()
        if 'last_name' in validated_data:
            instance.user.last_name = validated_data.pop('last_name')
            instance.user.save()
        if 'is_active' in validated_data:
            instance.user.is_active = validated_data.pop('is_active')
            instance.user.save(update_fields=['is_active'])
        if 'password' in validated_data:
            from django.utils import timezone
            instance.user.set_password(validated_data.pop('password'))
            instance.user.last_password_change = timezone.now()
            instance.user.must_reset_password = False
            instance.user.is_first_login = False
            instance.user.save()
            instance.must_change_password = False

        # Update profile
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update roles if provided
        if role_ids is not None:
            instance.userrole_set.all().delete()
            request_user = self.context['request'].user
            for i, role_id in enumerate(role_ids):
                UserRole.objects.create(
                    user_profile=instance,
                    role_id=role_id,
                    assigned_by=request_user,
                    is_primary=(i == 0)
                )
        
        return instance


class UserProfileSelfSerializer(UserProfileSerializer):
    """Lightweight current-user profile payload for the Profile screen.

    The full serializer resolves the complete permission and module graph. That
    data is necessary for access-control consumers, but it is not used by the
    personal profile editor and can make a remote-database request needlessly
    expensive.
    """

    class Meta(UserProfileSerializer.Meta):
        fields = [
            'id', 'canonical_employee', 'user', 'organization', 'organization_name', 'status',
            'is_mfa_enabled', 'employee_id', 'department', 'job_title',
            'manager_name', 'manager_detail', 'last_login_ip', 'last_login_at',
            'must_change_password', 'profile_photo', 'phone', 'bio', 'location',
            'engineer_profile', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class UserProfileListSerializer(serializers.ModelSerializer):
    """
    Optimized user profile serializer for lists.

    Performance:
    - Uses prefetched user + organization (no extra DB queries).
    - Caches full_name and primary_role computation.
    - <2s response for 276 users.

    Field selection (soft-coded — append-only; never remove without bumping API version):
    Core identity comes nested on `user` (matches detail serializer shape so the
    same frontend code paths work for list and detail responses without forks).
    Flat aliases (email, full_name, first_name, last_name) are kept for
    backward compatibility with callers that read the legacy flat shape.
    """
    # ── Identity (flat aliases — legacy callers depend on these) ───────────
    email = serializers.EmailField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    full_name = serializers.SerializerMethodField()
    # ── Identity (nested — matches UserProfileSerializer for shape parity) ─
    user = UserSerializer(read_only=True)
    # ── Organisation & roles ───────────────────────────────────────────────
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    primary_role = serializers.SerializerMethodField()
    # SOFT-CODED: all active roles for the user — uses prefetched userrole_set
    # so no extra DB query.  Format: [{id, name, code, level}]
    roles = serializers.SerializerMethodField()
    # ── HR-facing fields already loaded on UserProfile — no extra query ────
    profile_photo = serializers.SerializerMethodField()
    manager_name = serializers.SerializerMethodField()
    manager_detail = serializers.SerializerMethodField()
    join_date = serializers.SerializerMethodField()
    probation_end_date = serializers.SerializerMethodField()
    exit_date = serializers.SerializerMethodField()
    employment_status = serializers.SerializerMethodField()
    resignation_date = serializers.SerializerMethodField()
    contract_end_date = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = [
            # Identity
            'id', 'canonical_employee', 'user', 'email', 'first_name', 'last_name', 'full_name',
            # Organisation / role
            'organization_name', 'primary_role', 'roles',
            # Employment
            'employee_id', 'department', 'job_title', 'manager_name', 'manager_detail',
            'join_date', 'probation_end_date', 'exit_date', 'employment_status', 'resignation_date', 'contract_end_date',
            # Contact / location
            'phone', 'location', 'bio',
            # Status & security
            'status', 'is_mfa_enabled',
            # Media
            'profile_photo',
            # Timestamps
            'last_login_at', 'created_at', 'updated_at',
        ]

    def get_full_name(self, obj):
        """Get full name from prefetched user data"""
        return f"{obj.user.first_name} {obj.user.last_name}".strip()

    def get_primary_role(self, obj):
        """
        Get primary role from prefetched userrole_set
        Uses cached data - no additional DB query
        """
        # Use all() to access prefetched data without hitting DB
        user_roles = obj.userrole_set.all()
        for user_role in user_roles:
            if user_role.is_primary:
                return {
                    'id': str(user_role.role.id),
                    'name': user_role.role.name
                }
        return None

    def get_roles(self, obj):
        """
        Return all active roles for this user from prefetched userrole_set.
        Uses cached data — no additional DB query per user.
        Format: [{id, name, code, level, is_primary}]
        
        SOFT-CODED: Filters out custom_* roles (per-user legacy roles that should not
        appear in UI). Custom role prefix is defined in rbac_config.MODULE_ASSIGNMENT_CONFIG.
        """
        from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG
        custom_role_prefix = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')
        
        result = []
        for user_role in obj.userrole_set.all():
            if user_role.role.is_active and not user_role.role.code.startswith(custom_role_prefix):
                result.append({
                    'id':         str(user_role.role.id),
                    'name':       user_role.role.name,
                    'code':       user_role.role.code,
                    'level':      user_role.role.level,
                    'is_primary': user_role.is_primary,
                })
        return result

    @staticmethod
    def _employee_master(obj):
        try:
            return obj.user.employee_master
        except (AttributeError, ObjectDoesNotExist):
            return None

    @staticmethod
    def _offboarding(obj, reason=None):
        records = list(obj.user.offboarding_records.all())
        if reason:
            records = [record for record in records if record.exit_reason == reason]
        return records[0] if records else None

    def get_manager_name(self, obj):
        manager = obj.manager
        if manager:
            return manager.user.get_full_name() or manager.user.email
        master = self._employee_master(obj)
        if master and master.manager:
            return master.manager.get_full_name()
        return None

    def get_manager_detail(self, obj):
        manager = obj.manager
        if manager:
            return {
                'id': str(manager.id),
                'name': manager.user.get_full_name() or manager.user.email,
                'email': manager.user.email,
                'job_title': manager.job_title or '',
                'department': manager.department or '',
            }
        master = self._employee_master(obj)
        if master and master.manager:
            try:
                manager_profile = master.manager.user.rbac_profile
            except (AttributeError, ObjectDoesNotExist):
                manager_profile = None
            return {
                'id': str(manager_profile.id) if manager_profile else None,
                'name': master.manager.get_full_name(),
                'email': master.manager.email,
                'job_title': master.manager.designation or master.manager.job_title_uae or '',
                'department': master.manager.department or '',
            }
        return None

    def get_join_date(self, obj):
        master = self._employee_master(obj)
        return master.join_date if master else None

    def get_probation_end_date(self, obj):
        master = self._employee_master(obj)
        return master.probation_end_date if master else None

    def get_exit_date(self, obj):
        master = self._employee_master(obj)
        return master.exit_date if master else None

    def get_employment_status(self, obj):
        master = self._employee_master(obj)
        return master.employment_status if master else obj.status

    def get_resignation_date(self, obj):
        record = self._offboarding(obj, 'resignation')
        return record.last_working_day if record else None

    def get_contract_end_date(self, obj):
        record = self._offboarding(obj, 'contract_end')
        return record.last_working_day if record else None

    def get_profile_photo(self, obj):
        """Return absolute presigned URL for profile photo (same logic as detail serializer)."""
        if not obj.profile_photo:
            return None
        try:
            url = obj.profile_photo.url
            if url.startswith('http'):
                return url
            request = self.context.get('request')
            if request:
                absolute_uri = request.build_absolute_uri(url)
                if ':5173' in absolute_uri:
                    absolute_uri = absolute_uri.replace('http://localhost:5173', 'http://localhost:8000')
                return absolute_uri
            return url
        except Exception:
            return None


class UserStorageSerializer(serializers.ModelSerializer):
    """User storage serializer"""
    user_email = serializers.EmailField(source='user_profile.user.email', read_only=True)
    
    class Meta:
        model = UserStorage
        fields = [
            'id', 'user_profile', 'user_email',
            'filename', 'file_type', 'file_size', 'mime_type',
            's3_bucket', 's3_key', 's3_region', 's3_path',
            'md5_checksum', 'download_count', 'last_accessed_at',
            'is_deleted', 'deleted_at', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 's3_path', 'download_count', 'last_accessed_at',
            'is_deleted', 'deleted_at', 'created_at', 'updated_at'
        ]


class AuditLogSerializer(serializers.ModelSerializer):
    """Audit log serializer"""
    user_email = serializers.CharField(read_only=True)
    resource_name = serializers.CharField(source='resource_repr', read_only=True)
    
    class Meta:
        model = AuditLog
        fields = [
            'id', 'user', 'user_email', 'action',
            'resource_type', 'resource_id', 'resource_name',
            'timestamp', 'ip_address', 'user_agent',
            'changes', 'metadata', 'success', 'error_message'
        ]
        read_only_fields = fields  # Audit logs are read-only


class UserPermissionCheckSerializer(serializers.Serializer):
    """Serializer for checking user permissions"""
    permission_code = serializers.CharField()
    has_permission = serializers.BooleanField(read_only=True)


class UserModuleCheckSerializer(serializers.Serializer):
    """Serializer for checking user module access"""
    module_code = serializers.CharField()
    has_access = serializers.BooleanField(read_only=True)


class AccessRequestSerializer(serializers.ModelSerializer):
    """Serializer for module access requests."""
    user_email = serializers.EmailField(source='user_profile.user.email', read_only=True)
    user_name = serializers.SerializerMethodField()
    module_name = serializers.CharField(source='module.name', read_only=True)
    module_code = serializers.CharField(source='module.code', read_only=True)
    reviewed_by_email = serializers.EmailField(
        source='reviewed_by.email', read_only=True, allow_null=True
    )

    class Meta:
        model = AccessRequest
        fields = [
            'id', 'user_profile', 'user_email', 'user_name',
            'module', 'module_name', 'module_code',
            'reason', 'status',
            'reviewed_by', 'reviewed_by_email', 'reviewed_at', 'admin_note',
            'created_at',
        ]
        read_only_fields = [
            'id', 'status', 'reviewed_by', 'reviewed_at', 'created_at',
        ]

    def get_user_name(self, obj):
        u = obj.user_profile.user
        return f"{u.first_name} {u.last_name}".strip() or u.email


# ═════════════════════════════════════════════════════════════════════════════
# Enhanced Profile Serializers — Achievements, Experience, Social Media
# ═════════════════════════════════════════════════════════════════════════════

class AchievementSerializer(serializers.ModelSerializer):
    """Serializer for user achievements — sports, academics, professional, genius records."""

    # Read-only computed fields
    category_label = serializers.SerializerMethodField()
    category_icon = serializers.SerializerMethodField()
    category_color = serializers.SerializerMethodField()
    level_label = serializers.SerializerMethodField()
    level_icon = serializers.SerializerMethodField()
    user_profile = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Achievement
        fields = [
            'id', 'user_profile',
            'title', 'category', 'category_label', 'category_icon', 'category_color',
            'description', 'level', 'level_label', 'level_icon',
            'achieved_date', 'location', 'organization',
            'certificate_url', 'media_url',
            'is_public', 'is_verified', 'display_order',
            'created_at', 'updated_at',
        ]
        # Ownership is assigned from request.user by the viewset.  Keeping this
        # read-only also prevents a client from creating records for another user.
        read_only_fields = ['id', 'user_profile', 'created_at', 'updated_at', 'is_verified']
    
    def get_category_label(self, obj):
        """Return human-readable category label."""
        from apps.rbac.profile_config import get_achievement_category
        cat_config = get_achievement_category(obj.category)
        return cat_config.get('label', obj.category.title())
    
    def get_category_icon(self, obj):
        """Return category emoji icon."""
        from apps.rbac.profile_config import get_achievement_category
        cat_config = get_achievement_category(obj.category)
        return cat_config.get('icon', '🏆')
    
    def get_category_color(self, obj):
        """Return category color for UI."""
        from apps.rbac.profile_config import get_achievement_category
        cat_config = get_achievement_category(obj.category)
        return cat_config.get('color', 'blue')
    
    def get_level_label(self, obj):
        """Return human-readable level label."""
        if not obj.level:
            return None
        from apps.rbac.profile_config import ACHIEVEMENT_LEVELS
        for level in ACHIEVEMENT_LEVELS:
            if level['value'] == obj.level:
                return level['label']
        return obj.level.title()
    
    def get_level_icon(self, obj):
        """Return level emoji icon."""
        if not obj.level:
            return None
        from apps.rbac.profile_config import ACHIEVEMENT_LEVELS
        for level in ACHIEVEMENT_LEVELS:
            if level['value'] == obj.level:
                return level['icon']
        return '🏅'


class WorkExperienceSerializer(serializers.ModelSerializer):
    """Serializer for work experience entries."""
    
    # Read-only computed fields
    duration_text = serializers.SerializerMethodField()
    employment_type_label = serializers.SerializerMethodField()
    user_profile = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = WorkExperience
        fields = [
            'id', 'user_profile',
            'company_name', 'company_logo_url',
            'job_title', 'employment_type', 'employment_type_label',
            'industry', 'location',
            'start_date', 'end_date', 'is_current', 'duration_text',
            'description', 'achievements_text', 'skills_used',
            'is_public', 'display_order',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'user_profile', 'created_at', 'updated_at']
    
    def get_duration_text(self, obj):
        """Calculate and return human-readable duration."""
        from datetime import date
        from dateutil.relativedelta import relativedelta
        
        end = obj.end_date if obj.end_date else date.today()
        delta = relativedelta(end, obj.start_date)
        
        years = delta.years
        months = delta.months
        
        if years > 0 and months > 0:
            return f"{years} yr{'' if years == 1 else 's'} {months} mo"
        elif years > 0:
            return f"{years} year{'' if years == 1 else 's'}"
        elif months > 0:
            return f"{months} month{'' if months == 1 else 's'}"
        else:
            return "< 1 month"
    
    def get_employment_type_label(self, obj):
        """Return human-readable employment type label."""
        if not obj.employment_type:
            return None
        from apps.rbac.profile_config import EMPLOYMENT_TYPES
        for emp_type in EMPLOYMENT_TYPES:
            if emp_type['value'] == obj.employment_type:
                return emp_type['label']
        return obj.employment_type.replace('_', ' ').title()
    
    def validate(self, data):
        """Validate that end_date is after start_date."""
        start = data.get('start_date')
        end = data.get('end_date')
        is_current = data.get('is_current', False)
        
        # If marked as current, end_date should be None
        if is_current and end is not None:
            raise serializers.ValidationError({
                'end_date': 'Cannot set end_date for current employment'
            })
        
        # If not current, validate date range
        if not is_current and end and start and end < start:
            raise serializers.ValidationError({
                'end_date': 'End date must be after start date'
            })
        
        return data


class SocialMediaLinkSerializer(serializers.ModelSerializer):
    """Serializer for social media and professional network links."""
    
    # Read-only computed fields
    platform_label = serializers.SerializerMethodField()
    platform_icon = serializers.SerializerMethodField()
    platform_color = serializers.SerializerMethodField()
    user_profile = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = SocialMediaLink
        fields = [
            'id', 'user_profile',
            'platform', 'platform_label', 'platform_icon', 'platform_color',
            'url', 'username',
            'is_verified', 'is_public', 'display_order',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'user_profile', 'created_at', 'updated_at', 'is_verified']
    
    def get_platform_label(self, obj):
        """Return human-readable platform name."""
        from apps.rbac.profile_config import get_social_platform
        platform_config = get_social_platform(obj.platform)
        return platform_config.get('label', obj.platform.title())
    
    def get_platform_icon(self, obj):
        """Return platform icon name (for frontend icon library)."""
        from apps.rbac.profile_config import get_social_platform
        platform_config = get_social_platform(obj.platform)
        return platform_config.get('icon', 'globe')
    
    def get_platform_color(self, obj):
        """Return platform brand color."""
        from apps.rbac.profile_config import get_social_platform
        platform_config = get_social_platform(obj.platform)
        return platform_config.get('color', '#6B7280')
    
    def validate_url(self, value):
        """Validate URL format for the specific platform."""
        import re
        from apps.rbac.profile_config import get_social_platform
        
        # Get platform from initial data
        platform = self.initial_data.get('platform')
        if not platform:
            return value
        
        platform_config = get_social_platform(platform)
        pattern = platform_config.get('validation_pattern')
        
        if pattern:
            if not re.match(pattern, value):
                raise serializers.ValidationError(
                    f"Invalid URL format for {platform_config.get('label')}. "
                    f"Expected format: {platform_config.get('placeholder')}"
                )
        
        return value

    def validate(self, attrs):
        """Give a clear error when the user already linked this platform."""
        request = self.context.get('request')
        platform = attrs.get('platform') or getattr(self.instance, 'platform', None)
        if request and platform and hasattr(request.user, 'rbac_profile'):
            existing = SocialMediaLink.objects.filter(
                user_profile=request.user.rbac_profile,
                platform=platform,
            )
            if self.instance:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise serializers.ValidationError({
                    'platform': 'This platform is already linked. Edit the existing link instead.'
                })
        return attrs


class ProfileDocumentSerializer(serializers.ModelSerializer):
    """
    Serializer for profile documents (Emirates ID, Driving License, etc.)
    Handles S3 file uploads and soft-coded document type metadata.
    """
    
    # Read-only computed fields from soft-coded config
    document_type_label = serializers.SerializerMethodField()
    document_type_icon = serializers.SerializerMethodField()
    document_type_color = serializers.SerializerMethodField()
    document_file_url = serializers.SerializerMethodField()
    document_file_name = serializers.SerializerMethodField()
    is_expired = serializers.ReadOnlyField()
    expires_soon = serializers.ReadOnlyField()
    
    # User details for admin view
    user_email = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()
    user_profile = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ProfileDocument
        fields = [
            'id', 'user_profile', 'user_email', 'user_name',
            'document_type', 'document_type_label', 'document_type_icon', 'document_type_color',
            'document_file', 'document_file_url', 'document_file_name',
            'document_number', 'issue_date', 'expiry_date', 'issuing_authority',
            'verification_status', 'verified_by', 'verified_at', 'rejection_reason',
            'notes', 'is_active', 'is_expired', 'expires_soon',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'user_profile', 'created_at', 'updated_at',
            'verified_by', 'verified_at', 'is_expired', 'expires_soon',
            'document_file_url', 'document_file_name',
        ]
    
    def get_document_type_label(self, obj):
        """Return human-readable document type name."""
        from apps.rbac.profile_config import get_document_type
        doc_config = get_document_type(obj.document_type)
        if doc_config:
            return doc_config.get('label', obj.document_type.replace('_', ' ').title())
        return obj.document_type.replace('_', ' ').title()
    
    def get_document_type_icon(self, obj):
        """Return document type icon emoji."""
        from apps.rbac.profile_config import get_document_type
        doc_config = get_document_type(obj.document_type)
        if doc_config:
            return doc_config.get('icon', '📄')
        return '📄'
    
    def get_document_type_color(self, obj):
        """Return document type color scheme."""
        from apps.rbac.profile_config import get_document_type
        doc_config = get_document_type(obj.document_type)
        if doc_config:
            return {
                'badge_color': doc_config.get('badge_color', 'bg-gray-500'),
                'bg_color': doc_config.get('bg_color', 'bg-gray-50'),
                'text_color': doc_config.get('text_color', 'text-gray-700'),
                'border_color': doc_config.get('border_color', 'border-gray-300'),
            }
        return {
            'badge_color': 'bg-gray-500',
            'bg_color': 'bg-gray-50',
            'text_color': 'text-gray-700',
            'border_color': 'border-gray-300',
        }
    
    def get_document_file_url(self, obj):
        """Return presigned S3 URL or absolute URL for document file."""
        if not obj.document_file:
            return None
        try:
            url = obj.document_file.url
            # S3 presigned URLs are already absolute
            if url.startswith('http'):
                return url
            # Local dev: build absolute URL
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(url)
            return url
        except Exception:
            return None
    
    def get_document_file_name(self, obj):
        """Return original filename from S3 path."""
        if not obj.document_file:
            return None
        try:
            import os
            return os.path.basename(obj.document_file.name)
        except Exception:
            return None
    
    def get_user_email(self, obj):
        """Return user email for admin view."""
        return obj.user_profile.user.email if obj.user_profile else None
    
    def get_user_name(self, obj):
        """Return user full name for admin view."""
        if not obj.user_profile:
            return None
        user = obj.user_profile.user
        full_name = f"{user.first_name} {user.last_name}".strip()
        return full_name or user.email
    
    def validate_document_file(self, value):
        """Validate document file size and format."""
        import os
        from apps.rbac.profile_config import get_document_type
        
        # Get document type from initial data
        doc_type = self.initial_data.get('document_type')
        if not doc_type:
            # If updating existing document, use current type
            if self.instance:
                doc_type = self.instance.document_type
            else:
                raise serializers.ValidationError("Document type is required")
        
        doc_config = get_document_type(doc_type)
        if not doc_config:
            raise serializers.ValidationError(f"Invalid document type: {doc_type}")
        
        # Check file size
        max_size_mb = doc_config.get('max_file_size_mb', 5)
        max_size_bytes = max_size_mb * 1024 * 1024
        if value.size > max_size_bytes:
            raise serializers.ValidationError(
                f"File size must be less than {max_size_mb}MB. Current size: {value.size / (1024 * 1024):.2f}MB"
            )
        
        # Check file format
        allowed_formats = doc_config.get('allowed_formats', ['pdf', 'jpg', 'jpeg', 'png'])
        file_ext = os.path.splitext(value.name)[1][1:].lower()  # Remove leading dot
        if file_ext not in allowed_formats:
            raise serializers.ValidationError(
                f"Invalid file format. Allowed: {', '.join(allowed_formats).upper()}. Got: {file_ext.upper()}"
            )
        
        return value
    
    def validate_expiry_date(self, value):
        """Validate expiry date is not in the past."""
        if value:
            from django.utils import timezone
            if value < timezone.now().date():
                # Allow past dates but auto-mark as expired
                pass
        return value

