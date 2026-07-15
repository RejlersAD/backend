"""Check if RadAI managers exist in PRODUCTION database"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Organization

User = get_user_model()

MANAGERS_TO_CHECK = [
    'rafat.sm.saqer@rejlers.ae',
    'anam.abbas@rejlers.ae', 
    'aleksi.murtomaki@rejlers.ae'
]

print("\n" + "="*80)
print("PRODUCTION DATABASE - RADAI MANAGERS CHECK")
print("="*80)

# Check database info
from django.conf import settings
db_settings = settings.DATABASES['default']
print(f"\n📍 Database: {db_settings.get('NAME', 'N/A')}")
print(f"   Host: {db_settings.get('HOST', 'N/A')}")

# Check organizations
print("\n🏢 Organizations:")
for org in Organization.objects.filter(is_active=True):
    print(f"   ✅ {org.name} (ID: {org.id})")

print("\n" + "="*80)
print("CHECKING RADAI MANAGERS")
print("="*80)

found_count = 0
missing_count = 0

for email in MANAGERS_TO_CHECK:
    print(f"\n📧 {email}")
    
    try:
        user = User.objects.get(email=email)
        print(f"   ✅ User exists")
        print(f"      - Active: {user.is_active}")
        print(f"      - Name: {user.get_full_name()}")
        
        try:
            profile = user.rbac_profile
            print(f"   ✅ Profile exists")
            print(f"      - Organization: {profile.organization.name if profile.organization else 'NONE'}")
            print(f"      - Department: {profile.department}")
            print(f"      - Job Title: {profile.job_title}")
            print(f"      - Status: {profile.status}")
            print(f"      - Deleted: {profile.is_deleted}")
            
            if (profile.organization and 
                profile.department == 'radai' and 
                profile.status == 'active' and 
                not profile.is_deleted and
                user.is_active):
                print(f"   ✅ VALID - Will appear in dropdown")
                found_count += 1
            else:
                print(f"   ⚠️  EXISTS but may not appear (check conditions above)")
                missing_count += 1
                
        except UserProfile.DoesNotExist:
            print(f"   ❌ Profile MISSING")
            missing_count += 1
            
    except User.DoesNotExist:
        print(f"   ❌ User does NOT exist")
        missing_count += 1

print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"✅ Valid managers found: {found_count}/3")
print(f"❌ Missing/Invalid: {missing_count}/3")

if found_count == 3:
    print("\n🎉 ALL MANAGERS ARE READY!")
    print("   They should appear in https://www.radai.ae/profile dropdown")
elif found_count == 0:
    print("\n⚠️  NO MANAGERS FOUND IN PRODUCTION!")
    print("   Run: railway run python manage.py create_radai_managers")
else:
    print(f"\n⚠️  ONLY {found_count} managers found - {3-found_count} missing")
    print("   Run: railway run python manage.py create_radai_managers")

print("="*80 + "\n")
