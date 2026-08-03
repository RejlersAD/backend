"""
Django Management Command: Grant Procurement Module Access
Ensures all admin users have access to procurement modules

SOFT-CODED SOLUTION:
- Grants procurement module access to admin, super_admin, and project_manager roles
- Safe to run multiple times (idempotent)
- Uses role-based module assignment from rbac_config.py
"""

from django.core.management.base import BaseCommand
from apps.rbac.models import Role, Module


class Command(BaseCommand):
    help = 'Grant procurement module access to admin roles'

    def handle(self, *args, **options):
        self.stdout.write("=" * 80)
        self.stdout.write("GRANTING PROCUREMENT MODULE ACCESS")
        self.stdout.write("=" * 80)

        # Procurement modules to grant (soft-coded from rbac_config.py)
        procurement_modules = [
            'procurement',
            'procurement_vendors',
            'procurement_requisitions',
            'procurement_orders',
            'procurement_receipts',
        ]

        # Roles that should have procurement access
        target_roles = ['admin', 'super_admin', 'project_manager', 'procurement_manager']

        granted_count = 0
        for role_code in target_roles:
            try:
                role = Role.objects.get(code=role_code, is_active=True)
                self.stdout.write(f"\n[ROLE] {role.name} ({role.code})")

                for module_code in procurement_modules:
                    try:
                        module = Module.objects.get(code=module_code, is_active=True)
                        
                        # Check if already granted
                        if role.modules.filter(id=module.id).exists():
                            self.stdout.write(f"  ✓ {module.name} - already granted")
                        else:
                            # Grant module access
                            role.modules.add(module)
                            granted_count += 1
                            self.stdout.write(self.style.SUCCESS(f"  ✓ {module.name} - GRANTED"))
                    
                    except Module.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f"  ⚠️  Module '{module_code}' not found"))
            
            except Role.DoesNotExist:
                self.stdout.write(self.style.WARNING(f"[ROLE] {role_code} not found - skipping"))

        self.stdout.write("\n" + "=" * 80)
        if granted_count > 0:
            self.stdout.write(self.style.SUCCESS(f"✓ GRANTED {granted_count} NEW MODULE ACCESS(ES)"))
        else:
            self.stdout.write(self.style.SUCCESS("✓ ALL ROLES ALREADY HAVE PROCUREMENT ACCESS"))
        self.stdout.write("=" * 80)

        # Show summary of users with procurement access
        self.stdout.write("\n[USERS WITH PROCUREMENT ACCESS]")
        from apps.rbac.models import UserProfile
        
        users_with_access = UserProfile.objects.filter(
            roles__code__in=target_roles,
            roles__is_active=True
        ).distinct().select_related('user')[:10]

        if users_with_access.exists():
            for profile in users_with_access:
                user_roles = ', '.join([r.name for r in profile.roles.filter(is_active=True)])
                self.stdout.write(f"  ✓ {profile.user.email} ({user_roles})")
        else:
            self.stdout.write(self.style.WARNING("  ⚠️  No users found with admin roles"))

        self.stdout.write("")
