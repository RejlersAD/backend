"""
RBAC Admin Configuration
"""
from django.contrib import admin
from .models import (
    Organization, Module, Permission, Role, RolePermission, RoleModule,
    UserProfile, UserRole, UserStorage, AuditLog
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'code']


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'order']
    list_filter = ['is_active']
    search_fields = ['name', 'code']
    ordering = ['order', 'name']


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'module', 'action', 'is_active']
    list_filter = ['module', 'action', 'is_active']
    search_fields = ['name', 'code']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'level', 'is_active', 'is_system_role']
    list_filter = ['level', 'is_active', 'is_system_role']
    search_fields = ['name', 'code']


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'organization', 'status', 'employee_id', 'created_at']
    list_filter = ['organization', 'status', 'is_deleted']
    search_fields = ['user__email', 'employee_id']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['user_email', 'action', 'resource_type', 'timestamp', 'success']
    list_filter = ['action', 'resource_type', 'success', 'timestamp']
    search_fields = ['user_email', 'resource_type']
    readonly_fields = list_display + ['changes', 'metadata']
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
