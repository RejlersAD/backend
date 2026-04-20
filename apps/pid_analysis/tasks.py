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

    Args:
        upload_id: Unique identifier returned to the frontend as polling key.
        file_b64:  Base-64 encoded PDF bytes.
        filename:  Original filename (used as fallback drawing reference).

    Writes final result dict to Redis cache so Django web workers can serve it
    via the status and results endpoints without sharing in-process memory.
    """
    # Lazy imports avoid circular-import issues at module load time.
    from apps.pid_analysis.equipment_analysis_views import (
        _load_config,
        _extract_equipment_register_rows,
        _extract_text_from_pdf,
        _extract_titleblock_dwg_no_by_coords,
        _extract_titleblock_dwg_no,
        _extract_titleblock_revision,
        _extract_equipment_items,
        _pid_item_to_register_schema,
        _ai_gap_fill_pid_items,
        _REVISION_USE_TOPMOST,
    )

    cache_key = EQ_RESULT_CACHE_KEY_FMT.format(upload_id=upload_id)

    def _set_progress(pct: int, msg: str) -> None:
        cache.set(cache_key,
                  {'status': 'processing', 'progress': pct, 'message': msg},
                  EQ_RESULT_CACHE_TTL_S)

    logger.info('[EQTask] Starting  upload_id=%s  file=%s', upload_id, filename)
    _set_progress(5, 'Initialising extraction…')

    try:
        file_bytes  = base64.b64decode(file_b64)
        config      = _load_config()
        ext_cfg     = config.get('extraction', {})
        drawing_ref = filename.rsplit('.', 1)[0]
        pid_file    = _make_inmemory_file(file_bytes, filename)

        # ── Stage 1: Equipment Register extraction ───────────────────────────
        _set_progress(15, 'Scanning for equipment register table…')
        equipment       = _extract_equipment_register_rows(pid_file, config)
        extraction_mode = 'register'

        # ── Stage 2: Fall back to P&ID drawing mode if no register found ────
        if equipment is None:
            logger.info('[EQTask] No register found — falling back to P&ID drawing mode')
            pid_file.seek(0)
            _set_progress(30, 'Running OCR on P&ID drawing…')
            text = _extract_text_from_pdf(pid_file, config)

            # Title-block DWG NO: coordinate-based extraction first, then text-based.
            _coord_dwg_no = ''
            try:
                pid_file.seek(0)
                _coord_dwg_no = _extract_titleblock_dwg_no_by_coords(pid_file.read())
            except Exception:
                pass
            _tb_dwg_no = _coord_dwg_no or _extract_titleblock_dwg_no(text)
            if _tb_dwg_no:
                drawing_ref = _tb_dwg_no

            _set_progress(50, 'Extracting equipment items…')
            raw_items   = _extract_equipment_items(text, drawing_ref, config)
            equipment   = [_pid_item_to_register_schema(item) for item in raw_items]
            extraction_mode = 'pid_drawing'

            if equipment and text:
                _set_progress(70, 'Running AI gap-fill…')
                equipment = _ai_gap_fill_pid_items(equipment, text, config)

            _tb_rev_enabled = bool(ext_cfg.get('titleblock_revision_enabled', True))
            if _REVISION_USE_TOPMOST and _tb_rev_enabled:
                _doc_rev = _extract_titleblock_revision(text)
                if _doc_rev:
                    for _item in equipment:
                        _item['revision'] = _doc_rev

        # ── Numbering & drawing reference ────────────────────────────────────
        for idx, item in enumerate(equipment, 1):
            if not item.get('sl_no'):
                item['sl_no'] = str(idx)
            item['drawing_ref'] = drawing_ref

        # ── Equipment type classification ────────────────────────────────────
        _set_progress(85, 'Classifying equipment types…')
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

    except Exception as exc:
        logger.error('[EQTask] Failed  upload_id=%s  error=%s', upload_id, exc, exc_info=True)
        cache.set(cache_key, {'status': 'failed', 'error': str(exc)}, EQ_RESULT_CACHE_TTL_S)
        raise   # Let Celery mark the task as FAILURE for monitoring


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

    Args:
        upload_id:  Unique identifier for polling.
        files_data: List of {'b64': str, 'filename': str} dicts.

    Per-file errors are logged and skipped (remaining files continue processing).
    Combined result stored in Redis cache.
    """
    from apps.pid_analysis.equipment_analysis_views import (
        _load_config,
        _extract_equipment_register_rows,
        _extract_text_from_pdf,
        _extract_titleblock_dwg_no_by_coords,
        _extract_titleblock_dwg_no,
        _extract_equipment_items,
        _pid_item_to_register_schema,
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
            filename    = fd['filename']
            file_bytes  = base64.b64decode(fd['b64'])
            drawing_ref = filename.rsplit('.', 1)[0]
            pid_file    = _make_inmemory_file(file_bytes, filename)

            _set_progress(
                int(5 + (fi - 1) / n_files * 85),
                f'Processing file {fi} / {n_files}: {filename}…',
            )
            logger.info('[EQBatchTask] File %d/%d: %s', fi, n_files, filename)

            try:
                equipment = _extract_equipment_register_rows(pid_file, config)
                if equipment is None:
                    pid_file.seek(0)
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
                    raw_items = _extract_equipment_items(text, drawing_ref, config)
                    equipment = [_pid_item_to_register_schema(item) for item in raw_items]

                for idx, item in enumerate(equipment, 1):
                    if not item.get('sl_no'):
                        item['sl_no'] = str(idx)
                    item['drawing_ref'] = drawing_ref

                drawing_refs.append(drawing_ref)
                all_equipment.extend(equipment)

            except Exception as file_exc:
                logger.error('[EQBatchTask] Error on file %s: %s', filename, file_exc, exc_info=True)
                drawing_refs.append(drawing_ref)   # still register the drawing reference

        # Re-number sequentially across all drawings
        for idx, item in enumerate(all_equipment, 1):
            item['sl_no'] = idx

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
        raise
