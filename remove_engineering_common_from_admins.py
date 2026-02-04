"""
Remove Engineering & Common Features Access from Administrators
Smart script to revoke the role from admin users only
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from django.db import transaction

User = get_user_model()

# ============================================================================
# CONFIGURATION
# ============================================================================

ROLE_CODE_TO_REMOVE = "engineering_common_access"

# Admin detection criteria
ADMIN_ROLE_CODES = ['super_admin', 'super_administrator', 'admin', 'administrator']

# ============================================================================
# FUNCTIONS
# ============================================================================

def is_administrator(user):
    """Check if user is an administrator"""
    # Check Django superuser flag
    if user.is_superuser:
        return True
    
    # Check RBAC roles
    try:
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if not profile:
            return False
        
        # Check if user has any admin roles
        user_role_codes = profile.roles.filter(is_active=True).values_list('code', flat=True)
        return any(code.lower() in [r.lower() for r in ADMIN_ROLE_CODES] for code in user_role_codes)
        
    except Exception:
        return False

def main():
    print('\n' + '='*90)
    print('REMOVE ENGINEERING & COMMON ACCESS FROM ADMINISTRATORS')
    print('='*90 + '\n')
    
    # Get the role
    try:
        role = Role.objects.get(code=ROLE_CODE_TO_REMOVE)
        print(f'✅ Found role: {role.name} ({role.code})\n')
    except Role.DoesNotExist:
        print(f'❌ Role not found: {ROLE_CODE_TO_REMOVE}')
        return
    
    # Get all users with this role
    user_roles = UserRole.objects.filter(role=role).select_related('user_profile__user')
    total_users = user_roles.count()
    
    print(f'📊 Total users with this role: {total_users}\n')
    
    # Identify administrators
    print('🔍 Identifying administrators...')
    print('-' * 90)
    
    admin_users = []
    non_admin_users = []
    
    for user_role in user_roles:
        user = user_role.user_profile.user
        if is_administrator(user):
            admin_users.append((user, user_role))
            print(f'   👑 ADMIN: {user.email} (superuser={user.is_superuser})')
        else:
            non_admin_users.append(user)
    
    print(f'\n✅ Found {len(admin_users)} administrators')
    print(f'✅ Found {len(non_admin_users)} regular users\n')
    
    if len(admin_users) == 0:
        print('✅ No administrators found with this role. Nothing to remove.')
        return
    
    # Confirm removal
    print(f'⚠️  You are about to remove this role from {len(admin_users)} administrator(s).')
    response = input('   Do you want to proceed? (yes/no): ').strip().lower()
    
    if response != 'yes':
        print('\n❌ Operation cancelled.')
        return
    
    # Remove role from administrators
    print(f'\n🗑️  Removing role from administrators...')
    print('-' * 90)
    
    removed_count = 0
    errors = []
    
    with transaction.atomic():
        for user, user_role in admin_users:
            try:
                user_role.delete()
                removed_count += 1
                print(f'   ✅ Removed from: {user.email}')
            except Exception as e:
                errors.append({'user': user.email, 'error': str(e)})
                print(f'   ❌ Error for {user.email}: {e}')
    
    # Final report
    print(f'\n' + '='*90)
    print('📊 FINAL REPORT')
    print('='*90)
    print(f'✅ Successfully removed: {removed_count}/{len(admin_users)} administrators')
    print(f'✅ Regular users still have access: {len(non_admin_users)}')
    
    if errors:
        print(f'\n❌ Errors: {len(errors)}')
        for error in errors:
            print(f'   • {error["user"]}: {error["error"]}')
    
    print(f'\n' + '='*90)
    print('✅ OPERATION COMPLETED')
    print('='*90)
    
    # Verification
    print(f'\n🔍 VERIFICATION:')
    print('-' * 90)
    remaining = UserRole.objects.filter(role=role).count()
    print(f'Total users with role after removal: {remaining}')
    print(f'Expected: {len(non_admin_users)} (regular users only)')
    
    if remaining == len(non_admin_users):
        print('\n✅ SUCCESS: Only regular users have Engineering & Common access now!\n')
    else:
        print(f'\n⚠️  Warning: Count mismatch. Please verify manually.\n')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Script interrupted")
        exit(1)
    except Exception as e:
        print(f"\n\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        exit(1)
