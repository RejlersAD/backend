"""
Direct database user creation script for Railway
Run this with: railway run python create_admin_now.py
"""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

def create_admin():
    User = get_user_model()
    
    email = 'tanzeem.agra@rejlers.ae'
    username = 'tanzeem'
    password = 'Tanzeem@123'
    
    # Check if user exists
    if User.objects.filter(email=email).exists():
        user = User.objects.get(email=email)
        # Update password to ensure it's correct
        user.set_password(password)
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.save()
        print(f'✓ Updated existing user: {email}')
    else:
        # Create new superuser
        user = User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
            first_name='Tanzeem',
            last_name='Agra'
        )
        print(f'✓ Created new superuser: {email}')
    
    print(f'✓ User is active: {user.is_active}')
    print(f'✓ User is staff: {user.is_staff}')
    print(f'✓ User is superuser: {user.is_superuser}')
    print(f'✓ Password is set correctly')
    
    # Verify the password works
    from django.contrib.auth import authenticate
    auth_user = authenticate(email=email, password=password)
    if auth_user:
        print(f'✓ Authentication test PASSED')
    else:
        print(f'✗ Authentication test FAILED')
    
    return user

if __name__ == '__main__':
    print('Creating/updating admin user in database...')
    create_admin()
    print('Done!')
