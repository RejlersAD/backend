"""
Management command to delete manually-created non-system roles.
"""

from django.core.management.base import BaseCommand
from apps.rbac.models import Role


class Command(BaseCommand):
    help = 'Delete manually-created non-system roles (admin_it, manager, onboarding, procurement_manager, project_control)'

    def handle(self, *args, **options):
        roles_to_delete = [
            'admin_it',              # ICT Admin
            'manager',               # Manager
            'onboarding',            # onboarding/offboarding
            'procurement_manager',   # Procurement Manager
            'project_control',       # Project Control
        ]

        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.WARNING("DELETING NON-SYSTEM ROLES"))
        self.stdout.write("="*70 + "\n")

        total_deleted = 0
        total_skipped = 0

        for role_code in roles_to_delete:
            # Find role regardless of is_system_role flag
            role = Role.objects.filter(code=role_code, is_active=True).first()
            
            if not role:
                self.stdout.write(f"  ✓ {role_code:25} - Already deleted or inactive")
                continue

            user_count = role.user_profiles.count()
            
            if user_count > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ⚠ {role.name:40} - SKIPPED ({user_count} users assigned)"
                    )
                )
                total_skipped += 1
                continue

            # Delete role
            module_count = role.modules.count()
            role.modules.clear()
            role_name = role.name
            role.delete()
            
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ {role_name:40} - DELETED ({module_count} modules removed)"
                )
            )
            total_deleted += 1

        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS(f"✅ Deleted {total_deleted} roles"))
        if total_skipped > 0:
            self.stdout.write(self.style.WARNING(f"⚠️  Skipped {total_skipped} roles (have users assigned)"))
        
        # Show remaining roles (system + non-system)
        system_roles = Role.objects.filter(is_system_role=True, is_active=True).order_by('name')
        non_system_roles = Role.objects.filter(is_system_role=False, is_active=True).exclude(code__startswith='custom_').order_by('name')
        
        self.stdout.write(f"\n📋 System roles remaining: {system_roles.count()}")
        for role in system_roles:
            self.stdout.write(f"  - {role.name} ({role.code})")
        
        if non_system_roles.exists():
            self.stdout.write(f"\n⚠️  Non-system roles still remaining: {non_system_roles.count()}")
            for role in non_system_roles:
                user_count = role.user_profiles.count()
                self.stdout.write(f"  - {role.name} ({role.code}) - {user_count} users")
        else:
            self.stdout.write("\n✅ No non-system roles remaining (except custom_ user roles)")
        
        self.stdout.write("="*70 + "\n")
