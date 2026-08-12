"""
Management command to delete ALL unwanted roles from production.
Handles both system and non-system roles.
"""

from django.core.management.base import BaseCommand
from apps.rbac.models import Role


class Command(BaseCommand):
    help = 'Delete all unwanted roles from production database'

    def handle(self, *args, **options):
        # Roles to delete (both system and non-system)
        roles_to_delete = [
            # Originally unwanted engineering/hr roles
            'civil_engineer',
            'mechanical_engineer',
            'process_engineer',
            'electrical_engineer',
            'instrument_engineer',
            'piping_engineer',
            'design_engineer',
            'human_resource',
            'engineer',
            'onboarding',
            'ict_admin',
            # Non-system roles
            'admin_it',    # ICT Admin
        ]

        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.WARNING("CLEANING UP ALL UNWANTED ROLES"))
        self.stdout.write("="*70 + "\n")

        deleted = 0
        skipped = 0
        not_found = 0

        for role_code in roles_to_delete:
            # Search regardless of is_system_role flag
            roles = Role.objects.filter(code=role_code, is_active=True)
            if not roles.exists():
                self.stdout.write(f"  ✓ {role_code:30} - Not found (already deleted)")
                not_found += 1
                continue

            for role in roles:
                user_count = role.user_profiles.count()
                if user_count > 0:
                    self.stdout.write(
                        self.style.WARNING(f"  ⚠ {role.name:40} ({role.code}) - SKIPPED ({user_count} users)")
                    )
                    skipped += 1
                    continue

                module_count = role.modules.count()
                role.modules.clear()
                role.delete()
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ {role.name:40} ({role.code}) - DELETED ({module_count} modules)")
                )
                deleted += 1

        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS(f"✅ Deleted: {deleted} | Skipped (has users): {skipped} | Not found: {not_found}"))

        # Show current state
        all_active = Role.objects.filter(is_active=True).exclude(code__startswith='custom_').order_by('name')
        self.stdout.write(f"\n📋 Current roles in database ({all_active.count()}):")
        for role in all_active:
            uc = role.user_profiles.count()
            flag = "[SYS]" if role.is_system_role else "[NON]"
            self.stdout.write(f"  {flag} {role.name} ({role.code}) - {uc} users")
        self.stdout.write("="*70 + "\n")
