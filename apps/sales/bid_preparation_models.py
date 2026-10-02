"""Canonical preparation connections and retained, reviewed source captures."""
import uuid

from django.conf import settings
from django.db import models


class BidPreparation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opportunity = models.OneToOneField('sales.Deal', on_delete=models.PROTECT, related_name='bid_preparation')
    planning_project = models.OneToOneField('planning_intelligence.PlanningProject', on_delete=models.PROTECT,
                                          related_name='sales_bid_preparation')
    reason = models.CharField(max_length=1000)
    source_basis = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)


class QuotePreparationRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.ForeignKey('sales.Quote', on_delete=models.PROTECT, related_name='preparation_revisions')
    preparation = models.ForeignKey(BidPreparation, on_delete=models.PROTECT, related_name='captures')
    technical_proposal = models.ForeignKey('planning_intelligence.TechnicalProposal', on_delete=models.PROTECT,
                                         related_name='sales_preparation_revisions')
    revision = models.PositiveIntegerField()
    source = models.JSONField(default=dict)
    evidence = models.JSONField(default=dict)
    source_fingerprint = models.CharField(max_length=64)
    selected_fields = models.JSONField(default=list)
    before_fields = models.JSONField(default=dict)
    applied_fields = models.JSONField(default=dict)
    reason = models.CharField(max_length=1000)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-revision']
        constraints = [models.UniqueConstraint(fields=['quote', 'revision'], name='sales_quote_preparation_revision_uq')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Preparation captures are immutable; create a new capture.')
        return super().save(*args, **kwargs)


class BidPreparationCommand(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    request_id = models.UUIDField()
    action = models.CharField(max_length=16)
    request_hash = models.CharField(max_length=64)
    preparation = models.ForeignKey(BidPreparation, on_delete=models.PROTECT, related_name='commands')
    capture = models.OneToOneField(QuotePreparationRevision, on_delete=models.PROTECT, null=True, related_name='command')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['actor', 'request_id'], name='sales_bid_preparation_command_uq')]
