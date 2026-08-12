"""
Management command to force delete unused engineering roles.
This is a one-time cleanup command.

Usage:
    python manage.py force_delete_unused_roles
"""

from django.core.management.base import BaseCommand
from apps.rbac.models import Role


class Command(BaseCommand):
    help = 'Force delete unused engineering discipline and HR roles from database'

    def handle(self, *args, **options):
        roles_to_delete = [
            'civil_engineer', 'mechanical_engineer', 'process_engineer',
            'human_resource', 'piping_engineer', 'instrument_engineer',
            'electrical_engineer', 'design_engineer', 'engineer',
            'onboarding', 'ict_admin'
        ]

        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.WARNING("FORCE DELETING UNUSED ROLES"))
        self.stdout.write("="*70 + "\n")

        total_deleted = 0
        total_skipped = 0

        for role_code in roles_to_delete:
            role = Role.objects.filter(code=role_code, is_system_role=True).first()
            
            if not role:
                self.stdout.write(f"  ✓ {role_code:25} - Already deleted")
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
            self.stdout.write(self.style.WARNING(f"⚠️  Skipped {total_skipped} roles (have users)"))
        
        # Show remaining system roles
        remaining_roles = Role.objects.filter(is_system_role=True).order_by('name')
        self.stdout.write(f"\n📋 Current system roles ({remaining_roles.count()}):")
        for role in remaining_roles:
            user_count = role.user_profiles.count()
            self.stdout.write(f"  - {role.name} ({role.code}) - {user_count} users")
        
        self.stdout.write("="*70 + "\n")
