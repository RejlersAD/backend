"""
Verify Michelle's API Response - What does /api/v1/rbac/users/me/ return?
"""

import os
import django
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, UserRole
from apps.rbac.serializers import UserProfileSerializer

User = get_user_model()

def print_header(text):
    print("\n" + "="*80)
    print(f"  {text}")
    print("="*80)

def verify_michelle():
    print_header("MICHELLE API RESPONSE VERIFICATION")
    
    try:
        user = User.objects.get(email='michelle.dehoedt@rejlers.ae')
        print(f"✅ User: {user.email} (ID: {user.id})")
        
        profile = UserProfile.objects.select_related('user', 'organization').prefetch_related(
            'roles', 'roles__permissions', 'userrole_set__role'
        ).get(user=user)
        
        print(f"✅ Profile: {profile.id}")
        
        # Check database records
        print_header("DATABASE STATE")
        user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
        print(f"\nUserRole records in database: {user_roles.count()}")
        for ur in user_roles:
            primary = "⭐ PRIMARY" if ur.is_primary else "          "
            print(f"  {primary} | {ur.role.name} (code: {ur.role.code}, active: {ur.role.is_active})")
        
        # Check what serializer returns
        print_header("API SERIALIZER OUTPUT")
        serializer = UserProfileSerializer(profile)
        data = serializer.data
        
        print(f"\nRoles from serializer (what API returns): {len(data.get('roles', []))}")
        for role in data.get('roles', []):
            primary = "⭐ PRIMARY" if role.get('is_primary') else "          "
            print(f"  {primary} | {role['name']} (code: {role['code']})")
        
        # Check permissions
        print(f"\nPermissions: {len(data.get('permissions', []))}")
        print(f"Modules: {len(data.get('modules', []))}")
        
        if len(data.get('modules', [])) > 0:
            print(f"\nFirst 5 modules:")
            for mod in data.get('modules', [])[:5]:
                print(f"  - {mod.get('name')} (code: {mod.get('code')})")
        
        # Diagnosis
        print_header("DIAGNOSIS")
        db_count = user_roles.count()
        api_count = len(data.get('roles', []))
        
        if db_count == api_count:
            print(f"✅ MATCH: Database has {db_count} roles, API returns {api_count} roles")
            if api_count == 2:
                print("✅ Michelle has both Default and HR Admin roles visible in API")
                print("\n📌 If user still can't see HR features:")
                print("   1. User needs to LOGOUT completely")
                print("   2. CLEAR browser cache (Ctrl+Shift+Del)")
                print("   3. Close all browser tabs")
                print("   4. LOGIN again")
        else:
            print(f"❌ MISMATCH: Database has {db_count} roles but API returns {api_count} roles")
            print(f"\n🔍 Roles in database but NOT in API:")
            api_codes = [r['code'] for r in data.get('roles', [])]
            for ur in user_roles:
                if ur.role.code not in api_codes:
                    print(f"  ❌ {ur.role.name} (code: {ur.role.code})")
                    print(f"     - Active: {ur.role.is_active}")
                    print(f"     - Starts with 'custom_': {ur.role.code.startswith('custom_')}")
        
        print("\n" + "="*80 + "\n")
        
    except User.DoesNotExist:
        print("❌ User michelle.dehoedt@rejlers.ae not found")
    except UserProfile.DoesNotExist:
        print("❌ UserProfile not found")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    verify_michelle()
