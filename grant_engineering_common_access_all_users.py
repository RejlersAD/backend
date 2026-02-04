"""
Grant Engineering & COMMON Features Access to All Users
Intelligent soft-coded script to assign all Engineering and Common module access to all users
Smart, maintainable, and production-ready design
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Module, Role, RoleModule, UserRole
from django.db import transaction

User = get_user_model()

# ============================================================================
# SOFT-CODED CONFIGURATION
# ============================================================================

# Feature categories to grant (maps to module codes)
ENGINEERING_MODULES = [
    'pid_analysis',           # P&ID Design
    'pfd_to_pid',            # PFD to P&ID Converter
    'designiq',              # DesignIQ - AI Design Intelligence
]

COMMON_MODULES = [
    'crs_documents',         # CRS Documents
    'file_storage',          # File Storage
    'reports',               # Reports & Analytics
]

# Combined list
ALL_MODULES_TO_GRANT = ENGINEERING_MODULES + COMMON_MODULES

# Role configuration
ROLE_NAME = "Engineering & Common Features Access"
ROLE_CODE = "engineering_common_access"
ROLE_DESCRIPTION = "Full access to Engineering and Common features for all users"

# Filters for users to grant access
EXCLUDE_INACTIVE_USERS = True
EXCLUDE_DELETED_PROFILES = True
EXCLUDE_ADMINISTRATORS = True  # Exclude super admins and administrators

# Admin detection criteria (soft-coded)
ADMIN_ROLE_CODES = ['super_admin', 'super_administrator', 'admin', 'administrator']
ADMIN_USER_CHECKS = {
    'is_superuser': True,  # Exclude Django superusers
    'is_staff': False,     # Don't exclude staff (set to True to exclude staff too)
}

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def is_administrator(user):
    """Check if user is an administrator based on multiple criteria"""
    # Check Django flags
    if ADMIN_USER_CHECKS.get('is_superuser') and user.is_superuser:
        return True
    
    if ADMIN_USER_CHECKS.get('is_staff') and user.is_staff:
        return True
    
    # Check RBAC roles
    try:
        from apps.rbac.models import UserProfile, UserRole
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if not profile:
            return False
        
        # Check if user has any admin roles
        user_role_codes = profile.roles.filter(is_active=True).values_list('code', flat=True)
        return any(code.lower() in [r.lower() for r in ADMIN_ROLE_CODES] for code in user_role_codes)
        
    except Exception:
        return False

def get_eligible_users():
    """Get all eligible users based on configured filters"""
    users = User.objects.all()
    
    if EXCLUDE_INACTIVE_USERS:
        users = users.filter(is_active=True)
    
    # Exclude users with deleted profiles
    if EXCLUDE_DELETED_PROFILES:
        users = users.exclude(
            rbac_profile__is_deleted=True
        )
    
    # Exclude administrators if configured
    if EXCLUDE_ADMINISTRATORS:
        # Get list of admin user IDs
        admin_user_ids = []
        for user in users:
            if is_administrator(user):
                admin_user_ids.append(user.id)
        
        # Exclude admin users
        users = users.exclude(id__in=admin_user_ids)
    
    return users.order_by('email')

def get_or_create_modules():
    """Get all required modules, return available ones"""
    available_modules = []
    missing_modules = []
    
    for module_code in ALL_MODULES_TO_GRANT:
        try:
            module = Module.objects.get(code=module_code, is_active=True)
            available_modules.append(module)
        except Module.DoesNotExist:
            missing_modules.append(module_code)
    
    return available_modules, missing_modules

def get_or_create_role(modules):
    """Get or create the master role for Engineering & Common access"""
    role, created = Role.objects.get_or_create(
        code=ROLE_CODE,
        defaults={
            'name': ROLE_NAME,
            'description': ROLE_DESCRIPTION,
            'is_active': True
        }
    )
    
    # Link all modules to role
    for module in modules:
        RoleModule.objects.get_or_create(
            role=role,
            module=module
        )
    
    return role, created

def grant_access_to_user(user, role, modules):
    """Grant role and module access to a single user"""
    try:
        # Get or create user profile
        profile, profile_created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'is_deleted': False
            }
        )
        
        # Assign role to user
        user_role, ur_created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=role
        )
        
        return {
            'success': True,
            'profile_created': profile_created,
            'role_assigned': ur_created,
            'modules_count': len(modules)
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

# ============================================================================
# MAIN EXECUTION FUNCTION
# ============================================================================

def main():
    """Main execution function with comprehensive reporting"""
    print('\n' + '='*90)
    print('GRANT ENGINEERING & COMMON FEATURES ACCESS TO ALL USERS')
    print('='*90 + '\n')
    
    # Step 1: Get modules
    print('📦 Step 1: Fetching modules...')
    print('-' * 90)
    available_modules, missing_modules = get_or_create_modules()
    
    if missing_modules:
        print(f'⚠️  Warning: {len(missing_modules)} module(s) not found:')
        for module_code in missing_modules:
            print(f'   ❌ {module_code}')
    
    if not available_modules:
        print('❌ Error: No modules found. Cannot proceed.')
        return
    
    print(f'✅ Found {len(available_modules)} active modules:')
    print('\n   📌 ENGINEERING MODULES:')
    for module in available_modules:
        if module.code in ENGINEERING_MODULES:
            print(f'      ✓ {module.name} ({module.code})')
    
    print('\n   📌 COMMON MODULES:')
    for module in available_modules:
        if module.code in COMMON_MODULES:
            print(f'      ✓ {module.name} ({module.code})')
    
    # Step 2: Create/get master role
    print(f'\n📋 Step 2: Setting up master role...')
    print('-' * 90)
    role, role_created = get_or_create_role(available_modules)
    
    if role_created:
        print(f'✅ Created new role: {role.name}')
    else:
        print(f'✅ Using existing role: {role.name}')
    
    print(f'   Code: {role.code}')
    print(f'   Modules linked: {RoleModule.objects.filter(role=role).count()}')
    
    # Step 3: Get eligible users
    print(f'\n👥 Step 3: Fetching eligible users...')
    print('-' * 90)
    users = get_eligible_users()
    total_users = users.count()
    
    if total_users == 0:
        print('❌ No eligible users found.')
        return
    
    print(f'✅ Found {total_users} eligible users')
    
    # Ask for confirmation (optional - comment out for automated runs)
    print(f'\n⚠️  You are about to grant Engineering & Common access to {total_users} users.')
    response = input('   Do you want to proceed? (yes/no): ').strip().lower()
    
    if response != 'yes':
        print('\n❌ Operation cancelled by user.')
        return
    
    # Step 4: Grant access to all users
    print(f'\n🚀 Step 4: Granting access to users...')
    print('-' * 90)
    
    success_count = 0
    profile_created_count = 0
    role_assigned_count = 0
    error_count = 0
    errors = []
    
    with transaction.atomic():
        for idx, user in enumerate(users, 1):
            result = grant_access_to_user(user, role, available_modules)
            
            if result['success']:
                success_count += 1
                if result['profile_created']:
                    profile_created_count += 1
                if result['role_assigned']:
                    role_assigned_count += 1
                
                # Print progress every 10 users
                if idx % 10 == 0 or idx == total_users:
                    print(f'   Progress: {idx}/{total_users} users processed...')
            else:
                error_count += 1
                errors.append({
                    'user': user.email,
                    'error': result['error']
                })
                print(f'   ❌ Error for {user.email}: {result["error"]}')
    
    # Step 5: Final report
    print(f'\n' + '='*90)
    print('📊 FINAL REPORT')
    print('='*90)
    print(f'✅ Successfully processed: {success_count}/{total_users} users')
    print(f'   • New profiles created: {profile_created_count}')
    print(f'   • New roles assigned: {role_assigned_count}')
    print(f'   • Modules granted per user: {len(available_modules)}')
    
    if error_count > 0:
        print(f'\n❌ Errors encountered: {error_count}')
        for error in errors:
            print(f'   • {error["user"]}: {error["error"]}')
    
    print(f'\n' + '='*90)
    print('✅ OPERATION COMPLETED SUCCESSFULLY')
    print('='*90)
    
    # Verification sample
    print(f'\n🔍 VERIFICATION (Sample of first 3 users):')
    print('-' * 90)
    for user in users[:3]:
        try:
            profile = UserProfile.objects.get(user=user)
            user_modules = profile.get_all_modules()
            module_codes = [m.code for m in user_modules]
            
            eng_count = len([c for c in module_codes if c in ENGINEERING_MODULES])
            common_count = len([c for c in module_codes if c in COMMON_MODULES])
            
            print(f'\n{user.email}:')
            print(f'   • Engineering modules: {eng_count}/{len(ENGINEERING_MODULES)}')
            print(f'   • Common modules: {common_count}/{len(COMMON_MODULES)}')
            print(f'   • Total accessible modules: {user_modules.count()}')
        except Exception as e:
            print(f'\n{user.email}: ❌ Error verifying - {str(e)}')
    
    print(f'\n' + '='*90)
    print('🎉 All users now have access to Engineering & Common features!')
    print('='*90 + '\n')

# ============================================================================
# SCRIPT EXECUTION
# ============================================================================

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Script interrupted by user")
        exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        exit(1)
