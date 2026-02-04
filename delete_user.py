"""
Delete User Account
Safe deletion script with confirmation
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, UserRole

User = get_user_model()

EMAIL_TO_DELETE = 'muhammad.ilyas@rejlers.ae'

print('\n' + '='*80)
print('DELETE USER ACCOUNT')
print('='*80 + '\n')

try:
    user = User.objects.get(email=EMAIL_TO_DELETE)
    
    print(f'📧 Email: {user.email}')
    print(f'👤 Name: {user.first_name} {user.last_name}')
    print(f'🔑 Active: {user.is_active}')
    print(f'👑 Superuser: {user.is_superuser}')
    
    # Check profile
    try:
        profile = UserProfile.objects.get(user=user)
        roles = profile.roles.all()
        print(f'📋 Roles: {", ".join([r.name for r in roles])}')
    except UserProfile.DoesNotExist:
        print(f'📋 Roles: No profile found')
    
    print('\n' + '='*80)
    print('⚠️  WARNING: This will permanently delete the user account!')
    print('='*80)
    
    response = input('\nType "DELETE" to confirm deletion: ').strip()
    
    if response == 'DELETE':
        # Delete user (cascade will delete profile and related data)
        user.delete()
        print(f'\n✅ User {EMAIL_TO_DELETE} has been deleted successfully!')
    else:
        print(f'\n❌ Deletion cancelled.')
        
except User.DoesNotExist:
    print(f'❌ User not found: {EMAIL_TO_DELETE}')
except Exception as e:
    print(f'❌ Error: {str(e)}')
    import traceback
    traceback.print_exc()

print('\n' + '='*80 + '\n')
