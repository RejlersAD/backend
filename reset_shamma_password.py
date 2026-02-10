"""
Reset password for shamma.alkaabi@rejlers.ae
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

email = "shamma.alkaabi@rejlers.ae"  # Lowercase as it exists in DB
new_password = "Sh@6633172"

print("\n" + "="*80)
print(f"RESETTING PASSWORD FOR: {email}")
print("="*80)

try:
    user = User.objects.get(email__iexact=email)
    
    print(f"\n✅ User found")
    print(f"   ID: {user.id}")
    print(f"   Email: {user.email}")
    print(f"   Username: {user.username}")
    
    # Set the new password
    user.set_password(new_password)
    user.is_active = True
    user.must_reset_password = False  # Don't force reset since we're setting their desired password
    user.is_first_login = False
    user.temp_password_created_at = timezone.now()
    user.last_password_change = timezone.now()
    user.save()
    
    print(f"\n✅ Password reset successfully!")
    print(f"\n{'='*80}")
    print(f"LOGIN CREDENTIALS")
    print(f"{'='*80}")
    print(f"   Email: {user.email}")
    print(f"   Password: {new_password}")
    print(f"   Login URL: https://www.radai.ae/login")
    print(f"{'='*80}")
    print(f"\n⚠️  NOTE: User should login with LOWERCASE email:")
    print(f"   {user.email} (NOT Shamma.Alkaabi@rejlers.ae)")
    print(f"\n   We will fix the authentication to be case-insensitive.")
    print(f"{'='*80}\n")
    
except User.DoesNotExist:
    print(f"\n❌ User not found with email: {email}")
    print(f"   Please create the user first.\n")
except Exception as e:
    import traceback
    print(f"\n❌ ERROR: {str(e)}")
    traceback.print_exc()
    print()
