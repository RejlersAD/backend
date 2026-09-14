"""Server-owned pilot telemetry; stores identifiers and counters, never prompts."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
from datetime import timedelta
import logging
import time
import uuid

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)
_workflow = ContextVar('ai_workflow', default=None)


@contextmanager
def workflow_context(user, module, operation, key=None):
    if _workflow.get() is not None:
        yield _workflow.get()
        return
    row = None
    try:
        if user is not None and user.is_authenticated:
            from .models import UserProfile
            from .ai_measurement_models import AIWorkflowRun
            with transaction.atomic():
                profile = UserProfile.objects.select_for_update().filter(user=user, is_deleted=False, status='active').first()
                if profile and profile.organization_id:
                    now = timezone.now()
                    previous = AIWorkflowRun.objects.filter(user=user, organization_id=profile.organization_id).filter(
                        Q(started_at__gte=now - timedelta(minutes=30)) | Q(finished_at__gte=now - timedelta(minutes=30))).first()
                    row, _ = AIWorkflowRun.objects.get_or_create(user=user, module=module, operation=operation,
                        deduplication_key=sha256(f'{profile.organization_id}:{key or uuid.uuid4()}'.encode()).hexdigest(),
                        defaults={'started_at': now, 'organization_id': profile.organization_id, 'session_id': previous.session_id if previous else uuid.uuid4()})
                    if row.status != 'completed':
                        row.status, row.finished_at, row.error_code = 'running', None, ''
                        row.save(update_fields=['status', 'finished_at', 'error_code'])
    except Exception as exc:
        logger.warning('AI workflow start unavailable (%s)', type(exc).__name__)
    if row is not None:
        row._pending_usage = []
    token = _workflow.set(row)
    try:
        yield row
    except Exception as exc:
        finish_workflow(row, 'failed', type(exc).__name__)
        raise
    finally:
        _workflow.reset(token)
        if row is not None:
            pending, row._pending_usage = row._pending_usage, None
            for values in pending:
                record_usage(workflow=row, **values)


def finish_workflow(row, status, error=''):
    if row is None:
        return
    try:
        from .ai_measurement_models import AIWorkflowRun
        with transaction.atomic():
            # A repeated request cannot downgrade an already completed result.
            AIWorkflowRun.objects.filter(pk=row.pk).exclude(status='completed').update(status=status, finished_at=timezone.now(), error_code=error[:80])
    except Exception as exc:
        logger.warning('AI workflow completion unavailable (%s)', type(exc).__name__)


def bind_result(source_type, source_id):
    row = _workflow.get()
    if row is None:
        return
    try:
        from .ai_measurement_models import AIWorkflowRun
        with transaction.atomic():
            changed = AIWorkflowRun.objects.filter(pk=row.pk).exclude(status='completed').update(source_type=source_type, source_id=str(source_id))
            if changed:
                row.source_type, row.source_id = source_type, str(source_id)
    except Exception as exc:
        logger.warning('AI result attribution unavailable (%s)', type(exc).__name__)


def tracked_planning(operation):
    def decorate(function):
        @wraps(function)
        def wrapped(project, *args, **kwargs):
            parent = _workflow.get()
            fingerprint = kwargs.get('input_fingerprint')
            key = f'{project.pk}:{fingerprint}' if fingerprint else None
            with workflow_context(kwargs.get('user'), 'planning_package', operation, key) as row:
                result = function(project, *args, **kwargs)
                source = result[0] if isinstance(result, tuple) else result
                if getattr(source, 'pk', None):
                    bind_result(source._meta.label, source.pk)
                if parent is None:
                    finish_workflow(row, 'completed' if getattr(source, 'pk', None) else 'no_result')
                return result
        return wrapped
    return decorate


def tracked_planning_job(function):
    @wraps(function)
    def wrapped(task, job_id, *args, **kwargs):
        try:
            from apps.planning_intelligence.models import PlanningJob
            job = PlanningJob.objects.select_related('requested_by').filter(pk=job_id, is_deleted=False).first()
        except Exception as exc:
            logger.warning('AI job attribution unavailable (%s)', type(exc).__name__)
            return function(task, job_id, *args, **kwargs)
        if job is None or job.status in ('succeeded', 'cancelled'):
            return function(task, job_id, *args, **kwargs)
        with workflow_context(job.requested_by, 'planning_package', f'job:{job.job_type}', f'planning-job:{job.pk}') as row:
            result = function(task, job_id, *args, **kwargs)
            try:
                job.refresh_from_db(fields=['status', 'result_generation', 'result_data'])
                if job.result_generation_id:
                    bind_result('planning_intelligence.PlanningGeneration', job.result_generation_id)
                elif row and not row.source_id:
                    bind_result('planning_intelligence.PlanningJob', job.pk)
                finish_workflow(row, 'completed' if job.status == 'succeeded' else 'failed', '' if job.status == 'succeeded' else 'planning_job_failed')
            except Exception as exc:
                logger.warning('AI job completion attribution unavailable (%s)', type(exc).__name__)
            return result
    return wrapped


def tracked_pid(function):
    @wraps(function)
    def wrapped(self, request, *args, **kwargs):
        if request.method != 'POST' or not request.user.is_authenticated:
            return function(self, request, *args, **kwargs)
        # A caller-supplied idempotency key is scoped to user, route and drawing.
        key = request.headers.get('Idempotency-Key')
        if key:
            key = f'{kwargs.get("pk", "upload")}:{key[:200]}'
        with workflow_context(request.user, 'pid_analysis', function.__name__, key) as row:
            response = function(self, request, *args, **kwargs)
            finish_workflow(row, 'failed' if response.status_code >= 400 else 'completed' if row and row.source_id else 'no_result')
            return response
    return wrapped


def record_usage(*, user=None, workflow=None, provider, model, feature='', request_id=None,
                 tokens_input=0, tokens_output=0, latency_ms=0, success=True, error_code='', application='', observed_at=None, usage_available=True):
    row = workflow or _workflow.get()
    user = user or (row.user if row else None)
    if user is None:
        return
    observed_at = observed_at or timezone.now()
    pending = getattr(row, '_pending_usage', None)
    if pending is not None:
        pending.append(dict(user=user, provider=provider, model=model, feature=feature,
                            request_id=request_id, tokens_input=tokens_input, tokens_output=tokens_output,
                            latency_ms=latency_ms, success=success, error_code=error_code, application=application,
                            observed_at=observed_at, usage_available=usage_available))
        return
    try:
        from .ai_champion_models import AIUsageLog, AIPricingConfig
        with transaction.atomic():
            pricing = AIPricingConfig.objects.filter(provider=provider, model_name=model, is_active=True,
                currency='USD', effective_from__lte=observed_at).order_by('-effective_from').first()
            values = dict(user=user, timestamp=observed_at, application=application or row.module, feature=feature[:64], model_name=str(model)[:128],
                          tokens_input=max(0, int(tokens_input or 0)), tokens_output=max(0, int(tokens_output or 0)),
                          latency_ms=max(0, int(latency_ms)), success=success, error_code=error_code[:64], provenance='server',
                          pricing_recorded=pricing is not None and usage_available,
                          cost_usd=pricing.compute_cost(tokens_input or 0, tokens_output or 0) if pricing and usage_available else 0)
            AIUsageLog.objects.get_or_create(workflow=row, provider=provider, request_id=str(request_id or uuid.uuid4())[:64], defaults=values)
    except Exception as exc:
        logger.warning('AI request measurement unavailable (%s)', type(exc).__name__)


class _ObservedClient:
    """Wrap only a pilot's client instance; SDK retries remain one SDK call."""
    def __init__(self, target, provider, path='', workflow=None, model=''):
        self._target, self._provider, self._path = target, provider, path
        self._workflow, self._model = workflow, model

    def __getattr__(self, name):
        value = getattr(self._target, name)
        path = f'{self._path}.{name}'.strip('.')
        if path in ('chat', 'chat.completions', 'responses', 'models', 'images', 'embeddings'):
            return _ObservedClient(value, self._provider, path, self._workflow, self._model)
        if path == 'GenerativeModel':
            return lambda model, *a, **kw: _ObservedClient(value(model, *a, **kw), self._provider, workflow=self._workflow, model=model)
        if path not in ('chat.completions.create', 'responses.create', 'models.generate_content', 'generate_content', 'images.generate', 'embeddings.create'):
            return value

        def call(*args, **kwargs):
            row = _workflow.get()
            if row is None and getattr(self._workflow, '_pending_usage', None) is not None:
                row = self._workflow
            if row is None or kwargs.get('stream'):
                return value(*args, **kwargs)
            started = time.monotonic()
            response, failure = None, ''
            try:
                response = value(*args, **kwargs)
                return response
            except Exception as exc:
                failure = type(exc).__name__
                raise
            finally:
                usage = getattr(response, 'usage', None) or getattr(response, 'usage_metadata', None)
                record_usage(workflow=row, provider=self._provider, model=kwargs.get('model') or self._model or 'unknown',
                    usage_available=usage is not None and path != 'images.generate',
                    feature=path, request_id=getattr(response, 'id', None) or getattr(response, 'response_id', None),
                    tokens_input=getattr(usage, 'prompt_tokens', None) or getattr(usage, 'input_tokens', None) or getattr(usage, 'prompt_token_count', 0),
                    tokens_output=getattr(usage, 'completion_tokens', None) or getattr(usage, 'output_tokens', None) or getattr(usage, 'candidates_token_count', 0),
                    latency_ms=int((time.monotonic() - started) * 1000), success=not failure, error_code=failure)
        return call


def observed_openai(*args, **kwargs):
    from openai import OpenAI
    return _ObservedClient(OpenAI(*args, **kwargs), 'openai', workflow=_workflow.get())


def observed_google(client):
    return _ObservedClient(client, 'google', workflow=_workflow.get())


def observed_client(client, provider='openai'):
    return client if isinstance(client, _ObservedClient) else _ObservedClient(client, provider, workflow=_workflow.get())


def tracked_http(module, methods=('POST',)):
    """Measure authenticated processing endpoints, including function views.

    A delivered response is distinct from a persisted, reviewable output.
    """
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            request = next((arg for arg in args if hasattr(arg, 'method') and hasattr(arg, 'user')), None)
            if request is None or request.method not in methods or not request.user.is_authenticated:
                return function(*args, **kwargs)
            parent = _workflow.get()
            key = request.headers.get('Idempotency-Key')
            if key:
                key = f'{kwargs.get("pk", "upload")}:{key[:200]}'
            with workflow_context(request.user, module, function.__name__, key) as row:
                response = function(*args, **kwargs)
                if parent is None:
                    payload = getattr(response, 'data', None)
                    failed = response.status_code >= 400 or isinstance(payload, dict) and payload.get('success') is False
                    finish_workflow(row, 'failed' if failed else 'completed' if row and row.source_id else 'returned')
                return response
        return wrapped
    return decorate


def tracked_user_job(module):
    """Bind worker activity to an explicit actor, never a worker account."""
    def decorate(function):
        from inspect import signature
        parameters = signature(function)
        @wraps(function)
        def wrapped(*args, **kwargs):
            values = parameters.bind(*args, **kwargs).arguments
            user = None
            try:
                from django.contrib.auth import get_user_model
                user = get_user_model().objects.filter(pk=values.get('user_id')).first()
            except Exception as exc:
                logger.warning('AI job attribution unavailable (%s)', type(exc).__name__)
            task = values.get('self')
            key = getattr(getattr(task, 'request', None), 'id', None) or values.get('task_id') or values.get('document_id')
            with workflow_context(user, module, function.__name__, key) as row:
                result = function(*args, **kwargs)
                failed = isinstance(result, dict) and (result.get('success') is False or result.get('error'))
                finish_workflow(row, 'failed' if failed else 'completed' if row and row.source_id else 'returned')
                return result
        return wrapped
    return decorate
