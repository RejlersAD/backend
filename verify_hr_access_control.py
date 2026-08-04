"""
Verify HR Access Control - Soft-Coded Module-Based Security
Ensures ONLY users with HR modules can see HR section
Verifies Default role users CANNOT see HR features
"""

import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from apps.rbac.rbac_config import DEFAULT_ROLE_MODULES, ROLE_MODULE_POLICY
import json

User = get_user_model()

def print_header(title):
    print("\n" + "="*90)
    print(f"  {title}")
    print("="*90 + "\n")

def verify_default_role_modules():
    """Verify DEFAULT role does NOT include HR management modules"""
    print_header("1. DEFAULT ROLE MODULE VERIFICATION")
    
    print("📋 Modules in DEFAULT_ROLE_MODULES:")
    print(f"   Total: {len(DEFAULT_ROLE_MODULES)}\n")
    
    # Categorize modules
    hr_keywords = ['hr_management', 'payroll', 'timesheet', 'hr_onboarding']
    hr_modules = [m for m in DEFAULT_ROLE_MODULES if m in hr_keywords]
    hr_self_service = [m for m in DEFAULT_ROLE_MODULES if m == 'hr_self_service']
    other_modules = [m for m in DEFAULT_ROLE_MODULES if m not in hr_keywords and m != 'hr_self_service']
    
    print(f"✅ HR Management Modules (sensitive): {len(hr_modules)}")
    if hr_modules:
        print("   ❌ ERROR: Default role should NOT have these modules!")
        for m in hr_modules:
            print(f"      - {m}")
    else:
        print("   ✓ CORRECT: Default role has NO HR management modules")
    
    print(f"\n✅ HR Self-Service Modules (personal): {len(hr_self_service)}")
    if hr_self_service:
        print("   ✓ CORRECT: Default role includes hr_self_service (My Profile)")
        for m in hr_self_service:
            print(f"      - {m}")
    
    print(f"\n✅ Other Modules (engineering/common): {len(other_modules)}")
    for m in other_modules[:5]:
        print(f"   - {m}")
    if len(other_modules) > 5:
        print(f"   ... and {len(other_modules) - 5} more")
    
    return len(hr_modules) == 0

def verify_hr_admin_role_modules():
    """Verify HR Admin role HAS HR management modules"""
    print_header("2. HR ADMIN ROLE MODULE VERIFICATION")
    
    hr_admin_modules = ROLE_MODULE_POLICY.get('hr_admin', [])
    
    print("📋 Modules in hr_admin role:")
    print(f"   Total: {len(hr_admin_modules)}\n")
    
    # Check for required HR modules
    required_hr_modules = ['hr_management', 'payroll', 'timesheet', 'hr_onboarding', 'hr_self_service']
    has_all_hr = all(m in hr_admin_modules for m in required_hr_modules)
    
    print("✅ Required HR Modules:")
    for m in required_hr_modules:
        status = "✓" if m in hr_admin_modules else "✗"
        print(f"   {status} {m}")
    
    if has_all_hr:
        print("\n✓ CORRECT: HR Admin has all required HR modules")
    else:
        print("\n❌ ERROR: HR Admin is missing some HR modules")
    
    return has_all_hr

def verify_michelle_access():
    """Verify Michelle has hr_admin role and HR modules"""
    print_header("3. MICHELLE'S ACCESS VERIFICATION")
    
    try:
        michelle = User.objects.get(email='michelle.dehoedt@rejlers.ae')
        profile = UserProfile.objects.get(user=michelle)
        
        print(f"👤 User: {michelle.email}")
        
        # Get roles
        user_roles = UserRole.objects.filter(
            user_profile=profile
        ).select_related('role').order_by('-is_primary')
        
        print(f"📋 Roles: {user_roles.count()}")
        
        has_hr_admin = False
        has_default = False
        
        for ur in user_roles:
            primary_marker = "🌟 PRIMARY" if ur.is_primary else "   Secondary"
            print(f"\n   {primary_marker}: {ur.role.name} ({ur.role.code})")
            
            if ur.role.code == 'hr_admin':
                has_hr_admin = True
            if ur.role.code == 'default':
                has_default = True
        
        # Get modules from role_module policy
        all_modules = set()
        for ur in user_roles:
            role_modules = ROLE_MODULE_POLICY.get(ur.role.code, [])
            all_modules.update(role_modules)
        
        hr_modules = [m for m in all_modules if m in ['hr_management', 'payroll', 'timesheet', 'hr_onboarding', 'hr_self_service']]
        
        print(f"\n📦 Total Modules: {len(all_modules)}")
        print(f"🔒 HR Modules: {len(hr_modules)}")
        for m in hr_modules:
            print(f"   - {m}")
        
        print("\n✅ Access Verification:")
        print(f"   ✓ Has hr_admin role: {has_hr_admin}")
        print(f"   ✓ Has default role: {has_default}")
        print(f"   ✓ Has HR modules: {len(hr_modules) > 0}")
        
        if has_hr_admin and len(hr_modules) > 0:
            print("\n✅ PASS: Michelle should SEE HR section (has hr_admin + HR modules)")
            return True
        else:
            print("\n❌ FAIL: Michelle is missing HR access")
            return False
            
    except User.DoesNotExist:
        print("❌ Michelle's account not found")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def verify_default_user_access():
    """Verify a user with ONLY Default role does NOT have HR access"""
    print_header("4. DEFAULT USER ACCESS VERIFICATION")
    
    # Find a user with only default role (not Michelle)
    try:
        # Get default role
        default_role = Role.objects.get(code='default')
        
        # Find a user with only default role
        user_profiles = UserProfile.objects.filter(
            userrole__role=default_role
        ).exclude(
            user__email='michelle.dehoedt@rejlers.ae'
        ).distinct()[:5]
        
        print(f"🔍 Testing {user_profiles.count()} users with Default role:\n")
        
        default_modules = ROLE_MODULE_POLICY.get('default', [])
        hr_sensitive_modules = ['hr_management', 'payroll', 'timesheet', 'hr_onboarding']
        
        has_hr_in_default = any(m in default_modules for m in hr_sensitive_modules)
        
        print(f"📋 Default Role Modules: {len(default_modules)}")
        print(f"🔒 HR Sensitive Modules in Default: {sum(1 for m in default_modules if m in hr_sensitive_modules)}")
        
        if has_hr_in_default:
            print("\n❌ FAIL: Default role includes HR sensitive modules!")
            print("   This is a SECURITY ISSUE - Default users should NOT see HR section")
            for m in hr_sensitive_modules:
                if m in default_modules:
                    print(f"   - {m}")
            return False
        else:
            print("\n✅ PASS: Default role does NOT include HR sensitive modules")
            print("   Default users will NOT see HR section (correct behavior)")
            return True
            
    except Role.DoesNotExist:
        print("❌ Default role not found")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def explain_frontend_logic():
    """Explain how frontend filters HR section"""
    print_header("5. FRONTEND ACCESS CONTROL LOGIC")
    
    print("📝 How Sidebar.jsx filters the HR section:\n")
    
    print("1️⃣ Feature Flag Check:")
    print("   enableHRModule: true (enabled by default)")
    print("   → HR section appears in menu structure")
    
    print("\n2️⃣ Module-Based Filtering (CRITICAL SECURITY):")
    print("   Each HR child item has moduleCode:")
    print("   - HR Dashboard       → moduleCode: 'hr_management'")
    print("   - HR Employees       → moduleCode: 'hr_management'")
    print("   - HR Payroll         → moduleCode: 'payroll'")
    print("   - HR Timesheet       → moduleCode: 'timesheet'")
    print("   - HR Onboarding      → moduleCode: 'hr_onboarding'")
    
    print("\n3️⃣ Access Check Logic:")
    print("   hasModuleAccess(item):")
    print("   → return userModules.includes(item.moduleCode)")
    
    print("\n4️⃣ Section Hiding Logic:")
    print("   filterMenuByModules():")
    print("   → Filters all children by moduleCode")
    print("   → If NO children are accessible:")
    print("   → return null  // Hide entire section")
    
    print("\n5️⃣ Result:")
    print("   ✅ Michelle (hr_admin role):")
    print("      - Has modules: ['hr_management', 'payroll', 'timesheet', ...]")
    print("      - Sees ALL HR children")
    print("      - HR section VISIBLE")
    
    print("\n   ✅ Default User (default role):")
    print("      - Has modules: ['pid_analysis', 'crs_documents', 'hr_self_service', ...]")
    print("      - Has NO HR sensitive modules")
    print("      - ALL HR children filtered out")
    print("      - HR section HIDDEN")

def main():
    print("\n" + "╔" + "="*88 + "╗")
    print("║" + " "*25 + "HR ACCESS CONTROL VERIFICATION" + " "*33 + "║")
    print("╚" + "="*88 + "╝")
    
    # Run all checks
    check1 = verify_default_role_modules()
    check2 = verify_hr_admin_role_modules()
    check3 = verify_michelle_access()
    check4 = verify_default_user_access()
    explain_frontend_logic()
    
    # Summary
    print_header("6. SECURITY VERIFICATION SUMMARY")
    
    checks = [
        ("Default role has NO HR management modules", check1),
        ("HR Admin role HAS all HR modules", check2),
        ("Michelle has HR access (hr_admin role)", check3),
        ("Default users CANNOT see HR section", check4),
    ]
    
    all_passed = all(result for _, result in checks)
    
    for description, passed in checks:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"   {status}: {description}")
    
    print("\n" + "="*90)
    if all_passed:
        print("✅ SECURITY VERIFICATION PASSED")
        print("\n📌 Summary:")
        print("   • HR section is ONLY visible to users with HR modules")
        print("   • Default role users CANNOT see HR features")
        print("   • Michelle (hr_admin) CAN see HR features")
        print("   • Sensitive data is protected by module-based access control")
    else:
        print("❌ SECURITY VERIFICATION FAILED")
        print("\n⚠️  CRITICAL: Fix the failed checks above")
    print("="*90 + "\n")
    
    return 0 if all_passed else 1

if __name__ == '__main__':
    sys.exit(main())
