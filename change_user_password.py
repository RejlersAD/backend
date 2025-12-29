"""
Change user password script
Usage: python change_user_password.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

# Configuration - Soft coded
USER_EMAIL = "shareeq@rejlers.ae"
NEW_PASSWORD = "Shareeq@123"

User = get_user_model()

print("=" * 70)
print("PASSWORD CHANGE UTILITY")
print("=" * 70)

try:
    # Get user
    user = User.objects.get(email=USER_EMAIL)
    print(f"\n✅ User found: {user.email} ({user.username})")
    
    # Change password
    user.set_password(NEW_PASSWORD)
    user.save()
    
    print(f"\n✅ Password changed successfully!")
    print(f"   New Password: {NEW_PASSWORD}")
    
    # Verify the password change
    from django.contrib.auth import authenticate
    auth_user = authenticate(email=USER_EMAIL, password=NEW_PASSWORD)
    
    if auth_user:
        print(f"\n✅ Password verification: SUCCESS")
        print(f"   User can now login with: {USER_EMAIL} / {NEW_PASSWORD}")
    else:
        print(f"\n❌ Password verification: FAILED")
        print(f"   Something went wrong!")
    
except User.DoesNotExist:
    print(f"\n❌ User '{USER_EMAIL}' not found in database")
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
