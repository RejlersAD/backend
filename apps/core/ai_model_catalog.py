"""RADAI Intelligence model choice and usage totals.

- latest_chat_models(): the newest chat models each configured provider offers (cached), so a
  project owner can choose which ones members may use.
- general_chat_model(): general chat always runs on Gemini Flash, the low-cost model.
- usage_totals(): tokens and estimated cost of saved answers, in US dollars and UAE dirhams.
"""
import logging
import re
from decimal import Decimal

from django.core.cache import cache
from django.db.models import DecimalField, IntegerField, Sum
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce

logger = logging.getLogger(__name__)

CLOUD_PROVIDERS = ('anthropic', 'openai', 'gemini')
PROVIDER_LABELS = {'anthropic': 'Claude', 'openai': 'OpenAI', 'gemini': 'Gemini'}
CATALOG_CACHE_SECONDS = 3600
PER_PROVIDER = 8
# The UAE dirham is pegged to the US dollar at 3.6725.
AED_PER_USD = Decimal('3.6725')
GENERAL_CHAT_FALLBACK_MODEL = 'gemini-2.5-flash'


def model_label(provider, model_id):
    return f'{PROVIDER_LABELS.get(provider, provider)} ({model_id})'


def general_chat_model(gemini_row):
    """The Gemini Flash row general chat uses: the configured Gemini model if it is a Flash model."""
    model_id = gemini_row['model_id'] if 'flash' in gemini_row['model_id'] else GENERAL_CHAT_FALLBACK_MODEL
    return {'provider': 'gemini', 'model_id': model_id, 'label': f'Gemini Flash ({model_id})'}


# --- Latest models -------------------------------------------------------------------------

_OPENAI_CHAT = re.compile(r'^(gpt-[45]|o[134]\b|o[134]-)')
_OPENAI_SKIP = ('audio', 'realtime', 'transcribe', 'tts', 'search', 'image', 'embedding', 'instruct',
                'codex', 'moderation', 'computer-use', 'deep-research')
_DATED = re.compile(r'-\d{4}-\d{2}-\d{2}$|-\d{8}$')
_GEMINI_SKIP = ('embedding', 'image', 'tts', 'live', 'audio', 'aqa', 'learnlm', 'robotics', 'computer-use', 'native')


def _openai_models():
    import openai
    from .ai_consumer_clients import lazy_provider_client
    client = lazy_provider_client('openai', openai.OpenAI, timeout=20)
    rows = [row for row in client.models.list()
            if _OPENAI_CHAT.match(row.id) and not _DATED.search(row.id) and not any(word in row.id for word in _OPENAI_SKIP)]
    rows.sort(key=lambda row: getattr(row, 'created', 0) or 0, reverse=True)
    return [row.id for row in rows]


def _anthropic_models():
    import anthropic
    from .ai_credentials import resolve_provider_credential
    key, _metadata = resolve_provider_credential('anthropic')
    client = anthropic.Anthropic(api_key=key, timeout=20)
    try:
        rows = list(client.models.list(limit=100).data)
    finally:
        client.close()
    rows.sort(key=lambda row: str(getattr(row, 'created_at', '') or ''), reverse=True)
    return [row.id for row in rows]


def _gemini_version(name):
    found = re.search(r'gemini-(\d+(?:\.\d+)?)', name)
    return float(found.group(1)) if found else 0.0


def _gemini_models():
    from google import genai
    from .ai_consumer_clients import lazy_provider_client
    client = lazy_provider_client('gemini', genai.Client, http_options={'api_version': 'v1beta'})
    names = []
    for row in client.models.list(config={'page_size': 200}):
        name = (getattr(row, 'name', '') or '').removeprefix('models/')
        actions = getattr(row, 'supported_actions', None) or []
        if (name.startswith('gemini-') and 'generateContent' in actions
                and not any(word in name for word in _GEMINI_SKIP) and not re.search(r'-\d{3}$', name)):
            names.append(name)
    # Newest version first; stable before preview within a version.
    names.sort(key=lambda name: (-_gemini_version(name), 'preview' in name or 'exp' in name, name))
    return names


_LISTERS = {'openai': _openai_models, 'anthropic': _anthropic_models, 'gemini': _gemini_models}


def latest_chat_models(ready_rows):
    """Newest chat models per ready provider, each provider's configured model kept first.

    `ready_rows` are the organization's configured chat models (one per provider). A provider whose
    model list can't be read still offers its configured model.
    """
    catalog = []
    for configured in ready_rows:
        provider = configured['provider']
        if provider not in CLOUD_PROVIDERS:
            continue
        key = f'radai:chat-model-catalog:{provider}'
        ids = cache.get(key)
        if ids is None:
            try:
                ids = _LISTERS[provider]()[:PER_PROVIDER * 2]
                cache.set(key, ids, CATALOG_CACHE_SECONDS)
            except Exception as exc:  # noqa: BLE001 - a provider outage must not hide the others
                logger.warning('RADAI model catalog: %s list unavailable (%s)', provider, type(exc).__name__)
                ids = []
        ordered = [configured['model_id']] + [model_id for model_id in ids if model_id != configured['model_id']]
        for index, model_id in enumerate(ordered[:PER_PROVIDER]):
            catalog.append({'provider': provider, 'model_id': model_id, 'label': model_label(provider, model_id),
                            'configured': index == 0})
    return catalog


# --- Usage totals --------------------------------------------------------------------------

def _money(value):
    usd = Decimal(value or 0)
    return {'usd': str(usd.quantize(Decimal('0.0001'))), 'aed': str((usd * AED_PER_USD).quantize(Decimal('0.0001')))}


def usage_totals(messages):
    """Sum token counts and cost of saved assistant answers (cost is an estimate from list prices)."""
    def number(key, field):
        return Coalesce(Sum(Cast(KeyTextTransform(key, 'token_usage'), field)), 0, output_field=field)
    money = DecimalField(max_digits=20, decimal_places=6)
    totals = messages.filter(role='assistant').aggregate(
        input_tokens=number('input_tokens', IntegerField()),
        output_tokens=number('output_tokens', IntegerField()),
        cost=Coalesce(Sum(Cast(KeyTextTransform('cost_usd', 'token_usage'), money)), Decimal('0'), output_field=money),
    )
    return {'input_tokens': totals['input_tokens'], 'output_tokens': totals['output_tokens'],
            'total_tokens': totals['input_tokens'] + totals['output_tokens'],
            'answers': messages.filter(role='assistant').count(), 'cost': _money(totals['cost'])}
