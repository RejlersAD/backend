#!/usr/bin/env python
"""
Grant Admin Access to User
Soft-coded configuration for granting admin/staff access to users
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role

# ============================================
# CONFIGURATION (Soft-coded)
# ============================================
USER_EMAIL = "shareeq@rejlers.ae"
GRANT_DJANGO_STAFF = True  # Make user Django staff
GRANT_DJANGO_SUPERUSER = False  # Make user Django superuser
GRANT_ADMIN_ROLE = True  # Assign admin role via RBAC

User = get_user_model()

def main():
    print("="*70)
    print("GRANTING ADMIN ACCESS")
    print("="*70)
    print(f"\n📧 Target User: {USER_EMAIL}")
    print(f"🔧 Configuration:")
    print(f"   - Django Staff Access: {GRANT_DJANGO_STAFF}")
    print(f"   - Django Superuser Access: {GRANT_DJANGO_SUPERUSER}")
    print(f"   - RBAC Admin Role: {GRANT_ADMIN_ROLE}")
    print()
    
    try:
        # Find user
        user = User.objects.get(email=USER_EMAIL)
        print(f"✅ User found: {user.username} ({user.email})")
        print(f"\n📋 Current Status:")
        print(f"   - Is Staff: {user.is_staff}")
        print(f"   - Is Superuser: {user.is_superuser}")
        
        # Grant Django permissions
        changes_made = False
        
        if GRANT_DJANGO_STAFF and not user.is_staff:
            user.is_staff = True
            changes_made = True
            print(f"\n✅ Granted Django Staff access")
        
        if GRANT_DJANGO_SUPERUSER and not user.is_superuser:
            user.is_superuser = True
            changes_made = True
            print(f"\n✅ Granted Django Superuser access")
        
        if changes_made:
            user.save()
            print(f"💾 User permissions saved to database")
        
        # Grant RBAC Admin Role
        if GRANT_ADMIN_ROLE:
            try:
                profile = UserProfile.objects.get(user=user)
                print(f"\n✅ UserProfile found: {profile.id}")
                
                # Look for admin role
                admin_role = None
                try:
                    admin_role = Role.objects.get(code='admin')
                    print(f"✅ Admin role found: {admin_role.name}")
                except Role.DoesNotExist:
                    # Try super_admin
                    try:
                        admin_role = Role.objects.get(code='super_admin')
                        print(f"✅ Super Admin role found: {admin_role.name}")
                    except Role.DoesNotExist:
                        print(f"⚠️ No admin or super_admin role found in database")
                
                if admin_role:
                    # Check if user already has this role
                    if profile.roles.filter(id=admin_role.id).exists():
                        print(f"ℹ️ User already has {admin_role.name} role")
                    else:
                        profile.roles.add(admin_role)
                        print(f"✅ Assigned {admin_role.name} role to user")
                    
                    # Set as primary role (check if attribute exists)
                    if hasattr(profile, 'primary_role'):
                        if profile.primary_role != admin_role:
                            profile.primary_role = admin_role
                            profile.save()
                            print(f"✅ Set {admin_role.name} as primary role")
                    else:
                        print(f"ℹ️ primary_role attribute not available in UserProfile model")
                
            except UserProfile.DoesNotExist:
                print(f"❌ UserProfile not found for user")
        
        print(f"\n{'='*70}")
        print("✅ ADMIN ACCESS GRANTED SUCCESSFULLY!")
        print(f"{'='*70}")
        print(f"\n👤 User: {user.email}")
        print(f"   - Django Staff: {user.is_staff}")
        print(f"   - Django Superuser: {user.is_superuser}")
        
        try:
            profile = UserProfile.objects.get(user=user)
            roles_list = ', '.join([r.name for r in profile.roles.all()])
            print(f"   - RBAC Roles: {roles_list if roles_list else 'None'}")
            if hasattr(profile, 'primary_role') and profile.primary_role:
                print(f"   - Primary Role: {profile.primary_role.name}")
        except:
            pass
        
        print(f"\n🎯 User can now access: http://localhost:3000/admin/users")
        
    except User.DoesNotExist:
        print(f"❌ User not found: {USER_EMAIL}")
        return
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
