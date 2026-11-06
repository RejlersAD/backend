"""Models for the deterministic P&ID → IO List extraction (§8 data contract).

One IOListExtraction per run holds the full result; rows are JSON payload
lists (tags / lines / untagged / setpoints / vendor fills) so the engine's
evidence-rich structures persist without a rigid relational mirror. Service
decisions are relational — they drive the review workflow and audit trail.
"""
import uuid

from django.conf import settings
from django.db import models

from .models import get_io_list_document_storage


class IOListExtraction(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [(STATUS_QUEUED, 'Queued'), (STATUS_RUNNING, 'Running'),
                      (STATUS_COMPLETED, 'Completed'), (STATUS_FAILED, 'Failed')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'instrument_io_workflow.IOListProject', on_delete=models.CASCADE,
        related_name='extractions')

    # Inputs (stored references, not blobs — blobs live in project documents)
    pid_document = models.ForeignKey(
        'instrument_io_workflow.IOListDocument', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pid_extractions')
    iolist_document = models.ForeignKey(
        'instrument_io_workflow.IOListDocument', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='iolist_extractions')
    vendor_documents = models.JSONField(default=list, blank=True)   # [doc ids]

    # Stage state (§2): 1 upload · 2 legend · 3 doc info · 4 AI key
    stage_state = models.JSONField(default=dict, blank=True)
    legend_rules = models.JSONField(default=dict, blank=True)
    doc_number = models.CharField(max_length=128, blank=True, default='')
    revision = models.CharField(max_length=32, blank=True, default='')
    contractor_number = models.CharField(max_length=128, blank=True, default='')
    rejlers_number = models.CharField(max_length=128, blank=True, default='')
    sheet = models.CharField(max_length=32, blank=True, default='')   # e.g. 09/23
    agreement = models.CharField(max_length=255, blank=True, default='')
    use_ai = models.BooleanField(default=False)

    # Result payloads (engine output, §8)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES,
                              default=STATUS_QUEUED, db_index=True)
    error = models.TextField(blank=True, default='')
    summary = models.JSONField(default=dict, blank=True)
    tags_payload = models.JSONField(default=list, blank=True)
    lines_payload = models.JSONField(default=list, blank=True)
    untagged_payload = models.JSONField(default=list, blank=True)
    setpoints_payload = models.JSONField(default=list, blank=True)
    vendor_payload = models.JSONField(default=dict, blank=True)
    cause_notes_payload = models.JSONField(default=list, blank=True)
    alarmtrip_payload = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    is_outdated = models.BooleanField(default=False)   # input replaced later
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='io_list_extractions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'IOListExtraction {self.id} ({self.project}) — {self.status}'


class IOListServiceDecision(models.Model):
    """A reviewer's decision on a generated service (§7.2/7.5)."""
    DECISIONS = [('keep-io-list', 'Keep IO list'), ('use-generated', 'Use generated'),
                 ('ask-ai', 'Ask AI'), ('confirm', 'Confirm row')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    extraction = models.ForeignKey(
        IOListExtraction, on_delete=models.CASCADE, related_name='decisions')
    tag = models.CharField(max_length=128)
    decision = models.CharField(max_length=32, choices=DECISIONS)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('extraction', 'tag')
        ordering = ['tag']


class IOListAISuggestion(models.Model):
    """BYOK AI suggestion for one low-confidence loop (§7.5). Suggested until
    confirmed; never overwrites a confirmed or IO-list value."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    extraction = models.ForeignKey(
        IOListExtraction, on_delete=models.CASCADE, related_name='ai_suggestions')
    loop_key = models.CharField(max_length=64)
    suggestion = models.JSONField(default=dict)   # {service, line, reason, citations}
    status = models.CharField(max_length=16, default='suggested',
                              choices=[('suggested', 'Suggested'), ('confirmed', 'Confirmed'),
                                       ('rejected', 'Rejected')])
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('extraction', 'loop_key')


class IOListExportTemplate(models.Model):
    """The instrumentation team's Standard Template workbook (§ export).

    One organisation-level default (project = NULL); a project may override
    it with its own upload. The file lives in the I/O List storage backend
    (S3 in production) and is always read via file.open('rb'), never .path.
    `validation` caches the upload-time check result (sheets / 53 headers /
    cover cells)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'instrument_io_workflow.IOListProject', on_delete=models.CASCADE,
        null=True, blank=True, related_name='export_templates')
    file = models.FileField(
        upload_to='instrument_io_workflow/export_templates/',
        storage=get_io_list_document_storage)
    original_name = models.CharField(max_length=255, blank=True, default='')
    validation = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        scope = f'project {self.project_id}' if self.project_id else 'organisation'
        return f'IOListExportTemplate {self.original_name} ({scope})'
