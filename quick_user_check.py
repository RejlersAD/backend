import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from apps.rbac.models import UserProfile

User = get_user_model()

user = User.objects.filter(email__iexact='muhammad.ilyas@rejlers.ae').first()

if user:
    print(f'Email in DB: {user.email}')
    print(f'Is Active: {user.is_active}')
    print(f'Password Check (Rejlers@123): {check_password("Rejlers@123", user.password)}')
    
    profile = UserProfile.objects.filter(user=user).first()
    if profile:
        print(f'Must change password: {profile.must_change_password}')
        print(f'Profile status: {profile.status}')
    else:
        print('No profile found')
else:
    print('User not found')
