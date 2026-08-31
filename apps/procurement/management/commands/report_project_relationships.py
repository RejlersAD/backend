"""Report canonical Project linkage across Procurement records."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.procurement.services.project_relationships import build_project_relationship_report


class Command(BaseCommand):
    help = (
        'Report enterprise Project linkage for Procurement projects, PRs, and POs. '
        'The command is read-only unless --apply is supplied.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Persist only unambiguous exact-code matches.',
        )
        parser.add_argument(
            '--format', choices=('summary', 'json'), default='summary',
            help='Output format (default: summary).',
        )
        parser.add_argument(
            '--output',
            help='Optional report file path. Parent directory must already exist.',
        )
        parser.add_argument(
            '--sample-limit', type=int, default=50,
            help='Maximum unresolved records included in the report (default: 50).',
        )

    def handle(self, *args, **options):
        sample_limit = options['sample_limit']
        if sample_limit < 0 or sample_limit > 1000:
            raise CommandError('--sample-limit must be between 0 and 1000.')

        report = build_project_relationship_report(
            apply=options['apply'], sample_limit=sample_limit,
        )
        if options['format'] == 'json':
            rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
        else:
            rendered = self._summary(report)

        output = options.get('output')
        if output:
            path = Path(output).expanduser()
            if not path.parent.exists():
                raise CommandError(f'Output directory does not exist: {path.parent}')
            path.write_text(rendered + '\n', encoding='utf-8')
            self.stdout.write(self.style.SUCCESS(f'Project relationship report written to {path}'))
        else:
            self.stdout.write(rendered)

    @staticmethod
    def _summary(report):
        lines = [
            'Canonical Project Relationship Report',
            f"Mode: {report['mode']}",
            f"Enterprise projects: {report['enterprise_projects']}",
        ]
        labels = (
            ('Procurement projects', 'procurement_projects'),
            ('Purchase requisitions', 'purchase_requisitions'),
            ('Purchase orders', 'purchase_orders'),
        )
        for label, key in labels:
            row = report[key]
            lines.append(
                f"{label}: total={row['total']} linked_before={row['linked_before']} "
                f"resolvable={row['resolvable']} unresolved={row['unresolved']}"
            )
        lines.append(f"Changes applied: {report['changes_applied']}")
        if report['unresolved']:
            lines.append('Unresolved sample:')
            for item in report['unresolved']:
                lines.append(
                    f"- {item['record_type']} {item['id']}: "
                    f"{item['reason']} ({item['reference']})"
                )
        return '\n'.join(lines)
