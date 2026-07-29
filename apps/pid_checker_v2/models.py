"""
P&ID Checker V2 — data models.

Minimal empty scaffold; a real checker pipeline (file uploads, extraction,
comparison, findings, ...) will be layered on top later.
"""

import uuid

from django.conf import settings
from django.db import models


# ── Soft-coded field configuration ────────────────────────────────────────
PROJECT_NAME_MAX_LEN = 200
STATUS_MAX_LEN = 32

STATUS_DRAFT = 'draft'
STATUS_ACTIVE = 'active'
STATUS_ARCHIVED = 'archived'

STATUS_CHOICES = (
    (STATUS_DRAFT, 'Draft'),
    (STATUS_ACTIVE, 'Active'),
    (STATUS_ARCHIVED, 'Archived'),
)

DEFAULT_STATUS = STATUS_DRAFT


class PidCheckerV2Project(models.Model):
    """A single P&ID Checker V2 project (workspace container)."""

    project_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
    )
    name = models.CharField(max_length=PROJECT_NAME_MAX_LEN)
    description = models.TextField(blank=True, default='')
    status = models.CharField(
        max_length=STATUS_MAX_LEN,
        choices=STATUS_CHOICES,
        default=DEFAULT_STATUS,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pid_checker_v2_projects',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pid_checker_v2_project'
        ordering = ['-created_at']
        verbose_name = 'P&ID Checker V2 Project'
        verbose_name_plural = 'P&ID Checker V2 Projects'

    def __str__(self) -> str:
        return f'{self.name} ({self.status})'


# ══════════════════════════════════════════════════════════════════════
# Auto-save: extractions + line-tag rows
# ══════════════════════════════════════════════════════════════════════

# Soft-coded field limits
FILENAME_MAX_LEN = 255
SHA256_MAX_LEN = 64
MODE_MAX_LEN = 16
PROVIDER_MAX_LEN = 32
MODEL_MAX_LEN = 64
TAG_FIELD_MAX_LEN = 64
SERVICE_GROUP_MAX_LEN = 64

MODE_OCR = 'ocr'
MODE_VISION = 'vision'
MODE_CHOICES = (
    (MODE_OCR, 'OCR'),
    (MODE_VISION, 'AI Vision (BYOK)'),
)

PROVIDER_OPENAI = 'openai'
PROVIDER_CLAUDE = 'claude'
PROVIDER_CHOICES = (
    (PROVIDER_OPENAI, 'OpenAI'),
    (PROVIDER_CLAUDE, 'Anthropic Claude'),
)


class PidCheckerV2Extraction(models.Model):
    """One line-tag extraction run — auto-saved after each successful upload."""

    extraction_id = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pid_checker_v2_extractions',
    )
    project = models.ForeignKey(
        PidCheckerV2Project,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='extractions',
    )

    filename = models.CharField(max_length=FILENAME_MAX_LEN)
    file_size_bytes = models.BigIntegerField(default=0)
    file_sha256 = models.CharField(max_length=SHA256_MAX_LEN, db_index=True, blank=True, default='')

    mode = models.CharField(max_length=MODE_MAX_LEN, choices=MODE_CHOICES, default=MODE_OCR)
    provider = models.CharField(max_length=PROVIDER_MAX_LEN, choices=PROVIDER_CHOICES, blank=True, default='')
    model = models.CharField(max_length=MODEL_MAX_LEN, blank=True, default='')
    force_ocr = models.BooleanField(default=False)

    tag_count = models.IntegerField(default=0)
    summary_json = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pid_checker_v2_extraction'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_by', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.filename} — {self.tag_count} tags ({self.mode})'


class PidCheckerV2LineTag(models.Model):
    """A single line tag extracted during a run."""

    extraction = models.ForeignKey(
        PidCheckerV2Extraction,
        on_delete=models.CASCADE,
        related_name='line_tags',
    )
    tag = models.CharField(max_length=TAG_FIELD_MAX_LEN)
    size = models.CharField(max_length=TAG_FIELD_MAX_LEN, blank=True, default='')
    service = models.CharField(max_length=TAG_FIELD_MAX_LEN, blank=True, default='')
    spec = models.CharField(max_length=TAG_FIELD_MAX_LEN, blank=True, default='')
    serial = models.CharField(max_length=TAG_FIELD_MAX_LEN, blank=True, default='')
    service_group = models.CharField(max_length=SERVICE_GROUP_MAX_LEN, blank=True, default='')

    class Meta:
        db_table = 'pid_checker_v2_line_tag'
        ordering = ['service', 'serial', 'size']
        constraints = [
            models.UniqueConstraint(
                fields=['extraction', 'tag'],
                name='uniq_pidv2_extraction_tag',
            ),
        ]
        indexes = [
            models.Index(fields=['extraction', 'service']),
        ]

    def __str__(self) -> str:
        return self.tag


# ══════════════════════════════════════════════════════════════════════
# Legend Sheets — user-defined per-section extraction rules
# ══════════════════════════════════════════════════════════════════════
from .legend_defaults import SECTIONS as LEGEND_SECTIONS, SECTION_LABELS as LEGEND_SECTION_LABELS  # noqa: E402

LEGEND_SECTION_MAX_LEN = 32
LEGEND_NAME_MAX_LEN = 200

LEGEND_SECTION_CHOICES = tuple(
    (code, LEGEND_SECTION_LABELS.get(code, code)) for code in LEGEND_SECTIONS
)


class PidCheckerV2LegendSheet(models.Model):
    """A user-owned rule set for one section (e.g. 'line_list').

    Only one legend per (user, section) may be active at a time — enforced
    by ``activate()`` on the model manager and a partial unique index.
    """

    legend_id = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pid_checker_v2_legends',
    )
    section = models.CharField(
        max_length=LEGEND_SECTION_MAX_LEN,
        choices=LEGEND_SECTION_CHOICES,
    )
    name = models.CharField(max_length=LEGEND_NAME_MAX_LEN)
    description = models.TextField(blank=True, default='')
    definition = models.JSONField(default=dict)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pid_checker_v2_legend_sheet'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['created_by', 'section', '-updated_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['created_by', 'section'],
                condition=models.Q(is_active=True),
                name='uniq_pidv2_active_legend_per_user_section',
            ),
        ]

    def __str__(self) -> str:
        marker = ' *' if self.is_active else ''
        return f'{self.name} [{self.section}]{marker}'
