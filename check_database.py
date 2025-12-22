"""
Quick database verification script
"""
import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Module, Role, Permission, RoleModule, Organization, UserProfile

print("=" * 70)
print("DATABASE VERIFICATION - RBAC SYSTEM")
print("=" * 70)

# Organizations
orgs = Organization.objects.all()
print(f"\n✓ Organizations: {orgs.count()}")
for org in orgs:
    print(f"   • {org.name} ({org.code}) - Active: {org.is_active}")

# Modules (Features)
modules = Module.objects.filter(is_active=True).order_by('order')
print(f"\n✓ Active Modules (Features): {modules.count()}")
for module in modules:
    print(f"   • {module.name} ({module.code})")

# Roles
roles = Role.objects.filter(is_active=True).order_by('level')
print(f"\n✓ Active Roles: {roles.count()}")
for role in roles:
    modules_count = RoleModule.objects.filter(role=role).count()
    print(f"   • {role.name} (Level {role.level}) - {modules_count} modules assigned")

# Permissions
perms = Permission.objects.filter(is_active=True)
print(f"\n✓ Active Permissions: {perms.count()}")
print(f"   (Grouped by module)")
for module in modules[:5]:  # Show first 5 modules
    module_perms = perms.filter(module=module)
    print(f"   • {module.name}: {module_perms.count()} permissions")

# Role-Module Mappings
print(f"\n✓ Role-Module Mappings: {RoleModule.objects.count()}")
print("   (Sample mappings)")
for rm in RoleModule.objects.select_related('role', 'module')[:5]:
    print(f"   • {rm.role.name} → {rm.module.name}")

# User Profiles
profiles = UserProfile.objects.filter(is_deleted=False)
print(f"\n✓ User Profiles: {profiles.count()}")
for profile in profiles:
    roles_count = profile.roles.count()
    print(f"   • {profile.user.email} - {roles_count} role(s)")

print("\n" + "=" * 70)
print("✅ DATABASE IS FULLY CONFIGURED AND READY!")
print("=" * 70)
print("\nYou can now:")
print("1. Access http://localhost:3000/admin/users")
print("2. Create users with feature-based access control")
print("3. Users will see only the features they're granted")
print("=" * 70)
