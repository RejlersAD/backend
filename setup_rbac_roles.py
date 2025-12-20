import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Role, Module, Permission, UserProfile, UserRole
from apps.users.models import User

print("Creating RBAC roles and permissions...")

# Create modules
modules_data = [
    {'name': 'User Management', 'code': 'user_management', 'order': 1},
    {'name': 'Role Management', 'code': 'role_management', 'order': 2},
    {'name': 'Permission Management', 'code': 'permission_management', 'order': 3},
    {'name': 'System Settings', 'code': 'system_settings', 'order': 4},
]

for module_data in modules_data:
    module, created = Module.objects.get_or_create(
        code=module_data['code'],
        defaults=module_data
    )
    if created:
        print(f"✅ Created module: {module.name}")

# Create roles
roles_data = [
    {'name': 'Super Admin', 'code': 'super_admin', 'level': 1, 'is_system_role': True},
    {'name': 'Admin', 'code': 'admin', 'level': 2, 'is_system_role': True},
    {'name': 'Manager', 'code': 'manager', 'level': 3, 'is_system_role': True},
    {'name': 'User', 'code': 'user', 'level': 4, 'is_system_role': True},
]

for role_data in roles_data:
    role, created = Role.objects.get_or_create(
        code=role_data['code'],
        defaults=role_data
    )
    if created:
        print(f"✅ Created role: {role.name}")

# Create basic permissions
user_mgmt_module = Module.objects.get(code='user_management')
permissions_data = [
    {'name': 'View Users', 'code': 'view_users', 'action': 'view', 'module': user_mgmt_module},
    {'name': 'Create Users', 'code': 'create_users', 'action': 'create', 'module': user_mgmt_module},
    {'name': 'Edit Users', 'code': 'edit_users', 'action': 'edit', 'module': user_mgmt_module},
    {'name': 'Delete Users', 'code': 'delete_users', 'action': 'delete', 'module': user_mgmt_module},
]

for perm_data in permissions_data:
    perm, created = Permission.objects.get_or_create(
        code=perm_data['code'],
        defaults=perm_data
    )
    if created:
        print(f"✅ Created permission: {perm.name}")

# Assign super_admin role to user
user = User.objects.get(email='tanzeem.agra@rejlers.ae')
profile = UserProfile.objects.get(user=user)
super_admin_role = Role.objects.get(code='super_admin')

user_role, created = UserRole.objects.get_or_create(
    user_profile=profile,
    role=super_admin_role
)

if created:
    print(f"\n✅ Assigned super_admin role to {user.email}")
else:
    print(f"\nℹ️  User {user.email} already has super_admin role")

print("\n" + "="*50)
print("RBAC setup complete!")
print("="*50)
print(f"\nUser: {user.email}")
print(f"Roles: {[ur.role.name for ur in UserRole.objects.filter(user_profile=profile)]}")
print(f"Is Staff: {user.is_staff}")
print(f"Is Superuser: {user.is_superuser}")
