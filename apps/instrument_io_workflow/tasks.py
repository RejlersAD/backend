"""
Celery background tasks for large I/O List PDFs — parallel per-page
fan-out, mirroring apps.pid_verification.tasks' chord pattern: process a
large document as N parallel Celery subtasks instead of sequentially in
one process, then combine the results (same reasoning P&ID's own
comment gives — a 30-50+ page document can't reliably finish inside a
single synchronous HTTP request without risking a timeout or tying up a
Gunicorn worker for its full duration; this codebase has hit a real
worker-starvation incident from exactly that class of problem before).

Dispatched only for documents over services/config.py's
PAGE_FANOUT_THRESHOLD total pages — views.py's IOListDocumentViewSet.create()
decides. Smaller documents keep using the existing synchronous
services/orchestrator.py's extract_document() path inline in the upload
request, completely unchanged.

Lives at the app root (not under services/) because Celery's
app.autodiscover_tasks() (config/celery.py) only scans <app>/tasks.py for
each installed app — a services/tasks.py would silently never register
with a real worker, matching where apps.pid_verification.tasks lives too.

Fan-out granularity — NOT simply "one task per page" like P&ID, because
I/O List's two extractors have different cross-page requirements:
  - io_table/io_drawing pages: one Celery subtask PER PAGE.
    services.io_table_extractor's per-page loop has no cross-page state,
    so these are safe to fully parallelize.
  - comments_sheet pages: ONE subtask covering ALL of them together, in
    page order. services.comment_table_extractor.extract_comments_from_pages
    is explicitly stateful across pages — a review comment's text can
    continue onto the next page — so splitting it per-page would silently
    truncate/duplicate continuation rows. Still runs as its own background
    task (never blocks the HTTP request), just not parallelized further
    internally.

Pipeline (I/O List table document — page_type wired to io_table/io_drawing/
comments_sheet, PyMuPDF/local-OCR only):
    process_io_document           (classify pages, dispatch the chord)
      --chord-->
    process_io_table_page * N     (one per io_table/io_drawing page)
    process_io_comments_group * 1 (all comments_sheet pages together)
      --callback-->
    finalize_io_document          (combine, link, legend check, persist)

Pipeline (P&ID drawing document — every page reads as a P&ID, no
io_table/comments_sheet pages at all; process_io_document detects this up
front — see _looks_like_pid_drawing below — and dispatches this pipeline
instead of the one above):
    process_io_document                (detects pid_drawing, dispatches the chord)
      --chord-->
    process_pid_vision_page * N        (one per page — BYOK Vision if a key
                                         was supplied, else local OCR for
                                         that one page)
      --callback-->
    finalize_io_document                (dedupe, legend check, persist —
                                         same callback task, told which
                                         document_type it's finalizing)

The BYOK vision_provider/vision_api_key travel through the chord's task
arguments exactly the way apps.pid_verification.tasks already does for
its own Celery fan-out — same accepted tradeoff: with a real broker
(Redis) in production, the key transits broker storage for the life of
the task, rather than existing only in this one process's memory as it
does on the synchronous path. Not treated as new risk here — it's the
same handling apps.pid_verification's fan-out has used all along.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from celery import shared_task, chord
from django.db.models import F

logger = logging.getLogger(__name__)


def _persist_provisional_rows_and_comments(document_id: str, io_rows: list, comments: list) -> None:
    """Write this ONE unit's rows/comments to the DB immediately, in
    addition to (not instead of) returning them for finalize_io_document's
    combine step. Purpose: real, live progress — GET .../status/ (views.py)
    reads document.provisional_rows_count/provisional_comments_count
    (incremented below, not extracted_rows.count() — see those fields' own
    model comment for why a raw count is wrong specifically during a
    RE-extract), so a poller sees the count genuinely grow as each
    parallel unit finishes, not a fabricated animation.

    Safe by construction, not by convention: persist_extraction()
    (services/orchestrator.py), called once by finalize_io_document when
    the WHOLE document is done, unconditionally deletes every existing
    row/comment for this document before writing the final deduped/
    backfilled set — so these provisional rows are always replaced by the
    authoritative final result, never left stale or double-counted.
    Never raises — a provisional-persist failure must not fail the unit's
    own real work (the rows/comments it already extracted and is about to
    return to finalize_io_document regardless).
    """
    if not io_rows and not comments:
        return
    from .models import IOListDocument, IOListExtractedRow, IOListExtractedComment

    try:
        if io_rows:
            IOListExtractedRow.objects.bulk_create([
                IOListExtractedRow(
                    document_id=document_id,
                    tag_number=r.get('tag_number', ''),
                    page_number=r.get('page_number'),
                    data={k: v for k, v in r.items() if k not in ('tag_number', 'page_number')},
                )
                for r in io_rows
            ])
        if comments:
            IOListExtractedComment.objects.bulk_create([
                IOListExtractedComment(
                    document_id=document_id,
                    s_no=c.get('s_no', ''),
                    company_comment=c.get('company_comment', ''),
                    contractor_reply=c.get('contractor_reply', ''),
                    company_decision=c.get('company_decision', ''),
                    status_code=c.get('status_code', ''),
                    status_meaning=c.get('status_meaning', ''),
                    page_number=c.get('page_number'),
                    linked_tags=c.get('linked_tags', []),
                )
                for c in comments
            ])
        # Real, atomic — exactly how many rows/comments THIS call just
        # persisted, same F() pattern as pages_processed/vision_calls_done.
        # Correct for upload (starts at 0, nothing to conflate with) AND
        # re-extract (the previous run's rows are still in the DB but are
        # never counted here, only what THIS run has genuinely added so far).
        IOListDocument.objects.filter(id=document_id).update(
            provisional_rows_count=F('provisional_rows_count') + len(io_rows),
            provisional_comments_count=F('provisional_comments_count') + len(comments),
        )
    except Exception:
        logger.exception(
            '[IOWF] Provisional persist failed for document %s (%d rows, %d comments) — '
            'live progress count will lag, but finalize_io_document still has these '
            'in-memory and will persist the authoritative result at the end',
            document_id, len(io_rows), len(comments),
        )


def dispatch_io_document_processing(
    document_id: str,
    vision_provider: str | None = None,
    vision_api_key: str | None = None,
    thorough: bool = False,
) -> None:
    """Single entry point views.py's create()/re_extract() use to kick off
    extraction — hides the EAGER-mode-dev vs real-broker split so neither
    caller needs to know which one is active.

    With a real broker (Redis) configured, process_io_document.delay(...)
    already returns almost instantly — the actual work runs on a separate
    worker process, and the view's own refresh_from_db()/202 response plus
    the frontend's .../status/ polling loop see genuine incremental
    progress exactly as designed.

    WITHOUT a broker (CELERY_TASK_ALWAYS_EAGER=True — this dev
    environment's default when REDIS_URL/REDIS_HOST aren't set; see
    config/settings.py), .delay() has no worker to hand off to, so Celery
    runs the ENTIRE task inline, synchronously, in whichever thread called
    it — and for a chord, that means EVERY fanned-out subtask AND the
    finalize_io_document callback too, all before .delay() returns.
    Calling it directly from the request thread (as this code used to)
    meant the initial POST itself didn't return until the WHOLE extraction
    finished: for a P&ID drawing with BYOK Vision (many per-page API
    calls, doubled by VISION_PASSES), that's 20+ minutes of the request
    just hanging — the frontend's poll-based progress UI never got a
    chance to start polling, because the upload call that would hand it a
    document id to poll for hadn't returned yet. The per-page DB updates
    (pages_processed, current_phase, current_rows/current_comments) were
    already being written correctly the whole time — nothing was wrong
    with them, there was just no HTTP response yet for anything to poll
    against.

    Fix: in EAGER mode only, run .delay() on a background thread instead
    of inline, so the view can refresh_from_db()/return 202 right after
    dispatch, and a concurrent .../status/ poll sees those same per-page
    updates land in real time while the thread keeps working."""
    from django.conf import settings

    if not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        process_io_document.delay(document_id, vision_provider, vision_api_key, thorough)
        return

    import threading
    from django.db import connection

    def _run():
        try:
            process_io_document.delay(document_id, vision_provider, vision_api_key, thorough)
        except Exception:
            logger.exception(
                '[IOWF] Background EAGER-mode dispatch failed for document %s', document_id,
            )
            try:
                from .models import IOListDocument
                IOListDocument.objects.filter(id=document_id).update(
                    status='failed',
                    extraction_error='Extraction failed unexpectedly — please try again.',
                    current_phase='',
                )
            except Exception:
                pass
        finally:
            # This thread opened its own DB connection (Django connections
            # are thread-local); outside the request/response cycle
            # nothing closes it automatically, so it would otherwise leak
            # for the life of the dev server process.
            connection.close()

    threading.Thread(target=_run, daemon=True, name=f'io-extract-{document_id}').start()


def _looks_like_pid_drawing(pages: list) -> bool:
    """Cheap, dispatch-time approximation of
    services.orchestrator._detect_document_type — that function decides
    'io_list' vs 'pid_drawing' from ACTUAL extracted io_rows, which aren't
    available yet here (extraction hasn't run — that's the whole point of
    fanning it out). Approximates the same call with signals already on
    hand from classify_pages: any page classified io_table/io_drawing
    means real table content exists (the same condition process_io_document
    already used to decide there's table work to dispatch) -> 'io_list';
    otherwise fall back to the same P&ID title-block phrase check
    _detect_document_type uses when it has no io_rows. Reuses that
    function's own phrase list rather than duplicating it, so the two
    checks can't silently drift apart.
    """
    from .services.orchestrator import _PID_DRAWING_TITLE_PHRASES

    if any(p['page_type'] in ('io_table', 'io_drawing') for p in pages):
        return False
    combined_text = ' '.join((p.get('text') or '') for p in pages).lower()
    return any(phrase in combined_text for phrase in _PID_DRAWING_TITLE_PHRASES)


@shared_task(
    bind=True,
    name='instrument_io_workflow.process_document',
    max_retries=1,
    default_retry_delay=15,
    soft_time_limit=300,
    time_limit=360,
)
def process_io_document(
    self, document_id: str,
    vision_provider: str | None = None, vision_api_key: str | None = None,
    thorough: bool = False,
) -> None:
    """Coordinator: classify pages, fan out per-page/group subtasks via a
    Celery chord, dispatch finalize_io_document as its callback. Runs
    quickly — classification is pure PyMuPDF text triage; the actual
    extraction work happens in the fanned-out subtasks below.

    thorough: Quick/Thorough Scan toggle — only meaningful alongside
    vision_provider/vision_api_key (a P&ID drawing using Vision), passed
    straight through to each page's process_pid_vision_page subtask; see
    pid_vision_extractor.extract_pid_tags_from_page's own docstring.

    vision_provider/vision_api_key are BYOK — only meaningful when this
    document turns out to be a P&ID drawing (see _looks_like_pid_drawing);
    ignored entirely for a regular I/O List table document, same as
    views.py's synchronous path already does. Default None so every
    existing caller/queued task signature keeps working unchanged.

    Captures `started` (wall-clock time right now) and threads it through
    the chord to finalize_io_document, rather than having finalize derive
    elapsed time from a DB timestamp (document.created_at/updated_at) —
    those reflect when the ROW was created/last saved, not when THIS
    extraction run began, and go badly wrong on a re-extract of an old
    document (elapsed_seconds would show the age of the document, not the
    extraction's actual duration)."""
    started = time.time()
    from .services.page_classifier import classify_pages
    from .models import IOListDocument

    try:
        document = IOListDocument.objects.get(id=document_id)
    except IOListDocument.DoesNotExist:
        logger.error('[IOWF] process_io_document: document %s not found', document_id)
        return

    # Real phase, written the instant this task actually starts working —
    # GET .../status/ (views.py) surfaces this raw for the frontend.
    IOListDocument.objects.filter(id=document_id).update(current_phase='detecting_type')

    try:
        with document.pdf_file.open('rb') as f:
            pdf_bytes = f.read()
    except Exception as exc:
        logger.exception('[IOWF] process_io_document: failed to read PDF for %s', document_id)
        IOListDocument.objects.filter(id=document_id).update(
            status='failed', extraction_error=f'Could not read uploaded PDF: {exc}', current_phase='',
        )
        return

    pages = classify_pages(pdf_bytes)

    if _looks_like_pid_drawing(pages):
        page_count = len(pages)
        subtasks = [
            process_pid_vision_page.s(document_id, idx, vision_provider, vision_api_key, thorough)
            for idx in range(page_count)
        ]
        # vision_calls_total: the REAL, known-upfront call count for
        # whichever scan mode this run uses — VISION_PASSES/page (Quick)
        # or VISION_TILE_ROWS*VISION_TILE_COLS*VISION_PASSES/page
        # (Thorough); 0 when there's no key (local-OCR fallback has no
        # per-call concept, so the frontend falls back to pages_processed/
        # pages_total for that case). Gives GET .../status/ a MUCH finer
        # granularity than whole-page completion — see vision_calls_done's
        # own model-field comment for why that mattered.
        vision_calls_total = 0
        if vision_provider and vision_api_key:
            from .services.pid_vision_extractor import VISION_PASSES, VISION_TILE_ROWS, VISION_TILE_COLS
            calls_per_page = (VISION_TILE_ROWS * VISION_TILE_COLS * VISION_PASSES) if thorough else VISION_PASSES
            vision_calls_total = calls_per_page * page_count
        IOListDocument.objects.filter(id=document_id).update(
            pages_total=len(subtasks), pages_processed=0, current_phase='processing_pages',
            vision_calls_total=vision_calls_total, vision_calls_done=0, tokens_used_total=0,
            provisional_rows_count=0, provisional_comments_count=0,
        )
        chord(subtasks)(finalize_io_document.s(
            document_id, started, document_type='pid_drawing', thorough=thorough,
            vision_key_supplied=bool(vision_provider and vision_api_key),
        ))
        logger.info(
            '[IOWF] Detected P&ID drawing document %s (%d pages) — dispatched %d parallel Vision/OCR unit(s)',
            document_id, page_count, len(subtasks),
        )
        return

    comment_page_idx = [p['page_index'] for p in pages if p['page_type'] == 'comments_sheet']
    io_page_idx      = [p['page_index'] for p in pages if p['page_type'] in ('io_table', 'io_drawing')]

    subtasks = []
    if comment_page_idx:
        subtasks.append(process_io_comments_group.s(document_id, comment_page_idx))
    for idx in io_page_idx:
        subtasks.append(process_io_table_page.s(document_id, idx))

    IOListDocument.objects.filter(id=document_id).update(
        pages_total=len(subtasks), pages_processed=0, current_phase='processing_pages',
        # Reset — a re-extract can switch a document from pid_drawing to
        # io_table (or vice versa) between runs; stale values from a
        # PREVIOUS run's document type must not leak into this one's
        # progress display.
        vision_calls_total=0, vision_calls_done=0, tokens_used_total=0,
        provisional_rows_count=0, provisional_comments_count=0,
    )

    if not subtasks:
        # Nothing to extract (e.g. an all-cover/notes PDF) — finalize
        # immediately with empty results rather than dispatching an empty chord.
        finalize_io_document.delay([], document_id, started)
        return

    chord(subtasks)(finalize_io_document.s(document_id, started))
    logger.info(
        '[IOWF] Dispatched %d parallel unit(s) for document %s '
        '(%d comment page(s) as 1 unit, %d io page(s) as %d units)',
        len(subtasks), document_id, len(comment_page_idx), len(io_page_idx), len(io_page_idx),
    )


@shared_task(
    bind=True,
    name='instrument_io_workflow.process_comments_group',
    max_retries=1,
    default_retry_delay=15,
    soft_time_limit=300,
    time_limit=360,
)
def process_io_comments_group(self, document_id: str, page_indices: list) -> dict:
    """All comments_sheet pages together, in page order — see module
    docstring for why this unit can't be split per-page."""
    from .models import IOListDocument
    from .services.comment_table_extractor import extract_comments_from_pages

    result: dict[str, Any] = {'comments': [], 'io_rows': []}
    try:
        document = IOListDocument.objects.get(id=document_id)
        with document.pdf_file.open('rb') as f:
            pdf_bytes = f.read()
        result['comments'] = extract_comments_from_pages(pdf_bytes, page_indices)
        _persist_provisional_rows_and_comments(document_id, [], result['comments'])
    except Exception:
        logger.exception(
            '[IOWF] process_io_comments_group failed for document %s (pages %s)',
            document_id, page_indices,
        )
    finally:
        IOListDocument.objects.filter(id=document_id).update(
            pages_processed=F('pages_processed') + 1,
        )
    return result


@shared_task(
    bind=True,
    name='instrument_io_workflow.process_table_page',
    max_retries=1,
    default_retry_delay=15,
    soft_time_limit=120,
    time_limit=180,
)
def process_io_table_page(self, document_id: str, page_index: int) -> dict:
    """One io_table/io_drawing page — fully independent of every other
    page, safe to run in parallel."""
    from .models import IOListDocument
    from .services.io_table_extractor import extract_io_rows_from_pages

    result: dict[str, Any] = {'comments': [], 'io_rows': []}
    try:
        document = IOListDocument.objects.get(id=document_id)
        with document.pdf_file.open('rb') as f:
            pdf_bytes = f.read()
        result['io_rows'] = extract_io_rows_from_pages(pdf_bytes, [page_index])
        _persist_provisional_rows_and_comments(document_id, result['io_rows'], [])
    except Exception:
        logger.exception(
            '[IOWF] process_io_table_page failed for document %s page %d',
            document_id, page_index,
        )
    finally:
        IOListDocument.objects.filter(id=document_id).update(
            pages_processed=F('pages_processed') + 1,
        )
    return result


@shared_task(
    bind=True,
    name='instrument_io_workflow.process_pid_vision_page',
    max_retries=1,
    default_retry_delay=15,
    # Must stay comfortably above pid_vision_extractor.VISION_REQUEST_TIMEOUT_S
    # (150s) — otherwise Celery kills this task before the HTTP client's
    # own timeout would even fire, turning a slow-but-fine Vision call
    # into an artificial task failure. Extra headroom on top accounts for
    # the local-OCR fallback that can still run afterward within this same
    # task if the Vision call itself fails.
    soft_time_limit=840,
    time_limit=900,
)
def process_pid_vision_page(
    self, document_id: str, page_index: int,
    vision_provider: str | None, vision_api_key: str | None,
    thorough: bool = False,
) -> dict:
    """One P&ID drawing page — fully independent of every other page,
    safe to run in parallel. BYOK Vision when a key was supplied; local
    OCR for this one page otherwise, or if the Vision call itself fails
    (same degrade-gracefully rule the synchronous path uses — a bad key
    or a transient provider error should never take out one page's
    results, let alone the whole document's).

    thorough: Quick/Thorough Scan toggle, passed straight through to
    extract_pid_tags_from_page — ignored by the local-OCR fallback (that
    path has no tiling concept).

    Dedup against every other page's output happens once, centrally, in
    finalize_io_document — a single page task has no visibility into what
    any other parallel page task found, so it can't dedupe against them
    here."""
    from .models import IOListDocument
    from .services.io_table_extractor import extract_pid_drawing_row_for_page_via_local_ocr

    result: dict[str, Any] = {'io_rows': []}
    try:
        document = IOListDocument.objects.get(id=document_id)
        with document.pdf_file.open('rb') as f:
            pdf_bytes = f.read()

        if vision_provider and vision_api_key:
            try:
                from .services.pid_vision_extractor import extract_pid_tags_from_page

                def _on_call_complete(tokens_used=0):
                    # Real, fine-grained tick — one genuine Vision API
                    # call just finished (see extract_pid_tags_from_page's
                    # on_call_complete docstring). Atomic F() increment,
                    # same safe pattern as pages_processed below — correct
                    # whether pages run one at a time (EAGER-mode dev) or
                    # truly in parallel (real Celery workers). tokens_used
                    # is that one call's own real token count (0 if the
                    # call raised before a response came back) — summed
                    # into a genuine running total, not estimated.
                    IOListDocument.objects.filter(id=document_id).update(
                        vision_calls_done=F('vision_calls_done') + 1,
                        tokens_used_total=F('tokens_used_total') + tokens_used,
                    )

                result['io_rows'] = extract_pid_tags_from_page(
                    pdf_bytes, page_index, vision_provider, vision_api_key, thorough=thorough,
                    on_call_complete=_on_call_complete,
                )
            except Exception as exc:  # noqa: BLE001
                status = getattr(exc, 'status_code', None) or getattr(exc, 'http_status', None)
                logger.error(
                    '[IOWF] process_pid_vision_page: Vision call FAILED for document %s page %d: '
                    '%s: %s (status=%s) — falling back to local OCR for this page',
                    document_id, page_index + 1, type(exc).__name__, exc, status, exc_info=True,
                )
                result['io_rows'] = extract_pid_drawing_row_for_page_via_local_ocr(pdf_bytes, page_index)
        else:
            result['io_rows'] = extract_pid_drawing_row_for_page_via_local_ocr(pdf_bytes, page_index)
        _persist_provisional_rows_and_comments(document_id, result['io_rows'], [])
    except Exception:
        logger.exception(
            '[IOWF] process_pid_vision_page failed outright for document %s page %d',
            document_id, page_index,
        )
    finally:
        IOListDocument.objects.filter(id=document_id).update(
            pages_processed=F('pages_processed') + 1,
        )
    return result


@shared_task(
    bind=True,
    name='instrument_io_workflow.finalize_document',
    max_retries=1,
    default_retry_delay=15,
    soft_time_limit=180,
    time_limit=900,
)
def finalize_io_document(
    self, unit_results: list, document_id: str, started: float,
    document_type: str = 'io_list', thorough: bool = False,
    vision_key_supplied: bool = False,
) -> None:
    """Celery chord callback — combines every subtask's output, links
    comments<->rows, runs the legend check, persists. Mirrors
    apps.pid_verification.tasks.finalize_pid_document's role for this
    app. `unit_results` is populated automatically by Celery with the
    chord group's return values; document_id/started/document_type are
    the extra bound args set by process_io_document (see its own
    docstring for why `started` is passed explicitly rather than read
    from a DB timestamp). document_type defaults to 'io_list' so the
    existing table/comments pipeline's dispatch call (which doesn't pass
    it) keeps working unchanged.

    For document_type == 'pid_drawing': each process_pid_vision_page
    subtask only sees its own one page, so it can't dedupe against any
    other page's output the way the synchronous whole-document loop in
    pid_vision_extractor.extract_pid_tags_via_vision does as it goes —
    that dedup pass runs here instead, once, over the combined set from
    every page."""
    from .models import IOListDocument
    from .services.page_classifier import classify_pages
    from .services.orchestrator import combine_and_finalize, persist_extraction, sha256_of
    from .services.results_cache import save_results_cache

    try:
        document = IOListDocument.objects.get(id=document_id)
    except IOListDocument.DoesNotExist:
        logger.error('[IOWF] finalize_io_document: document %s not found', document_id)
        return

    try:
        with document.pdf_file.open('rb') as f:
            pdf_bytes = f.read()

        # Re-classify (cheap, pure PyMuPDF text triage) rather than thread
        # the classification result through the chord's argument-passing —
        # simpler and keeps every stage self-contained.
        pages = classify_pages(pdf_bytes)
        digest = sha256_of(pdf_bytes)

        comments: list = []
        io_rows: list = []
        for unit in (unit_results or []):
            comments.extend(unit.get('comments') or [])
            io_rows.extend(unit.get('io_rows') or [])
        # Deterministic order regardless of which parallel subtask finished
        # first.
        io_rows.sort(key=lambda r: (r.get('page_number') or 0))

        warnings: list = []
        if document_type == 'pid_drawing':
            from .services.pid_vision_extractor import (
                _dedupe_rows, _backfill_missing_unit_prefix_across_rows,
                _warn_about_remaining_unprefixed_tags, _warn_about_tags_mentioned_only_in_location,
            )
            io_rows = _dedupe_rows(io_rows)
            # Each process_pid_vision_page subtask already ran the
            # per-page backfill on its own single page in isolation — this
            # is the document-wide pass across every page's combined rows,
            # the ONLY place it can run for this fan-out path (no single
            # subtask ever sees another page's results). See that
            # function's own docstring for why per-page evidence alone
            # isn't enough (e.g. a unit-prefix note only legible on one
            # page of the set).
            _backfill_missing_unit_prefix_across_rows(io_rows)
            _warn_about_remaining_unprefixed_tags(io_rows)
            _warn_about_tags_mentioned_only_in_location(io_rows)
            # BUG FIX: this used to only check whether any FOUND row's
            # remarks mentioned "local OCR" — a real, confirmed symptom
            # from a live upload: a P&ID drawing uploaded with no API key
            # where local OCR found ZERO tags on every page (a real,
            # possible outcome — local OCR is much weaker than Vision on a
            # dense/complex drawing) produced an EMPTY io_rows, so
            # any(...) over it was vacuously False and no warning ever
            # appeared — silently 0 results with no explanation exactly
            # when the user needed the explanation most. vision_key_
            # supplied (set at dispatch, from whether the upload actually
            # included a provider+key) is the real, direct signal instead:
            # no key was supplied at all -> every page used local OCR,
            # regardless of how many tags it happened to find. Still also
            # covers the narrower case a key WAS supplied but individual
            # pages fell back to local OCR after their own Vision call
            # failed.
            if not vision_key_supplied:
                warnings = [
                    'No API key was provided — this P&ID drawing was extracted using basic OCR only. '
                    'Add a Claude or OpenAI API key and re-extract for much better accuracy.'
                ]
            elif any('local OCR' in (r.get('remarks') or '') for r in io_rows):
                warnings = ['Add an API key for better accuracy — some pages used local OCR']

        result = combine_and_finalize(
            digest, pages, comments, io_rows,
            document.uploaded_by, started,
            document_type=document_type, warnings=warnings,
            document_id=document_id,
        )
        # Fold in the two P&ID-Vision-specific numbers persist_extraction
        # below saves verbatim into document.extraction_stats (it spreads
        # result['stats'] as-is) — the metadata tab's "Extraction Details"
        # card reads them from there once the document is completed.
        # scan_mode only for a P&ID drawing (meaningless for a plain I/O
        # List table document — no Vision call, no scan mode). tokens_used
        # comes straight from document.tokens_used_total — the real,
        # cumulative count tasks.py's process_pid_vision_page has been
        # incrementing throughout this exact run (see that field's own
        # model comment) — read fresh here rather than trusted from
        # `document` (loaded before the chord ran, so it would otherwise
        # still show 0).
        if document_type == 'pid_drawing':
            result['stats']['scan_mode'] = 'thorough' if thorough else 'quick'
        result['stats']['tokens_used_total'] = IOListDocument.objects.filter(
            id=document_id,
        ).values_list('tokens_used_total', flat=True).first() or 0
        persist_extraction(document, result)
        # Clear the phase now that persist_extraction has just set
        # status='completed' — current_phase is only meaningful while
        # status == 'extracting'; leaving a stale value behind would be a
        # real field showing something that's no longer true.
        IOListDocument.objects.filter(id=document_id).update(current_phase='')
        # Non-fatal cache snapshot — same as views.py's create()/
        # re_extract() call, mirrored here for the async/Celery fan-out
        # path so a large document (over PAGE_FANOUT_THRESHOLD pages)
        # gets the same fast-viewing cache as a small one.
        save_results_cache(document, digest, 'thorough' if thorough else 'quick')
        logger.info(
            '[IOWF] finalize_io_document complete for document %s (%s): '
            'comments=%d io_rows=%d',
            document_id, document_type, len(comments), len(io_rows),
        )
    except Exception as exc:
        logger.exception('[IOWF] finalize_io_document failed for document %s', document_id)
        IOListDocument.objects.filter(id=document_id).update(
            status='failed', extraction_error=f'Extraction failed: {exc}', current_phase='',
        )
