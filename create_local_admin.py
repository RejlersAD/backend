#!/usr/bin/env python
"""Create admin user for local development"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from django.contrib.auth.hashers import make_password

# Create or update tanzeem admin user
email = 'tanzeem.agra@rejlers.ae'
try:
    user = User.objects.get(email=email)
    user.password = make_password('Tanzeem@123')
    user.is_superuser = True
    user.is_staff = True
    user.is_active = True
    user.first_name = 'Tanzeem'
    user.last_name = 'Agra'
    user.save()
    print(f'✅ Updated user: {user.email}')
except User.DoesNotExist:
    user = User.objects.create(
        email=email,
        username=email,
        password=make_password('Tanzeem@123'),
        first_name='Tanzeem',
        last_name='Agra',
        is_superuser=True,
        is_staff=True,
        is_active=True
    )
    print(f'✅ Created user: {user.email}')

print(f'   - Email: {user.email}')
print(f'   - Password: Tanzeem@123')
print(f'   - Superuser: {user.is_superuser}')
print(f'   - Staff: {user.is_staff}')
print(f'   - Active: {user.is_active}')
