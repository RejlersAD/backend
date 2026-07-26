"""
Test API Response for Michelle - Full Frontend/Backend Verification
Simulates what the frontend receives from the API
"""

import os
import sys
import django
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile
from apps.rbac.serializers import UserProfileSerializer
from django.db.models import Prefetch
from apps.rbac.models import UserRole

User = get_user_model()

def test_api_for_michelle():
    print("\n" + "="*80)
    print("  FULL API VERIFICATION FOR MICHELLE")
    print("  (Simulating Frontend API Call)")
    print("="*80 + "\n")
    
    # Get Michelle exactly as the API does
    michelle = User.objects.get(email='michelle.dehoedt@rejlers.ae')
    
    # Prefetch exactly as the UserProfileViewSet does
    profile = UserProfile.objects.prefetch_related(
        Prefetch('userrole_set', queryset=UserRole.objects.select_related('role'))
    ).get(user=michelle)
    
    # Serialize exactly as the API does
    serializer = UserProfileSerializer(profile)
    api_data = serializer.data
    
    # Format as JSON (what frontend receives)
    json_response = json.dumps(api_data, indent=2, default=str)
    
    print("📡 API Endpoint: GET /api/v1/rbac/users/me/")
    print(f"👤 User: {api_data['user']['email']}")
    print("\n" + "-"*80)
    print("🔍 KEY FIELDS FOR FRONTEND:")
    print("-"*80)
    
    # Primary Role
    print(f"\n1️⃣ PRIMARY ROLE:")
    if api_data.get('primary_role'):
        pr = api_data['primary_role']
        print(f"   ✅ Name: {pr['name']}")
        print(f"   ✅ Code: {pr.get('code', 'N/A')}")
        print(f"   ✅ ID: {pr['id']}")
    else:
        print("   ❌ None (PROBLEM!)")
    
    # All Roles
    print(f"\n2️⃣ ALL ROLES ({len(api_data.get('roles', []))}):")
    for idx, role in enumerate(api_data.get('roles', []), 1):
        print(f"\n   Role #{idx}:")
        print(f"   • Name: {role['name']}")
        print(f"   • Code: {role['code']}")
        print(f"   • Level: {role.get('level', 'N/A')}")
        print(f"   • Primary: {role.get('is_primary', 'MISSING!')}")  # Critical field
        print(f"   • ID: {role['id']}")
    
    # Modules
    modules = api_data.get('modules', [])
    print(f"\n3️⃣ MODULES ACCESS: {len(modules)} modules")
    
    # Group by section
    hr_modules = [m for m in modules if 'hr' in m.get('code', '').lower() or 'payroll' in m.get('code', '').lower() or 'timesheet' in m.get('code', '').lower()]
    default_modules = [m for m in modules if m not in hr_modules]
    
    print(f"   • HR Modules: {len(hr_modules)}")
    print(f"   • Other Modules: {len(default_modules)}")
    
    print("\n" + "-"*80)
    print("🧪 FRONTEND COMPATIBILITY CHECK:")
    print("-"*80)
    
    # Check 1: is_primary field exists
    has_is_primary = all('is_primary' in role for role in api_data.get('roles', []))
    print(f"\n✓ Check 1: 'is_primary' field in all roles: {'✅ YES' if has_is_primary else '❌ NO (CRITICAL!)'}")
    
    # Check 2: Primary role is set
    has_primary_role = api_data.get('primary_role') is not None
    print(f"✓ Check 2: 'primary_role' field exists: {'✅ YES' if has_primary_role else '❌ NO (WARNING)'}")
    
    # Check 3: Multiple roles
    has_multiple_roles = len(api_data.get('roles', [])) >= 2
    print(f"✓ Check 3: Multiple roles (Expected: 2+): {'✅ YES (' + str(len(api_data.get('roles', []))) + ' roles)' if has_multiple_roles else '❌ NO (Only ' + str(len(api_data.get('roles', []))) + ')'}")
    
    # Check 4: Both roles present
    role_codes = [r['code'] for r in api_data.get('roles', [])]
    has_default = 'default' in role_codes
    has_hr_admin = 'hr_admin' in role_codes
    print(f"✓ Check 4: Has 'default' role: {'✅ YES' if has_default else '❌ NO'}")
    print(f"✓ Check 5: Has 'hr_admin' role: {'✅ YES' if has_hr_admin else '❌ NO'}")
    
    print("\n" + "="*80)
    
    if has_is_primary and has_primary_role and has_multiple_roles and has_default and has_hr_admin:
        print("✅ SUCCESS: API response is CORRECT and FRONTEND-COMPATIBLE")
        print("\n📝 If frontend still has issues:")
        print("   1. Clear browser cache (Ctrl+Shift+Delete)")
        print("   2. Hard refresh (Ctrl+F5)")
        print("   3. Logout and login again")
        print("   4. Check browser console for errors (F12)")
    else:
        print("❌ PROBLEM: API response has issues")
        print("\n🔧 Issues found:")
        if not has_is_primary:
            print("   • Missing 'is_primary' field in roles")
        if not has_primary_role:
            print("   • Missing 'primary_role' field")
        if not has_multiple_roles:
            print("   • User only has 1 role (should have 2)")
        if not has_default:
            print("   • Missing 'default' role")
        if not has_hr_admin:
            print("   • Missing 'hr_admin' role")
    
    print("="*80 + "\n")
    
    # Show sample JSON (first 50 lines)
    print("📄 SAMPLE JSON RESPONSE (First 1000 chars):")
    print("-"*80)
    print(json_response[:1000] + "...")
    print("-"*80 + "\n")

if __name__ == '__main__':
    test_api_for_michelle()
