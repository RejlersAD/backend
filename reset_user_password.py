#!/usr/bin/env python
"""
Reset password for a specific user
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

def reset_password(email, new_password):
    """Reset password for a user"""
    try:
        # Try case-insensitive lookup
        user = User.objects.filter(email__iexact=email).first()
        
        if not user:
            print(f"❌ User not found: {email}")
            return False
        
        print(f"\n{'='*70}")
        print(f"USER PASSWORD RESET")
        print(f"{'='*70}")
        print(f"✅ User found: {user.email}")
        print(f"   Is active: {user.is_active}")
        print(f"   Is staff: {user.is_staff}")
        print(f"   Is superuser: {user.is_superuser}")
        
        # Set the new password
        user.set_password(new_password)
        user.save()
        
        print(f"\n✅ Password successfully reset for {user.email}")
        print(f"   New password: {new_password}")
        
        # Verify the password was set correctly
        if user.check_password(new_password):
            print(f"✅ Password verification: SUCCESS")
        else:
            print(f"❌ Password verification: FAILED")
            return False
        
        print(f"{'='*70}\n")
        return True
        
    except Exception as e:
        print(f"❌ Error resetting password: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    # Reset password for muhammad.ilyas@rejlers.ae
    email = "muhammad.ilyas@rejlers.ae"
    new_password = "Rejlers@123"
    
    print(f"\nResetting password for: {email}")
    print(f"New password will be: {new_password}")
    print()
    
    success = reset_password(email, new_password)
    
    if success:
        print("✅ Password reset completed successfully!")
        print(f"\nUser can now login with:")
        print(f"   Email: {email}")
        print(f"   Password: {new_password}")
    else:
        print("❌ Password reset failed!")
    
    sys.exit(0 if success else 1)
