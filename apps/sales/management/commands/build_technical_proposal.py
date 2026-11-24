"""Build a template-driven technical proposal (DEC-TP-001) for a sales opportunity.

Django adapter over the framework-independent `proposal_engine`: the bid reference comes from the
opportunity record; the engine does scanning, extraction, validation and rendering. Read-only on
the database.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.sales.models import Deal


class Command(BaseCommand):
    help = 'Generate the master DOCX, client PDF binder and build_report.md for an opportunity.'

    def add_arguments(self, parser):
        parser.add_argument('--opportunity', required=True, help='Deal UUID or deal code, e.g. Q-102134')
        parser.add_argument('--source', action='append', required=True,
                            help='bid component folder or ZIP (repeatable), e.g. 03_Proposal and 04_Internal')
        parser.add_argument('--out', required=True)
        parser.add_argument('--config', help='proposal standard YAML (default: Rejlers ADNOC standard)')
        parser.add_argument('--reference', help='baseline proposal PDF for structural comparison')
        parser.add_argument('--converter', choices=('auto', 'office', 'libreoffice'), default='auto')
        parser.add_argument('--no-render', action='store_true',
                            help='scan, extract and validate only (no Office/LibreOffice needed)')

    def handle(self, *args, **options):
        from proposal_engine import build_proposal

        key = options['opportunity']
        deal = (Deal.objects.filter(deal_code__iexact=key).first()
                or (Deal.objects.filter(pk=key).first() if len(key) == 36 else None))
        if deal is None:
            raise CommandError(f'Opportunity {key!r} not found')
        result = build_proposal(options['source'], options['out'], config_path=options['config'],
                                quote_ref=deal.deal_code, reference_pdf=options['reference'],
                                converter_backend=options['converter'], render=not options['no_render'])
        counts = result['counts']
        self.stdout.write(f"{deal.deal_code} – {deal.deal_name}")
        self.stdout.write(f"Findings: {counts['ERROR']} ERROR / {counts['WARN']} WARN / {counts['INFO']} INFO")
        if result['comparison']:
            self.stdout.write(f"Structural equivalence vs baseline: {result['comparison']['overall']:.1%}")
        self.stdout.write(f"Report: {result['report']}")
