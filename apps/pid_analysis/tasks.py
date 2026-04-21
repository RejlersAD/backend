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
import threading

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

# Heartbeat thread config — keeps frontend progress moving during blocking calls.
# Each thread increments progress by 1% every INTERVAL seconds, capped at END%.
# OCR: 19 ticks × 90 s ≈ 28 min — matches observed multi-page OCR duration.
# Gap-fill: 14 ticks × 30 s ≈ 7 min — covers AI gap-fill API latency.
EQ_OCR_HEARTBEAT_INTERVAL_S   = 90   # seconds between ticks during OCR
EQ_OCR_PROGRESS_START         = 30   # progress % when OCR begins
EQ_OCR_PROGRESS_END           = 49   # progress % cap (real milestone sets 50 next)
EQ_GAPFILL_HEARTBEAT_INTERVAL_S = 30  # seconds between ticks during AI gap-fill
EQ_GAPFILL_PROGRESS_START       = 71  # just above the 70% milestone set before gap-fill
EQ_GAPFILL_PROGRESS_END         = 84  # cap (real milestone sets 85 next)


# ── Internal helpers (no external state; safe to call from Celery worker) —————


def _progress_heartbeat(
    stop_event: threading.Event,
    cache_key: str,
    cache_ttl: int,
    start_pct: int,
    end_pct: int,
    interval_s: int,
    label: str = 'Processing',
) -> None:
    """
    Generic background heartbeat thread.
    Increments the cached progress by 1% every `interval_s` seconds,
    from `start_pct` up to (but not exceeding) `end_pct`.
    Stops when stop_event is set. Only writes if the cache entry is still
    in 'processing' state, so it never overwrites a completed/failed result.
    """
    pct = start_pct
    while not stop_event.wait(interval_s):
        if pct < end_pct:
            pct += 1
        try:
            entry = cache.get(cache_key) or {}
            if entry.get('status') == 'processing':
                cache.set(cache_key,
                          {'status': 'processing', 'progress': pct,
                           'message': f'{label}… ({pct}%)'},
                          cache_ttl)
        except Exception:
            pass  # heartbeat failure is non-fatal


# Keep the old name as a thin alias so any external callers are not broken.
_ocr_heartbeat = _progress_heartbeat


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
        _extract_equipment_via_vision,
        _dedup_equipment_by_tag,
        _infer_quantity_from_tag_variants,
        _apply_richness_quality_gate,
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

        # ── Early PDF readability check ──────────────────────────────────────
        # Detect files with broken cross-reference tables (common with Adobe
        # Acrobat "Make PDF Searchable", ABBYY FineReader, PXC OCR tools that
        # damage the xref section).  If neither PyMuPDF nor poppler can open
        # the file, fail immediately with a descriptive message rather than
        # silently returning 0 items after all extraction strategies run.
        _set_progress(8, 'Checking PDF readability…')
        try:
            import fitz as _fitz_ck
            _ck_doc = _fitz_ck.open(stream=file_bytes, filetype='pdf')
            _ck_pages = _ck_doc.page_count
            _ck_doc.close()
        except Exception:
            _ck_pages = 0
        if _ck_pages == 0:
            # fitz sees 0 pages — try pdf2image (poppler) as secondary check
            _poppler_ok = False
            try:
                from pdf2image import convert_from_bytes as _cvt_ck
                _pop_imgs = _cvt_ck(file_bytes, dpi=72, first_page=1, last_page=1, fmt='png')
                _poppler_ok = len(_pop_imgs) > 0
            except Exception:
                pass
            if not _poppler_ok:
                _err_msg = (
                    'PDF could not be parsed — the file may have a corrupted '
                    'cross-reference table. This is common with PDFs processed '
                    'by some OCR tools (Adobe Acrobat "Make PDF Searchable", '
                    'ABBYY FineReader, PXC). Please re-export the original '
                    'document as PDF and re-upload.'
                )
                logger.error('[EQTask] Unreadable PDF  upload_id=%s  file=%s', upload_id, filename)
                cache.set(cache_key,
                          {'status': 'failed', 'progress': 0, 'error': _err_msg, 'message': _err_msg},
                          EQ_RESULT_CACHE_TTL_S)
                return
        equipment       = _extract_equipment_register_rows(pid_file, config)
        extraction_mode = 'register'

        # ── Stage 2: Fall back to P&ID drawing mode if no register found ────
        if equipment is None:
            logger.info('[EQTask] No register found — falling back to P&ID drawing mode')
            pid_file.seek(0)
            _set_progress(30, 'Running OCR on P&ID drawing…')

            # Heartbeat thread: keeps progress moving during blocking OCR call.
            _stop_hb = threading.Event()
            _hb      = threading.Thread(
                target=_progress_heartbeat,
                args=(_stop_hb, cache_key, EQ_RESULT_CACHE_TTL_S,
                      EQ_OCR_PROGRESS_START, EQ_OCR_PROGRESS_END,
                      EQ_OCR_HEARTBEAT_INTERVAL_S, 'Running OCR on P&ID drawing'),
                daemon=True,
            )
            _hb.start()

            # Determine multi-page mode and page count.
            # "per_page" (default): each sheet is processed independently so
            # motor ratings and process parameters stay in a page-scoped context
            # window rather than being swamped by text from adjacent sheets.
            multi_page_mode = ext_cfg.get('multi_page_mode', 'per_page')
            _page_count = 1
            try:
                import fitz as _fitz_pp
                _pp_doc = _fitz_pp.open(stream=file_bytes, filetype='pdf')
                _page_count = _pp_doc.page_count if _pp_doc.page_count > 0 else 1
                _pp_doc.close()
            except Exception:
                pass

            try:
                if multi_page_mode == 'per_page' and _page_count > 1:
                    # Per-page extraction: each PDF sheet is OCR'd and processed
                    # independently.  Motor-rating callouts and data-box values
                    # are always on the SAME sheet as the equipment tag, so the
                    # 1500-char context window easily reaches them once per-page
                    # text is used instead of the concatenated multi-page blob.
                    _page_texts = []
                    _all_raw_items = []
                    for pg_idx in range(_page_count):
                        pid_file.seek(0)
                        pg_text = _extract_text_from_pdf(pid_file, config, _page_index=pg_idx)
                        _page_texts.append(pg_text)
                        pg_items = _extract_equipment_items(pg_text, drawing_ref, config)
                        _all_raw_items.extend(pg_items)
                    text = '\n'.join(_page_texts)
                    # Cross-page dedup: the same tag can appear on multiple pages
                    # (data box on sheet 1, cross-reference on sheet 2). Each page
                    # uses its own `seen` set so duplicates survive the per-page
                    # extraction loop. Dedup here keeps the richest entry.
                    raw_items = _dedup_equipment_by_tag(_all_raw_items)
                else:
                    text = _extract_text_from_pdf(pid_file, config)
                    raw_items = _extract_equipment_items(text, drawing_ref, config)
            finally:
                _stop_hb.set()
                _hb.join(timeout=2)

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
            equipment = [_pid_item_to_register_schema(item) for item in raw_items]
            # Post-schema dedup: OCR variants (V-308 vs V-308-TF) and any
            # residual cross-page duplicates are eliminated here. The richest
            # entry (most populated fields) is always kept.
            equipment = _dedup_equipment_by_tag(equipment)
            extraction_mode = 'pid_drawing'

            # ── Vision AI fallback ─────────────────────────────────────────
            # When OCR + regex finds very few items the PDF is almost certainly
            # an image-based P&ID with minimal embedded text.  Render each page
            # as a PNG and ask a vision model to extract equipment tags directly
            # from the graphical content.
            _vision_threshold = int(
                config.get('extraction', {}).get('vision_extraction_threshold', 5)
            )
            if len(equipment) < _vision_threshold:
                logger.info(
                    '[EQTask] OCR found %d item(s) < threshold %d — '
                    'switching to Vision AI extraction',
                    len(equipment), _vision_threshold,
                )
                _set_progress(55, 'Running Vision AI extraction…')
                _stop_vis = threading.Event()
                _hb_vis   = threading.Thread(
                    target=_progress_heartbeat,
                    args=(_stop_vis, cache_key, EQ_RESULT_CACHE_TTL_S,
                          55, 68,
                          EQ_OCR_HEARTBEAT_INTERVAL_S, 'Vision AI analysing pages'),
                    daemon=True,
                )
                _hb_vis.start()
                try:
                    vision_raw = _extract_equipment_via_vision(
                        file_bytes, drawing_ref, config
                    )
                finally:
                    _stop_vis.set()
                    _hb_vis.join(timeout=2)

                if vision_raw:
                    equipment = [_pid_item_to_register_schema(item) for item in vision_raw]
                    # Deduplicate by tag (richest entry wins) then infer qty from A/B/C sets
                    equipment = _dedup_equipment_by_tag(equipment)
                    equipment = _infer_quantity_from_tag_variants(equipment, config)
                    extraction_mode = 'pid_vision'
                    # Update drawing_ref from first item with a non-default ref
                    for _vi in vision_raw:
                        _vr = _vi.get('drawing_ref', '')
                        if _vr and '_P' not in _vr:
                            drawing_ref = _vr
                            break
                    logger.info('[EQTask] Vision AI found %d item(s) after dedup', len(equipment))

            if equipment:
                _set_progress(70, 'Running AI gap-fill…')
                # Heartbeat thread: keeps progress moving during AI gap-fill API call.
                _stop_gf = threading.Event()
                _hb_gf   = threading.Thread(
                    target=_progress_heartbeat,
                    args=(_stop_gf, cache_key, EQ_RESULT_CACHE_TTL_S,
                          EQ_GAPFILL_PROGRESS_START, EQ_GAPFILL_PROGRESS_END,
                          EQ_GAPFILL_HEARTBEAT_INTERVAL_S, 'Running AI gap-fill'),
                    daemon=True,
                )
                _hb_gf.start()
                try:
                    equipment = _ai_gap_fill_pid_items(equipment, text, config)
                finally:
                    _stop_gf.set()
                    _hb_gf.join(timeout=2)

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

        # ── Gate 3: Richness quality gate (register & vision paths) ──────────
        # OCR/regex path runs this gate inside _extract_equipment_items.
        # Register and vision items bypass that function so the gate is applied
        # here to catch low-quality rows from those paths as well.
        if extraction_mode in ('register', 'pid_vision'):
            equipment = _apply_richness_quality_gate(equipment, ext_cfg)
            # Re-number after gate may have removed rows
            for idx, item in enumerate(equipment, 1):
                item['sl_no'] = str(idx)

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
        _extract_titleblock_revision,
        _extract_equipment_items,
        _pid_item_to_register_schema,
        _ai_gap_fill_pid_items,
        _extract_equipment_via_vision,
        _dedup_equipment_by_tag,
        _infer_quantity_from_tag_variants,
        _apply_richness_quality_gate,
        _REVISION_USE_TOPMOST,
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
        config           = _load_config()
        ext_cfg          = config.get('extraction', {})
        all_equipment: list = []
        drawing_refs:  list = []
        _vision_threshold = int(ext_cfg.get('vision_extraction_threshold', 5))

        for fi, fd in enumerate(files_data, 1):
            filename    = fd['filename']
            file_bytes  = base64.b64decode(fd['b64'])
            drawing_ref = filename.rsplit('.', 1)[0]
            pid_file    = _make_inmemory_file(file_bytes, filename)
            file_progress_base = int(5 + (fi - 1) / n_files * 80)

            _set_progress(
                file_progress_base,
                f'Processing file {fi} / {n_files}: {filename}…',
            )
            logger.info('[EQBatchTask] File %d/%d: %s', fi, n_files, filename)

            # Early readability check — skip corrupted files with a warning
            try:
                import fitz as _fitz_bck
                _bck_doc = _fitz_bck.open(stream=file_bytes, filetype='pdf')
                _bck_pages = _bck_doc.page_count
                _bck_doc.close()
            except Exception:
                _bck_pages = 0
            if _bck_pages == 0:
                _poppler_ok = False
                try:
                    from pdf2image import convert_from_bytes as _cvt_bck
                    _bck_imgs = _cvt_bck(file_bytes, dpi=72, first_page=1, last_page=1, fmt='png')
                    _poppler_ok = len(_bck_imgs) > 0
                except Exception:
                    pass
                if not _poppler_ok:
                    logger.warning(
                        '[EQBatchTask] Skipping unreadable PDF  file=%s  '
                        '(broken xref — re-export and re-upload)',
                        filename,
                    )
                    continue  # skip this file, process remaining ones

            try:
                # ── Stage 1: Equipment Register extraction ───────────────────
                equipment = _extract_equipment_register_rows(pid_file, config)
                extraction_mode = 'register'

                if equipment is None:
                    # ── Stage 2: OCR + regex extraction ─────────────────────
                    pid_file.seek(0)
                    _set_progress(file_progress_base, f'OCR: file {fi}/{n_files}…')

                    # Per-page mode: process each sheet independently so that
                    # motor ratings and process parameters are found within a
                    # page-scoped context window (same logic as single-file task).
                    _b_multi_mode = ext_cfg.get('multi_page_mode', 'per_page')
                    _b_page_count = 1
                    try:
                        import fitz as _fitz_bpp
                        _bpp_doc = _fitz_bpp.open(stream=file_bytes, filetype='pdf')
                        _b_page_count = _bpp_doc.page_count if _bpp_doc.page_count > 0 else 1
                        _bpp_doc.close()
                    except Exception:
                        pass

                    if _b_multi_mode == 'per_page' and _b_page_count > 1:
                        _b_page_texts = []
                        _b_all_raw = []
                        for pg_idx in range(_b_page_count):
                            pid_file.seek(0)
                            pg_text = _extract_text_from_pdf(pid_file, config, _page_index=pg_idx)
                            _b_page_texts.append(pg_text)
                            pg_items = _extract_equipment_items(pg_text, drawing_ref, config)
                            _b_all_raw.extend(pg_items)
                        text = '\n'.join(_b_page_texts)
                        raw_items = _b_all_raw
                    else:
                        pid_file.seek(0)
                        text = _extract_text_from_pdf(pid_file, config)
                        raw_items = _extract_equipment_items(text, drawing_ref, config)

                    _coord_dwg_no = ''
                    try:
                        pid_file.seek(0)
                        _coord_dwg_no = _extract_titleblock_dwg_no_by_coords(pid_file.read())
                    except Exception:
                        pass
                    _tb_dwg_no = _coord_dwg_no or _extract_titleblock_dwg_no(text)
                    if _tb_dwg_no:
                        drawing_ref = _tb_dwg_no

                    equipment = [_pid_item_to_register_schema(item) for item in raw_items]
                    extraction_mode = 'pid_drawing'

                    # ── Stage 3: Vision AI fallback ──────────────────────────
                    if len(equipment) < _vision_threshold:
                        logger.info(
                            '[EQBatchTask] File %s: OCR found %d item(s) < threshold %d '
                            '— switching to Vision AI',
                            filename, len(equipment), _vision_threshold,
                        )
                        _set_progress(
                            file_progress_base,
                            f'Vision AI: file {fi}/{n_files}…',
                        )
                        vision_raw = _extract_equipment_via_vision(
                            file_bytes, drawing_ref, config
                        )
                        if vision_raw:
                            equipment = [_pid_item_to_register_schema(v) for v in vision_raw]
                            # Deduplicate by tag then infer qty from A/B/C sets
                            equipment = _dedup_equipment_by_tag(equipment)
                            equipment = _infer_quantity_from_tag_variants(equipment, config)
                            extraction_mode = 'pid_vision'
                            for _vi in vision_raw:
                                _vr = _vi.get('drawing_ref', '')
                                if _vr and '_P' not in _vr:
                                    drawing_ref = _vr
                                    break

                    # ── Stage 4: AI gap-fill ─────────────────────────────────
                    if equipment and text:
                        _set_progress(
                            file_progress_base,
                            f'AI gap-fill: file {fi}/{n_files}…',
                        )
                        equipment = _ai_gap_fill_pid_items(equipment, text, config)

                    # Apply title-block revision to all items on this drawing
                    _tb_rev_enabled = bool(ext_cfg.get('titleblock_revision_enabled', True))
                    if _REVISION_USE_TOPMOST and _tb_rev_enabled:
                        _doc_rev = _extract_titleblock_revision(text)
                        if _doc_rev:
                            for _item in equipment:
                                _item['revision'] = _doc_rev

                for idx, item in enumerate(equipment, 1):
                    if not item.get('sl_no'):
                        item['sl_no'] = str(idx)
                    item['drawing_ref'] = drawing_ref

                # ── Gate 3: Richness quality gate per file ───────────────────
                if extraction_mode in ('register', 'pid_vision'):
                    equipment = _apply_richness_quality_gate(equipment, ext_cfg)

                drawing_refs.append(drawing_ref)
                all_equipment.extend(equipment)
                logger.info(
                    '[EQBatchTask] File %s: %d item(s) mode=%s',
                    filename, len(equipment), extraction_mode,
                )

            except Exception as file_exc:
                logger.error('[EQBatchTask] Error on file %s: %s', filename, file_exc, exc_info=True)
                drawing_refs.append(drawing_ref)   # still register the drawing reference

        # Final cross-file dedup: the same physical equipment can appear in
        # multiple uploaded drawings (e.g. the tag is referenced on drawing A
        # and has its own data box on drawing B). Deduplicate across all files
        # keeping the richest record.
        all_equipment = _dedup_equipment_by_tag(all_equipment)

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
