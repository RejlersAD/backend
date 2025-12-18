#!/usr/bin/env python
"""
Create admin user: tanzeem.agra@rejlers.ae
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

# Delete existing user if exists
if User.objects.filter(email='tanzeem.agra@rejlers.ae').exists():
    User.objects.filter(email='tanzeem.agra@rejlers.ae').delete()
    print('🗑️  Deleted existing user')

# Create superuser
user = User.objects.create_superuser(
    username='tanzeem.agra',
    email='tanzeem.agra@rejlers.ae',
    password='Tanzeem@123'
)

print('✅ Superuser created successfully!')
print(f'   Email: {user.email}')
print(f'   Username: {user.username}')
print(f'   Is Superuser: {user.is_superuser}')
print(f'   Is Staff: {user.is_staff}')
print(f'   Is Active: {user.is_active}')
print('')
print('🔑 Login Credentials:')
print('   Email: tanzeem.agra@rejlers.ae')
print('   Password: Tanzeem@123')
