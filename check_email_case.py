"""
Check both case variations of Shamma.Alkaabi email
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model, authenticate
from apps.rbac.models import UserProfile

User = get_user_model()

emails_to_check = [
    "Shamma.Alkaabi@rejlers.ae",  # Mixed case (what user is trying)
    "shamma.alkaabi@rejlers.ae",  # Lowercase
    "SHAMMA.ALKAABI@REJLERS.AE"   # Uppercase
]

print("\n" + "="*80)
print("CHECKING EMAIL VARIATIONS")
print("="*80)

for email in emails_to_check:
    print(f"\n🔍 Checking: {email}")
    
    # Case-sensitive search
    user_exact = User.objects.filter(email=email).first()
    print(f"   Exact match: {'✅ Found' if user_exact else '❌ Not found'}")
    
    # Case-insensitive search
    user_iexact = User.objects.filter(email__iexact=email).first()
    print(f"   Case-insensitive: {'✅ Found' if user_iexact else '❌ Not found'}")
    
    if user_iexact:
        print(f"   User ID: {user_iexact.id}")
        print(f"   Actual email in DB: {user_iexact.email}")
        print(f"   is_active: {user_iexact.is_active}")
        
        # Try authentication
        print(f"\n   Testing authentication with password 'Sh@6633172'...")
        auth_user = authenticate(username=email, password="Sh@6633172")
        if auth_user:
            print(f"   ✅ Authentication SUCCESSFUL")
        else:
            print(f"   ❌ Authentication FAILED - incorrect password or auth backend issue")

print("\n" + "="*80)
print("DIAGNOSIS")
print("="*80)
print("""
The user exists with lowercase email 'shamma.alkaabi@rejlers.ae'
but login is attempted with mixed case 'Shamma.Alkaabi@rejlers.ae'.

The authentication backend needs to handle case-insensitive email lookup.
""")
print("="*80 + "\n")
