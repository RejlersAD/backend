#!/usr/bin/env python
"""Quick diagnostic to check inactive users in production"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, UserRole

User = get_user_model()

print("\n" + "=" * 80)
print("  USER STATISTICS")
print("=" * 80)
print(f"Total users: {User.objects.count()}")
print(f"Active users: {User.objects.filter(is_active=True).count()}")
print(f"Inactive users: {User.objects.filter(is_active=False).count()}")
print(f"Users with is_superuser=True: {User.objects.filter(is_superuser=True).count()}")
print(f"Users with is_staff=True: {User.objects.filter(is_staff=True).count()}")
print()

inactive_super = User.objects.filter(is_active=False, is_superuser=True).count()
inactive_staff = User.objects.filter(is_active=False, is_staff=True).count()
print(f"Inactive with is_superuser=True: {inactive_super}")
print(f"Inactive with is_staff=True: {inactive_staff}")
print()

# Protected admins
PROTECTED = ['mohammed.agra@rejlers.ae', 'fahad.hussein@rejlers.ae', 'tanzeem.agra@rejlers.ae']
ADMIN_ROLES = ['super_admin', 'admin', 'ict_admin']

print("Inactive users with super/staff flags (excluding protected admins):")
inactive_flagged = User.objects.filter(
    is_active=False
).filter(
    is_superuser=True
).exclude(
    email__in=PROTECTED
) | User.objects.filter(
    is_active=False
).filter(
    is_staff=True
).exclude(
    email__in=PROTECTED
)

print(f"Total: {inactive_flagged.count()}")
if inactive_flagged.count() > 0:
    print("\nSample (first 20):")
    for user in inactive_flagged[:20]:
        try:
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            roles = UserRole.objects.filter(
                user_profile=profile,
                role__is_active=True
            ).select_related('role')
            role_codes = [ur.role.code for ur in roles]
            has_admin = any(c in ADMIN_ROLES for c in role_codes)
            
            flags = f"super={user.is_superuser}, staff={user.is_staff}"
            print(f"  - {user.email} ({flags}, roles={', '.join(role_codes)}, admin={has_admin})")
        except Exception as e:
            print(f"  - {user.email} (ERROR: {e})")

print("=" * 80)
