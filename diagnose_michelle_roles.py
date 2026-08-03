"""
Diagnose Michelle's Role Assignment Issue
- Check if user exists
- Check assigned roles and their active status
- Check UserRole records
- Verify role filtering logic
"""

import os
import django
import sys

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG

User = get_user_model()

def print_header(text):
    print("\n" + "="*80)
    print(f"  {text}")
    print("="*80)

def check_user():
    print_header("1. USER VERIFICATION")
    
    email = 'michelle.dehoedt@rejlers.ae'
    
    try:
        user = User.objects.get(email=email)
        print(f"✅ User found: {user.email} (ID: {user.id})")
        print(f"   - Username: {user.username}")
        print(f"   - Full name: {user.get_full_name()}")
        print(f"   - is_staff: {user.is_staff}")
        print(f"   - is_superuser: {user.is_superuser}")
        print(f"   - is_active: {user.is_active}")
        return user
    except User.DoesNotExist:
        print(f"❌ User NOT FOUND: {email}")
        return None

def check_profile(user):
    print_header("2. USER PROFILE")
    
    profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
    
    if not profile:
        print(f"❌ No UserProfile found for {user.email}")
        return None
    
    print(f"✅ Profile found: {profile.id}")
    print(f"   - Employee ID: {profile.employee_id}")
    print(f"   - Department: {profile.department}")
    print(f"   - Job Title: {profile.job_title}")
    print(f"   - Organization: {profile.organization.name if profile.organization else 'N/A'}")
    print(f"   - is_deleted: {profile.is_deleted}")
    
    return profile

def check_all_roles():
    print_header("3. ALL AVAILABLE ROLES")
    
    roles = Role.objects.all().order_by('name')
    
    print(f"\n📋 Total Roles: {roles.count()}\n")
    
    for role in roles:
        status = "✅ ACTIVE" if role.is_active else "❌ INACTIVE"
        print(f"{status} | {role.name:30} | Code: {role.code:20} | Level: {role.level}")

def check_user_roles(profile):
    print_header("4. MICHELLE'S ROLE ASSIGNMENTS")
    
    # Get all UserRole records (including inactive roles)
    user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
    
    print(f"\n📋 Total UserRole records: {user_roles.count()}\n")
    
    if user_roles.count() == 0:
        print("❌ No roles assigned to this user!")
        return
    
    for ur in user_roles:
        role_status = "✅ ACTIVE" if ur.role.is_active else "❌ INACTIVE"
        primary = "⭐ PRIMARY" if ur.is_primary else "   SECONDARY"
        
        print(f"{role_status} | {primary} | {ur.role.name:30} | Code: {ur.role.code}")
        print(f"   Role ID: {ur.role.id}")
        print(f"   UserRole created: {ur.created_at}")
        print()

def check_serializer_logic(profile):
    print_header("5. SERIALIZER ROLE FILTERING LOGIC")
    
    custom_role_prefix = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')
    print(f"Custom role prefix filter: '{custom_role_prefix}'")
    
    print("\nSimulating get_roles() method logic:\n")
    
    result = []
    all_user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
    
    for user_role in all_user_roles:
        print(f"Checking: {user_role.role.name} (code: {user_role.role.code})")
        
        # Check 1: is_active
        if not user_role.role.is_active:
            print(f"   ❌ FILTERED OUT: Role is inactive (is_active=False)")
            continue
        
        # Check 2: custom_* prefix
        if user_role.role.code.startswith(custom_role_prefix):
            print(f"   ❌ FILTERED OUT: Role starts with '{custom_role_prefix}'")
            continue
        
        print(f"   ✅ INCLUDED in API response")
        result.append({
            'id': str(user_role.role.id),
            'name': user_role.role.name,
            'code': user_role.role.code,
            'level': user_role.role.level,
            'is_primary': user_role.is_primary,
        })
    
    print(f"\n📤 Roles that WILL be returned to frontend: {len(result)}")
    for r in result:
        print(f"   - {r['name']} (code: {r['code']})")
    
    return result

def check_api_response(profile):
    print_header("6. SIMULATED API RESPONSE")
    
    from apps.rbac.serializers import UserProfileSerializer
    
    serializer = UserProfileSerializer(profile)
    data = serializer.data
    
    print(f"\nRoles in serialized data: {len(data.get('roles', []))}")
    
    for role in data.get('roles', []):
        print(f"   - {role.get('name')} (code: {role.get('code')})")
    
    print(f"\nPermissions count: {len(data.get('permissions', []))}")
    print(f"Modules count: {len(data.get('modules', []))}")

def recommend_fixes(profile):
    print_header("7. RECOMMENDED FIXES")
    
    user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
    inactive_roles = [ur for ur in user_roles if not ur.role.is_active]
    
    if inactive_roles:
        print("\n🔧 ISSUE FOUND: User has inactive roles assigned\n")
        print("The following roles are assigned but INACTIVE:")
        for ur in inactive_roles:
            print(f"   - {ur.role.name} (code: {ur.role.code})")
        
        print("\n💡 FIX: Activate these roles in the Role table")
        print("   Run the following SQL in production:")
        print()
        for ur in inactive_roles:
            print(f"   UPDATE rbac_role SET is_active = true WHERE id = '{ur.role.id}';")
        print()
        print("   OR use Django shell:")
        for ur in inactive_roles:
            print(f"   Role.objects.filter(id='{ur.role.id}').update(is_active=True)")
    else:
        print("\n✅ All assigned roles are active")
        print("\n🔍 Check if roles are being filtered by custom_* prefix")

def main():
    print("\n" + "╔" + "═"*78 + "╗")
    print("║" + " "*20 + "MICHELLE ROLE ASSIGNMENT DIAGNOSIS" + " "*23 + "║")
    print("╚" + "═"*78 + "╝")
    
    user = check_user()
    if not user:
        return
    
    profile = check_profile(user)
    if not profile:
        return
    
    check_all_roles()
    check_user_roles(profile)
    serialized_roles = check_serializer_logic(profile)
    check_api_response(profile)
    recommend_fixes(profile)
    
    print("\n" + "="*80)
    print("  DIAGNOSIS COMPLETE")
    print("="*80 + "\n")

if __name__ == '__main__':
    main()
