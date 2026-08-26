from django.core.management.base import BaseCommand, CommandError

from ...services.regression_library import run_regression_library


class Command(BaseCommand):
    help = 'Run the versioned planning regression project library.'

    def handle(self, *args, **options):
        results = run_regression_library()
        for row in results:
            self.stdout.write(f"[{'PASS' if row['passed'] else 'FAIL'}] {row['code']}: {row['actual']}")
        failures = [row for row in results if not row['passed']]
        if failures:
            raise CommandError(f'{len(failures)} regression project(s) failed.')
