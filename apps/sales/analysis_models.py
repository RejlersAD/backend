"""Durable, requester-scoped advisory Opportunity Analysis; no Deal mutations."""
import uuid

from django.conf import settings
from django.db import models


class OpportunityAnalysis(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opportunity = models.ForeignKey('sales.Deal', on_delete=models.CASCADE, related_name='analyses')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    status = models.CharField(max_length=20, default='queued', db_index=True)
    dirty = models.BooleanField(default=True)
    snapshot = models.CharField(max_length=64, blank=True)
    provider_identity = models.CharField(max_length=64, blank=True)
    result = models.JSONField(default=dict)
    suppressed_sources = models.JSONField(default=dict)
    error_code = models.CharField(max_length=60, blank=True)
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    next_attempt_at = models.DateTimeField(null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['opportunity', 'requested_by'], name='sales_analysis_requester_uq')]


class OpportunityAnalysisSource(models.Model):
    report = models.ForeignKey(OpportunityAnalysis, on_delete=models.CASCADE, related_name='sources')
    source_key = models.CharField(max_length=100)
    identity = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='queued')
    diagnostic = models.CharField(max_length=60, blank=True)
    references = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['report', 'source_key'], name='sales_analysis_source_uq')]


class OpportunityAnalysisChunk(models.Model):
    source = models.ForeignKey(OpportunityAnalysisSource, on_delete=models.CASCADE, related_name='chunks')
    ordinal = models.PositiveIntegerField()
    location = models.CharField(max_length=120)
    text = models.TextField()
    status = models.CharField(max_length=20, default='queued', db_index=True)
    claims = models.JSONField(default=list)
    attempts = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=60, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source', 'ordinal'], name='sales_analysis_chunk_uq')]
