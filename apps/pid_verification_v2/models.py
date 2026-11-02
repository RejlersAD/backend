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
    # SOFT-CODED: Use project.id (UUID) as project slug
    project_slug = (
        str(instance.project.id) if instance.project_id else 'unassigned'
    )
    return f'pid_verification_v2/projects/{project_slug}/uploads/{instance.document_id}/{filename}'


def _report_path(instance, filename):
    # SOFT-CODED: Use project.id (UUID) as project slug
    doc_id = getattr(instance, 'document_id', 'unknown')
    project_slug = (
        str(instance.project.id) if instance.project_id else 'unassigned'
    )
    return f'pid_verification_v2/projects/{project_slug}/reports/{doc_id}/{filename}'


def _legend_upload_path(instance, filename):
    """Storage path for legend sheet files — scoped to project or 'global'."""
    # SOFT-CODED: Use project.id (UUID) as project slug
    project_slug = (
        str(instance.project.id) if instance.project_id else 'global'
    )
    return f'pid_verification_v2/projects/{project_slug}/legends/{instance.legend_id}/{filename}'


def _reference_data_upload_path(instance, filename):
    """Storage path for reference data files (line list, equipment list, instrument index)."""
    # SOFT-CODED: Use project.id (UUID) as project slug
    project_slug = (
        str(instance.project.id) if instance.project_id else 'global'
    )
    data_type = instance.data_type  # 'line_list', 'equipment_list', 'instrument_index', 'legend_pid', 'checklist'
    return f'pid_verification_v2/projects/{project_slug}/reference_data/{data_type}/{instance.reference_id}/{filename}'


# ---------------------------------------------------------------------------
# Project  (top-level grouping)
# ---------------------------------------------------------------------------

class PIDVProject(models.Model):
    """Groups multiple P&ID documents under one project."""

    # SOFT-CODED: Database schema mapping
    # Matches actual migration schema - BigAutoField PK + UUID project_id
    project_id   = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    project_name = models.CharField(max_length=255)
    description  = models.TextField(blank=True)
    
    # SOFT-CODED: Legend knowledge fields (per-project legend extraction)
    legend_knowledge_data = models.JSONField(
        null=True, 
        blank=True,
        help_text='Extracted legend prefixes specific to this project.'
    )
    legend_built_at = models.DateTimeField(null=True, blank=True)

    # SOFT-CODED: Generic per-project metadata store (e.g. encrypted BYOK API keys).
    # Keeping this as a single JSONField avoids one-off columns for every new
    # project-scoped setting — new keys can be nested under it without a migration.
    metadata = models.JSONField(default=dict, blank=True)

    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,  # Match migration schema
        null=True,
        blank=True,
        related_name='pidv2_v_projects',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pidv2_projects'
        ordering  = ['-created_at']

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

    # Drawing classification (spec B: user-set Type select on the project
    # workspace; drives the PFD / P&ID filter chips and set grouping)
    class DrawingType(models.TextChoices):
        PFD = 'PFD', 'PFD'
        PID = 'P&ID', 'P&ID'
        UFD = 'UFD', 'UFD'
    drawing_type  = models.CharField(max_length=10, choices=DrawingType.choices, blank=True, default='')

    # Owner
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pidv2_v_documents'
    )

    # Exports (filled after processing)
    excel_s3_url = models.CharField(max_length=1024, blank=True)
    pdf_s3_url   = models.CharField(max_length=1024, blank=True)

    # Step 6 sign-off names (written to C5/F5 of the checklist report).
    reviewed_by = models.CharField(max_length=255, blank=True)
    approved_by = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pidv2_documents'
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
        db_table = 'pidv2_drawings'
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
        # 'query' — notes/HOLDs requiring engineer confirmation rather than
        # errors (introduced 29 Oct 2026 for RED-001 semantics). Choices are
        # Python-level only (column is varchar(10)), so no migration needed.
        QUERY    = 'query',    'Query'

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
    # Link back to the extraction item this finding is about (engine prompt
    # §3/§7d): entity_id + bbox (percent of sheet) for the overlay.
    entity_id       = models.BigIntegerField(null=True, blank=True)
    bbox            = models.JSONField(null=True, blank=True)
    # Report metadata for the AI-PID-02 comment register (spec §2.5):
    # {sheet_label, grid, tag, finding_text, reference, suggested_correction,
    #  confidence, ai_result, checklist_items} — filled at finalize.
    report_meta     = models.JSONField(default=dict, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pidv2_findings'
        ordering = ['drawing', 'sl_no']
        indexes  = [
            models.Index(fields=['drawing', 'sl_no']),
            models.Index(fields=['severity']),
            models.Index(fields=['category']),
        ]

    def __str__(self):
        return f'[{self.sl_no}] {self.category}: {self.issue_observed[:60]}'


# ---------------------------------------------------------------------------
# PIDVExtractionItem  (per-document extraction table — engine prompt §4)
# ---------------------------------------------------------------------------

class PIDVExtractionItem(models.Model):
    """
    One extracted item (line label, instrument, equipment, off-page
    connector, note) of a processed P&ID document.

    Populated during the normal pipeline run from the page's extraction
    result and drawing metadata (line_tags / instruments / equipment /
    continuation_refs / notes). Only what the reader actually extracts is
    stored — fields with no extracted value stay null/empty, never
    fabricated.

    `parsed` schema per kind:
      lines       : {size, system, fluid_code, serial, piping_class,
                     insulation, design_cases: [{no, p_barg, t_c, stc}],
                     legend: {field_key: {code, description}}}
      instruments : {tag, function_letters, loop, hookup_type}
      equipment   : {tag, code, description, data_block}
      connectors  : {drawing_no, connector_no, direction, service,
                     in_register}
      notes       : {number, text, referenced_at: [], deleted}
    """

    class Kind(models.TextChoices):
        LINES       = 'lines',       'Line Labels'
        INSTRUMENTS = 'instruments', 'Instruments'
        EQUIPMENT   = 'equipment',   'Equipment'
        CONNECTORS  = 'connectors',  'Off-Page / C&E Connectors'
        NOTES       = 'notes',       'Notes'

    class Source(models.TextChoices):
        VECTOR = 'vector', 'Vector (PDF text layer)'
        OCR    = 'ocr',    'OCR'

    document     = models.ForeignKey(
        PIDVDocument, on_delete=models.CASCADE, related_name='extraction_items'
    )
    kind         = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    text         = models.TextField(help_text='Text as read from the sheet')
    parsed       = models.JSONField(default=dict, blank=True)
    page         = models.PositiveSmallIntegerField(default=0)

    # Bounding box in % of sheet width/height (x0, y0, x1, y1); null when
    # the reader produced no coordinates.
    bbox         = models.JSONField(null=True, blank=True)
    # How many times this exact (normalized) text appears on the drawing.
    labels       = models.PositiveIntegerField(default=1)

    flag         = models.CharField(max_length=120, null=True, blank=True)
    finding_ids  = models.JSONField(default=list, blank=True)

    source       = models.CharField(max_length=10, choices=Source.choices, default=Source.VECTOR)
    confidence   = models.FloatField(default=1.0)

    confirmed    = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pidv2_extraction_confirmations',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pidv2_extraction_items'
        ordering = ['document', 'kind', 'page', 'id']
        indexes  = [
            models.Index(fields=['document', 'kind']),
        ]

    def __str__(self):
        return f'{self.kind}: {self.text[:60]} (doc={self.document_id})'


# ---------------------------------------------------------------------------
# PIDVChecklistOverride  (engineer overrides of PRO-001 point results)
# ---------------------------------------------------------------------------

class PIDVChecklistOverride(models.Model):
    """Engineer's manual result for one checklist point of one document.

    Automatic/legend evaluations are computed live by
    services/checklist_pro001.evaluate_checklist(); a row here overrides
    the computed result (PATCH endpoint)."""

    document   = models.ForeignKey(
        PIDVDocument, on_delete=models.CASCADE, related_name='checklist_overrides'
    )
    point_id   = models.PositiveSmallIntegerField()
    result     = models.CharField(max_length=20)
    comment    = models.TextField(blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pidv2_checklist_overrides'
        unique_together = [('document', 'point_id')]


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
        related_name='pidv2_legend_sheets',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pidv2_legend_sheets'
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
        db_table = 'pidv2_instrument_symbols'
        ordering = ['category', 'symbol_code']
        indexes  = [
            models.Index(fields=['project', 'category']),
            models.Index(fields=['symbol_id']),
            models.Index(fields=['symbol_code']),
        ]
        unique_together = [('project', 'symbol_code', 'category')]

    def __str__(self):
        return f'[{self.get_category_display()}] {self.symbol_code} — {self.description[:60]}'


# ---------------------------------------------------------------------------
# PIDVReferenceData  (line list, equipment list, instrument index uploads)
# ---------------------------------------------------------------------------

class PIDVReferenceData(models.Model):
    """
    Stores reference data files uploaded for cross-checking P&ID drawings.
    
    Supported types:
      - line_list         : Piping line list (Excel/CSV/PDF)
      - equipment_list    : Equipment register (Excel/CSV/PDF)
      - instrument_index  : Instrument index (Excel/CSV/PDF)
    
    Files are parsed (if Excel/CSV) or stored as-is (PDF) for manual reference.
    Parsed data is stored in `parsed_data` JSONField for querying.
    """
    
    class DataType(models.TextChoices):
        LINE_LIST         = 'line_list',         'Line List'
        EQUIPMENT_LIST    = 'equipment_list',    'Equipment List'
        INSTRUMENT_INDEX  = 'instrument_index',  'Instrument Index'
        LEGEND_PID        = 'legend_pid',        'Legend Sheets (P&ID)'
        CHECKLIST         = 'checklist',         'Client Checklist'
    
    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED  = 'completed',  'Completed'
        FAILED     = 'failed',     'Failed'
    
    reference_id  = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
    # Owning project
    project = models.ForeignKey(
        PIDVProject,
        on_delete=models.CASCADE,
        related_name='reference_data',
    )
    
    data_type     = models.CharField(max_length=30, choices=DataType.choices, db_index=True)
    file_name     = models.CharField(max_length=512)
    original_file = models.FileField(
        upload_to=_reference_data_upload_path,
        max_length=500,
        null=True, blank=True,
    )
    
    # S3 path or presigned URL — populated after upload
    s3_path       = models.CharField(max_length=1024, blank=True)
    
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    
    # Parsed data from Excel/CSV files
    # Schema examples:
    #   line_list:        [{ line_number, fluid_code, size, spec, from, to, ... }]
    #   equipment_list:   [{ tag, description, type, size, material, ... }]
    #   instrument_index: [{ tag, service, type, range, location, ... }]
    parsed_data = models.JSONField(
        null=True, blank=True,
        help_text='Parsed tabular data from Excel/CSV files',
    )
    
    # Metadata: row count, column headers, file size
    metadata = models.JSONField(
        default=dict, blank=True,
        help_text='File metadata: row_count, columns, file_size_bytes',
    )
    
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pidv2_reference_data',
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pidv2_reference_data'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['reference_id']),
            models.Index(fields=['project', 'data_type']),
        ]
    
    def __str__(self):
        return f'{self.get_data_type_display()}: {self.file_name} [{self.status}]'


# ---------------------------------------------------------------------------
# MULTI-LAYER EXTRACTION MODELS
# ---------------------------------------------------------------------------

def _extraction_result_upload_path(instance, filename):
    """Storage path for extraction result JSON files"""
    project_slug = str(instance.project.project_id) if instance.project_id else 'unassigned'
    file_type = instance.file_type or 'unknown'
    return f'pid_verification_v2/projects/{project_slug}/extractions/{file_type}/{instance.extraction_id}/{filename}'


class PIDVExtractionResult(models.Model):
    """
    Stores multi-layer extraction results for one uploaded file.
    
    Extraction Layers:
      Layer 1: Free OCR (Tesseract, PyMuPDF, pdfplumber, EasyOCR, PaddleOCR)
      Layer 2: AI Enhancement with BYOK (OpenAI Vision, Claude, Gemini)
      Layer 3: Cross-file comparison and validation
    
    JSON Schema of `extraction_data`:
    {
        "file_info": {
            "filename": str,
            "page_count": int,
            "file_size_bytes": int,
            "file_hash": str (SHA-256)
        },
        "extraction_config": {
            "mode": "fast" | "balanced" | "deep" | "vision_only",
            "layer1_engines": ["tesseract", "pymupdf", "pdfplumber"],
            "layer2_engines": ["easyocr", "paddleocr"],
            "layer3_provider": "openai" | "claude" | "gemini" | null,
            "user_api_key_used": bool
        },
        "aggregated_data": {
            "equipment_tags": [{"tag": str, "page": int, "confidence": float, "bbox": [x,y,w,h]}],
            "line_numbers": [...],
            "symbols": [...],
            "notes": [...],
            "tables": [...] (for Equipment List, Line List, PMS)
        },
        "per_page_results": [
            {
                "page_num": int,
                "layer1_ocr": { "engine": str, "text": str, "confidence": float, "duration_sec": float },
                "layer2_fallback": { "engine": str, "triggered": bool, "reason": str, ... },
                "layer3_vision": { "provider": str, "used": bool, "cost_usd": float, ... },
                "extracted_items": { "equipment_tags": [...], "line_numbers": [...], ... },
                "quality_metrics": { "avg_confidence": float, "items_found": int, "ocr_weak_regions": [...] }
            }
        ],
        "cost_breakdown": {
            "layer1_cost_usd": 0.0,
            "layer2_cost_usd": 0.0,
            "layer3_cost_usd": float,
            "total_cost_usd": float
        },
        "processing_time": {
            "layer1_seconds": float,
            "layer2_seconds": float,
            "layer3_seconds": float,
            "total_seconds": float
        }
    }
    """
    
    class ExtractionMode(models.TextChoices):
        FAST        = 'fast',        'Fast (OCR-only)'
        BALANCED    = 'balanced',    'Balanced (Smart Vision Fallback)'
        DEEP        = 'deep',        'Deep (Full Vision Analysis)'
        VISION_ONLY = 'vision_only', 'Vision-Only (No OCR)'
    
    class Status(models.TextChoices):
        PENDING     = 'pending',     'Pending'
        LAYER1      = 'layer1',      'Layer 1: OCR Processing'
        LAYER2      = 'layer2',      'Layer 2: ML OCR Fallback'
        LAYER3      = 'layer3',      'Layer 3: Vision AI Enhancement'
        COMPLETED   = 'completed',   'Completed'
        FAILED      = 'failed',      'Failed'
    
    extraction_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
    project = models.ForeignKey(
        PIDVProject,
        on_delete=models.CASCADE,
        related_name='extraction_results',
    )
    
    # Link to document if this extraction is for a PIDVDocument
    document = models.ForeignKey(
        PIDVDocument,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='extractions',
    )
    
    # Or link to legend sheet
    legend_sheet = models.ForeignKey(
        PIDVLegendSheet,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='extractions',
    )
    
    # Or link to reference data
    reference_data = models.ForeignKey(
        'PIDVReferenceData',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='extractions',
    )
    
    file_type = models.CharField(
        max_length=50,
        help_text='pid_drawing, legend_sheet, equipment_list, line_list, pms',
    )
    file_name = models.CharField(max_length=512)
    page_count = models.PositiveSmallIntegerField(default=1)
    
    # Extraction configuration
    extraction_mode = models.CharField(
        max_length=20,
        choices=ExtractionMode.choices,
        default=ExtractionMode.BALANCED,
    )
    user_api_key_used = models.BooleanField(
        default=False,
        help_text='True if user provided BYOK API key for Layer 3'
    )
    vision_provider = models.CharField(
        max_length=20,
        blank=True,
        help_text='openai, claude, gemini'
    )
    
    # Extraction results (full JSON structure)
    extraction_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Complete extraction results (see docstring for schema)'
    )
    
    # JSON file export (for download/comparison)
    json_export_file = models.FileField(
        upload_to=_extraction_result_upload_path,
        null=True, blank=True,
        max_length=500,
    )
    json_s3_url = models.CharField(max_length=1024, blank=True)
    
    # Processing status
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(blank=True)
    
    # Cost and performance tracking
    total_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0.0000,
        help_text='Total cost of extraction (Layer 3 Vision AI only)'
    )
    processing_time_seconds = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text='Total processing time'
    )
    
    extracted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pidv2_extractions',
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'pidv2_extraction_results'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['extraction_id']),
            models.Index(fields=['project', 'file_type']),
            models.Index(fields=['status']),
            models.Index(fields=['extraction_mode']),
        ]
    
    def __str__(self):
        return f'Extraction: {self.file_name} [{self.extraction_mode}] - {self.status}'


class PIDVExtractionPage(models.Model):
    """
    Stores per-page extraction details for granular analysis.
    
    One PIDVExtractionResult has many PIDVExtractionPages.
    """
    
    page_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
    extraction_result = models.ForeignKey(
        PIDVExtractionResult,
        on_delete=models.CASCADE,
        related_name='pages',
    )
    
    page_number = models.PositiveSmallIntegerField()
    
    # Per-page extraction data
    layer1_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Layer 1 OCR results: { engine: str, text: str, confidence: float, items: [...] }'
    )
    layer2_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Layer 2 ML OCR fallback results (if triggered)'
    )
    layer3_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Layer 3 Vision AI results (if used)'
    )
    
    # Aggregated page-level data
    extracted_items = models.JSONField(
        default=dict,
        blank=True,
        help_text='Merged results: { equipment_tags: [...], line_numbers: [...], symbols: [...], notes: [...] }'
    )
    
    # Quality metrics
    avg_confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text='Average OCR confidence for this page (0-100)'
    )
    items_found = models.PositiveSmallIntegerField(
        default=0,
        help_text='Total items extracted on this page'
    )
    
    # Image snapshot with annotations (optional)
    annotated_image = models.FileField(
        upload_to=_extraction_result_upload_path,
        null=True, blank=True,
        max_length=500,
        help_text='Processed image with bounding boxes drawn'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'pidv2_extraction_pages'
        ordering = ['extraction_result', 'page_number']
        unique_together = [('extraction_result', 'page_number')]
        indexes = [
            models.Index(fields=['extraction_result', 'page_number']),
            models.Index(fields=['avg_confidence']),
        ]
    
    def __str__(self):
        return f'Page {self.page_number} of {self.extraction_result.file_name}'


class PIDVComparisonFinding(models.Model):
    """
    Stores findings from cross-file comparison and validation.
    
    Example findings:
      - Equipment tag "P-101" found in PID but missing from Equipment List
      - Line number "2\"-CS-1001-A1" in PID doesn't match Line List format
      - Symbol in PID not found in Legend Sheet
      - Duplicate tag "HV-205" in multiple locations on same PID
      - Material spec mismatch between Line List and PMS
    """
    
    class Severity(models.TextChoices):
        CRITICAL = 'critical', 'Critical'
        HIGH     = 'high',     'High'
        MEDIUM   = 'medium',   'Medium'
        LOW      = 'low',      'Low'
    
    class FindingType(models.TextChoices):
        TAG_CONSISTENCY      = 'tag_consistency',      'Tag Consistency'
        LINE_CONSISTENCY     = 'line_consistency',     'Line Number Consistency'
        SYMBOL_LEGEND_MATCH  = 'symbol_legend_match',  'Symbol-Legend Match'
        SPEC_MATERIAL_MATCH  = 'spec_material_match',  'Spec/Material Match'
        DUPLICATE_TAG        = 'duplicate_tag',        'Duplicate Tag'
        MISSING_ITEM         = 'missing_item',         'Missing Item'
        FORMAT_ERROR         = 'format_error',         'Format Error'
        DATA_QUALITY         = 'data_quality',         'Data Quality Issue'
    
    class Status(models.TextChoices):
        OPEN     = 'open',     'Open'
        REVIEWED = 'reviewed', 'Reviewed'
        RESOLVED = 'resolved', 'Resolved'
        IGNORED  = 'ignored',  'Ignored'
    
    finding_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
    project = models.ForeignKey(
        PIDVProject,
        on_delete=models.CASCADE,
        related_name='comparison_findings',
    )
    
    # Link to source extraction results (can involve multiple files)
    source_extractions = models.ManyToManyField(
        PIDVExtractionResult,
        related_name='comparison_findings',
        help_text='Extraction results involved in this finding'
    )
    
    finding_type = models.CharField(
        max_length=30,
        choices=FindingType.choices,
        db_index=True,
    )
    severity = models.CharField(
        max_length=10,
        choices=Severity.choices,
        default=Severity.MEDIUM,
        db_index=True,
    )
    
    # Finding details
    title = models.CharField(max_length=255)
    description = models.TextField(help_text='Detailed description of the finding')
    evidence = models.JSONField(
        default=dict,
        blank=True,
        help_text='Supporting data: { file1: {...}, file2: {...}, comparison_data: {...} }'
    )
    
    # AI-generated suggestion (if AI comparison enabled)
    ai_suggestion = models.TextField(
        blank=True,
        help_text='AI-generated correction or recommendation'
    )
    ai_confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text='AI confidence score (0-100)'
    )
    
    # Location reference
    location_info = models.JSONField(
        default=dict,
        blank=True,
        help_text='Where the issue was found: { file: str, page: int, bbox: [...], item: str }'
    )
    
    # Review tracking
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_pidv2_findings',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pidv2_comparison_findings'
        ordering = ['-severity', '-created_at']
        indexes = [
            models.Index(fields=['finding_id']),
            models.Index(fields=['project', 'finding_type']),
            models.Index(fields=['severity', 'status']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f'[{self.severity}] {self.title}'


# ---------------------------------------------------------------------------
# PIDVAICheckRun  (one AI check run per project)
# ---------------------------------------------------------------------------

class PIDVAICheckRun(models.Model):
    """
    Stores the results of one AI-powered check run on a project.
    
    Workflow:
      1. User triggers "Run AI Checks" on project with uploaded P&IDs and reference data
      2. System extracts equipment, lines, instruments from P&IDs using vision APIs
      3. System runs AUTO checks (two-way reconciliation vs reference data)
      4. System generates ASSIST check findings (AI extracts, engineer reviews)
      5. Results stored here with check_results JSONField
    
    Schema of check_results:
      [
        {
          "check_id": "AUTO_001",
          "name": "Line List Two-Way Reconciliation",
          "result": "Pass" | "Warning" | "Fail" | "Not Checked",
          "confidence": "High" | "Medium" | "Low" | "",
          "finding": "Description of result",
          "severity": "critical" | "major" | "minor",
          "details": { orphans: [...], missing: [...], ... },
          "category": "AUTO" | "ASSIST" | "HUMAN"
        }
      ]
    """
    
    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        EXTRACTING = 'extracting', 'Extracting P&ID Elements'
        CHECKING   = 'checking',   'Running Checks'
        COMPLETED  = 'completed',  'Completed'
        FAILED     = 'failed',     'Failed'
    
    run_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
    # Owning project
    project = models.ForeignKey(
        PIDVProject,
        on_delete=models.CASCADE,
        related_name='ai_check_runs',
    )
    
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    
    # Analysis mode used (standard, enhanced_openai, deep_claude, hybrid)
    analysis_mode = models.CharField(max_length=30, default='hybrid')
    
    # Extracted data from P&IDs (equipment, lines, instruments)
    # Schema:
    #   {
    #     "equipment": [{ tag, type, service, sheet, confidence }],
    #     "lines": [{ line_number, size, spec, from, to, sheet, confidence }],
    #     "instruments": [{ tag, type, location, sheet, confidence }],
    #     "overview": { sheet_count, equipment_count, line_count, instrument_count }
    #   }
    extracted_data = models.JSONField(
        null=True, blank=True,
        help_text='All extracted P&ID elements from vision APIs',
    )
    
    # Check results (AUTO + ASSIST + HUMAN)
    # See docstring above for schema
    check_results = models.JSONField(
        default=list, blank=True,
        help_text='Results of all executed checks',
    )
    
    # Summary statistics
    # { total_checks, auto_count, assist_count, human_count, pass_count, fail_count, warning_count }
    summary_stats = models.JSONField(
        default=dict, blank=True,
        help_text='Summary statistics of check run',
    )
    
    # Processing metadata
    # { sheets_processed, api_calls_made, total_cost_usd, processing_time_seconds }
    processing_metadata = models.JSONField(
        default=dict, blank=True,
        help_text='Processing metrics and costs',
    )
    
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='pidv2_ai_check_runs',
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'pidv2_ai_check_runs'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['run_id']),
            models.Index(fields=['project', 'status']),
        ]

    def __str__(self):
        return f'AI Check Run {self.run_id} [{self.status}] - {self.project.project_name}'


class PIDVTagIndex(models.Model):
    """
    One row per tag OCCURRENCE (a tag appearing on a specific page), not
    one row per unique tag — the same tag on 3 pages is 3 rows here. Feeds
    apps.pid_verification_v2.services.cross_reference:
    group_multi_page_tags() reads these rows to find tags appearing on 2+
    distinct pages.

    Field names/types/Meta here intentionally match migration 0006
    (0006_pidvtagindex.py) exactly — that migration already existed before
    this model was written; this is the model catching up to it, not the
    other way around. Confirmed via `makemigrations --check`: 0 drift.
    """
    page_number = models.PositiveSmallIntegerField(
        help_text='page_index of the source PIDVDrawing',
    )
    tag = models.CharField(
        max_length=255, db_index=True,
        help_text="normalize_tag()'d form — the matching key",
    )
    raw_tag = models.CharField(
        max_length=255,
        help_text='Original as-extracted tag text',
    )
    tag_type = models.CharField(
        max_length=20,
        choices=[
            ('line', 'Line'),
            ('equipment', 'Equipment'),
            ('instrument', 'Instrument'),
            ('valve', 'Valve'),
        ],
    )
    document = models.ForeignKey(
        PIDVDocument, on_delete=models.CASCADE, related_name='tag_index',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pidv2_tag_index'
        indexes = [
            models.Index(fields=['document', 'tag'], name='pidv2_tag_i_documen_86acd5_idx'),
            models.Index(fields=['document', 'page_number'], name='pidv2_tag_i_documen_d1d0fd_idx'),
        ]

    def __str__(self):
        return f'{self.tag} (p{self.page_number}) — {self.document_id}'


# ---------------------------------------------------------------------------
# Sheet  (one PDF page = one P&ID — multi-file and multi-sheet uploads)
# ---------------------------------------------------------------------------

class PIDVSheet(models.Model):
    """One P&ID sheet. Created one per PDF page after the title block is
    read (sync in tasks.py; backfilled for existing documents by data
    migration 0014). Pages with no title block get is_drawing=False and are
    skipped by all checks. Per-sheet extraction, findings and scene3d stay
    keyed by (document, page_index) — this row adds project-wide identity:
    drawing number, revision, supersession.
    """

    project      = models.ForeignKey(
        PIDVProject, on_delete=models.CASCADE, related_name='sheets',
        null=True, blank=True,
    )
    document     = models.ForeignKey(
        PIDVDocument, on_delete=models.CASCADE, related_name='sheets',
    )
    page_index   = models.PositiveSmallIntegerField(default=0)
    drawing_no   = models.CharField(max_length=255, blank=True, db_index=True)
    # False when only a contractor number (e.g. 'AA-E3-P-FP', '&AA-…') was
    # found — the client number pattern is preferred; the UI shows
    # "Drawing number to confirm".
    drawing_no_confirmed = models.BooleanField(default=True)
    rev          = models.CharField(max_length=20, blank=True)
    title        = models.CharField(max_length=512, blank=True)
    status       = models.CharField(max_length=20, blank=True)  # mirrors the document's
    is_drawing   = models.BooleanField(default=True)
    superseded_by = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='supersedes',
    )
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pidv2_sheets'
        unique_together = [('document', 'page_index')]
        ordering = ['document', 'page_index']
        indexes = [
            models.Index(fields=['project', 'drawing_no']),
        ]

    def __str__(self):
        return f'{self.drawing_no or "?"} (doc={self.document_id} p{self.page_index})'


# ---------------------------------------------------------------------------
# CrossFinding  (project-wide checks over current sheets — cross_checks.py)
# ---------------------------------------------------------------------------

class PIDVCrossFinding(models.Model):
    """One cross-drawing finding (X1…Xn per run): XCON-001 connector
    return, XLINE-001 same line different data, XTAG-001 same tag different
    type/service, XREV-001 revision drift. `evidence` carries
    {sheet_id, entity_id, bbox} per sheet so the UI can open each drawing
    at the spot."""

    class Kind(models.TextChoices):
        ERROR = 'error', 'Error'
        QUERY = 'query', 'Query'
        MINOR = 'minor', 'Minor'

    project   = models.ForeignKey(
        PIDVProject, on_delete=models.CASCADE, related_name='cross_findings',
    )
    number    = models.PositiveIntegerField(help_text='X<number> within the run')
    code      = models.CharField(max_length=20)   # XCON-001 | XLINE-001 | XTAG-001 | XREV-001
    kind      = models.CharField(max_length=10, choices=Kind.choices, default=Kind.QUERY)
    title     = models.CharField(max_length=512)
    detail    = models.TextField(blank=True)
    sheets    = models.JSONField(default=list, blank=True)    # [sheet_id, …]
    evidence  = models.JSONField(default=list, blank=True)    # [{sheet_id, entity_id, bbox}]
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pidv2_cross_findings'
        ordering = ['number']

    def __str__(self):
        return f'X{self.number} {self.code}: {self.title[:60]}'


# ---------------------------------------------------------------------------
# Checklist template + results (AI_PID_Review_Checklist_02 — spec §2)
# ---------------------------------------------------------------------------

class PIDVChecklistTemplate(models.Model):
    """An imported AI-PID-02-format workbook. The original .xlsx is kept in
    `source_file` (S3) and the export FILLS it — the download is identical
    to the engineer's template apart from the cells we write."""

    code        = models.CharField(max_length=40, db_index=True)   # 'AI-PID-02'
    title       = models.CharField(max_length=255, blank=True)
    version     = models.PositiveIntegerField(default=1)
    source_file = models.FileField(upload_to='pidv2/checklist_templates/', max_length=500)
    is_default  = models.BooleanField(default=False)
    item_count  = models.PositiveIntegerField(default=0)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pidv2_checklist_templates'
        unique_together = [('code', 'version')]
        ordering = ['code', '-version']

    def __str__(self):
        return f'{self.code} v{self.version} ({self.item_count} items)'


class PIDVChecklistItem(models.Model):
    """One item row of the template (rows 8–148 of 'AI P&ID Checklist').
    `xlsx_row` anchors the export: a template whose column A changed raises
    TemplateChanged (HTTP 409) instead of writing to the wrong row."""

    template    = models.ForeignKey(
        PIDVChecklistTemplate, on_delete=models.CASCADE, related_name='items',
    )
    no          = models.CharField(max_length=10)          # '0.1' … '13.4'
    section_no  = models.CharField(max_length=4)           # '0' … '13'
    section_title = models.CharField(max_length=255)
    sub_heading = models.CharField(max_length=60, blank=True)  # 'Piping' | 'Valves' (section 9)
    check_point = models.TextField()
    mode        = models.CharField(max_length=20)          # Automated | AI-Assisted | Engineer-Led
    ai_task     = models.TextField(blank=True)             # col D
    ref_inputs  = models.TextField(blank=True)             # col E
    evidence_required = models.TextField(blank=True)       # col F
    severity    = models.CharField(max_length=10)          # Critical | Major | Minor
    change_vs_original = models.CharField(max_length=60, blank=True)  # col L
    xlsx_row    = models.PositiveIntegerField()
    order       = models.PositiveIntegerField()

    class Meta:
        db_table = 'pidv2_checklist_items'
        unique_together = [('template', 'no')]
        ordering = ['order']

    def __str__(self):
        return f'{self.no} {self.check_point[:50]}'


class PIDVChecklistResult(models.Model):
    """Engineer-side state (and last AI snapshot) for one item of one sheet
    of one document. The AI result columns are recomputed live on every
    GET (checklist_ai02.evaluate_sheet) from the current inputs and
    findings — this row is authoritative for applicability overrides,
    engineer verification and remarks only."""

    APPLICABILITY = [('Y', 'Y'), ('N', 'N'), ('NA', 'NA')]
    AI_RESULTS = [('OK', 'OK'), ('Non-Conformance', 'Non-Conformance'),
                  ('Query', 'Query'), ('Not Verifiable', 'Not Verifiable')]
    VERIFICATION = [('Agree', 'Agree'), ('Disagree', 'Disagree'), ('Modified', 'Modified')]

    document    = models.ForeignKey(
        PIDVDocument, on_delete=models.CASCADE, related_name='checklist_results',
    )
    page        = models.PositiveSmallIntegerField(default=0)
    item        = models.ForeignKey(
        PIDVChecklistItem, on_delete=models.CASCADE, related_name='results',
    )
    applicability    = models.CharField(max_length=3, choices=APPLICABILITY, default='Y')
    applicability_by = models.CharField(max_length=10, default='ai')  # 'ai' | 'engineer'
    ai_result        = models.CharField(max_length=20, choices=AI_RESULTS, null=True, blank=True)
    evidence         = models.TextField(blank=True)
    finding_ids      = models.JSONField(default=list, blank=True)
    missing_inputs   = models.JSONField(default=list, blank=True)
    engineer_verification = models.CharField(max_length=10, choices=VERIFICATION, null=True, blank=True)
    remarks          = models.TextField(blank=True)
    verified_by      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pidv2_checklist_verifications',
    )
    verified_at      = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'pidv2_checklist_results'
        unique_together = [('document', 'page', 'item')]

    def __str__(self):
        return f'{self.item.no} @ doc {self.document_id} p{self.page}: {self.ai_result}'

