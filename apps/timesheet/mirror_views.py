"""
Mirror ingest API — receives biometric punch events from the office-side
sync agent (`scripts/timesheet_mirror_sync.py`).

POST  /api/v1/timesheet/mirror/ingest/
Header:  X-Timesheet-Mirror-Key: <shared secret>
Body:    {"events": [
    {
      "source_event_id": "deterministic-hash",
      "employee_code":   "1234",
      "employee_name":   "John Smith",
      "employee_email":  "" | "j@x.com",
      "department":      "" | "Engineering",
      "event_time":      "2026-06-10T08:31:42",
      "event_type":      "IN" | "OUT"
    }, ...
]}

Idempotent: re-uploading the same `source_event_id` updates instead of
duplicating. Designed for at-least-once delivery from the agent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import config as ts_config
from .models import TimesheetEvent

logger = logging.getLogger(__name__)


def _parse_event_time(raw: Any):
    if isinstance(raw, datetime):
        return raw if timezone.is_aware(raw) else timezone.make_aware(raw, dt_timezone.utc)
    if not raw:
        return None
    parsed = parse_datetime(str(raw))
    if not parsed:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed, dt_timezone.utc)


@api_view(['POST'])
@authentication_classes([])  # API-key auth only — no JWT required
@permission_classes([AllowAny])
def ingest_events(request):
    """Accept a batch of biometric events and upsert by `source_event_id`."""
    key_header = request.META.get('HTTP_X_TIMESHEET_MIRROR_KEY', '')
    expected = ts_config.MIRROR_API_KEY or ''
    if not expected:
        return Response({'error': 'mirror ingest disabled (no key configured)'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    if key_header != expected:
        return Response({'error': 'invalid mirror key'}, status=status.HTTP_403_FORBIDDEN)

    payload = request.data if isinstance(request.data, dict) else {}
    events = payload.get('events') or []
    if not isinstance(events, list):
        return Response({'error': 'events must be a list'}, status=status.HTTP_400_BAD_REQUEST)

    max_batch = ts_config.MIRROR_INGEST_MAX_BATCH
    if len(events) > max_batch:
        return Response(
            {'error': f'batch too large ({len(events)} > {max_batch})'},
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    inserted = 0
    updated = 0
    skipped = 0
    errors: list[dict] = []

    for idx, ev in enumerate(events):
        try:
            sid = str(ev.get('source_event_id') or '').strip()
            emp_code = str(ev.get('employee_code') or '').strip()
            event_time = _parse_event_time(ev.get('event_time'))
            event_type = str(ev.get('event_type') or '').strip().upper()
            if not (sid and emp_code and event_time and event_type in ('IN', 'OUT')):
                skipped += 1
                continue
            defaults = {
                'employee_code':   emp_code,
                'employee_name':   str(ev.get('employee_name') or '').strip()[:255],
                'employee_email':  str(ev.get('employee_email') or '').strip()[:255],
                'department':      str(ev.get('department') or '').strip()[:255],
                'event_time':      event_time,
                'event_type':      event_type,
            }
            _, created = TimesheetEvent.objects.update_or_create(
                source_event_id=sid, defaults=defaults,
            )
            if created:
                inserted += 1
            else:
                updated += 1
        except Exception as exc:  # pragma: no cover — never let one bad row kill the batch
            logger.warning('[timesheet.ingest] row %s failed: %s', idx, exc)
            errors.append({'index': idx, 'error': str(exc)})

    logger.info(
        '[timesheet.ingest] %s in, %s updated, %s skipped, %s errors',
        inserted, updated, skipped, len(errors),
    )
    return Response({
        'received': len(events),
        'inserted': inserted,
        'updated':  updated,
        'skipped':  skipped,
        'errors':   errors,
    })
