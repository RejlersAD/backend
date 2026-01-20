#!/usr/bin/env python
"""Create QHSE Module in RBAC System"""
import sys
import os
import django

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Module, Role, RoleModule
from django.contrib.auth import get_user_model

User = get_user_model()

print("\n" + "="*70)
print("🔐 QHSE RBAC Module Setup")
print("="*70 + "\n")

# Create QHSE Module
module, created = Module.objects.get_or_create(
    code='qhse',
    defaults={
        'name': 'QHSE Management',
        'description': 'Quality, Health, Safety, and Environment management module for project tracking and compliance',
        'is_active': True,
        'icon': 'shield-check',
        'order': 5
    }
)

if created:
    print(f"✅ Created QHSE module: {module.name}")
else:
    print(f"ℹ️  QHSE module already exists: {module.name}")

# Assign to Admin and Super Admin roles
admin_roles = Role.objects.filter(level__lte=2, is_active=True)  # Super Admin (1) and Admin (2)
assigned_count = 0

for role in admin_roles:
    role_module, created = RoleModule.objects.get_or_create(
        role=role,
        module=module
    )
    if created:
        print(f"  ✅ Assigned to role: {role.name}")
        assigned_count += 1
    else:
        print(f"  ℹ️  Already assigned to role: {role.name}")

print(f"\n📊 Summary:")
print(f"  • Module Code: {module.code}")
print(f"  • Module Name: {module.name}")
print(f"  • Order: {module.order}")
print(f"  • Roles with Access: {RoleModule.objects.filter(module=module).count()}")

# Count users with access through roles
try:
    users_with_access = User.objects.filter(
        rbac_profile__role__modules=module,
        is_active=True
    ).distinct().count()
    print(f"  • Users with Access: {users_with_access}")
except:
    print(f"  • Users with Access: Check user roles for access")

print("\n✅ QHSE RBAC setup completed!\n")
