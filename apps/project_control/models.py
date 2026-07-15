"""
Project Management — domain models layered on top of apps.core.project_models.Project.

These models are deliberately additive: they FK into the existing Project and
never modify it. Each row also inherits soft-delete and timestamping from
apps.core.models.BaseModel.
"""
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel
from apps.core.project_models import Project


# ─────────────────────────────────────────────────────────────────────────────
# Enum choices (soft-coded — change here, no migrations needed for label text)
# ─────────────────────────────────────────────────────────────────────────────
ESTIMATE_KIND_CHOICES = [
    ('estimate', 'Internal Estimate'),
    ('tender',   'Tender Submitted'),
    ('awarded',  'Awarded / Contract'),
    ('baseline', 'Baseline (locked)'),
    ('revised',  'Revised'),
]

ESTIMATE_SOURCE_CHOICES = [
    ('excel',       'Excel BOQ import'),
    ('manual',      'Manual entry'),
    ('finance',     'Finance invoice sync'),
    ('ai_takeoff',  'AI Take-Off'),
]

ESTIMATE_STATUS_CHOICES = [
    ('draft',      'Draft'),
    ('approved',   'Approved'),
    ('superseded', 'Superseded'),
]

DOCUMENT_KIND_CHOICES = [
    ('boq',             'BOQ'),
    ('tender',          'Tender'),
    ('contract',        'Contract'),
    ('change_order',    'Change Order'),
    ('drawing',         'Drawing'),
    ('progress_report', 'Progress Report'),
    ('minutes',         'Meeting Minutes'),
    ('specification',   'Specification'),
    ('other',           'Other'),
]

DOC_PARSE_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('queued',  'Queued'),
    ('done',    'Parsed'),
    ('failed',  'Failed'),
    ('skipped', 'Skipped'),
]

SNAPSHOT_SOURCE_CHOICES = [
    ('manual',   'Manual entry'),
    ('computed', 'Computed from tasks/invoices'),
    ('finance',  'Finance invoice sync'),
]

CHANGE_SEVERITY_CHOICES = [
    ('low',      'Low'),
    ('medium',   'Medium'),
    ('high',     'High'),
    ('critical', 'Critical'),
]

CHANGE_STATUS_CHOICES = [
    ('detected', 'Detected'),
    ('reviewed', 'Under Review'),
    ('accepted', 'Accepted'),
    ('rejected', 'Rejected'),
]


def _document_upload_path(instance, filename):
    """Storage path resolver — keeps S3 layout soft-coded via config."""
    from .config import S3_BASE_PREFIX
    project_code = (instance.project.code if instance.project_id else 'UNASSIGNED').replace(' ', '_')
    return f"{S3_BASE_PREFIX}/{project_code}/{instance.kind}/{uuid.uuid4().hex}_{filename}"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Estimate / Variance / WBS / Documents
# ─────────────────────────────────────────────────────────────────────────────
class WBSNode(BaseModel):
    """Work-breakdown structure node — recursive tree per project."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='wbs_nodes')
    parent  = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    code = models.CharField(max_length=64, help_text='e.g. 1.2.3')
    name = models.CharField(max_length=255)
    level = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['project', 'sort_order', 'code']
        unique_together = [('project', 'code')]
        indexes = [models.Index(fields=['project', 'level'])]

    def __str__(self):
        return f'{self.project.code} · {self.code} {self.name}'


class Estimate(BaseModel):
    """A single estimate version (internal estimate / tender / awarded / baseline)."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='estimates')
    version = models.PositiveIntegerField(default=1)
    kind = models.CharField(max_length=20, choices=ESTIMATE_KIND_CHOICES, default='estimate')
    source = models.CharField(max_length=20, choices=ESTIMATE_SOURCE_CHOICES, default='manual')
    status = models.CharField(max_length=20, choices=ESTIMATE_STATUS_CHOICES, default='draft')

    title = models.CharField(max_length=255, blank=True)
    currency = models.CharField(max_length=8, default='AED')
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    snapshot_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    # Soft link to the source document for audit (BOQ Excel etc.)
    source_document = models.ForeignKey(
        'ProjectDocument', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='derived_estimates',
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='project_control_estimates_created',
    )

    class Meta:
        ordering = ['-created_at']
        unique_together = [('project', 'kind', 'version')]
        indexes = [
            models.Index(fields=['project', 'kind']),
            models.Index(fields=['project', 'status']),
        ]

    def __str__(self):
        return f'{self.project.code} · {self.get_kind_display()} v{self.version}'


class EstimateLineItem(BaseModel):
    """One BOQ row inside an Estimate."""
    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name='line_items')
    wbs_code = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    discipline = models.CharField(max_length=64, blank=True)
    category = models.CharField(max_length=64, blank=True)
    unit = models.CharField(max_length=32, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    unit_rate = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sort_order = models.PositiveIntegerField(default=0)
    source_row = models.JSONField(default=dict, blank=True, help_text='Raw row captured from import')

    class Meta:
        ordering = ['estimate', 'sort_order', 'id']
        indexes = [
            models.Index(fields=['estimate', 'wbs_code']),
            models.Index(fields=['estimate', 'discipline']),
        ]

    def __str__(self):
        return f'{self.estimate_id} · {self.wbs_code or "-"} {self.description[:40]}'


class ProjectDocument(BaseModel):
    """Any project artefact stored on S3 (BOQ, tender, contract, change order…)."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='control_documents')
    kind = models.CharField(max_length=24, choices=DOCUMENT_KIND_CHOICES, default='other')
    title = models.CharField(max_length=255, blank=True)
    file = models.FileField(upload_to=_document_upload_path, max_length=512)
    original_filename = models.CharField(max_length=512, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)

    parse_status = models.CharField(max_length=12, choices=DOC_PARSE_STATUS_CHOICES, default='pending')
    parsed_data = models.JSONField(default=dict, blank=True)
    parse_error = models.TextField(blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='project_control_documents_uploaded',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project', 'kind']),
            models.Index(fields=['project', '-created_at']),
        ]

    def __str__(self):
        return f'{self.project.code} · {self.get_kind_display()} · {self.original_filename or self.file.name}'


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 / Phase 4 — model placeholders (tables migrate now, writers added when flag flips)
# ─────────────────────────────────────────────────────────────────────────────
class CostSnapshot(BaseModel):
    """Daily/weekly EVM snapshot — filled by Phase 3 forecasting task."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='cost_snapshots')
    period_end = models.DateField()
    planned_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    earned_value  = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    actual_cost   = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cpi = models.FloatField(null=True, blank=True)
    spi = models.FloatField(null=True, blank=True)
    eac = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=12, choices=SNAPSHOT_SOURCE_CHOICES, default='manual')
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['project', '-period_end']
        unique_together = [('project', 'period_end')]
        indexes = [models.Index(fields=['project', '-period_end'])]

    def __str__(self):
        return f'{self.project.code} · {self.period_end}'


class ChangeEvent(BaseModel):
    """A scope/cost change detected from a document — filled by Phase 4."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='change_events')
    source_document = models.ForeignKey(
        ProjectDocument, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='detected_changes',
    )
    detected_at = models.DateTimeField(auto_now_add=True)
    summary = models.CharField(max_length=512)
    description = models.TextField(blank=True)
    severity = models.CharField(max_length=12, choices=CHANGE_SEVERITY_CHOICES, default='medium')
    delta_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    delta_currency = models.CharField(max_length=8, default='AED')
    status = models.CharField(max_length=12, choices=CHANGE_STATUS_CHOICES, default='detected')
    ai_confidence = models.FloatField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='project_control_changes_reviewed',
    )

    class Meta:
        ordering = ['-detected_at']
        indexes = [
            models.Index(fields=['project', 'status']),
            models.Index(fields=['project', '-detected_at']),
        ]

    def __str__(self):
        return f'{self.project.code} · {self.summary[:60]}'


# ─────────────────────────────────────────────────────────────────────────────
# Planning Package — Phase-agnostic feature for work package planning
# ─────────────────────────────────────────────────────────────────────────────
PLANNING_PACKAGE_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('completed', 'Completed'),
    ('on_hold', 'On Hold'),
    ('cancelled', 'Cancelled'),
]

PLANNING_PACKAGE_PRIORITY_CHOICES = [
    ('low', 'Low'),
    ('medium', 'Medium'),
    ('high', 'High'),
    ('critical', 'Critical'),
]


class PlanningPackage(BaseModel):
    """
    Planning Package — work package for project planning and tracking.
    Enables project managers to break down projects into manageable packages
    with budgets, timelines, and deliverables.
    
    SOFT-CODED: All choice fields use configurable enums above
    """
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='planning_packages',
        help_text='Parent project this package belongs to'
    )
    package_code = models.CharField(
        max_length=64,
        help_text='Unique package identifier (e.g., PP-001, PKG-FEED-01)'
    )
    name = models.CharField(
        max_length=255,
        help_text='Package name/title'
    )
    description = models.TextField(
        blank=True,
        help_text='Detailed description of package scope'
    )
    
    # Status & Priority (SOFT-CODED via enums)
    status = models.CharField(
        max_length=20,
        choices=PLANNING_PACKAGE_STATUS_CHOICES,
        default='draft'
    )
    priority = models.CharField(
        max_length=20,
        choices=PLANNING_PACKAGE_PRIORITY_CHOICES,
        default='medium'
    )
    
    # Financial tracking
    budget = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Allocated budget for this package'
    )
    currency = models.CharField(max_length=8, default='AED')
    actual_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text='Actual cost spent to date'
    )
    
    # Schedule tracking
    planned_start = models.DateField(
        null=True,
        blank=True,
        help_text='Planned start date'
    )
    planned_end = models.DateField(
        null=True,
        blank=True,
        help_text='Planned completion date'
    )
    actual_start = models.DateField(
        null=True,
        blank=True,
        help_text='Actual start date'
    )
    actual_end = models.DateField(
        null=True,
        blank=True,
        help_text='Actual completion date'
    )
    
    # Progress tracking
    progress_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text='Completion percentage (0-100)'
    )
    
    # Ownership & team
    package_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_planning_packages',
        help_text='User responsible for this package'
    )
    
    # Relationships
    wbs_node = models.ForeignKey(
        WBSNode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='planning_packages',
        help_text='Optional link to WBS structure'
    )
    
    # Deliverables & notes
    deliverables = models.TextField(
        blank=True,
        help_text='Key deliverables (one per line or JSON)'
    )
    notes = models.TextField(
        blank=True,
        help_text='Additional notes, risks, assumptions'
    )
    
    class Meta:
        ordering = ['project', 'package_code']
        unique_together = [('project', 'package_code')]
        indexes = [
            models.Index(fields=['project', 'status']),
            models.Index(fields=['project', 'priority']),
            models.Index(fields=['project', '-created_at']),
            models.Index(fields=['package_manager']),
        ]

    def __str__(self):
        return f'{self.project.code} · {self.package_code} {self.name}'
    
    @property
    def budget_variance(self):
        """Calculate budget variance (negative = over budget)"""
        if self.budget:
            return self.budget - self.actual_cost
        return None
    
    @property
    def is_over_budget(self):
        """Check if package is over budget"""
        variance = self.budget_variance
        return variance is not None and variance < 0
    
    @property
    def days_remaining(self):
        """Calculate days until planned end date"""
        if self.planned_end:
            from datetime import date
            delta = self.planned_end - date.today()
            return delta.days
        return None
