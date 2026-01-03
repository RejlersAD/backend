import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.rbac.models import UserProfile

print("=" * 70)
print("DATABASE CONNECTION CHECK")
print("=" * 70)

try:
    # Check Users table
    user_count = User.objects.count()
    print(f"\n✓ Total Users in Database: {user_count}")
    
    # Check UserProfiles table
    profile_count = UserProfile.objects.count()
    print(f"✓ Total UserProfiles in Database: {profile_count}")
    
    if user_count > 0:
        print("\n" + "=" * 70)
        print("SAMPLE USERS (First 5):")
        print("=" * 70)
        users = User.objects.all()[:5]
        for idx, user in enumerate(users, 1):
            print(f"\n{idx}. Email: {user.email}")
            print(f"   Name: {user.first_name} {user.last_name}")
            print(f"   Username: {user.username}")
            print(f"   Active: {user.is_active}")
            print(f"   Staff: {user.is_staff}")
            print(f"   Superuser: {user.is_superuser}")
            
            # Check if profile exists
            try:
                profile = UserProfile.objects.get(user=user)
                print(f"   Profile: ✓ EXISTS (ID: {profile.id})")
                print(f"   Organization: {profile.organization.name if profile.organization else 'None'}")
            except UserProfile.DoesNotExist:
                print(f"   Profile: ✗ MISSING")
    else:
        print("\n⚠ WARNING: No users found in database!")
        print("   You may need to create a superuser or run migrations")
        
    print("\n" + "=" * 70)
    print("DATABASE CONNECTION: SUCCESSFUL")
    print("=" * 70)
    
except Exception as e:
    print(f"\n✗ ERROR connecting to database:")
    print(f"   {str(e)}")
    print("\n" + "=" * 70)
    print("DATABASE CONNECTION: FAILED")
    print("=" * 70)
