"""Synchronise the RBAC module catalogue (ALL_MODULES_CATALOGUE) into the DB.

Idempotent — safe to run at any time. Creates missing Module rows (via
_sync_module_catalogue, which also seeds the six standard action Permissions
per module via ensure_module_actions) and reports what was added.

Usage:
    python manage.py sync_module_catalogue            # apply
    python manage.py sync_module_catalogue --dry-run  # preview only
"""
from django.core.management.base import BaseCommand

from apps.rbac.rbac_config import ALL_MODULES_CATALOGUE
from apps.rbac.models import Module, _sync_module_catalogue


class Command(BaseCommand):
    help = 'Sync ALL_MODULES_CATALOGUE into the rbac_modules table (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List modules that would be created, without writing.')

    def handle(self, *args, **options):
        existing = set(Module.objects.values_list('code', flat=True))
        missing = [m for m in ALL_MODULES_CATALOGUE if m['code'] not in existing]

        if options['dry_run']:
            if not missing:
                self.stdout.write(self.style.SUCCESS('Catalogue already in sync — nothing to add.'))
            else:
                self.stdout.write('Modules that WOULD be created:')
                for m in missing:
                    self.stdout.write(f"  + {m['code']} ({m['name']})")
            return

        before = Module.objects.count()
        _sync_module_catalogue()
        after = Module.objects.count()
        added = after - before
        if missing:
            self.stdout.write(self.style.SUCCESS(f'Added {added} module(s):'))
            for m in missing:
                self.stdout.write(f"  + {m['code']} ({m['name']})")
        else:
            self.stdout.write(self.style.SUCCESS('Catalogue already in sync — nothing to add.'))
        self.stdout.write(f'Total modules in DB: {after}')
