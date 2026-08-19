"""
Django Management Command: Setup ICT Admin Permissions
Complete one-command solution for granting ICT admin access

Usage:
    python manage.py setup_ict_admin
    python manage.py setup_ict_admin --email=custom@email.com
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.core.cache import cache
from apps.rbac.models import UserProfile, Role, UserRole, Module, RoleModule
from apps.rbac.rbac_config import ROLE_MODULE_POLICY

User = get_user_model()


class Command(BaseCommand):
    help = 'Setup ICT admin with ICT Administrator role (soft-coded RBAC)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            default='radai@rejlers.ae',
            help='Email of the ICT admin user (default: radai@rejlers.ae)'
        )
        parser.add_argument(
            '--department',
            type=str,
            default='ICT',
            help='Department name (default: ICT)'
        )

    def handle(self, *args, **options):
        ict_email = options['email']
        ict_department = options['department']
        
        # Admin module codes from ROLE_MODULE_POLICY (soft-coded)
        ADMIN_MODULE_CODES = ROLE_MODULE_POLICY.get('ict_admin', [])
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("🔧 COMPLETE ICT ADMIN SETUP - SOFT-CODED RBAC"))
        self.stdout.write("=" * 80)
        self.stdout.write("")
        
        # Step 1: Get user and profile
        self.stdout.write(f"📧 User: {ict_email}")
        try:
            user = User.objects.get(email=ict_email)
            profile, _ = UserProfile.objects.get_or_create(user=user)
            self.stdout.write(self.style.SUCCESS(f"✅ Found user profile: {profile}"))
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"❌ User not found: {ict_email}"))
            return
        
        # Step 2: Get ICT Admin role (level 2) - NEW dedicated role
        try:
            ict_admin_role = Role.objects.get(code='ict_admin')
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR("❌ ICT Administrator role not found. Run migration 0033_seed_ict_admin_role first."))
            return
        
        admin_role = Role.objects.get(code='admin')
        super_admin_role = Role.objects.get(code='super_admin')
        
        self.stdout.write("")
        self.stdout.write("🔐 Step 1: Assign ICT Administrator Role")
        self.stdout.write("-" * 80)
        
        # Remove super_admin and regular admin roles if exist
        removed_roles = []
        super_admin_count = UserRole.objects.filter(
            user_profile=profile,
            role=super_admin_role
        ).delete()[0]
        if super_admin_count > 0:
            removed_roles.append("Super Administrator")
        
        admin_count = UserRole.objects.filter(
            user_profile=profile,
            role=admin_role
        ).delete()[0]
        if admin_count > 0:
            removed_roles.append("Administrator")
        
        if removed_roles:
            self.stdout.write(self.style.WARNING(f"⚠️  Removed roles: {', '.join(removed_roles)}"))
        
        # Assign ICT Admin role
        user_role, created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=ict_admin_role
        )
        
        if created:
            self.stdout.write(self.style.SUCCESS(f"✅ Assigned ICT Administrator role to {ict_email}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"✅ ICT Administrator role already assigned"))
        
        # Update user flags and department
        user.is_staff = True
        user.is_superuser = False
        user.save()
        
        profile.department = ict_department
        profile.save()
        
        self.stdout.write(self.style.SUCCESS(f"✅ Updated department to {ict_department}"))
        
        # Step 3: Ensure admin modules exist and grant to ICT Admin role
        self.stdout.write("")
        self.stdout.write(f"📦 Step 2: Grant Admin Modules to ICT Administrator Role ({len(ADMIN_MODULE_CODES)} modules)")
        self.stdout.write("-" * 80)
        
        granted_count = 0
        for module_code in ADMIN_MODULE_CODES:
            try:
                module = Module.objects.get(code=module_code, is_active=True)
                role_module, created = RoleModule.objects.get_or_create(
                    role=ict_admin_role,
                    module=module
                )
                
                if created:
                    self.stdout.write(f"  ✅ Granted: {module.code:<25} {module.name:<40} → ict_admin")
                    granted_count += 1
                else:
                    self.stdout.write(f"  ✅ Exists:  {module.code:<25} {module.name:<40} → ict_admin")
            except Module.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"  ⚠️  Module not found: {module_code} (run migrations first)"))
        
        self.stdout.write(self.style.SUCCESS(f"\n✅ Granted {granted_count} new modules, {len(ADMIN_MODULE_CODES)} total"))
        
        # Step 5: Clear cache
        self.stdout.write("")
        self.stdout.write("🔄 Step 4: Clear User Cache")
        self.stdout.write("-" * 80)
        
        cache_keys = [
            f'user_modules_{profile.id}',
            f'user_permissions_{profile.id}',
            f'user_roles_{profile.id}'
        ]
        
        for key in cache_keys:
            cache.delete(key)
        
        self.stdout.write(self.style.SUCCESS(f"✅ Cleared module/permission cache for {ict_email}"))
        
        # Step 6: Verification
        self.stdout.write("")
        self.stdout.write("📋 Step 5: Verification")
        self.stdout.write("-" * 80)
        
        # Show user roles
        user_roles = profile.roles.all()
        self.stdout.write("👤 User Roles:")
        for role in user_roles:
            self.stdout.write(f"  • {role.name:<30} ({role.code:<15}) Level {role.level}")
        
        # Show Django flags
        self.stdout.write("")
        self.stdout.write("🚩 Django Flags:")
        self.stdout.write(f"  • is_staff: {user.is_staff}")
        self.stdout.write(f"  • is_superuser: {user.is_superuser}")
        self.stdout.write(f"  • Department: {profile.department}")
        
        # Show accessible modules
        accessible_modules = profile.get_accessible_modules()
        self.stdout.write("")
        self.stdout.write(f"📦 Accessible Modules ({len(accessible_modules)} total):")
        
        for module in accessible_modules:
            is_admin_module = "✅ ADMIN" if module.code in ADMIN_MODULE_CODES else ""
            self.stdout.write(f"  • {module.code:<25} {module.name:<40} {is_admin_module}")
        
        # Final summary
        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("✅ ICT ADMIN SETUP COMPLETE"))
        self.stdout.write("=" * 80)
        self.stdout.write(f"User:                   {ict_email}")
        self.stdout.write(f"Role:                   ICT Administrator (level 2)")
        self.stdout.write(f"Department:             {ict_department}")
        self.stdout.write(f"Admin Modules granted:  {len(ADMIN_MODULE_CODES)}")
        self.stdout.write(f"Total accessible:       {len(accessible_modules)}")
        self.stdout.write("=" * 80)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("🎯 User must now:"))
        self.stdout.write("   1. Log out from https://www.radai.ae")
        self.stdout.write("   2. Clear browser cache (Ctrl+Shift+Delete)")
        self.stdout.write("   3. Log back in")
        self.stdout.write("   4. Test admin sections - should all work!")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("🔒 Security Note:"))
        self.stdout.write("   • ICT Admin role has ONLY admin section access")
        self.stdout.write("   • NO access to Engineering, HR, Finance, or Procurement")
        self.stdout.write("   • Soft-coded from ROLE_MODULE_POLICY in rbac_config.py")
        self.stdout.write("")
