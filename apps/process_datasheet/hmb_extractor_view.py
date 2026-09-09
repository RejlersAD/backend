"""
HMB Extractor — standalone Heat & Material Balance stream extraction (SYNC)
============================================================================
Reuses the existing `HMBVisionExtractor` service (already used internally by
the SDV/MOV pipelines) but exposes it as its own independent feature — no
P&ID upload, no valve/equipment mapping. Upload an HMB PDF, get back the
extracted stream table as a downloadable Excel workbook.

Does not modify any existing view, service or pipeline.
"""
import base64
import io
import logging
import os
import tempfile

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.http import JsonResponse
from openpyxl import Workbook

logger = logging.getLogger(__name__)

# Soft-coded column order/labels for the output workbook
_STREAM_COLUMNS = [
    ('stream_id', 'Stream ID'),
    ('line_no', 'Line No'),
    ('fluid', 'Fluid'),
    ('phase', 'Phase'),
    ('state', 'State'),
    ('pressure_normal', 'Pressure (Normal)'),
    ('pressure_design', 'Pressure (Design)'),
    ('pressure_unit', 'Pressure Unit'),
    ('temp_min', 'Temp Min'),
    ('temp_normal', 'Temp Normal'),
    ('temp_max', 'Temp Max'),
    ('temp_unit', 'Temp Unit'),
    ('design_temp_min', 'Design Temp Min'),
    ('design_temp_max', 'Design Temp Max'),
    ('design_temp_unit', 'Design Temp Unit'),
    ('shut_off_pressure', 'Shut-off Pressure'),
]

_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024


def _build_excel(streams):
    wb = Workbook()
    ws = wb.active
    ws.title = 'HMB Streams'
    ws.append([label for _, label in _STREAM_COLUMNS])
    for stream in streams:
        ws.append([stream.get(key, '') for key, _ in _STREAM_COLUMNS])
    for col_idx, (_, label) in enumerate(_STREAM_COLUMNS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = max(14, len(label) + 2)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _build_html_preview(streams):
    if not streams:
        return '<p>No streams detected in this HMB document.</p>'
    rows = ''.join(
        '<tr>'
        f"<td>{s.get('stream_id', '') or ''}</td>"
        f"<td>{s.get('fluid', '') or ''}</td>"
        f"<td>{s.get('phase', '') or ''}</td>"
        f"<td>{s.get('pressure_normal', '') or ''} {s.get('pressure_unit', '') or ''}</td>"
        f"<td>{s.get('temp_normal', '') or ''} {s.get('temp_unit', '') or ''}</td>"
        '</tr>'
        for s in streams[:25]
    )
    return (
        '<table class="hmb-preview-table">'
        '<thead><tr><th>Stream</th><th>Fluid</th><th>Phase</th>'
        '<th>Pressure</th><th>Temperature</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def extract_hmb_data(request):
    """
    HMB Extractor (standalone)

    POST /api/v1/process-datasheet/datasheets/extract-hmb/

    Body (multipart/form-data):
        - hmb_file: HMB PDF file (required)

    Returns (sync JSON):
        - success, html_preview, excel_file (base64), filename, message
    """
    try:
        hmb_file = request.FILES.get('hmb_file')
        if not hmb_file:
            return Response(
                {'error': 'HMB file (hmb_file) is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not hmb_file.name.lower().endswith('.pdf'):
            return Response(
                {'error': 'HMB file must be PDF'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if hmb_file.size > _MAX_FILE_SIZE_BYTES:
            return Response(
                {'error': 'HMB file exceeds 50MB limit'},
                status=status.HTTP_400_BAD_REQUEST
            )

        hmb_temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as hmb_temp:
                for chunk in hmb_file.chunks():
                    hmb_temp.write(chunk)
                hmb_temp_path = hmb_temp.name

            from apps.process_datasheet.hmb_vision_extractor import HMBVisionExtractor
            hmb_data = HMBVisionExtractor().extract_from_pdf(hmb_temp_path)
            streams = hmb_data.get('streams', [])

            excel_buf = _build_excel(streams)
            excel_base64 = base64.b64encode(excel_buf.read()).decode('utf-8')

            base_name = os.path.splitext(hmb_file.name)[0] or 'HMB'
            return JsonResponse({
                'success': True,
                'html_preview': _build_html_preview(streams),
                'excel_file': excel_base64,
                'filename': f'HMB_Streams_{base_name}.xlsx',
                'message': f'Extracted {len(streams)} stream(s) from HMB document.',
                'stream_count': len(streams),
                'process_conditions': hmb_data.get('process_conditions', {}),
            })
        finally:
            if hmb_temp_path:
                try:
                    os.unlink(hmb_temp_path)
                except Exception as cleanup_err:
                    logger.warning(f"[HMB Extractor] Cleanup warning: {cleanup_err}")

    except Exception as e:
        logger.error(f"[HMB Extractor] Error: {e}", exc_info=True)
        return Response(
            {'error': f'HMB extraction failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
