#!/usr/bin/env python
"""
Check roles and module access for a specific user
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserRole, Role

User = get_user_model()

def check_user_roles(email):
    """Check user roles and module access"""
    try:
        # Try case-insensitive lookup
        user = User.objects.filter(email__iexact=email).first()
        
        if not user:
            print(f"❌ User not found: {email}")
            return False
        
        print(f"\n{'='*70}")
        print(f"USER ROLE VERIFICATION")
        print(f"{'='*70}")
        print(f"✅ User: {user.email}")
        print(f"   Name: {user.first_name} {user.last_name}")
        print(f"   Is active: {user.is_active}")
        print(f"   Is superuser: {user.is_superuser}")
        
        # Check RBAC profile
        try:
            profile = user.rbac_profile
            print(f"\n✅ RBAC Profile found")
            print(f"   Organization: {profile.organization.name if profile.organization else 'None'}")
            print(f"   Is deleted: {profile.is_deleted}")
        except Exception as e:
            print(f"\n❌ No RBAC profile found: {e}")
            return False
        
        # Get all roles
        user_roles = UserRole.objects.filter(
            user_profile=profile
        ).select_related('role').prefetch_related('role__modules')
        
        print(f"\n📋 Assigned Roles ({user_roles.count()}):")
        
        if not user_roles.exists():
            print("   ⚠️  No roles assigned")
        else:
            for user_role in user_roles:
                role = user_role.role
                print(f"\n   Role: {role.name}")
                print(f"   Description: {role.description}")
                
                modules = role.modules.all()
                if modules:
                    print(f"   Modules ({modules.count()}):")
                    for module in modules:
                        print(f"      - {module.name}")
                else:
                    print(f"   Modules: None")
        
        # Check specifically for Engineering and Common features
        print(f"\n{'='*70}")
        print("ENGINEERING & COMMON FEATURES CHECK")
        print(f"{'='*70}")
        
        engineering_role = Role.objects.filter(
            name__icontains="Engineering & Common Features"
        ).first()
        
        if engineering_role:
            has_role = user_roles.filter(role=engineering_role).exists()
            print(f"✅ 'Engineering & Common Features Access' role exists")
            print(f"   User has this role: {'Yes ✅' if has_role else 'No ❌'}")
            
            if engineering_role.modules.exists():
                print(f"   Role includes modules:")
                for module in engineering_role.modules.all():
                    print(f"      - {module.name}")
        else:
            print(f"❌ 'Engineering & Common Features Access' role not found")
        
        print(f"{'='*70}\n")
        return True
        
    except Exception as e:
        print(f"❌ Error checking user roles: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    email = "muhammad.ilyas@rejlers.ae"
    
    print(f"\nChecking roles for: {email}")
    
    check_user_roles(email)
