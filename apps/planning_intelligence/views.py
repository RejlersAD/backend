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
    /api/v1/planning-intelligence/generations/<id>/edit/      (PATCH — create corrected child revision)
    /api/v1/planning-intelligence/generations/<id>/export/    (GET ?export_format=csv|json|primavera_csv|excel|pptx|xer)
    /api/v1/planning-intelligence/jobs/
    /api/v1/planning-intelligence/audit-events/
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Max
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .access import PlanningObjectPermission, accessible_projects
from .config import CLAUDE_API_KEY_PATTERN, CLAUDE_MODEL_CHOICES, DEFAULT_CLAUDE_MODEL
from .models import PlanningAuditEvent, PlanningFile, PlanningGeneration, PlanningJob, PlanningProject
from .serializers import (
    PlanningAuditEventSerializer, PlanningFileListSerializer, PlanningFileSerializer,
    PlanningGenerationEditSerializer, PlanningGenerationListSerializer,
    PlanningGenerationSerializer, PlanningJobSerializer, PlanningProjectSerializer,
)
from .services import byok_crypto, claude_client, export_utils
from .services.audit import record_event
from .services.validation_engine import validate
from .services.workflow_configuration import ensure_project_schedule_configuration
from .tasks import parse_uploaded_planning_file, run_planning_job

logger = logging.getLogger(__name__)

class PlanningProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = PlanningProjectSerializer
    queryset = PlanningProject.objects.all().filter(is_deleted=False).prefetch_related('files', 'generations')

    def get_queryset(self):
        return accessible_projects(self.request.user).select_related('enterprise_project').prefetch_related('files', 'generations')

    def perform_create(self, serializer):
        project = serializer.save(created_by=self.request.user)
        ensure_project_schedule_configuration(project, actor=self.request.user)
        record_event(project=project, actor=self.request.user, action='project.created', entity=project, after=serializer.data)

    def perform_update(self, serializer):
        before = PlanningProjectSerializer(serializer.instance).data
        project = serializer.save()
        record_event(project=project, actor=self.request.user, action='project.updated', entity=project, before=before, after=serializer.data)

    def perform_destroy(self, instance):
        """Soft-delete only — never hard-delete a project (RADAI global rule:
        archive/supersede, never destroy). Mirrors the is_deleted filtering
        already used everywhere else in this app's querysets."""
        instance.soft_delete()
        record_event(project=instance, actor=self.request.user, action='project.archived', entity=instance)

    def _require_byok(self, project):
        """Hard gate: refuse to analyze / generate when this project has no
        usable BYOK / Claude configuration. Returns a Response on failure,
        None on success."""
        if not byok_crypto.is_encryption_configured():
            return Response(
                {'error': 'Planning AI encryption is not configured on the server.', 'code': 'byok_encryption_unavailable'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if claude_client.get_claude_config(project) is None:
            return Response(
                {
                    'error': (
                        'This project has no active BYOK (Claude) configuration. '
                        'Open the AI Settings (BYOK) panel, enable it, save a valid '
                        'Anthropic API key, and run Test Connection before analyzing '
                        'or generating a schedule.'
                    ),
                    'code': 'byok_required',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return None

    @action(detail=True, methods=['post'], url_path='analyze')
    def analyze(self, request, pk=None):
        """Document Intelligence preview — does not persist a generation."""
        project = self.get_object()
        byok_error = self._require_byok(project)
        if byok_error is not None:
            return byok_error
        files_qs = project.files.filter(is_deleted=False, parse_status='done')
        if not files_qs.exists():
            return Response(
                {'error': 'No successfully parsed files yet. Upload files and wait for parsing to complete.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._enqueue_job(project, 'analyze', {})

    @action(detail=True, methods=['post'], url_path='generate')
    def generate(self, request, pk=None):
        """Runs the full planning pipeline: intelligence -> WBS -> activities
        -> EDDR -> manhours -> validation -> narrative, then persists a new
        PlanningGeneration version."""
        project = self.get_object()
        generation_options = (request.data or {}).get('generation_options') or {}
        expected_configuration_version = generation_options.get('expected_configuration_version')
        configuration, _ = ensure_project_schedule_configuration(project, actor=request.user)
        try:
            expected_configuration_version = (
                int(expected_configuration_version) if expected_configuration_version is not None else None
            )
        except (TypeError, ValueError):
            return Response(
                {'error': 'expected_configuration_version must be an integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if expected_configuration_version is not None and configuration and (
            expected_configuration_version != configuration.configuration_version
        ):
            return Response({
                'error': 'Schedule configuration changed after the wizard preview. Refresh the preview before generating.',
                'code': 'configuration_conflict',
                'current_configuration_version': configuration.configuration_version,
            }, status=status.HTTP_409_CONFLICT)
        byok_error = self._require_byok(project)
        if byok_error is not None:
            return byok_error
        if not project.files.filter(is_deleted=False, parse_status='done').exists():
            return Response({'error': 'No successfully parsed files are available.'}, status=status.HTTP_400_BAD_REQUEST)
        return self._enqueue_job(project, 'generate', dict(request.data or {}))

    @action(detail=True, methods=['post'], url_path='generation-preview')
    def generation_preview(self, request, pk=None):
        """Return the exact deterministic plan without creating a version."""
        project = self.get_object()
        byok_error = self._require_byok(project)
        if byok_error is not None:
            return byok_error
        if not project.files.filter(is_deleted=False, parse_status='done').exists():
            return Response(
                {'error': 'No successfully parsed files are available.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .services.pipeline import preview_schedule
        preview = preview_schedule(
            project, user=request.user,
            overrides=(request.data or {}).get('intelligence_overrides'),
        )
        return Response(preview)

    def _enqueue_job(self, project, job_type, request_data):
        active = project.jobs.filter(is_deleted=False, job_type=job_type, status__in=['queued', 'running']).first()
        if active:
            return Response(PlanningJobSerializer(active).data, status=status.HTTP_202_ACCEPTED)
        job = PlanningJob.objects.create(
            project=project, job_type=job_type, request_data=request_data, requested_by=self.request.user,
        )
        record_event(project=project, actor=self.request.user, action='job.queued', entity=job, after={'job_type': job_type})
        try:
            result = run_planning_job.delay(job.id)
            job.task_id = result.id or ''
            job.save(update_fields=['task_id', 'updated_at'])
        except Exception:  # noqa: BLE001
            logger.exception('Celery dispatch failed for planning job %s; executing eagerly', job.id)
            run_planning_job.apply(args=[job.id])
        job.refresh_from_db()
        return Response(PlanningJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

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
                'encryption_configured': byok_crypto.is_encryption_configured(),
            })

        if request.method == 'DELETE':
            project.ai_settings = {}
            project.save(update_fields=['ai_settings'])
            record_event(project=project, actor=request.user, action='ai_settings.removed', entity=project)
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
        if api_key and not byok_crypto.is_encryption_configured():
            return Response(
                {'error': 'BYOK_ENCRYPTION_KEY is not configured on the server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
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
        record_event(
            project=project, actor=request.user, action='ai_settings.updated', entity=project,
            after={'enabled': new_settings['enabled'], 'provider': new_settings['provider'], 'model': new_settings['model']},
        )
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
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    parser_classes = [MultiPartParser, FormParser]
    queryset = PlanningFile.objects.all().filter(is_deleted=False).select_related('project', 'uploaded_by')

    def get_serializer_class(self):
        if self.action == 'list':
            return PlanningFileListSerializer
        return PlanningFileSerializer

    def get_queryset(self):
        qs = super().get_queryset().filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs

    def perform_create(self, serializer):
        file_obj = serializer.validated_data.get('file')
        planning_file = serializer.save(
            uploaded_by=self.request.user if self.request.user.is_authenticated else None,
            original_filename=getattr(file_obj, 'name', ''),
            content_type=getattr(file_obj, 'content_type', ''),
            size_bytes=getattr(file_obj, 'size', 0) or 0,
            parse_status='pending',
        )
        record_event(
            project=planning_file.project, actor=self.request.user, action='file.uploaded',
            entity=planning_file, after={'filename': planning_file.original_filename, 'category': planning_file.category},
        )
        try:
            parse_uploaded_planning_file.delay(planning_file.id)
        except Exception as exc:  # noqa: BLE001
            logger.info('parse_uploaded_planning_file.delay failed (%s); running inline', exc)
            try:
                parse_uploaded_planning_file(planning_file.id)
            except Exception as inner:  # noqa: BLE001
                logger.warning('inline parse_uploaded_planning_file failed: %s', inner)

    def perform_destroy(self, instance):
        instance.soft_delete()
        record_event(project=instance.project, actor=self.request.user, action='file.archived', entity=instance)


class PlanningGenerationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    queryset = PlanningGeneration.objects.all().filter(is_deleted=False).select_related('project')

    def get_serializer_class(self):
        if self.action == 'list':
            return PlanningGenerationListSerializer
        return PlanningGenerationSerializer

    def get_queryset(self):
        qs = super().get_queryset().filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs

    @action(detail=True, methods=['patch'], url_path='edit')
    def edit(self, request, pk=None):
        """Create an immutable child revision with planner corrections to WBS,
        activities, EDDR, manhours, milestones or narrative.
        Body: partial JSON, e.g. {"wbs": [...]} or
        {"activities": [...]}. Only GENERATION_EDITABLE_FIELDS are accepted;
        computed fields (logic_matrix, validation, intelligence) are never
        editable here — re-run Generate to recompute those."""
        generation = self.get_object()
        edit_serializer = PlanningGenerationEditSerializer(data=request.data)
        edit_serializer.is_valid(raise_exception=True)
        validated = dict(edit_serializer.validated_data)
        change_summary = validated.pop('change_summary', '') or 'Planner correction'
        if not validated:
            return Response({'error': 'At least one editable schedule field is required.'}, status=status.HTTP_400_BAD_REQUEST)

        payload = {
            field: getattr(generation, field)
            for field in ('intelligence', 'wbs', 'activities', 'logic_matrix', 'eddr', 'milestones', 'manhours', 'validation', 'narrative')
        }
        payload.update(validated)
        if 'activities' in validated:
            payload['logic_matrix'] = [
                {
                    'activity_id': activity['id'], 'predecessor_id': predecessor.get('id'),
                    'type': predecessor.get('type', 'FS'), 'lag_days': predecessor.get('lag_days', 0),
                }
                for activity in payload['activities']
                for predecessor in activity.get('predecessors', [])
                if predecessor.get('id')
            ]
            payload['milestones'] = [activity for activity in payload['activities'] if activity.get('is_milestone')]
        payload['validation'] = validate(
            generation.project, payload['wbs'], payload['activities'], payload['eddr'], payload['intelligence'],
        )

        with transaction.atomic():
            project = PlanningProject.objects.select_for_update().get(pk=generation.project_id)
            next_version = (project.generations.aggregate(value=Max('version'))['value'] or 0) + 1
            revision = PlanningGeneration.objects.create(
                project=project, version=next_version, parent_generation=generation,
                change_summary=change_summary, generated_by=request.user, **payload,
            )
        record_event(
            project=project, actor=request.user, action='generation.revised', entity=revision,
            before={'generation_id': generation.id, 'version': generation.version},
            after={'generation_id': revision.id, 'version': revision.version, 'fields': sorted(validated)},
        )
        from .services.schedule_materializer import materialize_generation
        schedule_version, calculation_run, issues = materialize_generation(revision, requested_by=request.user)
        response_data = PlanningGenerationSerializer(revision).data
        response_data['schedule_version_id'] = schedule_version.id
        response_data['calculation_run_id'] = calculation_run.id if calculation_run else None
        response_data['materialization_issues'] = issues
        return Response(response_data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='materialize')
    def materialize(self, request, pk=None):
        """Idempotently convert an existing JSON generation to a CPM version."""
        generation = self.get_object()
        from .services.schedule_materializer import materialize_generation
        schedule_version, calculation_run, issues = materialize_generation(generation, requested_by=request.user)
        record_event(
            project=generation.project, actor=request.user, action='generation.materialized',
            entity=schedule_version, after={'generation_id': generation.id, 'run_id': getattr(calculation_run, 'id', None)},
        )
        return Response({
            'generation_id': generation.id,
            'schedule_id': schedule_version.schedule_id,
            'schedule_version_id': schedule_version.id,
            'calculation_run_id': calculation_run.id if calculation_run else None,
            'issues': issues,
        })

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

        if fmt == 'xer':
            content = export_utils.generation_to_xer_bytes(generation)
            response = HttpResponse(content, content_type='application/octet-stream')
            response['Content-Disposition'] = f'attachment; filename="{base_name}_schedule.xer"'
            return response

        # default: json
        content = export_utils.generation_to_json(generation)
        response = HttpResponse(content, content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="{base_name}.json"'
        return response


class PlanningJobViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = PlanningJobSerializer
    queryset = PlanningJob.objects.filter(is_deleted=False).select_related('project', 'result_generation')

    def get_queryset(self):
        queryset = super().get_queryset().filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        job = self.get_object()
        if job.status != 'queued':
            return Response({'error': 'Only queued jobs can be cancelled safely.'}, status=status.HTTP_409_CONFLICT)
        job.status = 'cancelled'
        job.message = 'Cancelled by user'
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'message', 'finished_at', 'updated_at'])
        record_event(project=job.project, actor=request.user, action='job.cancelled', entity=job)
        return Response(self.get_serializer(job).data)


class PlanningAuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = PlanningAuditEventSerializer
    queryset = PlanningAuditEvent.objects.select_related('project', 'actor')

    def get_queryset(self):
        queryset = super().get_queryset().filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset
