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
    check_cache: bool = False,
    compare_best: bool = False,
) -> None:
    """Single entry point views.py's create()/re_extract()/
    force_fresh_extract() use to kick off extraction — hides the
    EAGER-mode-dev vs real-broker split so no caller needs to know which
    one is active.

    check_cache/compare_best: threaded straight through to
    process_io_document — see its own docstring. Real Vision extraction
    isn't fully deterministic run-to-run on the same file/scan-mode
    (confirmed live), so there are now three distinct actions:
      - create() (Upload & Extract): check_cache=True, compare_best=False
        — a fresh upload may reuse a prior identical-file+scan-mode run.
      - re_extract() (Re-extract): check_cache=True, compare_best=False
        — deliberately the FAST, CONSISTENT action now: prefers the cache
        over running Vision again, same reasoning as create().
      - force_fresh_extract() (Force Fresh Analysis): check_cache=False,
        compare_best=True — always runs genuinely fresh, then keeps
        whichever of this run or the existing cache actually has more
        rows, updating the cache only when the fresh run is at least as
        good.
    Both default to False so any other/future caller doesn't accidentally
    opt into either behaviour without explicitly choosing to.

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
        process_io_document.delay(
            document_id, vision_provider, vision_api_key, thorough, check_cache, compare_best,
        )
        return

    import threading
    from django.db import connection

    def _run():
        try:
            process_io_document.delay(
                document_id, vision_provider, vision_api_key, thorough, check_cache, compare_best,
            )
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
    thorough: bool = False, check_cache: bool = False, compare_best: bool = False,
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

    check_cache: only meaningful once this document turns out to be a
    P&ID drawing — if True, look up services.results_cache.
    load_vision_results_cache(pdf_sha256, scan_mode, extraction_mode)
    BEFORE dispatching any Vision/OCR work (extraction_mode — 'ocr' or
    'vision_<provider>' — computed from whether a key was actually
    supplied for THIS request, so a keyed request can never read back a
    weaker no-key run's cache, or vice versa); a hit skips the whole
    chord and finalizes
    straight from the cached rows (see the pid_drawing branch below).
    create() passes True; re_extract() always passes False (see
    results_cache.py's own top docstring, section 2, for why this exists
    and the incident history behind not letting this get too broad).
    Ignored entirely for a plain I/O List table document — never cached.

    compare_best: threaded straight through to finalize_io_document — see
    its own docstring. force_fresh_extract() passes True alongside
    check_cache=False (always run genuinely fresh, then keep whichever
    of this run or the existing cache is better); create()/re_extract()
    never pass True.

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
        scan_mode = 'thorough' if thorough else 'quick'

        # BUG FIX: extraction_mode ('ocr' vs 'vision_<provider>') must be
        # part of the cache lookup, not just scan_mode — otherwise a fresh
        # upload WITH a newly-supplied, valid API key could still return a
        # WEAKER prior run's local-OCR result for this same file, since
        # nothing previously distinguished an OCR-produced cache entry
        # from a Vision-produced one. See results_cache.py's
        # vision_extraction_mode() docstring for the full incident.
        from .services.results_cache import vision_extraction_mode
        key_supplied = bool(vision_provider and vision_api_key)
        extraction_mode = vision_extraction_mode(vision_provider, key_supplied)

        # BUG FIX: local-OCR results (no API key) are low-quality and often
        # empty — not worth caching or reading back at all. 'ocr' never
        # participates in this cache in either direction: every no-key
        # upload runs OCR fresh, every time, and its result is never
        # written either (see the unconditional save call in
        # finalize_io_document below, gated the same way).
        if check_cache and extraction_mode != 'ocr':
            from .services.results_cache import load_vision_results_cache
            cached = load_vision_results_cache(document.pdf_sha256, scan_mode, extraction_mode)
            if cached is not None:
                logger.info(
                    '[IOWF] Vision cache HIT for document %s (hash=%s scan_mode=%s extraction_mode=%s, '
                    '%d cached rows) — skipping Vision/OCR entirely for this fresh upload',
                    document_id, (document.pdf_sha256 or '')[:12], scan_mode, extraction_mode, len(cached['io_rows']),
                )
                IOListDocument.objects.filter(id=document_id).update(
                    pages_total=page_count, pages_processed=page_count, current_phase='finalizing',
                    vision_calls_total=0, vision_calls_done=0, tokens_used_total=0,
                    provisional_rows_count=len(cached['io_rows']), provisional_comments_count=0,
                )
                # Reuses finalize_io_document's own combine/dedupe/persist
                # pipeline unchanged rather than duplicating it — a single
                # synthetic "unit" carrying the cached rows looks exactly
                # like what the real chord's subtasks would have returned.
                finalize_io_document.delay(
                    [{'io_rows': cached['io_rows'], 'comments': []}], document_id, started,
                    document_type='pid_drawing', thorough=thorough,
                    vision_key_supplied=key_supplied, vision_provider=vision_provider,
                )
                return
            logger.info(
                '[IOWF] Vision cache MISS for document %s (hash=%s scan_mode=%s extraction_mode=%s) '
                '— running fresh extraction',
                document_id, (document.pdf_sha256 or '')[:12], scan_mode, extraction_mode,
            )

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
            vision_key_supplied=key_supplied, vision_provider=vision_provider,
            compare_best=compare_best,
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

    # auth_failed distinguishes "key invalid/expired" from any other
    # reason a page fell back to local OCR (rate limit, transient network
    # error, model-not-available...) — see finalize_io_document, which
    # aggregates this across every page to pick the right user-facing
    # message. Real gap this closes: previously an invalid key produced
    # the SAME generic "add an API key" warning as no key at all, even
    # though the user HAD supplied one — just not a working one.
    result: dict[str, Any] = {'io_rows': [], 'auth_failed': False}
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
                if status in (401, 403):
                    result['auth_failed'] = True
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
    vision_key_supplied: bool = False, compare_best: bool = False,
    vision_provider: str | None = None,
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
    every page.

    compare_best: only meaningful for a fresh pid_drawing run (i.e. from
    "Force Fresh Analysis" — views.py's force_fresh_extract(), which is
    the only caller that ever passes True). Real Vision extraction isn't
    fully deterministic run-to-run on the same file (confirmed live: the
    same drawing, same Thorough Scan config, gave 1010 tags one run and
    902 another) — this compares this run's just-finished row count
    against whatever's ALREADY cached for this exact (file_hash,
    scan_mode) and keeps whichever is better, so the cache — and the
    document the user is looking at — only ever gets BETTER over
    repeated fresh runs, never regresses to a worse one. "Re-extract"
    (check_cache=True, no compare_best) deliberately does NOT do this —
    it's meant to be the FAST, consistent, cache-preferring action;
    comparing/possibly-discarding a fresh run is only for the action
    that explicitly asks to run fresh."""
    from .models import IOListDocument
    from .services.page_classifier import classify_pages
    from .services.orchestrator import combine_and_finalize, persist_extraction, sha256_of
    from .services.results_cache import save_results_cache, save_vision_results_cache

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
        any_auth_failed = False
        for unit in (unit_results or []):
            comments.extend(unit.get('comments') or [])
            io_rows.extend(unit.get('io_rows') or [])
            if unit.get('auth_failed'):
                any_auth_failed = True
        # Deterministic order regardless of which parallel subtask finished
        # first.
        io_rows.sort(key=lambda r: (r.get('page_number') or 0))

        warnings: list = []
        if document_type == 'pid_drawing':
            from .services.pid_vision_extractor import (
                _dedupe_rows, _backfill_missing_unit_prefix_across_rows,
                _warn_about_remaining_unprefixed_tags, _warn_about_tags_mentioned_only_in_location,
                _drop_empty_rows,
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
            # See _drop_empty_rows's own docstring — this fan-out path
            # (large documents, one Celery subtask per page) has its own
            # combine step separate from pid_vision_extractor's single-
            # process entry point, so the filter has to be applied here
            # too, not just there.
            io_rows = _drop_empty_rows(io_rows)
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
                    'Add Claude/OpenAI API key for P&ID Vision extraction. Without key, '
                    'basic OCR only (lower accuracy).'
                ]
            elif any_auth_failed:
                # Distinct from the "no key" message above and the
                # generic local-OCR one below — a key WAS supplied here,
                # it just didn't work (expired, revoked, typo'd, wrong
                # provider). Telling the user "add a key" when they
                # already added one is actively misleading; this is the
                # real gap that produced.
                warnings = [
                    'API key invalid or expired. Please check your key and try again.'
                ]
            elif any('local OCR' in (r.get('remarks') or '') for r in io_rows):
                warnings = ['Add an API key for better accuracy — some pages used local OCR']

            # See finalize_io_document's own docstring (compare_best
            # param) for why this exists. Compares THIS run's fresh
            # io_rows against whatever's already cached for this exact
            # (file_hash, scan_mode) — never against a different scan
            # mode or a different file — and keeps whichever is better.
            # A missing cache entry (first-ever run for this file+mode)
            # just falls through with the fresh result and no message,
            # same as if compare_best were False.
            scan_mode_for_compare = 'thorough' if thorough else 'quick'
            # Same extraction_mode dimension as process_io_document's
            # check_cache lookup — compare_best must only ever compare a
            # fresh Vision run against a PREVIOUS Vision run's cache (or a
            # fresh OCR run against a previous OCR run's), never across
            # the two, for the same reason described in
            # results_cache.vision_extraction_mode()'s docstring.
            from .services.results_cache import vision_extraction_mode
            extraction_mode_for_compare = vision_extraction_mode(vision_provider, vision_key_supplied)
            # 'ocr' is never cached (see the BUG FIX comment in
            # process_io_document) — nothing to compare a fresh no-key run
            # against, and Force Fresh Analysis on OCR wouldn't mean much
            # anyway (there's no Vision non-determinism to guard against).
            if compare_best and extraction_mode_for_compare != 'ocr':
                from .services.results_cache import load_vision_results_cache
                existing_cached = load_vision_results_cache(digest, scan_mode_for_compare, extraction_mode_for_compare)
                if existing_cached is not None:
                    cached_rows = existing_cached['io_rows']
                    fresh_count, cached_count = len(io_rows), len(cached_rows)
                    if fresh_count < cached_count:
                        logger.info(
                            '[IOWF] compare_best: fresh run (%d rows) worse than cached '
                            '(%d rows) for document %s — keeping cached result',
                            fresh_count, cached_count, document_id,
                        )
                        io_rows = cached_rows
                        warnings = [
                            f'Cached result is better ({cached_count} rows vs {fresh_count} rows). '
                            f'Keeping cached result!'
                        ] + warnings
                    elif fresh_count > cached_count:
                        logger.info(
                            '[IOWF] compare_best: fresh run (%d rows) better than cached '
                            '(%d rows) for document %s — updating cache',
                            fresh_count, cached_count, document_id,
                        )
                        warnings = [
                            f'Better result found! Updated from {cached_count} to {fresh_count} rows'
                        ] + warnings
                    # fresh_count == cached_count: keep the fresh rows and
                    # let the unconditional save_vision_results_cache call
                    # below refresh the cache's timestamp — no message,
                    # same row count either way.

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
        # Separate, hash+scan_mode-keyed cache (see results_cache.py's own
        # top docstring, section 2) — lets a FUTURE fresh upload of this
        # exact file skip Vision entirely, regardless of which project or
        # document record it lands under. Written unconditionally after
        # every successful pid_drawing completion, whether this run
        # actually called Vision or itself came from a cache hit
        # (re-saving identical data is harmless) — never for a plain
        # io_list table document (nothing to cache; no Vision call ever
        # happens there), and never for a no-key local-OCR run either —
        # OCR quality is unreliable enough (often 0 rows) that caching it
        # would just mean the NEXT no-key upload of this file silently
        # reuses that same low-quality result instead of trying OCR fresh.
        if document_type == 'pid_drawing' and extraction_mode_for_compare != 'ocr':
            save_vision_results_cache(
                digest, 'thorough' if thorough else 'quick', io_rows, warnings,
                extraction_mode=extraction_mode_for_compare,
            )
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
