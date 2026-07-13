"""
Quick diagnostic script to check kiran.ingale@rejlers.ae access
Run in Railway shell: python check_kiran_access.py
"""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module

User = get_user_model()

def check_user_access():
    email = 'kiran.ingale@rejlers.ae'
    
    print("=" * 80)
    print(f"🔍 CHECKING ACCESS FOR: {email}")
    print("=" * 80)
    
    try:
        user = User.objects.get(email=email)
        profile = user.rbac_profile
        
        print(f"\n👤 USER INFO:")
        print(f"   Name: {user.first_name} {user.last_name}")
        print(f"   Email: {user.email}")
        print(f"   Status: {profile.status}")
        print(f"   Organization: {profile.organization.name}")
        
        # Check roles
        print(f"\n🎭 ASSIGNED ROLES:")
        user_roles = profile.roles.filter(is_active=True)
        if user_roles.exists():
            for role in user_roles:
                print(f"   • {role.name} (code: {role.code}, level: {role.level})")
                
                # Check if this role gives admin access
                if role.code in ['super_admin', 'admin', 'ict_admin']:
                    print(f"     ⚠️  ADMIN ROLE - BYPASSES MODULE CHECKS!")
                elif role.code in ['hr_admin']:
                    print(f"     ⚠️  HR ADMIN ROLE - ACCESS TO HR/PAYROLL!")
        else:
            print("   ❌ No roles assigned")
        
        # Check modules
        print(f"\n📦 ACCESSIBLE MODULES:")
        modules = profile.get_all_modules()
        
        # Group by category
        finance_modules = [m for m in modules if 'finance' in m.code.lower()]
        qhse_modules = [m for m in modules if 'qhse' in m.code.lower()]
        hr_modules = [m for m in modules if 'hr' in m.code.lower() or 'payroll' in m.code.lower()]
        admin_modules = [m for m in modules if 'admin' in m.code.lower() or 'user' in m.code.lower()]
        
        print(f"\n   Total modules: {len(modules)}")
        
        if finance_modules:
            print(f"\n   💰 FINANCE MODULES ({len(finance_modules)}):")
            for m in finance_modules:
                print(f"      • {m.code}: {m.name}")
        
        if qhse_modules:
            print(f"\n   🛡️  QHSE MODULES ({len(qhse_modules)}):")
            for m in qhse_modules:
                print(f"      • {m.code}: {m.name}")
        
        if hr_modules:
            print(f"\n   👥 HR MODULES ({len(hr_modules)}):")
            for m in hr_modules:
                print(f"      • {m.code}: {m.name}")
        
        if admin_modules:
            print(f"\n   👨‍💼 ADMIN MODULES ({len(admin_modules)}):")
            for m in admin_modules:
                print(f"      • {m.code}: {m.name}")
        
        # Check for custom roles
        custom_roles = user_roles.filter(code__startswith='custom_')
        if custom_roles.exists():
            print(f"\n⚠️  CUSTOM ROLES FOUND ({custom_roles.count()}):")
            for role in custom_roles:
                print(f"   • {role.code}: {role.name}")
        
        # Diagnosis
        print(f"\n" + "=" * 80)
        print(f"🔍 DIAGNOSIS:")
        print("=" * 80)
        
        has_admin_role = user_roles.filter(code__in=['super_admin', 'admin', 'ict_admin']).exists()
        has_hr_admin = user_roles.filter(code='hr_admin').exists()
        has_custom_role = custom_roles.exists()
        
        if has_admin_role:
            print(f"❌ PROBLEM: User has ADMIN role - bypasses all module checks")
            print(f"   Solution: Remove admin role, assign appropriate role (default, engineer, etc.)")
        elif has_hr_admin:
            print(f"❌ PROBLEM: User has HR_ADMIN role - access to HR/Payroll/Finance")
            print(f"   Solution: Remove hr_admin role if not needed")
        elif has_custom_role:
            print(f"❌ PROBLEM: User has CUSTOM ROLE - may have unrestricted access")
            print(f"   Solution: Run 'python manage.py remove_custom_roles'")
        else:
            print(f"✅ LOOKS OK: No admin/custom roles found")
            print(f"   If user still sees Finance/QHSE, check:")
            print(f"   1. Role-to-module mappings in database")
            print(f"   2. Frontend token cache (logout/login)")
        
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
    check_user_access()
