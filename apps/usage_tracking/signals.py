"""
Usage Tracking — Signals
========================
Fans out a throttled realtime snapshot whenever a new UsageLog row is
written by the middleware. The throttle (Django cache lock in
`realtime.broadcast_snapshot`) means a request burst still produces at
most one broadcast per `USAGE_BROADCAST_THROTTLE_SECS`.

This file is wired up in `apps.py → ready()`.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UsageLog
from .realtime import broadcast_snapshot


@receiver(post_save, sender=UsageLog, dispatch_uid='usage_log_realtime_broadcast')
def _on_usage_log_saved(sender, instance, created, **kwargs):
    if not created:
        return
    broadcast_snapshot()
