"""
Management command: load_pid_equipment_types
============================================
Seeds / refreshes the PIDEquipmentType table from the soft-coded
designation_codes section of equipment_type_config.json.

Usage:
    python manage.py load_pid_equipment_types            # upsert all
    python manage.py load_pid_equipment_types --dry-run  # preview only
"""
import json
import os

from django.core.management.base import BaseCommand

from apps.pid_analysis.models import PIDEquipmentType

# Soft-coded path — same directory as the rest of the pid_analysis config
_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__),
    '..', '..', 'config', 'equipment_type_config.json',
)


class Command(BaseCommand):
    help = 'Seed PIDEquipmentType table from equipment_type_config.json designation_codes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be created/updated without writing to DB',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        config_path = os.path.normpath(_CONFIG_PATH)
        if not os.path.exists(config_path):
            self.stderr.write(self.style.ERROR(f'Config not found: {config_path}'))
            return

        with open(config_path, encoding='utf-8') as fh:
            config = json.load(fh)

        codes = config.get('designation_codes', {})
        if not codes:
            self.stderr.write(self.style.WARNING('No designation_codes found in config'))
            return

        created = updated = 0
        for code, meta in codes.items():
            defaults = {
                'name':        meta.get('name', code),
                'category':    meta.get('category', 'MISC'),
                'is_rotating': bool(meta.get('rotating', False)),
                'is_active':   True,
            }
            if dry_run:
                exists = PIDEquipmentType.objects.filter(pk=code).exists()
                action = 'UPDATE' if exists else 'CREATE'
                self.stdout.write(f'  [{action}] {code} → {defaults["name"]} ({defaults["category"]})')
                if not exists:
                    created += 1
                else:
                    updated += 1
            else:
                _obj, _created = PIDEquipmentType.objects.update_or_create(
                    code=code, defaults=defaults,
                )
                if _created:
                    created += 1
                else:
                    updated += 1

        prefix = '[DRY-RUN] ' if dry_run else ''
        self.stdout.write(
            self.style.SUCCESS(
                f'{prefix}PIDEquipmentType sync complete — '
                f'{created} created, {updated} updated'
            )
        )
