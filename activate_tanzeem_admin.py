#!/usr/bin/env python
"""
Activate and Set Password for Tanzeem Super Admin
Soft-coded configuration for activating super admin account
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model, authenticate

# ============================================
# CONFIGURATION (Soft-coded)
# ============================================
USER_EMAIL = "tanzeem.agra@rejlers.ae"
NEW_PASSWORD = "Tanzeem@123"
ACTIVATE_ACCOUNT = True
ENSURE_SUPERUSER = True
ENSURE_STAFF = True

User = get_user_model()

def main():
    print("="*70)
    print("ACTIVATING SUPER ADMIN ACCOUNT")
    print("="*70)
    print(f"\n📧 Target User: {USER_EMAIL}")
    print(f"🔧 Configuration:")
    print(f"   - Activate Account: {ACTIVATE_ACCOUNT}")
    print(f"   - Set Password: {NEW_PASSWORD}")
    print(f"   - Ensure Superuser: {ENSURE_SUPERUSER}")
    print(f"   - Ensure Staff: {ENSURE_STAFF}")
    print()
    
    try:
        # Find user
        user = User.objects.get(email=USER_EMAIL)
        print(f"✅ User found: {user.username} ({user.email})")
        print(f"\n📋 Current Status:")
        print(f"   - Is Active: {user.is_active}")
        print(f"   - Is Staff: {user.is_staff}")
        print(f"   - Is Superuser: {user.is_superuser}")
        
        changes_made = []
        
        # Activate account
        if ACTIVATE_ACCOUNT and not user.is_active:
            user.is_active = True
            changes_made.append("✅ Account activated")
        
        # Ensure superuser status
        if ENSURE_SUPERUSER and not user.is_superuser:
            user.is_superuser = True
            changes_made.append("✅ Superuser status granted")
        
        # Ensure staff status
        if ENSURE_STAFF and not user.is_staff:
            user.is_staff = True
            changes_made.append("✅ Staff status granted")
        
        # Set password
        user.set_password(NEW_PASSWORD)
        changes_made.append(f"✅ Password set to: {NEW_PASSWORD}")
        
        # Save changes
        user.save()
        print(f"\n🔧 Changes Applied:")
        for change in changes_made:
            print(f"   {change}")
        
        print(f"\n💾 User saved to database")
        
        # Verify password works
        print(f"\n🔐 Verifying password...")
        auth_user = authenticate(username=user.username, password=NEW_PASSWORD)
        if auth_user:
            print(f"✅ Password verification: SUCCESS")
        else:
            print(f"❌ Password verification: FAILED")
        
        print(f"\n{'='*70}")
        print("✅ SUPER ADMIN ACCOUNT READY!")
        print(f"{'='*70}")
        print(f"\n👤 Login Credentials:")
        print(f"   Email: {user.email}")
        print(f"   Username: {user.username}")
        print(f"   Password: {NEW_PASSWORD}")
        print(f"\n🔑 Account Status:")
        print(f"   - Is Active: {user.is_active} ✅")
        print(f"   - Is Staff: {user.is_staff} ✅")
        print(f"   - Is Superuser: {user.is_superuser} ✅")
        print(f"\n🎯 Can now login at: http://localhost:3000/login")
        
    except User.DoesNotExist:
        print(f"❌ User not found: {USER_EMAIL}")
        print(f"\nCreating new super admin user...")
        
        user = User.objects.create_user(
            username=USER_EMAIL.split('@')[0],
            email=USER_EMAIL,
            password=NEW_PASSWORD,
            is_active=True,
            is_staff=True,
            is_superuser=True
        )
        
        print(f"✅ Super admin created successfully!")
        print(f"\n👤 Login Credentials:")
        print(f"   Email: {user.email}")
        print(f"   Username: {user.username}")
        print(f"   Password: {NEW_PASSWORD}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
