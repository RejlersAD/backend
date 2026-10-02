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


class OpportunityFolderTag(models.Model):
    """User-authored category metadata, independent of either document store."""
    opportunity = models.ForeignKey('sales.Deal', on_delete=models.CASCADE, related_name='folder_tags')
    folder_key = models.CharField(max_length=30)
    tag = models.CharField(max_length=64, blank=True, default='')
    revision = models.PositiveIntegerField(default=1)
    last_request_hash = models.CharField(max_length=64)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['opportunity', 'folder_key'], name='sales_opportunity_folder_tag_uq'),
            models.CheckConstraint(check=models.Q(folder_key__in=[
                'correspondence', 'tender', 'proposal', 'internal', 'submitted', 'award',
            ]), name='sales_opportunity_folder_tag_key'),
        ]


class OpportunityWorkspaceUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(OpportunityWorkspace, on_delete=models.CASCADE, related_name='uploads')
    request_id = models.UUIDField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    folder_key = models.CharField(max_length=30)
    name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    storage_encoding = models.CharField(max_length=8, default='identity')
    stored_size = models.PositiveBigIntegerField(null=True)
    stored_sha256 = models.CharField(max_length=64, blank=True)
    provider = models.CharField(max_length=20, default='sharepoint')
    storage_name = models.CharField(max_length=1024, blank=True)
    storage_fingerprint = models.CharField(max_length=64, blank=True)
    mime_type = models.CharField(max_length=255, blank=True)
    normalized_name = models.CharField(max_length=400, null=True)
    document = models.ForeignKey('sales.OpportunityDocument', null=True, on_delete=models.PROTECT, related_name='revisions')
    version_number = models.PositiveIntegerField(default=1)
    previous_upload = models.ForeignKey('self', null=True, on_delete=models.PROTECT, related_name='+')
    revision_note = models.CharField(max_length=1000, blank=True)
    expected_head_token = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, default='uploading')
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(storage_encoding__in=['identity', 'gzip']),
                                   name='sales_upload_storage_encoding'),
            models.UniqueConstraint(fields=('workspace', 'request_id'), name='sales_workspace_upload_request'),
            models.UniqueConstraint(fields=('workspace', 'folder_key', 'normalized_name'),
                                    condition=models.Q(provider='radai', version_number=1), name='sales_private_attachment_name'),
            models.UniqueConstraint(fields=('document', 'version_number'), name='sales_document_revision_uq'),
            models.CheckConstraint(check=models.Q(version_number__gte=1), name='sales_document_revision_positive'),
        ]


class OpportunityDocument(models.Model):
    """Stable private file identity; each upload and stored object stays immutable."""
    id = models.UUIDField(primary_key=True, editable=False)
    workspace = models.ForeignKey(OpportunityWorkspace, on_delete=models.PROTECT, related_name='documents')
    folder_key = models.CharField(max_length=30)
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=400, null=True)
    root_upload = models.OneToOneField(OpportunityWorkspaceUpload, on_delete=models.PROTECT, related_name='root_document')
    head_upload = models.ForeignKey(OpportunityWorkspaceUpload, on_delete=models.PROTECT, related_name='+')
    pending_upload = models.ForeignKey(OpportunityWorkspaceUpload, null=True, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('workspace', 'folder_key', 'normalized_name'), name='sales_document_name_uq'),
        ]
