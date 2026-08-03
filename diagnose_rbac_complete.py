"""
RBAC System Diagnostic Tool
Comprehensive verification of role assignment system in local and production
"""
import os
import sys
import django

# Setup Django
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole, Module, RoleModule
from apps.rbac.rbac_config import DEFAULT_ROLE_CONFIG
from django.core.cache import cache
from django.db.models import Count, Q, F
import json

User = get_user_model()

print("="*80)
print("RBAC SYSTEM DIAGNOSTIC REPORT")
print("="*80)
print()

# ==================== DATABASE INFO ====================
print("1. DATABASE CONNECTION")
print("-"*80)
print(f"Database: {connection.settings_dict['NAME']}")
print(f"Host: {connection.settings_dict.get('HOST', 'localhost')}")
print(f"Engine: {connection.settings_dict['ENGINE']}")
print()

# ==================== USER & PROFILE COUNT ====================
print("2. USER & PROFILE STATISTICS")
print("-"*80)
total_users = User.objects.count()
total_profiles = UserProfile.objects.count()
profiles_with_roles = UserProfile.objects.filter(userrole__isnull=False).distinct().count()
profiles_without_roles = UserProfile.objects.filter(userrole__isnull=True).count()

print(f"Total Users: {total_users}")
print(f"Total UserProfiles: {total_profiles}")
print(f"Profiles WITH roles: {profiles_with_roles}")
print(f"Profiles WITHOUT roles: {profiles_without_roles}")
print()

# ==================== ROLE STATISTICS ====================
print("3. ROLE STATISTICS")
print("-"*80)
all_roles = Role.objects.all()
print(f"Total Roles: {all_roles.count()}")
print(f"Active Roles: {Role.objects.filter(is_active=True).count()}")
print(f"System Roles: {Role.objects.filter(is_system_role=True).count()}")
print()

print("Role Details:")
for role in all_roles:
    user_count = UserRole.objects.filter(role=role).count()
    module_count = RoleModule.objects.filter(role=role).count()
    print(f"  • {role.name} ({role.code})")
    print(f"    - Level: {role.level}, Active: {role.is_active}, System: {role.is_system_role}")
    print(f"    - Users: {user_count}, Modules: {module_count}")
print()

# ==================== DEFAULT ROLE CHECK ====================
print("4. DEFAULT ROLE CONFIGURATION")
print("-"*80)
default_role_code = DEFAULT_ROLE_CONFIG.get('code', 'default')
print(f"Config Default Role Code: {default_role_code}")

try:
    default_role = Role.objects.get(code=default_role_code)
    print(f"✅ Default Role Found: {default_role.name} (ID: {default_role.id})")
    print(f"   - Level: {default_role.level}")
    print(f"   - Active: {default_role.is_active}")
    print(f"   - System Role: {default_role.is_system_role}")
    
    # Check module assignments
    default_modules = RoleModule.objects.filter(role=default_role)
    print(f"   - Assigned Modules: {default_modules.count()}")
    for rm in default_modules:
        print(f"     • {rm.module.name} ({rm.module.code})")
except Role.DoesNotExist:
    print(f"❌ Default Role NOT FOUND with code: {default_role_code}")
print()

# ==================== USERROLE RELATIONSHIP CHECK ====================
print("5. USERROLE RELATIONSHIP INTEGRITY")
print("-"*80)
total_userroles = UserRole.objects.count()
print(f"Total UserRole Relationships: {total_userroles}")

# Check for duplicates (should be prevented by unique_together constraint)
duplicates = UserRole.objects.values('user_profile', 'role').annotate(
    count=Count('id')
).filter(count__gt=1)
print(f"Duplicate Assignments: {duplicates.count()}")
if duplicates.count() > 0:
    print("❌ WARNING: Found duplicate role assignments!")
    for dup in duplicates:
        print(f"   Profile: {dup['user_profile']}, Role: {dup['role']}, Count: {dup['count']}")
else:
    print("✅ No duplicate assignments found")

# Check primary role distribution
primary_roles = UserRole.objects.filter(is_primary=True).count()
users_with_primary = UserProfile.objects.filter(userrole__is_primary=True).distinct().count()
print(f"Primary Role Assignments: {primary_roles}")
print(f"Users with Primary Role: {users_with_primary}")

# Check users with multiple primary roles (should be 0)
multi_primary = UserProfile.objects.annotate(
    primary_count=Count('userrole', filter=Q(userrole__is_primary=True))
).filter(primary_count__gt=1)
print(f"Users with Multiple Primary Roles: {multi_primary.count()}")
if multi_primary.count() > 0:
    print("❌ WARNING: Found users with multiple primary roles!")
    for profile in multi_primary:
        print(f"   • {profile.user.email} has {profile.primary_count} primary roles")
else:
    print("✅ No users with multiple primary roles")
print()

# ==================== SAMPLE USER VERIFICATION ====================
print("6. SAMPLE USER ROLE ASSIGNMENTS")
print("-"*80)
sample_profiles = UserProfile.objects.prefetch_related(
    'userrole_set__role'
).filter(is_deleted=False)[:10]

for profile in sample_profiles:
    user_roles = profile.userrole_set.all()
    print(f"User: {profile.user.email}")
    print(f"  Profile ID: {profile.id}")
    print(f"  Status: {profile.status}")
    if user_roles.exists():
        print(f"  Assigned Roles: {user_roles.count()}")
        for ur in user_roles:
            primary_marker = " [PRIMARY]" if ur.is_primary else ""
            print(f"    • {ur.role.name} ({ur.role.code}){primary_marker}")
    else:
        print(f"  ❌ NO ROLES ASSIGNED")
    print()

# ==================== ROLE ASSIGNMENT API WORKFLOW TEST ====================
print("7. ROLE ASSIGNMENT WORKFLOW SIMULATION")
print("-"*80)
print("Testing role assignment workflow...")

# Find a test user profile
test_profile = UserProfile.objects.filter(is_deleted=False).first()
if test_profile:
    print(f"Test Profile: {test_profile.user.email} (ID: {test_profile.id})")
    
    # Check current roles
    current_roles = test_profile.userrole_set.all()
    print(f"Current Roles: {current_roles.count()}")
    for ur in current_roles:
        print(f"  • {ur.role.name} (Primary: {ur.is_primary})")
    
    # Simulate the workflow that happens when assigning a role via API
    print("\nSimulating API workflow:")
    print("  1. Frontend calls: rbacService.assignRole(userId, roleId, isPrimary=true)")
    print("  2. Backend receives: POST /rbac/users/{id}/assign_role/")
    print("  3. Backend executes: UserRole.objects.get_or_create(...)")
    print("  4. Backend returns response")
    print("  5. Frontend refreshes user list")
    print("  6. UserProfileListSerializer.get_roles() should return updated roles")
    
    # Check if the serializer would return the roles correctly
    roles_data = []
    from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG
    custom_role_prefix = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')
    
    for user_role in current_roles:
        if user_role.role.is_active and not user_role.role.code.startswith(custom_role_prefix):
            roles_data.append({
                'id':         str(user_role.role.id),
                'name':       user_role.role.name,
                'code':       user_role.role.code,
                'level':      user_role.role.level,
                'is_primary': user_role.is_primary,
            })
    
    print(f"\n  Serialized Roles (as frontend would see): {len(roles_data)} roles")
    for role_data in roles_data:
        print(f"    • {role_data}")
else:
    print("❌ No test profile available")
print()

# ==================== CACHE CHECK ====================
print("8. CACHE STATUS")
print("-"*80)
try:
    # Test Redis/cache connectivity
    cache.set('rbac_diagnostic_test', 'OK', 10)
    cache_test = cache.get('rbac_diagnostic_test')
    if cache_test == 'OK':
        print("✅ Cache system is working")
        
        # Check for cached user data
        sample_profile_id = UserProfile.objects.first().id if UserProfile.objects.exists() else None
        if sample_profile_id:
            modules_cached = cache.get(f'user_modules_{sample_profile_id}')
            perms_cached = cache.get(f'user_permissions_{sample_profile_id}')
            print(f"Sample Profile Cache:")
            print(f"  - Modules cached: {'Yes' if modules_cached else 'No'}")
            print(f"  - Permissions cached: {'Yes' if perms_cached else 'No'}")
    else:
        print("⚠️  Cache test failed")
except Exception as e:
    print(f"❌ Cache system error: {e}")
print()

# ==================== FRONTEND-BACKEND ALIGNMENT CHECK ====================
print("9. FRONTEND-BACKEND ALIGNMENT")
print("-"*80)
print("Checking if frontend would receive correct role data...")

# Simulate what the /rbac/users/ endpoint would return
from apps.rbac.serializers import UserProfileListSerializer

sample_profiles = UserProfile.objects.prefetch_related(
    'userrole_set__role'
).filter(is_deleted=False)[:3]

for profile in sample_profiles:
    print(f"\nUser: {profile.user.email}")
    print(f"  Database UserRole Count: {profile.userrole_set.count()}")
    
    # Manually execute the serializer logic
    serializer = UserProfileListSerializer(profile)
    serialized_data = serializer.data
    
    print(f"  Serialized Roles Count: {len(serialized_data.get('roles', []))}")
    print(f"  Serialized Roles: {json.dumps(serialized_data.get('roles', []), indent=4)}")
    print(f"  Primary Role: {serialized_data.get('primary_role', 'None')}")
    
    # Check if they match
    db_role_count = profile.userrole_set.filter(
        role__is_active=True
    ).exclude(
        role__code__startswith='custom_'
    ).count()
    
    serialized_role_count = len(serialized_data.get('roles', []))
    
    if db_role_count == serialized_role_count:
        print(f"  ✅ Role counts match: {db_role_count} roles")
    else:
        print(f"  ❌ MISMATCH: DB has {db_role_count} roles, serializer returned {serialized_role_count}")
print()

# ==================== RECOMMENDATIONS ====================
print("="*80)
print("10. RECOMMENDATIONS")
print("="*80)

issues_found = []
recommendations = []

if profiles_without_roles > 0:
    issues_found.append(f"{profiles_without_roles} users have no roles assigned")
    recommendations.append("Run: python manage.py sync_default_role --assign-all")

if multi_primary.count() > 0:
    issues_found.append(f"{multi_primary.count()} users have multiple primary roles")
    recommendations.append("Fix primary role assignments via admin or run cleanup script")

if duplicates.count() > 0:
    issues_found.append("Duplicate role assignments found")
    recommendations.append("Remove duplicates: DELETE FROM rbac_user_roles WHERE id NOT IN (SELECT MIN(id) FROM rbac_user_roles GROUP BY user_profile_id, role_id)")

if not issues_found:
    print("✅ No critical issues found")
    print("\nSystem appears to be functioning correctly.")
    print("If role assignments are not reflecting in the UI:")
    print("  1. Check browser console for API errors")
    print("  2. Clear Redis cache: cache.clear()")
    print("  3. Hard refresh frontend (Ctrl+Shift+R)")
    print("  4. Check network tab for API responses")
else:
    print("⚠️  Issues Found:")
    for i, issue in enumerate(issues_found, 1):
        print(f"  {i}. {issue}")
    
    print("\n💡 Recommended Actions:")
    for i, rec in enumerate(recommendations, 1):
        print(f"  {i}. {rec}")

print()
print("="*80)
print("DIAGNOSTIC COMPLETE")
print("="*80)
