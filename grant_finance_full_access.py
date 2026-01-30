"""
Grant Full Finance Module Access
Soft-coded script to assign complete Finance permissions to specified users
Intelligent, maintainable, and reusable design
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Module, Permission, Role, RoleModule, RolePermission, UserRole
from django.db import transaction

User = get_user_model()

# ============================================================================
# SOFT-CODED CONFIGURATION - Easy to modify without touching core logic
# ============================================================================

# User list for Finance access grant
USERS_TO_GRANT = [
    'aleksi.murtomaki@rejlers.ae',
    'aneef.thadikkarantavida@rejlers.ae',
    'bibi.varghese@rejlers.ae'
]

# Module configuration
MODULE_CODE = 'finance'
MODULE_DISPLAY_NAME = 'Finance'

# Role naming templates (dynamic and personalized)
ROLE_NAME_TEMPLATE = '{module} Full Access - {username}'
ROLE_CODE_TEMPLATE = '{module_code}_full_{username_slug}'
ROLE_DESCRIPTION_TEMPLATE = 'Complete {module} module permissions for {email}'

# Permission scope (all or specific)
GRANT_ALL_PERMISSIONS = True  # Set to False to grant specific permissions only
SPECIFIC_PERMISSIONS = []  # Add permission codes here if GRANT_ALL_PERMISSIONS = False

# ============================================================================
# INTELLIGENT HELPER FUNCTIONS
# ============================================================================

def get_username_slug(email):
    """Extract clean username from email for role naming"""
    return email.split('@')[0].replace('.', '_')

def format_role_config(email, module_code, module_name):
    """Generate role configuration dynamically"""
    username = email.split('@')[0]
    username_slug = get_username_slug(email)
    
    return {
        'code': ROLE_CODE_TEMPLATE.format(
            module_code=module_code,
            username_slug=username_slug
        ),
        'name': ROLE_NAME_TEMPLATE.format(
            module=module_name,
            username=username
        ),
        'description': ROLE_DESCRIPTION_TEMPLATE.format(
            module=module_name,
            email=email
        )
    }

# ============================================================================
# MAIN ACCESS GRANT FUNCTION
# ============================================================================

def grant_finance_access(email):
    """
    Grant full Finance module access to a user
    Uses soft-coded configuration and intelligent error handling
    """
    try:
        print(f"\n{'='*80}")
        print(f"💼 GRANTING FINANCE ACCESS TO: {email}")
        print(f"{'='*80}\n")
        
        # Step 1: Validate and get user
        try:
            user = User.objects.get(email=email)
            print(f"✅ Found user: {user.first_name} {user.last_name} ({user.email})")
        except User.DoesNotExist:
            print(f"❌ User not found: {email}")
            print(f"   Please ensure the user account exists in the system")
            return False
        
        # Step 2: Get or create user profile
        profile, profile_created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'is_deleted': False
            }
        )
        if profile_created:
            print(f"✅ Created new RBAC profile for {email}")
        else:
            print(f"✅ Found existing RBAC profile for {email}")
        
        # Step 3: Get Finance module
        try:
            finance_module = Module.objects.get(code=MODULE_CODE, is_active=True)
            print(f"✅ Found module: {finance_module.name} (code: {finance_module.code})")
        except Module.DoesNotExist:
            print(f"❌ {MODULE_DISPLAY_NAME} module not found with code '{MODULE_CODE}'")
            print(f"   Please ensure the Finance module is registered in the system")
            return False
        
        # Step 4: Generate role configuration (soft-coded)
        role_config = format_role_config(email, MODULE_CODE, finance_module.name)
        
        # Step 5: Get or create role
        role, role_created = Role.objects.get_or_create(
            code=role_config['code'],
            defaults={
                'name': role_config['name'],
                'description': role_config['description'],
                'is_active': True
            }
        )
        if role_created:
            print(f"✅ Created new role: {role.name}")
        else:
            print(f"✅ Using existing role: {role.name}")
        
        # Step 6: Link module to role
        role_module, rm_created = RoleModule.objects.get_or_create(
            role=role,
            module=finance_module
        )
        if rm_created:
            print(f"✅ Linked {MODULE_DISPLAY_NAME} module to role")
        else:
            print(f"ℹ️  {MODULE_DISPLAY_NAME} module already linked to role")
        
        # Step 7: Get permissions (soft-coded permission lookup)
        if GRANT_ALL_PERMISSIONS:
            permissions = Permission.objects.filter(
                module=finance_module,
                is_active=True
            )
            print(f"✅ Found {permissions.count()} active permissions for {MODULE_DISPLAY_NAME}")
        else:
            permissions = Permission.objects.filter(
                module=finance_module,
                code__in=SPECIFIC_PERMISSIONS,
                is_active=True
            )
            print(f"✅ Found {permissions.count()} specific permissions for {MODULE_DISPLAY_NAME}")
        
        if not permissions.exists():
            print(f"⚠️  No permissions found for {MODULE_DISPLAY_NAME} module")
            return False
        
        # Step 8: Assign permissions to role (intelligent batch creation)
        permissions_assigned = 0
        permissions_skipped = 0
        
        for permission in permissions:
            role_perm, rp_created = RolePermission.objects.get_or_create(
                role=role,
                permission=permission
            )
            if rp_created:
                permissions_assigned += 1
            else:
                permissions_skipped += 1
        
        print(f"✅ Assigned {permissions_assigned} new permissions to role")
        if permissions_skipped > 0:
            print(f"ℹ️  Skipped {permissions_skipped} already assigned permissions")
        
        # Step 9: Assign role to user (final step)
        user_role, ur_created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=role
        )
        
        if ur_created:
            print(f"✅ Assigned role to user")
        else:
            print(f"ℹ️  User already has this role")
        
        # Step 10: Summary report
        print(f"\n{'='*80}")
        print(f"✅ SUCCESS: Finance access granted to {email}")
        print(f"{'='*80}")
        print(f"   Role: {role.name}")
        print(f"   Permissions: {permissions.count()}")
        print(f"   Status: Active")
        print(f"{'='*80}\n")
        
        return True
        
    except Exception as e:
        print(f"\n{'='*80}")
        print(f"❌ ERROR: Failed to grant access to {email}")
        print(f"{'='*80}")
        print(f"   Error: {str(e)}")
        print(f"{'='*80}\n")
        return False

# ============================================================================
# BATCH PROCESSING FUNCTION
# ============================================================================

def grant_access_to_all_users():
    """
    Process all users in the USERS_TO_GRANT list
    Provides comprehensive reporting
    """
    print(f"\n{'#'*80}")
    print(f"#  FINANCE MODULE ACCESS GRANT SCRIPT")
    print(f"#  Soft-coded configuration for easy maintenance")
    print(f"{'#'*80}\n")
    
    print(f"📋 Configuration:")
    print(f"   Module: {MODULE_DISPLAY_NAME} ({MODULE_CODE})")
    print(f"   Users to process: {len(USERS_TO_GRANT)}")
    print(f"   Permission scope: {'All permissions' if GRANT_ALL_PERMISSIONS else f'{len(SPECIFIC_PERMISSIONS)} specific permissions'}")
    print(f"\n{'='*80}\n")
    
    success_count = 0
    failure_count = 0
    
    with transaction.atomic():
        for email in USERS_TO_GRANT:
            if grant_finance_access(email):
                success_count += 1
            else:
                failure_count += 1
    
    # Final summary
    print(f"\n{'#'*80}")
    print(f"#  EXECUTION SUMMARY")
    print(f"{'#'*80}")
    print(f"   Total users processed: {len(USERS_TO_GRANT)}")
    print(f"   ✅ Successful: {success_count}")
    print(f"   ❌ Failed: {failure_count}")
    print(f"{'#'*80}\n")
    
    return success_count, failure_count

# ============================================================================
# SCRIPT EXECUTION
# ============================================================================

if __name__ == '__main__':
    try:
        success, failures = grant_access_to_all_users()
        exit(0 if failures == 0 else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Script interrupted by user")
        exit(1)
    except Exception as e:
        print(f"\n\n❌ Fatal error: {str(e)}")
        exit(1)
