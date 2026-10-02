"""Atomic evidence and retry identities for reviewed canonical record links."""
from django.conf import settings
from django.db import models


class SharedRecordLinkCommand(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_id = models.UUIDField()
    source_type = models.CharField(max_length=40)
    source_id = models.CharField(max_length=80)
    request_hash = models.CharField(max_length=64)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reason = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['actor', 'request_id'], name='core_record_link_request')]
        indexes = [models.Index(fields=['source_type', 'source_id'], name='core_record_link_source')]
        ordering = ['-created_at', '-pk']
