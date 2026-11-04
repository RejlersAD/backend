"""Private source files and deterministic text evidence for saved email intake."""

import uuid
from django.db import models


class SalesEmailIntakeAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intake = models.ForeignKey('sales.SalesEmailIntake', on_delete=models.PROTECT, related_name='source_attachments')
    source_part_index = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=255, blank=True)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    storage_name = models.CharField(max_length=1024)
    storage_fingerprint = models.CharField(max_length=64)
    text_chunks = models.JSONField(default=list, blank=True)
    extraction_status = models.CharField(max_length=24, default='unread')
    extraction_diagnostic = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('intake', 'source_part_index'), name='sales_intake_attachment_part_uq')]
        ordering = ['source_part_index', 'pk']
