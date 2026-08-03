"""
Django management command to fix user permissions
Removes superuser/staff flags from users who should only have Default role
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role

User = get_user_model()


class Command(BaseCommand):
    help = 'Fix user permissions - remove superuser/staff flags from Default role users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='Email of the user to fix',
            default='Debasis.Sana@rejlers.ae'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without applying changes',
        )

    def handle(self, *args, **options):
        email = options['email']
        dry_run = options['dry_run']
        
        self.stdout.write("="*80)
        self.stdout.write(self.style.WARNING(" User Permission Fix Utility"))
        self.stdout.write("="*80 + "\n")
        
        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN MODE - No changes will be applied\n"))
        
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User not found: {email}\n"))
            return
        
        self.stdout.write(f"Found user: {email}")
        self.stdout.write(f"  Name: {user.first_name} {user.last_name}")
        self.stdout.write(f"  Active: {user.is_active}\n")
        
        # Check current Django flags
        self.stdout.write("CURRENT DJANGO FLAGS:")
        self.stdout.write(f"  is_superuser: {user.is_superuser}")
        self.stdout.write(f"  is_staff: {user.is_staff}\n")
        
        # Check RBAC profile and role
        try:
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            roles = profile.roles.filter(is_active=True)
            
            self.stdout.write("CURRENT RBAC ROLES:")
            for user_role in profile.userrole_set.filter(is_active=True):
                role = user_role.role
                primary = " (PRIMARY)" if user_role.is_primary else ""
                self.stdout.write(f"  - {role.name} (code: {role.code}){primary}")
            
            if not roles.exists():
                self.stdout.write(self.style.ERROR("  NO ACTIVE ROLES FOUND"))
            
            # Check modules
            modules = profile.get_all_modules()
            self.stdout.write(f"\nTOTAL MODULES: {len(modules)}")
            
            # Check for sensitive modules
            sensitive_modules = [
                'payroll', 'hr_management', 'timesheet', 'hr_onboarding',
                'finance', 'sales', 'procurement', 'procurement_vendors',
                'procurement_orders', 'procurement_requisitions', 'procurement_receipts'
            ]
            user_sensitive = [m for m in modules if m.code in sensitive_modules]
            if user_sensitive:
                self.stdout.write(self.style.ERROR(f"SENSITIVE MODULES: {', '.join([m.code for m in user_sensitive])}"))
            else:
                self.stdout.write(self.style.SUCCESS("No sensitive modules assigned"))
            
        except UserProfile.DoesNotExist:
            self.stdout.write(self.style.ERROR("  NO RBAC PROFILE FOUND\n"))
            return
        
        # Determine if fixes are needed
        needs_fix = False
        changes = []
        
        if user.is_superuser:
            needs_fix = True
            changes.append("Remove is_superuser flag")
        
        if user.is_staff:
            needs_fix = True
            changes.append("Remove is_staff flag")
        
        # Check if primary role is NOT default/super_admin/admin
        primary_role = profile.userrole_set.filter(is_primary=True, is_active=True).first()
        if primary_role:
            if primary_role.role.code not in ['default', 'super_admin', 'admin', 'ict_admin']:
                self.stdout.write(f"\nNote: Primary role is '{primary_role.role.code}' (not default)")
        
        if not needs_fix:
            self.stdout.write("\n" + self.style.SUCCESS("✅ NO FIXES NEEDED - User permissions are correct"))
            return
        
        # Apply fixes
        self.stdout.write("\n" + "="*80)
        self.stdout.write("PROPOSED CHANGES:")
        for change in changes:
            self.stdout.write(f"  - {change}")
        self.stdout.write("="*80 + "\n")
        
        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN - No changes applied\n"))
            return
        
        # Apply changes
        changed = False
        if user.is_superuser:
            user.is_superuser = False
            self.stdout.write("  ✅ Removed is_superuser flag")
            changed = True
        
        if user.is_staff:
            user.is_staff = False
            self.stdout.write("  ✅ Removed is_staff flag")
            changed = True
        
        if changed:
            user.save()
            self.stdout.write("\n" + self.style.SUCCESS("✅ USER UPDATED SUCCESSFULLY"))
        
        # Verify changes
        user.refresh_from_db()
        self.stdout.write("\nVERIFICATION (After Fix):")
        self.stdout.write(f"  is_superuser: {user.is_superuser}")
        self.stdout.write(f"  is_staff: {user.is_staff}")
        
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.SUCCESS("✅ PERMISSION FIX COMPLETE"))
        self.stdout.write("="*80 + "\n")
        
        self.stdout.write("IMPORTANT:")
        self.stdout.write("  - User access is now controlled ONLY by RBAC role")
        self.stdout.write("  - User will need to logout and login again for changes to take effect")
        self.stdout.write("  - Verify access at: https://www.radai.ae/admin/users\n")
