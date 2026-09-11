"""Prospective measurements. Historical usage is never silently reclassified."""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class AIWorkflowRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    organization = models.ForeignKey('rbac.Organization', on_delete=models.PROTECT)
    module = models.CharField(max_length=64)
    operation = models.CharField(max_length=64)
    deduplication_key = models.CharField(max_length=64)
    session_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=16, default='running')
    source_type = models.CharField(max_length=64, blank=True)
    source_id = models.CharField(max_length=100, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    instrumentation_version = models.CharField(max_length=32, default='pilot-workflow-v1')

    class Meta:
        ordering = ['-started_at', 'id']
        constraints = [models.UniqueConstraint(fields=['user', 'module', 'operation', 'deduplication_key'], name='unique_ai_workflow_submission')]
        indexes = [models.Index(fields=['organization', 'module', 'started_at'])]


class AIWorkforceSnapshot(models.Model):
    """Immutable daily cohort captured prospectively, not a historical backfill."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('rbac.Organization', on_delete=models.PROTECT)
    captured_at = models.DateTimeField(default=timezone.now)
    capture_date = models.DateField()
    policy_version = models.CharField(max_length=40, default='eligible-linked-active-v1')
    people = models.JSONField(default=dict)
    quality = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'capture_date'], name='unique_ai_workforce_snapshot_day')]
        indexes = [models.Index(fields=['organization', 'captured_at'])]
