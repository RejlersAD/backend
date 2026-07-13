"""
PRODUCTION FIX - Remove Admin Role from kiran.ingale@rejlers.ae
Run in Railway shell: python fix_kiran_production.py

This fixes the issue where user can still see Finance/QHSE despite config changes
"""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module
from django.db import transaction
from django.core.cache import cache

User = get_user_model()

def fix_user_access():
    email = 'kiran.ingale@rejlers.ae'
    
    print("=" * 80)
    print(f"🔧 COMPLETE PRODUCTION FIX - Removing ALL Admin Access")
    print(f"   Target: {email}")
    print("=" * 80)
    
    try:
        user = User.objects.get(email=email)
        profile = user.rbac_profile
        
        print(f"\n📋 CURRENT STATE:")
        print(f"   User: {user.first_name} {user.last_name}")
        print(f"   Email: {user.email}")
        print(f"   Status: {profile.status}")
        print(f"   Django is_staff: {user.is_staff}")
        print(f"   Django is_superuser: {user.is_superuser}")
        
        # Check current roles
        print(f"\n🎭 CURRENT ROLES:")
        current_roles = profile.roles.filter(is_active=True)
        
        has_admin = False
        has_default = False
        
        for role in current_roles:
            is_admin_role = role.code in ['admin', 'ict_admin']
            is_default_role = role.code == 'default'
            
            if is_admin_role:
                has_admin = True
                print(f"   ❌ {role.name} (code: {role.code}, level: {role.level}) - WILL BE REMOVED")
            elif is_default_role:
                has_default = True
                print(f"   ✅ {role.name} (code: {role.code}, level: {role.level}) - KEEP")
            else:
                print(f"   • {role.name} (code: {role.code}, level: {role.level})")
        
        # Check direct module assignments BEFORE checking if has_admin
        direct_modules = profile.modules.filter(is_active=True)
        
        if not has_admin:
            # Check if Django flags are the issue
            if user.is_staff or user.is_superuser:
                print(f"\n⚠️  USER HAS DJANGO ADMIN FLAGS SET")
                print(f"   is_staff: {user.is_staff}")
                print(f"   is_superuser: {user.is_superuser}")
                print(f"   These flags cause FRONTEND to bypass RBAC!")
                print(f"   Will fix below...")
            elif direct_modules.filter(code__in=['finance', 'qhse', 'hr_management', 'admin']).exists():
                print(f"\n⚠️  USER HAS DIRECT MODULE ASSIGNMENTS")
                print(f"   Will clear below...")
            else:
                print(f"\n✅ USER ALREADY FIXED - No admin role found")
                print(f"   If user still sees Finance/QHSE, tell them to:")
                print(f"   1. Logout from https://www.radai.ae")
                print(f"   2. Login again (to refresh JWT token)")
                print(f"   3. Hard refresh browser (Ctrl+F5)")
                return
        
        # Apply fix with transaction safety
        print(f"\n🔄 APPLYING FIX...")
        
        with transaction.atomic():
            # Step 0: Fix Django user flags (CRITICAL - Frontend checks these!)
            if user.is_staff or user.is_superuser:
                print(f"\n   0️⃣  Fixing Django user flags:")
                if user.is_staff:
                    user.is_staff = False
                    print(f"   ✅ Set is_staff = False")
                if user.is_superuser:
                    user.is_superuser = False
                    print(f"   ✅ Set is_superuser = False")
                user.save(update_fields=['is_staff', 'is_superuser'])
            
            # Step 1: Remove admin roles
            admin_roles = profile.roles.filter(
                code__in=['admin', 'ict_admin'], 
                is_active=True
            )
            
            removed_count = 0
            for role in admin_roles:
                profile.roles.remove(role)
                removed_count += 1
                print(f"   ❌ Removed: {role.code} ({role.name})")
            
            # Step 2: Ensure user has default role
            if not has_default:
                default_role = Role.objects.get(code='default', is_active=True)
                profile.roles.add(default_role)
                print(f"   ✅ Added: default (Default)")
            
            # Step 3: Clear all direct module assignments (already defined above)
            if direct_modules.exists():
                profile.modules.clear()
                print(f"   🧹 Cleared {direct_modules.count()} direct module assignments")
            else:
                print(f"   ✅ No direct module assignments to clear")
            
            # Step 4: Clear cache
            cache.delete(f'user_modules_{profile.id}')
            cache.delete(f'user_permissions_{profile.id}')
            print(f"   🗑️  Cleared module cache")
            
            print(f"\n✅ FIX APPLIED SUCCESSFULLY")
        
        # Verify after fix
        print(f"\n📋 VERIFICATION:")
        updated_roles = profile.roles.filter(is_active=True)
        print(f"\n🎭 UPDATED ROLES ({updated_roles.count()}):")
        for role in updated_roles:
            print(f"   ✅ {role.name} (code: {role.code}, level: {role.level})")
        
        # Check accessible modules
        modules = profile.get_all_modules()
        
        finance_modules = [m for m in modules if 'finance' in m.code.lower()]
        qhse_modules = [m for m in modules if 'qhse' in m.code.lower()]
        hr_modules = [m for m in modules if 'hr' in m.code.lower() or 'payroll' in m.code.lower()]
        admin_modules = [m for m in modules if 'admin' in m.code.lower() or 'user' in m.code.lower()]
        
        print(f"\n📦 ACCESSIBLE MODULES ({len(modules)} total):")
        
        if finance_modules or qhse_modules or hr_modules or admin_modules:
            print(f"\n   ⚠️  STILL HAS RESTRICTED MODULES:")
            if finance_modules:
                print(f"   💰 Finance: {len(finance_modules)} modules")
            if qhse_modules:
                print(f"   🛡️  QHSE: {len(qhse_modules)} modules")
            if hr_modules:
                print(f"   👥 HR: {len(hr_modules)} modules")
            if admin_modules:
                print(f"   👨‍💼 Admin: {len(admin_modules)} modules")
            print(f"\n   🔍 This may be from:")
            print(f"      - Other roles assigned (check above)")
            print(f"      - Role-to-module mappings in ROLE_MODULE_POLICY")
        else:
            print(f"   ✅ NO restricted modules (Finance, QHSE, HR, Admin)")
            print(f"   ✅ User should only see Engineering + Common modules")
        
        # Final instructions
        print(f"\n" + "=" * 80)
        print(f"📋 NEXT STEPS:")
        print("=" * 80)
        print(f"\n1️⃣  Tell user {email} to:")
        print(f"   • Logout from https://www.radai.ae")
        print(f"   • Login again (this refreshes JWT token with new permissions)")
        print(f"   • Hard refresh browser (Ctrl+F5 or Cmd+Shift+R)")
        print(f"\n2️⃣  Verify user CANNOT see:")
        print(f"   ❌ Finance")
        print(f"   ❌ QHSE")
        print(f"   ❌ Human Resources")
        print(f"   ❌ Admin")
        print(f"\n3️⃣  Verify user CAN see:")
        print(f"   ✅ Engineering (all sub-sections)")
        print(f"   ✅ COMMON (CRS, PFD to P&ID, DesignIQ)")
        print(f"\n4️⃣  Test direct URL access:")
        print(f"   Try: https://www.radai.ae/finance")
        print(f"   Expected: Access Denied or redirect to dashboard")
        
        print(f"\n✅ DATABASE FIX COMPLETE!")
        print()
        
    except User.DoesNotExist:
        print(f"❌ User '{email}' not found in database")
    except UserProfile.DoesNotExist:
        print(f"❌ User profile for '{email}' not found")
    except Role.DoesNotExist as e:
        print(f"❌ Role not found: {str(e)}")
        print(f"   Run: python manage.py seed_rbac")
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    fix_user_access()
