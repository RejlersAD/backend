#!/usr/bin/env python
"""
Auto-Fix RBAC Issues for All Users
Creates profiles and assigns default roles where needed
"""
import sys
import os
import django

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction
from apps.rbac.models import Organization, Role, UserProfile, UserRole

User = get_user_model()

print("\n" + "="*80)
print("🔧 AUTO-FIX RBAC ISSUES")
print("="*80 + "\n")

# Get default organization
default_org = Organization.objects.filter(is_active=True).first()
if not default_org:
    print("❌ No active organization found! Creating default...")
    default_org = Organization.objects.create(
        name='Default Organization',
        code='DEFAULT',
        is_active=True
    )
    print(f"✅ Created: {default_org.name}")

# Get default role
default_role = Role.objects.filter(code='user', is_active=True).first()
if not default_role:
    print("⚠️  No default 'user' role found. Looking for alternative...")
    default_role = Role.objects.filter(is_active=True).order_by('level').first()

if not default_role:
    print("❌ No active roles found! Please create roles first.")
    sys.exit(1)

print(f"✅ Using default role: {default_role.name}")
print(f"✅ Using default organization: {default_org.name}\n")

stats = {
    'profiles_created': 0,
    'roles_assigned': 0,
    'users_fixed': 0,
    'errors': 0
}

# Find users without profiles
users_without_profiles = []
active_users = User.objects.filter(is_active=True)

for user in active_users:
    try:
        profile = user.rbac_profile
    except UserProfile.DoesNotExist:
        users_without_profiles.append(user)

print(f"📊 Users without profiles: {len(users_without_profiles)}")

# Find users with profiles but no roles
users_without_roles = []
for user in active_users:
    try:
        profile = user.rbac_profile
        user_roles = UserRole.objects.filter(user_profile=profile)
        if not user_roles.exists():
            users_without_roles.append((user, profile))
    except UserProfile.DoesNotExist:
        pass

print(f"📊 Users without roles: {len(users_without_roles)}\n")

# Fix users without profiles
if users_without_profiles:
    print("🔧 Creating profiles...")
    for user in users_without_profiles:
        try:
            with transaction.atomic():
                profile = UserProfile.objects.create(
                    user=user,
                    organization=default_org,
                    status='active' if user.is_active else 'inactive'
                )
                
                # Assign default role
                UserRole.objects.create(
                    user_profile=profile,
                    role=default_role,
                    assigned_by=None,
                    is_primary=True
                )
                
                stats['profiles_created'] += 1
                stats['roles_assigned'] += 1
                stats['users_fixed'] += 1
                
                print(f"   ✅ {user.email}: Profile created + Role assigned")
        
        except Exception as e:
            stats['errors'] += 1
            print(f"   ❌ {user.email}: Error - {str(e)}")

# Fix users without roles
if users_without_roles:
    print("\n🔧 Assigning default roles...")
    for user, profile in users_without_roles:
        try:
            with transaction.atomic():
                UserRole.objects.create(
                    user_profile=profile,
                    role=default_role,
                    assigned_by=None,
                    is_primary=True
                )
                
                stats['roles_assigned'] += 1
                stats['users_fixed'] += 1
                
                print(f"   ✅ {user.email}: Role assigned")
        
        except Exception as e:
            stats['errors'] += 1
            print(f"   ❌ {user.email}: Error - {str(e)}")

# Summary
print("\n" + "="*80)
print("📊 FIX SUMMARY")
print("="*80)
print(f"✅ Profiles created: {stats['profiles_created']}")
print(f"✅ Roles assigned: {stats['roles_assigned']}")
print(f"✅ Users fixed: {stats['users_fixed']}")
print(f"❌ Errors: {stats['errors']}")
print("="*80 + "\n")

if stats['errors'] == 0 and stats['users_fixed'] > 0:
    print("✅ All issues fixed successfully!")
elif stats['users_fixed'] == 0:
    print("ℹ️  No issues found to fix.")
else:
    print(f"⚠️  Fixed {stats['users_fixed']} users with {stats['errors']} errors.")

print("\n💡 Next step: Run verify_all_users_rbac.py to confirm all fixes\n")
