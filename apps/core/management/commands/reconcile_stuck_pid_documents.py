"""
Recover P&ID Verification (V1) documents whose background processing
thread was killed mid-flight (e.g. by a Railway deploy/restart happening
while `_run_sync_pipeline` was still running — see
apps/pid_verification/views.py's `_run_sync_pipeline`) before it reached
its final `status = COMPLETED` save.

Because that background thread has no external supervisor, a hard kill
leaves the document permanently stuck showing `uploaded` or `processing`
in the UI forever — even when the real work (drawings, findings) actually
finished successfully. Confirmed live on 2026-08-27: a document showed
"uploaded" with 65 real findings and 1 real drawing already in the
database — the analysis had genuinely completed, only the final status
flip was lost.

This command is data-preserving, not just a failure-marker:
  - If a stuck document already has at least one PIDVDrawing with at least
    one PIDVFinding recorded, that's strong evidence processing actually
    finished — it's promoted to COMPLETED, recovering the real result
    instead of discarding it.
  - Otherwise (no drawings, or drawings with zero findings — inherently
    ambiguous: some documents legitimately have zero findings), it's left
    to the --mark-failed-after-minutes threshold: only marked FAILED once
    it's been stuck for a long time with no real progress at all, with a
    clear "processing was interrupted" message so it stops silently
    spinning in the UI.

Safe to run repeatedly (idempotent: only touches documents currently in
uploaded/processing) and safe to run on a schedule.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Recover or fail P&ID (V1) documents whose processing thread was killed mid-flight.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--stuck-after-minutes', type=int, default=10,
            help='Only consider documents with no update in at least this many minutes (default: 10).',
        )
        parser.add_argument(
            '--mark-failed-after-minutes', type=int, default=60,
            help=(
                'A stuck document with NO real findings/drawings yet is only marked FAILED '
                'once it has been stuck for at least this long (default: 60) — short-lived '
                '"still genuinely processing" documents are left alone.'
            ),
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing anything.',
        )

    def handle(self, *args, **options):
        from apps.pid_verification.models import PIDVDocument

        stuck_after = timezone.now() - timedelta(minutes=options['stuck_after_minutes'])
        fail_after = timezone.now() - timedelta(minutes=options['mark_failed_after_minutes'])
        dry_run = options['dry_run']

        stuck_qs = PIDVDocument.objects.filter(
            status__in=[PIDVDocument.Status.UPLOADED, PIDVDocument.Status.PROCESSING],
            updated_at__lt=stuck_after,
        ).prefetch_related('drawings__findings')

        recovered = 0
        failed = 0
        left_alone = 0

        for doc in stuck_qs:
            drawings = list(doc.drawings.all())
            has_real_findings = any(drawing.findings.exists() for drawing in drawings)

            if drawings and has_real_findings:
                total_findings = sum(drawing.findings.count() for drawing in drawings)
                self.stdout.write(
                    f'RECOVER {doc.document_id} ({doc.file_name}) — '
                    f'{len(drawings)} drawing(s), {total_findings} finding(s) already exist, '
                    f'stuck at status={doc.status!r} since {doc.updated_at} -> COMPLETED'
                )
                if not dry_run:
                    doc.status = PIDVDocument.Status.COMPLETED
                    doc.save(update_fields=['status', 'updated_at'])
                recovered += 1
            elif doc.updated_at < fail_after:
                self.stdout.write(
                    f'FAIL    {doc.document_id} ({doc.file_name}) — '
                    f'no drawings/findings recorded, stuck since {doc.updated_at} -> FAILED'
                )
                if not dry_run:
                    doc.status = PIDVDocument.Status.FAILED
                    doc.error_message = (
                        'Processing was interrupted (likely a server restart during analysis) '
                        'and never completed. Please re-upload and try again.'
                    )
                    doc.save(update_fields=['status', 'error_message', 'updated_at'])
                failed += 1
            else:
                left_alone += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{"Would recover" if dry_run else "Recovered"}: {recovered}   '
            f'{"Would fail" if dry_run else "Failed"}: {failed}   '
            f'Left alone (still within grace period): {left_alone}'
        ))
