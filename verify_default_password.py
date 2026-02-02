"""
Verify Default Password Configuration
Tests that the default password has been updated to Rejlers@123
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings

print('\n' + '='*80)
print('DEFAULT PASSWORD CONFIGURATION VERIFICATION')
print('='*80)

print(f'\n✅ Default User Password: {settings.DEFAULT_USER_PASSWORD}')

print('\n📋 Configuration Sources:')
print(f'   Environment Variable: {os.getenv("DEFAULT_USER_PASSWORD", "Not Set (using default)")}')
print(f'   Settings Value: {settings.DEFAULT_USER_PASSWORD}')

expected_password = 'Rejlers@123'
if settings.DEFAULT_USER_PASSWORD == expected_password:
    print(f'\n✅ SUCCESS: Default password correctly set to "{expected_password}"')
else:
    print(f'\n❌ ERROR: Default password is "{settings.DEFAULT_USER_PASSWORD}", expected "{expected_password}"')

print('\n' + '='*80)
print('USAGE IN SYSTEM')
print('='*80)
print('\n📍 Where this password is used:')
print('   1. User Management → Reset Password to Default')
print('   2. Admin-initiated password resets')
print('   3. Bulk user creation with default passwords')
print('\n💡 Users MUST change this password on first login')
print('='*80 + '\n')
