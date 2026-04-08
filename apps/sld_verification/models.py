"""
SLD Verification Models
========================
PostgreSQL schema for the Single Line Diagram (SLD) Quality Checker system.
Tables: SLDProject → SLDDocument → SLDDrawing → SLDFinding

Mirrors the pid_verification schema with SLD-specific fields.
"""
import uuid
from django.db import models
from django.conf import settings


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _sld_upload_path(instance, filename):
    project_slug = (
        str(instance.project.project_id) if instance.project_id else 'unassigned'
    )
    return f'sld_verification/projects/{project_slug}/uploads/{instance.document_id}/{filename}'


def _sld_report_path(instance, filename):
    doc_id = getattr(instance, 'document_id', 'unknown')
    project_slug = (
        str(instance.project.project_id) if instance.project_id else 'unassigned'
    )
    return f'sld_verification/projects/{project_slug}/reports/{doc_id}/{filename}'


# ---------------------------------------------------------------------------
# Project  (top-level grouping)
# ---------------------------------------------------------------------------

class SLDProject(models.Model):
    """Groups multiple SLD documents under one project."""

    project_id   = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project_name = models.CharField(max_length=255)
    description  = models.TextField(blank=True)

    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sld_projects',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table  = 'sld_projects'
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

class SLDDocument(models.Model):
    """Represents a single uploaded SLD file (PDF / image / DWG)."""

    class Status(models.TextChoices):
        UPLOADED   = 'uploaded',   'Uploaded'
        PROCESSING = 'processing', 'Processing'
        COMPLETED  = 'completed',  'Completed'
        FAILED     = 'failed',     'Failed'

    # Primary key
    document_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Project grouping
    project = models.ForeignKey(
        SLDProject,
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
        upload_to=_sld_upload_path,
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
        related_name='sld_documents'
    )

    # Exports (filled after processing)
    excel_s3_url = models.CharField(max_length=1024, blank=True)
    pdf_s3_url   = models.CharField(max_length=1024, blank=True)

    # Timestamps
    uploaded_at  = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'sld_documents'
        ordering = ['-uploaded_at']
        indexes  = [
            models.Index(fields=['document_id']),
            models.Index(fields=['file_hash']),
        ]

    def __str__(self):
        return f'{self.file_name} ({self.status})'


# ---------------------------------------------------------------------------
# Drawing  (one per page/sheet inside the document)
# ---------------------------------------------------------------------------

class SLDDrawing(models.Model):
    """A single page / sheet extracted from the uploaded SLD document."""

    document   = models.ForeignKey(SLDDocument, on_delete=models.CASCADE, related_name='drawings')
    drawing_id = models.CharField(max_length=128, blank=True, help_text='Sheet / drawing number')
    title      = models.CharField(max_length=512, blank=True)
    page_index = models.IntegerField(default=0, help_text='0-based page index in the PDF')
    metadata   = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sld_drawings'
        ordering = ['page_index']

    def __str__(self):
        return self.drawing_id or f'Page {self.page_index}'


# ---------------------------------------------------------------------------
# Finding  (one per quality issue detected)
# ---------------------------------------------------------------------------

class SLDFinding(models.Model):
    """A single quality finding linked to a drawing."""

    class Severity(models.TextChoices):
        CRITICAL = 'critical', 'Critical'
        MAJOR    = 'major',    'Major'
        MINOR    = 'minor',    'Minor'
        INFO     = 'info',     'Info'

    class FindingStatus(models.TextChoices):
        OPEN     = 'open',     'Open'
        ACCEPTED = 'accepted', 'Accepted'
        REJECTED = 'rejected', 'Rejected'

    drawing          = models.ForeignKey(SLDDrawing, on_delete=models.CASCADE, related_name='findings')
    sl_no            = models.IntegerField(default=0)
    category         = models.CharField(max_length=64)
    issue_observed   = models.TextField()
    action_required  = models.TextField(blank=True)
    evidence         = models.TextField(blank=True)
    direction        = models.CharField(max_length=64, blank=True)
    severity         = models.CharField(max_length=20, choices=Severity.choices, default=Severity.MINOR)
    status           = models.CharField(max_length=20, choices=FindingStatus.choices, default=FindingStatus.OPEN)
    rule_id          = models.CharField(max_length=64, blank=True)

    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sld_findings'
        ordering = ['sl_no']

    def __str__(self):
        return f'[{self.severity}] {self.category}: {self.issue_observed[:60]}'
