"""
Usage Tracking Models
Lightweight per-request usage logging for internal analytics.
Soft-coded discipline map keeps all label changes in one place.
"""
from django.db import models
from django.conf import settings
from django.utils import timezone


# ---------------------------------------------------------------------------
# Soft-coded: maps API path segment → human-readable discipline label
# Add or rename entries here without touching middleware or views.
# ---------------------------------------------------------------------------
DISCIPLINE_MAP = [
    ('pid',                   'Process (P&ID)'),
    ('pfd',                   'Digitization (PFD)'),
    ('process-datasheet',     'Process Datasheet'),
    ('electrical-datasheet',  'Electrical Datasheet'),
    ('crs',                   'CRS Documents'),
    ('designiq',              'DesignIQ'),
    ('finance',               'Finance'),
    ('procurement',           'Procurement'),
    ('qhse',                  'QHSE'),
    ('projects',              'Project Control'),
    ('sales',                 'Sales'),
    ('rbac',                  'Admin / RBAC'),
    ('users',                 'User Management'),
    ('notifications',         'Notifications'),
]


def classify_discipline(path: str):
    """
    Map a request path to (discipline_key, discipline_label).
    Strips /api/v1/ prefix then matches the first path segment.
    Returns ('other', 'Other') when nothing matches.
    """
    # Strip leading slash and /api/v1/ prefix
    stripped = path.lstrip('/')
    for prefix in ('api/v1/', 'api/'):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break

    for key, label in DISCIPLINE_MAP:
        if stripped.startswith(key):
            return key, label
    return 'other', 'Other'


class UsageLog(models.Model):
    """One lightweight row per authenticated API request."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='usage_logs',
    )
    user_email       = models.EmailField(blank=True, db_index=True)
    user_full_name   = models.CharField(max_length=255, blank=True)

    discipline_key   = models.CharField(max_length=80, db_index=True)
    discipline_label = models.CharField(max_length=120)

    request_path     = models.CharField(max_length=500)
    request_method   = models.CharField(max_length=10, default='GET')
    response_status  = models.SmallIntegerField(default=200)
    response_time_ms = models.IntegerField(null=True, blank=True)
    success          = models.BooleanField(default=True)

    timestamp        = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'usage_log'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp', 'discipline_key']),
            models.Index(fields=['user_email', '-timestamp']),
        ]

    def __str__(self):
        return (
            f"{self.user_email} → {self.discipline_label} "
            f"[{self.response_status}] ({self.timestamp:%Y-%m-%d %H:%M})"
        )
