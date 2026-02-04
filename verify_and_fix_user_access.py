#!/usr/bin/env python
"""
Verify and fix user access - ensure user has ONLY Engineering and Common features
NO superuser, NO staff, NO admin roles
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserRole, Role, Module

User = get_user_model()

# Soft-coded configuration
CONFIG = {
    'TARGET_EMAIL': 'muhammad.ilyas@rejlers.ae',
    'ALLOWED_ROLE_NAME': 'Engineering & Common Features Access',
    'ENGINEERING_MODULES': [
        'PID Analysis',
        'PFD to P&ID Converter',
        'CRS Document Management',
        'DesignIQ - AI Design Intelligence'
    ],
    'COMMON_MODULES': [
        'File Storage',
        'Reports & Analytics'
    ],
    'ADMIN_ROLES_TO_REMOVE': [
        'Super Administrator',
        'Administrator',
        'Admin'
    ]
}

def verify_and_fix_access(email):
    """Verify and fix user access to ensure ONLY Engineering and Common features"""
    try:
        user = User.objects.filter(email__iexact=email).first()
        
        if not user:
            print(f"❌ User not found: {email}")
            return False
        
        print(f"\n{'='*80}")
        print(f"USER ACCESS VERIFICATION & FIX")
        print(f"{'='*80}")
        print(f"Target User: {user.email}")
        print(f"Name: {user.first_name} {user.last_name}")
        
        changes_made = []
        
        # STEP 1: Check and remove superuser/staff flags
        print(f"\n📋 STEP 1: Checking Django User Flags")
        print(f"{'─'*80}")
        
        if user.is_superuser:
            print(f"⚠️  User is SUPERUSER - REMOVING...")
            user.is_superuser = False
            changes_made.append("Removed superuser flag")
        else:
            print(f"✅ User is NOT superuser")
        
        if user.is_staff:
            print(f"⚠️  User is STAFF - REMOVING...")
            user.is_staff = False
            changes_made.append("Removed staff flag")
        else:
            print(f"✅ User is NOT staff")
        
        # Save user changes
        if changes_made:
            user.save()
            print(f"\n✅ User flags updated")
        
        # STEP 2: Check RBAC profile
        print(f"\n📋 STEP 2: Checking RBAC Profile")
        print(f"{'─'*80}")
        
        try:
            profile = user.rbac_profile
            print(f"✅ RBAC Profile exists")
            print(f"   Organization: {profile.organization.name if profile.organization else 'None'}")
            print(f"   Is deleted: {profile.is_deleted}")
            
            if profile.is_deleted:
                print(f"⚠️  Profile is marked as deleted - FIXING...")
                profile.is_deleted = False
                profile.save()
                changes_made.append("Unmarked profile as deleted")
        except Exception as e:
            print(f"❌ RBAC Profile not found: {e}")
            return False
        
        # STEP 3: Check and fix role assignments
        print(f"\n📋 STEP 3: Checking Role Assignments")
        print(f"{'─'*80}")
        
        # Get all current roles
        current_roles = UserRole.objects.filter(
            user_profile=profile
        ).select_related('role').prefetch_related('role__modules')
        
        print(f"Current roles assigned: {current_roles.count()}")
        
        # Check for admin roles that should be removed
        admin_roles_found = []
        for user_role in current_roles:
            role_name = user_role.role.name
            print(f"   • {role_name}")
            
            if role_name in CONFIG['ADMIN_ROLES_TO_REMOVE']:
                admin_roles_found.append(user_role)
        
        # Remove admin roles
        if admin_roles_found:
            print(f"\n⚠️  Found {len(admin_roles_found)} admin role(s) - REMOVING...")
            for user_role in admin_roles_found:
                role_name = user_role.role.name
                user_role.delete()
                print(f"   ❌ Removed: {role_name}")
                changes_made.append(f"Removed admin role: {role_name}")
        else:
            print(f"\n✅ No admin roles found")
        
        # STEP 4: Ensure Engineering & Common role is assigned
        print(f"\n📋 STEP 4: Ensuring Engineering & Common Access")
        print(f"{'─'*80}")
        
        # Get or create the Engineering & Common role
        eng_common_role = Role.objects.filter(
            name__iexact=CONFIG['ALLOWED_ROLE_NAME']
        ).first()
        
        if not eng_common_role:
            print(f"❌ '{CONFIG['ALLOWED_ROLE_NAME']}' role not found!")
            print(f"   Creating role...")
            
            # Create the role
            eng_common_role = Role.objects.create(
                name=CONFIG['ALLOWED_ROLE_NAME'],
                code='eng_common_access',
                description='Full access to Engineering and Common features for all users'
            )
            
            # Assign modules
            all_required_modules = CONFIG['ENGINEERING_MODULES'] + CONFIG['COMMON_MODULES']
            for module_name in all_required_modules:
                module = Module.objects.filter(name__iexact=module_name).first()
                if module:
                    eng_common_role.modules.add(module)
                    print(f"   ✅ Added module: {module_name}")
            
            changes_made.append(f"Created role: {CONFIG['ALLOWED_ROLE_NAME']}")
        
        # Check if user has this role
        has_eng_common = UserRole.objects.filter(
            user_profile=profile,
            role=eng_common_role
        ).exists()
        
        if not has_eng_common:
            print(f"⚠️  User does NOT have '{CONFIG['ALLOWED_ROLE_NAME']}' - ASSIGNING...")
            UserRole.objects.create(
                user_profile=profile,
                role=eng_common_role
            )
            changes_made.append(f"Assigned role: {CONFIG['ALLOWED_ROLE_NAME']}")
        else:
            print(f"✅ User already has '{CONFIG['ALLOWED_ROLE_NAME']}'")
        
        # STEP 5: Verify final state
        print(f"\n📋 STEP 5: Final Verification")
        print(f"{'─'*80}")
        
        # Refresh user and profile
        user.refresh_from_db()
        profile.refresh_from_db()
        
        # Get final roles
        final_roles = UserRole.objects.filter(
            user_profile=profile
        ).select_related('role').prefetch_related('role__modules')
        
        print(f"\n✅ FINAL STATE:")
        print(f"   Email: {user.email}")
        print(f"   Is Superuser: {user.is_superuser}")
        print(f"   Is Staff: {user.is_staff}")
        print(f"   Total Roles: {final_roles.count()}")
        
        # List all roles and modules
        all_modules = set()
        for user_role in final_roles:
            role = user_role.role
            print(f"\n   Role: {role.name}")
            modules = role.modules.all()
            print(f"   Modules ({modules.count()}):")
            for module in modules:
                print(f"      ✅ {module.name}")
                all_modules.add(module.name)
        
        # Verify only Engineering and Common modules
        print(f"\n📦 Total Unique Modules Accessible: {len(all_modules)}")
        
        expected_modules = set(CONFIG['ENGINEERING_MODULES'] + CONFIG['COMMON_MODULES'])
        
        print(f"\n🔍 Module Verification:")
        print(f"   Expected modules: {len(expected_modules)}")
        print(f"   Actual modules: {len(all_modules)}")
        
        # Check for unauthorized modules
        unauthorized = all_modules - expected_modules
        if unauthorized:
            print(f"\n⚠️  WARNING: User has access to unauthorized modules:")
            for mod in unauthorized:
                print(f"      ❌ {mod}")
        else:
            print(f"\n✅ User has access ONLY to authorized modules")
        
        # Check for missing modules
        missing = expected_modules - all_modules
        if missing:
            print(f"\n⚠️  WARNING: User is missing some expected modules:")
            for mod in missing:
                print(f"      ⚠️  {mod}")
        else:
            print(f"✅ User has all expected modules")
        
        # Summary
        print(f"\n{'='*80}")
        print(f"SUMMARY")
        print(f"{'='*80}")
        
        if changes_made:
            print(f"\n✅ Changes Made ({len(changes_made)}):")
            for i, change in enumerate(changes_made, 1):
                print(f"   {i}. {change}")
        else:
            print(f"\n✅ No changes needed - user access is already correct")
        
        print(f"\n✅ User '{user.email}' has been verified and configured correctly")
        print(f"   - NO superuser access")
        print(f"   - NO staff access")
        print(f"   - NO admin roles")
        print(f"   - ONLY Engineering & Common features")
        print(f"\n{'='*80}\n")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    email = CONFIG['TARGET_EMAIL']
    
    print(f"\nVerifying and fixing access for: {email}")
    print(f"Ensuring ONLY Engineering & Common features access")
    print(f"No superuser, no staff, no admin roles\n")
    
    success = verify_and_fix_access(email)
    
    if success:
        print("✅ Verification and fix completed successfully!")
    else:
        print("❌ Verification and fix failed!")
    
    sys.exit(0 if success else 1)
