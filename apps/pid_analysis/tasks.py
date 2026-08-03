"""
Celery Background Tasks — Equipment List Extraction
====================================================
Handles long-running equipment extraction from single and multi-page P&ID PDFs.
Core extraction functions remain untouched in equipment_analysis_views.py.

Flow (single file):
  1. View validates file, generates upload_id, stores 'processing' in cache.
  2. View dispatches this task via .delay() and returns HTTP 202 immediately.
  3. Task runs extraction (register mode or P&ID drawing mode).
  4. Task writes final result to Redis cache under EQ_RESULT_CACHE_KEY_FMT.
  5. Frontend polls /status/<upload_id>/ every 3 s; when 'completed', fetches
     /results/<upload_id>/ which reads the same cache key.
"""
import base64
import io
import logging
import re

from celery import shared_task
from django.core.cache import cache
from django.core.files.uploadedfile import InMemoryUploadedFile

logger = logging.getLogger(__name__)

# ── Soft-coded task & cache configuration ─────────────────────────────────────
# Single-file limits: allow up to 50-min processing per drawing.
EQ_TASK_SOFT_LIMIT_S    = 3000     # 50 min — soft kill (SoftTimeLimitExceeded raised)
EQ_TASK_HARD_LIMIT_S    = 3600     # 60 min — hard kill (SIGKILL)

# Batch limits: allow for many drawings in a single upload.
EQ_BATCH_SOFT_LIMIT_S   = 7200     # 120 min
EQ_BATCH_HARD_LIMIT_S   = 7800     # 130 min

# How long results survive in Redis before expiry.
EQ_RESULT_CACHE_TTL_S   = 14400    # 4 hours

# Redis cache key format — must match the helper in equipment_analysis_views.py.
EQ_RESULT_CACHE_KEY_FMT = 'eq_analysis:{upload_id}'


# ── Internal helpers (no external state; safe to call from Celery worker) ─────

def _make_inmemory_file(file_bytes: bytes, filename: str) -> InMemoryUploadedFile:
    """Wrap raw bytes in a Django InMemoryUploadedFile for the existing extractors."""
    return InMemoryUploadedFile(
        io.BytesIO(file_bytes), 'file', filename, 'application/pdf', len(file_bytes), None
    )


# ── Soft-coded: PDF repair & validation ───────────────────────────────────────
# When fitz reports 0 pages (corrupted xref / damaged streams) we attempt a
# qpdf repair pass before giving up.  This handles PDFs that were damaged during
# upload or file transfer (e.g. binary bytes mangled by a UTF-8 codec).
_QPDF_REPAIR_TIMEOUT_S = int(60)   # seconds — soft-coded


def _try_qpdf_repair(file_bytes: bytes) -> bytes:
    """
    Attempt to repair a PDF with qpdf --qdf mode.
    Returns repaired bytes on success, or the original bytes if repair fails/
    qpdf is unavailable.  Never raises.
    """
    import subprocess, tempfile, os
    tmp_in  = None
    tmp_out = None
    try:
        # Write to temp file — qpdf works on disk
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(file_bytes)
            tmp_in = f.name
        tmp_out = tmp_in + '_repaired.pdf'

        result = subprocess.run(
            ['qpdf', '--qdf', '--object-streams=disable',
             '--replace-input', '--', tmp_in],
            capture_output=True, timeout=_QPDF_REPAIR_TIMEOUT_S,
        )
        # qpdf uses --replace-input so output is back in tmp_in
        if os.path.exists(tmp_in) and os.path.getsize(tmp_in) > 0:
            with open(tmp_in, 'rb') as f:
                repaired = f.read()
            if len(repaired) > 1024:  # sanity: real PDF > 1 KB
                logger.info('[PDFRepair] qpdf repaired %d → %d bytes (exit=%d)',
                            len(file_bytes), len(repaired), result.returncode)
                return repaired
    except FileNotFoundError:
        logger.debug('[PDFRepair] qpdf not found — skipping repair attempt')
    except subprocess.TimeoutExpired:
        logger.warning('[PDFRepair] qpdf timed out after %ds', _QPDF_REPAIR_TIMEOUT_S)
    except Exception as exc:
        logger.debug('[PDFRepair] qpdf error: %s', exc)
    finally:
        for p in [tmp_in, tmp_out]:
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
    return file_bytes


def _validate_pdf_pages(file_bytes: bytes, filename: str) -> tuple:
    """
    Open the PDF with fitz and return (page_count, file_bytes).
    If page_count == 0, attempts qpdf repair and re-checks.
    Raises ValueError with a user-friendly message if PDF is unreadable.
    """
    import fitz as _fitz
    try:
        doc  = _fitz.open(stream=file_bytes, filetype='pdf')
        count = doc.page_count
        doc.close()
    except Exception as exc:
        raise ValueError(
            f'Unable to open "{filename}" as a PDF: {exc}. '
            f'Please re-export the file from your CAD/document tool.'
        )

    if count == 0:
        logger.warning('[PDFValidate] "%s" opened with 0 pages — attempting qpdf repair', filename)
        repaired = _try_qpdf_repair(file_bytes)
        try:
            doc2   = _fitz.open(stream=repaired, filetype='pdf')
            count2 = doc2.page_count
            doc2.close()
        except Exception:
            count2 = 0

        if count2 > 0:
            logger.info('[PDFValidate] qpdf repair recovered %d page(s) from "%s"', count2, filename)
            return count2, repaired

        # Still 0 — the file is unrecoverable
        raise ValueError(
            f'"{filename}" appears to be a corrupted PDF (0 pages readable). '
            f'This typically happens when the file was transferred incorrectly. '
            f'Please re-export or re-download the PDF from the original source.'
        )

    return count, file_bytes


def _classify_equipment_types(equipment: list, config: dict) -> None:
    """
    Apply tag-prefix → equipment type classification (mutates items in-place).
    Mirrors the classification block in analyze_pid_equipment; kept here so the
    task can run it without re-importing the view function body.
    """
    _desg_codes = config.get('designation_codes', {})
    _prefix_map = config.get('tag_prefix_type_map', {})
    _pfx_keys   = sorted(_prefix_map.keys(), key=len, reverse=True)
    _type_re    = re.compile(r'^([A-Z]{1,4})')
    for _item in equipment:
        _match = _type_re.match(_item.get('tag', ''))
        if _match:
            _pfx   = _match.group(1)
            _desig = next((_prefix_map[pk] for pk in _pfx_keys if _pfx.startswith(pk)), None)
            if _desig and _desig in _desg_codes:
                _item['equipment_type']      = _desig
                _item['equipment_type_name'] = _desg_codes[_desig]['name']
                _item['equipment_category']  = _desg_codes[_desig]['category']
            else:
                _item.setdefault('equipment_type', '')
                _item.setdefault('equipment_type_name', '')
                _item.setdefault('equipment_category', '')


def _persist_to_db(equipment: list, upload_id: str, extraction_mode: str,
                   drawing_ref: str, config: dict) -> None:
    """
    Upsert extracted items to DB (best-effort — failure is logged, never raised).
    Mirrors the DB-persist block in analyze_pid_equipment.
    """
    try:
        from apps.pid_analysis.equipment_analysis_views import _get_equipment_models
        PIDEquipmentType, PIDEquipmentItem = _get_equipment_models()
        _desg_codes  = config.get('designation_codes', {})
        _scalar_keys = {
            'revision', 'description', 'extraction_mode',
            'sl_no', 'tag', 'drawing_ref',
            'equipment_type', 'equipment_type_name', 'equipment_category',
        }
        for _item in equipment:
            _etag  = _item.get('tag', '')
            _edata = {k: v for k, v in _item.items() if k not in _scalar_keys}
            _etype_code = _item.get('equipment_type') or None
            _etype_obj  = None
            if _etype_code:
                _etype_obj, _ = PIDEquipmentType.objects.get_or_create(
                    code=_etype_code,
                    defaults={
                        'name':        _desg_codes.get(_etype_code, {}).get('name', _etype_code),
                        'category':    _desg_codes.get(_etype_code, {}).get('category', 'MISC'),
                        'is_rotating': bool(_desg_codes.get(_etype_code, {}).get('rotating', False)),
                    },
                )
            PIDEquipmentItem.objects.update_or_create(
                upload_id=upload_id,
                tag=_etag,
                defaults={
                    'drawing_ref':     drawing_ref,
                    'revision':        _item.get('revision', ''),
                    'description':     _item.get('description', ''),
                    'extraction_mode': extraction_mode,
                    'equipment_type':  _etype_obj,
                    'data':            _edata,
                },
            )
        logger.info('[EQTask] DB: saved %d items (upload_id=%s)', len(equipment), upload_id)
    except Exception as exc:
        logger.warning('[EQTask] DB save warning (non-fatal): %s', exc)


# ── Soft-coded: multi-page P&ID threshold ─────────────────────────────────────
# PDFs with more than EQ_MULTIPAGE_THRESHOLD pages trigger the per-page path.
# 1 = any multi-page PDF is processed page by page (recommended for P&ID sheets).
EQ_MULTIPAGE_THRESHOLD = 1


# ── Page-by-page extraction helper ────────────────────────────────────────────
# Shared by both single-file and batch tasks.
# Core extraction functions in equipment_analysis_views.py are NOT modified —
# only the orchestration layer (how pages/files are looped) changes here.

def _process_pid_pages(file_bytes: bytes, filename: str, config: dict,
                       set_progress=None) -> tuple:
    """
    Full extraction pipeline for a single PDF.

    1. Register-table mode  — handles multi-page registers natively (unchanged).
    2. P&ID drawing mode    — iterates pages one by one when PDF has > 1 page,
       runs AI gap-fill and title-block revision per page, then cross-page dedup.

    Returns: (equipment_list, drawing_ref, extraction_mode)
    Core extraction functions in equipment_analysis_views.py are unchanged.
    """
    from apps.pid_analysis.equipment_analysis_views import (
        _extract_equipment_register_rows,
        _extract_text_from_pdf,
        _extract_titleblock_dwg_no_by_coords,
        _extract_titleblock_dwg_no,
        _extract_titleblock_revision,
        _extract_equipment_items,
        _pid_item_to_register_schema,
        _ai_gap_fill_pid_items,
        _dedup_equipment_by_tag,
        _REVISION_USE_TOPMOST,
    )

    ext_cfg     = config.get('extraction', {})
    drawing_ref = filename.rsplit('.', 1)[0]

    # ── Validate PDF + attempt repair if 0 pages ────────────────────────────
    # Raises ValueError (caught by caller) on unrecoverable files.
    if set_progress:
        set_progress(8, 'Validating PDF…')
    _page_count, file_bytes = _validate_pdf_pages(file_bytes, filename)
    logger.info('[EQPages] "%s" — %d page(s) detected', filename, _page_count)

    pid_file = _make_inmemory_file(file_bytes, filename)

    # ── Stage 1: Equipment Register detection ────────────────────────────────
    if set_progress:
        set_progress(15, 'Scanning for equipment register table…')
    equipment       = _extract_equipment_register_rows(pid_file, config)
    extraction_mode = 'register'

    if equipment is None:
        extraction_mode = 'pid_drawing'
        _tb_rev_enabled = bool(ext_cfg.get('titleblock_revision_enabled', True))

        # _page_count already set by _validate_pdf_pages above — no re-open needed.

        if _page_count <= EQ_MULTIPAGE_THRESHOLD:
            # ── Single-page path: original behaviour unchanged ──────────────
            pid_file.seek(0)
            if set_progress:
                set_progress(30, 'Running OCR on P&ID drawing…')
            text = _extract_text_from_pdf(pid_file, config)

            _coord_dwg_no = ''
            try:
                pid_file.seek(0)
                _coord_dwg_no = _extract_titleblock_dwg_no_by_coords(pid_file.read())
            except Exception:
                pass
            _tb_dwg_no = _coord_dwg_no or _extract_titleblock_dwg_no(text)
            if _tb_dwg_no:
                drawing_ref = _tb_dwg_no

            if set_progress:
                set_progress(50, 'Extracting equipment items…')
            raw_items = _extract_equipment_items(text, drawing_ref, config)
            equipment = [_pid_item_to_register_schema(item) for item in raw_items]

            if equipment and text:
                if set_progress:
                    set_progress(70, 'Running AI gap-fill…')
                equipment = _ai_gap_fill_pid_items(equipment, text, config)

            if _REVISION_USE_TOPMOST and _tb_rev_enabled:
                _doc_rev = _extract_titleblock_revision(text)
                if _doc_rev:
                    for _item in equipment:
                        _item['revision'] = _doc_rev

        else:
            # ── Multi-page path: process each P&ID page independently ───────
            logger.info(
                '[EQPages] Multi-page PDF (%d pages) — processing page by page', _page_count
            )
            _all_items:        list = []
            _all_drawing_refs: list = []

            for _pg in range(_page_count):
                _pct = int(20 + _pg / _page_count * 60)
                _msg = f'Processing P&ID page {_pg + 1} / {_page_count}…'
                if set_progress:
                    set_progress(_pct, _msg)

                pid_file.seek(0)
                _page_text      = _extract_text_from_pdf(pid_file, config, _page_index=_pg)
                _pg_drawing_ref = (
                    _extract_titleblock_dwg_no(_page_text)
                    or f'{drawing_ref}_P{_pg + 1}'
                )

                _raw      = _extract_equipment_items(_page_text, _pg_drawing_ref, config)
                _pg_items = [_pid_item_to_register_schema(item) for item in _raw]

                # AI gap-fill per page (same as single-page path)
                if _pg_items and _page_text:
                    _pg_items = _ai_gap_fill_pid_items(_pg_items, _page_text, config)

                # Title-block revision per page
                if _REVISION_USE_TOPMOST and _tb_rev_enabled:
                    _pg_rev = _extract_titleblock_revision(_page_text)
                    if _pg_rev:
                        for _item in _pg_items:
                            _item['revision'] = _pg_rev

                _all_items.extend(_pg_items)
                if _pg_drawing_ref not in _all_drawing_refs:
                    _all_drawing_refs.append(_pg_drawing_ref)

                logger.info(
                    '[EQPages] Page %d/%d: %d items (ref=%s)',
                    _pg + 1, _page_count, len(_pg_items), _pg_drawing_ref,
                )

            # Dedup across pages by tag — richest extraction per tag wins
            equipment   = _dedup_equipment_by_tag(_all_items)
            drawing_ref = ', '.join(_all_drawing_refs)
            logger.info(
                '[EQPages] Multi-page dedup: %d raw → %d unique items',
                len(_all_items), len(equipment),
            )

    return equipment or [], drawing_ref, extraction_mode


# ── Celery Tasks ───────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name='pid_analysis.run_equipment_analysis',
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=EQ_TASK_SOFT_LIMIT_S,
    time_limit=EQ_TASK_HARD_LIMIT_S,
)
def run_equipment_analysis_task(self, upload_id: str, file_b64: str, filename: str):
    """
    Async Celery task: extract equipment list from a single P&ID PDF.
    Supports both single-page and multi-page PDFs (processed page by page via
    _process_pid_pages helper — core extraction functions are unchanged).

    Args:
        upload_id: Unique identifier returned to the frontend as polling key.
        file_b64:  Base-64 encoded PDF bytes.
        filename:  Original filename (used as fallback drawing reference).

    Writes final result dict to Redis cache so Django web workers can serve it
    via the status and results endpoints without sharing in-process memory.
    """
    from apps.pid_analysis.equipment_analysis_views import _load_config

    cache_key = EQ_RESULT_CACHE_KEY_FMT.format(upload_id=upload_id)

    def _set_progress(pct: int, msg: str) -> None:
        cache.set(cache_key,
                  {'status': 'processing', 'progress': pct, 'message': msg},
                  EQ_RESULT_CACHE_TTL_S)

    logger.info('[EQTask] Starting  upload_id=%s  file=%s', upload_id, filename)
    _set_progress(5, 'Initialising extraction…')

    try:
        file_bytes = base64.b64decode(file_b64)
        config     = _load_config()

        # ── Full pipeline: register → single-page P&ID → multi-page P&ID ────
        equipment, drawing_ref, extraction_mode = _process_pid_pages(
            file_bytes, filename, config, set_progress=_set_progress,
        )

        # ── Numbering & drawing reference ────────────────────────────────────
        for idx, item in enumerate(equipment, 1):
            if not item.get('sl_no'):
                item['sl_no'] = str(idx)
            item['drawing_ref'] = drawing_ref

        # ── Equipment type classification ────────────────────────────────────
        _set_progress(90, 'Classifying equipment types…')
        _classify_equipment_types(equipment, config)

        # ── Persist to DB (non-fatal) ────────────────────────────────────────
        _persist_to_db(equipment, upload_id, extraction_mode, drawing_ref, config)

        # ── Store final result in Redis cache ────────────────────────────────
        result = {
            'status':          'completed',
            'equipment':       equipment,
            'total':           len(equipment),
            'drawing_ref':     drawing_ref,
            'extraction_mode': extraction_mode,
        }
        cache.set(cache_key, result, EQ_RESULT_CACHE_TTL_S)
        logger.info('[EQTask] Completed  upload_id=%s  items=%d  mode=%s',
                    upload_id, len(equipment), extraction_mode)

    except ValueError as exc:
        # User-facing errors (corrupted PDF, unsupported format, etc.)
        # Store as 'failed' with the friendly message; do NOT re-raise so
        # Celery marks it SUCCESS (the failure is expected, not a bug).
        logger.warning('[EQTask] PDF validation error  upload_id=%s  error=%s', upload_id, exc)
        cache.set(cache_key, {'status': 'failed', 'error': str(exc)}, EQ_RESULT_CACHE_TTL_S)

    except Exception as exc:
        logger.error('[EQTask] Failed  upload_id=%s  error=%s', upload_id, exc, exc_info=True)
        cache.set(cache_key, {'status': 'failed', 'error': str(exc)}, EQ_RESULT_CACHE_TTL_S)
        # Do NOT re-raise: failure is written to cache; the frontend reads it via
        # /status/<upload_id>/. Re-raising in EAGER mode propagates to the view → HTTP 500.


@shared_task(
    bind=True,
    name='pid_analysis.run_equipment_batch_analysis',
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=EQ_BATCH_SOFT_LIMIT_S,
    time_limit=EQ_BATCH_HARD_LIMIT_S,
)
def run_equipment_batch_analysis_task(self, upload_id: str, files_data: list):
    """
    Async Celery task: extract equipment list from multiple P&ID PDFs.

    Each file is processed through the full pipeline including multi-page
    support, AI gap-fill, and title-block revision extraction via
    _process_pid_pages.  Results from all files are cross-file deduplicated
    before being returned (richest extraction per tag wins).

    Args:
        upload_id:  Unique identifier for polling.
        files_data: List of {'b64': str, 'filename': str} dicts.

    Per-file errors are logged and skipped (remaining files continue processing).
    Combined result stored in Redis cache.
    """
    from apps.pid_analysis.equipment_analysis_views import (
        _load_config,
        _dedup_equipment_by_tag,
    )

    cache_key = EQ_RESULT_CACHE_KEY_FMT.format(upload_id=upload_id)
    n_files   = len(files_data)

    def _set_progress(pct: int, msg: str) -> None:
        cache.set(cache_key,
                  {'status': 'processing', 'progress': pct, 'message': msg},
                  EQ_RESULT_CACHE_TTL_S)

    logger.info('[EQBatchTask] Starting  upload_id=%s  files=%d', upload_id, n_files)
    _set_progress(5, f'Processing 0 / {n_files} file(s)…')

    try:
        config        = _load_config()
        all_equipment: list = []
        drawing_refs:  list = []

        for fi, fd in enumerate(files_data, 1):
            filename   = fd['filename']
            file_bytes = base64.b64decode(fd['b64'])

            # Map this file's inner progress (0–100) to a sub-range of 5–90%
            _file_base = int(5 + (fi - 1) / n_files * 85)
            _file_span = 85 / n_files

            def _file_progress(inner_pct: int, msg: str,
                                _base=_file_base, _span=_file_span) -> None:
                combined = int(_base + inner_pct / 100 * _span)
                _set_progress(combined, f'[{fi}/{n_files}] {msg}')

            logger.info('[EQBatchTask] File %d/%d: %s', fi, n_files, filename)
            _file_progress(0, f'Starting {filename}…')

            try:
                equipment, drawing_ref, _ = _process_pid_pages(
                    file_bytes, filename, config, set_progress=_file_progress,
                )

                for idx, item in enumerate(equipment, 1):
                    if not item.get('sl_no'):
                        item['sl_no'] = str(idx)
                    item['drawing_ref'] = drawing_ref

                all_equipment.extend(equipment)
                # drawing_ref may be comma-separated for multi-page PDFs
                for _ref in drawing_ref.split(','):
                    _ref = _ref.strip()
                    if _ref and _ref not in drawing_refs:
                        drawing_refs.append(_ref)

            except Exception as file_exc:
                logger.error(
                    '[EQBatchTask] Error on file %s: %s', filename, file_exc, exc_info=True
                )

        # Cross-file deduplication (richest extraction per tag wins)
        all_equipment = _dedup_equipment_by_tag(all_equipment)

        # Re-number sequentially across all drawings
        for idx, item in enumerate(all_equipment, 1):
            item['sl_no'] = idx

        _set_progress(92, 'Classifying equipment types…')
        _classify_equipment_types(all_equipment, config)

        result = {
            'status':      'completed',
            'equipment':   all_equipment,
            'total':       len(all_equipment),
            'drawing_ref': ', '.join(drawing_refs),
        }
        cache.set(cache_key, result, EQ_RESULT_CACHE_TTL_S)
        logger.info('[EQBatchTask] Completed  upload_id=%s  total=%d  drawings=%d',
                    upload_id, len(all_equipment), len(drawing_refs))

    except Exception as exc:
        logger.error('[EQBatchTask] Failed  upload_id=%s: %s', upload_id, exc, exc_info=True)
        cache.set(cache_key, {'status': 'failed', 'error': str(exc)}, EQ_RESULT_CACHE_TTL_S)
        # Do NOT re-raise: same reasoning as run_equipment_analysis_task.


# ═══════════════════════════════════════════════════════════════════════════
# Instrument Index — async extraction pipeline
# ═══════════════════════════════════════════════════════════════════════════
# Instrument Index scanning is the heaviest of the three extractors (overview
# + 2 rotations + 4 tiles per page, dense tags, long AI passes) and previously
# ran synchronously — which meant a 20-30 minute drawing would exceed Gunicorn
# worker timeout and the request would silently drop. We now dispatch it as a
# Celery task with the same soft/hard time limits as the equipment pipeline
# and expose a status endpoint the frontend can poll.

# Soft-coded task limits — mirror the equipment task budget so heavy P&IDs
# have the same headroom. Tune here if a class of drawings needs longer.
II_TASK_SOFT_LIMIT_S = 3000       # 50 min — SoftTimeLimitExceeded raised
II_TASK_HARD_LIMIT_S = 3600       # 60 min — SIGKILL
II_RESULT_CACHE_TTL_S = 14400     # 4 hours in Redis
II_RESULT_CACHE_KEY_FMT = 'instrument_index:{upload_id}'


@shared_task(
    bind=True,
    name='pid_analysis.run_instrument_index_task',
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=II_TASK_SOFT_LIMIT_S,
    time_limit=II_TASK_HARD_LIMIT_S,
)
def run_instrument_index_task(
    self,
    upload_id: str,
    pid_b64: str,
    filename: str,
    drawing_info: dict,
    legend_b64: str = '',
    legend_filename: str = '',
):
    """Extract the instrument index in the background.

    The frontend polls
        GET /api/v1/pid_analysis/instrument-index/status/<upload_id>/
    every few seconds until this task writes ``status='completed'`` (or
    ``'failed'``) to Redis.
    """
    from apps.pid_analysis.instrument_index_service import InstrumentIndexService

    cache_key = II_RESULT_CACHE_KEY_FMT.format(upload_id=upload_id)

    def _set_progress(pct: int, msg: str) -> None:
        cache.set(cache_key,
                  {'status': 'processing', 'progress': pct, 'message': msg},
                  II_RESULT_CACHE_TTL_S)

    logger.info('[IITask] Starting  upload_id=%s  file=%s', upload_id, filename)
    _set_progress(5, 'Initialising instrument index extraction…')

    try:
        pid_bytes = base64.b64decode(pid_b64)
        service = InstrumentIndexService()

        legend_context_override = None
        if legend_b64:
            _set_progress(10, 'Parsing legend sheet…')
            try:
                legend_bytes = base64.b64decode(legend_b64)
                legend_context_override = service.build_legend_context_from_uploaded_file(
                    legend_bytes, legend_filename or 'legend.pdf',
                )
            except Exception as exc:
                logger.warning('[IITask] Legend parse failed (continuing): %s', exc)

        _set_progress(20, 'Scanning P&ID for instrument tags…')
        instruments = service.extract_instruments(
            pid_bytes,
            drawing_info,
            legend_context_override=legend_context_override,
        )

        _set_progress(85, 'Building category summary…')
        category_summary: dict = {}
        for inst in instruments or []:
            cat = inst.get('category') or 'Unknown'
            category_summary[cat] = category_summary.get(cat, 0) + 1

        _set_progress(92, 'Generating Excel workbook…')
        excel_available = False
        try:
            excel_bytes = service.generate_excel(instruments or [], drawing_info)
            # Excel is cached under a separate key that the download endpoint reads.
            cache.set(
                f'instrument_index_excel_{upload_id}',
                excel_bytes,
                II_RESULT_CACHE_TTL_S,
            )
            excel_available = True
        except Exception as exc:
            logger.error('[IITask] Excel generation failed: %s', exc, exc_info=True)

        result = {
            'status':          'completed',
            'success':         True,
            'upload_id':       upload_id,
            'drawing_info':    drawing_info,
            'instruments':     instruments or [],
            'total':           len(instruments or []),
            'category_summary': category_summary,
            'excel_url':       (
                f'/api/v1/pid_analysis/instrument-index/download-excel/{upload_id}/'
                if excel_available else None
            ),
        }
        cache.set(cache_key, result, II_RESULT_CACHE_TTL_S)
        logger.info('[IITask] Completed  upload_id=%s  items=%d',
                    upload_id, len(instruments or []))

    except Exception as exc:
        logger.error('[IITask] Failed  upload_id=%s  error=%s', upload_id, exc, exc_info=True)
        cache.set(
            cache_key,
            {'status': 'failed', 'error': str(exc)},
            II_RESULT_CACHE_TTL_S,
        )
        # Do NOT re-raise — same reasoning as the equipment task.

