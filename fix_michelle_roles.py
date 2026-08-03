"""
Fix Michelle's Role Assignment
- Ensure she has both 'Default' and 'HR & Payroll Administrator' roles
- Verify both roles are active
- Update role assignments without changing core logic
"""

import os
import django
import sys

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from django.utils import timezone

User = get_user_model()

def print_header(text):
    print("\n" + "="*80)
    print(f"  {text}")
    print("="*80)

def fix_michelle_roles():
    print_header("FIXING MICHELLE'S ROLE ASSIGNMENT")
    
    email = 'michelle.dehoedt@rejlers.ae'
    
    # 1. Get user and profile
    try:
        user = User.objects.get(email=email)
        print(f"\n✅ User found: {user.email} (ID: {user.id})")
    except User.DoesNotExist:
        print(f"\n❌ ERROR: User not found: {email}")
        return
    
    profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
    if not profile:
        print(f"❌ ERROR: No UserProfile found for {user.email}")
        return
    
    print(f"✅ Profile found: {profile.id}")
    
    # 2. Get the required roles
    print("\n📋 Fetching required roles...")
    
    try:
        default_role = Role.objects.get(code='default')
        print(f"✅ Default role found: {default_role.name} (is_active: {default_role.is_active})")
    except Role.DoesNotExist:
        print("❌ ERROR: 'Default' role not found in system")
        return
    
    try:
        hr_role = Role.objects.get(code='hr_admin')
        print(f"✅ HR Admin role found: {hr_role.name} (is_active: {hr_role.is_active})")
    except Role.DoesNotExist:
        print("❌ ERROR: 'HR & Payroll Administrator' role not found")
        return
    
    # 3. Ensure both roles are active
    roles_updated = []
    
    if not default_role.is_active:
        default_role.is_active = True
        default_role.save()
        roles_updated.append('Default')
        print(f"🔧 Activated 'Default' role")
    
    if not hr_role.is_active:
        hr_role.is_active = True
        hr_role.save()
        roles_updated.append('HR Admin')
        print(f"🔧 Activated 'HR Admin' role")
    
    if roles_updated:
        print(f"\n✅ Activated roles: {', '.join(roles_updated)}")
    else:
        print("\n✅ Both roles are already active")
    
    # 4. Check current role assignments
    print("\n📋 Current role assignments:")
    current_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
    
    for ur in current_roles:
        primary = "⭐ PRIMARY" if ur.is_primary else "   SECONDARY"
        print(f"   {primary} | {ur.role.name} (code: {ur.role.code})")
    
    # 5. Assign Default role if missing
    default_assigned = UserRole.objects.filter(user_profile=profile, role=default_role).exists()
    hr_assigned = UserRole.objects.filter(user_profile=profile, role=hr_role).exists()
    
    changes_made = False
    
    if not default_assigned:
        UserRole.objects.create(
            user_profile=profile,
            role=default_role,
            is_primary=False  # HR Admin should remain primary
        )
        print(f"\n✅ Added 'Default' role to Michelle")
        changes_made = True
    else:
        print(f"\n✅ 'Default' role already assigned")
    
    if not hr_assigned:
        UserRole.objects.create(
            user_profile=profile,
            role=hr_role,
            is_primary=True  # Make HR Admin primary
        )
        print(f"✅ Added 'HR & Payroll Administrator' role to Michelle")
        changes_made = True
    else:
        print(f"✅ 'HR & Payroll Administrator' role already assigned")
        
        # Ensure HR Admin is primary
        hr_user_role = UserRole.objects.get(user_profile=profile, role=hr_role)
        if not hr_user_role.is_primary:
            hr_user_role.is_primary = True
            hr_user_role.save()
            print(f"🔧 Set 'HR & Payroll Administrator' as primary role")
            changes_made = True
    
    # 6. Verify final state
    print_header("FINAL ROLE ASSIGNMENT")
    
    final_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
    print(f"\nMichelle now has {final_roles.count()} role(s):\n")
    
    for ur in final_roles:
        status = "✅ ACTIVE" if ur.role.is_active else "❌ INACTIVE"
        primary = "⭐ PRIMARY" if ur.is_primary else "   SECONDARY"
        print(f"{status} | {primary} | {ur.role.name:30} | Code: {ur.role.code}")
    
    # 7. Test API response
    print_header("TESTING API RESPONSE")
    
    from apps.rbac.serializers import UserProfileSerializer
    serializer = UserProfileSerializer(profile)
    data = serializer.data
    
    print(f"\nRoles that will appear in /api/v1/rbac/users/me/:")
    for role in data.get('roles', []):
        primary_mark = "⭐" if role.get('is_primary') else "  "
        print(f"   {primary_mark} {role.get('name')} (code: {role.get('code')})")
    
    print(f"\nTotal permissions: {len(data.get('permissions', []))}")
    print(f"Total modules: {len(data.get('modules', []))}")
    
    if changes_made:
        print("\n" + "="*80)
        print("✅ CHANGES APPLIED SUCCESSFULLY")
        print("="*80)
        print("\n📌 Next Steps:")
        print("   1. Michelle should logout and login again")
        print("   2. Or clear browser cache and refresh")
        print("   3. Verify she can access HR features")
    else:
        print("\n" + "="*80)
        print("✅ NO CHANGES NEEDED - ROLES ALREADY CORRECT")
        print("="*80)
        print("\n💡 If Michelle still has issues:")
        print("   1. Check browser console for errors")
        print("   2. Verify JWT token is not cached")
        print("   3. Clear localStorage and cookies")
        print("   4. Check backend logs for /rbac/users/me/ endpoint")

def main():
    print("\n" + "╔" + "═"*78 + "╗")
    print("║" + " "*25 + "MICHELLE ROLE FIX" + " "*30 + "║")
    print("╚" + "═"*78 + "╝")
    
    fix_michelle_roles()
    
    print("\n" + "="*80)
    print("  FIX COMPLETE")
    print("="*80 + "\n")

if __name__ == '__main__':
    main()
