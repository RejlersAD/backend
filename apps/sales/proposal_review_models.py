"""Revision-bound internal feedback; never commercial approval evidence."""
import uuid

from django.conf import settings
from django.db import models


class ProposalReviewDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.ForeignKey('sales.Quote', on_delete=models.PROTECT, related_name='review_documents')
    attachment = models.ForeignKey('sales.OpportunityWorkspaceUpload', on_delete=models.PROTECT, related_name='proposal_reviews')
    revision = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    page_count = models.PositiveIntegerField()
    feedback_version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('quote', 'revision'), name='sales_review_document_revision')]
        ordering = ['-revision']


class ProposalReviewComment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(ProposalReviewDocument, on_delete=models.PROTECT, related_name='comments')
    parent = models.ForeignKey('self', on_delete=models.PROTECT, null=True, related_name='replies')
    body = models.TextField()
    kind = models.CharField(max_length=20, default='comment')
    page_number = models.PositiveIntegerField(null=True)
    anchor = models.JSONField(null=True)
    context = models.CharField(max_length=200, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    is_resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    resolved_at = models.DateTimeField(null=True)


class ProposalReviewCommand(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.ForeignKey('sales.Quote', on_delete=models.PROTECT, related_name='review_commands')
    document = models.ForeignKey(ProposalReviewDocument, on_delete=models.PROTECT, related_name='commands')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    request_id = models.UUIDField()
    action = models.CharField(max_length=20)
    payload_hash = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    outcome = models.CharField(max_length=20, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('quote', 'request_id'), name='sales_review_request_identity')]
