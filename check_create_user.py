#!/usr/bin/env python
"""
Script to check and create admin user for local development
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User

email = 'tanzeem.agra@rejlers.ae'
password = os.environ.get('ADMIN_PASSWORD')
if not password:
    raise SystemExit('ADMIN_PASSWORD environment variable is not set.')

try:
    user = User.objects.get(email=email)
    print(f"✅ User exists: {email}")
    print(f"   - ID: {user.id}")
    print(f"   - Is active: {user.is_active}")
    print(f"   - Is staff: {user.is_staff}")
    print(f"   - Is superuser: {user.is_superuser}")
    
    # Ensure user is active
    if not user.is_active:
        user.is_active = True
        user.save()
        print("   ✅ Activated user")
    
    # Update password
    user.set_password(password)
    user.save()
    print(f"   ✅ Password updated")
    
except User.DoesNotExist:
    print(f"❌ User does not exist: {email}")
    print(f"Creating new superuser...")
    
    user = User.objects.create_superuser(
        username=email.split('@')[0],  # Use email prefix as username
        email=email,
        password=password,
        first_name='Tanzeem',
        last_name='Agra',
        is_active=True
    )
    print(f"✅ Created superuser: {email}")
    print(f"   - ID: {user.id}")
    print(f"   - Username: {user.username}")
    print(f"   - Password: {password}")

print("\n✅ User is ready for login")
