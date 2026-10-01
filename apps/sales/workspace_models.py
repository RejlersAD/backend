"""Durable Sales storage work, separate from commercial lifecycle evidence."""
import uuid

from django.conf import settings
from django.db import models


class OpportunityWorkspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opportunity = models.OneToOneField('sales.Deal', on_delete=models.CASCADE, related_name='document_workspace')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    required_action = models.CharField(max_length=10, default='create')
    status = models.CharField(max_length=20, default='not_configured', db_index=True)
    config_fingerprint = models.CharField(max_length=64, blank=True)
    root_item_id = models.CharField(max_length=255, blank=True)
    web_url = models.URLField(max_length=2000, blank=True)
    folders = models.JSONField(default=dict)
    # Persisted before each remote mutation. A lost response is not proof of success.
    intent = models.JSONField(default=dict)
    error_code = models.CharField(max_length=40, blank=True)
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class OpportunityWorkspaceUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(OpportunityWorkspace, on_delete=models.CASCADE, related_name='uploads')
    request_id = models.UUIDField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    folder_key = models.CharField(max_length=30)
    name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    provider = models.CharField(max_length=20, default='sharepoint')
    storage_name = models.CharField(max_length=1024, blank=True)
    storage_fingerprint = models.CharField(max_length=64, blank=True)
    mime_type = models.CharField(max_length=255, blank=True)
    normalized_name = models.CharField(max_length=400, null=True)
    status = models.CharField(max_length=20, default='uploading')
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('workspace', 'request_id'), name='sales_workspace_upload_request'),
            models.UniqueConstraint(fields=('workspace', 'folder_key', 'normalized_name'),
                                    condition=models.Q(provider='radai'), name='sales_private_attachment_name'),
        ]
