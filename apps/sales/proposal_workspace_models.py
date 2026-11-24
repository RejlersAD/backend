"""Persistent local technical-proposal selections and immutable JSON versions."""
import uuid

from django.conf import settings
from django.db import models


class OpportunityProposalSourceSelection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opportunity = models.ForeignKey(
        'sales.Deal', on_delete=models.CASCADE, related_name='proposal_source_selections',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='proposal_source_selections',
    )
    source_keys = models.JSONField(default=list)
    expected_snapshot = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['opportunity', 'actor'],
                name='sales_proposal_source_actor_uq',
            ),
        ]


class QuoteTechnicalProposalVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.ForeignKey(
        'sales.Quote', on_delete=models.CASCADE, related_name='technical_proposal_versions',
    )
    version = models.PositiveIntegerField()
    content = models.JSONField()
    content_sha256 = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='technical_proposal_versions_created',
    )
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(
                fields=['quote', 'version'], name='sales_technical_proposal_version_uq',
            ),
        ]


class ProposalBuildJob(models.Model):
    """Durable request to build a standard technical proposal with `proposal_engine` (DEC-TP-001).

    Rendering takes minutes, so the request is committed first and a worker claims it with a lease.
    At most one queued/running job exists per proposal; repeating the request returns that job.
    """
    STATUS_CHOICES = [
        ('queued', 'Queued'), ('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.ForeignKey('sales.Quote', on_delete=models.CASCADE, related_name='proposal_build_jobs')
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='proposal_build_jobs',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued', db_index=True)
    source_keys = models.JSONField(default=list)
    source_snapshot = models.CharField(max_length=64, blank=True)
    section_key = models.CharField(max_length=80, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error_code = models.CharField(max_length=60, blank=True)
    summary = models.JSONField(default=dict)
    result_version = models.ForeignKey(
        QuoteTechnicalProposalVersion, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    lease_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['quote'], condition=models.Q(status__in=['queued', 'running']),
                name='sales_proposal_build_active_uq',
            ),
        ]
