"""
Verify database state after user creation
"""
import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import UserProfile, Role, Module, RoleModule

print("=" * 70)
print("DATABASE VERIFICATION - USER CREATION WITH MODULES")
print("=" * 70)

# Check test user
test_user = UserProfile.objects.filter(user__email='test.user@example.com').first()

if test_user:
    print(f"\n✅ Test User Found: {test_user.user.email}")
    print(f"   Name: {test_user.user.first_name} {test_user.user.last_name}")
    print(f"   Organization: {test_user.organization.name}")
    
    roles = test_user.roles.all()
    print(f"\n✅ Assigned Roles: {roles.count()}")
    for role in roles:
        print(f"   - {role.name} ({role.code})")
        
        role_modules = RoleModule.objects.filter(role=role)
        print(f"     Modules: {role_modules.count()}")
        for rm in role_modules:
            print(f"       • {rm.module.name}")
    
    modules = test_user.get_all_modules()
    print(f"\n✅ Accessible Modules: {len(modules)}")
    for module in modules:
        print(f"   - {module.name}")
    
    permissions = test_user.get_all_permissions()
    print(f"\n✅ Total Permissions: {permissions.count()}")
else:
    print("\n❌ Test user not found")

# Check all roles
print("\n" + "=" * 70)
print("ALL ROLES IN SYSTEM")
print("=" * 70)

all_roles = Role.objects.filter(is_active=True)
for role in all_roles:
    role_modules = RoleModule.objects.filter(role=role)
    print(f"\n{role.name} ({role.code})")
    print(f"  Level: {role.level}")
    print(f"  Modules: {role_modules.count()}")
    if role_modules.count() <= 10:
        for rm in role_modules:
            print(f"    • {rm.module.name}")

print("\n" + "=" * 70)
print("✅ VERIFICATION COMPLETE")
print("=" * 70)
