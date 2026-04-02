from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.pid_verification.services.legend_knowledge import (
    LEGEND_KNOWLEDGE_PATH,
    build_legend_knowledge,
    save_legend_knowledge,
)


class Command(BaseCommand):
    help = "Extract legend sheet data and persist reusable PID legend knowledge JSON"

    def add_arguments(self, parser):
        parser.add_argument(
            "files",
            nargs="+",
            help="Path(s) to legend sheet PDF files",
        )
        parser.add_argument(
            "--output",
            default=str(LEGEND_KNOWLEDGE_PATH),
            help="Output JSON path (default: backend/domain_knowledge/pid_verification/legend_knowledge.json)",
        )

    def handle(self, *args, **options):
        files = options["files"]
        output = Path(options["output"])

        missing = [f for f in files if not Path(f).exists()]
        if missing:
            raise CommandError(f"File(s) not found: {missing}")

        knowledge = build_legend_knowledge(files)
        target = save_legend_knowledge(knowledge, output)

        self.stdout.write(self.style.SUCCESS(f"Legend knowledge saved: {target}"))
        self.stdout.write(f"Sources: {len(knowledge.get('sources', []))}")
        self.stdout.write(f"Instrument prefixes: {len(knowledge.get('instrument_prefixes', []))}")
        self.stdout.write(f"Valve prefixes: {len(knowledge.get('valve_prefixes', []))}")
