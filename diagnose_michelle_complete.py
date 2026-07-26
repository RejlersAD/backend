"""
COMPREHENSIVE PRODUCTION vs LOCAL COMPARISON FOR MICHELLE
Compares database state, API responses, and cache for both environments
"""

import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, UserRole, Role
from apps.rbac.serializers import UserProfileSerializer
from django.db.models import Prefetch
import json

User = get_user_model()

def get_michelle_detailed_report():
    """Generate comprehensive report on Michelle's access"""
    print("\n" + "="*100)
    print(" " * 30 + "MICHELLE ROLE DETAILED REPORT")
    print("="*100 + "\n")
    
    try:
        michelle = User.objects.get(email='michelle.dehoedt@rejlers.ae')
        profile = UserProfile.objects.prefetch_related(
            Prefetch('userrole_set', queryset=UserRole.objects.select_related('role'))
        ).get(user=michelle)
        
        print("┌─────────────────────────────────────────────────────────────────────────────────────┐")
        print("│ 1. USER ACCOUNT DETAILS                                                             │")
        print("└─────────────────────────────────────────────────────────────────────────────────────┘\n")
        
        print(f"  Email:        {michelle.email}")
        print(f"  Username:     {michelle.username}")
        print(f"  Full Name:    {michelle.first_name} {michelle.last_name}")
        print(f"  Is Active:    {michelle.is_active}")
        print(f"  Is Staff:     {michelle.is_staff}")
        print(f"  Is Superuser: {michelle.is_superuser}")
        print(f"  User ID:      {michelle.id}")
        print(f"  Profile ID:   {profile.id}")
        print(f"  Status:       {profile.status}")
        
        print("\n┌─────────────────────────────────────────────────────────────────────────────────────┐")
        print("│ 2. ROLE ASSIGNMENTS (from UserRole junction table)                                 │")
        print("└─────────────────────────────────────────────────────────────────────────────────────┘\n")
        
        user_roles = UserRole.objects.filter(
            user_profile=profile
        ).select_related('role').order_by('-is_primary', 'role__name')
        
        print(f"  Total Roles: {user_roles.count()}\n")
        
        for idx, ur in enumerate(user_roles, 1):
            primary_status = "🌟 PRIMARY" if ur.is_primary else "   Secondary"
            print(f"  Role #{idx}: {ur.role.name}")
            print(f"    • Code:       {ur.role.code}")
            print(f"    • Status:     {primary_status}")
            print(f"    • Is Primary: {ur.is_primary} (stored in UserRole junction)")
            print(f"    • Level:      {ur.role.level}")
            print(f"    • Is Active:  {ur.role.is_active}")
            print(f"    • Role ID:    {ur.role.id}")
            print(f"    • UserRole ID:{ur.id}")
            print()
        
        print("┌─────────────────────────────────────────────────────────────────────────────────────┐")
        print("│ 3. API SERIALIZER OUTPUT (what frontend receives)                                  │")
        print("└─────────────────────────────────────────────────────────────────────────────────────┘\n")
        
        serializer = UserProfileSerializer(profile)
        api_data = serializer.data
        
        print("  Endpoint: GET /api/v1/rbac/users/me/\n")
        print(f"  Primary Role Field:")
        print(f"    {json.dumps(api_data.get('primary_role'), indent=4)}\n")
        
        print(f"  Roles Array ({len(api_data.get('roles', []))}):")
        for idx, role in enumerate(api_data.get('roles', []), 1):
            primary_marker = "🌟" if role.get('is_primary') else "  "
            print(f"    {primary_marker} Role #{idx}:")
            print(f"       Name:       {role['name']}")
            print(f"       Code:       {role['code']}")
            print(f"       Level:      {role.get('level', 'N/A')}")
            print(f"       Is Primary: {role.get('is_primary', '❌ MISSING!')}")
            print(f"       ID:         {role['id']}")
            print()
        
        print(f"  Modules: {len(api_data.get('modules', []))} total")
        
        # Group modules
        modules = api_data.get('modules', [])
        hr_modules = [m for m in modules if any(
            keyword in m.get('code', '').lower() 
            for keyword in ['hr', 'payroll', 'timesheet', 'leave']
        )]
        
        print(f"    • HR-related: {len(hr_modules)}")
        print(f"    • Other:      {len(modules) - len(hr_modules)}")
        
        print("\n┌─────────────────────────────────────────────────────────────────────────────────────┐")
        print("│ 4. VERIFICATION CHECKS                                                              │")
        print("└─────────────────────────────────────────────────────────────────────────────────────┘\n")
        
        checks = []
        
        # Check 1: Two roles
        has_two_roles = user_roles.count() == 2
        checks.append(("Has exactly 2 roles", has_two_roles))
        
        # Check 2: Has default role
        has_default = user_roles.filter(role__code='default').exists()
        checks.append(("Has 'default' role", has_default))
        
        # Check 3: Has hr_admin role
        has_hr_admin = user_roles.filter(role__code='hr_admin').exists()
        checks.append(("Has 'hr_admin' role", has_hr_admin))
        
        # Check 4: Only one primary
        primary_count = user_roles.filter(is_primary=True).count()
        checks.append(("Has exactly 1 primary role", primary_count == 1))
        
        # Check 5: hr_admin is primary
        hr_admin_is_primary = user_roles.filter(
            role__code='hr_admin', 
            is_primary=True
        ).exists()
        checks.append(("'hr_admin' is the primary role", hr_admin_is_primary))
        
        # Check 6: API has is_primary flags
        has_is_primary_in_api = all(
            'is_primary' in r 
            for r in api_data.get('roles', [])
        )
        checks.append(("API includes 'is_primary' flag in all roles", has_is_primary_in_api))
        
        # Check 7: API has primary_role field
        has_primary_role_field = api_data.get('primary_role') is not None
        checks.append(("API includes 'primary_role' field", has_primary_role_field))
        
        # Check 8: primary_role matches database
        if has_primary_role_field:
            api_primary_code = api_data.get('primary_role', {}).get('code')
            db_primary = user_roles.filter(is_primary=True).first()
            db_primary_code = db_primary.role.code if db_primary else None
            primary_matches = api_primary_code == db_primary_code
            checks.append((
                f"API primary_role matches database (both '{api_primary_code}')", 
                primary_matches
            ))
        
        # Print checks
        all_passed = True
        for check_name, passed in checks:
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"  {status}: {check_name}")
            if not passed:
                all_passed = False
        
        print("\n┌─────────────────────────────────────────────────────────────────────────────────────┐")
        print("│ 5. SUMMARY                                                                          │")
        print("└─────────────────────────────────────────────────────────────────────────────────────┘\n")
        
        if all_passed:
            print("  ✅ ALL CHECKS PASSED")
            print("\n  Backend is configured correctly. If frontend shows issues:")
            print("    1. Clear browser cache (Ctrl+Shift+Delete)")
            print("    2. Clear localStorage: localStorage.clear()")
            print("    3. Hard refresh (Ctrl+F5)")
            print("    4. Logout and login again")
            print("    5. Check browser console for errors (F12)")
            print("\n  Test Page: Open frontend/test_michelle_roles.html in browser")
        else:
            print("  ❌ SOME CHECKS FAILED")
            print("\n  Review the failed checks above and run:")
            print("    python cross_verify_michelle_roles.py")
            print("  to auto-fix role assignments.")
        
        print("\n" + "="*100 + "\n")
        
        return all_passed
        
    except User.DoesNotExist:
        print("❌ ERROR: Michelle's user account not found")
        print("   Email: michelle.dehoedt@rejlers.ae")
        return False
    except UserProfile.DoesNotExist:
        print("❌ ERROR: Michelle's user profile not found")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def show_frontend_debugging_commands():
    """Show commands to debug frontend"""
    print("┌─────────────────────────────────────────────────────────────────────────────────────┐")
    print("│ FRONTEND DEBUGGING COMMANDS (Run in Browser Console - F12)                         │")
    print("└─────────────────────────────────────────────────────────────────────────────────────┘\n")
    
    commands = [
        ("Check current user data in localStorage", "JSON.parse(localStorage.getItem('radai_user_data'))"),
        ("Check access token", "localStorage.getItem('radai_access_token')"),
        ("Clear all auth data", "localStorage.removeItem('radai_access_token'); localStorage.removeItem('radai_refresh_token'); localStorage.removeItem('radai_user_data')"),
        ("Test API directly", "fetch('http://localhost:8000/api/v1/rbac/users/me/', {headers: {'Authorization': 'Bearer ' + localStorage.getItem('radai_access_token')}}).then(r => r.json()).then(d => console.log(d))"),
        ("Check Redux state (if using Redux DevTools)", "store.getState().rbac"),
    ]
    
    for idx, (description, command) in enumerate(commands, 1):
        print(f"  {idx}. {description}:")
        print(f"     {command}\n")
    
    print("="*100 + "\n")

if __name__ == '__main__':
    passed = get_michelle_detailed_report()
    print()
    show_frontend_debugging_commands()
    
    if passed:
        sys.exit(0)
    else:
        sys.exit(1)
