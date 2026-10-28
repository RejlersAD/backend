"""Celery task: synchronise the AI model catalogue from every enabled provider."""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .ai_hub_adapters import AdapterError, NotConfigured, list_models
from .ai_hub_capabilities import describe_model
from .ai_hub_models import AIModel, AIProvider, AIRoute, AISyncRun, AIHubSettings

logger = logging.getLogger(__name__)


def _family_parts(model_id):
    tail = model_id.rsplit('/', 1)[-1]
    maker = model_id.rsplit('/', 1)[0] if '/' in model_id else ''
    pieces = tail.split('-')
    return maker, pieces[0] if pieces else tail, tail


def _affected_tasks(provider_id, model_id):
    needle = f'{provider_id}:{model_id}'
    return [route.task_id for route in AIRoute.objects.all() if needle in (route.chain or [])]


@sensitive_variables()
def _resolve_secret(provider):
    """Org credential first, then server env config (settings.AI_HUB_SERVER_KEYS)."""
    from .ai_hub_runtime import _credential_for
    return _credential_for(provider.id)


def _sync_provider(provider, run, settings_row):
    per_source = {'provider': provider.id, 'result': 'ok', 'count': 0}
    changes = []
    resolved = _resolve_secret(provider)
    secret = base_url = None
    extra = {}
    if resolved:
        secret, base_url, extra, _scope = resolved
    try:
        rows = list_models(provider, secret=secret, base_url=base_url or None, extra=extra)
    except NotConfigured:
        per_source.update(result='skipped', error='not_configured')
        return per_source, changes
    except AdapterError as exc:
        per_source.update(result='error', error=str(exc))
        return per_source, changes
    except Exception:
        logger.exception('ai_hub sync failed for provider %s', provider.id)
        per_source.update(result='error', error='unexpected')
        return per_source, changes

    now = timezone.now()
    seen_ids = set()
    for row in rows:
        model_id = row.get('model_id')
        if not model_id:
            continue
        seen_ids.add(model_id)
        described = describe_model(model_id)
        capabilities = row.get('capabilities') or described['capabilities']
        weights = row.get('weights') or described['weights']
        maker, family, variant = _family_parts(model_id)
        model = AIModel.objects.filter(provider=provider, model_id=model_id).first()
        if model is None:
            model = AIModel.objects.create(
                provider=provider, model_id=model_id, maker=maker, family=family,
                variant=variant, capabilities=capabilities, weights=weights,
                licence=described['licence'],
                context_tokens=row.get('context_tokens'),
                price_in_per_m=row.get('price_in_per_m'),
                price_out_per_m=row.get('price_out_per_m'),
                status=row.get('status') if row.get('status') in ('ga', 'preview') else 'new',
                status_note=row.get('status_note', ''),
                enabled=False if settings_row.new_models_disabled else bool(row.get('status') == 'ga'),
                last_seen=now, consecutive_misses=0,
            )
            changes.append({'kind': 'new', 'model_id': model_id, 'provider': provider.id,
                            'effect_on_routing': []})
        else:
            dirty = {'last_seen': now, 'consecutive_misses': 0}
            if model.status == 'shut_down':
                dirty['status'] = 'ga'
                changes.append({'kind': 'ga', 'model_id': model_id, 'provider': provider.id,
                                'effect_on_routing': _affected_tasks(provider.id, model_id)})
            if row.get('status') == 'deprecated' and model.status != 'deprecated':
                dirty['status'] = 'deprecated'
                dirty['status_note'] = row.get('status_note') or 'Provider reports deprecation'
                changes.append({'kind': 'deprecated', 'model_id': model_id, 'provider': provider.id,
                                'effect_on_routing': _affected_tasks(provider.id, model_id)})
            for field, value in dirty.items():
                setattr(model, field, value)
            model.save(update_fields=list(dirty))
    # lifecycle: missing 2 consecutive syncs → shut_down
    for model in AIModel.objects.filter(provider=provider).exclude(model_id__in=seen_ids):
        model.consecutive_misses += 1
        update = ['consecutive_misses']
        if model.consecutive_misses >= 2 and model.status != 'shut_down':
            model.status = 'shut_down'
            update.append('status')
            changes.append({'kind': 'shut_down', 'model_id': model.model_id,
                            'provider': provider.id,
                            'effect_on_routing': _affected_tasks(provider.id, model.model_id)})
        model.save(update_fields=update)

    per_source['count'] = len(seen_ids)
    return per_source, changes


def _enrich_from_openrouter(changes):
    """Optional catalogue enrichment (pricing/context) via OpenRouter meta."""
    provider = AIProvider.objects.filter(id='openrouter', enabled=True).first()
    if provider is None:
        return
    resolved = _resolve_secret(provider)
    if not resolved:
        return
    secret, base_url, extra, _ = resolved
    try:
        rows = list_models(provider, secret=secret, base_url=base_url or None, extra=extra)
    except Exception:
        return
    by_id = {r['model_id']: r for r in rows}
    for model in AIModel.objects.exclude(provider_id='openrouter'):
        meta = by_id.get(model.model_id) or by_id.get(f'{model.provider_id}/{model.model_id}')
        if meta and model.source != 'openrouter_meta':
            model.source = 'openrouter_meta'
            model.save(update_fields=['source'])


@shared_task(name='core.sync_ai_catalogue', bind=True)
def sync_ai_catalogue(self, run_id=None):
    run = None
    if run_id:
        run = AISyncRun.objects.filter(id=run_id).first()
    if run is None:
        run = AISyncRun.objects.create(status='running')

    settings_row = AIHubSettings.get()
    per_source, all_changes = [], []
    failures = 0
    for provider in AIProvider.objects.filter(enabled=True):
        source, changes = _sync_provider(provider, run, settings_row)
        per_source.append(source)
        all_changes.extend(changes)
        if source['result'] == 'error':
            failures += 1

    if settings_row.enrich_openrouter:
        _enrich_from_openrouter(all_changes)

    run.per_source = per_source
    run.changes = all_changes
    run.finished_at = timezone.now()
    total = AIProvider.objects.filter(enabled=True).count()
    run.status = 'ok' if failures == 0 else ('partial' if failures < total else 'failed')
    run.save(update_fields=['per_source', 'changes', 'finished_at', 'status'])

    from .ai_hub_adapters import audit
    audit(None, 'sync', detail={'run_id': str(run.id), 'status': run.status,
                                'changes': len(all_changes)})
    return {'run_id': str(run.id), 'status': run.status}
