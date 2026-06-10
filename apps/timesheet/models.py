"""
Time Sheet — Postgres mirror of biometric attendance events.

Filled by the office-side agent (`scripts/timesheet_mirror_sync.py`) which
reads from the on-prem SQL Server and POSTs batches to the ingest endpoint.
Read by `mirror_services.py` when `TIMESHEET_DATA_SOURCE=mirror` (Railway/
production), so the API serves attendance data without ever needing a route
to the office LAN.

`source_event_id` is a deterministic hash of (employee_code, event_time,
event_type) — guarantees idempotent upserts even if the agent re-uploads.
"""
from django.db import models


class TimesheetEvent(models.Model):
    EVENT_IN = 'IN'
    EVENT_OUT = 'OUT'
    EVENT_CHOICES = [(EVENT_IN, 'IN'), (EVENT_OUT, 'OUT')]

    source_event_id = models.CharField(max_length=128, unique=True, db_index=True)
    employee_code = models.CharField(max_length=64, db_index=True)
    employee_name = models.CharField(max_length=255, blank=True, default='')
    employee_email = models.CharField(max_length=255, blank=True, default='')
    department = models.CharField(max_length=255, blank=True, default='')
    event_time = models.DateTimeField(db_index=True)
    event_type = models.CharField(max_length=8, choices=EVENT_CHOICES, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-event_time']
        indexes = [
            models.Index(fields=['employee_code', '-event_time']),
            models.Index(fields=['-event_time', 'event_type']),
        ]

    def __str__(self):
        return f'{self.employee_code} {self.event_type} @ {self.event_time:%Y-%m-%d %H:%M}'
