"""
Grant Full QHSE Module Access
Soft-coded script to assign complete QHSE permissions to specified users
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Module, Permission, Role, RoleModule, RolePermission, UserRole
from django.db import transaction

User = get_user_model()

# Soft-coded user list - easily maintainable
USERS_TO_GRANT = [
    'rajasekhar.pasumarthi@rejlers.ae',
    'shaju.chacko@rejlers.ae',
    'ravikumar.naickar@rejlers.ae',
    'Shamma.Alkaabi@rejlers.ae'
]

# Soft-coded module configuration
MODULE_CODE = 'qhse'
ROLE_NAME_TEMPLATE = 'QHSE Full Access - {email}'  # Dynamic role name
ROLE_DESCRIPTION_TEMPLATE = 'Full QHSE module permissions for {email}'

def grant_qhse_access(email):
    """
    Grant full QHSE module access to a user
    Soft-coded with dynamic role and permission assignment
    """
    try:
        print(f"\n{'='*80}")
        print(f"🔧 GRANTING QHSE ACCESS TO: {email}")
        print(f"{'='*80}\n")
        
        # Step 1: Get user
        try:
            user = User.objects.get(email=email)
            print(f"✅ Found user: {user.first_name} {user.last_name} ({user.email})")
        except User.DoesNotExist:
            print(f"❌ User not found: {email}")
            return False
        
        # Step 2: Get or create user profile
        profile, created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'is_deleted': False
            }
        )
        if created:
            print(f"✅ Created new RBAC profile for {email}")
        else:
            print(f"✅ Found existing RBAC profile for {email}")
        
        # Step 3: Get QHSE module (soft-coded lookup)
        try:
            qhse_module = Module.objects.get(code=MODULE_CODE, is_active=True)
            print(f"✅ Found module: {qhse_module.name} (code: {qhse_module.code})")
        except Module.DoesNotExist:
            print(f"❌ QHSE module not found with code '{MODULE_CODE}'")
            return False
        
        # Step 4: Get or create role for this user (soft-coded role creation)
        role_name = ROLE_NAME_TEMPLATE.format(email=email.split('@')[0])
        role_description = ROLE_DESCRIPTION_TEMPLATE.format(email=email)
        
        role, role_created = Role.objects.get_or_create(
            code=f"qhse_full_{email.split('@')[0]}",
            defaults={
                'name': role_name,
                'description': role_description,
                'is_active': True
            }
        )
        if role_created:
            print(f"✅ Created new role: {role.name}")
        else:
            print(f"✅ Using existing role: {role.name}")
        
        # Step 5: Link module to role (soft-coded module-role relationship)
        role_module, rm_created = RoleModule.objects.get_or_create(
            role=role,
            module=qhse_module
        )
        if rm_created:
            print(f"✅ Linked QHSE module to role")
        else:
            print(f"ℹ️  QHSE module already linked to role")
        
        # Step 6: Get all QHSE permissions (soft-coded permission lookup)
        qhse_permissions = Permission.objects.filter(
            module=qhse_module,
            is_active=True
        )
        
        if qhse_permissions.exists():
            print(f"\n🔐 Assigning {qhse_permissions.count()} QHSE permissions to role...")
            
            # Step 7: Assign all permissions to role (soft-coded permission assignment)
            with transaction.atomic():
                assigned_count = 0
                for permission in qhse_permissions:
                    role_perm, created = RolePermission.objects.get_or_create(
                        role=role,
                        permission=permission
                    )
                    if created:
                        assigned_count += 1
                        print(f"   ✅ {permission.code}: {permission.name}")
                    else:
                        print(f"   ℹ️  {permission.code}: {permission.name} (already assigned)")
                
                if assigned_count > 0:
                    print(f"\n✅ Assigned {assigned_count} new permissions")
                else:
                    print(f"\nℹ️  All permissions were already assigned")
        else:
            print(f"\nℹ️  No individual permissions found for QHSE module")
            print(f"ℹ️  Access granted through RoleModule relationship")
        
        # Step 8: Assign role to user (soft-coded user-role relationship)
        user_role, ur_created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=role,
            defaults={
                'is_primary': False,
                'assigned_by': user  # Self-assigned by script
            }
        )
        if ur_created:
            print(f"✅ Assigned role to user profile")
        else:
            print(f"ℹ️  Role already assigned to user profile")
        
        # Step 9: Verify access (soft-coded verification)
        print(f"\n{'='*80}")
        print(f"🔍 VERIFICATION")
        print(f"{'='*80}")
        
        # Refresh profile to get latest data
        profile.refresh_from_db()
        
        # Get user's roles and modules through database queries
        user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
        role_modules = RoleModule.objects.filter(
            role__in=[ur.role for ur in user_roles]
        ).select_related('module')
        
        module_codes = [rm.module.code for rm in role_modules]
        has_qhse = MODULE_CODE in module_codes
        
        print(f"   • User: {user.email}")
        print(f"   • Profile ID: {profile.id}")
        print(f"   • Assigned Role: {role.name}")
        print(f"   • Total Roles: {user_roles.count()}")
        print(f"   • Total Modules: {len(module_codes)}")
        print(f"   • Module Codes: {', '.join(module_codes)}")
        print(f"   • QHSE Module: {'✅ GRANTED' if has_qhse else '❌ NOT FOUND'}")
        print(f"   • QHSE Permissions: {qhse_permissions.count() if qhse_permissions.exists() else 'N/A (module-based access)'}")
        
        if has_qhse:
            print(f"\n{'='*80}")
            print(f"✅ SUCCESS! {email} now has full QHSE access")
            print(f"{'='*80}\n")
            return True
        else:
            print(f"\n{'='*80}")
            print(f"❌ WARNING! QHSE module not found in user's access")
            print(f"{'='*80}\n")
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """
    Main function to grant QHSE access to all specified users
    Soft-coded with dynamic user list
    """
    print(f"\n{'#'*80}")
    print(f"# QHSE FULL ACCESS GRANT SCRIPT")
    print(f"# Soft-coded permission assignment system")
    print(f"{'#'*80}\n")
    
    print(f"📋 Users to grant QHSE access:")
    for email in USERS_TO_GRANT:
        print(f"   • {email}")
    
    print(f"\n🎯 Target Module: {MODULE_CODE.upper()}")
    print(f"🎯 Permission Level: FULL ACCESS\n")
    
    results = {}
    
    for email in USERS_TO_GRANT:
        success = grant_qhse_access(email)
        results[email] = success
    
    # Summary
    print(f"\n{'#'*80}")
    print(f"# SUMMARY")
    print(f"{'#'*80}\n")
    
    successful = sum(1 for v in results.values() if v)
    failed = len(results) - successful
    
    print(f"✅ Successful: {successful}/{len(results)}")
    print(f"❌ Failed: {failed}/{len(results)}\n")
    
    for email, success in results.items():
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"   {status}: {email}")
    
    print(f"\n{'#'*80}\n")


if __name__ == '__main__':
    main()
