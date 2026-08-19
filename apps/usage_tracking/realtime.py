"""
Usage Tracking — Realtime Snapshot Helper
=========================================

Pure read-only helpers shared by:
  * `UsageStreamConsumer` (WebSocket initial snapshot + periodic ticks)
  * `signals.py` (post-save broadcast)

All thresholds, group names and intervals are SOFT-CODED at the top so
they can be tuned via env vars without touching consumer / dashboard code.

Design notes
------------
* No DB writes here — purely aggregation queries.
* Heaviest query (top features) is bounded to ``ACTIVE_WINDOW_MINUTES`` so
  it stays under a few ms even with millions of UsageLog rows.
* Snapshot shape is intentionally a superset of the polling
  `/usage/overview/` payload so the frontend can merge them
  interchangeably.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Dict, List

from django.db.models import Avg, Count, Q
from django.utils import timezone

# ── Soft-coded constants (env-overridable) ───────────────────────────────
USAGE_WS_GROUP            = os.getenv('USAGE_WS_GROUP',       'usage_stream')
USAGE_WS_ENABLED          = os.getenv('USAGE_WS_ENABLED',     '1') == '1'
USAGE_TICK_INTERVAL_SECS  = int(os.getenv('USAGE_TICK_INTERVAL_SECS', '5'))
USAGE_BROADCAST_THROTTLE_SECS = int(os.getenv('USAGE_BROADCAST_THROTTLE_SECS', '2'))
ACTIVE_WINDOW_MINUTES     = int(os.getenv('USAGE_ACTIVE_WINDOW_MINUTES', '15'))
TOP_FEATURES_LIMIT        = int(os.getenv('USAGE_TOP_FEATURES_LIMIT', '5'))
TOP_USERS_LIMIT           = int(os.getenv('USAGE_TOP_USERS_LIMIT', '5'))
RECENT_EVENTS_LIMIT       = int(os.getenv('USAGE_RECENT_EVENTS_LIMIT', '10'))
TODAY_TREND_BUCKETS       = int(os.getenv('USAGE_TODAY_TREND_BUCKETS', '24'))


def build_snapshot() -> Dict[str, Any]:
    """
    Build the full realtime snapshot consumed by the dashboard's live tick.

    Returns a JSON-serialisable dict; never raises. On any DB hiccup it
    returns a minimal shape so the WS layer keeps streaming.
    """
    try:
        # Late import — keeps this module importable from signals.py even
        # before the app registry is fully loaded.
        from .models import UsageLog
    except Exception:
        return _empty_snapshot()

    now           = timezone.now()
    today_start   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active_cutoff = now - timedelta(minutes=ACTIVE_WINDOW_MINUTES)
    minute_cutoff = now - timedelta(minutes=1)

    try:
        today_qs   = UsageLog.objects.filter(timestamp__gte=today_start)
        active_qs  = UsageLog.objects.filter(timestamp__gte=active_cutoff)
        minute_qs  = UsageLog.objects.filter(timestamp__gte=minute_cutoff)

        today_total   = today_qs.count()
        today_users   = today_qs.values('user_email').distinct().count()
        active_users  = active_qs.values('user_email').distinct().count()
        per_minute    = minute_qs.count()
        avg_resp      = today_qs.aggregate(a=Avg('response_time_ms'))['a'] or 0
        success_today = today_qs.filter(success=True).count()

        success_rate = round(success_today / max(today_total, 1) * 100, 1)

        top_features = list(
            active_qs.values('discipline_key', 'discipline_label')
                     .annotate(count=Count('id'))
                     .order_by('-count')[:TOP_FEATURES_LIMIT]
        )

        top_users = list(
            active_qs.values('user_email', 'user_full_name')
                     .annotate(count=Count('id'))
                     .order_by('-count')[:TOP_USERS_LIMIT]
        )

        recent_events = list(
            UsageLog.objects.values(
                'user_email', 'user_full_name', 'discipline_label',
                'request_path', 'response_status', 'response_time_ms',
                'timestamp',
            ).order_by('-timestamp')[:RECENT_EVENTS_LIMIT]
        )
        # Normalise timestamps for JSON
        for ev in recent_events:
            if ev.get('timestamp'):
                ev['timestamp'] = ev['timestamp'].isoformat()

        return {
            'type':            'usage_tick',
            'generated_at':    now.isoformat(),
            'window_minutes':  ACTIVE_WINDOW_MINUTES,
            'kpis': {
                'today_requests':  today_total,
                'today_users':     today_users,
                'active_users':    active_users,
                'requests_per_min': per_minute,
                'avg_response_ms': round(avg_resp),
                'success_rate':    success_rate,
            },
            'top_features':  top_features,
            'top_users':     top_users,
            'recent_events': recent_events,
        }
    except Exception:
        return _empty_snapshot()


def _empty_snapshot() -> Dict[str, Any]:
    return {
        'type':           'usage_tick',
        'generated_at':   timezone.now().isoformat(),
        'window_minutes': ACTIVE_WINDOW_MINUTES,
        'kpis': {
            'today_requests':   0,
            'today_users':      0,
            'active_users':     0,
            'requests_per_min': 0,
            'avg_response_ms':  0,
            'success_rate':     100.0,
        },
        'top_features':  [],
        'top_users':     [],
        'recent_events': [],
    }


def broadcast_snapshot() -> None:
    """
    Push a fresh snapshot to every connected dashboard.

    Throttled via Django cache so a burst of 100 requests/sec results in
    at most one broadcast per ``USAGE_BROADCAST_THROTTLE_SECS``. Safe to
    call from synchronous code (signals, middleware) — uses
    ``async_to_sync``.
    """
    if not USAGE_WS_ENABLED:
        return

    try:
        from django.core.cache import cache
        lock_key = f'usage_ws:lock:{USAGE_WS_GROUP}'
        # ``cache.add`` returns False if the key already exists.
        if not cache.add(lock_key, '1', timeout=USAGE_BROADCAST_THROTTLE_SECS):
            return

        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return

        snapshot = build_snapshot()
        async_to_sync(layer.group_send)(
            USAGE_WS_GROUP,
            {'type': 'usage.tick', 'payload': snapshot},
        )
    except Exception:
        # Realtime is a luxury — never let it break the request flow.
        pass
