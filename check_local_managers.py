"""
Check if RadAI managers exist in local database
"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Organization

User = get_user_model()

print("\n" + "="*80)
print("CHECKING LOCAL DATABASE FOR RADAI MANAGERS")
print("="*80 + "\n")

emails = [
    'rafat.sm.saqer@rejlers.ae',
    'anam.abbas@rejlers.ae',
    'aleksi.murtomaki@rejlers.ae'
]

for email in emails:
    print(f"Checking: {email}")
    
    user_exists = User.objects.filter(email=email).exists()
    print(f"  User exists: {user_exists}")
    
    if user_exists:
        user = User.objects.get(email=email)
        print(f"  Active: {user.is_active}")
        
        try:
            profile = UserProfile.objects.get(user=user)
            print(f"  Profile exists: True")
            print(f"  Department: {profile.department}")
            print(f"  Job Title: {profile.job_title}")
            print(f"  Status: {profile.status}")
            print(f"  Deleted: {profile.is_deleted}")
            print(f"  Organization: {profile.organization.name if profile.organization else 'None'}")
            
            # Check if it will appear in dropdown
            will_show = (
                user.is_active and
                profile.status == 'active' and
                not profile.is_deleted and
                profile.organization_id is not None
            )
            print(f"  Will show in dropdown: {'✅ YES' if will_show else '❌ NO'}")
            
        except UserProfile.DoesNotExist:
            print(f"  Profile exists: False")
    
    print()

# Check organization
print("-" * 80)
org_count = Organization.objects.filter(is_active=True).count()
print(f"Active organizations in local DB: {org_count}")

if org_count > 0:
    org = Organization.objects.filter(is_active=True).first()
    print(f"First organization: {org.name} (ID: {org.id})")
else:
    print("⚠️  WARNING: No active organization found!")
    print("   Managers need an organization to appear in the dropdown")

print("\n" + "="*80)
