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
import json
import logging
import os
import re
import tempfile
import uuid

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from django.db.models import Q
from django.core.cache import cache
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from apps.project_organizer.models import Project
from .models import HMBMasterTemplateProfile, HMBCaseImportBatch, HMBCaseRecord, HMBSourceUpload
from .services.hmb_storage import HMBStorageError, delete_hmb_source, store_hmb_source
from .hmb_master_template_parser import (
    analyze_hmb_master_template,
    parse_hmb_case_workbook,
    parse_hmb_csv_file,
    HMB_MASTER_TEMPLATE_CONFIG,
    HMB_MASTER_TEMPLATE_ANALYSIS_VERSION,
    HMB_FIXED_CASE_LABELS,
    canonical_hmb_case_name,
    hmb_property_identity,
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

HMB_IMPORT_PREVIEW_CONFIG = {
    'cache_prefix': 'hmb-import-preview:',
    'ttl_seconds': 30 * 60,
    'max_files': 12,
    'max_size_bytes': 50 * 1024 * 1024,
    'allowed_extensions': ('.xlsx', '.xlsm', '.csv', '.pdf'),
    'sample_record_limit': 24,
    'high_confidence': 0.85,
}

HMB_PDF_PROPERTY_FIELDS = {
    'temperature': ('temp_normal', 'temp_unit'),
    'pressure': ('pressure_normal', 'pressure_unit'),
    'mass flow': ('mass_flow', 'mass_flow_unit'),
    'molecular weight': ('molecular_weight', None),
}


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


def _serialize_source_upload(source_upload: HMBSourceUpload) -> dict:
    return {
        'id': str(source_upload.id),
        'upload_kind': source_upload.upload_kind,
        'original_filename': source_upload.original_filename,
        'size_bytes': source_upload.size_bytes,
        'status': source_upload.status,
        'created_at': source_upload.created_at,
    }


def _is_admin(user) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return True
    role = (getattr(user, 'role', '') or '').lower()
    return role in {'admin', 'super_admin', 'tenant_admin'}


# Soft-coded HMB project access policy. Mirrors the RBAC VisibilityStrategy
# pattern used across the platform (module-team collaboration) instead of a
# bespoke owner-only rule.
HMB_ACCESS_CONFIG = {
    # 'owner'   → only admin or the project creator (legacy behaviour).
    # 'module_team' → admin, the creator, OR any user who has the
    #                 `process_datasheet` module (same-module collaboration).
    'strategy': os.getenv('HMB_ACCESS_STRATEGY', 'module_team').strip().lower(),
    # Module whose grant confers team visibility when strategy=module_team.
    'team_module_code': os.getenv('HMB_ACCESS_TEAM_MODULE', 'process_datasheet').strip(),
}


def _user_has_hmb_team_access(user) -> bool:
    """True when the user holds the soft-coded team module (module_team mode)."""
    try:
        from apps.rbac.data_visibility_config import user_has_module_access
        return user_has_module_access(user, HMB_ACCESS_CONFIG['team_module_code'])
    except Exception:
        return False


def _get_accessible_project(user, project_id: str):
    try:
        project = Project.objects.select_related('created_by').get(project_id=project_id)
    except Project.DoesNotExist:
        return None, Response({'error': 'Project not found.'}, status=status.HTTP_404_NOT_FOUND)

    if _is_admin(user) or project.created_by_id == getattr(user, 'id', None):
        return project, None
    # Team visibility: same rule as the shared project organizer — a user who
    # shares a collaboration module with the project owner can open the project.
    if HMB_ACCESS_CONFIG['strategy'] == 'module_team':
        try:
            from apps.project_organizer.views import _shares_team_module
            if _shares_team_module(user, project.created_by):
                return project, None
        except Exception:
            logger.warning('Unable to verify shared project access.')
    return None, Response({'error': 'Access denied for this project.'}, status=status.HTTP_403_FORBIDDEN)


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
        ).values(
            'case_name', 'section_key', 'property_name', 'unit',
            'row_index', 'value_text',
            'source_filename', 'source_stream_id', 'source_metadata',
        )
    )
    available_cases = sorted({canonical_hmb_case_name(name) for name in HMBCaseRecord.objects.filter(
        project=project, template_profile=template_profile,
    ).values_list('case_name', flat=True).distinct()})
    output_template = HMBSourceUpload.objects.filter(project=project, upload_kind='output_template').first()
    configured_cases = list(dict.fromkeys(
        canonical_hmb_case_name(name) for name in (output_template.metadata.get('case_slots', []) if output_template else [])
    ))
    case_names = configured_cases + [name for name in available_cases if name not in configured_cases]
    values = {}
    sources = {}
    conflicts = []
    for record in records:
        identity = hmb_property_identity(
            record.get('section_key'),
            record.get('property_name'),
            record.get('unit'),
        )
        key = (canonical_hmb_case_name(record['case_name']), identity)
        if key in values and values[key] != record['value_text']:
            conflicts.append({
                'case_name': key[0],
                'section_key': identity[0],
                'property_key': identity[1],
                'unit_key': identity[2],
            })
            continue
        values[key] = record['value_text']
        sources[key] = {
            'filename': record['source_filename'], 'stream_id': record['source_stream_id'],
            'status': 'legacy', **(record.get('source_metadata') or {}),
        }
    rows = []
    for section in sections:
        for prop in section.get('properties', []) or []:
            row_index = int(prop.get('row') or 0)
            identity = hmb_property_identity(
                section.get('key', ''),
                prop.get('property', ''),
                prop.get('unit', ''),
            )
            rows.append({
                'section_key': section.get('key', ''),
                'section': section.get('label', ''),
                'row_index': row_index,
                'property': prop.get('property', ''),
                'unit': prop.get('unit', ''),
                'values': {case: values.get((case, identity), '') for case in case_names},
                'sources': {case: sources.get((case, identity), {}) for case in case_names},
            })
    return {
        'project_id': str(project.project_id),
        'template_profile_id': str(template_profile.id),
        'stream': stream_meta,
        'fixed_case_slots': configured_cases,
        'case_names': case_names,
        'rows': rows,
        'conflicts': conflicts,
    }


def _normalise_pdf_hmb(streams, source_filename: str, template_payload: dict) -> dict:
    template_streams = {
        str(stream.get('stream_id', '')).strip(): stream
        for stream in template_payload.get('stream_columns', []) or []
        if str(stream.get('stream_id', '')).strip()
    }
    general_section = next(
        (
            section for section in template_payload.get('sections', []) or []
            if str(section.get('key', '')).lower() == 'general'
        ),
        None,
    )
    properties = general_section.get('properties', []) if general_section else []
    records = []
    ignored_streams = []
    unresolved = []
    for stream in streams:
        stream_id = str(stream.get('stream_id', '') or '').strip()
        if stream_id not in template_streams:
            if stream_id:
                ignored_streams.append(stream_id)
            continue
        for prop in properties:
            property_key = hmb_property_identity('general', prop.get('property'), prop.get('unit'))[1]
            field_config = HMB_PDF_PROPERTY_FIELDS.get(property_key)
            if not field_config:
                continue
            value_field, unit_field = field_config
            value = stream.get(value_field)
            if value in (None, '', '---'):
                continue
            source_unit = str(stream.get(unit_field, '') or '') if unit_field else ''
            template_unit = str(prop.get('unit', '') or '')
            if source_unit and template_unit and source_unit.lower() != template_unit.lower():
                unresolved.append({
                    'stream_id': stream_id,
                    'property': prop.get('property'),
                    'source_unit': source_unit,
                    'template_unit': template_unit,
                })
            records.append({
                'case_name': canonical_hmb_case_name(source_filename),
                'source_filename': source_filename,
                'sheet_name': 'PDF',
                'section_key': 'general',
                'section_label': general_section.get('label', 'General') if general_section else 'General',
                'row_index': int(prop.get('row') or 0),
                'property_name': str(prop.get('property', '') or ''),
                'unit': template_unit,
                'stream_id': stream_id,
                'source_stream_id': stream_id,
                'stream_description': str(template_streams[stream_id].get('description', '') or ''),
                'value_text': str(value),
            })
    mapped_streams = {record['stream_id'] for record in records}
    return {
        'case_name': canonical_hmb_case_name(source_filename),
        'sheet_name': 'PDF',
        'detected_format': 'pdf_vision',
        'stream_count': len(mapped_streams),
        'record_count': len(records),
        'records': records,
        'exceptions': {
            'unresolved_mappings': unresolved[:120],
            'unresolved_mappings_count': len(unresolved),
        },
        'stream_mapping': {
            'source_stream_count': len(streams),
            'mapped_source_stream_count': len(mapped_streams),
            'ignored_source_stream_count': len(set(ignored_streams)),
            'ignored_source_streams': sorted(set(ignored_streams))[:120],
            'template_stream_count': len(template_streams),
        },
    }


def _hmb_preview_file_summary(parsed: dict) -> dict:
    mapping = parsed.get('stream_mapping', {}) or {}
    source_stream_count = int(mapping.get('source_stream_count') or 0)
    mapped_stream_count = int(mapping.get('mapped_source_stream_count') or parsed.get('stream_count') or 0)
    template_stream_count = int(mapping.get('template_stream_count') or 0)
    exceptions = parsed.get('exceptions', {}) or {}
    unresolved_count = sum(int(exceptions.get(key) or 0) for key in (
        'unresolved_mappings_count',
        'unit_mismatches_count',
        'unmatched_streams_count',
        'duplicate_stream_matches_count',
    ))
    source_ratio = mapped_stream_count / source_stream_count if source_stream_count else 0
    template_ratio = mapped_stream_count / template_stream_count if template_stream_count else 0
    stream_ratio = max(source_ratio, template_ratio)
    confidence = max(0.0, min(1.0, stream_ratio - min(0.35, unresolved_count * 0.001)))
    if parsed.get('record_count', 0) and mapped_stream_count and source_stream_count == mapped_stream_count:
        confidence = max(confidence, 0.9)
    return {
        'filename': parsed.get('source_filename', ''),
        'file_id': parsed.get('file_id'),
        'blocking_errors': sum(int(exceptions.get(key) or 0) for key in (
            'unit_mismatches_count', 'duplicate_stream_matches_count', 'unresolved_mappings_count',
        )),
        'source_stored': bool(parsed.get('source_upload_id')),
        'source_upload_id': parsed.get('source_upload_id'),
        'case_name': parsed.get('case_name', ''),
        'detected_format': parsed.get('detected_format', ''),
        'stream_count': parsed.get('stream_count', 0),
        'record_count': parsed.get('record_count', 0),
        'confidence': round(confidence, 3),
        'requires_mapping': confidence < HMB_IMPORT_PREVIEW_CONFIG['high_confidence'] or not parsed.get('record_count'),
        'stream_mapping': mapping,
        'exceptions': exceptions,
        'sample_records': (parsed.get('records') or [])[:HMB_IMPORT_PREVIEW_CONFIG['sample_record_limit']],
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

        storage_result = store_hmb_source(
            temp_path,
            upload_kind=HMBSourceUpload.KIND_MASTER_TEMPLATE,
            original_filename=template_file.name,
            project_id=project.project_id if project else None,
            user_id=request.user.id,
        )
        storage_key = storage_result.get('key', '')
        try:
            with transaction.atomic():
                profile, _created = HMBMasterTemplateProfile.objects.update_or_create(
                    created_by=request.user,
                    project=project,
                    file_sha256=file_sha,
                    defaults=profile_defaults,
                )
                source_upload = None
                if storage_result['stored']:
                    source_upload = HMBSourceUpload.objects.create(
                        project=project,
                        template_profile=profile,
                        uploaded_by=request.user,
                        upload_kind=HMBSourceUpload.KIND_MASTER_TEMPLATE,
                        original_filename=template_file.name,
                        storage_key=storage_key,
                        file_sha256=storage_result['sha256'],
                        size_bytes=storage_result['size'],
                        content_type=storage_result['content_type'],
                    )
        except Exception:
            delete_hmb_source(storage_key)
            raise

        return Response({
            'success': True,
            'message': 'Master template analyzed successfully.',
            'template_profile_id': str(profile.id),
            'template_profile': _serialize_template_profile(profile),
            'source_upload': _serialize_source_upload(source_upload) if source_upload else None,
            'source_stored': bool(source_upload),
            'baseline': {
                key: value
                for key, value in analysis.get('baseline', {}).items()
                if key != 'records'
            },
            **analysis,
        })
    except HMBStorageError as exc:
        logger.error('[HMB Master Template] storage failed: %s', exc, exc_info=True)
        return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except ValueError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
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
    preferred_id = None
    project_id = request.query_params.get('project_id')
    if project_id:
        project, project_err = _get_accessible_project(request.user, project_id)
        if project_err:
            return project_err
        profiles = profiles.filter(
            Q(project=project) | Q(project__isnull=True, created_by=request.user)
        )
        preferred_id = HMBCaseImportBatch.objects.filter(
            project=project, status='completed', template_profile__in=profiles,
        ).order_by('-created_at').values_list('template_profile_id', flat=True).first()
        if not preferred_id:
            preferred_id = profiles.filter(project=project).values_list('id', flat=True).first()
    elif not _is_admin(request.user):
        profiles = profiles.filter(
            Q(project__isnull=True, created_by=request.user) | Q(project__created_by=request.user)
        ).distinct()

    rows = list(profiles[:100])
    if preferred_id:
        rows.sort(key=lambda profile: profile.id != preferred_id)
    return Response({
        'success': True,
        'count': profiles.count(),
        'preferred_template_profile_id': str(preferred_id) if preferred_id else None,
        'results': [_serialize_template_profile(p) for p in rows],
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

    if profile.project_id:
        _project, project_err = _get_accessible_project(request.user, profile.project_id)
        if project_err:
            return project_err
    elif not _is_admin(request.user) and profile.created_by_id != getattr(request.user, 'id', None):
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
def preview_hmb_case_files_view(request):
    project_id = request.data.get('project_id')
    template_profile_id = request.data.get('template_profile_id')
    if not project_id or not template_profile_id:
        return Response(
            {'error': 'project_id and template_profile_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    project, project_err = _get_accessible_project(request.user, project_id)
    if project_err:
        return project_err
    try:
        template_profile = HMBMasterTemplateProfile.objects.get(id=template_profile_id, is_active=True)
    except (HMBMasterTemplateProfile.DoesNotExist, ValueError):
        return Response({'error': 'Template profile not found'}, status=status.HTTP_404_NOT_FOUND)
    if template_profile.project_id and template_profile.project_id != project.project_id:
        return Response({'error': 'Template profile does not belong to this project'}, status=status.HTTP_400_BAD_REQUEST)
    if not template_profile.project_id and not _is_admin(request.user) and template_profile.created_by_id != request.user.id:
        return Response({'error': 'Access denied for template profile.'}, status=status.HTTP_403_FORBIDDEN)

    files = request.FILES.getlist('case_files')
    if not files:
        return Response({'error': 'At least one case file is required'}, status=status.HTTP_400_BAD_REQUEST)
    if len(files) > HMB_IMPORT_PREVIEW_CONFIG['max_files']:
        return Response(
            {'error': f'Maximum {HMB_IMPORT_PREVIEW_CONFIG["max_files"]} files per preview.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    parsed_files = []
    failures = []
    storage_failed = False
    template_payload = template_profile.analysis_payload or {}
    for file_obj in files:
        filename = file_obj.name or 'HMB input'
        extension = os.path.splitext(filename.lower())[1]
        if extension not in HMB_IMPORT_PREVIEW_CONFIG['allowed_extensions']:
            failures.append({'filename': filename, 'error': f'Unsupported extension: {extension or "none"}'})
            continue
        if file_obj.size > HMB_IMPORT_PREVIEW_CONFIG['max_size_bytes']:
            failures.append({'filename': filename, 'error': 'File exceeds 50MB limit'})
            continue
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
                for chunk in file_obj.chunks():
                    temp_file.write(chunk)
                temp_path = temp_file.name
            if extension == '.csv':
                parsed = parse_hmb_csv_file(temp_path, filename, template_payload)
            elif extension == '.pdf':
                from apps.process_datasheet.hmb_vision_extractor import HMBVisionExtractor
                extracted = HMBVisionExtractor().extract_from_pdf(temp_path)
                parsed = _normalise_pdf_hmb(extracted.get('streams', []), filename, template_payload)
            else:
                parsed = parse_hmb_case_workbook(temp_path, filename, template_payload)
            if not parsed.get('records'):
                raise ValueError('No mapped values found. Verify the selected master and source workbook.')
            storage_result = store_hmb_source(
                temp_path,
                upload_kind=HMBSourceUpload.KIND_CASE_FILE,
                original_filename=filename,
                project_id=project.project_id,
                user_id=request.user.id,
            )
            source_upload = None
            storage_key = storage_result.get('key', '')
            try:
                if storage_result['stored']:
                    source_upload = HMBSourceUpload.objects.create(
                        project=project,
                        template_profile=template_profile,
                        uploaded_by=request.user,
                        upload_kind=HMBSourceUpload.KIND_CASE_FILE,
                        original_filename=filename,
                        storage_key=storage_key,
                        file_sha256=storage_result['sha256'],
                        size_bytes=storage_result['size'],
                        content_type=storage_result['content_type'],
                    )
            except Exception:
                delete_hmb_source(storage_key)
                raise
            parsed['source_filename'] = filename
            parsed['file_id'] = str(uuid.uuid4())
            parsed['source_upload_id'] = str(source_upload.id) if source_upload else None
            parsed_files.append(parsed)
        except HMBStorageError as exc:
            storage_failed = True
            logger.error('[HMB Preview] %s storage failed: %s', filename, exc, exc_info=True)
            failures.append({'filename': filename, 'error': str(exc), 'code': 'storage_failed'})
        except Exception as exc:
            logger.warning('[HMB Preview] %s failed: %s', filename, exc, exc_info=True)
            failures.append({'filename': filename, 'error': str(exc)})
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    logger.warning('[HMB Preview] Could not remove temporary file %s', temp_path)

    if not parsed_files:
        return Response(
            {'error': 'No files could be analyzed', 'failures': failures},
            status=status.HTTP_503_SERVICE_UNAVAILABLE if storage_failed else status.HTTP_400_BAD_REQUEST,
        )
    duplicate_cases = {
        case_name
        for case_name in {parsed.get('case_name', '') for parsed in parsed_files}
        if sum(1 for parsed in parsed_files if parsed.get('case_name', '') == case_name) > 1
    }
    token = str(uuid.uuid4())
    cache_key = f'{HMB_IMPORT_PREVIEW_CONFIG["cache_prefix"]}{token}'
    cache.set(cache_key, {
        'user_id': getattr(request.user, 'id', None),
        'project_id': str(project.project_id),
        'template_profile_id': str(template_profile.id),
        'files': parsed_files,
    }, timeout=HMB_IMPORT_PREVIEW_CONFIG['ttl_seconds'])
    summaries = []
    for parsed in parsed_files:
        summary = _hmb_preview_file_summary(parsed)
        if parsed.get('case_name') in duplicate_cases:
            summary['requires_mapping'] = True
            summary['assignment_error'] = 'Duplicate suggested case slot; choose a unique slot before execution.'
        summaries.append(summary)
    return Response({
        'success': True,
        'preview_token': token,
        'expires_in_seconds': HMB_IMPORT_PREVIEW_CONFIG['ttl_seconds'],
        'files': summaries,
        'failures': failures,
        'can_execute': not duplicate_cases and all(not item['requires_mapping'] for item in summaries),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def execute_hmb_case_preview_view(request):
    token = (request.data.get('preview_token') or '').strip()
    if not token:
        return Response({'error': 'preview_token is required'}, status=status.HTTP_400_BAD_REQUEST)
    cache_key = f'{HMB_IMPORT_PREVIEW_CONFIG["cache_prefix"]}{token}'
    preview = cache.get(cache_key)
    if not preview:
        return Response({'error': 'Preview expired or was not found. Analyze the files again.'}, status=status.HTTP_410_GONE)
    if str(preview.get('user_id')) != str(getattr(request.user, 'id', None)):
        return Response({'error': 'Preview belongs to another user.'}, status=status.HTTP_403_FORBIDDEN)
    project, project_err = _get_accessible_project(request.user, preview['project_id'])
    if project_err:
        return project_err
    try:
        template_profile = HMBMasterTemplateProfile.objects.get(id=preview['template_profile_id'], is_active=True)
    except HMBMasterTemplateProfile.DoesNotExist:
        return Response({'error': 'Template profile not found'}, status=status.HTTP_404_NOT_FOUND)

    assignments = request.data.get('case_assignments') or {}
    if isinstance(assignments, str):
        try:
            assignments = json.loads(assignments)
        except json.JSONDecodeError:
            return Response({'error': 'case_assignments must be valid JSON'}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(assignments, dict):
        return Response({'error': 'case_assignments must be an object.'}, status=400)
    if template_profile.project_id and template_profile.project_id != project.pk:
        return Response({'error': 'Master no longer belongs to this project.'}, status=403)
    if not template_profile.project_id and not _is_admin(request.user) and template_profile.created_by_id != request.user.pk:
        return Response({'error': 'Access denied for master.'}, status=403)
    resolved_files = []
    case_names = set()
    for parsed in preview['files']:
        filename = parsed.get('source_filename', '')
        if _hmb_preview_file_summary(parsed)['blocking_errors']:
            return Response({'error': f'{filename} has unresolved unit or mapping errors. Correct the source and analyze again.'}, status=400)
        case_name = canonical_hmb_case_name(assignments.get(parsed.get('file_id')) or assignments.get(filename) or parsed.get('case_name'))
        if case_name in case_names:
            return Response({'error': f'Duplicate case assignment: {case_name}'}, status=status.HTTP_400_BAD_REQUEST)
        case_names.add(case_name)
        if not parsed.get('records'):
            return Response({'error': f'{filename} has no mapped records.'}, status=status.HTTP_400_BAD_REQUEST)
        resolved_files.append((parsed, case_name))

    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        previous = HMBCaseImportBatch.objects.filter(
            project=project, imported_by=request.user, metadata__preview_token=token,
        ).first()
        if previous:
            return Response({'success': True, 'batch_id': str(previous.pk), 'total_records': previous.total_records,
                             'imported_file_count': previous.source_file_count, 'already_imported': True})
        existing_cases = {
            canonical_hmb_case_name(name) for name in HMBCaseRecord.objects.filter(
                project=project, template_profile=template_profile,
            ).values_list('case_name', flat=True).distinct()
        }
        replacements = sorted(case_names & existing_cases)
        if replacements and request.data.get('replace_existing') is not True:
            return Response({'error': 'Existing cases require explicit replacement approval.', 'existing_cases': replacements}, status=409)
        batch = HMBCaseImportBatch.objects.create(
            project=project,
            template_profile=template_profile,
            imported_by=request.user,
            source_file_count=len(resolved_files),
            status='completed',
        )
        total_records = 0
        file_summaries = []
        for parsed, case_name in resolved_files:
            existing_case_names = HMBCaseRecord.objects.filter(
                project=project,
                template_profile=template_profile,
            ).values_list('case_name', flat=True).distinct()
            replace_names = [name for name in existing_case_names if canonical_hmb_case_name(name) == case_name]
            HMBCaseRecord.objects.filter(
                project=project,
                template_profile=template_profile,
                case_name__in=replace_names,
            ).delete()
            rows = [
                HMBCaseRecord(
                    batch=batch,
                    project=project,
                    template_profile=template_profile,
                    case_name=case_name,
                    source_filename=record.get('source_filename', parsed.get('source_filename', '')),
                    stream_id=record.get('stream_id', ''),
                    source_stream_id=record.get('source_stream_id', record.get('stream_id', '')),
                    stream_description=record.get('stream_description', ''),
                    section_key=record.get('section_key', ''),
                    section_label=record.get('section_label', ''),
                    property_name=record.get('property_name', ''),
                    unit=record.get('unit', ''),
                    value_text=record.get('value_text', ''),
                    source_metadata=record.get('source_metadata', {}),
                    row_index=record.get('row_index', 0),
                )
                for record in parsed['records']
            ]
            HMBCaseRecord.objects.bulk_create(rows, batch_size=2000)
            total_records += len(rows)
            file_summaries.append({
                'filename': parsed.get('source_filename', ''),
                'case_name': case_name,
                'detected_format': parsed.get('detected_format', ''),
                'record_count': len(rows),
                'exceptions': parsed.get('exceptions', {}),
                'source_upload_id': parsed.get('source_upload_id'),
                'file_id': parsed.get('file_id'),
            })
        batch.total_records = total_records
        batch.metadata = {'files': file_summaries, 'preview_token': token}
        batch.save(update_fields=['total_records', 'metadata'])
        source_upload_ids = [
            parsed.get('source_upload_id') for parsed, _case_name in resolved_files
            if parsed.get('source_upload_id')
        ]
        if source_upload_ids:
            HMBSourceUpload.objects.filter(
                id__in=source_upload_ids,
                project=project,
                uploaded_by=request.user,
                status=HMBSourceUpload.STATUS_ANALYZED,
            ).update(
                import_batch=batch,
                status=HMBSourceUpload.STATUS_IMPORTED,
                imported_at=timezone.now(),
            )
    return Response({
        'success': True,
        'batch_id': str(batch.id),
        'project_id': str(project.project_id),
        'template_profile_id': str(template_profile.id),
        'template_profile_name': template_profile.template_name or template_profile.source_filename,
        'source_file_count': len(resolved_files),
        'imported_file_count': len(file_summaries),
        'total_records': total_records,
        'files': file_summaries,
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

        if template_profile.project_id and template_profile.project_id != project.project_id:
            return Response({'error': 'template_profile_id does not belong to the selected project.'}, status=status.HTTP_400_BAD_REQUEST)
        if not template_profile.project_id and not _is_admin(request.user) and template_profile.created_by_id != request.user.id:
            return Response({'error': 'Access denied for template profile.'}, status=status.HTTP_403_FORBIDDEN)

    auto_template_applied = False
    auto_template_scope = ''
    if template_profile is None:
        profile_qs = HMBMasterTemplateProfile.objects.filter(
            is_active=True,
            project=project,
        )
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
                created_by=request.user,
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
                    source_metadata=rec.get('source_metadata', {}),
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

            storage_result = store_hmb_source(
                temp_path,
                upload_kind=HMBSourceUpload.KIND_CASE_FILE,
                original_filename=file_obj.name,
                project_id=project.project_id,
                user_id=request.user.id,
            )
            storage_key = storage_result.get('key', '')

            try:
                with transaction.atomic():
                    HMBCaseRecord.objects.filter(
                        project=project,
                        template_profile=template_profile,
                        case_name=case_name,
                    ).delete()
                    HMBCaseRecord.objects.bulk_create(replacement_rows, batch_size=2000)
                    if storage_result['stored']:
                        HMBSourceUpload.objects.create(
                            project=project,
                            template_profile=template_profile,
                            import_batch=batch,
                            uploaded_by=request.user,
                            upload_kind=HMBSourceUpload.KIND_CASE_FILE,
                            original_filename=file_obj.name,
                            storage_key=storage_key,
                            file_sha256=storage_result['sha256'],
                            size_bytes=storage_result['size'],
                            content_type=storage_result['content_type'],
                            status=HMBSourceUpload.STATUS_IMPORTED,
                            imported_at=timezone.now(),
                        )
            except Exception:
                delete_hmb_source(storage_key)
                raise
            total_records += len(replacement_rows)
        except HMBStorageError as exc:
            failures.append({'filename': file_obj.name, 'error': str(exc), 'code': 'storage_failed'})
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

    if not total_records and any(item.get('code') == 'storage_failed' for item in failures):
        return Response({
            'error': 'HMB files could not be retained in private storage.',
            'batch_id': str(batch.id),
            'failures': failures,
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

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
    case_names = sorted({canonical_hmb_case_name(name) for name in qs.values_list('case_name', flat=True)})
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
        matching_names = [
            name for name in qs.values_list('case_name', flat=True).distinct()
            if canonical_hmb_case_name(name) == canonical_hmb_case_name(case_name)
        ]
        qs = qs.filter(case_name__in=matching_names)

    latest_case_raw = qs.order_by('-id').values_list('case_name', flat=True).first() or ''
    if latest_case_raw and not case_name and not all_cases:
        qs = qs.filter(case_name=latest_case_raw)
    latest_case = canonical_hmb_case_name(latest_case_raw) if latest_case_raw else ''

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
    for record in records:
        record['case_name'] = canonical_hmb_case_name(record['case_name'])

    available_cases = sorted({
        canonical_hmb_case_name(name)
        for name in HMBCaseRecord.objects.filter(project=project).values_list('case_name', flat=True)
    })
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
