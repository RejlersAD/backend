"""
Debug Role Filtering Issue for Michelle
Find out why Default role is filtered from API response
"""

import os
import django
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG
from apps.rbac.serializers import UserProfileSerializer

User = get_user_model()

def print_header(text):
    print("\n" + "="*80)
    print(f"  {text}")
    print("="*80)

def check_default_role():
    print_header("1. DEFAULT ROLE INVESTIGATION")
    
    # Find all roles with 'default' in name or code
    default_roles = Role.objects.filter(code__icontains='default')
    
    print(f"\nRoles with 'default' in code: {default_roles.count()}")
    for role in default_roles:
        print(f"  - ID: {role.id}")
        print(f"    Name: {role.name}")
        print(f"    Code: {role.code}")
        print(f"    Level: {role.level}")
        print(f"    Active: {role.is_active}")
        print(f"    Starts with 'custom_': {role.code.startswith('custom_')}")
        print()

def check_michelle_roles():
    print_header("2. MICHELLE'S ROLE ASSIGNMENTS")
    
    try:
        user = User.objects.get(email='michelle.dehoedt@rejlers.ae')
        profile = UserProfile.objects.get(user=user)
        
        print(f"\n✅ Michelle found: {user.email} (ID: {user.id})")
        print(f"✅ Profile: {profile.id}")
        
        # Get ALL UserRole records
        user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
        
        print(f"\n📋 UserRole Records in Database: {user_roles.count()}")
        for ur in user_roles:
            print(f"\n  UserRole ID: {ur.id}")
            print(f"  Role: {ur.role.name}")
            print(f"  Role Code: {ur.role.code}")
            print(f"  Role Active: {ur.role.is_active}")
            print(f"  Is Primary: {ur.is_primary}")
            print(f"  Starts with 'custom_': {ur.role.code.startswith('custom_')}")
            
            # Check filtering condition
            custom_prefix = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')
            passes_filter = ur.role.is_active and not ur.role.code.startswith(custom_prefix)
            print(f"  PASSES FILTER: {passes_filter}")
            if not passes_filter:
                print(f"    ❌ FILTERED OUT because:")
                if not ur.role.is_active:
                    print(f"       - Role is inactive")
                if ur.role.code.startswith(custom_prefix):
                    print(f"       - Code starts with '{custom_prefix}'")
        
        return profile, user_roles
        
    except User.DoesNotExist:
        print("❌ Michelle not found")
        return None, None
    except UserProfile.DoesNotExist:
        print("❌ Michelle has no profile")
        return None, None

def simulate_serializer_logic(profile):
    print_header("3. SIMULATING SERIALIZER get_roles() METHOD")
    
    if not profile:
        print("❌ No profile to test")
        return
    
    # This is the EXACT logic from UserProfileSerializer.get_roles()
    custom_role_prefix = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')
    
    print(f"\nCustom Role Prefix: '{custom_role_prefix}'")
    print(f"\nIterating through obj.userrole_set.all():")
    
    result = []
    for user_role in profile.userrole_set.all():
        print(f"\n  Checking UserRole: {user_role.id}")
        print(f"    Role: {user_role.role.name} (code: {user_role.role.code})")
        print(f"    is_active: {user_role.role.is_active}")
        print(f"    starts with '{custom_role_prefix}': {user_role.role.code.startswith(custom_role_prefix)}")
        
        if user_role.role.is_active and not user_role.role.code.startswith(custom_role_prefix):
            print(f"    ✅ INCLUDED in result")
            result.append({
                'id': str(user_role.role.id),
                'name': user_role.role.name,
                'code': user_role.role.code,
                'level': user_role.role.level,
                'is_primary': user_role.is_primary,
            })
        else:
            print(f"    ❌ EXCLUDED from result")
    
    print(f"\n📋 Final Result ({len(result)} roles):")
    for r in result:
        primary_flag = "⭐ PRIMARY" if r['is_primary'] else "          "
        print(f"  {primary_flag} | {r['name']} (code: {r['code']})")
    
    return result

def test_actual_serializer(profile):
    print_header("4. ACTUAL SERIALIZER OUTPUT")
    
    if not profile:
        print("❌ No profile to test")
        return
    
    # Use the actual serializer
    serializer = UserProfileSerializer(profile)
    data = serializer.data
    
    print(f"\nRoles from serializer.data['roles']:")
    for role in data.get('roles', []):
        print(f"  - {role['name']} (code: {role['code']})")
    
    print(f"\nTotal roles in API response: {len(data.get('roles', []))}")

def recommend_fix():
    print_header("5. RECOMMENDED FIX")
    
    print("\n📌 If Default role is being filtered:")
    print("   Possible causes:")
    print("   1. Role code is actually 'custom_default' not 'default'")
    print("   2. Role is inactive (is_active=False)")
    print("   3. UserRole record doesn't exist")
    print("   4. Prefetch not working properly")
    
    print("\n📌 To fix in production:")
    print("   1. Run: python fix_michelle_roles.py")
    print("   2. This will add Default role if missing")
    print("   3. Verify role code is 'default' not 'custom_default'")
    print("   4. Ensure role is active")

def main():
    print("\n╔" + "═"*78 + "╗")
    print("║" + " "*20 + "ROLE FILTERING DEBUG - MICHELLE" + " "*22 + "║")
    print("╚" + "═"*78 + "╝")
    
    check_default_role()
    profile, user_roles = check_michelle_roles()
    
    if profile:
        simulated_result = simulate_serializer_logic(profile)
        test_actual_serializer(profile)
    
    recommend_fix()
    
    print("\n" + "="*80)
    print("  DEBUG COMPLETE")
    print("="*80 + "\n")

if __name__ == '__main__':
    main()
