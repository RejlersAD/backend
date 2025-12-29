#!/usr/bin/env python
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.rbac.models import UserProfile, Module, Role, UserRole

def main():
    print("=" * 60)
    print("Adding PID Module to Shareeq")
    print("=" * 60)
    
    # Get user
    user = User.objects.get(email='shareeq@rejlers.ae')
    profile = UserProfile.objects.get(user=user)
    
    # Get the custom role for shareeq
    custom_role = Role.objects.filter(code='custom_shareeq').first()
    
    if custom_role:
        # Get PID module
        pid_module = Module.objects.get(code='PID')
        
        # Add PID to the role
        custom_role.modules.add(pid_module)
        custom_role.save()
        
        print(f"\n✅ Added PID module to role: {custom_role.name}")
    
    # Get all modules
    modules = profile.get_all_modules()
    print(f"\n📦 User Modules ({modules.count()}):")
    for module in modules:
        print(f"  ✓ {module.code}: {module.name}")
    
    print("\n" + "=" * 60)
    print("✅ Module update completed!")
    print(f"   Login: {user.email} / shareeq123")
    print("=" * 60)

if __name__ == '__main__':
    main()
