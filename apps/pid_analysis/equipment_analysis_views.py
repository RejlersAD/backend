"""
Equipment Analysis Views - P&ID Equipment List Extraction
"""

import json
import logging
import os
import re
import uuid
from functools import lru_cache

from django.http import HttpResponse
from rest_framework import status as drf_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), 'config', 'equipment_type_config.json'
)


@lru_cache(maxsize=1)
def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as fh:
            raw = json.load(fh)
        return {k: v for k, v in raw.items() if not k.startswith('_')}
    except Exception as exc:
        logger.warning('[EquipmentList] Could not load config: %s - using defaults', exc)
        return {
            'extraction': {'context_window_chars': 120, 'description_max_words': 5},
            'type_labels': {
                'V': 'Vessel', 'P': 'Pump', 'E': 'Heat Exchanger', 'T': 'Tank',
                'K': 'Compressor', 'C': 'Column / Tower', 'H': 'Heater / Cooler',
                'D': 'Drum / Separator', 'R': 'Reactor',
            },
            'fluid_keywords': ['crude', 'gas', 'oil', 'water', 'steam'],
            'excel_columns': [
                {'key': 'sl_no',            'label': 'S. No',             'width': 6 },
                {'key': 'tag',              'label': 'Tag Number',         'width': 14},
                {'key': 'type_label',       'label': 'Equipment Type',     'width': 22},
                {'key': 'description',      'label': 'Description',        'width': 30},
                {'key': 'drawing_ref',      'label': 'Drawing Reference',  'width': 22},
                {'key': 'line_connections', 'label': 'Line Connections',   'width': 30},
                {'key': 'service_fluid',    'label': 'Service / Fluid',    'width': 20},
            ],
        }


_LINE_TAG_RE = re.compile(
    r'(?<![A-Za-z0-9])'
    r'(\d+(?:\.\d+)?)\s*["\u201c\u201d\u2019\'`]{1,2}'
    r'[\s\-_]{0,3}([A-Z]{1,4})[\s\-_]+(\d{3,6})[\s\-_]+(\d{4,8})'
    r'(?:[\s\-_]+([A-Z0-9]{1,8}))?'
    r'(?![A-Za-z0-9])',
    re.IGNORECASE,
)


def _extract_text_from_pdf(file_obj, config=None) -> str:
    """
    Extract all text from a PDF.

    Vector PDFs: PyMuPDF reads every text block including those written at any
    rotation angle (it handles the page/block transform internally).

    Scanned PDFs (OCR fallback): Tesseract is run once per rotation angle
    listed in extraction.ocr_rotation_angles (soft-coded in
    equipment_type_config.json).  Default [0, 90, 180, 270] catches horizontal
    text, pipe-run labels written vertically on P&IDs, upside-down title
    blocks, and any other orientation.  Unique non-empty results are joined so
    the later regex pass sees all discovered text.
    """
    cfg         = config or {}
    ext_cfg     = cfg.get('extraction', {})
    # Soft-coded — change via equipment_type_config.json, no Python edit needed
    ocr_angles  = ext_cfg.get('ocr_rotation_angles', [0, 90, 180, 270])

    text_parts = []
    try:
        import fitz
        file_bytes = file_obj.read()
        doc = fitz.open(stream=file_bytes, filetype='pdf')
        for page in doc:
            # TEXT_DEHYPHENATE joins words split across line-ends;
            # TEXT_PRESERVE_WHITESPACE keeps spacing that helps regex matching.
            flags = getattr(fitz, 'TEXT_DEHYPHENATE', 0) | getattr(fitz, 'TEXT_PRESERVE_WHITESPACE', 0)
            page_text = page.get_text('text', flags=flags) if flags else page.get_text('text')
            text_parts.append(page_text or '')
        doc.close()
    except Exception as exc:
        logger.debug('[EquipmentList] PyMuPDF issue: %s', exc)

    full_text = '\n'.join(text_parts).strip()

    if len(full_text) < 200:
        # ----------------------------------------------------------------
        # OCR fallback — multi-angle (soft-coded via ocr_rotation_angles)
        # Each angle renders the page rotated so Tesseract sees the text
        # upright.  Negative PIL rotation == clockwise == matches the
        # counter-clockwise PDF convention for text written vertically.
        # ----------------------------------------------------------------
        try:
            import fitz
            import pytesseract
            from PIL import Image
            import io

            file_obj.seek(0)
            file_bytes = file_obj.read()
            doc = fitz.open(stream=file_bytes, filetype='pdf')
            ocr_parts = []
            for page in doc:
                mat = fitz.Matrix(2.0, 2.0)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
                base_img = Image.open(io.BytesIO(pix.tobytes('png')))
                for angle in ocr_angles:
                    rotated = base_img.rotate(-angle, expand=True) if angle != 0 else base_img
                    ocr_text = pytesseract.image_to_string(rotated, config='--oem 1 --psm 11')
                    if ocr_text.strip():
                        ocr_parts.append(ocr_text)
            doc.close()
            full_text = '\n'.join(ocr_parts)
        except Exception as exc:
            logger.debug('[EquipmentList] Tesseract fallback issue: %s', exc)

    return full_text


def _extract_equipment_items(text: str, drawing_ref: str, config: dict) -> list:
    """
    Parse extracted PDF text and build a list of equipment dicts.

    All field extraction patterns are soft-coded in equipment_type_config.json.
    Add / adjust patterns there without touching this function.

    Fields returned per item
    ------------------------
    tag, type_label, description, area, drawing_ref,
    line_connections, nozzle_connections, service_fluid,
    material_class, process_notes
    """
    ext_cfg     = config.get('extraction', {})
    type_labels = config.get('type_labels', {})
    fluid_kws   = [kw for kw in config.get('fluid_keywords', []) if not kw.startswith('_')]
    ctx_win     = int(ext_cfg.get('context_window_chars', 160))
    desc_words  = int(ext_cfg.get('description_max_words', 6))

    tag_re = re.compile(r'\b([A-Z]{1,2})-([0-9]{3,5}[A-Z]?)\b')

    # --- Soft-coded helper patterns (read once per call) ------------------
    area_re    = re.compile(
        ext_cfg.get('area_pattern',
                    r'(?:AREA|UNIT|TRAIN|BAY|SECTION|BATTERY)\s*[:\-]?\s*([A-Z0-9]{1,8})'),
        re.IGNORECASE,
    )
    nozzle_re  = re.compile(
        ext_cfg.get('nozzle_pattern', r'\bN[0-9]{1,2}[A-Z]?\b')
    )
    mat_re     = re.compile(
        ext_cfg.get('material_class_pattern',
                    r'\b(A1[A-Z]|B1[A-Z]|C1[A-Z]|D1[A-Z]|[A-D]2[A-Z]'
                    r'|CS|SS|DSS|SDSS|GRE|PVC|HDPE|CPVC)\b'),
        re.IGNORECASE,
    )
    note_re    = re.compile(
        ext_cfg.get('note_pattern', r'\b(?:SEE\s+)?NOTE\s*[0-9]+\b'),
        re.IGNORECASE,
    )
    # -----------------------------------------------------------------------

    instr_valve_prefixes = {
        'FT','FI','FIC','FC','PT','PI','PIC','PC','LT','LI','LIC','LC',
        'TT','TI','TIC','TC','AT','AI','FY','PY','LY','TY',
        'HV','FV','XV','PV','SDV','BDV','PSV','PRV','CV','LV','TV',
        'FE','TE','LE','PE','HS','HIC','HI',
    }

    seen = set()
    results = []

    for m in tag_re.finditer(text):
        prefix = m.group(1).upper()
        tag    = m.group(0)

        if prefix in instr_valve_prefixes:
            continue
        if type_labels and prefix not in type_labels:
            continue
        if tag in seen:
            continue
        seen.add(tag)

        start = max(0, m.start() - ctx_win)
        end   = min(len(text), m.end() + ctx_win)
        ctx   = text[start:end]

        type_label = type_labels.get(prefix, 'Equipment')

        # ── Description (capitalised words immediately after tag) ──────────
        after = text[m.end():m.end() + ctx_win]
        cap_words = re.findall(r'\b[A-Z][A-Z0-9/\-]{1,20}\b', after)
        desc_words_filtered = [
            w for w in cap_words
            if not re.match(r'^[A-Z]{1,2}-[0-9]', w) and len(w) > 1
        ][:desc_words]
        description = ' '.join(desc_words_filtered) if desc_words_filtered else ''

        # ── Line connections (piping designation tokens) ───────────────────
        lc_tokens = []
        for lm in _LINE_TAG_RE.finditer(ctx):
            token = lm.group(0).strip()
            if token and token not in lc_tokens:
                lc_tokens.append(token)

        # ── Service / fluid ───────────────────────────────────────────────
        ctx_lower = ctx.lower()
        found_fluids = [kw for kw in fluid_kws if kw in ctx_lower]
        service_fluid = ', '.join(found_fluids[:2]) if found_fluids else ''

        # ── Area / Unit (soft-coded area_pattern) ─────────────────────────
        area_m = area_re.search(ctx)
        area   = area_m.group(0).strip() if area_m else ''

        # ── Nozzle connections (soft-coded nozzle_pattern) ────────────────
        nozzle_tokens = list(dict.fromkeys(nozzle_re.findall(ctx)))[:6]

        # ── Material / piping spec class (soft-coded material_class_pattern)
        mat_matches    = mat_re.findall(ctx)
        material_class = mat_matches[0] if mat_matches else ''

        # ── Process note references (soft-coded note_pattern) ─────────────
        note_matches  = list(dict.fromkeys(
            n.strip() for n in note_re.findall(ctx)
        ))[:3]
        process_notes = ', '.join(note_matches) if note_matches else ''

        results.append({
            'tag':               tag,
            'type_label':        type_label,
            'description':       description,
            'area':              area,
            'drawing_ref':       drawing_ref,
            'line_connections':  lc_tokens,
            'nozzle_connections': nozzle_tokens,
            'service_fluid':     service_fluid,
            'material_class':    material_class,
            'process_notes':     process_notes,
        })

    results.sort(key=lambda x: x['tag'])
    return results


_result_store: dict = {}


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def analyze_pid_equipment(request):
    """POST /api/v1/pid/equipment/analyze/"""
    config  = _load_config()
    ext_cfg = config.get('extraction', {})
    allowed = [e.lower() for e in ext_cfg.get('allowed_extensions', ['pdf'])]
    max_mb  = float(ext_cfg.get('max_file_size_mb', 50))

    pid_file = request.FILES.get('file') or (list(request.FILES.values())[0] if request.FILES else None)
    if not pid_file:
        return Response({'error': 'No file provided', 'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    ext = pid_file.name.rsplit('.', 1)[-1].lower()
    if ext not in allowed:
        return Response({'error': f'Unsupported format: .{ext}. Allowed: {", ".join(allowed)}', 'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    if pid_file.size > max_mb * 1024 * 1024:
        return Response({'error': f'File exceeds {max_mb} MB limit', 'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    drawing_ref = pid_file.name.rsplit('.', 1)[0]
    upload_id   = f'EQ-{uuid.uuid4().hex[:12].upper()}'

    logger.info('[EquipmentList] Analyzing: %s  upload_id=%s', pid_file.name, upload_id)

    try:
        text      = _extract_text_from_pdf(pid_file, config)
        equipment = _extract_equipment_items(text, drawing_ref, config)

        for idx, item in enumerate(equipment, 1):
            item['sl_no'] = idx

        _result_store[upload_id] = {
            'status':      'completed',
            'equipment':   equipment,
            'total':       len(equipment),
            'drawing_ref': drawing_ref,
        }

        logger.info('[EquipmentList] Done: %d items found', len(equipment))

        return Response({
            'success':     True,
            'upload_id':   upload_id,
            'status':      'completed',
            'equipment':   equipment,
            'total':       len(equipment),
            'drawing_ref': drawing_ref,
            'columns':     [c['label'] for c in config.get('excel_columns', []) if c['key'] != 'sl_no'],
        }, status=drf_status.HTTP_200_OK)

    except Exception as exc:
        logger.error('[EquipmentList] Error: %s', exc, exc_info=True)
        _result_store[upload_id] = {'status': 'failed', 'error': str(exc)}
        return Response({'error': f'Extraction failed: {exc}', 'success': False},
                        status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_equipment_analysis_status(request, upload_id):
    """GET /api/v1/pid/equipment/status/<upload_id>/"""
    entry = _result_store.get(upload_id)
    if not entry:
        return Response({'upload_id': upload_id, 'status': 'not_found', 'progress': 0},
                        status=drf_status.HTTP_404_NOT_FOUND)
    return Response({
        'upload_id': upload_id,
        'status':    entry.get('status', 'processing'),
        'progress':  100 if entry.get('status') == 'completed' else 50,
        'message':   entry.get('error', 'Extraction complete' if entry.get('status') == 'completed' else 'Processing...'),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_equipment_analysis_results(request, upload_id):
    """GET /api/v1/pid/equipment/results/<upload_id>/"""
    entry = _result_store.get(upload_id)
    if not entry:
        return Response({'error': 'Results not found - re-upload the file', 'upload_id': upload_id},
                        status=drf_status.HTTP_404_NOT_FOUND)
    if entry.get('status') == 'failed':
        return Response({'error': entry.get('error', 'Extraction failed'), 'upload_id': upload_id},
                        status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)

    config = _load_config()
    return Response({
        'success':     True,
        'upload_id':   upload_id,
        'equipment':   entry.get('equipment', []),
        'total':       entry.get('total', 0),
        'drawing_ref': entry.get('drawing_ref', ''),
        'columns':     [c['label'] for c in config.get('excel_columns', []) if c['key'] != 'sl_no'],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_equipment_excel(request, upload_id):
    """GET /api/v1/pid/equipment/download-excel/<upload_id>/"""
    entry = _result_store.get(upload_id)
    if not entry or entry.get('status') != 'completed':
        return Response({'error': 'Results not available - re-upload the file'},
                        status=drf_status.HTTP_404_NOT_FOUND)

    config    = _load_config()
    col_defs  = config.get('excel_columns', [])
    equipment = entry.get('equipment', [])
    drawing   = entry.get('drawing_ref', 'equipment_list')

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Equipment List'

        header_font  = Font(bold=True, color='FFFFFF', size=11)
        header_fill  = PatternFill(start_color='1E3A5F', end_color='1E3A5F', fill_type='solid')
        header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
        thin_border  = Border(
            left=Side(style='thin'), right=Side(style='thin'),
            top=Side(style='thin'),  bottom=Side(style='thin'),
        )
        alt_fill = PatternFill(start_color='EFF6FF', end_color='EFF6FF', fill_type='solid')

        headers = [c['label'] for c in col_defs]
        for col_idx, label in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=label)
            cell.font      = header_font
            cell.fill      = header_fill
            cell.alignment = header_align
            cell.border    = thin_border

        ws.row_dimensions[1].height = 30

        for row_idx, item in enumerate(equipment, 2):
            row_fill = alt_fill if row_idx % 2 == 0 else None
            for col_idx, col_def in enumerate(col_defs, 1):
                key   = col_def['key']
                value = item.get(key, '')
                if isinstance(value, list):
                    value = ', '.join(str(v) for v in value) if value else '-'
                elif value == '' or value is None:
                    value = '-'
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.alignment = Alignment(vertical='center', wrap_text=(key in ('line_connections', 'description')))
                cell.border    = thin_border
                if row_fill:
                    cell.fill = row_fill

        for col_idx, col_def in enumerate(col_defs, 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = col_def.get('width', 18)

        ws.freeze_panes = 'A2'

        import io
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        safe_name = re.sub(r'[^\w\-]', '_', drawing)
        response  = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{safe_name}_equipment_list.xlsx"'
        return response

    except ImportError:
        return Response({'error': 'openpyxl is not installed on the server'},
                        status=drf_status.HTTP_501_NOT_IMPLEMENTED)
    except Exception as exc:
        logger.error('[EquipmentList] Excel error: %s', exc, exc_info=True)
        return Response({'error': f'Excel generation failed: {exc}'},
                        status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def analyze_pid_equipment_batch(request):
    """POST /api/v1/pid/equipment/analyze-batch/
    Accepts multiple files and returns combined equipment results for all drawings.
    """
    config  = _load_config()
    ext_cfg = config.get('extraction', {})
    allowed = [e.lower() for e in ext_cfg.get('allowed_extensions', ['pdf'])]
    max_mb  = float(ext_cfg.get('max_file_size_mb', 50))

    files = list(request.FILES.values())
    if not files:
        return Response({'error': 'No files provided', 'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    all_equipment = []
    drawing_refs  = []
    upload_id     = f'EQB-{uuid.uuid4().hex[:12].upper()}'

    for pid_file in files:
        ext = pid_file.name.rsplit('.', 1)[-1].lower()
        if ext not in allowed:
            return Response(
                {'error': f'Unsupported format: .{ext}. Allowed: {", ".join(allowed)}', 'success': False},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        if pid_file.size > max_mb * 1024 * 1024:
            return Response(
                {'error': f'{pid_file.name} exceeds {max_mb} MB limit', 'success': False},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

    for pid_file in files:
        drawing_ref = pid_file.name.rsplit('.', 1)[0]
        drawing_refs.append(drawing_ref)
        logger.info('[EquipmentList Batch] Analyzing: %s  upload_id=%s', pid_file.name, upload_id)
        try:
            text      = _extract_text_from_pdf(pid_file, config)
            equipment = _extract_equipment_items(text, drawing_ref, config)
            for idx, item in enumerate(equipment, 1):
                item['sl_no'] = idx
                item.setdefault('drawing_ref', drawing_ref)
            all_equipment.extend(equipment)
        except Exception as exc:
            logger.error('[EquipmentList Batch] Error on %s: %s', pid_file.name, exc, exc_info=True)
            _result_store[upload_id] = {'status': 'failed', 'error': str(exc)}
            return Response({'error': f'Extraction failed for {pid_file.name}: {exc}', 'success': False},
                            status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Re-number across drawings
    for idx, item in enumerate(all_equipment, 1):
        item['sl_no'] = idx

    _result_store[upload_id] = {
        'status':      'completed',
        'equipment':   all_equipment,
        'total':       len(all_equipment),
        'drawing_ref': ', '.join(drawing_refs),
    }

    logger.info('[EquipmentList Batch] Done: %d items from %d drawing(s)', len(all_equipment), len(files))

    return Response({
        'success':     True,
        'upload_id':   upload_id,
        'status':      'completed',
        'equipment':   all_equipment,
        'total':       len(all_equipment),
        'drawing_ref': ', '.join(drawing_refs),
        'columns':     [c['label'] for c in config.get('excel_columns', []) if c['key'] != 'sl_no'],
    }, status=drf_status.HTTP_200_OK)
