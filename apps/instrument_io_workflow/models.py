"""
Lean data model — four tables (project management added):

    IOListProject           — project container for organizing documents
    IOListDocument          — one uploaded PDF (single revision)
    IOListExtractedComment  — rows from the Comments Resolution Sheet
    IOListExtractedRow      — rows from the structured IO table

Multi-revision chain tracking is delegated to the existing CRS chain backend
(apps.crs.CRSRevisionChain). We only store an optional FK reference to it.
"""

from django.conf import settings
from django.db import models


class IOListProject(models.Model):
    """
    Project container for grouping I/O List documents.
    Soft-coded — all field choices and labels live in config.py and frontend.
    """
    
    STATUS_CHOICES = [
        ('draft',      'Draft'),
        ('active',     'Active'),
        ('review',     'Under Review'),
        ('completed',  'Completed'),
        ('archived',   'Archived'),
    ]
    
    CATEGORY_CHOICES = [
        ('oil_gas',    'Oil & Gas'),
        ('refinery',   'Refinery'),
        ('lng',        'LNG'),
        ('power',      'Power Plant'),
        ('water',      'Water/Wastewater'),
        ('other',      'Other'),
    ]
    
    # Identity
    project_name   = models.CharField(max_length=255)
    project_code   = models.CharField(max_length=100, blank=True, default='')
    description    = models.TextField(blank=True, default='')
    
    # Classification
    category       = models.CharField(
        max_length=50, choices=CATEGORY_CHOICES, default='oil_gas',
    )
    status         = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft',
    )
    
    # Metadata
    client         = models.CharField(max_length=255, blank=True, default='')
    location       = models.CharField(max_length=255, blank=True, default='')
    tags           = models.JSONField(default=list, blank=True)  # Flexible tagging
    
    # Audit
    created_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='io_list_projects',
    )
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['created_by', 'status']),
            models.Index(fields=['category']),
            models.Index(fields=['-created_at']),
        ]
        verbose_name = 'I/O List Project'
        verbose_name_plural = 'I/O List Projects'
    
    def __str__(self) -> str:
        return f'{self.project_name} ({self.get_status_display()})'
    
    @property
    def document_count(self):
        """Cached count of documents in this project."""
        return self.documents.count()


class IOListDocument(models.Model):
    """A single uploaded Instrument IO List PDF (one revision)."""

    STATUS_CHOICES = [
        ('uploaded',  'Uploaded'),
        ('extracting','Extracting'),
        ('completed', 'Completed'),
        ('failed',    'Failed'),
    ]

    # Identity
    project_name      = models.CharField(max_length=255, blank=True, default='')
    document_number   = models.CharField(max_length=255, blank=True, default='')
    revision_label    = models.CharField(max_length=20,  blank=True, default='')
    plant             = models.CharField(max_length=120, blank=True, default='')
    unit              = models.CharField(max_length=60,  blank=True, default='')

    # Storage
    pdf_file          = models.FileField(
        upload_to='instrument_io_workflow/%Y/%m/',
        storage='apps.core.storage_backends.IOListDocumentStorage',
    )
    pdf_sha256        = models.CharField(max_length=64, db_index=True)

    # Extraction state
    status            = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='uploaded',
    )
    extraction_stats  = models.JSONField(default=dict, blank=True)
    extraction_error  = models.TextField(blank=True, default='')

    # Optional link into existing CRS revision chain (NO core change to CRS)
    crs_chain_id      = models.CharField(max_length=64, blank=True, default='')
    
    # Project organization (soft-coded, backward compatible)
    project           = models.ForeignKey(
        'IOListProject',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
        help_text='Optional project container for organizing documents',
    )

    # Audit
    uploaded_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='io_list_documents',
    )
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['pdf_sha256']),
            models.Index(fields=['crs_chain_id']),
            models.Index(fields=['document_number', 'revision_label']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f'{self.document_number or "—"} rev {self.revision_label or "?"}'


class IOListExtractedComment(models.Model):
    """One row from the Comments Resolution Sheet."""
    document          = models.ForeignKey(
        IOListDocument, on_delete=models.CASCADE,
        related_name='extracted_comments',
    )
    s_no              = models.CharField(max_length=40,  blank=True, default='')
    company_comment   = models.TextField(blank=True, default='')
    contractor_reply  = models.TextField(blank=True, default='')
    company_decision  = models.TextField(blank=True, default='')
    status_code       = models.CharField(max_length=20,  blank=True, default='')
    status_meaning    = models.CharField(max_length=120, blank=True, default='')
    page_number       = models.PositiveIntegerField(null=True, blank=True)
    linked_tags       = models.JSONField(default=list, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['document_id', 'page_number', 'id']


class IOListExtractedRow(models.Model):
    """One row from the structured IO table (DCS or ESD sheet)."""
    document          = models.ForeignKey(
        IOListDocument, on_delete=models.CASCADE,
        related_name='extracted_rows',
    )
    tag_number        = models.CharField(max_length=80, db_index=True,
                                          blank=True, default='')
    page_number       = models.PositiveIntegerField(null=True, blank=True)
    # All other 39 columns live here as flexible JSON — keeps schema soft-coded.
    # Frontend reads via IO_LIST_CANONICAL_COLUMNS order.
    data              = models.JSONField(default=dict, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['document_id', 'page_number', 'id']
        indexes  = [models.Index(fields=['tag_number'])]
