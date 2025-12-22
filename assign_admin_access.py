"""
Assign all modules and permissions to Super Admin and Admin roles
"""
import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Role, Module, RoleModule, Permission, RolePermission
from django.contrib.auth import get_user_model

User = get_user_model()

# Get an admin user to use as granted_by
admin_user = User.objects.filter(is_superuser=True).first()

if not admin_user:
    print("No superuser found. Please create one first.")
    sys.exit(1)

# Get Super Admin and Admin roles
admin_roles = Role.objects.filter(code__in=['super_admin', 'admin'])

# Get all active modules and permissions
modules = Module.objects.filter(is_active=True)
permissions = Permission.objects.filter(is_active=True)

print("=" * 70)
print("ASSIGNING FULL ACCESS TO ADMIN ROLES")
print("=" * 70)

for role in admin_roles:
    print(f"\nProcessing role: {role.name}")
    
    # Clear existing assignments
    RoleModule.objects.filter(role=role).delete()
    RolePermission.objects.filter(role=role).delete()
    
    # Assign all modules
    for module in modules:
        RoleModule.objects.create(
            role=role,
            module=module,
            granted_by=admin_user
        )
    
    # Assign all permissions
    for perm in permissions:
        RolePermission.objects.create(
            role=role,
            permission=perm,
            granted_by=admin_user
        )
    
    print(f"✓ Assigned {modules.count()} modules")
    print(f"✓ Assigned {permissions.count()} permissions")

print("\n" + "=" * 70)
print("✅ ADMIN ROLES CONFIGURED SUCCESSFULLY!")
print("=" * 70)
print("\nSuper Admin and Admin now have access to ALL features and permissions")
print("All other users will get custom roles based on selected features")
print("=" * 70)
