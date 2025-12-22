"""
RBAC Serializers
Enterprise-grade serializers for Role-Based Access Control
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import (
    Organization, Module, Permission, Role, RolePermission, RoleModule,
    UserProfile, UserRole, UserStorage, AuditLog
)

User = get_user_model()


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
        return obj.permissions.filter(is_active=True).count()


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
        return obj.user_profiles.filter(userprofile__is_deleted=False).count()
    
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
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'is_active']
        read_only_fields = ['id']


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
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    roles = RoleListSerializer(many=True, read_only=True)
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
    
    # User creation fields
    email = serializers.EmailField(write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    first_name = serializers.CharField(write_only=True, required=False)
    last_name = serializers.CharField(write_only=True, required=False)
    phone = serializers.CharField(write_only=True, required=False)
    
    def validate(self, attrs):
        """Validate required fields for creation"""
        if self.instance is None:
            if 'email' not in attrs:
                raise serializers.ValidationError({'email': 'Email is required for user creation'})
            if 'password' not in attrs:
                raise serializers.ValidationError({'password': 'Password is required for user creation'})
            if 'first_name' not in attrs:
                raise serializers.ValidationError({'first_name': 'First name is required for user creation'})
            if 'last_name' not in attrs:
                raise serializers.ValidationError({'last_name': 'Last name is required for user creation'})
        return attrs
    
    class Meta:
        model = UserProfile
        fields = [
            'id', 'user', 'organization', 'organization_name', 'status', 'is_mfa_enabled',
            'roles', 'role_ids', 'module_ids', 'permissions', 'modules',
            'employee_id', 'department', 'job_title', 'manager',
            'last_login_ip', 'last_login_at', 'failed_login_attempts',
            'is_deleted', 'deleted_at', 'deleted_by',
            'created_at', 'updated_at',
            # Write-only fields for user creation
            'email', 'password', 'first_name', 'last_name', 'phone'
        ]
        read_only_fields = [
            'id', 'user', 'last_login_ip', 'last_login_at', 'failed_login_attempts',
            'is_deleted', 'deleted_at', 'deleted_by', 'created_at', 'updated_at'
        ]
    
    def get_permissions(self, obj):
        """Get all permissions for user"""
        permissions = obj.get_all_permissions()
        return PermissionListSerializer(permissions, many=True).data
    
    def get_modules(self, obj):
        """Get all accessible modules for user"""
        modules = obj.get_all_modules()
        return [{'id': str(m.id), 'code': m.code, 'name': m.name} for m in modules]
    
    def create(self, validated_data):
        role_ids = validated_data.pop('role_ids', [])
        module_ids = validated_data.pop('module_ids', [])
        
        # Extract user data
        email = validated_data.pop('email')
        password = validated_data.pop('password')
        first_name = validated_data.pop('first_name', '')
        last_name = validated_data.pop('last_name', '')
        phone = validated_data.pop('phone', None)
        
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
        
        # Create user with appropriate permissions
        user = User.objects.create_user(
            username=email.split('@')[0],
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            is_superuser=is_super_admin,
            is_staff=is_super_admin
        )
        
        # Set phone if provided
        if phone:
            validated_data['phone'] = phone
        
        # Create profile
        profile = UserProfile.objects.create(user=user, **validated_data)
        
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
        if module_ids:
            request_user = self.context['request'].user
            from django.db import transaction
            
            with transaction.atomic():
                # Create a unique custom role for this user based on email
                user_role_code = f'custom_{email.split("@")[0]}'
                custom_role, created = Role.objects.get_or_create(
                    code=user_role_code,
                    defaults={
                        'name': f'Custom Role - {first_name} {last_name}',
                        'description': f'Custom role for {email} with selected features',
                        'level': 10,  # Higher level for custom roles
                        'is_active': True
                    }
                )
                
                # If role already exists, update the name
                if not created:
                    custom_role.name = f'Custom Role - {first_name} {last_name}'
                    custom_role.description = f'Custom role for {email} with selected features'
                    custom_role.save()
                
                # Assign the custom role to the user
                user_role, _ = UserRole.objects.get_or_create(
                    user_profile=profile,
                    role=custom_role,
                    assigned_by=request_user,
                    defaults={'is_primary': not role_ids}  # Primary if no other roles
                )
                
                # Clear existing module assignments for this custom role
                RoleModule.objects.filter(role=custom_role).delete()
                RolePermission.objects.filter(role=custom_role).delete()
                
                # Assign modules to the role
                for module_id in module_ids:
                    RoleModule.objects.get_or_create(
                        role=custom_role,
                        module_id=module_id,
                        defaults={'granted_by': request_user}
                    )
                
                # Get all permissions for the selected modules and assign them
                permissions = Permission.objects.filter(
                    module_id__in=module_ids,
                    is_active=True
                )
                
                for permission in permissions:
                    RolePermission.objects.get_or_create(
                        role=custom_role,
                        permission=permission,
                        defaults={'granted_by': request_user}
                    )
        
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
        if 'password' in validated_data:
            instance.user.set_password(validated_data.pop('password'))
            instance.user.save()
        
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


class UserProfileListSerializer(serializers.ModelSerializer):
    """Simplified user profile serializer for lists"""
    email = serializers.EmailField(source='user.email', read_only=True)
    full_name = serializers.SerializerMethodField()
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    primary_role = serializers.SerializerMethodField()
    
    class Meta:
        model = UserProfile
        fields = [
            'id', 'email', 'full_name', 'organization_name',
            'status', 'primary_role', 'employee_id', 'department',
            'last_login_at', 'created_at'
        ]
    
    def get_full_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}".strip()
    
    def get_primary_role(self, obj):
        primary = obj.userrole_set.filter(is_primary=True).first()
        if primary:
            return {'id': str(primary.role.id), 'name': primary.role.name}
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
