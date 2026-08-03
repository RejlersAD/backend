"""
Diagnose Leave Approval - Who can approve leaves?
"""
import os
import sys
import django

# Setup Django  
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from apps.rbac.rbac_config import HR_MANAGER_ROLE_CODES

User = get_user_model()

print("="*80)
print("LEAVE APPROVAL DIAGNOSTIC - Who Can Approve Leaves?")
print("="*80)
print()

print("1. HR_MANAGER_ROLE_CODES Configuration:")
print(f"   {HR_MANAGER_ROLE_CODES}")
print()

print("2. Users with HR Manager Roles:")
print("-"*80)
for role_code in HR_MANAGER_ROLE_CODES:
    try:
        role = Role.objects.get(code=role_code, is_active=True)
        user_roles = UserRole.objects.filter(role=role).select_related('user_profile__user')
        print(f"\n   Role: {role.name} ({role.code})")
        print(f"   Users: {user_roles.count()}")
        for ur in user_roles:
            user = ur.user_profile.user
            print(f"     • {user.email} - Primary: {ur.is_primary}")
    except Role.DoesNotExist:
        print(f"\n   Role {role_code} NOT FOUND!")

print()
print("3. Users with is_staff=True (legacy HR access):")
print("-"*80)
staff_users = User.objects.filter(is_staff=True)
for user in staff_users:
    print(f"   • {user.email}")

print()
print("4. Checking Specific Users:")
print("-"*80)

target_users = [
    'sanglin.samuel@rejlers.ae',
    'michelle.dehoedt@rejlers.ae',
]

for email in target_users:
    try:
        user = User.objects.get(email=email)
        profile = UserProfile.objects.get(user=user, is_deleted=False)
        
        print(f"\n   {email}:")
        print(f"     - is_staff: {user.is_staff}")
        print(f"     - is_superuser: {user.is_superuser}")
        print(f"     - Roles: {profile.userrole_set.count()}")
        
        for ur in profile.userrole_set.all():
            primary_str = " [PRIMARY]" if ur.is_primary else ""
            print(f"       • {ur.role.name} ({ur.role.code}){primary_str}")
        
        # Check if would pass _is_hr_manager check
        has_hr_access = False
        if user.is_superuser or user.is_staff:
            has_hr_access = True
            print(f"     - HR Access: YES (via is_staff/is_superuser)")
        else:
            for ur in profile.userrole_set.all():
                code = (ur.role.code or '').lower()
                if code in HR_MANAGER_ROLE_CODES or code.startswith('hr'):
                    has_hr_access = True
                    print(f"     - HR Access: YES (via role: {ur.role.code})")
                    break
        
        if not has_hr_access:
            print(f"     - HR Access: NO")
            
    except User.DoesNotExist:
        print(f"\n   {email}: USER NOT FOUND")
    except UserProfile.DoesNotExist:
        print(f"\n   {email}: USER PROFILE NOT FOUND")

print()
print("="*80)
print("RECOMMENDATION:")
print("="*80)
print()
print("To make sanglin.samuel@rejlers.ae the HR Manager for leave approvals:")
print()
print("  Option 1: Assign HR Manager role via RBAC")
print("    1. Navigate to http://localhost:5173/admin/users")
print("    2. Find sanglin.samuel@rejlers.ae")
print("    3. Assign role: 'HR & Payroll Administrator' (hr_admin)")
print()
print("  Option 2: Update is_staff flag (NOT RECOMMENDED)")
print("    This gives full Django admin access")
print()
print("  Option 3: Create a specific 'HR Manager' role assignment")
print("    Assign the 'hr_manager' role code")
print()
print("To REMOVE michelle.dehoedt@rejlers.ae from HR approval:")
print("  1. Remove 'hr_admin' or 'hr_manager' role")
print("  2. OR set is_staff=False (if she doesn't need admin access)")
print()
print("="*80)
