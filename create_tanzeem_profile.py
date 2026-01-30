"""
Create UserProfile for tanzeem.agra@rejlers.ae
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Module, Organization

User = get_user_model()

try:
    user = User.objects.get(email='tanzeem.agra@rejlers.ae')
    
    # Check if profile already exists
    profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
    
    if profile:
        print(f'✅ Profile already exists for {user.email}')
        roles = list(profile.roles.values_list('name', flat=True))
        print(f'   Roles: {roles if roles else "No roles assigned"}')
        print(f'   Department: {profile.department}')
        print(f'   Job Title: {profile.job_title}')
        print(f'   Organization: {profile.organization.name if profile.organization else "None"}')
        print(f'   Modules: {profile.modules.count()}')
    else:
        # Get or create default organization
        org, _ = Organization.objects.get_or_create(
            name='Rejlers',
            defaults={
                'description': 'Default Organization',
                'is_active': True
            }
        )
        
        # Create profile
        profile = UserProfile.objects.create(
            user=user,
            organization=org,
            department='IT',
            job_title='System Administrator',
            phone='+971501234567',
            status='active'
        )
        
        # Assign all modules (super admin)
        all_modules = Module.objects.all()
        profile.modules.set(all_modules)
        
        print(f'\n✅ Created UserProfile for {user.email}')
        print(f'   Organization: {org.name}')
        print(f'   Department: {profile.department}')
        print(f'   Job Title: {profile.job_title}')
        print(f'   Modules assigned: {profile.modules.count()}')
        
except User.DoesNotExist:
    print('❌ User not found!')
