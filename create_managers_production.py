"""
Direct Production Manager Creation Script
==========================================
This script connects to Railway production database and creates the missing managers.
Runs locally using Railway CLI.
"""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

print("\n" + "="*80)
print("🔧 CREATE MISSING MANAGERS IN PRODUCTION")
print("="*80 + "\n")

MISSING_MANAGERS = [
    {
        'email': 'rafat.sm.saqer@rejlers.ae',
        'first_name': 'Rafat',
        'last_name': 'S. M. Saqer',
        'department': 'radai',
        'job_title': 'Manager',
    },
    {
        'email': 'anam.abbas@rejlers.ae',
        'first_name': 'Anam',
        'last_name': 'Abbas',
        'department': 'radai',
        'job_title': 'Manager',
    },
    {
        'email': 'aleksi.murtomaki@rejlers.ae',
        'first_name': 'Aleksi',
        'last_name': 'Murtomaki',
        'department': 'radai',
        'job_title': 'Manager',
    },
]

try:
    print("📊 STEP 1: CHECK EXISTING MANAGERS")
    print("-" * 80)
    
    existing_count = 0
    missing_count = 0
    
    for mgr in MISSING_MANAGERS:
        email = mgr['email']
        if User.objects.filter(email=email).exists():
            user = User.objects.get(email=email)
            print(f"  ✅ EXISTS: {mgr['first_name']} {mgr['last_name']} ({email})")
            existing_count += 1
            
            try:
                profile = UserProfile.objects.get(user=user)
                print(f"     Department: {profile.department}, Job Title: {profile.job_title}")
                if profile.department != 'radai':
                    print(f"     ⚠️  Will update to 'radai'")
            except UserProfile.DoesNotExist:
                print(f"     ⚠️  No profile - will create")
        else:
            print(f"  ❌ MISSING: {mgr['first_name']} {mgr['last_name']} ({email})")
            missing_count += 1
    
    print(f"\n📈 Status: {existing_count} exist, {missing_count} missing\n")
    
    if missing_count == 0 and existing_count == len(MISSING_MANAGERS):
        print("✅ All managers already exist!")
        
        # Check if all have correct department
        needs_update = False
        for mgr in MISSING_MANAGERS:
            user = User.objects.get(email=mgr['email'])
            profile = UserProfile.objects.get(user=user)
            if profile.department != 'radai' or profile.job_title != 'Manager':
                needs_update = True
                break
        
        if not needs_update:
            print("✅ All departments and job titles are correct!")
            print("\n🎉 No action needed - managers are ready!\n")
            exit(0)
    
    print("👤 STEP 2: CREATE/UPDATE USERS AND PROFILES")
    print("-" * 80)
    
    created_count = 0
    updated_count = 0
    
    for mgr in MISSING_MANAGERS:
        email = mgr['email']
        first_name = mgr['first_name']
        last_name = mgr['last_name']
        department = mgr['department']
        job_title = mgr['job_title']
        
        print(f"\n  Processing: {email}")
        
        # Create or get user
        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={
                'username': email.split('@')[0].replace('.', '_'),
                'first_name': first_name,
                'last_name': last_name,
                'is_active': True,
                'is_staff': False,
                'is_superuser': False,
            }
        )
        
        if user_created:
            print(f"    ✅ Created user: {first_name} {last_name}")
            created_count += 1
        else:
            # Update name if different
            if user.first_name != first_name or user.last_name != last_name:
                user.first_name = first_name
                user.last_name = last_name
                user.save()
                print(f"    🔄 Updated user name")
            print(f"    ✅ User exists")
        
        # Create or update profile
        profile, profile_created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'department': department,
                'job_title': job_title,
                'is_deleted': False,
            }
        )
        
        if profile_created:
            print(f"    ✅ Created profile: department={department}, job_title={job_title}")
        else:
            # Update department and job title
            changed = False
            if profile.department != department:
                profile.department = department
                changed = True
            if profile.job_title != job_title:
                profile.job_title = job_title
                changed = True
            if profile.is_deleted:
                profile.is_deleted = False
                changed = True
            
            if changed:
                profile.save()
                print(f"    🔄 Updated profile: department={department}, job_title={job_title}")
                updated_count += 1
            else:
                print(f"    ✅ Profile already correct")
    
    print(f"\n📊 STEP 3: VERIFY RADAI DEPARTMENT EXISTS")
    print("-" * 80)
    
    from apps.rbac.constants import DEPARTMENTS
    has_radai = any(dept[0] == 'radai' for dept in DEPARTMENTS)
    
    if has_radai:
        print(f"  ✅ 'RadAI' department exists in constants")
    else:
        print(f"  ⚠️  'RadAI' NOT in constants - but profiles use it")
    
    print(f"\n🔍 STEP 4: FINAL VERIFICATION")
    print("-" * 80)
    print("\nAll managers now in database:")
    
    for mgr in MISSING_MANAGERS:
        try:
            user = User.objects.get(email=mgr['email'])
            profile = UserProfile.objects.get(user=user)
            status = "✅ ACTIVE" if (user.is_active and not profile.is_deleted) else "❌ INACTIVE"
            print(f"  {status} {user.get_full_name()} - {user.email}")
            print(f"         Department: {profile.department} | Job Title: {profile.job_title}")
        except Exception as e:
            print(f"  ❌ {mgr['email']}: ERROR - {str(e)}")
    
    print(f"\n" + "="*80)
    print("✅ COMPLETION SUMMARY")
    print("="*80)
    print(f"Users created:       {created_count}")
    print(f"Users updated:       {updated_count}")
    print(f"Total managers:      {len(MISSING_MANAGERS)}")
    print(f"RadAI in constants:  {'✅ Yes' if has_radai else '⚠️ No (but works)'}")
    print("="*80 + "\n")
    
    print("🎉 SUCCESS! Managers are now available in Profile dropdown!")
    print("   Go to https://www.radai.ae/profile and check 'Reporting Manager' field\n")
    print("💡 You may need to clear browser cache (Ctrl+Shift+R) to see them.\n")

except Exception as e:
    print(f"\n❌ ERROR: {str(e)}")
    import traceback
    traceback.print_exc()
    exit(1)
