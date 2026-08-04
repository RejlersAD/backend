"""
Sync HR Admin Role Modules
Applies ROLE_MODULE_POLICY modules to all users with hr_admin role
"""

import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module, UserRole
from apps.rbac.rbac_config import ROLE_MODULE_POLICY
from django.core.cache import cache

User = get_user_model()

def sync_hr_admin_modules():
    """Sync modules for hr_admin role"""
    print("\n" + "="*80)
    print("  SYNC HR ADMIN ROLE MODULES")
    print("="*80 + "\n")
    
    try:
        # Get hr_admin role
        hr_admin_role = Role.objects.get(code='hr_admin')
        print(f"✅ Found role: {hr_admin_role.name} (code: {hr_admin_role.code})")
        
        # Get modules from policy
        hr_admin_module_codes = ROLE_MODULE_POLICY.get('hr_admin', [])
        print(f"\n📋 Modules in ROLE_MODULE_POLICY for hr_admin: {len(hr_admin_module_codes)}")
        
        if len(hr_admin_module_codes) == 0:
            print("❌ ERROR: hr_admin has no modules in ROLE_MODULE_POLICY!")
            print("   Make sure rbac_config.py has been updated")
            return False
        
        for code in hr_admin_module_codes:
            print(f"   - {code}")
        
        # Get Module objects
        modules = []
        for code in hr_admin_module_codes:
            try:
                module = Module.objects.get(code=code)
                modules.append(module)
            except Module.DoesNotExist:
                print(f"⚠️  WARNING: Module '{code}' not found in database, skipping")
        
        print(f"\n✅ Found {len(modules)} modules in database")
        
        # Get current modules assigned to role
        current_module_codes = set(hr_admin_role.modules.values_list('code', flat=True))
        target_module_codes = set(hr_admin_module_codes)
        
        missing_modules = target_module_codes - current_module_codes
        
        if not missing_modules:
            print(f"\n✅ Role already has all {len(target_module_codes)} modules assigned")
        else:
            print(f"\n📝 Adding {len(missing_modules)} missing modules to hr_admin role:")
            for code in missing_modules:
                print(f"   + {code}")
            
            # Add missing modules to role
            for module in modules:
                if module.code in missing_modules:
                    hr_admin_role.modules.add(module)
            
            print(f"\n✅ Modules added to hr_admin role")
        
        # Get all users with hr_admin role and clear their cache
        user_roles = UserRole.objects.filter(role=hr_admin_role).select_related('user_profile', 'user_profile__user')
        
        print(f"\n👥 Users with hr_admin role: {user_roles.count()}")
        
        if user_roles.count() > 0:
            print(f"\n🔄 Clearing cache for users:")
            for user_role in user_roles:
                profile = user_role.user_profile
                user = profile.user
                
                print(f"   • {user.email}")
                
                # Clear cache
                cache_keys = [
                    f'user_modules_{user.id}',
                    f'user_modules_{profile.id}',
                    f'user_permissions_{user.id}',
                    f'user_permissions_{profile.id}',
                ]
                for key in cache_keys:
                    cache.delete(key)
            
            print(f"\n✅ Cache cleared for {user_roles.count()} user(s)")
        
        print("\n" + "="*80)
        print(f"✅ SYNC COMPLETE")
        print("   • hr_admin role has {len(hr_admin_role.modules.all())} modules")
        print(f"   • {user_roles.count()} users will inherit these modules")
        print("="*80 + "\n")
        
        return True
        
    except Role.DoesNotExist:
        print("❌ ERROR: hr_admin role not found in database")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = sync_hr_admin_modules()
    sys.exit(0 if success else 1)
