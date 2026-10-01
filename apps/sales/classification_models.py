"""Reviewable document metadata and durable, exact-source classification work."""
import uuid

from django.conf import settings
from django.db import models


class OpportunityDocumentClassification(models.Model):
    document = models.OneToOneField('sales.OpportunityDocument', on_delete=models.PROTECT,
                                   related_name='classification')
    confirmed_type = models.CharField(max_length=40, blank=True)
    custom_tag = models.CharField(max_length=80, blank=True, default='')
    revision = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)


class OpportunityDocumentClassificationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.OneToOneField('sales.OpportunityWorkspaceUpload', on_delete=models.PROTECT,
                                 related_name='classification_run')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    status = models.CharField(max_length=20, default='queued', db_index=True)
    source_sha256 = models.CharField(max_length=64)
    source_identity = models.CharField(max_length=64)
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    next_attempt_at = models.DateTimeField(null=True, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    suggested_type = models.CharField(max_length=40, default='unclassified')
    origin = models.CharField(max_length=20, default='unclassified')
    evidence = models.JSONField(default=list)
    error_code = models.CharField(max_length=50, blank=True)
    ai_status = models.CharField(max_length=20, default='not_needed')
    provider = models.CharField(max_length=30, blank=True)
    model = models.CharField(max_length=200, blank=True)
    extraction_code = models.CharField(max_length=50, blank=True)
    engine_version = models.CharField(max_length=50, default='document_classification_v1')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class OpportunityDocumentClassificationCommand(models.Model):
    document = models.ForeignKey('sales.OpportunityDocument', on_delete=models.PROTECT, related_name='+')
    upload = models.ForeignKey('sales.OpportunityWorkspaceUpload', on_delete=models.PROTECT, related_name='+')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    request_id = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    kind = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('actor', 'request_id'), name='sales_doc_classification_request')]
