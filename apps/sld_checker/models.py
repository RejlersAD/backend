"""SLD Checker models — all tables prefixed sldc_.

The checker READS apps.electrical_comparison (SldAnalysis result JSON,
SldAnalysisFile uploads) and writes only its own tables. Core rows are
referenced by plain UUIDs (analysis_id / file_id), never by FK, so the
checker can never cascade-delete or lock core state.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.electrical_comparison.models import ElectricalProject


class SldSheet(models.Model):
    """One rendered PDF page of the analysed SLD."""

    READABILITY = [('text', 'text'), ('outline-ocr', 'outline-ocr'), ('scan', 'scan')]

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    analysis_id = models.UUIDField(db_index=True)          # core SldAnalysis.id
    file_id = models.UUIDField(null=True, blank=True)      # core SldAnalysisFile.id
    page_index = models.PositiveIntegerField()             # 0-based
    drawing_no = models.CharField(max_length=100, blank=True)  # empty = to confirm
    rev = models.CharField(max_length=20, blank=True)
    title = models.CharField(max_length=255, blank=True)
    readability = models.CharField(max_length=20, choices=READABILITY, default='scan')
    image_key = models.CharField(max_length=500, blank=True)   # storage key of the PNG

    class Meta:
        db_table = 'sldc_sheets'
        ordering = ['page_index']


class SldNode(models.Model):
    """One equipment/load symbol located (or expected) on the SLD."""

    KINDS = [('source', 'source'), ('transformer', 'transformer'), ('generator', 'generator'),
             ('board', 'board'), ('subpanel', 'subpanel'), ('motor', 'motor'),
             ('load', 'load'), ('interface', 'interface'), ('ups', 'ups')]
    SOURCES = [('core', 'core'), ('checker', 'checker')]

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    analysis_id = models.UUIDField(db_index=True)
    sheet = models.ForeignKey(SldSheet, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='nodes')
    tag = models.CharField(max_length=120)
    norm_tag = models.CharField(max_length=120, db_index=True)
    kind = models.CharField(max_length=20, choices=KINDS, default='load')
    description = models.TextField(blank=True)
    voltage_v = models.FloatField(null=True, blank=True)
    rated_a = models.FloatField(null=True, blank=True)
    isc_ka = models.FloatField(null=True, blank=True)
    kva = models.FloatField(null=True, blank=True)
    kw = models.FloatField(null=True, blank=True)
    z_pct = models.FloatField(null=True, blank=True)
    xd_pct = models.FloatField(null=True, blank=True)
    vector_group = models.CharField(max_length=20, null=True, blank=True)
    essential = models.BooleanField(default=False)
    bbox = models.JSONField(null=True, blank=True)   # [x0,y0,x1,y1] in % of page
    confidence = models.FloatField(null=True, blank=True)
    needs_review = models.BooleanField(default=False)
    source = models.CharField(max_length=10, choices=SOURCES, default='core')
    core_ref = models.CharField(max_length=40, blank=True)  # e.g. 'load', 'equipment'

    class Meta:
        db_table = 'sldc_nodes'
        ordering = ['norm_tag']


class SldEdge(models.Model):
    """A fed-from connection between two nodes."""

    KINDS = [('incomer', 'incomer'), ('feeder', 'feeder'), ('tie', 'tie'),
             ('alternative', 'alternative')]
    STARTERS = [('DOL', 'DOL'), ('SD', 'SD'), ('SS', 'SS'), ('VFD', 'VFD')]

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    analysis_id = models.UUIDField(db_index=True)
    from_node = models.ForeignKey(SldNode, on_delete=models.CASCADE, related_name='edges_out')
    to_node = models.ForeignKey(SldNode, on_delete=models.CASCADE, related_name='edges_in')
    kind = models.CharField(max_length=20, choices=KINDS, default='feeder')
    feeder_no = models.CharField(max_length=40, blank=True)
    device = models.CharField(max_length=40, blank=True)       # ACB/MCCB/SF…
    device_a = models.FloatField(null=True, blank=True)
    device_ka = models.FloatField(null=True, blank=True)
    starter = models.CharField(max_length=10, choices=STARTERS, null=True, blank=True)
    relays = models.JSONField(default=list, blank=True)        # ANSI codes
    cable_tag = models.CharField(max_length=120, blank=True)
    cable_size = models.CharField(max_length=80, blank=True)
    sheet = models.ForeignKey(SldSheet, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='edges')
    bbox = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'sldc_edges'


class SldFinding(models.Model):
    """One check result (rule violation / query / not-verifiable)."""

    FAMILIES = [('engineering', 'engineering'), ('load-list', 'load-list'),
                ('drafting', 'drafting'), ('equipment', 'equipment'), ('cross', 'cross')]
    SEVERITIES = [('critical', 'critical'), ('major', 'major'), ('minor', 'minor')]
    RESULTS = [('Non-Conformance', 'Non-Conformance'), ('Query', 'Query'),
               ('Not Verifiable', 'Not Verifiable')]
    STATUSES = [('open', 'open'), ('confirmed', 'confirmed'), ('rejected', 'rejected')]

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    analysis_id = models.UUIDField(db_index=True)
    number = models.CharField(max_length=10, blank=True)       # E1 / L2 / D3 / Q1
    rule_id = models.CharField(max_length=30, db_index=True)
    family = models.CharField(max_length=20, choices=FAMILIES)
    severity = models.CharField(max_length=10, choices=SEVERITIES)
    result = models.CharField(max_length=20, choices=RESULTS, default='Query')
    title = models.CharField(max_length=255)
    found = models.TextField(blank=True)
    estimate = models.TextField(blank=True)
    basis = models.TextField(blank=True)
    action = models.TextField(blank=True)
    node_ids = models.JSONField(default=list, blank=True)
    sheet = models.ForeignKey(SldSheet, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='findings')
    bbox = models.JSONField(null=True, blank=True)
    checklist_items = models.JSONField(default=list, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default='open')
    engineer_note = models.TextField(blank=True)
    core_ref = models.CharField(max_length=40, blank=True)     # core row ref when mapped

    class Meta:
        db_table = 'sldc_findings'
        ordering = ['number']


class SldCheckConfig(models.Model):
    """Per-project check thresholds (all soft-coded defaults)."""

    project = models.OneToOneField(ElectricalProject, on_delete=models.CASCADE,
                                   related_name='sldc_config')
    dol_kw_gen = models.FloatField(default=37)       # DOL limit on generator-fed boards (kW)
    dol_kw_other = models.FloatField(default=75)     # DOL limit otherwise (kW)
    spare_pct = models.FloatField(default=20)        # min spare feeders %
    load_amber = models.FloatField(default=85)       # bus loading amber threshold %
    load_red = models.FloatField(default=100)        # bus loading red threshold %
    xd_pct_default = models.FloatField(default=15)   # generator X"d % when not on SLD
    pf = models.FloatField(default=0.85)
    eff = models.FloatField(default=0.95)
    conf_min = models.FloatField(default=0.8)        # below -> needs_review, rules skip

    class Meta:
        db_table = 'sldc_config'


# ── Checklist (AI-SLD-01) — mirrors apps.pid_verification_v2 checklist ────

class SldChecklistTemplate(models.Model):
    """An imported AI-SLD-01-format workbook; the export FILLS it."""

    code = models.CharField(max_length=40, db_index=True)    # 'AI-SLD-01'
    title = models.CharField(max_length=255, blank=True)
    version = models.PositiveIntegerField(default=1)
    source_file = models.FileField(upload_to='sldc/checklist_templates/', max_length=500)
    is_default = models.BooleanField(default=False)
    item_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sldc_checklist_templates'
        unique_together = [('code', 'version')]
        ordering = ['code', '-version']

    def __str__(self):
        return f'{self.code} v{self.version} ({self.item_count} items)'


class SldChecklistItem(models.Model):
    template = models.ForeignKey(SldChecklistTemplate, on_delete=models.CASCADE,
                                 related_name='items')
    no = models.CharField(max_length=10)
    section_no = models.CharField(max_length=4)
    section_title = models.CharField(max_length=255)
    sub_heading = models.CharField(max_length=60, blank=True)
    check_point = models.TextField()
    mode = models.CharField(max_length=20)                   # Automated | AI-Assisted | Engineer-Led
    ai_task = models.TextField(blank=True)
    ref_inputs = models.TextField(blank=True)
    evidence_required = models.TextField(blank=True)
    severity = models.CharField(max_length=10)               # Critical | Major | Minor
    xlsx_row = models.PositiveIntegerField()
    order = models.PositiveIntegerField()

    class Meta:
        db_table = 'sldc_checklist_items'
        unique_together = [('template', 'no')]
        ordering = ['order']

    def __str__(self):
        return f'{self.no} {self.check_point[:50]}'


class SldChecklistResult(models.Model):
    """Engineer-side state for one checklist item of one analysis. The AI
    result is recomputed live (checklist_eval.evaluate); this row persists
    only applicability overrides, verification and remarks."""

    APPLICABILITY = [('Y', 'Y'), ('N', 'N'), ('NA', 'NA')]
    AI_RESULTS = [('OK', 'OK'), ('Non-Conformance', 'Non-Conformance'),
                  ('Query', 'Query'), ('Not Verifiable', 'Not Verifiable')]
    VERIFICATION = [('Agree', 'Agree'), ('Disagree', 'Disagree'), ('Modified', 'Modified')]

    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    analysis_id = models.UUIDField(db_index=True)
    page = models.PositiveSmallIntegerField(default=0)
    item = models.ForeignKey(SldChecklistItem, on_delete=models.CASCADE,
                             related_name='results')
    applicability = models.CharField(max_length=3, choices=APPLICABILITY, default='Y')
    applicability_by = models.CharField(max_length=10, default='ai')   # 'ai' | 'engineer'
    ai_result = models.CharField(max_length=20, choices=AI_RESULTS, null=True, blank=True)
    engineer_verification = models.CharField(max_length=10, choices=VERIFICATION,
                                             null=True, blank=True)
    remarks = models.TextField(blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True,
                                    related_name='sldc_checklist_verifications')
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'sldc_checklist_results'
        unique_together = [('analysis_id', 'page', 'item')]
