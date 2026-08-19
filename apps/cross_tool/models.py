"""
Cross-Tool Registry Model
=========================
Unified document index shared between P&ID QC and PFD QC.
Populated automatically via Django signals — no changes to either source app.

S3 manifest path: cross_tool_registry/manifest.json
"""
import uuid
from django.conf import settings
from django.db import models


class CrossToolRegistry(models.Model):
    # ── Document type choices (SOFT-CODED: add new tool types here) ──────────
    DOC_TYPE_PID = 'pid'
    DOC_TYPE_PFD = 'pfd'
    DOC_TYPE_CHOICES = [
        (DOC_TYPE_PID, 'P&ID QC'),
        (DOC_TYPE_PFD, 'PFD QC'),
    ]

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doc_type     = models.CharField(max_length=10, choices=DOC_TYPE_CHOICES, db_index=True)
    doc_id       = models.UUIDField(db_index=True)         # PIDVDocument.document_id or PFDQDocument.document_id
    project_id   = models.UUIDField(null=True, blank=True, db_index=True)
    project_name = models.CharField(max_length=255, blank=True)
    file_name    = models.CharField(max_length=512)
    status       = models.CharField(max_length=20, default='uploaded')
    uploaded_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cross_tool_entries',
    )
    s3_path   = models.CharField(max_length=1024, blank=True)
    s3_synced = models.BooleanField(default=False, db_index=True)

    registered_at = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table       = 'cross_tool_registry'
        unique_together = [('doc_type', 'doc_id')]
        ordering       = ['-registered_at']
        indexes = [
            models.Index(fields=['doc_type', 'uploaded_by'], name='ctr_type_user_idx'),
            models.Index(fields=['s3_synced'],               name='ctr_s3_synced_idx'),
        ]

    def __str__(self):
        return f'[{self.doc_type.upper()}] {self.file_name}'
