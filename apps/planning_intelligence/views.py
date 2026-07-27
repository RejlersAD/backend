"""
RADAI Project Planning Application — DRF ViewSets.

URL roots (wired in apps.planning_intelligence.urls):
    /api/v1/planning-intelligence/projects/
    /api/v1/planning-intelligence/projects/<id>/analyze/      (POST)
    /api/v1/planning-intelligence/projects/<id>/generate/     (POST)
    /api/v1/planning-intelligence/projects/<id>/ai-settings/  (GET/POST/DELETE)
    /api/v1/planning-intelligence/projects/<id>/ai-settings/test/ (POST)
    /api/v1/planning-intelligence/files/
    /api/v1/planning-intelligence/generations/
    /api/v1/planning-intelligence/generations/<id>/export/    (GET ?export_format=csv|json|primavera_csv|excel|pptx)
"""
from __future__ import annotations

import logging

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .config import (
    CLAUDE_API_KEY_PATTERN, CLAUDE_MODEL_CHOICES, DEFAULT_CLAUDE_MODEL, MAX_FILE_BYTES,
)
from .models import PlanningFile, PlanningGeneration, PlanningProject
from .serializers import (
    PlanningFileListSerializer, PlanningFileSerializer, PlanningGenerationListSerializer,
    PlanningGenerationSerializer, PlanningProjectSerializer,
)
from .services import byok_crypto, claude_client, export_utils
from .services.activity_generator import build_activities
from .services.eddr_generator import build_eddr
from .services.intelligence import analyze_project
from .services.manhour_estimator import build_manhours
from .services.narrative_generator import build_narrative
from .services.validation_engine import validate
from .services.wbs_generator import build_wbs
from .tasks import parse_uploaded_planning_file

logger = logging.getLogger(__name__)


class PlanningProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PlanningProjectSerializer
    queryset = PlanningProject.objects.all().filter(is_deleted=False).prefetch_related('files', 'generations')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user if self.request.user.is_authenticated else None)

    def perform_destroy(self, instance):
        """Soft-delete only — never hard-delete a project (RADAI global rule:
        archive/supersede, never destroy). Mirrors the is_deleted filtering
        already used everywhere else in this app's querysets."""
        instance.soft_delete()

    @action(detail=True, methods=['post'], url_path='analyze')
    def analyze(self, request, pk=None):
        """Document Intelligence preview — does not persist a generation."""
        project = self.get_object()
        files_qs = project.files.filter(is_deleted=False, parse_status='done')
        if not files_qs.exists():
            return Response(
                {'error': 'No successfully parsed files yet. Upload files and wait for parsing to complete.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        intelligence = analyze_project(list(files_qs), project=project, user=request.user)
        return Response({'intelligence': intelligence})

    @action(detail=True, methods=['post'], url_path='generate')
    def generate(self, request, pk=None):
        """Runs the full planning pipeline: intelligence -> WBS -> activities
        -> EDDR -> manhours -> validation -> narrative, then persists a new
        PlanningGeneration version."""
        project = self.get_object()
        files_qs = project.files.filter(is_deleted=False, parse_status='done')

        intelligence = analyze_project(list(files_qs), project=project, user=request.user)
        wbs = build_wbs(project, intelligence)
        schedule = build_activities(project, wbs, intelligence)
        activities = schedule['activities']
        logic_matrix = schedule['logic_matrix']
        eddr = build_eddr(activities)
        manhours = build_manhours(project, activities)
        validation_issues = validate(project, wbs, activities, eddr, intelligence)
        narrative = build_narrative(project, activities, eddr, validation_issues, user=request.user)
        milestones = [a for a in activities if a.get('is_milestone')]

        next_version = (project.generations.first().version + 1) if project.generations.exists() else 1
        generation = PlanningGeneration.objects.create(
            project=project,
            version=next_version,
            intelligence=intelligence,
            wbs=wbs,
            activities=activities,
            logic_matrix=logic_matrix,
            eddr=eddr,
            milestones=milestones,
            manhours=manhours,
            validation=validation_issues,
            narrative=narrative,
            generated_by=request.user if request.user.is_authenticated else None,
        )
        return Response(PlanningGenerationSerializer(generation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='ai-settings')
    def ai_settings(self, request, pk=None):
        """
        GET    -> {'enabled','provider','model','key_configured','model_choices'}
        POST   -> body {'enabled': bool, 'model': str, 'api_key': str (optional)}
                  Saves settings; only overwrites the stored key when 'api_key'
                  is supplied (so enabling/switching models doesn't require
                  re-entering the key every time).
        DELETE -> clears all BYOK settings for this project.
        """
        project = self.get_object()
        ai_settings = project.ai_settings or {}

        if request.method == 'GET':
            return Response({
                'enabled': bool(ai_settings.get('enabled')),
                'provider': ai_settings.get('provider') or 'anthropic',
                'model': ai_settings.get('model') or DEFAULT_CLAUDE_MODEL,
                'key_configured': bool(ai_settings.get('api_key_encrypted')),
                'model_choices': CLAUDE_MODEL_CHOICES,
            })

        if request.method == 'DELETE':
            project.ai_settings = {}
            project.save(update_fields=['ai_settings'])
            return Response({
                'enabled': False, 'provider': 'anthropic', 'model': DEFAULT_CLAUDE_MODEL,
                'key_configured': False, 'model_choices': CLAUDE_MODEL_CHOICES,
            })

        # POST
        enabled = bool(request.data.get('enabled', ai_settings.get('enabled', False)))
        model = request.data.get('model') or ai_settings.get('model') or DEFAULT_CLAUDE_MODEL
        valid_models = {choice['value'] for choice in CLAUDE_MODEL_CHOICES}
        if model not in valid_models:
            return Response({'error': f'Unknown model "{model}".'}, status=status.HTTP_400_BAD_REQUEST)

        api_key = (request.data.get('api_key') or '').strip()
        new_settings = dict(ai_settings)
        new_settings['provider'] = 'anthropic'
        new_settings['enabled'] = enabled
        new_settings['model'] = model

        if api_key:
            if not CLAUDE_API_KEY_PATTERN.match(api_key):
                return Response(
                    {'error': 'API key does not look like a valid Anthropic key (expected format: sk-ant-...).'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            new_settings['api_key_encrypted'] = byok_crypto.encrypt_api_key(api_key)
            new_settings['key_updated_at'] = timezone.now().isoformat()
        elif enabled and not new_settings.get('api_key_encrypted'):
            return Response(
                {'error': 'No API key is configured for this project yet — provide one to enable BYOK.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        project.ai_settings = new_settings
        project.save(update_fields=['ai_settings'])
        return Response({
            'enabled': new_settings['enabled'],
            'provider': new_settings['provider'],
            'model': new_settings['model'],
            'key_configured': bool(new_settings.get('api_key_encrypted')),
            'model_choices': CLAUDE_MODEL_CHOICES,
        })

    @action(detail=True, methods=['post'], url_path='ai-settings/test')
    def ai_settings_test(self, request, pk=None):
        """One minimal live Claude call to validate the stored key works.
        Persists nothing."""
        project = self.get_object()
        if claude_client.get_claude_config(project) is None:
            return Response(
                {'success': False, 'message': 'BYOK is not enabled or no API key is configured for this project.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = claude_client.call_claude(
            project,
            system_prompt='Reply with exactly one word: OK',
            user_prompt='Connection test.',
            max_tokens=10,
            feature='connection_test',
            user=request.user,
        )
        if result is None:
            return Response({
                'success': False,
                'message': 'Claude call failed — check that the API key is valid and has available quota.',
            })
        return Response({'success': True, 'message': 'Claude connection verified successfully.'})


class PlanningFileViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    queryset = PlanningFile.objects.all().filter(is_deleted=False).select_related('project', 'uploaded_by')

    def get_serializer_class(self):
        if self.action == 'list':
            return PlanningFileListSerializer
        return PlanningFileSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        project_id = self.request.query_params.get('project')
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs

    def perform_create(self, serializer):
        file_obj = serializer.validated_data.get('file')
        if file_obj and file_obj.size and file_obj.size > MAX_FILE_BYTES:
            raise ValueError(f'File exceeds maximum allowed size ({MAX_FILE_BYTES} bytes).')

        planning_file = serializer.save(
            uploaded_by=self.request.user if self.request.user.is_authenticated else None,
            original_filename=getattr(file_obj, 'name', ''),
            content_type=getattr(file_obj, 'content_type', ''),
            size_bytes=getattr(file_obj, 'size', 0) or 0,
            parse_status='pending',
        )
        try:
            parse_uploaded_planning_file.delay(planning_file.id)
        except Exception as exc:  # noqa: BLE001
            logger.info('parse_uploaded_planning_file.delay failed (%s); running inline', exc)
            try:
                parse_uploaded_planning_file(planning_file.id)
            except Exception as inner:  # noqa: BLE001
                logger.warning('inline parse_uploaded_planning_file failed: %s', inner)


class PlanningGenerationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = PlanningGeneration.objects.all().filter(is_deleted=False).select_related('project')

    def get_serializer_class(self):
        if self.action == 'list':
            return PlanningGenerationListSerializer
        return PlanningGenerationSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        project_id = self.request.query_params.get('project')
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs

    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, pk=None):
        # NOTE: the export format is read from `export_format`, NOT `format`.
        # DRF's own content negotiation intercepts a query param literally
        # named `format` (URL_FORMAT_OVERRIDE, default enabled) to pick a
        # *renderer* before this method ever runs — since no renderer is
        # registered for 'pptx'/'csv'/'excel'/etc., that raises Http404
        # in DRF's initial()/perform_content_negotiation() before dispatch
        # reaches this action at all.
        generation = self.get_object()
        fmt = (request.query_params.get('export_format') or 'json').lower()
        base_name = f'{generation.project.name}_v{generation.version}'.replace(' ', '_')

        if fmt == 'csv':
            content = export_utils.activities_to_csv(generation.activities)
            response = HttpResponse(content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{base_name}_activities.csv"'
            return response

        if fmt == 'eddr_csv':
            content = export_utils.eddr_to_csv(generation.eddr)
            response = HttpResponse(content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{base_name}_eddr.csv"'
            return response

        if fmt == 'primavera_csv':
            content = export_utils.activities_to_primavera_csv(generation.activities)
            response = HttpResponse(content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{base_name}_primavera.csv"'
            return response

        if fmt == 'excel':
            content = export_utils.activities_to_excel_bytes(generation.activities)
            response = HttpResponse(
                content,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            response['Content-Disposition'] = f'attachment; filename="{base_name}_activities.xlsx"'
            return response

        if fmt == 'pptx':
            content = export_utils.generation_to_pptx_bytes(generation)
            response = HttpResponse(
                content,
                content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            )
            response['Content-Disposition'] = f'attachment; filename="{base_name}_presentation.pptx"'
            return response

        # default: json
        content = export_utils.generation_to_json(generation)
        response = HttpResponse(content, content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="{base_name}.json"'
        return response
