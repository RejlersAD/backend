import json

from django.core.management.base import BaseCommand, CommandError

from ...services.deployment_compatibility import check_planning_compatibility


class Command(BaseCommand):
    help = 'Verify Phase 1-4 database and API compatibility for deployment.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument('--require-worker', action='store_true')

    def handle(self, *args, **options):
        result = check_planning_compatibility(require_worker=options['require_worker'])
        if options['as_json']:
            self.stdout.write(json.dumps(result, indent=2))
        else:
            for row in result['checks']:
                self.stdout.write(f"[{row['status'].upper()}] {row['code']}: {row['detail']}")
        if not result['compatible']:
            raise CommandError('Planning deployment compatibility checks failed.')
