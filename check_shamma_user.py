"""
Diagnostic script to check Shamma.Alkaabi@rejlers.ae account status
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model, authenticate
from apps.rbac.models import UserProfile

User = get_user_model()

email = "Shamma.Alkaabi@rejlers.ae"

print("\n" + "="*80)
print(f"DIAGNOSTIC REPORT FOR: {email}")
print("="*80)

# Check if user exists
try:
    user = User.objects.get(email=email)
    print(f"\n✅ User exists in database")
    print(f"   - ID: {user.id}")
    print(f"   - Username: {user.username}")
    print(f"   - Email: {user.email}")
    print(f"   - is_active: {user.is_active}")
    print(f"   - is_staff: {user.is_staff}")
    print(f"   - is_superuser: {user.is_superuser}")
    print(f"   - is_verified: {getattr(user, 'is_verified', 'N/A')}")
    print(f"   - is_first_login: {getattr(user, 'is_first_login', 'N/A')}")
    print(f"   - must_reset_password: {getattr(user, 'must_reset_password', 'N/A')}")
    print(f"   - last_login: {user.last_login}")
    print(f"   - date_joined: {user.date_joined}")
    
    # Check RBAC Profile
    try:
        profile = user.rbac_profile
        print(f"\n✅ RBAC Profile exists")
        print(f"   - Status: {profile.status}")
        print(f"   - is_deleted: {profile.is_deleted}")
        print(f"   - deleted_at: {profile.deleted_at}")
        print(f"   - Organization: {profile.organization.name}")
        print(f"   - must_change_password: {profile.must_change_password}")
        print(f"   - Roles: {', '.join([r.name for r in profile.roles.all()])}")
        
        # Diagnose issues
        print("\n" + "-"*80)
        print("DIAGNOSIS:")
        print("-"*80)
        
        issues = []
        if not user.is_active:
            issues.append("❌ User.is_active is False - User account is disabled")
        
        if profile.is_deleted:
            issues.append(f"❌ Profile is soft-deleted (deleted_at: {profile.deleted_at})")
        
        if profile.status != 'active':
            issues.append(f"❌ Profile status is '{profile.status}' (should be 'active')")
        
        if profile.locked_until:
            from django.utils import timezone
            if profile.locked_until > timezone.now():
                issues.append(f"❌ Account is locked until {profile.locked_until}")
        
        if not issues:
            print("✅ No issues found with account status")
            print("\n⚠️  The problem might be:")
            print("   1. Incorrect password")
            print("   2. Authentication backend configuration issue")
            print("\n   Testing password authentication...")
            
            # Try to authenticate with provided password
            test_password = "Sh@6633172"
            auth_user = authenticate(username=email, password=test_password)
            if auth_user:
                print(f"   ✅ Password authentication successful!")
            else:
                print(f"   ❌ Password authentication failed - incorrect password")
                print(f"\n   SOLUTION: Reset password using:")
                print(f"   python backend/reset_user_password.py {email}")
        else:
            print("FOUND ISSUES:")
            for issue in issues:
                print(f"   {issue}")
            
            print("\n" + "="*80)
            print("RECOMMENDED FIXES:")
            print("="*80)
            
            if not user.is_active:
                print(f"\n1. Activate user account:")
                print(f"   user = User.objects.get(email='{email}')")
                print(f"   user.is_active = True")
                print(f"   user.save()")
            
            if profile.is_deleted:
                print(f"\n2. Restore soft-deleted profile:")
                print(f"   profile = user.rbac_profile")
                print(f"   profile.is_deleted = False")
                print(f"   profile.deleted_at = None")
                print(f"   profile.save()")
            
            if profile.status != 'active':
                print(f"\n3. Set profile status to active:")
                print(f"   profile.status = 'active'")
                print(f"   profile.save()")
        
    except UserProfile.DoesNotExist:
        print(f"\n❌ RBAC Profile does NOT exist")
        print(f"\n   SOLUTION: Create RBAC profile using Super Admin dashboard")
        print(f"   or run: python backend/create_rbac_profile.py {email}")
    
except User.DoesNotExist:
    print(f"\n❌ User does NOT exist in database")
    print(f"\n   SOLUTION: Create user account via:")
    print(f"   1. Super Admin Dashboard at https://www.radai.ae/admin/users")
    print(f"   2. Or run script: python backend/create_user.py")

print("\n" + "="*80 + "\n")
