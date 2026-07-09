"""
Fix ICT Admin Role Assignment
Assigns proper admin role to ICT department users instead of super_admin

Usage: python manage.py shell < _fix_ict_admin_role.py
"""
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module, RoleModule, UserRole

User = get_user_model()

# ══════════════════════════════════════════════════════════════════════════
# SOFT-CODED: ICT Admin Configuration
# ══════════════════════════════════════════════════════════════════════════
# ICT department users get admin access but NOT super admin privileges.
# They can manage users and roles but cannot modify system-level settings.

ICT_ADMIN_CONFIG = {
    'emails': [
        'radai@rejlers.ae',
    ],
    'role_code': 'admin',  # NOT super_admin
    'department': 'ICT',
    'modules': [
        # Admin section modules
        'admin_dashboard',
        'user_mgmt',
        'role_access_mgmt',
        'wrench_integration',
        'ai_champion',
        'enquiry_management',
    ],
    # SOFT-CODED: Set is_staff=True to allow Django admin panel access
    # but is_superuser=False to prevent unrestricted access
    'set_staff_flag': True,
    'set_superuser_flag': False,
}
# ══════════════════════════════════════════════════════════════════════════

def fix_ict_admin_roles():
    """
    Assign proper admin role to ICT users.
    Removes super_admin role if present and assigns admin role instead.
    """
    print(f"\n{'='*80}")
    print(f"ICT ADMIN ROLE FIX")
    print(f"{'='*80}\n")
    
    # Get or create the admin role
    try:
        admin_role = Role.objects.get(code=ICT_ADMIN_CONFIG['role_code'], is_active=True)
        print(f"✅ Admin role found: {admin_role.name} (level {admin_role.level})")
    except Role.DoesNotExist:
        print(f"❌ Admin role not found with code: {ICT_ADMIN_CONFIG['role_code']}")
        return
    
    # Verify admin role has the correct modules
    role_module_codes = set(
        RoleModule.objects.filter(role=admin_role)
        .values_list('module__code', flat=True)
    )
    
    print(f"\n📦 Admin role current modules: {len(role_module_codes)}")
    missing_modules = set(ICT_ADMIN_CONFIG['modules']) - role_module_codes
    if missing_modules:
        print(f"⚠️  Missing modules in admin role: {', '.join(missing_modules)}")
        print(f"   Run: python manage.py seed_rbac")
    else:
        print(f"✅ All required admin modules present in role")
    
    print()
    
    # Process each ICT admin user
    for email in ICT_ADMIN_CONFIG['emails']:
        print(f"\n{'─'*80}")
        print(f"Processing: {email}")
        print(f"{'─'*80}")
        
        try:
            user = User.objects.get(email=email)
            print(f"✅ User found")
        except User.DoesNotExist:
            print(f"❌ User not found: {email}")
            continue
        
        # Get or create RBAC profile
        try:
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            print(f"✅ RBAC profile found")
        except UserProfile.DoesNotExist:
            print(f"❌ No RBAC profile - user needs to be set up in User Management first")
            continue
        
        # Check current roles
        current_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
        print(f"\n📋 Current roles ({current_roles.count()}):")
        for ur in current_roles:
            print(f"   - {ur.role.name} (code: {ur.role.code}, level: {ur.role.level})")
        
        # Remove super_admin role if present
        super_admin_removed = False
        for ur in current_roles:
            if ur.role.code == 'super_admin':
                print(f"\n⚠️  Removing super_admin role (inappropriate for ICT admin)")
                ur.delete()
                super_admin_removed = True
        
        # Add admin role if not present
        admin_role_obj, created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=admin_role,
            defaults={'is_primary': True, 'assigned_by': None}
        )
        
        if created:
            print(f"✅ Assigned admin role")
        else:
            print(f"✅ Admin role already assigned")
        
        # Update Django User flags
        changes = []
        if user.is_staff != ICT_ADMIN_CONFIG['set_staff_flag']:
            user.is_staff = ICT_ADMIN_CONFIG['set_staff_flag']
            changes.append(f"is_staff={user.is_staff}")
        
        if user.is_superuser != ICT_ADMIN_CONFIG['set_superuser_flag']:
            user.is_superuser = ICT_ADMIN_CONFIG['set_superuser_flag']
            changes.append(f"is_superuser={user.is_superuser}")
        
        if changes:
            user.save()
            print(f"✅ Updated User flags: {', '.join(changes)}")
        else:
            print(f"✅ User flags already correct")
        
        # Update profile department
        if profile.department != ICT_ADMIN_CONFIG['department']:
            profile.department = ICT_ADMIN_CONFIG['department']
            profile.save()
            print(f"✅ Updated department to: {ICT_ADMIN_CONFIG['department']}")
        
        # Clear user's module cache
        from django.core.cache import cache
        cache.delete(f'user_modules_{profile.id}')
        cache.delete(f'user_permissions_{profile.id}')
        print(f"✅ Cleared module/permission cache")
        
        # Summary
        print(f"\n🎯 FINAL STATE:")
        print(f"   - Role: {admin_role.name} (level {admin_role.level})")
        print(f"   - is_staff: {user.is_staff}")
        print(f"   - is_superuser: {user.is_superuser}")
        print(f"   - Department: {profile.department}")
        print(f"   - Module Access: {len(ICT_ADMIN_CONFIG['modules'])} admin modules")
        
        if super_admin_removed or created or changes:
            print(f"\n⚠️  USER MUST REFRESH BROWSER OR RE-LOGIN to see changes")
    
    print(f"\n{'='*80}")
    print(f"✅ ICT ADMIN ROLE FIX COMPLETED")
    print(f"{'='*80}\n")


if __name__ == '__main__':
    fix_ict_admin_roles()
