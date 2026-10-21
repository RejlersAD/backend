"""
Spec Customization — Component Matching Views
==============================================

API endpoints for uploading and managing Match/SPEC/CAT workbooks.

Endpoints (mounted at /api/v1/spec-customization/):
  POST   matching/upload/               → upload Match.xlsx + SPEC.xlsx + CAT.xlsx
  GET    matching/sets/                 → list workbook sets for a project
  GET    matching/sets/<id>/            → workbook set detail
  DELETE matching/sets/<id>/            → delete workbook set
  POST   matching/sets/<id>/activate/   → activate workbook set
  POST   matching/sets/<id>/parse/      → parse Match.xlsx (create rules)
  GET    matching/sets/<id>/rules/      → list matching rules
  POST   matching/match/                → match a component
  GET    matching/results/              → list matching results
"""
from __future__ import annotations

import logging
from typing import Any, Dict
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .matching_models import (
    MatchingWorkbookSet,
    MatchingRule,
    ComponentMatchingResult,
    ComponentMatchDecision,
)
from .project_models import SpecProject
from .services.component_matcher import parse_match_xlsx, match_component, run_job_matching
from .services.reference_intelligence import validate_reference_files, file_sha256, reference_hash

logger = logging.getLogger(__name__)


def _is_spec_admin(user) -> bool:
    return bool(
        getattr(user, 'is_authenticated', False)
        and (
            getattr(user, 'is_superuser', False)
            or getattr(user, 'is_staff', False)
            or (getattr(user, 'role', '') or '').lower() in {'admin', 'super_admin', 'tenant_admin'}
        )
    )


def _get_accessible_project(request, project_id) -> SpecProject:
    project = get_object_or_404(SpecProject, project_id=project_id)
    if not _is_spec_admin(request.user) and project.created_by_id != getattr(request.user, 'id', None):
        raise PermissionDenied('You do not have access to this project.')
    return project


def _get_accessible_workbook_set(request, set_id) -> MatchingWorkbookSet:
    workbook_set = get_object_or_404(MatchingWorkbookSet.objects.select_related('project'), id=set_id)
    if not _is_spec_admin(request.user) and workbook_set.project.created_by_id != getattr(request.user, 'id', None):
        raise PermissionDenied('You do not have access to this workbook set.')
    return workbook_set


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Validate Excel file
# ─────────────────────────────────────────────────────────────────────────────
def _validate_excel_file(uploaded_file, field_name: str) -> tuple[bool, str]:
    """
    Validate uploaded Excel file.
    
    Returns:
        (is_valid, error_message)
    """
    if not uploaded_file:
        return False, f"{field_name} is required"
    
    # Check extension
    filename = uploaded_file.name.lower()
    if not (filename.endswith('.xlsx') or filename.endswith('.xls')):
        return False, f"{field_name} must be an Excel file (.xlsx or .xls)"
    
    # Check size (max 50MB)
    MAX_SIZE = 50 * 1024 * 1024  # 50MB
    if uploaded_file.size > MAX_SIZE:
        return False, f"{field_name} exceeds maximum size of 50MB"
    
    return True, ''


# ─────────────────────────────────────────────────────────────────────────────
# Upload Match/SPEC/CAT workbooks
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@permission_classes([IsAuthenticated])
@transaction.atomic
def upload_matching_workbooks(request):
    """
    Upload Match.xlsx, SPEC.xlsx, and CAT.xlsx for a project.
    
    Required fields:
        - project_id: UUID of SpecProject
        - match_file: Match.xlsx file
        - spec_file: SPEC.xlsx file
        - cat_file: CAT.xlsx file
    
    Optional fields:
        - version_label: Version label (default: auto-generated)
        - auto_parse: Whether to auto-parse Match.xlsx (default: true)
    """
    # Get project
    project_id = request.data.get('project_id')
    if not project_id:
        return Response(
            {'error': 'project_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    project = _get_accessible_project(request, project_id)
    SpecProject.objects.select_for_update().get(pk=project.pk)
    
    # Get files
    match_file = request.FILES.get('match_file')
    spec_file = request.FILES.get('spec_file')
    cat_file = request.FILES.get('cat_file')
    
    # Validate files
    is_valid, error = _validate_excel_file(match_file, 'match_file')
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
    
    is_valid, error = _validate_excel_file(spec_file, 'spec_file')
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
    
    is_valid, error = _validate_excel_file(cat_file, 'cat_file')
    if not is_valid:
        return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
    
    # Validate before changing activation or writing files.
    errors = validate_reference_files(match_file, spec_file, cat_file)
    if errors:
        return Response({'error': 'Reference schema validation failed.', 'schema_errors': errors}, status=422)
    metadata = ReferenceMetadataSerializer(data=request.data)
    metadata.is_valid(raise_exception=True)
    hashes = {f'{kind}_file_hash': file_sha256(source) for kind, source in
              [('match', match_file), ('spec', spec_file), ('cat', cat_file)]}

    # Deactivate existing active sets
    MatchingWorkbookSet.objects.filter(
        project=project,
        is_active=True
    ).update(is_active=False)
    
    # Create workbook set
    version_label = request.data.get('version_label', f'Upload {MatchingWorkbookSet.objects.filter(project=project).count() + 1}')
    
    workbook_set = MatchingWorkbookSet.objects.create(
        project=project,
        schema_valid=True, schema_errors=[],
        **hashes, **metadata.validated_data,
        version_label=version_label,
        match_file=match_file,
        spec_file=spec_file,
        cat_file=cat_file,
        match_file_name=match_file.name,
        spec_file_name=spec_file.name,
        cat_file_name=cat_file.name,
        is_active=True,
        uploaded_by=request.user,
    )
    
    # Auto-parse Match.xlsx if requested
    auto_parse = request.data.get('auto_parse', 'true').lower() in ('true', '1', 'yes')
    if auto_parse:
        try:
            rules_count, error = parse_match_xlsx(
                workbook_set.match_file,
                workbook_set
            )
            
            if error:
                workbook_set.parse_error = error
                workbook_set.is_parsed = False
            else:
                workbook_set.is_parsed = True
                workbook_set.rules_count = rules_count
            
            workbook_set.save(update_fields=['is_parsed', 'parse_error', 'rules_count'])
            
        except Exception as e:
            logger.exception(f"[MatchingUpload] Auto-parse failed: {e}")
            workbook_set.parse_error = str(e)
            workbook_set.save(update_fields=['parse_error'])
    
    # Count sheets in SPEC and CAT
    try:
        import pandas as pd
        with workbook_set.spec_file.storage.open(workbook_set.spec_file.name, 'rb') as stream:
            with pd.ExcelFile(stream) as workbook:
                workbook_set.spec_sheets_count = len(workbook.sheet_names)
        with workbook_set.cat_file.storage.open(workbook_set.cat_file.name, 'rb') as stream:
            with pd.ExcelFile(stream) as workbook:
                workbook_set.cat_sheets_count = len(workbook.sheet_names)
        workbook_set.save(update_fields=['spec_sheets_count', 'cat_sheets_count'])
    except Exception as e:
        logger.warning(f"[MatchingUpload] Failed to count sheets: {e}")
    
    return Response({
        'success': True,
        'workbook_set': {
            **_reference_metadata(workbook_set),
            'id': str(workbook_set.id),
            'project_id': str(project.project_id),
            'project_name': project.name,
            'version_label': workbook_set.version_label,
            'is_active': workbook_set.is_active,
            'is_parsed': workbook_set.is_parsed,
            'rules_count': workbook_set.rules_count,
            'spec_sheets_count': workbook_set.spec_sheets_count,
            'cat_sheets_count': workbook_set.cat_sheets_count,
            'parse_error': workbook_set.parse_error,
            'created_at': workbook_set.created_at.isoformat(),
        }
    }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# List workbook sets for a project
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_matching_workbook_sets(request):
    """
    List all matching workbook sets for a project.
    
    Query params:
        - project_id: Filter by project (required)
        - is_active: Filter by active status (optional)
    """
    project_id = request.query_params.get('project_id')
    if not project_id:
        return Response(
            {'error': 'project_id query parameter is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    project = _get_accessible_project(request, project_id)
    queryset = MatchingWorkbookSet.objects.filter(project=project)
    
    # Filter by active status
    is_active = request.query_params.get('is_active')
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active.lower() in ('true', '1', 'yes'))
    
    sets = []
    for ws in queryset:
        sets.append({
            **_reference_metadata(ws),
            'id': str(ws.id),
            'version_label': ws.version_label,
            'is_active': ws.is_active,
            'is_parsed': ws.is_parsed,
            'rules_count': ws.rules_count,
            'spec_sheets_count': ws.spec_sheets_count,
            'cat_sheets_count': ws.cat_sheets_count,
            'match_file_name': ws.match_file_name,
            'spec_file_name': ws.spec_file_name,
            'cat_file_name': ws.cat_file_name,
            'created_at': ws.created_at.isoformat(),
        })
    
    return Response({'items': sets, 'count': len(sets)})


# ─────────────────────────────────────────────────────────────────────────────
# Get workbook set detail
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def matching_workbook_set_detail(request, set_id):
    """Get or delete a workbook set."""
    ws = _get_accessible_workbook_set(request, set_id)
    
    if request.method == 'DELETE':
        ws = MatchingWorkbookSet.objects.select_for_update().get(pk=ws.pk)
        if ws.frozen_at or ws.pinned_jobs.exists():
            return Response({'error': 'Frozen or pinned reference sets cannot be deleted.'}, status=409)
        ws.delete()
        return Response({'success': True}, status=status.HTTP_204_NO_CONTENT)
    
    return Response({
        **_reference_metadata(ws),
        'id': str(ws.id),
        'project_id': str(ws.project.project_id),
        'project_name': ws.project.name,
        'version_label': ws.version_label,
        'is_active': ws.is_active,
        'is_parsed': ws.is_parsed,
        'rules_count': ws.rules_count,
        'spec_sheets_count': ws.spec_sheets_count,
        'cat_sheets_count': ws.cat_sheets_count,
        'match_file_name': ws.match_file_name,
        'spec_file_name': ws.spec_file_name,
        'cat_file_name': ws.cat_file_name,
        'parse_error': ws.parse_error,
        'created_at': ws.created_at.isoformat(),
        'updated_at': ws.updated_at.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Activate workbook set
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def activate_matching_workbook_set(request, set_id):
    """Activate a workbook set (deactivate others in same project)."""
    ws = _get_accessible_workbook_set(request, set_id)
    SpecProject.objects.select_for_update().get(pk=ws.project_id)
    
    # Deactivate all sets in same project
    MatchingWorkbookSet.objects.filter(
        project=ws.project
    ).update(is_active=False)
    
    # Activate this set
    ws.is_active = True
    ws.save(update_fields=['is_active'])
    
    return Response({'success': True, 'is_active': True})


# ─────────────────────────────────────────────────────────────────────────────
# Parse Match.xlsx
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def parse_matching_workbook(request, set_id):
    """Parse Match.xlsx and create/update matching rules."""
    ws = _get_accessible_workbook_set(request, set_id)
    ws = MatchingWorkbookSet.objects.select_for_update().get(pk=ws.pk)
    if ws.frozen_at or ws.pinned_jobs.exists():
        return Response({'error': 'Frozen or pinned reference sets cannot be reparsed.'}, status=409)
    
    if not ws.match_file:
        return Response(
            {'error': 'Match file not uploaded'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        rules_count, error = parse_match_xlsx(ws.match_file, ws)
        
        if error:
            ws.parse_error = error
            ws.is_parsed = False
        else:
            ws.is_parsed = True
            ws.rules_count = rules_count
            ws.parse_error = ''
        
        ws.save(update_fields=['is_parsed', 'parse_error', 'rules_count'])
        
        return Response({
            'success': not bool(error),
            'rules_count': rules_count,
            'error': error,
        })
        
    except Exception as e:
        logger.exception(f"[MatchingParse] Parse failed: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ─────────────────────────────────────────────────────────────────────────────
# List matching rules
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_matching_rules(request, set_id):
    """List all matching rules for a workbook set."""
    ws = _get_accessible_workbook_set(request, set_id)
    
    rules = MatchingRule.objects.filter(workbook_set=ws).order_by('pdf_component_name')
    
    items = []
    for rule in rules:
        items.append({
            'id': str(rule.id),
            'pdf_component_name': rule.pdf_component_name,
            'catalog_component_name': rule.catalog_component_name,
            'cat_sheet_name': rule.cat_sheet_name,
            'row_number': rule.row_number,
        })
    
    return Response({'items': items, 'count': len(items)})


# ─────────────────────────────────────────────────────────────────────────────
# Match component
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def match_component_endpoint(request):
    """
    Match a component to CAT commodity code.
    
    Request body:
        {
            "workbook_set_id": "uuid",  (optional — uses active set for project)
            "project_id": "uuid",  (required if workbook_set_id not provided)
            "pdf_component_name": "GATE VALVE",
            "nominal_size": "2",
            "pressure_rating": "150#",
            "material_grade": "A105"
        }
    """
    # Get workbook set
    workbook_set_id = request.data.get('workbook_set_id')
    project_id = request.data.get('project_id')
    
    if workbook_set_id:
        ws = _get_accessible_workbook_set(request, workbook_set_id)
    elif project_id:
        project = _get_accessible_project(request, project_id)
        ws = MatchingWorkbookSet.objects.filter(
            project=project,
            is_active=True
        ).first()
        if not ws:
            return Response(
                {'error': 'No active workbook set found for this project'},
                status=status.HTTP_404_NOT_FOUND
            )
    else:
        return Response(
            {'error': 'Either workbook_set_id or project_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    if not ws.is_parsed:
        return Response({'error': 'Parse the reference mapping workbook successfully before matching.'}, status=status.HTTP_409_CONFLICT)

    # Get matching criteria
    pdf_component_name = request.data.get('pdf_component_name', '')
    nominal_size = request.data.get('nominal_size', '')
    pressure_rating = request.data.get('pressure_rating', '')
    material_grade = request.data.get('material_grade', '')
    
    if not pdf_component_name:
        return Response(
            {'error': 'pdf_component_name is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Perform matching
    result = match_component(
        pdf_component_name=pdf_component_name,
        nominal_size=nominal_size,
        pressure_rating=pressure_rating,
        material_grade=material_grade,
        workbook_set=ws,
        sub_type=request.data.get('sub_type', ''),
        end_connection=request.data.get('end_connection', ''),
    )
    
    return Response(result)


# ─────────────────────────────────────────────────────────────────────────────
# List matching results
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_matching_results(request):
    """
    List matching results.
    
    Query params:
        - workbook_set_id: Filter by workbook set
        - project_id: Filter by project
        - limit: Max results (default: 100)
    """
    queryset = ComponentMatchingResult.objects.all()
    if not _is_spec_admin(request.user):
        queryset = queryset.filter(workbook_set__project__created_by=request.user)
    
    # Filter by workbook set
    workbook_set_id = request.query_params.get('workbook_set_id')
    if workbook_set_id:
        _get_accessible_workbook_set(request, workbook_set_id)
        queryset = queryset.filter(workbook_set__id=workbook_set_id)
    
    # Filter by project
    project_id = request.query_params.get('project_id')
    if project_id:
        _get_accessible_project(request, project_id)
        queryset = queryset.filter(workbook_set__project__project_id=project_id)
    
    # Limit
    limit = int(request.query_params.get('limit', 100))
    queryset = queryset.order_by('-created_at')[:limit]
    
    items = []
    for result in queryset:
        items.append({
            'id': str(result.id),
            'pdf_component_name': result.pdf_component_name,
            'nominal_size': result.nominal_size,
            'pressure_rating': result.pressure_rating,
            'material_grade': result.material_grade,
            'matched_commodity_code': result.matched_commodity_code,
            'matched_description': result.matched_description,
            'match_score': result.match_score,
            'match_method': result.match_method,
            'created_at': result.created_at.isoformat(),
        })
    
    return Response({'items': items, 'count': len(items)})


class ReferenceMetadataSerializer(serializers.Serializer):
    standard_edition = serializers.CharField(max_length=64, required=False, allow_blank=True)
    units = serializers.CharField(max_length=16, required=False, allow_blank=True)
    effective_date = serializers.DateField(required=False, allow_null=True)
    # Business approval authority remains undecided. Upload cannot self-approve.
    review_status = serializers.ChoiceField(choices=['draft', 'review'], required=False)


def _reference_metadata(ws):
    return {**{f'{kind}_file_hash': getattr(ws, f'{kind}_file_hash') for kind in ('match', 'spec', 'cat')},
            'reference_hash': reference_hash(ws), 'schema_valid': ws.schema_valid, 'schema_errors': ws.schema_errors,
            'standard_edition': ws.standard_edition, 'units': ws.units,
            'effective_date': ws.effective_date.isoformat() if ws.effective_date else None,
            'review_status': ws.review_status, 'frozen_at': ws.frozen_at.isoformat() if ws.frozen_at else None,
            'frozen_by': str(ws.frozen_by_id) if ws.frozen_by_id else None}


class MatchingRunSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    workbook_set_id = serializers.UUIDField(required=False)
    codelist_profile = serializers.ChoiceField(choices=['BUHASA'], required=False)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def matching_run(request):
    from .views import _get_accessible_job
    payload = MatchingRunSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    job = _get_accessible_job(request, payload.validated_data['job_id'])
    set_id = payload.validated_data.get('workbook_set_id') or job.reference_workbook_set_id
    if set_id:
        ws = _get_accessible_workbook_set(request, set_id)
    else:
        project = _get_accessible_project(request, job.document.project_id)
        SpecProject.objects.select_for_update().get(pk=project.pk)
        ws = MatchingWorkbookSet.objects.filter(project=project, is_active=True).first()
        if not ws:
            return Response({'error': 'No active reference set for this project.'}, status=409)
    try:
        result = run_job_matching(job, ws, codelist_profile=payload.validated_data.get('codelist_profile'))
        from .services.controlled_release import matching_changed
        matching_changed(job, request.user, 'matching_run')
        return Response(result)
    except ValueError as exc:
        return Response({'error': str(exc)}, status=409)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def matching_set_freeze(request):
    field = serializers.UUIDField()
    set_id = field.run_validation(request.data.get('workbook_set_id'))
    ws = _get_accessible_workbook_set(request, set_id)
    SpecProject.objects.select_for_update().get(pk=ws.project_id)
    ws = MatchingWorkbookSet.objects.select_for_update().get(pk=ws.pk)
    if not ws.frozen_at:
        errors = validate_reference_files(ws.match_file, ws.spec_file, ws.cat_file)
        if not ws.schema_valid or not ws.is_parsed or errors:
            return Response({'error': 'A validated and parsed reference set is required.', 'schema_errors': errors}, status=409)
        for kind in ('match', 'spec', 'cat'):
            if file_sha256(getattr(ws, f'{kind}_file')) != getattr(ws, f'{kind}_file_hash'):
                return Response({'error': 'Reference integrity check failed.'}, status=409)
        ws.frozen_at, ws.frozen_by = timezone.now(), request.user
        ws.save(update_fields=['frozen_at', 'frozen_by'])
    return Response({'id': str(ws.pk), **_reference_metadata(ws)})


def _decision_payload(decision):
    fields = ['pdf_component_name', 'nominal_size', 'pressure_rating', 'material_grade',
              'sub_type', 'end_connection', 'state', 'matched_commodity_code', 'matched_description',
              'match_score', 'candidates', 'discriminators_matched', 'review_decision', 'final_commodity_code', 'review_notes']
    return {**{f: getattr(decision, f) for f in fields}, 'id': str(decision.pk),
            'job_id': str(decision.job_id), 'workbook_set_id': str(decision.workbook_set_id),
            'reviewed_by': str(decision.reviewed_by_id) if decision.reviewed_by_id else None,
            'reviewed_at': decision.reviewed_at.isoformat() if decision.reviewed_at else None,
            'score_description': 'Ranking aid only; not calibrated probability.'}


class DecisionFilters(serializers.Serializer):
    job = serializers.UUIDField(required=False)
    workbook_set = serializers.UUIDField(required=False)
    state = serializers.ChoiceField(choices=[v for v, _ in ComponentMatchDecision.STATE_CHOICES], required=False)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def matching_decisions(request):
    from .views import _get_accessible_job
    filters = DecisionFilters(data=request.query_params)
    filters.is_valid(raise_exception=True)
    qs = ComponentMatchDecision.objects.select_related('job__document', 'workbook_set__project')
    values = filters.validated_data
    if 'job' in values:
        _get_accessible_job(request, values['job'])
        qs = qs.filter(job_id=values['job'])
    if 'workbook_set' in values:
        _get_accessible_workbook_set(request, values['workbook_set'])
        qs = qs.filter(workbook_set_id=values['workbook_set'])
    if 'state' in values:
        qs = qs.filter(state=values['state'])
    # Both reference and source must be accessible, even for unfiltered reads.
    if not _is_spec_admin(request.user):
        from django.db.models import Q
        qs = qs.filter(workbook_set__project__created_by=request.user).filter(
            Q(job__created_by=request.user) | Q(job__document__uploaded_by=request.user) |
            Q(job__document__project_id__in=[str(pk) for pk in SpecProject.objects.filter(created_by=request.user).values_list('project_id', flat=True)]))
    items = [_decision_payload(d) for d in qs]
    return Response({'items': items, 'count': len(items)})


class SourceReferenceSerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1)
    table_id = serializers.CharField(max_length=128, required=False, allow_blank=False)
    bbox = serializers.ListField(child=serializers.FloatField(), min_length=4, max_length=4, required=False)
    coordinate_space = serializers.ChoiceField(choices=['pdf_points', 'normalized'], required=False)

    def validate(self, values):
        import math
        box = values.get('bbox')
        if box is not None:
            if not all(math.isfinite(value) and value >= 0 for value in box) or box[0] >= box[2] or box[1] >= box[3]:
                raise serializers.ValidationError('Bounding box must contain finite ordered x0,y0,x1,y1 coordinates.')
            if 'coordinate_space' not in values:
                raise serializers.ValidationError('Bounding box coordinate_space is required.')
            if values['coordinate_space'] == 'normalized' and max(box) > 1:
                raise serializers.ValidationError('Normalized bounding box coordinates must be between zero and one.')
        return values


class DecisionReviewSerializer(serializers.Serializer):
    decision_id = serializers.UUIDField()
    review_decision = serializers.ChoiceField(choices=['approved', 'rejected', 'remapped', 'normalized'])
    codelist_selections = serializers.DictField(required=False)
    candidate_id = serializers.CharField(max_length=64, required=False)
    final_commodity_code = serializers.CharField(max_length=128, required=False, allow_blank=True)
    review_notes = serializers.CharField(required=False, allow_blank=True)
    source_reference = SourceReferenceSerializer(required=False)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def matching_decision_review(request):
    from .views import _get_accessible_job
    payload = DecisionReviewSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    values = payload.validated_data
    decision = get_object_or_404(ComponentMatchDecision, pk=values['decision_id'])
    job = _get_accessible_job(request, decision.job_id)
    if values['review_decision'] == 'normalized':
        # Match the batch command's project -> job -> reference lock order.
        accessible_set = _get_accessible_workbook_set(request, decision.workbook_set_id)
        SpecProject.objects.select_for_update().get(pk=accessible_set.project_id)
    from .models import PaperSpecExtractionJob
    PaperSpecExtractionJob.objects.select_for_update().get(pk=job.pk)
    decision = ComponentMatchDecision.objects.select_for_update().get(pk=decision.pk)
    ws = _get_accessible_workbook_set(request, decision.workbook_set_id)
    if decision.job.reference_workbook_set_id != ws.pk:
        return Response({'error': 'Decision does not belong to the pinned reference.'}, status=409)
    action = values['review_decision']
    code = values.get('final_commodity_code', '')
    evidence = decision.discriminators_matched.get('codelists', {})
    if 'source_reference' in values:
        if action != 'approved':
            return Response({'error': 'Source auto-linking requires match approval.'}, status=400)
        if job.document.total_pages and values['source_reference']['page'] > job.document.total_pages:
            return Response({'error': 'Source page is outside this job document.'}, status=400)
    if action == 'normalized':
        if not values.get('codelist_selections'):
            return Response({'error': 'Canonical selections are required.'}, status=400)
        try:
            run_job_matching(job, ws, codelist_profile='BUHASA',
                             selection_updates={str(decision.pk): values['codelist_selections']})
        except ValueError as exc:
            return Response({'error': str(exc)}, status=409)
        decision.refresh_from_db()
        from .services.controlled_release import matching_changed
        matching_changed(job, request.user, 'codelist_review')
        return Response(_decision_payload(decision))
    if action in ('approved', 'remapped') and evidence.get('profile') == 'BUHASA':
        from .services.buhasa_codelists import blockers
        problems = blockers(evidence)
        if problems:
            return Response({'error': 'Resolve codelist selections first.', 'blockers': problems}, status=409)
        selected = next((candidate for candidate in decision.candidates
                         if candidate.get('candidate_id') == values.get('candidate_id')), None)
        if not selected or selected['commodity_code'] != (code or decision.matched_commodity_code):
            return Response({'error': 'Select an eligible catalog row explicitly.'}, status=400)
        evidence['selected_candidate_id'] = selected['candidate_id']
    if action == 'approved':
        if decision.state not in ('unique_match', 'ambiguous'):
            return Response({'error': 'This state requires rejection or explicit remapping.'}, status=409)
        code = code or decision.matched_commodity_code
        if not code or code not in {c['commodity_code'] for c in decision.candidates}:
            return Response({'error': 'Select a candidate commodity code explicitly.'}, status=400)
    elif action == 'remapped':
        if not code:
            return Response({'error': 'Remap requires final_commodity_code.'}, status=400)
        # A remap must still identify an actual code in the pinned catalog.
        from .services.reference_intelligence import reference_stream, read_reference_sheet
        import pandas as pd
        found = False
        with reference_stream(ws.cat_file) as stream:
            with pd.ExcelFile(stream) as book:
                names = book.sheet_names
        for name in names:
            rows = read_reference_sheet(ws.cat_file, name)
            if 'IndustryCommodityCode' in rows.columns and code in set(rows.IndustryCommodityCode.str.strip()):
                found = True
                break
        if not found:
            return Response({'error': 'Commodity code is absent from the pinned catalog.'}, status=400)
    else:
        code = ''
    decision.review_decision = action
    decision.final_commodity_code = code
    decision.review_notes = values.get('review_notes', '')
    decision.reviewed_by, decision.reviewed_at = request.user, timezone.now()
    if 'source_reference' in values:
        decision.discriminators_matched = {**decision.discriminators_matched,
            'source_reference': {**values['source_reference'], 'document_id': str(job.document_id),
                                 'document_sha256': job.document.sha256_hash,
                                 'linked_by': str(request.user.pk), 'linked_at': decision.reviewed_at.isoformat()}}
    decision.save(update_fields=['review_decision', 'final_commodity_code', 'review_notes', 'reviewed_by', 'reviewed_at', 'discriminators_matched'])
    from .services.controlled_release import matching_changed
    matching_changed(job, request.user, 'match_review')
    return Response(_decision_payload(decision))
