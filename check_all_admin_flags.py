"""
COMPLETE PRODUCTION FIX - Check ALL admin flags for kiran.ingale@rejlers.ae
Run in Railway shell: python check_all_admin_flags.py

This checks EVERY way a user could be considered an admin:
1. Django user flags (is_staff, is_superuser)
2. RBAC roles (admin, ict_admin, super_admin)
3. Direct module assignments
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role

User = get_user_model()

def check_all_flags():
    email = 'kiran.ingale@rejlers.ae'
    
    print("=" * 80)
    print(f"🔍 COMPLETE ADMIN FLAG CHECK")
    print(f"   User: {email}")
    print("=" * 80)
    
    try:
        user = User.objects.get(email=email)
        profile = user.rbac_profile
        
        # Check 1: Django User Flags
        print(f"\n1️⃣  DJANGO USER FLAGS (auth_user table):")
        print(f"   is_staff: {user.is_staff}")
        print(f"   is_superuser: {user.is_superuser}")
        
        if user.is_staff:
            print(f"   ⚠️  is_staff=True → Frontend considers user as ADMIN")
            print(f"   ⚠️  Frontend gives ALL modules (bypasses backend RBAC)")
        if user.is_superuser:
            print(f"   ⚠️  is_superuser=True → Backend AND frontend bypass")
            print(f"   ⚠️  User has unrestricted access to everything")
        
        # Check 2: RBAC Roles
        print(f"\n2️⃣  RBAC ROLES (rbac_userprofile_roles):")
        roles = profile.roles.filter(is_active=True)
        
        admin_roles = []
        other_roles = []
        
        for role in roles:
            if role.code in ['super_admin', 'admin', 'ict_admin', 'hr_admin']:
                admin_roles.append(role)
                print(f"   ❌ {role.name} (code: {role.code}, level: {role.level})")
            else:
                other_roles.append(role)
                print(f"   ✅ {role.name} (code: {role.code}, level: {role.level})")
        
        if admin_roles:
            print(f"\n   ⚠️  User has {len(admin_roles)} admin-level role(s)")
            for role in admin_roles:
                if role.code == 'super_admin':
                    print(f"      • {role.code}: FULL bypass (backend + frontend)")
                elif role.code in ['admin', 'ict_admin']:
                    print(f"      • {role.code}: Was bypassing (now fixed in config)")
                elif role.code == 'hr_admin':
                    print(f"      • {role.code}: HR/Payroll/Finance access")
        
        # Check 3: Direct Module Assignments
        print(f"\n3️⃣  DIRECT MODULE ASSIGNMENTS (rbac_userprofile_modules):")
        direct_modules = profile.modules.filter(is_active=True)
        
        if direct_modules.exists():
            print(f"   ⚠️  {direct_modules.count()} module(s) directly assigned:")
            for mod in direct_modules[:10]:
                print(f"      • {mod.code}: {mod.name}")
            if direct_modules.count() > 10:
                print(f"      ... and {direct_modules.count() - 10} more")
        else:
            print(f"   ✅ No direct module assignments (good)")
        
        # Check 4: Effective Module Access
        print(f"\n4️⃣  EFFECTIVE MODULE ACCESS (computed):")
        all_modules = profile.get_all_modules()
        
        finance_mods = [m for m in all_modules if 'finance' in m.code.lower()]
        qhse_mods = [m for m in all_modules if 'qhse' in m.code.lower()]
        hr_mods = [m for m in all_modules if 'hr' in m.code.lower() or 'payroll' in m.code.lower()]
        admin_mods = [m for m in all_modules if 'admin' in m.code.lower() or 'user' in m.code.lower()]
        
        print(f"   Total accessible modules: {len(all_modules)}")
        
        has_restricted = finance_mods or qhse_mods or hr_mods or admin_mods
        
        if has_restricted:
            print(f"\n   ❌ HAS RESTRICTED MODULES:")
            if finance_mods:
                print(f"      💰 Finance: {len(finance_mods)} modules")
                for m in finance_mods[:3]:
                    print(f"         - {m.code}")
            if qhse_mods:
                print(f"      🛡️  QHSE: {len(qhse_mods)} modules")
                for m in qhse_mods[:3]:
                    print(f"         - {m.code}")
            if hr_mods:
                print(f"      👥 HR: {len(hr_mods)} modules")
                for m in hr_mods[:3]:
                    print(f"         - {m.code}")
            if admin_mods:
                print(f"      👨‍💼 Admin: {len(admin_mods)} modules")
                for m in admin_mods[:3]:
                    print(f"         - {m.code}")
        else:
            print(f"   ✅ NO restricted modules")
        
        # ROOT CAUSE ANALYSIS
        print(f"\n" + "=" * 80)
        print(f"🎯 ROOT CAUSE ANALYSIS:")
        print("=" * 80)
        
        causes = []
        
        if user.is_staff or user.is_superuser:
            causes.append({
                'level': 'CRITICAL',
                'cause': 'Django user flags set',
                'detail': f"is_staff={user.is_staff}, is_superuser={user.is_superuser}",
                'impact': 'Frontend bypasses ALL RBAC checks',
                'fix': 'Set both flags to False'
            })
        
        if admin_roles:
            if any(r.code == 'super_admin' for r in admin_roles):
                causes.append({
                    'level': 'EXPECTED',
                    'cause': 'User has super_admin role',
                    'detail': 'This is intentional for system administrators',
                    'impact': 'Full system access (by design)',
                    'fix': 'None needed (unless role incorrectly assigned)'
                })
            else:
                causes.append({
                    'level': 'HIGH',
                    'cause': f"User has admin-level roles: {', '.join(r.code for r in admin_roles)}",
                    'detail': 'Regular admin roles should follow ROLE_MODULE_POLICY',
                    'impact': 'Was bypassing module checks (now fixed in config)',
                    'fix': 'Remove admin roles if user should only have default access'
                })
        
        if direct_modules.exists():
            causes.append({
                'level': 'MEDIUM',
                'cause': f'{direct_modules.count()} modules directly assigned',
                'detail': 'Direct assignments bypass role-based access',
                'impact': 'User gets these modules regardless of roles',
                'fix': 'Clear direct assignments, rely on ROLE_MODULE_POLICY'
            })
        
        if not causes:
            print(f"\n✅ NO ISSUES FOUND")
            print(f"   User configuration looks correct")
        else:
            for i, cause in enumerate(causes, 1):
                print(f"\n{i}. [{cause['level']}] {cause['cause']}")
                print(f"   Detail: {cause['detail']}")
                print(f"   Impact: {cause['impact']}")
                print(f"   Fix: {cause['fix']}")
        
        # RECOMMENDED FIX
        print(f"\n" + "=" * 80)
        print(f"✅ RECOMMENDED FIX:")
        print("=" * 80)
        
        if user.is_staff or user.is_superuser:
            print(f"\n🔧 CRITICAL: Fix Django user flags")
            print(f"   Run: python fix_kiran_django_flags.py")
        
        if admin_roles and not any(r.code == 'super_admin' for r in admin_roles):
            print(f"\n🔧 Remove admin roles")
            print(f"   Run: python fix_kiran_production.py")
        
        if direct_modules.exists():
            print(f"\n🔧 Clear direct module assignments")
            print(f"   Included in: python fix_kiran_production.py")
        
        print(f"\n💡 After running fixes:")
        print(f"   1. User must logout from https://www.radai.ae")
        print(f"   2. Login again (refresh JWT token)")
        print(f"   3. Hard refresh browser (Ctrl+F5)")
        print()
        
    except User.DoesNotExist:
        print(f"❌ User '{email}' not found")
    except UserProfile.DoesNotExist:
        print(f"❌ User profile for '{email}' not found")
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    check_all_flags()
