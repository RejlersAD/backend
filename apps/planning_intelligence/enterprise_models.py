"""Integration delivery, export audit, and enterprise retention records."""
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

from .models import PlanningProject
from .schedule_models import ScheduleVersion


class IntegrationEndpoint(BaseModel):
    AUTH_CHOICES = [('none', 'None'), ('bearer', 'Bearer Token'), ('hmac_sha256', 'HMAC SHA-256')]
    FORMAT_CHOICES = [('json', 'JSON'), ('xer', 'Primavera XER'), ('csv', 'CSV')]

    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='integration_endpoints')
    name = models.CharField(max_length=120)
    target_url = models.URLField(max_length=1000)
    export_format = models.CharField(max_length=12, choices=FORMAT_CHOICES, default='json')
    auth_type = models.CharField(max_length=16, choices=AUTH_CHOICES, default='hmac_sha256')
    secret_encrypted = models.TextField(blank=True)
    event_types = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    timeout_seconds = models.PositiveSmallIntegerField(default=15)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_integrations_created',
    )

    class Meta:
        ordering = ['name']
        unique_together = [('project', 'name')]


class IntegrationDelivery(BaseModel):
    STATUS_CHOICES = [
        ('queued', 'Queued'), ('delivering', 'Delivering'),
        ('succeeded', 'Succeeded'), ('failed', 'Failed'),
    ]

    endpoint = models.ForeignKey(IntegrationEndpoint, on_delete=models.CASCADE, related_name='deliveries')
    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='integration_deliveries')
    event_type = models.CharField(max_length=64, default='schedule.published')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='queued', db_index=True)
    request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    idempotency_key = models.CharField(max_length=255)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    payload_sha256 = models.CharField(max_length=64, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_excerpt = models.CharField(max_length=1000, blank=True)
    error_message = models.CharField(max_length=1000, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_integration_deliveries_requested',
    )

    class Meta:
        ordering = ['-created_at']
        unique_together = [('endpoint', 'idempotency_key')]
        indexes = [models.Index(fields=['endpoint', '-created_at']), models.Index(fields=['status', '-created_at'])]


class ScheduleExportRecord(BaseModel):
    FORMAT_CHOICES = [('json', 'JSON'), ('csv', 'CSV'), ('xlsx', 'Excel'), ('xer', 'Primavera XER')]

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='export_records')
    export_format = models.CharField(max_length=12, choices=FORMAT_CHOICES)
    filename = models.CharField(max_length=255)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_exports_requested',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['version', '-created_at'])]


class PlanningRetentionPolicy(BaseModel):
    project = models.OneToOneField(
        PlanningProject, on_delete=models.CASCADE, related_name='retention_policy',
    )
    export_history_days = models.PositiveIntegerField(default=365)
    delivery_history_days = models.PositiveIntegerField(default=180)
    completed_job_days = models.PositiveIntegerField(default=180)
    legal_hold = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_retention_policies_updated',
    )

    class Meta:
        ordering = ['project_id']
