"""AI Model hub admin endpoints (mounted at /api/v1/rbac/admin/ai/).

Secrets are never returned (prefix…last4 only) and never logged. The
credentials/test endpoint performs a live probe and saves NOTHING.
"""
import json
import time

from django.conf import settings
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.database_maintenance_views import IsActiveUser
from apps.rbac.permissions import IsAdmin

from .ai_credentials import decrypt_api_key, encrypt_api_key, encryption_ready
from .ai_hub_adapters import AdapterError, NotConfigured, audit, list_models
from .ai_hub_models import (
    AICredential, AIModel, AIProvider, AIRoute, AISyncRun, AIHubSettings,
)

PERSONAL_PROVIDER_ALIAS = {'claude': 'anthropic'}


def _perm_classes():
    return [IsAuthenticated, IsActiveUser, IsAdmin]


def _provider_json(provider, with_counts=True):
    data = {
        'id': provider.id, 'name': provider.name, 'group': provider.group,
        'protocol': provider.protocol, 'auth_kind': provider.auth_kind,
        'default_base_url': provider.default_base_url, 'list_endpoint': provider.list_endpoint,
        'enabled': provider.enabled, 'sort': provider.sort,
    }
    if with_counts:
        creds = provider.credentials.filter(revoked_at__isnull=True).count()
        server_keys = getattr(settings, 'AI_HUB_SERVER_KEYS', {}) or {}
        if creds:
            status_ = 'connected'
        elif provider.id in server_keys:
            status_ = 'server_config'
        else:
            status_ = 'not_connected'
        data.update(status=status_, credentials_count=creds,
                    models_count=provider.models.count())
    return data


def _model_json(model):
    return {
        'provider_id': model.provider_id, 'provider_name': model.provider.name,
        'provider_group': model.provider.group, 'model_id': model.model_id,
        'maker': model.maker, 'family': model.family, 'variant': model.variant,
        'capabilities': model.capabilities, 'weights': model.weights, 'licence': model.licence,
        'context_tokens': model.context_tokens,
        'price_in_per_m': float(model.price_in_per_m) if model.price_in_per_m is not None else None,
        'price_out_per_m': float(model.price_out_per_m) if model.price_out_per_m is not None else None,
        'status': model.status, 'status_note': model.status_note,
        'enabled': model.enabled, 'source': model.source,
    }


def _credential_json(cred):
    return {
        'id': str(cred.id), 'provider_id': cred.provider_id, 'provider_name': cred.provider.name,
        'name': cred.name, 'scope': cred.scope,
        'owner_display': 'Organisation' if cred.scope == 'org' else ', '.join(cred.module_ids or []),
        'module_ids': cred.module_ids, 'key_display': cred.key_display,
        'models_allowed': cred.models_allowed,
        'monthly_cap_usd': float(cred.monthly_cap_usd) if cred.monthly_cap_usd is not None else None,
        'rotate_days': cred.rotate_days,
        'last_test_at': cred.last_test_at, 'last_test_model': cred.last_test_model,
        'last_test_ok': cred.last_test_ok,
        'created_by': str(cred.created_by_id) if cred.created_by_id else None,
        'created_at': cred.created_at,
        'revoked_at': cred.revoked_at,
    }


def _personal_rows(user):
    """Project the user's own BYOK SldUserAIKey rows as scope=personal."""
    try:
        from apps.electrical_comparison.models import SldUserAIKey
    except Exception:
        return []
    rows = []
    for key in SldUserAIKey.objects.filter(user=user):
        provider_id = PERSONAL_PROVIDER_ALIAS.get(key.provider, key.provider)
        provider = AIProvider.objects.filter(id=provider_id).first()
        rows.append({
            'id': str(key.id), 'provider_id': provider_id,
            'provider_name': provider.name if provider else key.provider,
            'name': f'Personal {key.provider} key', 'scope': 'personal',
            'owner_display': getattr(user, 'email', None) or str(user),
            'module_ids': [], 'key_display': f'••••{key.key_last4}',
            'models_allowed': [], 'monthly_cap_usd': None, 'rotate_days': None,
            'last_test_at': key.last_tested_at, 'last_test_model': '', 'last_test_ok': None,
            'created_by': str(user.pk), 'created_at': key.created_at, 'revoked_at': None,
            'personal': True,
        })
    return rows


class ProvidersView(APIView):
    permission_classes = _perm_classes()

    def get(self, request):
        providers = [_provider_json(p) for p in
                     AIProvider.objects.prefetch_related('models', 'credentials')]
        return Response({'providers': providers})


class ModelsView(APIView):
    permission_classes = _perm_classes()

    def get(self, request):
        qs = AIModel.objects.select_related('provider')
        search = request.query_params.get('search', '').strip().lower()
        if search:
            qs = qs.filter(model_id__icontains=search)
        weights = request.query_params.get('weights')
        if weights in ('open', 'closed'):
            qs = qs.filter(weights=weights)
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        provider = request.query_params.get('provider')
        if provider:
            qs = qs.filter(provider_id=provider)
        caps = [c.strip() for c in request.query_params.get('caps', '').split(',') if c.strip()]

        models, per_capability, per_status = [], {}, {}
        for model in qs:
            if caps and not set(caps).issubset(set(model.capabilities or [])):
                continue
            models.append(_model_json(model))
            per_status[model.status] = per_status.get(model.status, 0) + 1
            for cap in model.capabilities or []:
                per_capability[cap] = per_capability.get(cap, 0) + 1
        return Response({'models': models,
                         'counts': {'per_capability': per_capability, 'per_status': per_status}})


class ModelDetailView(APIView):
    permission_classes = _perm_classes()

    def patch(self, request, provider_id, model_id):
        enabled = request.data.get('enabled')
        if not isinstance(enabled, bool):
            return Response({'detail': 'enabled must be a boolean.'},
                            status=status.HTTP_400_BAD_REQUEST)
        model = AIModel.objects.filter(provider_id=provider_id, model_id=model_id).first()
        if model is None:
            return Response({'detail': 'Model not found.'}, status=status.HTTP_404_NOT_FOUND)
        model.enabled = enabled
        model.save(update_fields=['enabled'])
        audit(request.user, 'model_enable' if enabled else 'model_disable',
              provider=provider_id, detail={'model_id': model_id})
        return Response(_model_json(model))


class CredentialsView(APIView):
    permission_classes = _perm_classes()

    def get(self, request):
        creds = [_credential_json(c) for c in
                 AICredential.objects.select_related('provider').filter(revoked_at__isnull=True)]
        creds.extend(_personal_rows(request.user))
        return Response({'credentials': creds, 'storage_configured': encryption_ready()})

    @sensitive_variables()
    def post(self, request):
        if not encryption_ready():
            return Response({'detail': 'Secret storage is not configured.', 'code': 'storage_not_configured'},
                            status=status.HTTP_403_FORBIDDEN)
        data = request.data if isinstance(request.data, dict) else {}
        provider = AIProvider.objects.filter(id=data.get('provider_id')).first()
        if provider is None:
            return Response({'detail': 'Unknown provider_id.'}, status=status.HTTP_400_BAD_REQUEST)
        name = (data.get('name') or '').strip()
        scope = data.get('scope') or 'org'
        secret = data.get('secret') or ''
        if not name or scope not in ('org', 'module'):
            return Response({'detail': 'name and scope (org|module) are required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if provider.auth_kind not in ('base', 'local') and not secret:
            return Response({'detail': 'secret is required for this provider.'},
                            status=status.HTTP_400_BAD_REQUEST)
        payload = secret
        extras = {k: data[k] for k in ('access_key', 'secret_key', 'region', 'project', 'api_version')
                  if data.get(k)}
        if extras:
            payload = json.dumps({'secret': secret, **extras})
        cred = AICredential.objects.create(
            provider=provider, name=name, scope=scope,
            module_ids=data.get('module_ids') or [],
            encrypted_secret=encrypt_api_key(payload),
            secret_prefix=secret[:4] if secret else '',
            secret_last4=secret[-4:] if secret else '',
            base_url=(data.get('base_url') or '').strip(),
            models_allowed=data.get('models_allowed') or [],
            monthly_cap_usd=data.get('monthly_cap_usd'),
            rotate_days=data.get('rotate_days') or 90,
            created_by=request.user,
        )
        audit(request.user, 'create', provider=provider.id, credential_id=cred.id,
              detail={'name': name, 'scope': scope})
        return Response(_credential_json(cred), status=status.HTTP_201_CREATED)


@sensitive_variables()
def _run_test(provider, secret, base_url=None, extra=None):
    started = time.monotonic()
    try:
        rows = list_models(provider, secret=secret, base_url=base_url or None, extra=extra)
        return {'ok': True,
                'models': [{'model_id': r['model_id'], 'status': r.get('status', 'ga')}
                           for r in rows[:50]],
                'latency_ms': int((time.monotonic() - started) * 1000)}
    except (NotConfigured, AdapterError) as exc:
        return {'ok': False, 'models': [],
                'latency_ms': int((time.monotonic() - started) * 1000),
                'error': str(exc) or 'not_configured'}


class CredentialAnonymousTestView(APIView):
    """Live probe of raw auth fields; saves NOTHING (no DB writes)."""
    permission_classes = _perm_classes()

    @sensitive_variables()
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        provider = AIProvider.objects.filter(id=data.get('provider_id')).first()
        if provider is None:
            return Response({'detail': 'Unknown provider_id.'}, status=status.HTTP_400_BAD_REQUEST)
        extra = {k: data[k] for k in ('access_key', 'secret_key', 'region', 'project', 'api_version')
                 if data.get(k)}
        result = _run_test(provider, data.get('secret') or '',
                           base_url=(data.get('base_url') or '').strip() or None, extra=extra)
        return Response(result, status=status.HTTP_200_OK if result['ok'] else status.HTTP_200_OK)


class CredentialTestView(APIView):
    permission_classes = _perm_classes()

    @sensitive_variables()
    def post(self, request, pk):
        cred = AICredential.objects.select_related('provider').filter(pk=pk, revoked_at__isnull=True).first()
        if cred is None:
            return Response({'detail': 'Credential not found.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            payload = decrypt_api_key(cred.encrypted_secret)
        except Exception:
            return Response({'ok': False, 'models': [], 'latency_ms': 0,
                             'error': 'credential_unreadable'})
        secret, extra = payload, {}
        if payload.startswith('{'):
            try:
                blob = json.loads(payload)
                secret = blob.get('secret', '')
                extra = {k: v for k, v in blob.items() if k != 'secret'}
            except ValueError:
                pass
        result = _run_test(cred.provider, secret, base_url=cred.base_url or None, extra=extra)
        cred.last_test_at = timezone.now()
        cred.last_test_ok = result['ok']
        cred.last_test_model = result['models'][0]['model_id'] if result['models'] else ''
        cred.save(update_fields=['last_test_at', 'last_test_ok', 'last_test_model'])
        audit(request.user, 'test', provider=cred.provider_id, credential_id=cred.id,
              detail={'ok': result['ok'], 'latency_ms': result['latency_ms']})
        return Response(result)


class CredentialRotateView(APIView):
    permission_classes = _perm_classes()

    @sensitive_variables()
    def post(self, request, pk):
        if not encryption_ready():
            return Response({'detail': 'Secret storage is not configured.', 'code': 'storage_not_configured'},
                            status=status.HTTP_403_FORBIDDEN)
        cred = AICredential.objects.filter(pk=pk, revoked_at__isnull=True).first()
        if cred is None:
            return Response({'detail': 'Credential not found.'}, status=status.HTTP_404_NOT_FOUND)
        secret = (request.data.get('secret') or '') if isinstance(request.data, dict) else ''
        if not secret:
            return Response({'detail': 'secret is required.'}, status=status.HTTP_400_BAD_REQUEST)
        cred.encrypted_secret = encrypt_api_key(secret)
        cred.secret_prefix = secret[:4]
        cred.secret_last4 = secret[-4:]
        cred.save(update_fields=['encrypted_secret', 'secret_prefix', 'secret_last4'])
        audit(request.user, 'rotate', provider=cred.provider_id, credential_id=cred.id)
        return Response(_credential_json(cred))


class CredentialDetailView(APIView):
    permission_classes = _perm_classes()

    def delete(self, request, pk):
        cred = AICredential.objects.filter(pk=pk, revoked_at__isnull=True).first()
        if cred is None:
            return Response({'detail': 'Credential not found.'}, status=status.HTTP_404_NOT_FOUND)
        hard = str(request.query_params.get('hard', '')).lower() in ('1', 'true', 'yes')
        if hard:
            # Permanent delete; audit row survives (credential_id is a plain field).
            audit(request.user, 'delete', provider=cred.provider_id, credential_id=cred.id)
            cred.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        cred.revoked_at = timezone.now()
        cred.enabled = False
        cred.save(update_fields=['revoked_at', 'enabled'])
        audit(request.user, 'revoke', provider=cred.provider_id, credential_id=cred.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _validate_slot(slot, route):
    """Return error string for a chain slot, or None when eligible."""
    if slot is None:
        return None
    if not isinstance(slot, str) or ':' not in slot:
        return 'expected "provider:model"'
    provider_id, model_id = slot.split(':', 1)
    model = AIModel.objects.select_related('provider').filter(
        provider_id=provider_id, model_id=model_id).first()
    if model is None:
        return 'unknown model'
    if not model.enabled:
        return 'model is not enabled'
    if model.status == 'shut_down':
        return 'model is shut down'
    missing = set(route.needs or []) - set(model.capabilities or [])
    if missing:
        return f'missing capabilities: {", ".join(sorted(missing))}'
    if route.data_policy == 'onprem' and model.provider.group != 'self':
        return 'data policy requires a self-hosted provider'
    return None


class RoutesView(APIView):
    permission_classes = _perm_classes()

    def get(self, request):
        tasks = [{
            'task_id': r.task_id, 'label': r.label, 'module': r.module, 'path': r.path,
            'needs': r.needs, 'data_policy': r.data_policy,
            'chain': list((r.chain or []) + [None, None, None])[:3],
        } for r in AIRoute.objects.all()]
        return Response({'tasks': tasks})


class RouteDetailView(APIView):
    permission_classes = _perm_classes()

    def put(self, request, task_id):
        route = AIRoute.objects.filter(task_id=task_id).first()
        if route is None:
            return Response({'detail': 'Unknown task.'}, status=status.HTTP_404_NOT_FOUND)
        chain = request.data.get('chain') if isinstance(request.data, dict) else None
        if not isinstance(chain, list) or len(chain) > 3:
            return Response({'detail': 'chain must be a list of up to 3 slots.'},
                            status=status.HTTP_400_BAD_REQUEST)
        errors = [_validate_slot(slot, route) for slot in chain]
        if any(errors):
            return Response({'detail': 'Routing chain has ineligible slots.',
                             'errors': errors}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        route.chain = chain
        route.save(update_fields=['chain'])
        audit(request.user, 'routing_change', detail={'task_id': task_id, 'chain': chain})
        return Response({'task_id': route.task_id,
                         'chain': list((route.chain or []) + [None, None, None])[:3]})


class SyncView(APIView):
    permission_classes = _perm_classes()

    def post(self, request):
        run = AISyncRun.objects.create(status='running')
        try:
            from .ai_hub_tasks import sync_ai_catalogue
            if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
                sync_ai_catalogue(run_id=str(run.id))
            else:
                sync_ai_catalogue.delay(run_id=str(run.id))
        except Exception:
            sync_ai_catalogue(run_id=str(run.id))
        return Response({'run_id': str(run.id)}, status=status.HTTP_202_ACCEPTED)


class SyncRunView(APIView):
    permission_classes = _perm_classes()

    def get(self, request, run_id):
        run = AISyncRun.objects.filter(id=run_id).first()
        if run is None:
            return Response({'detail': 'Run not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'id': str(run.id), 'started_at': run.started_at,
                         'finished_at': run.finished_at, 'status': run.status,
                         'per_source': run.per_source, 'changes': run.changes})


class SyncScheduleView(APIView):
    permission_classes = _perm_classes()

    def get(self, request):
        row = AIHubSettings.get()
        return Response({'frequency': row.frequency, 'time': row.time,
                         'enrich_openrouter': row.enrich_openrouter,
                         'new_models_disabled': row.new_models_disabled,
                         'email_on_deprecation': row.email_on_deprecation})

    def put(self, request):
        row = AIHubSettings.get()
        data = request.data if isinstance(request.data, dict) else {}
        if data.get('frequency') in ('6h', 'daily', 'weekly', 'manual'):
            row.frequency = data['frequency']
        time_value = data.get('time')
        if isinstance(time_value, str) and len(time_value) == 5 and ':' in time_value:
            row.time = time_value
        for field in ('enrich_openrouter', 'new_models_disabled', 'email_on_deprecation'):
            if isinstance(data.get(field), bool):
                setattr(row, field, data[field])
        row.save()
        audit(request.user, 'schedule_change', detail={'frequency': row.frequency})
        return self.get(request)


class StorageStatusView(APIView):
    permission_classes = _perm_classes()

    def get(self, request):
        return Response({'configured': encryption_ready()})


class OverviewView(APIView):
    permission_classes = _perm_classes()

    def get(self, request):
        providers = list(AIProvider.objects.all())
        models = AIModel.objects.all()
        connected = sum(1 for p in providers
                        if p.credentials.filter(revoked_at__isnull=True).exists())
        return Response({
            'providers_available': len(providers),
            'paid': sum(1 for p in providers if p.group in ('paid', 'cloud')),
            'open_self': sum(1 for p in providers if p.group in ('open', 'self')),
            'connected': connected,
            'models_total': models.count(),
            'closed': models.filter(weights='closed').count(),
            'open_weight': models.filter(weights='open').count(),
            'enabled_count': models.filter(enabled=True).count(),
            'modules_routed': sum(1 for r in AIRoute.objects.all() if any(r.chain or [])),
            'modules_total': AIRoute.objects.count(),
        })
