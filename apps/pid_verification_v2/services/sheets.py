"""
Sheet registry — one PDF page = one P&ID (multi-file + multi-sheet uploads)
===========================================================================

A PIDVSheet row is created/updated for every PIDVDrawing after its title
block is read (hooked from tasks.py's two drawing-persistence sites), and
backfilled for existing documents by data migration 0014.

Drawing-number rules (spec A1):
  - the CLIENT number pattern (\\d{2}-P[PU]-\\d{3}-\\d{5,6}(-\\d{2})?) wins
    over contractor numbers ('&AA-…', 'AA-E3-…');
  - only a contractor number found → drawing_no_confirmed=False
    ("Drawing number to confirm" in the UI);
  - pages with no title block → is_drawing=False (skipped by all checks).

Duplicates (same drawing_no as a current project sheet):
  - newer revision → the old sheet is superseded (read-only) and finding
    dispositions carry over by rule_id + evidence text;
  - same revision → the upload endpoint returns 409 duplicate_revision
    (checked inline for single-page uploads; multi-page sets keep both
    rows — the engineer resolves them from the drawings table).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Client (Borouge) drawing number: 13-PP-152-20070 / 37-PU-152-09905-01
CLIENT_NO_RE = re.compile(r'^\d{2}-P[PU]-\d{3}-\d{5,6}(?:-\d{2})?$')
# Contractor numbers: '&AA-…', 'AA-E3-P-FP', 'E3-PP-152-01100' style.
CONTRACTOR_NO_RE = re.compile(r'^(?:&[A-Z0-9]{1,4}|[A-Z0-9]{1,3}-?E\d|[A-Z]{2}-E\d)-')

_REV_RE = re.compile(r'^[A-Z]{1,3}$')


def pick_drawing_number(candidates) -> tuple[str, bool]:
    """Choose the best drawing number from candidates: client pattern first,
    then anything contractor-shaped (unconfirmed), then the first non-empty
    candidate (confirmed — an unknown but plausible number)."""
    cands = [str(c).strip() for c in candidates if c and str(c).strip()]
    if not cands:
        return '', True
    for c in cands:
        if CLIENT_NO_RE.match(c):
            return c, True
    for c in cands:
        if CONTRACTOR_NO_RE.match(c):
            return c, False
    return cands[0], True


def _rev_key(rev: str) -> Optional[tuple]:
    """Revision sort key: 'C' < 'Q', 'Rev 3' numeric, etc. None = unknown."""
    r = (rev or '').strip().upper().lstrip('REV').strip()
    if not r:
        return None
    if r.isdigit():
        return (1, int(r), '')
    if _REV_RE.match(r):
        return (0, 0, r)
    return (2, 0, r)


def sync_sheet_for_drawing(document, drawing) -> None:
    """Create/update the PIDVSheet for one PIDVDrawing (document, page).
    Called from tasks.py after the title block is stored. Non-fatal by
    design at the call site."""
    from apps.pid_verification_v2.models import PIDVSheet

    tb = (drawing.metadata or {}).get('title_block') or {}
    has_block = bool(tb.get('drawing_number') or tb.get('title'))
    candidates = [tb.get('drawing_number'), tb.get('drawing_number_base'), drawing.title]
    drawing_no, confirmed = pick_drawing_number(candidates)

    sheet, _created = PIDVSheet.objects.update_or_create(
        document=document, page_index=drawing.page_index,
        defaults={
            'project': document.project,
            'drawing_no': drawing_no,
            'drawing_no_confirmed': confirmed,
            'rev': (tb.get('revision') or '').strip(),
            'title': (tb.get('title') or drawing.title or '').strip(),
            'status': document.status or '',
            'is_drawing': has_block,
        },
    )
    if _created or drawing_no:
        _apply_supersession(sheet)


def sync_sheets_for_document(document) -> None:
    for drawing in document.drawings.all():
        try:
            sync_sheet_for_drawing(document, drawing)
        except Exception:
            logger.warning(
                '[Sheets] sync failed for doc=%s page=%s (non-fatal)',
                document.document_id, drawing.page_index, exc_info=True,
            )


def _apply_supersession(sheet) -> None:
    """Newer revision of the same drawing number supersedes the old sheet;
    finding dispositions carry over by rule_id + evidence text. Same
    revision keeps both (the 409 duplicate path lives in upload_pid)."""
    if not sheet.project_id or not sheet.drawing_no:
        return
    from apps.pid_verification_v2.models import PIDVSheet

    peers = (
        PIDVSheet.objects
        .filter(project_id=sheet.project_id, drawing_no=sheet.drawing_no,
                is_drawing=True, superseded_by__isnull=True)
        .exclude(pk=sheet.pk)
    )
    my_rev = _rev_key(sheet.rev)
    for old in peers:
        old_rev = _rev_key(old.rev)
        if my_rev is None or old_rev is None or my_rev <= old_rev:
            continue  # same or older revision — never supersede here
        old.superseded_by = sheet
        old.save(update_fields=['superseded_by'])
        _carry_dispositions(old, sheet)
        logger.info(
            '[Sheets] %s rev %s superseded by rev %s (sheet %s → %s)',
            sheet.drawing_no, old.rev, sheet.rev, old.pk, sheet.pk,
        )


def _carry_dispositions(old_sheet, new_sheet) -> None:
    """Copy engineer dispositions (status) from the superseded sheet's
    findings to the new sheet's, matched by (rule_id, evidence)."""
    from apps.pid_verification_v2.models import PIDVFinding

    try:
        old_drawing = old_sheet.document.drawings.get(page_index=old_sheet.page_index)
        new_drawing = new_sheet.document.drawings.get(page_index=new_sheet.page_index)
    except Exception:
        return
    decided = (
        PIDVFinding.objects
        .filter(drawing=old_drawing)
        .exclude(status='open')
        .values_list('rule_id', 'evidence', 'status')
    )
    key_to_status = {(r, (e or '').strip()): s for r, e, s in decided}
    if not key_to_status:
        return
    carried = 0
    for f in PIDVFinding.objects.filter(drawing=new_drawing, status='open'):
        st = key_to_status.get((f.rule_id, (f.evidence or '').strip()))
        if st:
            f.status = st
            f.save(update_fields=['status'])
            carried += 1
    if carried:
        logger.info(
            '[Sheets] carried %d dispositions onto superseding sheet %s',
            carried, new_sheet.pk,
        )
