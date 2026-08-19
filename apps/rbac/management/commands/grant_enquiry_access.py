"""
Django Management Command: Grant Enquiry Management Access
Grant enquiry_management module to special users defined in soft-coded config

Usage:
    python manage.py grant_enquiry_access
    python manage.py grant_enquiry_access --email=specific@email.com
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.core.cache import cache
from apps.rbac.models import UserProfile, Module, UserRole, Role
from apps.core.config.enquiry_access_config import (
    ENQUIRY_SPECIAL_ACCESS_USERS,
    ENQUIRY_MODULE_CODE,
    ENQUIRY_ADMIN_ROLES,
)

User = get_user_model()


class Command(BaseCommand):
    help = 'Grant enquiry_management module access (soft-coded from enquiry_access.config.py)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='Grant access to specific email (default: all users in ENQUIRY_SPECIAL_ACCESS_USERS)'
        )

    def handle(self, *args, **options):
        specific_email = options.get('email')
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("🔧 GRANT ENQUIRY MANAGEMENT ACCESS - SOFT-CODED"))
        self.stdout.write("=" * 80)
        self.stdout.write("")
        
        # Step 1: Get enquiry_management module
        try:
            enquiry_module = Module.objects.get(code=ENQUIRY_MODULE_CODE, is_active=True)
            self.stdout.write(f"📦 Module: {enquiry_module.name} ({enquiry_module.code})")
        except Module.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"❌ Module '{ENQUIRY_MODULE_CODE}' not found!"))
            self.stdout.write(self.style.WARNING("   Run migrations first: python manage.py migrate rbac"))
            return
        
        # Step 2: Determine which users to grant access
        if specific_email:
            target_emails = [specific_email]
            self.stdout.write(f"🎯 Target: Specific user ({specific_email})")
        else:
            target_emails = ENQUIRY_SPECIAL_ACCESS_USERS
            self.stdout.write(f"🎯 Target: Special access users from config ({len(target_emails)} users)")
        
        self.stdout.write("")
        self.stdout.write("📧 Special Access Users:")
        for email in target_emails:
            self.stdout.write(f"   • {email}")
        
        self.stdout.write("")
        self.stdout.write("🔐 Step 1: Grant Access via Role Assignment")
        self.stdout.write("-" * 80)
        
        granted_count = 0
        already_has_count = 0
        error_count = 0
        
        # Get ICT admin role (soft-coded role that includes enquiry_management)
        try:
            ict_admin_role = Role.objects.get(code='ict_admin', is_active=True)
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR("❌ ICT Admin role not found!"))
            return
        
        for email in target_emails:
            try:
                user = User.objects.get(email=email, is_active=True)
                profile, _ = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={'is_deleted': False}
                )
                
                # Check if user already has enquiry access via any role
                if profile.has_module_access(ENQUIRY_MODULE_CODE):
                    self.stdout.write(f"  ✅ Already has access: {email}")
                    already_has_count += 1
                    continue
                
                # Assign ICT admin role if not already assigned
                user_role, created = UserRole.objects.get_or_create(
                    user_profile=profile,
                    role=ict_admin_role,
                    defaults={'is_primary': False}
                )
                
                if created:
                    self.stdout.write(self.style.SUCCESS(f"  ✅ Granted ICT Admin role: {email}"))
                    granted_count += 1
                else:
                    self.stdout.write(f"  ✅ Already has ICT Admin role: {email}")
                    already_has_count += 1
                
                # Clear cache
                cache.delete(f'user_modules_{profile.id}')
                cache.delete(f'user_permissions_{profile.id}')
                cache.delete(f'user_roles_{profile.id}')
                
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"  ❌ User not found: {email}"))
                error_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ❌ Error for {email}: {str(e)}"))
                error_count += 1
        
        # Step 3: Verify role has module
        self.stdout.write("")
        self.stdout.write("🔍 Step 2: Verify ICT Admin Role Has Enquiry Module")
        self.stdout.write("-" * 80)
        
        from apps.rbac.models import RoleModule
        role_module, rm_created = RoleModule.objects.get_or_create(
            role=ict_admin_role,
            module=enquiry_module
        )
        
        if rm_created:
            self.stdout.write(self.style.SUCCESS(f"  ✅ Added {ENQUIRY_MODULE_CODE} to {ict_admin_role.code} role"))
        else:
            self.stdout.write(f"  ✅ ICT Admin role already has {ENQUIRY_MODULE_CODE} module")
        
        # Summary
        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("✅ GRANT SUMMARY"))
        self.stdout.write("=" * 80)
        self.stdout.write(f"Users granted access:   {granted_count}")
        self.stdout.write(f"Already had access:     {already_has_count}")
        self.stdout.write(f"Errors:                 {error_count}")
        self.stdout.write(f"Total processed:        {len(target_emails)}")
        self.stdout.write("=" * 80)
        
        # Final verification
        self.stdout.write("")
        self.stdout.write("🔍 Step 3: Final Verification")
        self.stdout.write("-" * 80)
        
        for email in target_emails:
            try:
                user = User.objects.get(email=email, is_active=True)
                profile = UserProfile.objects.get(user=user, is_deleted=False)
                has_access = profile.has_module_access(ENQUIRY_MODULE_CODE)
                
                if has_access:
                    self.stdout.write(self.style.SUCCESS(f"  ✅ Verified: {email} → enquiry_management access GRANTED"))
                else:
                    self.stdout.write(self.style.ERROR(f"  ❌ Failed: {email} → NO ACCESS"))
            except (User.DoesNotExist, UserProfile.DoesNotExist):
                pass
        
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("✅ Done! Users can now access /admin/enquiries"))
        self.stdout.write("")
