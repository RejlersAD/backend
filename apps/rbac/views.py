"""
RBAC Views - DRF ViewSets for Super Admin Dashboard
"""
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q, Count
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    Organization, Module, Permission, Role, RolePermission, RoleModule,
    UserProfile, UserRole, UserStorage, AuditLog
)
from .serializers import (
    OrganizationSerializer, ModuleSerializer, PermissionSerializer,
    RoleSerializer, RoleListSerializer, RolePermissionSerializer, RoleModuleSerializer,
    UserProfileSerializer, UserProfileListSerializer, UserRoleSerializer,
    UserStorageSerializer, AuditLogSerializer,
    UserPermissionCheckSerializer, UserModuleCheckSerializer
)
from .permissions import (
    IsSuperAdmin, IsAdmin, CanManageUsers, CanManageRoles, SameOrganization
)
from .utils import create_audit_log
from .s3_service import S3Service


class OrganizationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing organizations
    Only super admin can manage organizations
    """
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['name', 'code', 'primary_contact_email']
    ordering_fields = ['name', 'created_at']
    filterset_fields = ['is_active']
    
    def perform_create(self, serializer):
        org = serializer.save()
        create_audit_log(
            user=self.request.user,
            action='create',
            resource_type='Organization',
            resource_id=org.id,
            resource_repr=str(org),
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )
    
    def perform_update(self, serializer):
        old_data = {
            'name': serializer.instance.name,
            'is_active': serializer.instance.is_active
        }
        org = serializer.save()
        create_audit_log(
            user=self.request.user,
            action='update',
            resource_type='Organization',
            resource_id=org.id,
            resource_repr=str(org),
            changes={'old': old_data, 'new': serializer.data},
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )


class ModuleViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing application modules
    Only super admin can create/edit modules
    """
    queryset = Module.objects.all()
    serializer_class = ModuleSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['name', 'code']
    ordering_fields = ['order', 'name']
    filterset_fields = ['is_active']
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def active(self, request):
        """Get all active modules"""
        modules = Module.objects.filter(is_active=True).order_by('order', 'name')
        serializer = self.get_serializer(modules, many=True)
        return Response(serializer.data)


class PermissionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing permissions
    Only super admin can create/edit permissions
    """
    queryset = Permission.objects.select_related('module').all()
    serializer_class = PermissionSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['name', 'code']
    ordering_fields = ['module__name', 'action', 'name']
    filterset_fields = ['module', 'action', 'is_active']
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def by_module(self, request):
        """Get permissions grouped by module"""
        module_id = request.query_params.get('module_id')
        if module_id:
            permissions = Permission.objects.filter(
                module_id=module_id,
                is_active=True
            )
        else:
            permissions = Permission.objects.filter(is_active=True)
        
        serializer = self.get_serializer(permissions, many=True)
        return Response(serializer.data)


class RoleViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing roles
    Only super admin can create/edit roles
    """
    queryset = Role.objects.prefetch_related('permissions', 'modules').all()
    permission_classes = [IsAuthenticated, CanManageRoles]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['name', 'code']
    ordering_fields = ['level', 'name']
    filterset_fields = ['level', 'is_active']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return RoleListSerializer
        return RoleSerializer
    
    def perform_create(self, serializer):
        role = serializer.save()
        create_audit_log(
            user=self.request.user,
            action='create',
            resource_type='Role',
            resource_id=role.id,
            resource_repr=str(role),
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )
    
    def perform_update(self, serializer):
        role = serializer.save()
        create_audit_log(
            user=self.request.user,
            action='update',
            resource_type='Role',
            resource_id=role.id,
            resource_repr=str(role),
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )
    
    def perform_destroy(self, instance):
        if instance.is_system_role:
            raise serializers.ValidationError("Cannot delete system roles")
        
        create_audit_log(
            user=self.request.user,
            action='delete',
            resource_type='Role',
            resource_id=instance.id,
            resource_repr=str(instance),
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )
        instance.delete()
    
    @action(detail=True, methods=['post'])
    def assign_permission(self, request, pk=None):
        """Assign permission to role"""
        role = self.get_object()
        permission_id = request.data.get('permission_id')
        
        if not permission_id:
            return Response(
                {'error': 'permission_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            permission = Permission.objects.get(id=permission_id)
            RolePermission.objects.get_or_create(
                role=role,
                permission=permission,
                defaults={'granted_by': request.user}
            )
            
            create_audit_log(
                user=request.user,
                action='permission_grant',
                resource_type='Role',
                resource_id=role.id,
                resource_repr=str(role),
                metadata={'permission': permission.code},
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            return Response({'status': 'permission assigned'})
        except Permission.DoesNotExist:
            return Response(
                {'error': 'Permission not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=True, methods=['post'])
    def revoke_permission(self, request, pk=None):
        """Revoke permission from role"""
        role = self.get_object()
        permission_id = request.data.get('permission_id')
        
        if not permission_id:
            return Response(
                {'error': 'permission_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        deleted_count = RolePermission.objects.filter(
            role=role,
            permission_id=permission_id
        ).delete()[0]
        
        if deleted_count > 0:
            create_audit_log(
                user=request.user,
                action='permission_revoke',
                resource_type='Role',
                resource_id=role.id,
                resource_repr=str(role),
                metadata={'permission_id': str(permission_id)},
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        
        return Response({'status': 'permission revoked', 'count': deleted_count})
    
    @action(detail=True, methods=['post'])
    def assign_module(self, request, pk=None):
        """Assign module to role"""
        role = self.get_object()
        module_id = request.data.get('module_id')
        
        if not module_id:
            return Response(
                {'error': 'module_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            module = Module.objects.get(id=module_id)
            RoleModule.objects.get_or_create(
                role=role,
                module=module,
                defaults={'granted_by': request.user}
            )
            
            return Response({'status': 'module assigned'})
        except Module.DoesNotExist:
            return Response(
                {'error': 'Module not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=True, methods=['post'])
    def revoke_module(self, request, pk=None):
        """Revoke module from role"""
        role = self.get_object()
        module_id = request.data.get('module_id')
        
        if not module_id:
            return Response(
                {'error': 'module_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        deleted_count = RoleModule.objects.filter(
            role=role,
            module_id=module_id
        ).delete()[0]
        
        return Response({'status': 'module revoked', 'count': deleted_count})


class UserProfileViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user profiles
    Admin can manage users in their organization
    Super admin can manage all users
    """
    permission_classes = [IsAuthenticated, CanManageUsers]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['user__email', 'user__first_name', 'user__last_name', 'employee_id']
    ordering_fields = ['created_at', 'user__email', 'last_login_at']
    filterset_fields = ['organization', 'status', 'is_deleted']
    
    def get_queryset(self):
        """Filter users based on role"""
        user = self.request.user
        queryset = UserProfile.objects.select_related(
            'user', 'organization'
        ).prefetch_related('roles').filter(is_deleted=False)
        
        # Super admin sees all
        try:
            profile = user.rbac_profile
            if not profile.roles.filter(code='super_admin', is_active=True).exists():
                # Other admins see only their organization
                queryset = queryset.filter(organization=profile.organization)
        except UserProfile.DoesNotExist:
            return UserProfile.objects.none()
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'list':
            return UserProfileListSerializer
        return UserProfileSerializer
    
    def perform_create(self, serializer):
        profile = serializer.save()
        create_audit_log(
            user=self.request.user,
            action='create',
            resource_type='UserProfile',
            resource_id=profile.id,
            resource_repr=str(profile),
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )
    
    def perform_update(self, serializer):
        profile = serializer.save()
        create_audit_log(
            user=self.request.user,
            action='update',
            resource_type='UserProfile',
            resource_id=profile.id,
            resource_repr=str(profile),
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )
    
    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        """Deactivate user"""
        profile = self.get_object()
        profile.status = 'inactive'
        profile.user.is_active = False
        profile.save()
        profile.user.save()
        
        create_audit_log(
            user=request.user,
            action='update',
            resource_type='UserProfile',
            resource_id=profile.id,
            resource_repr=str(profile),
            metadata={'status_change': 'active -> inactive'},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        return Response({'status': 'user deactivated'})
    
    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        """Activate user"""
        profile = self.get_object()
        profile.status = 'active'
        profile.user.is_active = True
        profile.save()
        profile.user.save()
        
        create_audit_log(
            user=request.user,
            action='update',
            resource_type='UserProfile',
            resource_id=profile.id,
            resource_repr=str(profile),
            metadata={'status_change': 'inactive -> active'},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        return Response({'status': 'user activated'})
    
    @action(detail=True, methods=['delete'])
    def soft_delete(self, request, pk=None):
        """Soft delete user"""
        profile = self.get_object()
        profile.is_deleted = True
        profile.deleted_at = timezone.now()
        profile.deleted_by = request.user
        profile.user.is_active = False
        profile.save()
        profile.user.save()
        
        create_audit_log(
            user=request.user,
            action='delete',
            resource_type='UserProfile',
            resource_id=profile.id,
            resource_repr=str(profile),
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        return Response({'status': 'user soft deleted'})
    
    @action(detail=True, methods=['post'])
    def assign_role(self, request, pk=None):
        """Assign role to user"""
        profile = self.get_object()
        role_id = request.data.get('role_id')
        is_primary = request.data.get('is_primary', False)
        
        if not role_id:
            return Response(
                {'error': 'role_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            role = Role.objects.get(id=role_id)
            user_role, created = UserRole.objects.get_or_create(
                user_profile=profile,
                role=role,
                defaults={'assigned_by': request.user, 'is_primary': is_primary}
            )
            
            create_audit_log(
                user=request.user,
                action='role_assign',
                resource_type='UserProfile',
                resource_id=profile.id,
                resource_repr=str(profile),
                metadata={'role': role.name},
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            return Response({
                'status': 'created' if created else 'already_exists',
                'role': role.name
            })
        except Role.DoesNotExist:
            return Response(
                {'error': 'Role not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=True, methods=['post'])
    def revoke_role(self, request, pk=None):
        """Revoke role from user"""
        profile = self.get_object()
        role_id = request.data.get('role_id')
        
        if not role_id:
            return Response(
                {'error': 'role_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        deleted_count = UserRole.objects.filter(
            user_profile=profile,
            role_id=role_id
        ).delete()[0]
        
        if deleted_count > 0:
            create_audit_log(
                user=request.user,
                action='role_revoke',
                resource_type='UserProfile',
                resource_id=profile.id,
                resource_repr=str(profile),
                metadata={'role_id': str(role_id)},
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        
        return Response({'status': 'role revoked', 'count': deleted_count})
    
    @action(detail=False, methods=['get'])
    def me(self, request):
        """Get current user's profile - creates one if it doesn't exist"""
        import traceback
        
        try:
            # Log the request for debugging
            print(f"\n[DEBUG /rbac/users/me/] User: {request.user}")
            print(f"[DEBUG /rbac/users/me/] User authenticated: {request.user.is_authenticated}")
            print(f"[DEBUG /rbac/users/me/] User email: {getattr(request.user, 'email', 'N/A')}")
            
            # Try to get existing profile
            profile = UserProfile.objects.filter(
                user=request.user,
                is_deleted=False
            ).first()
            
            print(f"[DEBUG /rbac/users/me/] Profile found: {profile is not None}")
            
            # If no profile exists, return user info without RBAC data
            if not profile:
                print(f"[DEBUG /rbac/users/me/] No RBAC profile for {request.user.email}")
                return Response({
                    'id': str(request.user.id),
                    'email': request.user.email,
                    'username': request.user.username,
                    'first_name': request.user.first_name,
                    'last_name': request.user.last_name,
                    'roles': [],
                    'organization': None,
                    'status': 'pending',
                    'message': 'RBAC profile not configured. Please contact administrator.'
                })
            
            # Try to serialize the profile
            print(f"[DEBUG /rbac/users/me/] Serializing profile...")
            serializer = self.get_serializer(profile)
            data = serializer.data
            print(f"[DEBUG /rbac/users/me/] Serialization successful")
            print(f"[DEBUG /rbac/users/me/] Roles count: {len(data.get('roles', []))}")
            
            return Response(data)
            
        except Exception as e:
            # Log the full error for debugging
            print(f"\n[ERROR /rbac/users/me/] Exception occurred: {str(e)}")
            print(f"[ERROR /rbac/users/me/] Exception type: {type(e).__name__}")
            print(f"[ERROR /rbac/users/me/] Traceback:")
            traceback.print_exc()
            
            # Return detailed error in response
            return Response(
                {
                    'error': f'Server error: {str(e)}',
                    'error_type': type(e).__name__,
                    'user': str(request.user),
                    'traceback': traceback.format_exc()
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get user statistics"""
        queryset = self.get_queryset()
        
        stats = {
            'total_users': queryset.count(),
            'active_users': queryset.filter(status='active').count(),
            'inactive_users': queryset.filter(status='inactive').count(),
            'suspended_users': queryset.filter(status='suspended').count(),
            'by_organization': list(queryset.values('organization__name').annotate(count=Count('id'))),
        }
        
        return Response(stats)


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing audit logs
    Read-only access for admins
    """
    queryset = AuditLog.objects.select_related('user').all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated, IsAdmin]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['user_email', 'action', 'resource_type']
    ordering_fields = ['-timestamp']
    filterset_fields = ['action', 'resource_type', 'success']
    
    def get_queryset(self):
        """Filter logs based on organization for non-super-admins"""
        user = self.request.user
        queryset = AuditLog.objects.all()
        
        try:
            profile = user.rbac_profile
            if not profile.roles.filter(code='super_admin', is_active=True).exists():
                # Filter to organization users
                org_user_ids = UserProfile.objects.filter(
                    organization=profile.organization
                ).values_list('user_id', flat=True)
                queryset = queryset.filter(user_id__in=org_user_ids)
        except UserProfile.DoesNotExist:
            return AuditLog.objects.none()
        
        return queryset.order_by('-timestamp')
    
    @action(detail=False, methods=['get'])
    def user_activity(self, request):
        """Get activity logs for specific user"""
        user_id = request.query_params.get('user_id')
        if not user_id:
            return Response(
                {'error': 'user_id parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logs = self.get_queryset().filter(user_id=user_id)[:50]
        serializer = self.get_serializer(logs, many=True)
        return Response(serializer.data)


class StorageViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing file storage with S3 integration
    """
    queryset = UserStorage.objects.filter(is_deleted=False)
    serializer_class = UserStorageSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['file_name', 'category']
    ordering_fields = ['-created_at', 'file_size', 'download_count']
    filterset_fields = ['category', 'mime_type']
    
    def get_queryset(self):
        """Filter files based on user and organization"""
        user = self.request.user
        queryset = UserStorage.objects.filter(is_deleted=False)
        
        try:
            profile = user.userprofile
            # Super admins see all files
            if not profile.has_permission('file_view_all'):
                # Regular users see only their files and organization files
                queryset = queryset.filter(
                    Q(user_profile=profile) | Q(organization=profile.organization)
                )
        except UserProfile.DoesNotExist:
            return UserStorage.objects.none()
        
        return queryset.order_by('-created_at')
    
    @action(detail=False, methods=['post'])
    def generate_upload_url(self, request):
        """
        Generate pre-signed URL for uploading a file to S3
        
        POST /api/v1/rbac/storage/generate_upload_url/
        {
            "file_name": "drawing.pdf",
            "file_size": 1024000,
            "content_type": "application/pdf",
            "category": "pid_analysis",
            "tags": {"project": "ABC-123"}
        }
        """
        file_name = request.data.get('file_name')
        file_size = request.data.get('file_size')
        content_type = request.data.get('content_type')
        category = request.data.get('category', 'general')
        tags = request.data.get('tags', {})
        
        if not file_name or not file_size:
            return Response(
                {'error': 'file_name and file_size are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            s3_service = S3Service(organization=request.user.userprofile.organization)
            result = s3_service.generate_upload_url(
                user=request.user,
                file_name=file_name,
                file_size=file_size,
                content_type=content_type,
                category=category,
                tags=tags
            )
            return Response(result, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """
        Generate pre-signed URL for downloading a file from S3
        
        GET /api/v1/rbac/storage/{id}/download/
        """
        try:
            s3_service = S3Service(organization=request.user.userprofile.organization)
            result = s3_service.generate_download_url(
                storage_id=pk,
                user=request.user
            )
            return Response(result, status=status.HTTP_200_OK)
        except PermissionError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_403_FORBIDDEN
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def verify_upload(self, request, pk=None):
        """
        Verify that file was successfully uploaded to S3
        
        POST /api/v1/rbac/storage/{id}/verify_upload/
        {
            "checksum": "md5hash"
        }
        """
        checksum = request.data.get('checksum')
        
        try:
            s3_service = S3Service(organization=request.user.userprofile.organization)
            result = s3_service.verify_upload(
                storage_id=pk,
                user=request.user,
                checksum=checksum
            )
            return Response({'verified': result}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """
        Get storage statistics for current user
        
        GET /api/v1/rbac/storage/stats/
        """
        try:
            s3_service = S3Service(organization=request.user.userprofile.organization)
            stats = s3_service.get_storage_stats(user=request.user)
            return Response(stats, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def destroy(self, request, *args, **kwargs):
        """Soft delete file from storage"""
        try:
            storage = self.get_object()
            s3_service = S3Service(organization=request.user.userprofile.organization)
            s3_service.delete_file(storage_id=storage.id, user=request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except PermissionError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_403_FORBIDDEN
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
