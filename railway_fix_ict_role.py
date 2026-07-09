#!/usr/bin/env python
"""
Direct copy-paste script for Railway Backend Shell
Removes super_admin role and assigns admin role to ICT users
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rad_ai.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from django.core.cache import cache

User = get_user_model()

# ICT Department Configuration (Soft-Coded)
ICT_USERS = ['radai@rejlers.ae']
ICT_ROLE = 'admin'  # Level 2 - Limited admin access
ICT_DEPARTMENT = 'ICT'

print("\n" + "="*70)
print("🔒 ICT ADMIN ROLE FIX - PRODUCTION DATABASE")
print("="*70 + "\n")

for email in ICT_USERS:
    try:
        print(f"📧 Processing: {email}")
        
        # Get user and profile
        user = User.objects.get(email=email)
        profile = UserProfile.objects.get(user=user, is_deleted=False)
        
        # Show current state
        print(f"\n📋 BEFORE:")
        current_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
        for ur in current_roles:
            print(f"  • {ur.role.name} ({ur.role.code}) - Level {ur.role.level}")
        print(f"  • is_staff: {user.is_staff}")
        print(f"  • is_superuser: {user.is_superuser}")
        print(f"  • Department: {profile.department or 'None'}")
        
        # Get roles
        super_admin_role = Role.objects.get(code='super_admin')
        admin_role = Role.objects.get(code=ICT_ROLE)
        
        # Remove super_admin role
        removed = UserRole.objects.filter(user_profile=profile, role=super_admin_role).delete()
        if removed[0] > 0:
            print(f"\n✅ Removed super_admin role")
        else:
            print(f"\n⚠️  User did not have super_admin role")
        
        # Assign admin role
        ur, created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=admin_role,
            defaults={
                'is_primary': True,
                'granted_by': user,
            }
        )
        if created:
            print(f"✅ Assigned admin role")
        else:
            print(f"⚠️  User already had admin role")
        
        # Update User flags
        changes = []
        if user.is_superuser:
            user.is_superuser = False
            changes.append("is_superuser=False")
        if not user.is_staff:
            user.is_staff = True
            changes.append("is_staff=True")
        
        if changes:
            user.save()
            print(f"✅ Updated User flags: {', '.join(changes)}")
        
        # Update department
        if profile.department != ICT_DEPARTMENT:
            old_dept = profile.department or 'None'
            profile.department = ICT_DEPARTMENT
            profile.save()
            print(f"✅ Updated department: {old_dept} → {ICT_DEPARTMENT}")
        
        # Clear cache
        cache.delete(f'user_modules_{profile.id}')
        cache.delete(f'user_permissions_{profile.id}')
        print(f"✅ Cleared module/permission cache")
        
        # Show final state
        print(f"\n📋 AFTER:")
        final_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
        for ur in final_roles:
            print(f"  • {ur.role.name} ({ur.role.code}) - Level {ur.role.level}")
        print(f"  • is_staff: {user.is_staff}")
        print(f"  • is_superuser: {user.is_superuser}")
        print(f"  • Department: {profile.department}")
        
        # Show modules
        modules = profile.get_all_modules()
        print(f"\n📦 Accessible Modules ({modules.count()}):")
        admin_module_codes = ['admin_dashboard', 'user_mgmt', 'role_access_mgmt', 
                              'wrench_integration', 'ai_champion', 'enquiry_management']
        for m in modules[:20]:  # Limit to first 20
            is_admin = "✅ ADMIN" if m.code in admin_module_codes else ""
            print(f"  • {m.code} {is_admin}")
        
        print(f"\n{'='*70}")
        print(f"✅ SUCCESS: {email} role updated to admin (level 2)")
        print(f"{'='*70}\n")
        
    except User.DoesNotExist:
        print(f"❌ User not found: {email}\n")
    except Role.DoesNotExist as e:
        print(f"❌ Role not found: {e}\n")
    except Exception as e:
        print(f"❌ Error processing {email}: {e}\n")
        import traceback
        traceback.print_exc()

print("\n" + "="*70)
print("⚠️  USER ACTION REQUIRED:")
print("="*70)
print("User must LOG OUT and CLEAR BROWSER CACHE to see changes!")
print("1. Log out from https://www.radai.ae")
print("2. Clear browser cache (Ctrl+Shift+Delete)")
print("3. Log back in")
print("="*70 + "\n")
