"""Data migration: one PIDVSheet per existing PIDVDrawing (30 Oct 2026).

Self-contained copy of services/sheets.pick_drawing_number — migrations
must not import app code that can drift.
"""
import re

from django.db import migrations

_CLIENT_NO_RE = re.compile(r'^\d{2}-P[PU]-\d{3}-\d{5,6}(?:-\d{2})?$')
_CONTRACTOR_NO_RE = re.compile(r'^(?:&[A-Z0-9]{1,4}|[A-Z0-9]{1,3}-?E\d|[A-Z]{2}-E\d)-')


def _pick(candidates):
    cands = [str(c).strip() for c in candidates if c and str(c).strip()]
    if not cands:
        return '', True
    for c in cands:
        if _CLIENT_NO_RE.match(c):
            return c, True
    for c in cands:
        if _CONTRACTOR_NO_RE.match(c):
            return c, False
    return cands[0], True


def backfill_sheets(apps, schema_editor):
    PIDVDrawing = apps.get_model('pid_verification_v2', 'PIDVDrawing')
    PIDVSheet = apps.get_model('pid_verification_v2', 'PIDVSheet')
    rows = []
    for d in PIDVDrawing.objects.select_related('document').iterator():
        tb = (d.metadata or {}).get('title_block') or {}
        drawing_no, confirmed = _pick([tb.get('drawing_number'), tb.get('drawing_number_base'), d.title])
        rows.append(PIDVSheet(
            project_id=d.document.project_id,
            document_id=d.document_id,
            page_index=d.page_index,
            drawing_no=drawing_no,
            drawing_no_confirmed=confirmed,
            rev=(tb.get('revision') or '').strip()[:20],
            title=((tb.get('title') or d.title or '').strip())[:512],
            status=d.document.status or '',
            is_drawing=bool(tb.get('drawing_number') or tb.get('title')),
        ))
    PIDVSheet.objects.bulk_create(rows, batch_size=500, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification_v2', '0013_pidvcrossfinding_pidvsheet'),
    ]

    operations = [
        migrations.RunPython(backfill_sheets, migrations.RunPython.noop),
    ]
