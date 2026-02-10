"""
Create Shamma.Alkaabi@rejlers.ae user account with proper RBAC
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Organization, Role, UserRole
from django.utils import timezone
import logging

User = get_user_model()
logger = logging.getLogger(__name__)

# User details
email = "Shamma.Alkaabi@rejlers.ae"
password = "Sh@6633172"
first_name = "Shamma"
last_name = "Alkaabi"
username = "Shamma.Alkaabi"

print("\n" + "="*80)
print(f"CREATING USER: {email}")
print("="*80)

try:
    # Check if user already exists
    existing_user = User.objects.filter(email=email).first()
    if existing_user:
        print(f"\n⚠️  User with email {email} already exists!")
        print(f"   User ID: {existing_user.id}")
        print(f"   is_active: {existing_user.is_active}")
        
        # Check if profile exists and if it's deleted
        try:
            profile = existing_user.rbac_profile
            if profile.is_deleted:
                print(f"\n🔄 Profile is soft-deleted. Restoring...")
                profile.is_deleted = False
                profile.deleted_at = None
                profile.status = 'active'
                profile.save()
                print(f"   ✅ Profile restored successfully")
            else:
                print(f"   ✅ Profile already exists and is active")
        except UserProfile.DoesNotExist:
            print(f"\n⚠️  User exists but has no RBAC profile. Creating one...")
            # Create RBAC profile for existing user
            org = Organization.objects.filter(name__icontains='default').first()
            if not org:
                org = Organization.objects.first()
            
            profile = UserProfile.objects.create(
                user=existing_user,
                organization=org,
                status='active',
                is_deleted=False
            )
            
            # Assign default role
            default_role = Role.objects.filter(
                name__icontains='Engineering'
            ).first()
            
            if default_role:
                UserRole.objects.create(
                    user_profile=profile,
                    role=default_role,
                    is_primary=True
                )
                print(f"   ✅ RBAC profile created with role: {default_role.name}")
        
        # Make sure user is active
        if not existing_user.is_active:
            existing_user.is_active = True
            existing_user.save()
            print(f"   ✅ User activated")
        
        # Update password
        existing_user.set_password(password)
        existing_user.must_reset_password = True
        existing_user.is_first_login = True
        existing_user.temp_password_created_at = timezone.now()
        existing_user.save()
        print(f"   ✅ Password updated")
        
        print(f"\n{'='*80}")
        print(f"✅ USER READY TO LOGIN")
        print(f"{'='*80}")
        print(f"   Email: {email}")
        print(f"   Password: {password}")
        print(f"   Login URL: https://www.radai.ae/login")
        print(f"{'='*80}\n")
        
    else:
        # Create new user
        print(f"\n📝 Creating new user...")
        
        # Get default organization
        org = Organization.objects.filter(name__icontains='default').first()
        if not org:
            org = Organization.objects.first()
        
        if not org:
            print(f"❌ ERROR: No organization found. Please create an organization first.")
            exit(1)
        
        print(f"   Organization: {org.name}")
        
        # Create User
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            is_active=True,
            is_staff=False,
            is_superuser=False,
            is_first_login=True,
            must_reset_password=True,
            temp_password_created_at=timezone.now()
        )
        print(f"   ✅ User created: ID {user.id}")
        
        # Create RBAC Profile
        profile = UserProfile.objects.create(
            user=user,
            organization=org,
            status='active',
            is_deleted=False,
            must_change_password=True
        )
        print(f"   ✅ RBAC Profile created: ID {profile.id}")
        
        # Assign default role (Engineering & Common Features Access)
        default_role = Role.objects.filter(
            name__icontains='Engineering'
        ).first()
        
        if not default_role:
            # If no engineering role, get any active role
            default_role = Role.objects.filter(is_active=True).first()
        
        if default_role:
            UserRole.objects.create(
                user_profile=profile,
                role=default_role,
                is_primary=True,
                assigned_by=None  # System assignment
            )
            print(f"   ✅ Role assigned: {default_role.name}")
        else:
            print(f"   ⚠️  No roles found. User created without role.")
        
        print(f"\n{'='*80}")
        print(f"✅ USER CREATED SUCCESSFULLY")
        print(f"{'='*80}")
        print(f"   Email: {email}")
        print(f"   Password: {password}")
        print(f"   Organization: {org.name}")
        print(f"   Role: {default_role.name if default_role else 'None'}")
        print(f"   Login URL: https://www.radai.ae/login")
        print(f"{'='*80}\n")

except Exception as e:
    import traceback
    print(f"\n❌ ERROR: Failed to create user")
    print(f"   {str(e)}")
    print(f"\nFull traceback:")
    traceback.print_exc()
    print(f"\n{'='*80}\n")
