"""
P&ID Verification Models
========================
PostgreSQL schema for the P&ID Quality Checker system.
Tables: PIDVProject → PIDVDocument → PIDVDrawing → PIDVFinding
"""
import uuid
import hashlib
import os
from django.db import models
from django.conf import settings


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _pid_upload_path(instance, filename):
    project_slug = (
        str(instance.project.project_id) if instance.project_id else 'unassigned'
    )
    return f'pid_verification/projects/{project_slug}/uploads/{instance.document_id}/{filename}'


def _report_path(instance, filename):
    doc_id = getattr(instance, 'document_id', 'unknown')
    project_slug = (
        str(instance.project.project_id) if instance.project_id else 'unassigned'
    )
    return f'pid_verification/projects/{project_slug}/reports/{doc_id}/{filename}'


# ---------------------------------------------------------------------------
# Project  (top-level grouping)
# ---------------------------------------------------------------------------

class PIDVProject(models.Model):
    """Groups multiple P&ID documents under one project."""

    project_id   = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project_name = models.CharField(max_length=255)
    description  = models.TextField(blank=True)

    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pid_v_projects',
    )

    # Per-project legend sheet knowledge (overrides the global legend for this project).
    # Stores the structured output of build_legend_knowledge(): instrument_prefixes,
    # valve_prefixes, note_keywords, hold_keywords, sources.
    legend_knowledge_data = models.JSONField(
        null=True, blank=True,
        help_text='Extracted legend prefixes specific to this project.'
    )
    legend_built_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table  = 'pidv_projects'
        ordering  = ['-created_at']
        indexes   = [models.Index(fields=['project_id'])]

    def __str__(self):
        return self.project_name

    @property
    def document_count(self):
        return self.documents.count()


# ---------------------------------------------------------------------------
# Document  (one per uploaded file)
# ---------------------------------------------------------------------------

class PIDVDocument(models.Model):
    """Represents a single uploaded file (PDF / image / DWG)."""

    class Status(models.TextChoices):
        UPLOADED   = 'uploaded',   'Uploaded'
        PROCESSING = 'processing', 'Processing'
        COMPLETED  = 'completed',  'Completed'
        FAILED     = 'failed',     'Failed'

    # Primary key
    document_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Project grouping (optional — null means "unassigned")
    project = models.ForeignKey(
        PIDVProject,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='documents',
    )

    # File storage
    file_name    = models.CharField(max_length=512)
    s3_path      = models.CharField(max_length=1024, blank=True)
    file_hash    = models.CharField(
        max_length=64,
        db_index=True,
        help_text='SHA-256 of the raw file – enables deterministic caching'
    )
    original_file = models.FileField(
        upload_to=_pid_upload_path,
        max_length=500,
        null=True, blank=True
    )

    # Status
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.UPLOADED)
    error_message = models.TextField(blank=True)

    # Owner
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pid_v_documents'
    )

    # Exports (filled after processing)
    excel_s3_url = models.CharField(max_length=1024, blank=True)
    pdf_s3_url   = models.CharField(max_length=1024, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table  = 'pidv_documents'
        ordering  = ['-created_at']
        indexes   = [
            models.Index(fields=['document_id']),
            models.Index(fields=['file_hash']),
            models.Index(fields=['status']),
            models.Index(fields=['uploaded_by', '-created_at']),
        ]

    def __str__(self):
        return f'{self.file_name} [{self.status}]'


# ---------------------------------------------------------------------------
# Drawing  (one document → one or many drawings)
# ---------------------------------------------------------------------------

class PIDVDrawing(models.Model):
    """One P&ID drawing segmented from a document."""

    document   = models.ForeignKey(
        PIDVDocument,
        on_delete=models.CASCADE,
        related_name='drawings'
    )
    drawing_id = models.CharField(max_length=100, db_index=True)   # e.g. "DRAWING-1"
    title      = models.CharField(max_length=512, blank=True)       # extracted title block
    page_index = models.PositiveSmallIntegerField(default=0)        # page/segment index
    metadata   = models.JSONField(default=dict, blank=True)         # raw extraction metadata
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'pidv_drawings'
        unique_together = [('document', 'drawing_id')]
        ordering        = ['page_index']
        indexes         = [
            models.Index(fields=['document', 'drawing_id']),
        ]

    def __str__(self):
        return f'{self.drawing_id} (doc={self.document.document_id})'


# ---------------------------------------------------------------------------
# Finding  (one drawing → many findings)
# ---------------------------------------------------------------------------

class PIDVFinding(models.Model):
    """A single quality issue detected by the deterministic rule engine."""

    class Severity(models.TextChoices):
        CRITICAL = 'critical', 'Critical'
        MAJOR    = 'major',    'Major'
        MINOR    = 'minor',    'Minor'
        INFO     = 'info',     'Info'

    class FindingStatus(models.TextChoices):
        OPEN     = 'open',     'Open'
        REVIEWED = 'reviewed', 'Reviewed'
        RESOLVED = 'resolved', 'Resolved'

    class Category(models.TextChoices):
        TAG          = 'tag',          'Tag Issues'
        CONNECTIVITY = 'connectivity', 'Connectivity Issues'
        VALVE        = 'valve',        'Valve & Equipment'
        LINE_SIZE    = 'line_size',    'Line Size'
        NOTES        = 'notes',        'Notes & HOLDs'

    drawing         = models.ForeignKey(PIDVDrawing, on_delete=models.CASCADE, related_name='findings')
    sl_no           = models.PositiveIntegerField(help_text='Sequential number within the drawing')
    category        = models.CharField(max_length=20, choices=Category.choices)
    issue_observed  = models.TextField()
    action_required = models.TextField()
    evidence        = models.TextField(blank=True, help_text='Raw OCR text / location hint')
    direction       = models.CharField(max_length=100, blank=True, help_text='Horizontal / Vertical / N/A')
    severity        = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MAJOR)
    status          = models.CharField(max_length=10, choices=FindingStatus.choices, default=FindingStatus.OPEN)
    rule_id         = models.CharField(max_length=50, blank=True, help_text='Rule that triggered this finding')
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pidv_findings'
        ordering = ['drawing', 'sl_no']
        indexes  = [
            models.Index(fields=['drawing', 'sl_no']),
            models.Index(fields=['severity']),
            models.Index(fields=['category']),
        ]

    def __str__(self):
        return f'[{self.sl_no}] {self.category}: {self.issue_observed[:60]}'
