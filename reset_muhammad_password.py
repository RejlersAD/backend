import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from apps.rbac.models import UserProfile

User = get_user_model()

EMAIL = 'muhammad.ilyas@rejlers.ae'
NEW_PASSWORD = 'Rejlers@123'

user = User.objects.filter(email__iexact=EMAIL).first()

if user:
    print(f'\n✅ Found user: {user.email}')
    
    # Reset password
    user.password = make_password(NEW_PASSWORD)
    user.save()
    
    print(f'✅ Password reset to: {NEW_PASSWORD}')
    
    # Set must_change_password flag
    profile = UserProfile.objects.filter(user=user).first()
    if profile:
        profile.must_change_password = True
        profile.save()
        print(f'✅ Must change password flag set to: True')
    
    print(f'\n🎉 You can now login with:')
    print(f'   Email: {user.email}')
    print(f'   Password: {NEW_PASSWORD}')
    print(f'   Note: You will be required to change password after login\n')
else:
    print(f'❌ User not found: {EMAIL}')
