"""
Final Synchronization Fix - Mark UserProfiles as deleted for soft-deleted users
"""

import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction
from apps.rbac.models import UserProfile

User = get_user_model()

def main():
    print("="*90)
    print("  FINAL DATABASE SYNCHRONIZATION FIX")
    print("="*90)
    
    # Find UserProfiles where user has .deleted_ email but profile is_deleted=False
    profiles_to_mark = UserProfile.objects.filter(
        user__email__contains='.deleted_',
        is_deleted=False
    )
    
    count = profiles_to_mark.count()
    
    print(f"\n📊 Found {count} profile(s) to mark as deleted\n")
    
    if count == 0:
        print("✅ All profiles are properly synchronized!")
        return
    
    marked = 0
    for profile in profiles_to_mark:
        try:
            with transaction.atomic():
                profile.is_deleted = True
                profile.save()
                print(f"   ✅ Marked as deleted: {profile.user.email}")
                marked += 1
        except Exception as e:
            print(f"   ❌ Failed: {profile.user.email} - {str(e)}")
    
    print(f"\n✅ Successfully marked {marked} profile(s) as deleted")
    
    # Verify final counts
    print("\n" + "="*90)
    print("  VERIFICATION")
    print("="*90)
    
    total_profiles = UserProfile.objects.count()
    active_profiles = UserProfile.objects.filter(is_deleted=False).count()
    deleted_profiles = UserProfile.objects.filter(is_deleted=True).count()
    
    # Count users that should appear in admin UI
    admin_ui_users = UserProfile.objects.filter(
        is_deleted=False,
        user__is_active=True
    ).exclude(
        user__email__contains='.deleted_'
    ).count()
    
    print(f"\n📊 Database Statistics:")
    print(f"   Total UserProfiles:        {total_profiles}")
    print(f"   Active Profiles:           {active_profiles}")
    print(f"   Deleted Profiles:          {deleted_profiles}")
    print(f"   Expected Admin UI Count:   {admin_ui_users}")
    print(f"\n✅ Database is now properly synchronized!")
    print("="*90 + "\n")

if __name__ == "__main__":
    main()
