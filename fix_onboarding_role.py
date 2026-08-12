"""
Quick Fix: Assign hr_onboarding module to Onboarding role
Run this on PREPROD backend to fix lira.viaga@rejlers.ae access issue

USAGE:
    Railway: railway run python fix_onboarding_role.py
    Docker: docker exec <container> python fix_onboarding_role.py
    
This script will:
1. Find the "Onboarding" role
2. Find the "hr_onboarding" module
3. Create RoleModule assignment if missing
4. Clear user cache so changes take effect immediately
"""
import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import Role, Module, RoleModule, UserProfile
from django.core.cache import cache

User = get_user_model()

def fix_onboarding_role():
    print("\n" + "="*80)
    print("FIX: Assign hr_onboarding module to Onboarding role".center(80))
    print("="*80 + "\n")
    
    # Step 1: Find the Onboarding role
    print("🔍 Step 1: Finding 'Onboarding' role...")
    try:
        # Try different possible code variations
        role = None
        for code in ['onboarding', 'Onboarding', 'ONBOARDING']:
            try:
                role = Role.objects.get(code=code)
                print(f"✓ Found role: {role.name} (code: {role.code}, id: {role.id})")
                break
            except Role.DoesNotExist:
                continue
        
        if not role:
            # Try by name
            role = Role.objects.filter(name__icontains='onboarding').first()
            if role:
                print(f"✓ Found role by name: {role.name} (code: {role.code}, id: {role.id})")
            else:
                print("❌ ERROR: Cannot find 'Onboarding' role!")
                print("   Available roles with 'onboard' in name/code:")
                for r in Role.objects.filter(name__icontains='onboard') | Role.objects.filter(code__icontains='onboard'):
                    print(f"     • {r.name} (code: {r.code})")
                return False
                
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return False
    
    # Step 2: Find hr_onboarding module
    print("\n🔍 Step 2: Finding 'hr_onboarding' module...")
    try:
        module = Module.objects.get(code='hr_onboarding')
        print(f"✓ Found module: {module.name} (code: {module.code}, id: {module.id})")
        print(f"  Active: {module.is_active}")
    except Module.DoesNotExist:
        print("❌ ERROR: Module 'hr_onboarding' does not exist in database!")
        print("   Solution: Run migrations or seed_rbac")
        return False
    
    # Step 3: Check if assignment already exists
    print("\n🔍 Step 3: Checking if module is already assigned...")
    existing = RoleModule.objects.filter(role=role, module=module).first()
    
    if existing:
        print(f"✓ Module is ALREADY ASSIGNED to role (id: {existing.id})")
        print("  This means the assignment exists but user might have old cache.")
        print("  Proceeding to clear cache...")
    else:
        print("⚠ Module is NOT assigned to role")
        print("  Creating RoleModule assignment...")
        
        # Create the assignment
        role_module = RoleModule.objects.create(
            role=role,
            module=module
        )
        print(f"✓ Created RoleModule assignment (id: {role_module.id})")
        print(f"  Role: {role.name}")
        print(f"  Module: {module.name}")
    
    # Step 4: Clear cache for all users with this role
    print("\n🔍 Step 4: Clearing cache for affected users...")
    
    # Find lira.viaga specifically
    try:
        lira = User.objects.get(email='lira.viaga@rejlers.ae')
        profile = lira.rbac_profile
        cache_key = f'user_modules_{profile.id}'
        cache.delete(cache_key)
        print(f"✓ Cleared cache for: {lira.email}")
    except User.DoesNotExist:
        print("⚠ User 'lira.viaga@rejlers.ae' not found")
    
    # Clear cache for all users with this role
    from apps.rbac.models import UserRole
    user_roles = UserRole.objects.filter(role=role).select_related('user_profile')
    cleared_count = 0
    for ur in user_roles:
        cache_key = f'user_modules_{ur.user_profile.id}'
        cache.delete(cache_key)
        cleared_count += 1
    
    print(f"✓ Cleared cache for {cleared_count} user(s) with '{role.name}' role")
    
    # Step 5: Verify the fix
    print("\n🔍 Step 5: Verifying fix...")
    try:
        lira = User.objects.get(email='lira.viaga@rejlers.ae')
        profile = lira.rbac_profile
        accessible_modules = profile.get_all_modules()
        accessible_codes = [m.code for m in accessible_modules]
        
        if 'hr_onboarding' in accessible_codes:
            print("✅ SUCCESS! User now has access to 'hr_onboarding'")
            print(f"   Total accessible modules: {len(accessible_modules)}")
            print("\n📋 User's modules now include:")
            for m in accessible_modules:
                if m.code == 'hr_onboarding':
                    print(f"   ✓ {m.code} - {m.name} ✨ NEW!")
                elif m.code.startswith('hr_'):
                    print(f"   • {m.code} - {m.name}")
        else:
            print("❌ FAILED: User still doesn't have 'hr_onboarding' module")
            print("   Something else might be wrong. Check:")
            print("   • Module.is_active = True")
            print("   • Role.is_active = True")
            print("   • Feature flag in rbac_config.py")
            return False
            
    except User.DoesNotExist:
        print("⚠ Cannot verify - user not found")
    
    # Final instructions
    print("\n" + "="*80)
    print("🎉 FIX COMPLETED SUCCESSFULLY!")
    print("="*80)
    print("\n📝 User Actions Required:")
    print("   1. User must LOGOUT from preprod")
    print("   2. Clear browser cache (Ctrl+Shift+Del) [Optional but recommended]")
    print("   3. LOGIN again")
    print("   4. Check sidebar for '4.3 Onboarding | Offboarding'")
    print("\n🔗 Preprod URL: https://frontend-cyan-eta-q169h70uw0.vercel.app")
    print("\n")
    
    return True

if __name__ == '__main__':
    try:
        success = fix_onboarding_role()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
