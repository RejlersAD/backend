"""
Production Script: Add Missing Department and Reporting Managers
================================================================
Run this in Railway backend shell to add RadAI department and missing managers.

Usage in Railway Shell:
    exec(open('railway_add_radai_department_managers.py').read())
"""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rad_ai.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

print("\n" + "="*80)
print("🔧 ADD RADAI DEPARTMENT & MISSING MANAGERS")
print("="*80 + "\n")

# ============================================================================
# SOFT-CODED: Missing Managers
# ============================================================================
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
    print("📊 CURRENT STATUS CHECK")
    print("-" * 80)
    
    # Check existing managers
    existing_count = 0
    missing_count = 0
    
    for mgr in MISSING_MANAGERS:
        email = mgr['email']
        if User.objects.filter(email=email).exists():
            user = User.objects.get(email=email)
            print(f"  ✅ EXISTS: {mgr['first_name']} {mgr['last_name']} ({email})")
            existing_count += 1
            
            # Check department
            try:
                profile = UserProfile.objects.get(user=user)
                if profile.department != 'radai':
                    print(f"     ⚠️  Current department: {profile.department} (will update to 'radai')")
            except UserProfile.DoesNotExist:
                print(f"     ⚠️  No profile found (will create)")
        else:
            print(f"  ❌ MISSING: {mgr['first_name']} {mgr['last_name']} ({email})")
            missing_count += 1
    
    print(f"\n📈 Summary: {existing_count} exist, {missing_count} missing\n")
    
    # ========================================================================
    # STEP 1: Create/Update Users
    # ========================================================================
    print("👤 STEP 1: CREATE/UPDATE USERS")
    print("-" * 80)
    
    created_count = 0
    updated_count = 0
    
    for mgr in MISSING_MANAGERS:
        email = mgr['email']
        first_name = mgr['first_name']
        last_name = mgr['last_name']
        department = mgr['department']
        job_title = mgr['job_title']
        
        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={
                'username': email.split('@')[0],
                'first_name': first_name,
                'last_name': last_name,
                'is_active': True,
                'is_staff': False,
            }
        )
        
        if user_created:
            print(f"  ✅ Created user: {first_name} {last_name} ({email})")
            created_count += 1
        else:
            # Update names if changed
            if user.first_name != first_name or user.last_name != last_name:
                user.first_name = first_name
                user.last_name = last_name
                user.save()
                print(f"  🔄 Updated user: {first_name} {last_name} ({email})")
                updated_count += 1
            else:
                print(f"  ✅ User exists: {first_name} {last_name} ({email})")
        
        # Create/Update UserProfile with RadAI department
        profile, profile_created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'department': department,
                'job_title': job_title,
                'is_deleted': False,
            }
        )
        
        if profile_created:
            print(f"     ✅ Created profile with department: {department}")
        else:
            # Update department and job title
            if profile.department != department or profile.job_title != job_title:
                profile.department = department
                profile.job_title = job_title
                profile.save()
                print(f"     🔄 Updated profile: department={department}, job_title={job_title}")
            else:
                print(f"     ✅ Profile exists: department={department}")
    
    print(f"\n✅ Users: {created_count} created, {updated_count} updated")
    
    # ========================================================================
    # STEP 2: Verify RadAI Department in Constants
    # ========================================================================
    print(f"\n📋 STEP 2: VERIFY RADAI DEPARTMENT")
    print("-" * 80)
    
    from apps.rbac.constants import DEPARTMENTS, get_department_label
    
    has_radai = any(dept[0] == 'radai' for dept in DEPARTMENTS)
    
    if has_radai:
        print(f"  ✅ 'RadAI' department exists in backend constants")
    else:
        print(f"  ⚠️  'RadAI' department NOT found in backend constants")
        print(f"     Action required: Add ('radai', 'RadAI') to DEPARTMENTS in backend/apps/rbac/constants.py")
    
    # ========================================================================
    # STEP 3: Verification
    # ========================================================================
    print(f"\n🔍 STEP 3: FINAL VERIFICATION")
    print("-" * 80)
    
    print("\nManagers in Profile dropdown:")
    for mgr in MISSING_MANAGERS:
        try:
            user = User.objects.get(email=mgr['email'])
            profile = UserProfile.objects.get(user=user)
            print(f"  ✅ {profile.full_name or user.get_full_name()} - {user.email}")
            print(f"     Department: {profile.department}")
            print(f"     Job Title: {profile.job_title}")
        except Exception as e:
            print(f"  ❌ {mgr['email']}: {str(e)}")
    
    # ========================================================================
    # SUMMARY
    # ========================================================================
    print(f"\n" + "="*80)
    print("✅ COMPLETION SUMMARY")
    print("="*80)
    print(f"Users created:       {created_count}")
    print(f"Users updated:       {updated_count}")
    print(f"Total managers:      {len(MISSING_MANAGERS)}")
    print(f"RadAI department:    {'✅ Available' if has_radai else '⚠️ Needs manual addition'}")
    print("="*80 + "\n")
    
    if not has_radai:
        print("⚠️  FRONTEND/BACKEND UPDATE REQUIRED")
        print("   1. Add ('radai', 'RadAI') to backend/apps/rbac/constants.py DEPARTMENTS")
        print("   2. Add { value: 'radai', label: 'RadAI' } to frontend/src/pages/Profile.jsx DEPARTMENTS")
        print("   3. Deploy changes to production\n")
    else:
        print("🎉 All managers are now available in the Profile dropdown!")
        print("   Users can select them as Reporting Manager in Profile page.\n")

except Exception as e:
    print(f"\n❌ ERROR: {str(e)}")
    import traceback
    traceback.print_exc()
