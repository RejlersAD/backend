"""Celery tasks for P&ID Checker V2 long-running vision extractions."""
from __future__ import annotations

import base64
import logging

from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger(__name__)

# ─── Soft-coded task budgets (mirror pid_analysis pattern) ───────────
EXTRACT_TAG_SOFT_LIMIT_S = 3000        # 50 min — SoftTimeLimitExceeded raised
EXTRACT_TAG_HARD_LIMIT_S = 3600        # 60 min — SIGKILL
EXTRACT_TAG_RESULT_TTL_S = 14400       # 4 hours in Redis

EXTRACT_INSTRUMENT_CACHE_KEY_FMT = 'pidv2_extract_instrument:{job_id}'
EXTRACT_EQUIPMENT_CACHE_KEY_FMT  = 'pidv2_extract_equipment:{job_id}'


def _set_progress(cache_key: str, pct: int, msg: str) -> None:
    cache.set(
        cache_key,
        {'status': 'processing', 'progress': pct, 'message': msg},
        EXTRACT_TAG_RESULT_TTL_S,
    )


@shared_task(
    bind=True,
    name='pid_checker_v2.run_extract_instrument_tags_task',
    max_retries=0,
    soft_time_limit=EXTRACT_TAG_SOFT_LIMIT_S,
    time_limit=EXTRACT_TAG_HARD_LIMIT_S,
)
def run_extract_instrument_tags_task(
    self,
    job_id: str,
    pdf_b64: str,
    filename: str,
    provider: str,
    api_key: str,
    user_id: int | None = None,
):
    """Run instrument-tag vision extraction in the background.

    Frontend polls GET /api/v1/pid_checker_v2/extract-instrument-tags/status/<job_id>/
    """
    from .services.instrument_vision_extractor import extract_instrument_tags_via_vision
    from .views import _persist_token_usage  # local import to avoid app-load cycles

    cache_key = EXTRACT_INSTRUMENT_CACHE_KEY_FMT.format(job_id=job_id)
    logger.info('[PIDV2InstrExtract] start job=%s file=%s provider=%s',
                job_id, filename, provider)
    _set_progress(cache_key, 5, 'Initialising instrument vision extraction…')

    try:
        pdf_bytes = base64.b64decode(pdf_b64)
        _set_progress(cache_key, 20, 'Scanning P&ID tiles for instrument tags…')

        result = extract_instrument_tags_via_vision(pdf_bytes, provider, api_key)

        _set_progress(cache_key, 90, 'Persisting token usage…')
        if user_id is not None:
            try:
                from django.contrib.auth import get_user_model
                user = get_user_model().objects.filter(pk=user_id).first()
                if user is not None:
                    _persist_token_usage(
                        user=user,
                        feature='instrument_extraction',
                        usage=result.get('token_usage') or {},
                    )
            except Exception as exc:  # never fail the job because of accounting
                logger.warning('[PIDV2InstrExtract] usage persist failed: %s', exc)

        payload = {
            'status':     'completed',
            'filename':   filename,
            'provider':   result['provider'],
            'model':      result['model'],
            'tags':       result['tags'],
            'call_count': result['call_count'],
            'token_usage': result.get('token_usage'),
        }
        cache.set(cache_key, payload, EXTRACT_TAG_RESULT_TTL_S)
        logger.info('[PIDV2InstrExtract] done job=%s tags=%d',
                    job_id, len(result['tags']))

    except Exception as exc:
        logger.error('[PIDV2InstrExtract] failed job=%s error=%s', job_id, exc, exc_info=True)
        msg = str(exc)
        if 'overloaded' in msg.lower() or '529' in msg:
            friendly = (f"{provider.title()} vision API is temporarily overloaded "
                        "after several automatic retries. Please try again in a "
                        "minute or switch provider.")
        else:
            friendly = f'vision extraction failed: {exc}'
        cache.set(
            cache_key,
            {'status': 'failed', 'error': friendly},
            EXTRACT_TAG_RESULT_TTL_S,
        )
        # Do NOT re-raise — polled result carries the error.
