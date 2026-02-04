"""Quick script to check user login credentials"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from apps.rbac.models import UserProfile

User = get_user_model()

email = 'muhammad.ilyas@rejlers.ae'
password = 'Rejlers@123'

print('\n' + '='*70)
print('USER LOGIN VERIFICATION')
print('='*70)

# Check user
user = User.objects.filter(email__iexact=email).first()

if not user:
    print(f'❌ User not found: {email}')
else:
    print(f'\n✅ User found!')
    print(f'   Email in DB: {user.email}')
    print(f'   Is active: {user.is_active}')
    print(f'   Is staff: {user.is_staff}')
    print(f'   Is superuser: {user.is_superuser}')
    print(f'   Has usable password: {user.has_usable_password()}')
    
    # Check password
    password_correct = check_password(password, user.password)
    print(f'\n   Password check ("{password}"): {password_correct}')
    
    # Check profile
    try:
        profile = UserProfile.objects.get(user=user)
        print(f'\n   Profile found: Yes')
        print(f'   Must change password: {profile.must_change_password}')
        print(f'   Profile is deleted: {profile.is_deleted}')
    except UserProfile.DoesNotExist:
        print(f'\n   ❌ Profile not found')
    
    # Email case sensitivity check
    print(f'\n📧 Email Case Sensitivity:')
    print(f'   Login attempt: Muhammad.Ilyas@rejlers.ae')
    print(f'   Database value: {user.email}')
    print(f'   Match: {user.email == email}')

print('\n' + '='*70)
