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
    Admins can view organizations, only super admin can create/edit/delete
    """
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['name', 'code', 'primary_contact_email']
    ordering_fields = ['name', 'created_at']
    filterset_fields = ['is_active']
    
    def get_permissions(self):
        """
        Allow admins to read organizations, only super admin can modify
        """
        if self.action in ['list', 'retrieve']:
            permission_classes = [IsAuthenticated, IsAdmin]
        else:
            permission_classes = [IsAuthenticated, IsSuperAdmin]
        return [permission() for permission in permission_classes]
    
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
    Admins can view modules, only super admin can create/edit/delete
    """
    queryset = Module.objects.all()
    serializer_class = ModuleSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['name', 'code']
    ordering_fields = ['order', 'name']
    filterset_fields = ['is_active']
    
    def get_permissions(self):
        """
        Allow admins to read modules, only super admin can modify
        """
        if self.action in ['list', 'retrieve', 'active']:
            permission_classes = [IsAuthenticated, IsAdmin]
        else:
            permission_classes = [IsAuthenticated, IsSuperAdmin]
        return [permission() for permission in permission_classes]
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated, IsAdmin])
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
    
    def get_permissions(self):
        """
        Custom permissions:
        - 'me' action only requires authentication
        - Other actions require user management permissions
        """
        if self.action == 'me':
            return [IsAuthenticated()]
        return [IsAuthenticated(), CanManageUsers()]
    
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
    
    @action(detail=False, methods=['post'])
    def bulk_upload(self, request):
        """
        Bulk upload users from CSV/Excel
        Expected CSV format: email,first_name,last_name,password,department,job_title,phone_number,role_codes,module_codes
        role_codes and module_codes should be comma-separated (e.g., "admin,engineer" or "PID,PFD")
        """
        import csv
        import io
        from django.contrib.auth import get_user_model
        from django.db import transaction
        
        User = get_user_model()
        
        if 'file' not in request.FILES:
            return Response(
                {'error': 'No file provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        file = request.FILES['file']
        organization_id = request.data.get('organization_id')
        
        # Validate file extension
        if not file.name.endswith(('.csv', '.txt')):
            return Response(
                {'error': 'Invalid file format. Please upload a CSV file.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get organization
        try:
            organization = Organization.objects.get(id=organization_id) if organization_id else None
        except Organization.DoesNotExist:
            return Response(
                {'error': 'Organization not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Parse CSV
        try:
            decoded_file = file.read().decode('utf-8')
            io_string = io.StringIO(decoded_file)
            reader = csv.DictReader(io_string)
            
            results = {
                'success': [],
                'failed': [],
                'skipped': []
            }
            
            with transaction.atomic():
                for row_num, row in enumerate(reader, start=2):  # Start at 2 (1 is header)
                    try:
                        # Validate required fields
                        email = row.get('email', '').strip()
                        if not email:
                            results['failed'].append({
                                'row': row_num,
                                'email': email or 'N/A',
                                'error': 'Email is required'
                            })
                            continue
                        
                        # Check if user already exists
                        if User.objects.filter(email=email).exists():
                            results['skipped'].append({
                                'row': row_num,
                                'email': email,
                                'reason': 'User already exists'
                            })
                            continue
                        
                        # Generate unique username from email
                        base_username = email.split('@')[0]
                        username = base_username
                        counter = 1
                        while User.objects.filter(username=username).exists():
                            username = f"{base_username}{counter}"
                            counter += 1
                        
                        # Create user
                        user = User.objects.create_user(
                            username=username,
                            email=email,
                            first_name=row.get('first_name', '').strip(),
                            last_name=row.get('last_name', '').strip(),
                            password=row.get('password', 'TempPass@123').strip()
                        )
                        
                        # Create user profile
                        profile = UserProfile.objects.create(
                            user=user,
                            organization=organization,
                            department=row.get('department', '').strip(),
                            job_title=row.get('job_title', '').strip(),
                            phone_number=row.get('phone_number', '').strip(),
                            status='active'
                        )
                        
                        # Assign roles
                        role_codes = row.get('role_codes', '').strip()
                        if role_codes:
                            role_code_list = [r.strip() for r in role_codes.split(',')]
                            roles = Role.objects.filter(code__in=role_code_list, is_active=True)
                            for idx, role in enumerate(roles):
                                UserRole.objects.create(
                                    user_profile=profile,
                                    role=role,
                                    assigned_by=request.user,
                                    is_primary=(idx == 0)
                                )
                        
                        # Assign modules
                        module_codes = row.get('module_codes', '').strip()
                        if module_codes:
                            module_code_list = [m.strip() for m in module_codes.split(',')]
                            modules = Module.objects.filter(code__in=module_code_list, is_active=True)
                            for module in modules:
                                profile.modules.add(module)
                        
                        results['success'].append({
                            'row': row_num,
                            'email': email,
                            'name': f"{user.first_name} {user.last_name}".strip()
                        })
                        
                        # Create audit log
                        create_audit_log(
                            user=request.user,
                            action='bulk_create',
                            resource_type='UserProfile',
                            resource_id=profile.id,
                            resource_repr=str(profile),
                            metadata={'source': 'bulk_upload'},
                            ip_address=request.META.get('REMOTE_ADDR'),
                            user_agent=request.META.get('HTTP_USER_AGENT', '')
                        )
                        
                    except Exception as e:
                        results['failed'].append({
                            'row': row_num,
                            'email': row.get('email', 'N/A'),
                            'error': str(e)
                        })
            
            # Create summary audit log
            create_audit_log(
                user=request.user,
                action='bulk_upload',
                resource_type='UserProfile',
                resource_id=None,
                resource_repr='Bulk User Upload',
                metadata={
                    'success_count': len(results['success']),
                    'failed_count': len(results['failed']),
                    'skipped_count': len(results['skipped'])
                },
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            return Response({
                'message': 'Bulk upload completed',
                'summary': {
                    'total_processed': len(results['success']) + len(results['failed']) + len(results['skipped']),
                    'successful': len(results['success']),
                    'failed': len(results['failed']),
                    'skipped': len(results['skipped'])
                },
                'details': results
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to process file: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def download_template(self, request):
        """Download CSV template for bulk upload"""
        import csv
        from django.http import HttpResponse
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="user_bulk_upload_template.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'email', 'first_name', 'last_name', 'password', 
            'department', 'job_title', 'phone_number', 
            'role_codes', 'module_codes'
        ])
        writer.writerow([
            'john.doe@company.com', 'John', 'Doe', 'SecurePass@123',
            'Engineering', 'Senior Engineer', '+971501234567',
            'engineer,reviewer', 'PID,PFD,CRS'
        ])
        writer.writerow([
            'jane.smith@company.com', 'Jane', 'Smith', 'SecurePass@123',
            'Management', 'Project Manager', '+971507654321',
            'manager', 'PID,PFD,CRS,PROJECT_CONTROL'
        ])
        
        return response
    
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
    
    @action(detail=False, methods=['get'])
    def my_features(self, request):
        """Get list of features current user has access to"""
        from .utils import get_user_accessible_features
        
        features = get_user_accessible_features(request.user)
        return Response({
            'features': list(features.values()),
            'accessible_count': sum(1 for f in features.values() if f['accessible'])
        })


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


# ============================================================================
# ANALYTICS VIEWSETS - AI-Powered Admin Features
# ============================================================================

from .analytics_models import (
    SystemMetrics, UserActivityAnalytics, SecurityAlert, PredictiveInsight,
    FeatureUsageAnalytics, ErrorLogAnalytics, SystemHealthCheck
)
from .analytics_serializers import (
    SystemMetricsSerializer, UserActivityAnalyticsSerializer, SecurityAlertSerializer,
    PredictiveInsightSerializer, FeatureUsageAnalyticsSerializer, ErrorLogAnalyticsSerializer,
    SystemHealthCheckSerializer, DashboardStatsSerializer, RealTimeActivitySerializer
)
from datetime import timedelta, datetime
from django.db.models import Avg, Sum, Count, Max, Min, F


class AnalyticsDashboardViewSet(viewsets.ViewSet):
    """
    AI-Powered Analytics Dashboard
    Comprehensive admin overview with real-time insights
    """
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    
    @action(detail=False, methods=['get'])
    def overview(self, request):
        """
        Get comprehensive dashboard overview
        Includes system health, user stats, security alerts, and AI insights
        """
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        
        # User Statistics
        total_users = UserProfile.objects.filter(is_deleted=False).count()
        active_today = UserActivityAnalytics.objects.filter(
            date=today, 
            login_count__gt=0
        ).count()
        
        # System Metrics (Latest)
        latest_metrics = SystemMetrics.objects.first()
        
        # Security Alerts
        active_alerts = SecurityAlert.objects.filter(status='new').count()
        critical_alerts = SecurityAlert.objects.filter(
            status='new',
            severity='critical'
        ).count()
        
        # AI Predictions
        active_predictions = PredictiveInsight.objects.filter(
            is_active=True,
            is_acknowledged=False
        ).count()
        high_impact = PredictiveInsight.objects.filter(
            is_active=True,
            is_acknowledged=False,
            impact_level='high'
        ).count()
        
        # Error Statistics
        errors_today = ErrorLogAnalytics.objects.filter(
            last_occurrence__date=today,
            status='open'
        ).count()
        critical_errors = ErrorLogAnalytics.objects.filter(
            status='open',
            severity='critical'
        ).count()
        
        # User Growth
        users_yesterday = UserProfile.objects.filter(
            created_at__date__lte=yesterday,
            is_deleted=False
        ).count()
        growth_rate = ((total_users - users_yesterday) / users_yesterday * 100) if users_yesterday > 0 else 0
        
        # System Health
        latest_health = SystemHealthCheck.objects.first()
        health_score = latest_health.health_score if latest_health else 100.0
        
        data = {
            'total_users': total_users,
            'active_users_today': active_today,
            'total_api_requests_today': latest_metrics.api_requests_count if latest_metrics else 0,
            'system_health_score': health_score,
            'avg_response_time_ms': latest_metrics.avg_response_time_ms if latest_metrics else 0,
            'success_rate_percentage': latest_metrics.success_rate_percentage if latest_metrics else 100,
            'active_connections': latest_metrics.active_connections if latest_metrics else 0,
            'active_alerts_count': active_alerts,
            'critical_alerts_count': critical_alerts,
            'active_predictions_count': active_predictions,
            'high_impact_insights_count': high_impact,
            'errors_today': errors_today,
            'critical_errors_count': critical_errors,
            'user_growth_percentage': round(growth_rate, 2),
            'engagement_trend': 'growing' if growth_rate > 0 else 'stable',
        }
        
        serializer = DashboardStatsSerializer(data)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def real_time_activity(self, request):
        """
        Get real-time activity feed
        Recent user actions, alerts, and system events
        """
        limit = int(request.query_params.get('limit', 20))
        
        # Get recent audit logs
        recent_audits = AuditLog.objects.select_related('user').order_by('-timestamp')[:limit]
        
        activities = []
        for audit in recent_audits:
            activities.append({
                'activity_type': audit.action,
                'user_email': audit.user_email,
                'description': f"{audit.action.title()} {audit.resource_type}",
                'timestamp': audit.timestamp,
                'severity': 'high' if not audit.success else 'normal',
                'metadata': {
                    'resource_id': audit.resource_id,
                    'success': audit.success,
                    'changes': audit.changes
                }
            })
        
        serializer = RealTimeActivitySerializer(activities, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def system_performance(self, request):
        """
        Get system performance metrics over time
        """
        days = int(request.query_params.get('days', 7))
        start_date = timezone.now() - timedelta(days=days)
        
        metrics = SystemMetrics.objects.filter(
            timestamp__gte=start_date
        ).order_by('timestamp').values(
            'timestamp', 'avg_response_time_ms', 'success_rate_percentage',
            'cpu_usage_percentage', 'memory_usage_mb', 'active_connections',
            'api_requests_count', 'failed_requests_count'
        )
        
        return Response(list(metrics))
    
    @action(detail=False, methods=['get'])
    def user_engagement_trends(self, request):
        """
        Get user engagement trends and patterns
        """
        days = int(request.query_params.get('days', 30))
        start_date = timezone.now().date() - timedelta(days=days)
        
        analytics = UserActivityAnalytics.objects.filter(
            date__gte=start_date
        ).values('date').annotate(
            total_logins=Sum('login_count'),
            avg_engagement=Avg('engagement_score'),
            avg_productivity=Avg('productivity_score'),
            users_with_anomalies=Count('id', filter=Q(anomaly_detected=True))
        ).order_by('date')
        
        return Response(list(analytics))


class SystemMetricsViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for system performance metrics
    Read-only for admins to monitor system health
    """
    queryset = SystemMetrics.objects.all()
    serializer_class = SystemMetricsSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['timestamp']
    ordering = ['-timestamp']
    
    @action(detail=False, methods=['get'])
    def latest(self, request):
        """Get the most recent metrics"""
        latest = self.queryset.first()
        if latest:
            serializer = self.get_serializer(latest)
            return Response(serializer.data)
        return Response({})
    
    @action(detail=False, methods=['get'])
    def averages(self, request):
        """Get average metrics over a time period"""
        days = int(request.query_params.get('days', 7))
        start_date = timezone.now() - timedelta(days=days)
        
        averages = self.queryset.filter(timestamp__gte=start_date).aggregate(
            avg_response_time=Avg('avg_response_time_ms'),
            avg_success_rate=Avg('success_rate_percentage'),
            avg_cpu=Avg('cpu_usage_percentage'),
            avg_memory=Avg('memory_usage_mb'),
            total_requests=Sum('api_requests_count'),
            total_failed=Sum('failed_requests_count')
        )
        
        return Response(averages)


class UserActivityAnalyticsViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for user behavior analytics
    AI-powered insights into user patterns and engagement
    """
    queryset = UserActivityAnalytics.objects.select_related('user').all()
    serializer_class = UserActivityAnalyticsSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['date', 'anomaly_detected', 'usage_pattern']
    search_fields = ['user__email', 'user__first_name', 'user__last_name']
    ordering = ['-date']
    
    @action(detail=False, methods=['get'])
    def top_engaged_users(self, request):
        """Get users with highest engagement scores"""
        days = int(request.query_params.get('days', 7))
        start_date = timezone.now().date() - timedelta(days=days)
        limit = int(request.query_params.get('limit', 10))
        
        top_users = self.queryset.filter(date__gte=start_date).values(
            'user__email', 'user__first_name', 'user__last_name'
        ).annotate(
            avg_engagement=Avg('engagement_score'),
            total_logins=Sum('login_count'),
            total_actions=Sum('drawings_uploaded') + Sum('analyses_completed')
        ).order_by('-avg_engagement')[:limit]
        
        return Response(list(top_users))
    
    @action(detail=False, methods=['get'])
    def anomalies(self, request):
        """Get users with detected anomalies"""
        days = int(request.query_params.get('days', 7))
        start_date = timezone.now().date() - timedelta(days=days)
        
        anomalies = self.queryset.filter(
            date__gte=start_date,
            anomaly_detected=True
        ).select_related('user').order_by('-risk_score')
        
        serializer = self.get_serializer(anomalies, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def user_timeline(self, request, pk=None):
        """Get activity timeline for a specific user"""
        user_id = pk
        days = int(request.query_params.get('days', 30))
        start_date = timezone.now().date() - timedelta(days=days)
        
        timeline = self.queryset.filter(
            user_id=user_id,
            date__gte=start_date
        ).order_by('date')
        
        serializer = self.get_serializer(timeline, many=True)
        return Response(serializer.data)


class SecurityAlertViewSet(viewsets.ModelViewSet):
    """
    ViewSet for security alerts
    AI-powered threat detection and management
    """
    queryset = SecurityAlert.objects.select_related('user', 'resolved_by').all()
    serializer_class = SecurityAlertSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['severity', 'status', 'alert_type']
    search_fields = ['title', 'description', 'user__email']
    ordering = ['-detection_time']
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Mark alert as resolved"""
        alert = self.get_object()
        alert.status = 'resolved'
        alert.resolved_at = timezone.now()
        alert.resolved_by = request.user
        alert.resolution_notes = request.data.get('notes', '')
        alert.save()
        
        serializer = self.get_serializer(alert)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def investigate(self, request, pk=None):
        """Mark alert as under investigation"""
        alert = self.get_object()
        alert.status = 'investigating'
        alert.save()
        
        serializer = self.get_serializer(alert)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def critical(self, request):
        """Get all critical unresolved alerts"""
        critical_alerts = self.queryset.filter(
            severity='critical',
            status__in=['new', 'investigating']
        )
        
        serializer = self.get_serializer(critical_alerts, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get security alert statistics"""
        days = int(request.query_params.get('days', 30))
        start_date = timezone.now() - timedelta(days=days)
        
        stats = {
            'total_alerts': self.queryset.filter(detection_time__gte=start_date).count(),
            'by_severity': dict(self.queryset.filter(
                detection_time__gte=start_date
            ).values('severity').annotate(count=Count('id')).values_list('severity', 'count')),
            'by_status': dict(self.queryset.filter(
                detection_time__gte=start_date
            ).values('status').annotate(count=Count('id')).values_list('status', 'count')),
            'resolution_time_avg_hours': 0,  # Calculate from resolved alerts
        }
        
        return Response(stats)


class PredictiveInsightViewSet(viewsets.ModelViewSet):
    """
    ViewSet for AI-generated predictions and insights
    Machine learning powered recommendations
    """
    queryset = PredictiveInsight.objects.select_related('acknowledged_by').all()
    serializer_class = PredictiveInsightSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['insight_type', 'impact_level', 'is_active', 'is_acknowledged']
    ordering = ['-created_at']
    
    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        """Acknowledge an insight"""
        insight = self.get_object()
        insight.is_acknowledged = True
        insight.acknowledged_by = request.user
        insight.acknowledged_at = timezone.now()
        insight.save()
        
        serializer = self.get_serializer(insight)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def pending(self, request):
        """Get unacknowledged insights"""
        pending = self.queryset.filter(
            is_active=True,
            is_acknowledged=False
        ).order_by('-confidence_score')
        
        serializer = self.get_serializer(pending, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def high_priority(self, request):
        """Get high-impact unacknowledged insights"""
        high_priority = self.queryset.filter(
            is_active=True,
            is_acknowledged=False,
            impact_level='high'
        ).order_by('-confidence_score')
        
        serializer = self.get_serializer(high_priority, many=True)
        return Response(serializer.data)


class FeatureUsageAnalyticsViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for feature usage analytics
    Track feature adoption and health
    """
    queryset = FeatureUsageAnalytics.objects.all()
    serializer_class = FeatureUsageAnalyticsSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['feature_name', 'date', 'trend']
    ordering = ['-date']
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get summary of all features"""
        days = int(request.query_params.get('days', 7))
        start_date = timezone.now().date() - timedelta(days=days)
        
        summary = self.queryset.filter(date__gte=start_date).values(
            'feature_name'
        ).annotate(
            avg_adoption_rate=Avg('adoption_rate_percentage'),
            avg_health_score=Avg('health_score'),
            total_users=Sum('active_users'),
            total_usage=Sum('total_usage_count')
        ).order_by('-total_usage')
        
        return Response(list(summary))
    
    @action(detail=False, methods=['get'])
    def trending(self, request):
        """Get trending features"""
        trending = self.queryset.filter(
            trend='growing'
        ).order_by('-growth_rate_percentage')[:10]
        
        serializer = self.get_serializer(trending, many=True)
        return Response(serializer.data)


class ErrorLogAnalyticsViewSet(viewsets.ModelViewSet):
    """
    ViewSet for error analytics
    AI-powered error tracking and root cause analysis
    """
    queryset = ErrorLogAnalytics.objects.all()
    serializer_class = ErrorLogAnalyticsSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['severity', 'status', 'error_type']
    search_fields = ['error_type', 'error_message']
    ordering = ['-last_occurrence']
    
    @action(detail=True, methods=['post'])
    def mark_resolved(self, request, pk=None):
        """Mark error as resolved"""
        error = self.get_object()
        error.status = 'resolved'
        error.resolution_notes = request.data.get('notes', '')
        error.resolved_at = timezone.now()
        error.save()
        
        serializer = self.get_serializer(error)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def critical_errors(self, request):
        """Get all critical unresolved errors"""
        critical = self.queryset.filter(
            severity='critical',
            status='open'
        ).order_by('-occurrence_count')
        
        serializer = self.get_serializer(critical, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get error statistics"""
        days = int(request.query_params.get('days', 7))
        start_date = timezone.now() - timedelta(days=days)
        
        stats = {
            'total_errors': self.queryset.filter(last_occurrence__gte=start_date).count(),
            'total_occurrences': self.queryset.filter(
                last_occurrence__gte=start_date
            ).aggregate(total=Sum('occurrence_count'))['total'] or 0,
            'by_severity': dict(self.queryset.filter(
                last_occurrence__gte=start_date
            ).values('severity').annotate(count=Count('id')).values_list('severity', 'count')),
            'affected_users': self.queryset.filter(
                last_occurrence__gte=start_date
            ).aggregate(total=Sum('affected_users_count'))['total'] or 0,
        }
        
        return Response(stats)


class SystemHealthCheckViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for system health monitoring
    Real-time system status and diagnostics
    """
    queryset = SystemHealthCheck.objects.all()
    serializer_class = SystemHealthCheckSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    ordering = ['-check_time']
    
    @action(detail=False, methods=['get'])
    def latest(self, request):
        """Get latest health check"""
        latest = self.queryset.first()
        if latest:
            serializer = self.get_serializer(latest)
            return Response(serializer.data)
        return Response({})
    
    @action(detail=False, methods=['get'])
    def history(self, request):
        """Get health check history"""
        hours = int(request.query_params.get('hours', 24))
        start_time = timezone.now() - timedelta(hours=hours)
        
        history = self.queryset.filter(check_time__gte=start_time).order_by('check_time')
        serializer = self.get_serializer(history, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def component_status(self, request):
        """Get current status of all system components"""
        latest = self.queryset.first()
        if latest:
            return Response({
                'database': latest.database_status,
                'redis': latest.redis_status,
                'celery': latest.celery_status,
                'storage': latest.storage_status,
                'api': latest.api_status,
                'overall': latest.overall_status,
                'health_score': latest.health_score,
                'check_time': latest.check_time,
            })
        return Response({})

