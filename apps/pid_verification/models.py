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


def _legend_upload_path(instance, filename):
    """Storage path for legend sheet files — scoped to project or 'global'."""
    project_slug = (
        str(instance.project.project_id) if instance.project_id else 'global'
    )
    return f'pid_verification/projects/{project_slug}/legends/{instance.legend_id}/{filename}'


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
        UPLOADED        = 'uploaded',        'Uploaded'
        PROCESSING      = 'processing',      'Processing'
        COMPLETED       = 'completed',       'Completed'
        FAILED          = 'failed',          'Failed'
        LEGEND_PENDING  = 'legend_pending',  'Waiting for Legend'

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


# ---------------------------------------------------------------------------
# PIDVLegendSheet  (legend sheets uploaded alongside P&IDs)
# ---------------------------------------------------------------------------

class PIDVLegendSheet(models.Model):
    """
    Stores a single legend sheet file and its AI-extracted structured data.

    Extracted sections (all stored in `extracted_data` JSONField):
      - line_representation     : [{ key, description, line_style }]
      - line_numbering_piping   : { format, fields: [{ pos, name, example, desc }] }
      - line_numbering_pipeline : { format, fields: [...] }
      - abbreviations_process   : [{ abbr, full_name, category }]
      - inline_equipment        : [{ symbol, description, type }]
      - service_codes           : { code: description, ... }
      - insulation_codes        : { code: description, ... }
      - piping_specs            : { spec_code: description, ... }
      - instrument_prefixes     : [...]
      - valve_prefixes          : [...]
      - raw_sections            : { heading: [rows] }  ← full OCR/AI output
    """

    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED  = 'completed',  'Completed'
        FAILED     = 'failed',     'Failed'

    legend_id     = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Owning project — nullable so global/unassigned legend sheets are allowed
    project = models.ForeignKey(
        PIDVProject,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='legend_sheets',
    )

    file_name     = models.CharField(max_length=512)
    original_file = models.FileField(
        upload_to=_legend_upload_path,
        max_length=500,
        null=True, blank=True,
    )
    # S3 key or presigned URL — populated after upload
    s3_path       = models.CharField(max_length=1024, blank=True)

    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)

    # Full structured extraction output from AI pipeline
    extracted_data = models.JSONField(
        null=True, blank=True,
        help_text='Structured legend data extracted by AI (see docstring for schema)',
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pid_legend_sheets',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pidv_legend_sheets'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['legend_id']),
            models.Index(fields=['project', 'status']),
        ]

    def __str__(self):
        return f'Legend: {self.file_name} [{self.status}]'


# ---------------------------------------------------------------------------
# PIDVInstrumentSymbol  (individual symbols extracted from legend sheets)
# ---------------------------------------------------------------------------

def _instrument_symbol_upload_path(instance, filename):
    project_slug = str(instance.project.project_id) if instance.project_id else 'global'
    return f'pid_verification/projects/{project_slug}/instrument_symbols/{instance.symbol_id}/{filename}'


class PIDVInstrumentSymbol(models.Model):
    """
    Stores one instrument / valve / equipment symbol extracted from a legend sheet.

    Six standard categories (user-defined in the legend):
      CONTROL_VALVE      — pneumatic, hydraulic, electric, solenoid actuated valves
      MANUAL_VALVE       — ball, gate, globe, butterfly, plug, needle, check
      INSTRUMENT         — ISA 5.1 measurement bubbles / transmitters / indicators
      INSTRUMENT_TAGGING — tag number format, loop ID scheme
      EQUIPMENT_NUMBERING— equipment tag format and numbering convention
      INLINE_EQUIPMENT   — strainers, restrictions, rupture disks, silencers, etc.

    Designed to be fully queryable per-project for cross-checking P&ID drawings.
    All flexible attributes go in `attributes` JSONField.

    Schema of `attributes` (examples per category):
      control_valve:      { actuator_type, fail_action, body_type, size_range, cv_material }
      manual_valve:       { body_type, connection_type, end_connection, size_range, material }
      instrument:         { variable, function_code, isa_prefix, measurement_range, signal_type }
      instrument_tagging: { format, example, fields: [{pos, name, description}], numbering_basis }
      equipment_numbering:{ format, example, fields: [...], sequence_reset_basis }
      inline_equipment:   { type, connection_type, size, material, rating }
    """

    # Soft-coded category choices — extend freely, value is stored in DB
    class Category(models.TextChoices):
        CONTROL_VALVE       = 'control_valve',       'Control Valves'
        MANUAL_VALVE        = 'manual_valve',         'Manual Valves'
        INSTRUMENT          = 'instrument',           'Instruments'
        INSTRUMENT_TAGGING  = 'instrument_tagging',   'Instrument Tagging'
        EQUIPMENT_NUMBERING = 'equipment_numbering',  'Equipment Numbering'
        INLINE_EQUIPMENT    = 'inline_equipment',     'In-Line Equipment'

    class Source(models.TextChoices):
        AI_EXTRACTION = 'ai_extraction', 'AI Extraction'
        TEXT_PARSE    = 'text_parse',    'Text Parse'
        MANUAL        = 'manual',        'Manual Entry'

    symbol_id    = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    project = models.ForeignKey(
        PIDVProject,
        on_delete=models.CASCADE,
        related_name='instrument_symbols',
    )
    legend_sheet = models.ForeignKey(
        PIDVLegendSheet,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='instrument_symbols',
    )

    symbol_code      = models.CharField(max_length=100, db_index=True, help_text='e.g. HV, FIC, E-100, ZV')
    description      = models.TextField(help_text='Human-readable description from legend sheet')
    category         = models.CharField(max_length=30, choices=Category.choices, db_index=True)
    symbol_type      = models.CharField(max_length=100, blank=True, help_text='e.g. ball_valve, diff_pressure_transmitter')
    drawing_standard = models.CharField(max_length=100, blank=True, default='ISA 5.1', help_text='e.g. ISA 5.1, IEC 62424')

    # Flexible per-category attributes
    attributes = models.JSONField(
        default=dict, blank=True,
        help_text='Flexible per-category attributes (see docstring for schema)',
    )

    source = models.CharField(max_length=20, choices=Source.choices, default=Source.AI_EXTRACTION)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pidv_instrument_symbols'
        ordering = ['category', 'symbol_code']
        indexes  = [
            models.Index(fields=['project', 'category']),
            models.Index(fields=['symbol_id']),
            models.Index(fields=['symbol_code']),
        ]
        unique_together = [('project', 'symbol_code', 'category')]

    def __str__(self):
        return f'[{self.get_category_display()}] {self.symbol_code} — {self.description[:60]}'
