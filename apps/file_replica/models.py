import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from .storage import replica_storage, version_path


class ReplicaSource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    root_path = models.CharField(max_length=1024)
    included_paths = models.JSONField(default=list, blank=True)
    excluded_paths = models.JSONField(default=list, blank=True)
    mode = models.CharField(max_length=16, choices=[('catalogue', 'Catalogue'), ('mirror', 'Mirror')], default='catalogue')
    enabled = models.BooleanField(default=True)
    max_file_size_mb = models.PositiveIntegerField(default=100)
    interval_seconds = models.PositiveIntegerField(default=300)
    token_hash = models.CharField(max_length=64, blank=True, editable=False)
    active_run = models.UUIDField(null=True, blank=True, editable=False)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name', 'id']

    @property
    def status(self):
        if not self.enabled:
            return 'disabled'
        if not self.last_heartbeat:
            return 'not_connected'
        if self.last_heartbeat < timezone.now() - timedelta(seconds=max(900, self.interval_seconds * 3)):
            return 'offline'
        if self.last_error:
            return 'error'
        return 'syncing' if self.active_run else 'connected'


class ReplicaScope(models.Model):
    """One top-level project folder; project publication is explicitly enabled."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(ReplicaSource, on_delete=models.CASCADE, related_name='scopes')
    relative_path = models.CharField(max_length=1024)
    path_key = models.CharField(max_length=64)
    project = models.ForeignKey('core.Project', null=True, blank=True, on_delete=models.SET_NULL, related_name='replica_scopes')
    access_enabled = models.BooleanField(default=False)

    class Meta:
        ordering = ['relative_path', 'id']
        constraints = [models.UniqueConstraint(fields=['source', 'path_key'], name='replica_scope_path_unique')]


class ReplicaScan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(ReplicaSource, on_delete=models.CASCADE, related_name='scans')
    status = models.CharField(max_length=16, default='running')
    config_hash = models.CharField(max_length=64)
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']


class ReplicaEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(ReplicaSource, on_delete=models.CASCADE, related_name='entries')
    scope = models.ForeignKey(ReplicaScope, on_delete=models.CASCADE, related_name='entries')
    relative_path = models.CharField(max_length=2048)
    normalized_path = models.CharField(max_length=4096)
    path_key = models.CharField(max_length=64)
    parent_path = models.CharField(max_length=2048, blank=True)
    name = models.CharField(max_length=512)
    is_directory = models.BooleanField(default=False)
    size_bytes = models.PositiveBigIntegerField(default=0)
    modified_at = models.DateTimeField(null=True, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, default='indexed')
    error = models.TextField(blank=True)
    last_seen_scan = models.ForeignKey(ReplicaScan, null=True, on_delete=models.SET_NULL, related_name='+')
    last_seen_at = models.DateTimeField(null=True, blank=True)
    current_version = models.ForeignKey('ReplicaVersion', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-is_directory', 'name', 'id']
        constraints = [models.UniqueConstraint(fields=['source', 'path_key'], name='replica_entry_path_unique')]
        indexes = [models.Index(fields=['source', 'scope']), models.Index(fields=['source', 'status'])]


class ReplicaVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(ReplicaEntry, on_delete=models.CASCADE, related_name='versions')
    number = models.PositiveIntegerField()
    file = models.FileField(storage=replica_storage, upload_to=version_path, max_length=512)
    checksum = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    modified_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=['entry', 'number'], name='replica_version_number_unique')]


class ReplicaExtraction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(ReplicaEntry, on_delete=models.CASCADE, related_name='extractions')
    version = models.ForeignKey(ReplicaVersion, on_delete=models.PROTECT, related_name='extractions')
    status = models.CharField(max_length=24, default='pending_review')
    sections = models.JSONField(default=list)
    suggestions = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    error = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    review_notes = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
