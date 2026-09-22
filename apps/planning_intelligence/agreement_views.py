"""Single-upload agreement setup with durable, project-scoped analysis jobs."""
import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import serializers
from rest_framework.exceptions import MethodNotAllowed, NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.project_models import Project, ProjectMember
from apps.rbac.action_policy import module_action_allowed
from .access import can_access_enterprise_project
from .config import CLAUDE_API_KEY_PATTERN, CLAUDE_MODEL_VALUES, DEFAULT_CLAUDE_MODEL, MAX_FILE_BYTES
from .models import PlanningFile, PlanningJob, PlanningProject
from .serializers import PlanningJobSerializer
from .services import byok_crypto
from .services.agreement_workspace import accept_agreement_workspace, agreement_workspace_summary, source_fingerprint
from .services.audit import record_event
from .services.claude_client import get_claude_config
from .services.evidence_graph import EvidenceError
from .services.operational_jobs import canonical_fingerprint, dispatch_job, get_or_create_job
from .services.project_setup import require_setup_access

logger = logging.getLogger(__name__)


class AgreementAnalysisSerializer(serializers.Serializer):
    idempotency_key = serializers.UUIDField()
    file = serializers.FileField(required=False)
    file_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False,
                                    min_length=1, max_length=20)
    name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    code = serializers.CharField(required=False, allow_blank=True, max_length=50)
    ai_api_key = serializers.CharField(required=False, allow_blank=True, max_length=512, write_only=True)
    ai_model = serializers.ChoiceField(choices=sorted(CLAUDE_MODEL_VALUES), required=False, allow_blank=True)

    def validate_file(self, value):
        if value.size > MAX_FILE_BYTES:
            raise serializers.ValidationError('The agreement exceeds the upload size limit.')
        if Path(value.name).suffix.lower() not in {'.pdf', '.docx', '.xlsx', '.csv', '.txt'}:
            raise serializers.ValidationError('Upload a PDF, Word, Excel, CSV or text agreement.')
        return value

    def validate(self, data):
        if bool(data.get('file')) == bool(data.get('file_ids')):
            raise serializers.ValidationError('Upload one agreement or select existing project documents.')
        if self.context.get('create') and not data.get('file'):
            raise serializers.ValidationError({'file': 'Upload an agreement to create the project.'})
        if not self.context.get('create') and any(data.get(key) for key in ('name', 'code', 'ai_api_key', 'ai_model')):
            raise serializers.ValidationError('Update the existing project and its AI connection in project settings.')
        key = data.get('ai_api_key', '')
        if key and not CLAUDE_API_KEY_PATTERN.fullmatch(key):
            raise serializers.ValidationError({'ai_api_key': 'Enter a valid Anthropic API key.'})
        if key and not byok_crypto.is_encryption_configured():
            raise EvidenceError('AI key encryption is not configured on this server.', 'byok_encryption_unavailable', 503)
        return data


class AgreementAcceptanceSerializer(serializers.Serializer):
    workspace_id = serializers.UUIDField()
    revision = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=4000, default='')
    selected_fact_ids = serializers.ListField(child=serializers.CharField(max_length=128), required=False,
                                            max_length=10000)


def _dispatch(job):
    try:
        dispatch_job(job)
    except RuntimeError:
        logger.exception('Could not dispatch agreement analysis job %s', job.pk)


def _request_fingerprint(data):
    upload = data.get('file')
    digest = hashlib.sha256()
    if upload:
        for chunk in upload.chunks():
            digest.update(chunk)
        upload.seek(0)
    return canonical_fingerprint({
        'sha256': digest.hexdigest() if upload else None,
        'filename': upload.name if upload else None,
        'file_ids': sorted(set(data.get('file_ids', []))),
        'name': data.get('name', ''), 'code': data.get('code', ''), 'ai_model': data.get('ai_model', ''),
        'ai_key_digest': hashlib.sha256(data.get('ai_api_key', '').encode('utf-8')).hexdigest(),
    })


@method_decorator(sensitive_post_parameters('ai_api_key'), name='dispatch')
class AgreementWorkspaceView(APIView):
    permission_classes = [IsAuthenticated]
    operation = None

    @property
    def permission_action(self):
        if self.request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return 'read'
        return 'create' if self.operation == 'create' else 'update'

    def enterprise(self, request, project_id, *, write=False, lock=False):
        action = 'update' if write else 'read'
        if not module_action_allowed(request.user, 'planning_package', action):
            raise PermissionDenied('Your access does not permit this agreement workspace action.')
        query = Project.objects.select_for_update() if lock else Project.objects
        project = get_object_or_404(query, pk=project_id, is_deleted=False)
        if not can_access_enterprise_project(request.user, project):
            raise NotFound()
        if write and not can_access_enterprise_project(request.user, project, write=True):
            raise PermissionDenied('Your project role permits viewing, but not updating this agreement draft.')
        return project

    def envelope(self, request, enterprise, *, job=None):
        project = PlanningProject.objects.filter(enterprise_project=enterprise, is_deleted=False).first()
        can_edit = (can_access_enterprise_project(request.user, enterprise, write=True)
                    and module_action_allowed(request.user, 'planning_package', 'update'))
        config = get_claude_config(project) if project else None
        workspace = agreement_workspace_summary(project, actor=request.user) if project else None
        jobs = project.jobs.filter(job_type='agreement_setup', is_deleted=False).order_by('-created_at', '-pk') if project else None
        active = jobs.filter(status__in=['queued', 'running']).first() if jobs is not None else None
        latest = jobs.first() if jobs is not None else None
        files = []
        if project:
            for source in project.files.filter(is_deleted=False).order_by('-pk'):
                try:
                    url = source.file.url if source.file else None
                    if url:
                        url = request.build_absolute_uri(url)
                except (OSError, ValueError):
                    url = None
                files.append({'id': source.pk, 'original_filename': source.original_filename,
                              'category': source.category, 'parse_status': source.parse_status,
                              'url': url, 'preview_url': url, 'file': url})
        urls = {source['id']: source['preview_url'] for source in files}
        if workspace:
            for section in workspace.get('projection', {}).values():
                for item in section.get('items', []):
                    for source in item.get('sources', []):
                        source['preview_url'] = urls.get(source.get('file_id'))
        serialize_job = lambda row: PlanningJobSerializer(row, context={'request': request}).data if row else None
        data = {
            'enterprise_project_id': enterprise.pk, 'planning_project_id': project.pk if project else None,
            'workspace': workspace, 'permissions': {
                'can_analyze': bool(can_edit and (project or module_action_allowed(request.user, 'planning_package', 'create'))),
                'can_accept': bool(can_edit and workspace and not active and not workspace.get('stale')),
                'analyze_reason': ('Project and planning workspace update access are required.' if not can_edit else
                                   'Planning workspace creation access is required.'
                                   if not project and not module_action_allowed(request.user, 'planning_package', 'create') else ''),
                'accept_reason': ('The source documents changed. Analyze the current agreement again before accepting inputs.'
                                  if workspace and workspace.get('stale') else
                                  'Wait for the current agreement analysis to finish.' if active else
                                  'Project update permission is required to accept supported inputs.' if not can_edit else ''),
            },
            'active_job': serialize_job(active), 'latest_job': serialize_job(latest), 'files': files,
            'ai': {'available': bool(config), 'provider': 'anthropic', 'model': config['model'] if config else None,
                   'reason': '' if config else 'Configure Claude in the project planning settings to enable AI analysis.'},
        }
        if job:
            data.update(job=serialize_job(job), enterprise_project={'id': enterprise.pk, 'name': enterprise.name, 'code': enterprise.code},
                        planning_project={'id': project.pk, 'name': project.name})
        return data

    def get(self, request, project_id=None):
        if self.operation:
            raise MethodNotAllowed('GET')
        return Response(self.envelope(request, self.enterprise(request, project_id)))

    def post(self, request, project_id=None):
        try:
            if self.operation == 'accept':
                enterprise = self.enterprise(request, project_id, write=True)
                project = get_object_or_404(PlanningProject, enterprise_project=enterprise, is_deleted=False)
                serializer = AgreementAcceptanceSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                if project.jobs.filter(job_type='agreement_setup', status__in=['queued', 'running'], is_deleted=False).exists():
                    raise EvidenceError('Wait for the current agreement analysis to finish.', 'agreement_analysis_running')
                accept_agreement_workspace(project, request.user, **serializer.validated_data)
                return Response(self.envelope(request, enterprise))
            if self.operation not in {'create', 'analyze'}:
                raise MethodNotAllowed('POST')
            creating = self.operation == 'create'
            if creating:
                require_setup_access(request.user)
            else:
                self.enterprise(request, project_id, write=True)
            serializer = AgreementAnalysisSerializer(data=request.data, context={'create': creating})
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            fingerprint = _request_fingerprint(data)
            key = canonical_fingerprint({'operation': self.operation, 'actor': request.user.pk,
                                         'project': project_id, 'key': str(data['idempotency_key'])})
            with transaction.atomic():
                if creating:
                    # Serializing this creator also covers retries before a project exists.
                    get_user_model().objects.select_for_update().get(pk=request.user.pk)
                    existing = PlanningJob.objects.filter(job_type='agreement_setup', idempotency_key=key,
                                                          requested_by=request.user, is_deleted=False).select_related('project__enterprise_project').first()
                    if existing:
                        return self.replay(request, existing, fingerprint)
                    code = data.get('code') or f'AGR-{uuid4().hex[:12].upper()}'
                    if Project.objects.filter(code=code).exists():
                        raise serializers.ValidationError({'code': 'A project already uses this code.'})
                    enterprise = Project.objects.create(name=data.get('name') or 'Agreement project', code=code,
                                                        owner=request.user, custom_fields={'agreement_setup': True})
                    ProjectMember.objects.create(project=enterprise, user=request.user, role='project_manager')
                else:
                    enterprise = self.enterprise(request, project_id, write=True, lock=True)
                project = PlanningProject.objects.filter(enterprise_project=enterprise, is_deleted=False).first()
                if not project:
                    if not module_action_allowed(request.user, 'planning_package', 'create'):
                        raise PermissionDenied('Planning workspace creation access is required.')
                    project = PlanningProject.objects.create(enterprise_project=enterprise, name=enterprise.name,
                                client=enterprise.client_name, created_by=request.user, duration_months=0)
                project = PlanningProject.objects.select_for_update().get(pk=project.pk)
                existing = project.jobs.filter(job_type='agreement_setup', idempotency_key=key, is_deleted=False).first()
                if existing:
                    return self.replay(request, existing, fingerprint)
                if project.jobs.filter(job_type='agreement_setup', status__in=['queued', 'running'], is_deleted=False).exists():
                    raise EvidenceError('An agreement analysis is already running. Its progress is saved on this project.', 'agreement_analysis_running')
                if data.get('ai_api_key'):
                    project.ai_settings = {'enabled': True, 'provider': 'anthropic',
                        'model': data.get('ai_model') or DEFAULT_CLAUDE_MODEL,
                        'api_key_encrypted': byok_crypto.encrypt_api_key(data['ai_api_key']),
                        'key_updated_at': timezone.now().isoformat()}
                    project.save(update_fields=['ai_settings', 'updated_at'])
                upload = data.get('file')
                if upload:
                    source = PlanningFile.objects.create(project=project, category='agreement', file=upload,
                        original_filename=upload.name, content_type=upload.content_type or '', size_bytes=upload.size,
                        uploaded_by=request.user, parse_status='pending')
                    file_ids = [source.pk]
                    record_event(project=project, actor=request.user, action='file.uploaded', entity=source,
                                 after={'filename': source.original_filename, 'category': 'agreement'})
                else:
                    file_ids = sorted(set(data['file_ids']))
                    if project.files.filter(pk__in=file_ids, is_deleted=False).count() != len(file_ids):
                        raise serializers.ValidationError({'file_ids': 'Select files from this project.'})
                job, _ = get_or_create_job(project, 'agreement_setup',
                    {'file_ids': file_ids, 'request_fingerprint': fingerprint,
                     'source_fingerprint': source_fingerprint(project, file_ids)}, request.user, idempotency_key=key)
                record_event(project=project, actor=request.user, action='agreement.queued', entity=job,
                             after={'file_ids': file_ids, 'created_project': creating})
                transaction.on_commit(lambda: _dispatch(job))
            return Response(self.envelope(request, enterprise, job=job), status=202)
        except EvidenceError as exc:
            return Response(exc.payload, status=exc.status_code)
        except IntegrityError:
            return Response({'error': 'The project changed during this request. Reload before trying again.',
                             'code': 'agreement_project_conflict'}, status=409)

    def replay(self, request, job, fingerprint):
        if (job.request_data or {}).get('request_fingerprint') != fingerprint:
            raise EvidenceError('This request key was already used for different agreement inputs.', 'agreement_request_conflict')
        enterprise = self.enterprise(request, job.project.enterprise_project_id, write=True)
        return Response(self.envelope(request, enterprise, job=job), status=202)
