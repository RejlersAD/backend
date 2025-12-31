"""Check filtering logic for users endpoint"""
import os
import sys
import django

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.rbac.models import UserProfile

# Check the logged-in user
logged_in_email = "tanzeem.agra@rejlers.ae"
logged_in_user = User.objects.get(email=logged_in_email)
logged_in_profile = UserProfile.objects.get(user=logged_in_user)

print("=" * 80)
print("LOGGED IN USER INFO")
print("=" * 80)
print(f"Email: {logged_in_email}")
print(f"Organization: {logged_in_profile.organization.name if logged_in_profile.organization else 'None'}")
print(f"Organization ID: {logged_in_profile.organization.id if logged_in_profile.organization else 'None'}")

# Check if super admin
is_super_admin = logged_in_profile.roles.filter(code='super_admin', is_active=True).exists()
print(f"Is Super Admin: {is_super_admin}")

print("\n" + "=" * 80)
print("USERS THAT SHOULD BE VISIBLE")
print("=" * 80)

if is_super_admin:
    print("Super admin sees ALL users")
    queryset = UserProfile.objects.filter(is_deleted=False)
else:
    print(f"Non-super-admin sees only users in: {logged_in_profile.organization.name}")
    queryset = UserProfile.objects.filter(
        is_deleted=False,
        organization=logged_in_profile.organization
    )

print(f"\nTotal profiles to show: {queryset.count()}\n")

for profile in queryset.select_related('user'):
    print(f"✓ {profile.user.email}")
    print(f"  Organization: {profile.organization.name if profile.organization else 'None'}")
    print(f"  User Active: {profile.user.is_active}")
    print(f"  Profile Status: {profile.status}")
    print(f"  Is Deleted: {profile.is_deleted}")
    print("-" * 80)

print("\n" + "=" * 80)
print("NEWLY CREATED USER CHECK")
print("=" * 80)

try:
    new_user = User.objects.get(email="xerxez.in@gmail.com")
    new_profile = UserProfile.objects.get(user=new_user)
    
    print(f"Email: {new_user.email}")
    print(f"Organization: {new_profile.organization.name if new_profile.organization else 'None'}")
    print(f"Organization ID: {new_profile.organization.id if new_profile.organization else 'None'}")
    print(f"Is Deleted: {new_profile.is_deleted}")
    print(f"User Active: {new_user.is_active}")
    print(f"Profile Status: {new_profile.status}")
    
    if not is_super_admin:
        if new_profile.organization != logged_in_profile.organization:
            print("\n⚠️  PROBLEM: User is in different organization!")
            print(f"Logged-in user org: {logged_in_profile.organization.name} (ID: {logged_in_profile.organization.id})")
            print(f"New user org: {new_profile.organization.name} (ID: {new_profile.organization.id})")
        else:
            print("\n✅ User is in same organization - should be visible")
    
except User.DoesNotExist:
    print("User xerxez.in@gmail.com not found!")
