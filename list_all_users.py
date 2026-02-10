"""
List all users in the database
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

print("\n" + "="*80)
print("ALL USERS IN DATABASE")
print("="*80 + "\n")

users = User.objects.all().order_by('-date_joined')
print(f"Total users: {users.count()}\n")

for user in users:
    print(f"ID {user.id}: {user.email}")
    print(f"   Username: {user.username}")
    print(f"   is_active: {user.is_active}")
    print(f"   is_staff: {user.is_staff}")
    print(f"   is_superuser: {user.is_superuser}")
    
    # Check RBAC profile
    try:
        profile = user.rbac_profile
        print(f"   RBAC Status: {profile.status} | is_deleted: {profile.is_deleted}")
        print(f"   Organization: {profile.organization.name}")
        print(f"   Roles: {', '.join([r.name for r in profile.roles.all()]) or 'None'}")
    except UserProfile.DoesNotExist:
        print(f"   ⚠️  No RBAC profile")
    
    print()

print("="*80 + "\n")
