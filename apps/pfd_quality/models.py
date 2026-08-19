"""
PFD Quality Checker — Database Models
======================================
4-tier hierarchy:
  PFDQProject → PFDQDocument → PFDQDrawing → PFDQFinding

All db_table names are prefixed 'pfdq_' to avoid collisions.
"""
import uuid

from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


def _pfdq_upload_path(instance, filename):
    project_slug = str(instance.project.project_id) if instance.project_id else 'unassigned'
    return f'pfd_quality/projects/{project_slug}/uploads/{instance.document_id}/{filename}'


# ---------------------------------------------------------------------------
# Tier 1 — Project
# ---------------------------------------------------------------------------

class PFDQProject(models.Model):
    project_id   = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    project_name = models.CharField(max_length=255)
    description  = models.TextField(blank=True)
    created_by   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pfdq_projects')
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pfdq_projects'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.project_name} ({self.project_id})'


# ---------------------------------------------------------------------------
# Tier 2 — Document (uploaded PFD file)
# ---------------------------------------------------------------------------

class PFDQDocument(models.Model):
    class Status(models.TextChoices):
        UPLOADED   = 'uploaded',   'Uploaded'
        PROCESSING = 'processing', 'Processing'
        COMPLETED  = 'completed',  'Completed'
        FAILED     = 'failed',     'Failed'

    document_id   = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    project       = models.ForeignKey(
        PFDQProject, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='documents',
    )
    file_name     = models.CharField(max_length=512)
    s3_path       = models.CharField(max_length=1024, blank=True)
    original_file = models.FileField(upload_to=_pfdq_upload_path, max_length=500, null=True, blank=True)
    file_hash     = models.CharField(max_length=64, blank=True, db_index=True)
    status        = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UPLOADED, db_index=True,
    )
    error_message = models.TextField(blank=True)
    uploaded_by   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pfdq_documents')
    excel_s3_url  = models.CharField(max_length=1024, blank=True)
    pdf_s3_url    = models.CharField(max_length=1024, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pfdq_documents'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.file_name} [{self.status}]'


# ---------------------------------------------------------------------------
# Tier 3 — Drawing (one page of the PDF)
# ---------------------------------------------------------------------------

class PFDQDrawing(models.Model):
    document   = models.ForeignKey(PFDQDocument, on_delete=models.CASCADE, related_name='drawings')
    drawing_id = models.CharField(max_length=200, db_index=True)
    title      = models.CharField(max_length=512, blank=True)
    page_index = models.PositiveSmallIntegerField(default=0)
    metadata   = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pfdq_drawings'
        ordering = ['page_index']

    def __str__(self):
        return self.drawing_id


# ---------------------------------------------------------------------------
# Tier 4 — Finding (a quality issue on a drawing)
# ---------------------------------------------------------------------------

class PFDQFinding(models.Model):
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
        EQUIPMENT    = 'equipment',    'Equipment'
        STREAM       = 'stream',       'Stream'
        CONTROL      = 'control',      'Control'
        TITLE_BLOCK  = 'title_block',  'Title Block'
        SAFETY       = 'safety',       'Safety'
        UTILITY      = 'utility',      'Utility'
        NOTES        = 'notes',        'Notes & HOLDs'

    drawing         = models.ForeignKey(PFDQDrawing, on_delete=models.CASCADE, related_name='findings')
    sl_no           = models.PositiveIntegerField()
    category        = models.CharField(max_length=30, choices=Category.choices)
    rule_id         = models.CharField(max_length=50, blank=True)
    issue_observed  = models.TextField()
    action_required = models.TextField()
    evidence        = models.TextField(blank=True)
    direction       = models.CharField(max_length=100, blank=True)
    severity        = models.CharField(
        max_length=20, choices=Severity.choices, default=Severity.MAJOR,
    )
    status          = models.CharField(
        max_length=20, choices=FindingStatus.choices, default=FindingStatus.OPEN,
    )
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pfdq_findings'
        ordering = ['sl_no']

    def __str__(self):
        return f'[{self.rule_id}] {self.issue_observed[:60]}'
