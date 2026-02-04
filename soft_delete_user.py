"""
Soft Delete User Account
Marks user as deleted without removing from database
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

EMAIL_TO_DELETE = 'muhammad.ilyas@rejlers.ae'

print('\n' + '='*80)
print('SOFT DELETE USER ACCOUNT')
print('='*80 + '\n')

try:
    user = User.objects.get(email=EMAIL_TO_DELETE)
    
    print(f'📧 Email: {user.email}')
    print(f'👤 Name: {user.first_name} {user.last_name}')
    print(f'🔑 Active: {user.is_active}')
    
    # Deactivate user
    user.is_active = False
    user.save()
    print(f'\n✅ User deactivated (is_active = False)')
    
    # Mark profile as deleted if exists
    try:
        profile = UserProfile.objects.get(user=user)
        profile.is_deleted = True
        profile.save()
        print(f'✅ Profile marked as deleted')
    except UserProfile.DoesNotExist:
        print(f'ℹ️  No profile found')
    
    print(f'\n✅ User {EMAIL_TO_DELETE} has been soft-deleted successfully!')
    print(f'   - Cannot login anymore')
    print(f'   - Not visible in active users list')
    print(f'   - Data preserved in database')
        
except User.DoesNotExist:
    print(f'❌ User not found: {EMAIL_TO_DELETE}')
except Exception as e:
    print(f'❌ Error: {str(e)}')
    import traceback
    traceback.print_exc()

print('\n' + '='*80 + '\n')
