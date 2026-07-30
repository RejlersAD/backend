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


# ─── Master Line List (Excel upload) ──────────────────────────────────
LINE_LIST_FILENAME_MAX_LEN = 300
LINE_LIST_TITLE_MAX_LEN = 500
LINE_LIST_PID_REF_MAX_LEN = 500


class PidCheckerV2LineListUpload(models.Model):
    """A parsed master Line List (Excel) uploaded by a user.

    The rows are stored as ``PidCheckerV2LineListRow`` children.  We keep
    the raw meta captured from the header block (title, doc no, source
    P&ID reference) so the UI can display it and the cross-check can pin
    the diff to one drawing.
    """

    line_list_id = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pid_checker_v2_line_lists',
    )
    filename = models.CharField(max_length=LINE_LIST_FILENAME_MAX_LEN)
    sheet_name = models.CharField(max_length=200, blank=True, default='')
    title = models.CharField(max_length=LINE_LIST_TITLE_MAX_LEN, blank=True, default='')
    doc_no = models.CharField(max_length=LINE_LIST_TITLE_MAX_LEN, blank=True, default='')
    doc_date = models.CharField(max_length=64, blank=True, default='')
    pid_extract_ref = models.CharField(max_length=LINE_LIST_PID_REF_MAX_LEN, blank=True, default='')
    total_rows = models.PositiveIntegerField(default=0)
    columns = models.JSONField(default=dict)     # {canonical_key: excel_col_idx}
    summary = models.JSONField(default=dict)     # per-service / per-spec counts
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pid_checker_v2_line_list_upload'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_by', '-created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['created_by'],
                condition=models.Q(is_active=True),
                name='uniq_pidv2_active_line_list_per_user',
            ),
        ]

    def __str__(self) -> str:
        marker = ' *' if self.is_active else ''
        return f'{self.filename} ({self.total_rows} rows){marker}'


class PidCheckerV2LineListRow(models.Model):
    """One line from a master Line List Excel."""

    upload = models.ForeignKey(
        PidCheckerV2LineListUpload,
        on_delete=models.CASCADE,
        related_name='rows',
    )
    excel_row = models.PositiveIntegerField(default=0)
    tag = models.CharField(max_length=200, db_index=True, blank=True, default='')
    size = models.CharField(max_length=32, blank=True, default='')
    service_code = models.CharField(max_length=16, blank=True, default='')
    serial = models.CharField(max_length=32, blank=True, default='')
    spec = models.CharField(max_length=32, blank=True, default='')
    from_ref = models.CharField(max_length=300, blank=True, default='')
    to_ref = models.CharField(max_length=300, blank=True, default='')
    pid_no = models.CharField(max_length=300, blank=True, default='')
    fluid_service = models.CharField(max_length=200, blank=True, default='')
    # Free-form bag of the rest so we don't lose the ~30 columns
    extras = models.JSONField(default=dict)

    class Meta:
        db_table = 'pid_checker_v2_line_list_row'
        ordering = ['excel_row']
        indexes = [
            models.Index(fields=['upload', 'tag']),
        ]

    def __str__(self) -> str:
        return self.tag or f'row {self.excel_row}'


# ─── Master Equipment List (Excel upload) ─────────────────────────────
EQUIPMENT_LIST_FILENAME_MAX_LEN = 300
EQUIPMENT_LIST_TITLE_MAX_LEN = 500
EQUIPMENT_LIST_PID_REF_MAX_LEN = 500


class PidCheckerV2EquipmentListUpload(models.Model):
    """A parsed master Equipment List (Excel) uploaded by a user.

    Rows are stored as ``PidCheckerV2EquipmentListRow`` children.  We keep
    the raw meta captured from the header block (title, doc no, source
    P&ID reference, company, project) so the UI can display it and the
    cross-check can pin the diff to one drawing.
    """

    equipment_list_id = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pid_checker_v2_equipment_lists',
    )
    filename = models.CharField(max_length=EQUIPMENT_LIST_FILENAME_MAX_LEN)
    sheet_name = models.CharField(max_length=200, blank=True, default='')
    title = models.CharField(max_length=EQUIPMENT_LIST_TITLE_MAX_LEN, blank=True, default='')
    doc_no = models.CharField(max_length=EQUIPMENT_LIST_TITLE_MAX_LEN, blank=True, default='')
    doc_date = models.CharField(max_length=64, blank=True, default='')
    pid_extract_ref = models.CharField(max_length=EQUIPMENT_LIST_PID_REF_MAX_LEN, blank=True, default='')
    company = models.CharField(max_length=EQUIPMENT_LIST_TITLE_MAX_LEN, blank=True, default='')
    project = models.CharField(max_length=EQUIPMENT_LIST_TITLE_MAX_LEN, blank=True, default='')
    total_rows = models.PositiveIntegerField(default=0)
    columns = models.JSONField(default=dict)     # {canonical_key: excel_col_idx}
    summary = models.JSONField(default=dict)     # per-pid / per-moc counts
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pid_checker_v2_equipment_list_upload'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_by', '-created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['created_by'],
                condition=models.Q(is_active=True),
                name='uniq_pidv2_active_equipment_list_per_user',
            ),
        ]

    def __str__(self) -> str:
        marker = ' *' if self.is_active else ''
        return f'{self.filename} ({self.total_rows} rows){marker}'


class PidCheckerV2EquipmentListRow(models.Model):
    """One equipment item from a master Equipment List Excel."""

    upload = models.ForeignKey(
        PidCheckerV2EquipmentListUpload,
        on_delete=models.CASCADE,
        related_name='rows',
    )
    excel_row = models.PositiveIntegerField(default=0)
    tag = models.CharField(max_length=64, db_index=True, blank=True, default='')
    description = models.CharField(max_length=300, blank=True, default='')
    design_flow = models.CharField(max_length=100, blank=True, default='')
    op_pressure = models.CharField(max_length=64, blank=True, default='')
    op_temp = models.CharField(max_length=64, blank=True, default='')
    design_p_min = models.CharField(max_length=32, blank=True, default='')
    design_p_max = models.CharField(max_length=32, blank=True, default='')
    design_t_min = models.CharField(max_length=32, blank=True, default='')
    design_t_max = models.CharField(max_length=32, blank=True, default='')
    moc = models.CharField(max_length=100, blank=True, default='')
    insulation = models.CharField(max_length=64, blank=True, default='')
    dim_length = models.CharField(max_length=32, blank=True, default='')
    dim_diameter = models.CharField(max_length=32, blank=True, default='')
    motor_rating = models.CharField(max_length=32, blank=True, default='')
    pid_no = models.CharField(max_length=300, blank=True, default='')
    qty = models.CharField(max_length=16, blank=True, default='')
    phase = models.CharField(max_length=64, blank=True, default='')
    remarks = models.CharField(max_length=500, blank=True, default='')
    # Deep-attribute columns compared against P&ID Vision extraction.
    nominal_capacity = models.CharField(max_length=64, blank=True, default='')
    length_tt = models.CharField(max_length=64, blank=True, default='')
    diameter_id = models.CharField(max_length=64, blank=True, default='')
    material_shell = models.CharField(max_length=120, blank=True, default='')
    material_internal = models.CharField(max_length=120, blank=True, default='')
    trim = models.CharField(max_length=120, blank=True, default='')
    # Free-form bag of the rest so we don't lose extra columns
    extras = models.JSONField(default=dict)

    class Meta:
        db_table = 'pid_checker_v2_equipment_list_row'
        ordering = ['excel_row']
        indexes = [
            models.Index(fields=['upload', 'tag']),
        ]

    def __str__(self) -> str:
        return self.tag or f'row {self.excel_row}'


# --- Master Instrument Index (Excel upload) ---------------------------
INSTRUMENT_INDEX_FILENAME_MAX_LEN = 300
INSTRUMENT_INDEX_TITLE_MAX_LEN = 500
INSTRUMENT_INDEX_PID_REF_MAX_LEN = 500


class PidCheckerV2InstrumentIndexUpload(models.Model):
    """A parsed master Instrument Index (Excel) uploaded by a user.

    Each instrument is stored as one ``PidCheckerV2InstrumentIndexRow``
    (already merged from the primary + secondary Excel rows).
    """

    instrument_index_id = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pid_checker_v2_instrument_indexes',
    )
    filename = models.CharField(max_length=INSTRUMENT_INDEX_FILENAME_MAX_LEN)
    sheet_name = models.CharField(max_length=200, blank=True, default='')
    title = models.CharField(max_length=INSTRUMENT_INDEX_TITLE_MAX_LEN, blank=True, default='')
    doc_no = models.CharField(max_length=INSTRUMENT_INDEX_TITLE_MAX_LEN, blank=True, default='')
    doc_date = models.CharField(max_length=64, blank=True, default='')
    pid_extract_ref = models.CharField(max_length=INSTRUMENT_INDEX_PID_REF_MAX_LEN, blank=True, default='')
    company = models.CharField(max_length=300, blank=True, default='')
    project = models.CharField(max_length=500, blank=True, default='')
    total_rows = models.PositiveIntegerField(default=0)
    columns = models.JSONField(default=dict)
    summary = models.JSONField(default=dict)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pid_checker_v2_instrument_index_upload'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_by', '-created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['created_by'],
                condition=models.Q(is_active=True),
                name='uniq_pidv2_active_instrument_index_per_user',
            ),
        ]

    def __str__(self) -> str:
        marker = ' *' if self.is_active else ''
        return f'{self.filename} ({self.total_rows} rows){marker}'


class PidCheckerV2InstrumentIndexRow(models.Model):
    """One instrument from a master Instrument Index Excel."""

    upload = models.ForeignKey(
        PidCheckerV2InstrumentIndexUpload,
        on_delete=models.CASCADE,
        related_name='rows',
    )
    excel_row = models.PositiveIntegerField(default=0)
    tag = models.CharField(max_length=64, db_index=True, blank=True, default='')
    instrument_type = models.CharField(max_length=200, blank=True, default='')
    service_description = models.CharField(max_length=500, blank=True, default='')
    pid_no = models.CharField(max_length=300, blank=True, default='')
    line_no = models.CharField(max_length=200, blank=True, default='')
    eqpt_no = models.CharField(max_length=64, blank=True, default='')
    location = models.CharField(max_length=64, blank=True, default='')
    ex_class = models.CharField(max_length=64, blank=True, default='')
    power_supply = models.CharField(max_length=64, blank=True, default='')
    range_min = models.CharField(max_length=32, blank=True, default='')
    range_max = models.CharField(max_length=32, blank=True, default='')
    range_unit = models.CharField(max_length=32, blank=True, default='')
    cal_min = models.CharField(max_length=32, blank=True, default='')
    cal_max = models.CharField(max_length=32, blank=True, default='')
    cal_unit = models.CharField(max_length=32, blank=True, default='')
    datasheet_no = models.CharField(max_length=200, blank=True, default='')
    loop_dwg_no = models.CharField(max_length=200, blank=True, default='')
    hookup_dwg_no = models.CharField(max_length=200, blank=True, default='')
    location_layout_no = models.CharField(max_length=200, blank=True, default='')
    manufacturer = models.CharField(max_length=200, blank=True, default='')
    model = models.CharField(max_length=200, blank=True, default='')
    remarks = models.CharField(max_length=500, blank=True, default='')
    rev = models.CharField(max_length=16, blank=True, default='')
    extras = models.JSONField(default=dict)

    class Meta:
        db_table = 'pid_checker_v2_instrument_index_row'
        ordering = ['excel_row']
        indexes = [
            models.Index(fields=['upload', 'tag']),
        ]

    def __str__(self) -> str:
        return self.tag or f'row {self.excel_row}'
