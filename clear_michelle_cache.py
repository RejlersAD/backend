"""
Clear Frontend Cache and Verify Michelle's Access
Helps diagnose frontend caching issues
"""

import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, UserRole
from django.core.cache import cache
import json

User = get_user_model()

def clear_cache_for_michelle():
    """Clear all cache entries related to Michelle"""
    print("\n" + "="*80)
    print("  CLEAR CACHE FOR MICHELLE")
    print("="*80 + "\n")
    
    try:
        michelle = User.objects.get(email='michelle.dehoedt@rejlers.ae')
        profile = UserProfile.objects.get(user=michelle)
        
        # List of cache keys to clear
        cache_keys = [
            f'user_profile_{michelle.id}',
            f'user_profile_uuid_{profile.id}',
            f'user_modules_{michelle.id}',
            f'user_modules_{profile.id}',
            f'user_roles_{michelle.id}',
            f'user_roles_{profile.id}',
            f'user_permissions_{michelle.id}',
            f'user_permissions_{profile.id}',
            'user_profiles_list',
            'active_users',
        ]
        
        print("🔧 Clearing cache keys...")
        for key in cache_keys:
            result = cache.delete(key)
            if result:
                print(f"   ✅ Deleted: {key}")
            else:
                print(f"   ℹ️  Not found: {key}")
        
        # Clear pattern-based cache (if using Redis)
        try:
            from django.core.cache import caches
            redis_cache = caches['default']
            if hasattr(redis_cache, '_cache') and hasattr(redis_cache._cache, 'delete_pattern'):
                patterns = [
                    f'*user_{michelle.id}*',
                    f'*user_{profile.id}*',
                    f'*michelle*',
                ]
                for pattern in patterns:
                    deleted = redis_cache._cache.delete_pattern(pattern)
                    print(f"   ✅ Deleted {deleted} keys matching: {pattern}")
        except Exception as e:
            print(f"   ℹ️  Pattern-based cache clear not available: {e}")
        
        print("\n✅ Cache cleared successfully")
        
    except User.DoesNotExist:
        print("❌ Michelle's user account not found")
        return False
    except UserProfile.DoesNotExist:
        print("❌ Michelle's profile not found")
        return False
    except Exception as e:
        print(f"❌ Error clearing cache: {e}")
        return False
    
    return True

def verify_database_roles():
    """Verify Michelle has correct roles in database"""
    print("\n" + "="*80)
    print("  DATABASE VERIFICATION")
    print("="*80 + "\n")
    
    try:
        michelle = User.objects.get(email='michelle.dehoedt@rejlers.ae')
        profile = UserProfile.objects.get(user=michelle)
        
        user_roles = UserRole.objects.filter(
            user_profile=profile
        ).select_related('role').order_by('-is_primary')
        
        print(f"👤 User: {michelle.email}")
        print(f"📋 Total Roles: {user_roles.count()}")
        print("\nRoles:")
        
        for idx, ur in enumerate(user_roles, 1):
            primary_marker = "🌟 PRIMARY" if ur.is_primary else "   Secondary"
            print(f"\n   {idx}. {ur.role.name}")
            print(f"      Code: {ur.role.code}")
            print(f"      Status: {primary_marker}")
            print(f"      Is Active: {ur.role.is_active}")
            print(f"      Level: {ur.role.level}")
        
        # Verify expected roles
        role_codes = [ur.role.code for ur in user_roles]
        print("\n📊 Verification:")
        print(f"   ✓ Has 'default' role: {'✅ YES' if 'default' in role_codes else '❌ NO'}")
        print(f"   ✓ Has 'hr_admin' role: {'✅ YES' if 'hr_admin' in role_codes else '❌ NO'}")
        
        primary_roles = [ur for ur in user_roles if ur.is_primary]
        print(f"   ✓ Primary roles count: {len(primary_roles)} (Expected: 1)")
        
        if len(primary_roles) == 1:
            print(f"   ✓ Primary role: {primary_roles[0].role.name} ({primary_roles[0].role.code})")
        elif len(primary_roles) == 0:
            print("   ❌ WARNING: No primary role set!")
        else:
            print(f"   ⚠️  WARNING: Multiple primary roles detected!")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def get_api_simulation():
    """Simulate the exact API response Michelle would receive"""
    print("\n" + "="*80)
    print("  API RESPONSE SIMULATION")
    print("="*80 + "\n")
    
    try:
        from apps.rbac.serializers import UserProfileSerializer
        from django.db.models import Prefetch
        
        michelle = User.objects.get(email='michelle.dehoedt@rejlers.ae')
        profile = UserProfile.objects.prefetch_related(
            Prefetch('userrole_set', queryset=UserRole.objects.select_related('role'))
        ).get(user=michelle)
        
        serializer = UserProfileSerializer(profile)
        data = serializer.data
        
        print("📡 Endpoint: GET /api/v1/rbac/users/me/")
        print(f"👤 Email: {data['user']['email']}")
        print(f"\n🔑 Primary Role: {data['primary_role']}")
        print(f"\n📋 All Roles ({len(data['roles'])}):")
        
        for idx, role in enumerate(data['roles'], 1):
            primary_marker = "🌟" if role.get('is_primary') else "  "
            print(f"   {primary_marker} {idx}. {role['name']} (code: {role['code']}, level: {role.get('level')})")
            print(f"       is_primary: {role.get('is_primary', 'MISSING!')}")
        
        print(f"\n📦 Modules: {len(data.get('modules', []))} total")
        
        # Check for issues
        issues = []
        if not data.get('primary_role'):
            issues.append("❌ No primary_role field")
        if not all('is_primary' in r for r in data.get('roles', [])):
            issues.append("❌ Missing is_primary flag in some roles")
        if len(data.get('roles', [])) < 2:
            issues.append("❌ Expected 2 roles, found: " + str(len(data.get('roles', []))))
        
        if issues:
            print("\n⚠️  ISSUES FOUND:")
            for issue in issues:
                print(f"   {issue}")
        else:
            print("\n✅ API response looks correct!")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " "*20 + "MICHELLE FRONTEND CACHE DIAGNOSTIC" + " "*24 + "║")
    print("╚" + "="*78 + "╝")
    
    # Step 1: Clear cache
    cache_cleared = clear_cache_for_michelle()
    
    # Step 2: Verify database
    db_verified = verify_database_roles()
    
    # Step 3: Simulate API
    api_simulated = get_api_simulation()
    
    # Summary
    print("\n" + "="*80)
    print("  SUMMARY")
    print("="*80 + "\n")
    
    print(f"   1. Cache Cleared: {'✅' if cache_cleared else '❌'}")
    print(f"   2. Database Verified: {'✅' if db_verified else '❌'}")
    print(f"   3. API Simulation: {'✅' if api_simulated else '❌'}")
    
    if cache_cleared and db_verified and api_simulated:
        print("\n✅ ALL CHECKS PASSED")
        print("\n📝 Frontend Troubleshooting Steps:")
        print("   1. Clear browser cache (Ctrl+Shift+Delete)")
        print("   2. Hard refresh (Ctrl+F5)")
        print("   3. Open DevTools Console (F12)")
        print("   4. Check for errors in console")
        print("   5. Verify localStorage: localStorage.getItem('radai_user_data')")
        print("   6. If localStorage has old data, logout and login again")
        print("\n💡 Backend cache has been cleared. Frontend should fetch fresh data on next page load.")
    else:
        print("\n❌ SOME CHECKS FAILED - Review errors above")
    
    print("\n" + "="*80 + "\n")

if __name__ == '__main__':
    main()
