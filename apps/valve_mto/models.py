"""
Valve MTO — Server-side persistence
====================================

Until this app existed, the Valve MTO workspace (frontend:
src/pages/Engineering/Piping/ValveMTO.jsx) was ENTIRELY client-persisted —
project headers and extracted rows lived only in browser localStorage
(`radai.valveMTO.state.v1`), so closing the browser (or switching
devices) silently lost a user's extracted/hand-corrected MTO data. This
app adds a real, durable, server-side copy alongside that (the frontend
keeps localStorage too, as an offline-friendly cache/backup — see its
own comment), so the data survives regardless of the browser.

Two models, mirroring the frontend's own project/row split
(src/config/valveMTO.config.js's VALVE_COLUMNS and
src/config/valveMTOProjects.js's project shape):

    ValveMTOProject — one saved "project" (a named MTO workspace, usually
    one per source P&ID/valve schedule).
    ValveMTORow — one valve/line row belonging to a project, in the
    canonical 17-field schema the extractor/frontend already use.

Does NOT touch the actual extraction pipeline (apps.pid_verification's
piping_valve_mto_view.py / services/piping_valve_mto_extractor.py) — this
app is pure storage; the frontend calls the existing extraction endpoint
first, then POSTs the resulting rows here to persist them.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class ValveMTOProject(models.Model):
    STATUS_EXTRACTING = 'extracting'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_EXTRACTING, 'Extracting'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project_name = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='valve_mto_projects',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # The PDF (or spreadsheet) the rows were originally extracted/imported
    # from — filename only, not a stored file (the extraction pipeline in
    # apps.pid_verification already handles the transient upload/job
    # lifecycle; duplicating file storage here isn't this app's job).
    source_pdf_name = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_EXTRACTING, db_index=True)

    class Meta:
        ordering = ['-updated_at', '-created_at']
        verbose_name = 'Valve MTO Project'
        verbose_name_plural = 'Valve MTO Projects'
        indexes = [
            models.Index(fields=['created_by', '-updated_at']),
        ]

    def __str__(self) -> str:
        return f'{self.project_name} [{self.status}] ({self.id})'


class ValveMTORow(models.Model):
    """One valve/line row — the canonical schema shared by the extractor
    (piping_valve_mto_extractor.py's ROW_KEYS) and the frontend table
    (valveMTO.config.js's VALVE_COLUMNS), so every field the extractor can
    populate has a real column here — nothing gets dropped on save. `tab`
    records which of the frontend's ISLAND/FIELD/COMBINED tabs this row
    was showing under (mirrors `area` for COMBINED-derived rows) — kept
    as its own field rather than derived, since a user can freely re-tab
    a row in the UI independent of its `area` value.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(ValveMTOProject, on_delete=models.CASCADE, related_name='rows')

    tag_number = models.CharField(max_length=128, blank=True, default='')
    valve_type = models.CharField(max_length=128, blank=True, default='')
    pms_class = models.CharField(max_length=128, blank=True, default='')
    rating = models.CharField(max_length=64, blank=True, default='')
    size_primary = models.CharField(max_length=32, blank=True, default='')
    # BUG FIX: the frontend's local row shape (VALVE_COLUMNS) has THREE
    # size-related columns — size_1, size_2, AND bore — but this model
    # originally only had one ('size_secondary', mapped from 'bore') to
    # match the field list this app was first built to. That silently
    # dropped 'size_2' on every save — a real, confirmed data-loss gap.
    # size_secondary_2 now covers it; size_secondary (bore) is unchanged.
    size_secondary = models.CharField(max_length=32, blank=True, default='')  # bore (FB/RB)
    size_secondary_2 = models.CharField(max_length=32, blank=True, default='')  # size_2 (NB)
    line_number = models.CharField(max_length=255, blank=True, default='')
    line_list_ref = models.CharField(max_length=255, blank=True, default='')
    # P&ID drawing sheet number (AD111-XXX-D-XXXXX format) the valve
    # appears ON — distinct from line_number (the valve's own line/piping
    # tag). Added alongside 'facing' below so the extractor's two newer
    # fields (see piping_valve_mto_extractor.py's MANDATORY FIELDS rules
    # for pid_number/facing) have real columns here; without them both
    # would be silently dropped on every save, same class of gap
    # size_secondary_2 fixed for 'size_2' above.
    pid_number = models.CharField(max_length=255, blank=True, default='')
    # Valve facing: RF (Raised Face) / FF (Flat Face) / RTJ (Ring-Type
    # Joint) — kept as its own field (not folded into 'rating') so it
    # round-trips independently even though the standard Excel template
    # displays it combined with rating under one "RATING / FACING" column
    # (see valveMTOExporter.js).
    facing = models.CharField(max_length=16, blank=True, default='')
    description = models.TextField(blank=True, default='')
    qty_island = models.IntegerField(null=True, blank=True)
    qty_field = models.IntegerField(null=True, blank=True)
    qty_combined = models.IntegerField(null=True, blank=True)
    unit = models.CharField(max_length=32, blank=True, default='')
    area = models.CharField(max_length=32, blank=True, default='')  # ISLAND / FIELD / COMBINED
    operational_status = models.CharField(max_length=64, blank=True, default='')  # e.g. LO/LC/TSO/FBLC...
    remarks = models.TextField(blank=True, default='')
    tab = models.CharField(max_length=16, blank=True, default='')  # island / field / combined
    row_order = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['row_order', 'created_at']
        verbose_name = 'Valve MTO Row'
        verbose_name_plural = 'Valve MTO Rows'
        indexes = [
            models.Index(fields=['project', 'tab', 'row_order']),
        ]

    def __str__(self) -> str:
        return f'{self.tag_number or "(untagged)"} [{self.project_id}]'
