"""
Check tanzeem.agra@rejlers.ae user and profile
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

try:
    user = User.objects.get(email='tanzeem.agra@rejlers.ae')
    
    print('USER DATA:')
    print('=' * 60)
    print(f'Email: {user.email}')
    print(f'Username: {user.username}')
    print(f'First Name: {user.first_name}')
    print(f'Last Name: {user.last_name}')
    print(f'Is Staff: {user.is_staff}')
    print(f'Is Superuser: {user.is_superuser}')
    print(f'Is Active: {user.is_active}')
    print(f'Date Joined: {user.date_joined}')
    print(f'Last Login: {user.last_login}')
    
    print('\nPROFILE DATA:')
    print('=' * 60)
    
    try:
        profile = user.userprofile
        print('✅ Profile exists')
        print(f'Role: {profile.role}')
        print(f'Department: {profile.department}')
        print(f'Job Title: {profile.job_title}')
        print(f'Phone: {profile.phone}')
        print(f'Profile Photo: {profile.profile_photo.url if profile.profile_photo else "None"}')
        print(f'Modules: {list(profile.modules.values_list("name", flat=True))}')
    except UserProfile.DoesNotExist:
        print('❌ No profile found for this user!')
        print('Creating profile...')
        profile = UserProfile.objects.create(
            user=user,
            role='ADMIN',
            department='IT',
            job_title='System Administrator'
        )
        print('✅ Profile created')
        
except User.DoesNotExist:
    print('❌ User not found!')
