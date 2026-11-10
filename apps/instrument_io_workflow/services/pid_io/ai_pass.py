"""BYOK AI review as part of the MAIN extraction (§7.5 extended).

When the extraction runs with use_ai and the user has a stored AI key, the
low-confidence loops (serviceConfidence < 0.6, field I/O or soft) are
reviewed by the AI right after the drawing rules finish — one call per
loop, a few workers in parallel, failures never fail the extraction.

Every suggestion stays 'suggested' until the user confirms it in the app;
nothing is auto-applied to the IO rows.
"""
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.db import close_old_connections

logger = logging.getLogger(__name__)

AI_CONFIDENCE_FLOOR = 0.6          # loops at/above this stay rules-only
AI_PASS_WORKERS = 4                # parallel BYOK calls
AI_PASS_TIMEOUT_S = 60
_MODEL_BY_PROVIDER = {'claude': 'claude-haiku-4-5', 'openai': 'gpt-4o-mini'}


class NoAiKeyError(RuntimeError):
    """The extraction asked for the AI pass but the user has no BYOK key."""


def user_ai_key(user):
    """The user's own BYOK key (stored per user, shared across features).
    Returns (provider, raw_key) or None."""
    if not user:
        return None
    from apps.electrical_comparison.models import SldUserAIKey
    from apps.electrical_comparison.services import sld_crypto
    rec = SldUserAIKey.objects.filter(user=user).first()
    if not rec or not sld_crypto.encryption_ready():
        return None
    return rec.provider, sld_crypto.decrypt_api_key(rec.encrypted_key)


def ai_candidate_loops(extraction):
    """Loop keys the rules could not settle (mirrors engine.ai_loops)."""
    loops = set()
    for t in extraction.tags_payload or []:
        if t.get('derived'):
            continue
        if (t.get('serviceConfidence') or 0) < AI_CONFIDENCE_FLOOR \
                and t.get('ioClass') in ('AI', 'AO', 'DI', 'DO', 'soft'):
            loops.add(t.get('loopKey') or '')
    loops.discard('')
    return sorted(loops)


def _call_provider(prompt, provider, api_key):
    """One text completion with an explicit BYOK key (never logged)."""
    model = _MODEL_BY_PROVIDER.get(provider)
    if provider == 'openai':
        import openai
        client = openai.OpenAI(api_key=api_key, timeout=AI_PASS_TIMEOUT_S)
        resp = client.chat.completions.create(
            model=model, max_tokens=512,
            messages=[{'role': 'user', 'content': prompt}])
        return resp.choices[0].message.content or ''
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, timeout=AI_PASS_TIMEOUT_S)
    resp = client.messages.create(model=model or 'claude-haiku-4-5', max_tokens=512,
                                  messages=[{'role': 'user', 'content': prompt}])
    return ''.join(b.text for b in resp.content if getattr(b, 'type', None) == 'text')


def suggest_for_loop(extraction, loop_key, provider=None, api_key=None):
    """One BYOK call for one loop. Returns the suggestion dict.
    With provider+api_key the user's own key is used; without them the
    central AI registry is tried. Raises on provider errors."""
    members = [t for t in extraction.tags_payload
               if t.get('loopKey') == loop_key]
    context = {
        'loop': loop_key,
        'members': [{'tag': t['tag'], 'system': t['system'], 'ioType': t['ioType'],
                     'line': t.get('line') or '', 'equipment': t.get('equipment') or '',
                     'serviceEvidence': t.get('serviceEvidence') or ''}
                    for t in members],
    }
    prompt = (
        'You are an instrument engineer completing an IO list. Given the loop members '
        'and rule-based evidence from a P&ID, propose the SERVICE text and LINE NUMBER. '
        'Return ONLY JSON: {"service": str, "line": str, "reason": str}.\n'
        f'Loop data: {json.dumps(context)}')
    if api_key:
        text = _call_provider(prompt, provider or 'claude', api_key)
    else:
        from apps.core.ai_consumer_clients import lazy_provider_client
        client = lazy_provider_client('anthropic', __import__('anthropic').Anthropic,
                                      api_key=lambda: '', timeout=AI_PASS_TIMEOUT_S)
        resp = client.messages.create(model='claude-haiku-4-5', max_tokens=512,
                                      messages=[{'role': 'user', 'content': prompt}])
        text = ''.join(b.text for b in resp.content if getattr(b, 'type', None) == 'text')
    return json.loads(text[text.find('{'):text.rfind('}') + 1] or '{}')


def run_ai_pass(extraction, progress=None):
    """Review every low-confidence loop with the user's key. Stores one
    IOListAISuggestion per loop (status 'suggested'). Returns
    {'suggested': n, 'failed': n, 'total': n}."""
    from ...models_extraction import IOListAISuggestion

    cred = user_ai_key(extraction.created_by)
    if not cred:
        raise NoAiKeyError('No AI key stored — connect your key in Stage 4.')
    provider, api_key = cred
    loops = ai_candidate_loops(extraction)
    total = len(loops)
    done = {'n': 0, 'ok': 0, 'fail': 0}
    lock = threading.Lock()

    def _progress():
        if progress:
            progress({'done': done['n'], 'total': total,
                      'suggested': done['ok'], 'failed': done['fail']})

    def _one(loop_key):
        close_old_connections()
        try:
            suggestion = suggest_for_loop(extraction, loop_key,
                                          provider=provider, api_key=api_key)
            IOListAISuggestion.objects.update_or_create(
                extraction=extraction, loop_key=loop_key,
                defaults={'suggestion': suggestion,
                          'created_by': extraction.created_by})
            with lock:
                done['n'] += 1
                done['ok'] += 1
                if done['n'] % 5 == 0 or done['n'] == total:
                    _progress()
        except Exception as exc:
            logger.warning('[io-list AI pass] loop %s failed: %s', loop_key, exc)
            with lock:
                done['n'] += 1
                done['fail'] += 1
                if done['n'] % 5 == 0 or done['n'] == total:
                    _progress()
        finally:
            close_old_connections()

    _progress()
    with ThreadPoolExecutor(max_workers=AI_PASS_WORKERS) as pool:
        futures = [pool.submit(_one, lk) for lk in loops]
        for f in as_completed(futures):
            f.result()      # _one never raises; keeps the pool honest
    return {'suggested': done['ok'], 'failed': done['fail'], 'total': total}
