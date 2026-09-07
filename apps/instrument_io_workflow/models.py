"""
Lean data model — six tables:

    IOListProject             — project container for organizing documents
    IOListDocument             — one uploaded PDF (single revision)
    IOListExtractedComment     — rows from the Comments Resolution Sheet
    IOListExtractedRow         — rows from the structured IO table
    IOListLegendSheet          — one user-defined legend (tag format or code
                                  lookup) for one of the 19 legend sections
    IOListLegendSymbolImage    — an optional manually-uploaded reference
                                  picture for one symbol/code in a section

Multi-revision chain tracking is delegated to the existing CRS chain backend
(apps.crs.CRSRevisionChain). We only store an optional FK reference to it.
"""

import uuid

from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# Legend sections
#
# Every legend a user can define/activate falls into exactly one of 19
# sections. Each section is one of three *kinds*, used by
# services/legend_comparison.py + services/orchestrator.py to decide how an
# active legend in that section gets checked against extracted rows:
#
#   FORMAT sections     — define a tag *pattern* for the 'tag_number' field.
#                          All active format legends are OR'd together (a
#                          row's tag only needs to match ONE of them).
#   LOOKUP sections      — a code -> meaning table checked against one
#                          specific row field (e.g. signal_types -> 'signal_type').
#   SYMBOL LOOKUP sections — a code/name table checked against the
#                          Vision-populated 'symbol_type' field (P&ID
#                          drawing extraction only), OR'd together the same
#                          way format sections are.
#
# SUPPLEMENTARY_FORMAT_SECTIONS are the format sections beyond the primary
# instrument tag convention (instrument_index) — equipment/well/line tag
# conventions that populate their own row field (equipment_tag/line_tag)
# rather than 'tag_number' when the row comes from P&ID Vision extraction.
# ---------------------------------------------------------------------------

IO_LEGEND_SECTION_CHOICES = [
    ('equipment_register', 'Equipment Numbering'),
    ('instrument_index', 'Instrument Tagging'),
    ('valve_types', 'Manual Valves'),
    ('actuator_types', 'Actuator Types'),
    ('flow_instruments', 'Flow Instruments'),
    ('control_valves', 'Control Valves'),
    ('signal_line_types', 'Line Representation'),
    ('equipment_symbols', 'Equipment Symbols'),
    ('instrument_functions', 'Instrument Functions'),
    ('instrument_typical_letter', 'Instrument Typical Letter'),
    ('instrument_bubbles', 'Instrument Bubbles'),
    ('instrument_symbols', 'Instruments'),
    ('signal_types', 'Signal Types'),
    ('cabinet_locations', 'Cabinet/Panel Locations'),
    ('well_instrument_tagging', 'Well Instrument Tagging'),
    ('well_equipment_numbering', 'Well Equipment Numbering'),
    ('line_numbering', 'Line Numbering'),
    ('main_equipment', 'Main Equipment'),
    ('inline_equipment', 'In Line Equipment'),
]

IO_LEGEND_SECTION_LABELS = dict(IO_LEGEND_SECTION_CHOICES)

# Tag-format sections — checked (OR'd) against 'tag_number'.
IO_LEGEND_FORMAT_SECTIONS = {
    'instrument_index',
    'equipment_register',
    'well_instrument_tagging',
    'well_equipment_numbering',
    'line_numbering',
}

# Format sections beyond the primary instrument convention — populate their
# own field (equipment_tag/line_tag) on P&ID-Vision-sourced rows instead of
# 'tag_number'. See services/pid_vision_extractor.py:_row_from_tag_info.
IO_LEGEND_SUPPLEMENTARY_FORMAT_SECTIONS = {
    'equipment_register',
    'line_numbering',
    'well_instrument_tagging',
    'well_equipment_numbering',
}

# Code-lookup sections — {section: row field name} they validate.
IO_LEGEND_LOOKUP_SECTIONS = {
    'instrument_functions': 'instrument_type',
    'instrument_typical_letter': 'instrument_type',
    'signal_types': 'signal_type',
    'cabinet_locations': 'cabinet_location',
    'main_equipment': 'equipment_type',
    'inline_equipment': 'equipment_type',
    'flow_instruments': 'instrument_type',
}

# Symbol-shaped lookup sections — checked (OR'd) against the Vision-only
# 'symbol_type' field.
IO_LEGEND_SYMBOL_LOOKUP_SECTIONS = {
    'valve_types',
    'actuator_types',
    'control_valves',
    'equipment_symbols',
    'instrument_bubbles',
    'instrument_symbols',
    'signal_line_types',
}


def get_io_list_document_storage():
    """Return the configured I/O List storage backend.

    FileField accepts a storage instance or a callable, not a dotted-path
    string. Keeping this as a callable also lets Django serialize the field
    correctly in migrations and select the S3/local implementation at runtime.
    """
    from apps.core.storage_backends import IOListDocumentStorage
    return IOListDocumentStorage()


def io_legend_symbol_image_upload_path(instance, filename):
    """Storage path for a manually-uploaded legend symbol reference image."""
    return f'instrument_io_workflow/legend_symbols/{instance.image_id}/{filename}'


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

    # No document_count property here on purpose — IOListProjectViewSet.
    # get_queryset() always annotates document_count=Count('documents')
    # (one query for the whole list instead of one per project), and a
    # same-named @property with no setter collides with that annotation:
    # Django's queryset iteration does setattr(obj, 'document_count', ...)
    # to apply the annotated value, which raises AttributeError against a
    # property that only defines a getter. Real traceback hit live via
    # GET /projects/ — 500 "property 'document_count' of 'IOListProject'
    # object has no setter" — before this was removed.


class IOListDocument(models.Model):
    """A single uploaded Instrument IO List PDF (one revision)."""

    STATUS_CHOICES = [
        ('uploaded',  'Uploaded'),
        ('extracting','Extracting'),
        ('completed', 'Completed'),
        ('failed',    'Failed'),
    ]

    DOCUMENT_TYPE_CHOICES = [
        ('io_list',     'IO List'),
        ('pid_drawing', 'P&ID Drawing'),
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
        storage=get_io_list_document_storage,
    )
    pdf_sha256        = models.CharField(max_length=64, db_index=True)

    # Extraction state
    status            = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='uploaded',
    )
    extraction_stats  = models.JSONField(default=dict, blank=True)
    extraction_error  = models.TextField(blank=True, default='')
    legend_findings   = models.JSONField(default=list, blank=True)
    document_type     = models.CharField(
        max_length=20, choices=DOCUMENT_TYPE_CHOICES, default='io_list',
    )

    # Async (Celery chord) fan-out progress — page count of a P&ID Vision
    # extraction, surfaced to the frontend for a progress indicator.
    pages_processed   = models.IntegerField(default=0)
    pages_total       = models.IntegerField(default=0)

    # Finer-grained progress for the P&ID Vision path specifically — one
    # whole page can take a long time (VISION_PASSES calls, or
    # VISION_TILE_ROWS*VISION_TILE_COLS*VISION_PASSES calls in Thorough
    # Scan), during which pages_processed/pages_total alone sits frozen
    # (e.g. "0 of 2" for many minutes on a 2-page drawing) — a real,
    # reported symptom ("it showing 0% only, it should be dynamic also").
    # vision_calls_total is set once, at dispatch, from the actually
    # known call count (pages * calls-per-page for the chosen scan mode —
    # never guessed); vision_calls_done increments by exactly 1, real,
    # each time one individual Vision API call genuinely finishes
    # (tasks.py's process_pid_vision_page, via pid_vision_extractor's
    # on_call_complete callback) — never a fake tick. Stays 0/0 for a
    # regular I/O List table document, which has no such per-call
    # concept; GET .../status/ callers fall back to pages_processed/
    # pages_total when vision_calls_total is 0.
    vision_calls_done  = models.IntegerField(default=0)
    vision_calls_total = models.IntegerField(default=0)

    # Real, cumulative token usage across every Vision call made for this
    # run — summed straight off each API response's own usage object
    # (pid_vision_extractor._call_claude/_call_openai), never estimated.
    # 0 for a regular I/O List table document or the no-key local-OCR
    # fallback (neither makes a Vision API call).
    tokens_used_total = models.IntegerField(default=0)

    # Live row/comment counts for THIS run specifically — deliberately
    # separate from extracted_rows.count()/extracted_comments.count().
    # On a fresh upload those are equivalent (nothing existed before), but
    # on a RE-extract the document still has its PREVIOUS run's rows sitting
    # in the DB (persist_extraction only wipes and replaces them once, at
    # the very end — see that function) — a raw .count() during the run
    # would silently mix old + newly-provisional rows, showing a real but
    # misleading number (confirmed live: 46 -> 66 -> 46 across one
    # re-extract, the middle value being old+new combined). These two
    # counters instead increment by exactly how many rows/comments each
    # unit genuinely just persisted (tasks.py's
    # _persist_provisional_rows_and_comments), reset to 0 at dispatch —
    # accurate for both upload and re-extract alike.
    provisional_rows_count     = models.IntegerField(default=0)
    provisional_comments_count = models.IntegerField(default=0)

    # Wall-clock start of THIS extraction run — set once, right when
    # views.py hands off to dispatch_io_document_processing (create() and
    # re_extract() alike), never touched again by tasks.py's own
    # .update() calls (those never include this field, so it survives
    # untouched for the run's whole duration). Lets the frontend show a
    # genuine "time elapsed" / derive an ETA from real wall-clock deltas
    # instead of a client-side fake timer — created_at is wrong for this
    # on a re-extract (that reflects the ORIGINAL upload, not this run).
    extraction_started_at = models.DateTimeField(null=True, blank=True)

    # Real, backend-written processing phase — set at the exact points
    # tasks.py/orchestrator.py actually enter each stage (never guessed
    # client-side). GET .../status/ (views.py) surfaces this raw; the
    # frontend maps each value to display text, but the VALUE itself is
    # always genuine backend state, not a client-side timer/animation.
    PHASE_CHOICES = [
        ('', 'Not processing'),
        ('detecting_type', 'Detecting document type'),
        ('processing_pages', 'Processing pages'),
        ('linking_comments', 'Linking comments to tags'),
        ('validating_legend', 'Validating tags against legend'),
    ]
    current_phase     = models.CharField(
        max_length=20, choices=PHASE_CHOICES, blank=True, default='',
    )

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


class IOListLegendSheet(models.Model):
    """One user-defined legend for one of the 19 I/O List legend sections.

    'definition' holds the format/lookup shape:
        {separator, fields: [{key, label, regex, suffix?, optional?, lookup?}]}
    — matches apps.pid_checker_v2.PidCheckerV2LegendSheet's definition shape
    on purpose (a one-time data migration copied existing legends across
    unchanged), but this app never imports from pid_checker_v2 at runtime.

    Only one legend per (created_by, section) may be is_active=True at a
    time — enforced by the partial unique constraint below.
    """
    legend_id   = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True,
    )
    section     = models.CharField(
        max_length=32, choices=IO_LEGEND_SECTION_CHOICES,
        default='instrument_index',
    )
    name        = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    definition  = models.JSONField(default=dict)
    is_active   = models.BooleanField(default=False)
    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='io_list_legends',
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'instrument_io_workflow_legend_sheet'
        ordering = ['-updated_at']
        indexes  = [
            models.Index(fields=['created_by', 'section', '-updated_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=('created_by', 'section'),
                condition=models.Q(is_active=True),
                name='uniq_io_legend_active_per_user_section',
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f'{self.name} ({self.get_section_display()})'


class IOListLegendSymbolImage(models.Model):
    """A reference picture for one symbol/code within a legend section —
    e.g. what a particular valve-type symbol looks like on a drawing.
    Independent of any one IOListLegendSheet row so it survives edits/
    regeneration of the legend's JSON definition.

    Two sources of rows:
      - A user's own manual upload (is_default=False, created_by=that user).
      - A shared default (is_default=True, created_by=None) — the
        repo-committed static/io_list_default_symbols/ library, mirrored
        into this table by the seed_io_default_symbols management command
        (run automatically post-migrate — see apps.py) so the shared
        picture library survives independently of the static files on
        disk too. A user's own upload always takes priority over a
        default with the same (section, symbol_name) — see
        SymbolImagesListView.
    """
    image_id     = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True,
    )
    section      = models.CharField(max_length=32)
    symbol_name  = models.CharField(max_length=200)
    image_file   = models.ImageField(
        upload_to=io_legend_symbol_image_upload_path,
        max_length=500, blank=True, null=True,
    )
    content_type = models.CharField(max_length=100, default='image/png')
    is_default   = models.BooleanField(default=False)
    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='io_list_legend_symbol_images',
    )
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'instrument_io_workflow_legend_symbol_image'
        ordering = ['section', 'symbol_name']
        unique_together = [('created_by', 'section', 'symbol_name')]
        constraints = [
            models.UniqueConstraint(
                fields=['section', 'symbol_name'],
                condition=models.Q(is_default=True),
                name='uniq_io_default_symbol_per_section',
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f'{self.symbol_name} ({self.section})'
