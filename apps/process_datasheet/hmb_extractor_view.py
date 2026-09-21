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
import hashlib
import io
import logging
import os
import re
import tempfile

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from apps.project_organizer.models import Project
from .models import HMBMasterTemplateProfile, HMBCaseImportBatch, HMBCaseRecord
from .hmb_master_template_parser import (
    analyze_hmb_master_template,
    parse_hmb_case_workbook,
    HMB_MASTER_TEMPLATE_CONFIG,
    HMB_MASTER_TEMPLATE_ANALYSIS_VERSION,
    HMB_FIXED_CASE_LABELS,
)

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
_MASTER_TEMPLATE_MAX_SIZE_BYTES = 20 * 1024 * 1024
_MASTER_TEMPLATE_ALLOWED_EXTS = ('.xlsx', '.xlsm')
_CASE_TEMPLATE_MAX_SIZE_BYTES = 25 * 1024 * 1024
_CASE_TEMPLATE_ALLOWED_EXTS = ('.xlsx', '.xlsm')


def _serialize_template_profile(profile: HMBMasterTemplateProfile) -> dict:
    return {
        'id': str(profile.id),
        'project_id': str(profile.project_id) if profile.project_id else None,
        'template_name': profile.template_name,
        'source_filename': profile.source_filename,
        'file_sha256': profile.file_sha256,
        'sheet_name': profile.sheet_name,
        'case_title': profile.case_title,
        'stream_count': profile.stream_count,
        'section_count': profile.section_count,
        'property_row_count': profile.property_row_count,
        'analysis_version': profile.analysis_version,
        'is_active': profile.is_active,
        'created_at': profile.created_at,
        'updated_at': profile.updated_at,
    }


def _is_admin(user) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return True
    role = (getattr(user, 'role', '') or '').lower()
    return role in {'admin', 'super_admin', 'tenant_admin'}


def _get_accessible_project(user, project_id: str):
    try:
        project = Project.objects.get(project_id=project_id)
    except Project.DoesNotExist:
        return None, Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)

    if not _is_admin(user) and project.created_by_id != getattr(user, 'id', None):
        return None, Response({'error': 'Access denied for this project.'}, status=status.HTTP_403_FORBIDDEN)
    return project, None


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


def _build_hmb_stream_comparison(project, template_profile, stream_id: str) -> dict:
    payload = template_profile.analysis_payload or {}
    sections = payload.get('sections', [])
    stream_columns = payload.get('stream_columns', [])
    stream_meta = next(
        (stream for stream in stream_columns if str(stream.get('stream_id', '')) == stream_id),
        None,
    )
    if stream_meta is None:
        raise ValueError('Stream ID is not present in the selected Master template.')

    records = list(
        HMBCaseRecord.objects.filter(
            project=project,
            template_profile=template_profile,
            stream_id=stream_id,
        ).values('case_name', 'row_index', 'value_text')
    )
    available_cases = sorted({record['case_name'] for record in records})
    case_names = list(HMB_FIXED_CASE_LABELS)
    case_names.extend(name for name in available_cases if name not in HMB_FIXED_CASE_LABELS)
    values = {
        (record['case_name'], record['row_index']): record['value_text']
        for record in records
    }
    rows = []
    for section in sections:
        for prop in section.get('properties', []) or []:
            row_index = int(prop.get('row') or 0)
            rows.append({
                'section_key': section.get('key', ''),
                'section': section.get('label', ''),
                'row_index': row_index,
                'property': prop.get('property', ''),
                'unit': prop.get('unit', ''),
                'values': {case: values.get((case, row_index), '') for case in case_names},
            })
    return {
        'project_id': str(project.project_id),
        'template_profile_id': str(template_profile.id),
        'stream': stream_meta,
        'fixed_case_slots': HMB_FIXED_CASE_LABELS,
        'case_names': case_names,
        'rows': rows,
    }


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


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def analyze_hmb_master_template_view(request):
    """
    Analyze HMB Master Template workbook (Phase 1)

    POST /api/v1/process-datasheet/datasheets/analyze-hmb-master-template/

    Body (multipart/form-data):
        - master_template_file: Excel workbook (.xlsx/.xlsm)

    Returns:
        - workbook structure summary (streams, sections, row schema)
        - compact long-format preview records
    """
    template_file = request.FILES.get('master_template_file')
    if not template_file:
        return Response(
            {'error': 'Master template file (master_template_file) is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    filename_lower = (template_file.name or '').lower()
    if not filename_lower.endswith(_MASTER_TEMPLATE_ALLOWED_EXTS):
        return Response(
            {'error': 'Master template must be .xlsx or .xlsm'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if template_file.size > _MASTER_TEMPLATE_MAX_SIZE_BYTES:
        return Response(
            {'error': 'Master template exceeds 20MB limit'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    project = None
    project_id = request.data.get('project_id')
    if project_id:
        project, project_err = _get_accessible_project(request.user, project_id)
        if project_err:
            return project_err

    temp_path = None
    hasher = hashlib.sha256()
    try:
        suffix = '.xlsm' if filename_lower.endswith('.xlsm') else '.xlsx'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            for chunk in template_file.chunks():
                tmp.write(chunk)
                hasher.update(chunk)
            temp_path = tmp.name

        analysis = analyze_hmb_master_template(temp_path)
        file_sha = hasher.hexdigest()

        template_name = (request.data.get('template_name') or '').strip()
        profile_defaults = {
            'template_name': template_name,
            'source_filename': template_file.name,
            'project': project,
            'sheet_name': analysis.get('template_meta', {}).get('sheet_name', ''),
            'case_title': analysis.get('template_meta', {}).get('case_title', ''),
            'stream_count': analysis.get('summary', {}).get('stream_count', 0),
            'section_count': analysis.get('summary', {}).get('section_count', 0),
            'property_row_count': analysis.get('summary', {}).get('property_row_count', 0),
            'analysis_version': HMB_MASTER_TEMPLATE_ANALYSIS_VERSION,
            'config_snapshot': HMB_MASTER_TEMPLATE_CONFIG,
            'analysis_payload': {
                'template_meta': analysis.get('template_meta', {}),
                'summary': analysis.get('summary', {}),
                'stream_columns': analysis.get('stream_columns', []),
                'sections': analysis.get('sections', []),
                'template_layout': analysis.get('template_layout', {}),
                'baseline': {
                    key: value
                    for key, value in analysis.get('baseline', {}).items()
                    if key != 'records'
                },
                'named_ranges': analysis.get('named_ranges', []),
                'warnings': analysis.get('warnings', []),
            },
            'normalized_preview': analysis.get('normalized_preview', []),
            'is_active': True,
        }

        profile, _created = HMBMasterTemplateProfile.objects.update_or_create(
            created_by=request.user,
            file_sha256=file_sha,
            defaults=profile_defaults,
        )

        return Response({
            'success': True,
            'message': 'Master template analyzed successfully.',
            'template_profile_id': str(profile.id),
            'template_profile': _serialize_template_profile(profile),
            'baseline': {
                key: value
                for key, value in analysis.get('baseline', {}).items()
                if key != 'records'
            },
            **analysis,
        })
    except Exception as exc:
        logger.error('[HMB Master Template] analysis failed: %s', exc, exc_info=True)
        return Response(
            {'error': f'Master template analysis failed: {str(exc)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except Exception as cleanup_err:
                logger.warning('[HMB Master Template] cleanup warning: %s', cleanup_err)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_hmb_master_templates_view(request):
    """List persisted HMB master template profiles for the current user."""
    profiles = HMBMasterTemplateProfile.objects.filter(is_active=True)
    if not _is_admin(request.user):
        profiles = profiles.filter(created_by=request.user)

    project_id = request.query_params.get('project_id')
    if project_id:
        profiles = profiles.filter(project_id=project_id)

    return Response({
        'success': True,
        'count': profiles.count(),
        'results': [_serialize_template_profile(p) for p in profiles[:100]],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def retrieve_hmb_master_template_view(request, profile_id):
    """Retrieve one persisted HMB master template profile and its stored payload."""
    try:
        profile = HMBMasterTemplateProfile.objects.get(
            id=profile_id,
            is_active=True,
        )
    except HMBMasterTemplateProfile.DoesNotExist:
        return Response({'error': 'Template profile not found'}, status=status.HTTP_404_NOT_FOUND)

    if not _is_admin(request.user) and profile.created_by_id != getattr(request.user, 'id', None):
        return Response({'error': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

    return Response({
        'success': True,
        'template_profile': _serialize_template_profile(profile),
        'template_meta': (profile.analysis_payload or {}).get('template_meta', {}),
        'summary': (profile.analysis_payload or {}).get('summary', {}),
        'stream_columns': (profile.analysis_payload or {}).get('stream_columns', []),
        'sections': (profile.analysis_payload or {}).get('sections', []),
        'template_layout': (profile.analysis_payload or {}).get('template_layout', {}),
        'baseline': (profile.analysis_payload or {}).get('baseline', {}),
        'named_ranges': (profile.analysis_payload or {}).get('named_ranges', []),
        'warnings': (profile.analysis_payload or {}).get('warnings', []),
        'normalized_preview': profile.normalized_preview or [],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def import_hmb_case_files_view(request):
    """
    Import multiple HMB case workbook files, normalize them, and persist rows.

    POST /api/v1/process-datasheet/datasheets/import-hmb-cases/

    Body (multipart/form-data):
      - project_id (required)
      - template_profile_id (optional)
      - case_files (one or many .xlsx/.xlsm)
    """
    project_id = request.data.get('project_id')
    if not project_id:
        return Response({'error': 'project_id is required'}, status=status.HTTP_400_BAD_REQUEST)

    project, project_err = _get_accessible_project(request.user, project_id)
    if project_err:
        return project_err

    template_profile = None
    template_profile_id = request.data.get('template_profile_id')
    if template_profile_id:
        try:
            template_profile = HMBMasterTemplateProfile.objects.get(
                id=template_profile_id,
                is_active=True,
            )
        except HMBMasterTemplateProfile.DoesNotExist:
            return Response({'error': 'template_profile_id not found'}, status=status.HTTP_404_NOT_FOUND)

        if not _is_admin(request.user) and template_profile.created_by_id != getattr(request.user, 'id', None):
            return Response({'error': 'Access denied for template profile.'}, status=status.HTTP_403_FORBIDDEN)

        if template_profile.project_id and template_profile.project_id != project.project_id:
            return Response({'error': 'template_profile_id does not belong to the selected project.'}, status=status.HTTP_400_BAD_REQUEST)

    auto_template_applied = False
    auto_template_scope = ''
    if template_profile is None:
        profile_qs = HMBMasterTemplateProfile.objects.filter(
            is_active=True,
            project=project,
        )
        if not _is_admin(request.user):
            profile_qs = profile_qs.filter(created_by_id=getattr(request.user, 'id', None))
        template_profile = profile_qs.order_by('-updated_at').first()
        if template_profile:
            auto_template_applied = True
            auto_template_scope = 'project'
        else:
            # Fallback to global defaults when a project has no scoped template.
            # This keeps case imports linked to the active master schema.
            template_profile = HMBMasterTemplateProfile.objects.filter(
                is_active=True,
                project__isnull=True,
            ).order_by('-updated_at').first()
            auto_template_applied = bool(template_profile)
            auto_template_scope = 'global' if template_profile else ''

    files = request.FILES.getlist('case_files')
    if not files:
        one_file = request.FILES.get('case_files') or request.FILES.get('case_file')
        if one_file:
            files = [one_file]
    if not files:
        return Response({'error': 'At least one case file is required (case_files)'}, status=status.HTTP_400_BAD_REQUEST)

    batch = HMBCaseImportBatch.objects.create(
        project=project,
        template_profile=template_profile,
        imported_by=request.user,
        source_file_count=len(files),
        status='completed',
    )

    total_records = 0
    file_summaries = []
    failures = []
    imported_case_names = set()

    for file_obj in files:
        filename_lower = (file_obj.name or '').lower()
        if not filename_lower.endswith(_CASE_TEMPLATE_ALLOWED_EXTS):
            failures.append({'filename': file_obj.name, 'error': 'Unsupported extension'})
            continue
        if file_obj.size > _CASE_TEMPLATE_MAX_SIZE_BYTES:
            failures.append({'filename': file_obj.name, 'error': 'File exceeds 25MB limit'})
            continue

        temp_path = None
        try:
            suffix = '.xlsm' if filename_lower.endswith('.xlsm') else '.xlsx'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in file_obj.chunks():
                    tmp.write(chunk)
                temp_path = tmp.name

            parsed = parse_hmb_case_workbook(
                temp_path,
                source_filename=file_obj.name,
                template_profile_payload=(template_profile.analysis_payload or {}) if template_profile else None,
            )
            case_name = parsed.get('case_name', '')
            if case_name in imported_case_names:
                failures.append({
                    'filename': file_obj.name,
                    'case_name': case_name,
                    'error': f'Duplicate case slot in this import: {case_name}',
                })
                continue
            imported_case_names.add(case_name)

            file_summaries.append({
                'filename': file_obj.name,
                'case_name': case_name,
                'sheet_name': parsed.get('sheet_name', ''),
                'detected_format': parsed.get('detected_format', ''),
                'stream_count': parsed.get('stream_count', 0),
                'record_count': parsed.get('record_count', 0),
                'stream_mapping': parsed.get('stream_mapping', {}),
                'exceptions': parsed.get('exceptions', {}),
            })

            replacement_rows = [
                HMBCaseRecord(
                    batch=batch,
                    project=project,
                    template_profile=template_profile,
                    case_name=case_name,
                    source_filename=rec.get('source_filename', file_obj.name),
                    stream_id=rec.get('stream_id', ''),
                    stream_description=rec.get('stream_description', ''),
                    section_key=rec.get('section_key', ''),
                    section_label=rec.get('section_label', ''),
                    property_name=rec.get('property_name', ''),
                    unit=rec.get('unit', ''),
                    source_stream_id=rec.get('source_stream_id', rec.get('stream_id', '')),
                    value_text=rec.get('value_text', ''),
                    row_index=rec.get('row_index', 0),
                )
                for rec in parsed.get('records', [])
            ]
            if not replacement_rows:
                failures.append({
                    'filename': file_obj.name,
                    'case_name': case_name,
                    'error': 'No mapped records were found in the workbook.',
                })
                file_summaries.pop()
                continue

            with transaction.atomic():
                HMBCaseRecord.objects.filter(
                    project=project,
                    template_profile=template_profile,
                    case_name=case_name,
                ).delete()
                HMBCaseRecord.objects.bulk_create(replacement_rows, batch_size=2000)
            total_records += len(replacement_rows)
        except Exception as exc:
            failures.append({'filename': file_obj.name, 'error': str(exc)})
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except Exception as cleanup_err:
                    logger.warning('[HMB Case Import] cleanup warning: %s', cleanup_err)

    batch.total_records = total_records
    batch.metadata = {
        'files': file_summaries,
        'failures': failures,
    }
    if failures and not total_records:
        batch.status = 'failed'
    batch.save(update_fields=['total_records', 'metadata', 'status'])

    return Response({
        'success': True,
        'batch_id': str(batch.id),
        'project_id': str(project.project_id),
        'template_profile_id': str(template_profile.id) if template_profile else None,
        'template_profile_name': (
            (template_profile.template_name or template_profile.source_filename)
            if template_profile else ''
        ),
        'auto_template_applied': auto_template_applied,
        'auto_template_scope': auto_template_scope,
        'source_file_count': len(files),
        'imported_file_count': len(file_summaries),
        'failed_file_count': len(failures),
        'total_records': total_records,
        'files': file_summaries,
        'failures': failures,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hmb_project_consolidated_summary_view(request, project_id):
    """Project-level summary of persisted HMB case records (database view)."""
    project, project_err = _get_accessible_project(request.user, project_id)
    if project_err:
        return project_err

    template_profile_id = request.query_params.get('template_profile_id')
    qs = HMBCaseRecord.objects.filter(project=project)
    if template_profile_id:
        qs = qs.filter(template_profile_id=template_profile_id)

    total_records = qs.count()
    case_names = sorted(set(qs.values_list('case_name', flat=True)))
    stream_ids = sorted(set(qs.values_list('stream_id', flat=True)))

    section_counts = {}
    for row in qs.values('section_key', 'section_label').order_by('section_label').distinct():
        key = row['section_key']
        section_counts[key] = {
            'label': row['section_label'],
            'records': qs.filter(section_key=key).count(),
        }

    return Response({
        'success': True,
        'project_id': str(project.project_id),
        'template_profile_id': template_profile_id,
        'total_records': total_records,
        'case_count': len(case_names),
        'stream_count': len(stream_ids),
        'cases': case_names,
        'sections': section_counts,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hmb_project_records_preview_view(request, project_id):
    """Return imported HMB case records for frontend canvas preview."""
    project, project_err = _get_accessible_project(request.user, project_id)
    if project_err:
        return project_err

    template_profile_id = request.query_params.get('template_profile_id')
    case_name = (request.query_params.get('case_name') or '').strip()
    all_cases = str(request.query_params.get('all_cases', '')).strip().lower() in {'1', 'true', 'yes'}
    limit_raw = request.query_params.get('limit')

    try:
        limit = int(limit_raw) if limit_raw is not None else 5000
    except (TypeError, ValueError):
        limit = 5000
    limit = max(100, min(limit, 50000))

    qs_all = HMBCaseRecord.objects.filter(project=project)
    qs = qs_all
    template_filter_applied = False
    template_filter_relaxed = False
    if template_profile_id:
        qs_by_template = qs_all.filter(template_profile_id=template_profile_id)
        if qs_by_template.exists():
            qs = qs_by_template
            template_filter_applied = True
        else:
            # Backward-compatibility: old imports may not have template_profile set.
            # In this case we relax template filtering so the canvas still shows data.
            qs = qs_all
            template_filter_relaxed = True
    if case_name:
        qs = qs.filter(case_name=case_name)

    latest_case = qs.order_by('-id').values_list('case_name', flat=True).first() or ''
    if latest_case and not case_name and not all_cases:
        qs = qs.filter(case_name=latest_case)

    records = list(
        qs.order_by('section_label', 'row_index', 'stream_id').values(
            'case_name',
            'source_filename',
            'section_key',
            'section_label',
            'row_index',
            'property_name',
            'unit',
            'stream_id',
            'source_stream_id',
            'value_text',
        )[:limit]
    )

    available_cases = sorted(set(
        HMBCaseRecord.objects.filter(project=project)
        .values_list('case_name', flat=True)
    ))
    available_case_files = sorted(set(
        HMBCaseRecord.objects.filter(project=project)
        .values_list('source_filename', flat=True)
    ))

    return Response({
        'success': True,
        'project_id': str(project.project_id),
        'template_profile_id': template_profile_id,
        'template_filter_applied': template_filter_applied,
        'template_filter_relaxed': template_filter_relaxed,
        'all_cases': all_cases,
        'case_name': case_name or latest_case,
        'available_cases': available_cases,
        'available_case_files': available_case_files,
        'record_count': len(records),
        'records': records,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hmb_project_stream_comparison_view(request, project_id):
    project, project_err = _get_accessible_project(request.user, project_id)
    if project_err:
        return project_err

    template_profile_id = request.query_params.get('template_profile_id')
    stream_id = (request.query_params.get('stream_id') or '').strip()
    if not template_profile_id or not stream_id:
        return Response(
            {'error': 'template_profile_id and stream_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        profile = HMBMasterTemplateProfile.objects.get(id=template_profile_id, is_active=True)
    except (HMBMasterTemplateProfile.DoesNotExist, ValueError):
        return Response({'error': 'Template profile not found'}, status=status.HTTP_404_NOT_FOUND)
    if profile.project_id and profile.project_id != project.project_id:
        return Response({'error': 'Template profile does not belong to this project'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        comparison = _build_hmb_stream_comparison(project, profile, stream_id)
    except ValueError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'success': True, **comparison})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hmb_project_stream_export_view(request, project_id):
    project, project_err = _get_accessible_project(request.user, project_id)
    if project_err:
        return project_err

    template_profile_id = request.query_params.get('template_profile_id')
    stream_id = (request.query_params.get('stream_id') or '').strip()
    if not template_profile_id or not stream_id:
        return Response(
            {'error': 'template_profile_id and stream_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        profile = HMBMasterTemplateProfile.objects.get(id=template_profile_id, is_active=True)
    except (HMBMasterTemplateProfile.DoesNotExist, ValueError):
        return Response({'error': 'Template profile not found'}, status=status.HTTP_404_NOT_FOUND)
    if profile.project_id and profile.project_id != project.project_id:
        return Response({'error': 'Template profile does not belong to this project'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        comparison = _build_hmb_stream_comparison(project, profile, stream_id)
    except ValueError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Overall'
    case_names = comparison['case_names']
    stream = comparison['stream']
    sheet['A1'] = stream_id
    sheet['C1'] = 'Stream'
    sheet['C2'] = 'Description'
    sheet['C3'] = 'PFD No.'
    sheet['A4'] = 'Phase'
    sheet['B4'] = 'Property'
    sheet['C4'] = 'Unit'
    for index, case_name in enumerate(case_names, start=4):
        sheet.cell(1, index, stream_id)
        sheet.cell(2, index, stream.get('description', ''))
        sheet.cell(4, index, case_name)
    for output_row, row in enumerate(comparison['rows'], start=5):
        sheet.cell(output_row, 1, row['section'])
        sheet.cell(output_row, 2, row['property'])
        sheet.cell(output_row, 3, row['unit'])
        for index, case_name in enumerate(case_names, start=4):
            value = row['values'].get(case_name, '')
            try:
                value = float(value) if value not in ('', None) else ''
            except (TypeError, ValueError):
                pass
            sheet.cell(output_row, index, value)

    header_fill = PatternFill('solid', fgColor='D9EAF7')
    section_fill = PatternFill('solid', fgColor='E2F0D9')
    for cell in sheet[4]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    section_start = 5
    last_section = sheet.cell(5, 1).value if sheet.max_row >= 5 else None
    for row_number in range(6, sheet.max_row + 2):
        section = sheet.cell(row_number, 1).value if row_number <= sheet.max_row else None
        if section == last_section:
            continue
        if row_number - 1 > section_start:
            sheet.merge_cells(start_row=section_start, start_column=1, end_row=row_number - 1, end_column=1)
        anchor = sheet.cell(section_start, 1)
        anchor.fill = section_fill
        anchor.font = Font(bold=True)
        anchor.alignment = Alignment(horizontal='center', vertical='center', text_rotation=90)
        last_section = section
        section_start = row_number
    sheet.column_dimensions['A'].width = 15
    sheet.column_dimensions['B'].width = 34
    sheet.column_dimensions['C'].width = 16
    for col in range(4, 4 + len(case_names)):
        sheet.column_dimensions[get_column_letter(col)].width = 18
    sheet.freeze_panes = 'D5'
    sheet.auto_filter.ref = f'A4:{get_column_letter(max(3, sheet.max_column))}{sheet.max_row}'

    output = io.BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    safe_stream = re.sub(r'[^A-Za-z0-9_.-]+', '_', stream_id) or 'stream'
    response['Content-Disposition'] = f'attachment; filename="HMB_Final_{safe_stream}.xlsx"'
    return response
