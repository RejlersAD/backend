"""
Celery Background Task — Electrical Comparison
================================================
Follows the same "Celery task does the actual work, a status endpoint
reports progress via polling" pattern as
apps.pid_verification_v2.tasks.process_pid_document and its
upload_pid view counterpart (apps.pid_verification_v2.views).

process_electrical_comparison(job_id, context) does everything
UploadComparisonView.post() used to do synchronously, inline, after
file validation: AI Vision extraction of the P&ID (if any), Equipment/
Load List parsing, the up-to-3 comparisons, and persisting results —
updating job.current_stage / job.pages_done / job.pages_total at each
step so JobStatusView can report real progress to the frontend.

`context` carries everything the task needs that isn't already a column
on ElectricalComparisonJob — the uploaded files' temp paths (written to
disk by UploadComparisonView._save_uploaded_file_to_temp, since an
in-memory UploadedFile object from the original request cannot cross
into a separate Celery worker process) plus api_key/provider/model:
{
    'pid_file_path': str | None,
    'equipment_file_path': str | None, 'equipment_file_name': str,
    'load_list_file_path': str | None, 'load_list_file_name': str,
    'api_key': str, 'provider': str, 'model': str | None,
}

This module imports a few helpers from .views (_collect_comparison_rows,
_build_panel_verification) rather than duplicating that logic — views.py
in turn lazy-imports THIS module's task function inside its own post()
method, so neither side imports the other at module-load time and there
is no circular import.
"""
import gc
import logging
import os
import time

from celery import shared_task
from django.db import connection

logger = logging.getLogger(__name__)

# Per-page DB-save resilience — a long multi-page run (Vision calls
# taking several seconds each, plus retry backoff) can leave the DB
# connection idle long enough for a managed Postgres instance to drop it
# server-side between saves; reporting progress to the frontend is a
# nice-to-have, never worth crashing an otherwise-successful extraction
# over, so saves here are both defensive (reconnect-if-needed) and
# non-fatal (log and keep going if the save itself still fails).
# _DB_RECONNECT_EVERY_N_PAGES proactively closes (Django transparently
# reopens on next use) the connection on a schedule, instead of only
# reacting after a save already failed.
_DB_RECONNECT_EVERY_N_PAGES = 5
# Each page holds a rendered PIL image (at ELECTRICAL_VISION_RENDER_DPI
# — 400 — these are large), its base64 encoding, and the Vision
# response — same cadence as the DB reconnect above, run a forced
# garbage-collection pass periodically rather than waiting for Python's
# own generational GC to get around to it on a long multi-page run.
_GC_EVERY_N_PAGES = 5
# Progress is WRITTEN to the DB after every page (always still written
# on the very last page, even though that's now redundant with N=1) —
# pages_done/current_stage/raw_text_per_page are updated in-memory on
# the job object every page regardless of this value; it only throttles
# how often that save round-trips to the database. Was 3 (throttled) —
# changed to 1 so the frontend's poll loop can observe pages_done
# advancing after every single page instead of in batches of 3, which
# otherwise reads as the progress bar "skipping" between poll ticks.
_DB_SAVE_EVERY_N_PAGES = 1


def _save_job_progress(job, update_fields):
    """Ensures the DB connection is alive before saving, and never lets a
    save failure itself crash the whole job — logs it and keeps going."""
    try:
        connection.ensure_connection()
        job.save(update_fields=update_fields)
    except Exception as exc:  # noqa: BLE001
        logger.error('[ElecCompareTask] Failed to save job progress: %s', exc)

# Per-page Vision call resilience (FIX 1/2) — a transient provider error
# (rate limit, timeout, momentary 5xx) on ONE page must never sink an
# otherwise-successful multi-page run. _VISION_RETRY_ATTEMPTS is retries
# AFTER the first attempt (so 2 → 3 total attempts per page);
# _VISION_RETRY_DELAY_S is the pause between attempts.
_VISION_RETRY_ATTEMPTS = 2
_VISION_RETRY_DELAY_S = 2
# Small pause after every SUCCESSFUL Vision call, so N consecutive page
# calls don't land back-to-back with zero spacing and trip the
# provider's own rate limiting.
_INTER_PAGE_DELAY_S = 1

# Stage names — must match SingleLineDiagram.jsx's STAGE_LABELS map
# exactly, since the frontend looks up a display label by this string.
STAGE_UPLOADING = 'uploading'
STAGE_READING_PAGES = 'reading_pages'
STAGE_AI_VISION = 'ai_vision'
STAGE_PARSING_EXCEL = 'parsing_excel'
STAGE_COMPARING = 'comparing'
STAGE_SAVING_RESULTS = 'saving_results'


def _extract_electrical_tags_with_progress(pdf_bytes, api_key, provider, model, job):
    """Same pipeline as apps.electrical_comparison.services.tag_extractor.
    extract_electrical_tags() — re-orchestrated HERE, rather than simply
    calling that function, so job.pages_done can be updated after every
    single page as it's processed. extract_electrical_tags() itself has
    no progress-callback hook (tag_extractor.py was out of scope to
    modify this turn), so this reimplements its top-level page loop
    while reusing every one of its own per-page/per-tag helpers
    UNCHANGED — nothing about page classification, tag validation, or
    false-positive filtering is duplicated here, only the loop that
    walks pages and now also reports progress.
    """
    from .services.tag_extractor import (
        _get_page_count, _classify_page, _call_electrical_vision,
        _classify_tag_string, _extract_candidates_from_text,
        _flag_suspicious_areas, _flag_suspicious_sequences,
        PAGE_TYPE_LEGEND, PAGE_TYPE_SKIP,
    )

    if not (api_key and api_key.strip()):
        raise ValueError('api_key is required for Vision-based tag extraction')

    page_count = _get_page_count(pdf_bytes)
    job.pages_total = page_count
    job.current_stage = STAGE_READING_PAGES
    job.save()

    # First pass — classify every page and collect legend/symbol-key
    # context up front (see tag_extractor.extract_electrical_tags' own
    # docstring for why this has to happen before any drawing page is
    # sent to Vision, not just for pages that come after a legend page).
    page_types = {}
    page_texts = {}
    legend_context_parts = []
    for page_index in range(page_count):
        page_type, text = _classify_page(pdf_bytes, page_index)
        page_types[page_index] = page_type
        page_texts[page_index] = text
        if page_type == PAGE_TYPE_LEGEND:
            legend_context_parts.append(text.strip())
    legend_context = '\n\n'.join(legend_context_parts) if legend_context_parts else None
    legend_pages_found = len(legend_context_parts)

    job.current_stage = STAGE_AI_VISION
    job.pages_done = 0
    job.raw_text_per_page = job.raw_text_per_page or {}
    job.save()

    all_valid_tags = []
    all_invalid_tags = []
    raw_text_parts = []
    seen_tags = set()
    provider_used = provider
    model_used = ''
    total_calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost_usd = 0.0
    pages_skipped = 0

    def _due_for_db_write(idx):
        # Always write on the very last page regardless of the N-page
        # throttle, so the final pages_done/current_stage/
        # raw_text_per_page the DB sees is never stale.
        return idx % _DB_SAVE_EVERY_N_PAGES == 0 or idx == page_count - 1

    for page_index in range(page_count):
        # Proactively close the DB connection on a schedule (Django
        # transparently reopens on next use) rather than only reacting
        # after a save already failed against a stale one.
        if page_index % _DB_RECONNECT_EVERY_N_PAGES == 0:
            connection.close()

        # Free the previous pages' rendered images/responses before
        # starting this one, rather than letting them accumulate across
        # a long multi-page run.
        if page_index % _GC_EVERY_N_PAGES == 0:
            gc.collect()

        page_type = page_types[page_index]

        if page_type == PAGE_TYPE_SKIP:
            pages_skipped += 1
            job.raw_text_per_page[str(page_index)] = '[SKIPPED - administrative page, not sent to Vision]'
            job.pages_done = page_index + 1
            if _due_for_db_write(page_index):
                _save_job_progress(job, ['pages_done', 'current_stage', 'raw_text_per_page'])
            continue

        if page_type == PAGE_TYPE_LEGEND:
            job.raw_text_per_page[str(page_index)] = page_texts[page_index]
            job.pages_done = page_index + 1
            if _due_for_db_write(page_index):
                _save_job_progress(job, ['pages_done', 'current_stage', 'raw_text_per_page'])
            continue

        # FIX 4 — never let one bad page crash the whole job: everything
        # from the Vision call through tag classification for THIS page
        # is wrapped so an unexpected failure anywhere in here skips
        # just this page (job keeps its progress so far) instead of
        # propagating out and failing the entire multi-page run.
        try:
            # FIX 1 — retry the Vision call itself up to
            # _VISION_RETRY_ATTEMPTS times, with a short pause between
            # attempts, before giving up on this page specifically.
            result = None
            vision_exc = None
            for attempt in range(1, _VISION_RETRY_ATTEMPTS + 2):  # 1 initial attempt + N retries
                try:
                    result = _call_electrical_vision(
                        pdf_bytes=pdf_bytes,
                        page_index=page_index,
                        api_key=api_key,
                        provider=provider,
                        model=model,
                        legend_context=legend_context,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    vision_exc = exc
                    if attempt <= _VISION_RETRY_ATTEMPTS:
                        logger.warning(
                            '[ElecCompareTask] Vision call failed for page %d/%d (attempt %d/%d): %s — retrying in %ds',
                            page_index + 1, page_count, attempt, _VISION_RETRY_ATTEMPTS + 1, exc, _VISION_RETRY_DELAY_S,
                        )
                        time.sleep(_VISION_RETRY_DELAY_S)

            if result is None:
                # All retries exhausted — skip this page and continue to
                # the next one rather than crashing the entire job.
                logger.error(
                    '[ElecCompareTask] Page %d/%d failed after %d attempt(s), skipping: %s',
                    page_index + 1, page_count, _VISION_RETRY_ATTEMPTS + 1, vision_exc,
                )
                job.raw_text_per_page[str(page_index)] = f'[FAILED - Vision call failed after retries: {vision_exc}]'
                job.pages_done = page_index + 1
                if _due_for_db_write(page_index):
                    _save_job_progress(job, ['pages_done', 'current_stage', 'raw_text_per_page'])
                continue

            # FIX 2 — small delay after a successful call, so back-to-
            # back page calls don't trip the provider's rate limiting.
            time.sleep(_INTER_PAGE_DELAY_S)

            provider_used = result.get('provider', provider)
            model_used = result.get('model', model_used)

            usage = result.get('token_usage') or {}
            total_calls += usage.get('calls', 1)
            total_input_tokens += usage.get('input_tokens', 0)
            total_output_tokens += usage.get('output_tokens', 0)
            try:
                total_cost_usd += float(usage.get('cost_usd', 0) or 0)
            except (TypeError, ValueError):
                pass

            page_text = result.get('raw_text', '')
            raw_text_parts.append(page_text)
            job.raw_text_per_page[str(page_index)] = page_text

            page_valid = []
            page_invalid = []
            for tag_str in result.get('tags', []):
                # FIX 3 — a malformed entry in the model's own 'tags'
                # list must not abort the whole page; skip just that one
                # tag and keep going.
                try:
                    v, i = _classify_tag_string(tag_str)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        '[ElecCompareTask] Page %d/%d: could not classify tag %r, skipping it: %s',
                        page_index + 1, page_count, tag_str, exc,
                    )
                    continue
                if v:
                    page_valid.append(v)
                if i:
                    page_invalid.append(i)

            text_valid, text_invalid = _extract_candidates_from_text(page_text)
            page_valid.extend(text_valid)
            page_invalid.extend(text_invalid)

            for t in page_valid:
                if t['tag'] in seen_tags:
                    continue
                seen_tags.add(t['tag'])
                all_valid_tags.append(t)
            all_invalid_tags.extend(page_invalid)

            # The point of reimplementing this loop here — real,
            # granular per-page progress, visible to the frontend via
            # JobStatusView polling WHILE extraction is still running
            # (pages_done is always current in-memory; the DB WRITE
            # itself is throttled to every _DB_SAVE_EVERY_N_PAGES pages
            # — see _due_for_db_write — but always flushed on the last
            # page so nothing is ever left stale at the end).
            job.pages_done = page_index + 1
            if _due_for_db_write(page_index):
                _save_job_progress(job, ['pages_done', 'current_stage', 'raw_text_per_page'])
            logger.info(
                '[ElecCompareTask] page %d/%d done (%d valid tag(s) so far)',
                page_index + 1, page_count, len(all_valid_tags),
            )
        except Exception as exc:  # noqa: BLE001
            # Last-resort safety net — anything unexpected that slipped
            # past the more targeted guards above still must not kill
            # the whole job over one page.
            logger.error('[ElecCompareTask] Page %d failed: %s, skipping', page_index + 1, exc)
            job.raw_text_per_page[str(page_index)] = f'[FAILED - unexpected error: {exc}]'
            job.pages_done = page_index + 1
            _save_job_progress(job, ['pages_done', 'current_stage', 'raw_text_per_page'])
            continue

    # FIX 5 — tag_extractor.extract_electrical_tags() calls this after
    # its own page loop, but that function isn't what runs in
    # production (this re-orchestrated loop is — see this function's
    # own docstring); this call was missing here, which meant the real
    # Celery task never flagged suspicious areas/sequences at all.
    all_valid_tags = _flag_suspicious_areas(all_valid_tags)
    all_valid_tags = _flag_suspicious_sequences(all_valid_tags)

    all_valid_tags.sort(key=lambda t: (int(t['area']), t['type_code']))

    return {
        'tags': all_valid_tags,
        'invalid_tags': all_invalid_tags,
        'raw_text': '\n'.join(raw_text_parts),
        'raw_text_per_page': job.raw_text_per_page,
        'provider': provider_used,
        'model': model_used,
        'token_usage': {
            'calls': total_calls,
            'input_tokens': total_input_tokens,
            'output_tokens': total_output_tokens,
            'total_tokens': total_input_tokens + total_output_tokens,
            'cost_usd': str(total_cost_usd),
        },
        'page_count': page_count,
        'pages_skipped': pages_skipped,
        'legend_pages_found': legend_pages_found,
    }


def _cleanup_temp_file(path):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


@shared_task(
    bind=True,
    name='electrical_comparison.process_comparison',
    max_retries=0,
    time_limit=1200,       # 20 min hard limit
    soft_time_limit=1140,  # 19 min soft limit
)
def process_electrical_comparison(self, job_id: str, context: dict = None):
    context = context or {}
    import io

    from apps.pid_verification_v2.services.comparison_engine import compare_with_equipment_list
    from .models import ElectricalComparisonJob, ElectricalComparisonResult
    from .services.tag_extractor import resolve_equipment_type
    from .services.excel_parser import parse_excel_tags, extract_panel_tag_from_load_list
    # Lazy-imported from views.py (not at module top) to avoid a
    # module-load-time circular import — views.py itself lazy-imports
    # this task function inside UploadComparisonView.post().
    from .views import _collect_comparison_rows, _build_panel_verification, _build_combined_comparison

    logger.info('[ElecCompareTask] Starting job_id=%s', job_id)

    try:
        job = ElectricalComparisonJob.objects.get(job_id=job_id)
    except ElectricalComparisonJob.DoesNotExist:
        logger.error('[ElecCompareTask] Job %s not found', job_id)
        return

    pid_file_path = context.get('pid_file_path')
    equipment_file_path = context.get('equipment_file_path')
    equipment_file_name = context.get('equipment_file_name') or ''
    load_list_file_path = context.get('load_list_file_path')
    load_list_file_name = context.get('load_list_file_name') or ''
    api_key = context.get('api_key', '')
    provider = context.get('provider', 'claude')
    model = context.get('model')

    pid_tags = []
    extraction = None

    try:
        # ── AI Vision extraction (only if a P&ID file is present) —
        # isolated in its own try/except, same reasoning as the old
        # inline view code: a ValueError here means the AI Vision call
        # itself failed (bad/missing key, provider rejection); a
        # ValueError from Excel parsing further below is a DIFFERENT
        # failure that must never be mislabeled "AI Vision failed".
        if pid_file_path:
            job.current_stage = STAGE_UPLOADING
            job.save()
            with open(pid_file_path, 'rb') as f:
                pdf_bytes = f.read()
            try:
                extraction = _extract_electrical_tags_with_progress(pdf_bytes, api_key, provider, model, job)
            except ValueError as e:
                job.status = 'failed'
                error_msg = str(e)
                if 'api_key' in error_msg.lower():
                    reason = 'Invalid or missing API key'
                elif 'insufficient' in error_msg.lower():
                    reason = 'Insufficient tokens. Please top up.'
                else:
                    reason = error_msg
                job.error_message = f'AI Vision failed: {reason}. Please check your API key and try again.'
                job.save()
                return
            except Exception as e:
                # BUG FIX (carried over from the original inline view
                # code): a REAL rejected/invalid key reaches Anthropic/
                # OpenAI and fails with an SDK-specific exception here,
                # not the ValueError branch above — this must still
                # surface the real reason, not a generic message.
                job.status = 'failed'
                job.error_message = f'AI Vision failed: {e}. Please check your API key and try again.'
                job.save()
                return

            pid_tags = extraction['tags']
            job.model_used = extraction.get('model', '')
            job.raw_text_per_page = extraction.get('raw_text_per_page', {})
            job.save()
            if not pid_tags:
                job.status = 'failed'
                job.error_message = 'No electrical tags found in this drawing. Please check the PDF quality.'
                job.save()
                return

        # ── Everything from here on (Excel parsing, comparisons, DB
        # save) is NOT an AI Vision concern — a ValueError here must
        # never be mislabeled "AI Vision failed" either.
        job.current_stage = STAGE_PARSING_EXCEL
        job.save()

        equipment_tags = []
        if equipment_file_path:
            with open(equipment_file_path, 'rb') as f:
                equip_bytes = f.read()
            equip_fileobj = io.BytesIO(equip_bytes)
            equip_fileobj.name = equipment_file_name
            equipment_tags = parse_excel_tags(equip_fileobj, 'equipment_list')

        load_list_tags = []
        load_list_panel_info = None
        is_load_list_pdf = bool(load_list_file_path) and load_list_file_name.lower().endswith('.pdf')
        if load_list_file_path:
            with open(load_list_file_path, 'rb') as f:
                load_list_bytes = f.read()
            if is_load_list_pdf:
                load_list_panel_info = extract_panel_tag_from_load_list(load_list_bytes)
            load_fileobj = io.BytesIO(load_list_bytes)
            load_fileobj.name = load_list_file_name
            load_list_tags = parse_excel_tags(load_fileobj, 'load_list')

        job.current_stage = STAGE_COMPARING
        job.save()

        comparisons_done = []
        equipment_results = None
        load_results = None
        eq_vs_ll_results = None
        panel_verification = None

        # FIX 1 — carry 'suspicious'/'suspicious_reason' through alongside
        # 'tag'/'type'. tag_extractor.py's _flag_suspicious_areas/
        # _flag_suspicious_sequences already compute these on each pid_
        # tags entry, but they were being dropped here — compare_with_
        # equipment_list itself only reads 'tag'/'type', so these two
        # extra fields pass through it unused, but views.py's
        # _collect_comparison_rows (FIX 2) looks them up directly off
        # this SAME list (passed to it as primary_tags) to build each
        # saved row's remarks, independent of whatever the comparison
        # engine itself returns.
        pid_equip = [
            {
                'tag': t['tag'],
                'type': t['equipment_type'],
                'suspicious': t.get('suspicious', False),
                'suspicious_reason': t.get('suspicious_reason', ''),
            }
            for t in pid_tags
        ]

        # Comparison 1 — P&ID vs Equipment List
        if pid_file_path and equipment_tags:
            equipment_results = compare_with_equipment_list(pid_equip, equipment_tags)
            comparisons_done.append('pid_vs_equipment')

        # Comparison 2 — P&ID vs Load List
        if pid_file_path and load_list_tags:
            load_results = compare_with_equipment_list(pid_equip, load_list_tags)
            comparisons_done.append('pid_vs_loadlist')

        # Comparison 3 — Equipment List vs Load List (see
        # views._build_panel_verification's own docstring for why this
        # is panel-verification, not a direct tag comparison, when the
        # Load List is a PDF).
        if equipment_tags and load_list_tags:
            if is_load_list_pdf:
                panel_verification = _build_panel_verification(equipment_tags, load_list_tags, load_list_panel_info)
            else:
                eq_vs_ll_results = compare_with_equipment_list(equipment_tags, load_list_tags)
            comparisons_done.append('equipment_vs_loadlist')

        job.current_stage = STAGE_SAVING_RESULTS
        job.save()

        results_to_save = []
        if equipment_results is not None:
            _collect_comparison_rows(job, results_to_save, equipment_results, 'equipment_list', pid_equip, equipment_tags)
        if load_results is not None:
            _collect_comparison_rows(job, results_to_save, load_results, 'load_list', pid_equip, load_list_tags)
        if eq_vs_ll_results is not None:
            _collect_comparison_rows(job, results_to_save, eq_vs_ll_results, 'equipment_vs_loadlist', equipment_tags, load_list_tags)

        if panel_verification is not None:
            for p in panel_verification['panels']:
                results_to_save.append(ElectricalComparisonResult(
                    job=job,
                    tag_number=p['panel_tag'],
                    description=p['description'],
                    status=p['status'],
                    source='equipment_vs_loadlist',
                    equipment_type=resolve_equipment_type(p['panel_tag']),
                    remarks=(
                        f"{p['motor_count']} motor(s) in Load List" if p['motor_count'] is not None
                        else 'No Load List data for this panel'
                    ),
                ))
            for m in panel_verification['motors']:
                results_to_save.append(ElectricalComparisonResult(
                    job=job,
                    tag_number=m['motor_tag'],
                    description=m['description'],
                    status=m['status'],
                    source='equipment_vs_loadlist',
                    equipment_type=resolve_equipment_type(m['motor_tag']),
                    remarks=f"Panel: {m['panel']}" if m['panel'] else 'No panel tag found in Load List',
                ))

        # Tab 4 "Full Comparison" — only when ALL 3 files were uploaded
        # (the actual union/status logic lives in views._build_combined_
        # comparison, imported above; this is just the wiring needed to
        # run it alongside the other 3 comparisons and persist its rows
        # the same way).
        if pid_file_path and equipment_tags and load_list_tags:
            combined_comparison = _build_combined_comparison(pid_tags, equipment_tags, load_list_tags)
            comparisons_done.append('combined')
            for row in combined_comparison['rows']:
                results_to_save.append(ElectricalComparisonResult(
                    job=job,
                    tag_number=row['tag'],
                    description=row['description'],
                    status=row['status'],
                    source='combined',
                    equipment_type=resolve_equipment_type(row['tag']),
                    remarks=row['remarks'],
                ))

        if results_to_save:
            ElectricalComparisonResult.objects.bulk_create(results_to_save)

        job.status = 'completed'
        job.save()
        logger.info(
            '[ElecCompareTask] Completed job_id=%s comparisons=%s rows_saved=%d',
            job_id, comparisons_done, len(results_to_save),
        )

    except ValueError as e:
        job.status = 'failed'
        job.error_message = str(e)
        job.save()
    except Exception as e:
        logger.exception('[ElecCompareTask] Failed job_id=%s: %s', job_id, e)
        job.status = 'failed'
        job.error_message = str(e)
        job.save()
    finally:
        _cleanup_temp_file(pid_file_path)
        _cleanup_temp_file(equipment_file_path)
        _cleanup_temp_file(load_list_file_path)
