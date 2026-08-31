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

ALLOCATION_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('closed', 'Closed'),
]

COST_SOURCE_TYPE_CHOICES = [
    ('purchase_requisition', 'Purchase Requisition'),
    ('purchase_order', 'Purchase Order'),
    ('invoice_allocation', 'Verified Invoice Allocation'),
    ('manual', 'Manual'),
]

LEDGER_ENTRY_TYPE_CHOICES = [
    ('budget', 'Budget'),
    ('commitment', 'Commitment'),
    ('actual', 'Actual Cost'),
    ('adjustment', 'Adjustment'),
]

LEDGER_STATUS_CHOICES = [
    ('posted', 'Posted'),
    ('reversed', 'Reversed'),
]

COMMERCIAL_EVENT_TYPE_CHOICES = [
    ('po_approved', 'Purchase Order Approved'),
    ('receipt_accepted', 'Receipt Accepted'),
    ('invoice_approved', 'Invoice Approved'),
    ('invoice_verified', 'Invoice Three-Way Match Verified'),
    ('payment_scheduled', 'Payment Scheduled'),
    ('payment_recorded', 'Payment Recorded'),
    ('payment_held', 'Payment Held'),
    ('payment_released', 'Payment Released'),
    ('payment_cancelled', 'Payment Cancelled'),
    ('historical_reconciliation', 'Historical Reconciliation'),
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


class BudgetAllocation(BaseModel):
    """Approved control budget assigned to one enterprise Project/WBS node."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='control_budget_allocations')
    wbs_node = models.ForeignKey(WBSNode, on_delete=models.PROTECT, related_name='budget_allocations')
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=8, default='AED')
    status = models.CharField(max_length=12, choices=ALLOCATION_STATUS_CHOICES, default='draft', db_index=True)
    source_budget = models.ForeignKey(
        'procurement.Budget', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='control_allocations',
    )
    notes = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='project_control_budgets_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['project', 'wbs_node__sort_order', 'code']
        constraints = [
            models.UniqueConstraint(fields=['project', 'code'], name='pc_budget_project_code_uniq'),
            models.CheckConstraint(check=models.Q(amount__gt=0), name='pc_budget_amount_positive'),
        ]
        indexes = [models.Index(fields=['project', 'status'], name='pc_budget_proj_status_idx')]


class CostAllocation(BaseModel):
    """Manual project/WBS split for ambiguous Procurement or Finance records."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='cost_allocations')
    wbs_node = models.ForeignKey(WBSNode, on_delete=models.PROTECT, related_name='cost_allocations')
    budget_allocation = models.ForeignKey(
        BudgetAllocation, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='cost_allocations',
    )
    source_type = models.CharField(max_length=30, choices=COST_SOURCE_TYPE_CHOICES, db_index=True)
    source_id = models.CharField(max_length=64, db_index=True)
    source_reference = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=8, default='AED')
    status = models.CharField(max_length=12, choices=ALLOCATION_STATUS_CHOICES, default='draft', db_index=True)
    notes = models.TextField(blank=True)
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='project_cost_allocations_created',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='project_cost_allocations_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['source_type', 'source_id', 'project', 'wbs_node'],
                condition=models.Q(is_deleted=False), name='pc_cost_source_wbs_uniq',
            ),
            models.CheckConstraint(check=models.Q(amount__gt=0), name='pc_cost_amount_positive'),
        ]
        indexes = [
            models.Index(fields=['project', 'status'], name='pc_cost_proj_status_idx'),
            models.Index(fields=['source_type', 'source_id', 'status'], name='pc_cost_source_status_idx'),
        ]


class CostLedgerEntry(BaseModel):
    """Auditable, idempotently generated project cost ledger entry."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='cost_ledger_entries')
    wbs_node = models.ForeignKey(WBSNode, null=True, blank=True, on_delete=models.PROTECT, related_name='ledger_entries')
    budget_allocation = models.ForeignKey(
        BudgetAllocation, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='ledger_entries',
    )
    cost_allocation = models.ForeignKey(
        CostAllocation, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='ledger_entries',
    )
    entry_key = models.CharField(max_length=255, unique=True)
    entry_type = models.CharField(max_length=20, choices=LEDGER_ENTRY_TYPE_CHOICES, db_index=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=8, default='AED')
    source_type = models.CharField(max_length=30, blank=True, db_index=True)
    source_id = models.CharField(max_length=64, blank=True, db_index=True)
    source_reference = models.CharField(max_length=120, blank=True)
    entry_date = models.DateField()
    status = models.CharField(max_length=12, choices=LEDGER_STATUS_CHOICES, default='posted', db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='project_cost_ledger_entries_created',
    )

    class Meta:
        ordering = ['-entry_date', '-created_at']
        constraints = [models.CheckConstraint(check=models.Q(amount__gte=0), name='pc_ledger_amount_nonnegative')]
        indexes = [
            models.Index(fields=['project', 'entry_type', 'status'], name='pc_ledger_proj_type_idx'),
            models.Index(fields=['project', 'wbs_node', 'status'], name='pc_ledger_proj_wbs_idx'),
        ]


class CommercialEvent(models.Model):
    """Immutable cross-department event and audit record.

    ``event_key`` makes signal delivery and historical reconciliation safe to
    retry. Source applications remain authoritative; this table records the
    commercial consequence and whether the project ledger was rebuilt.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='commercial_events',
    )
    event_key = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=40, choices=COMMERCIAL_EVENT_TYPE_CHOICES, db_index=True)
    source_type = models.CharField(max_length=40, db_index=True)
    source_id = models.CharField(max_length=64, db_index=True)
    source_reference = models.CharField(max_length=160, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=8, blank=True)
    event_at = models.DateTimeField(db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='project_commercial_events',
    )
    payload = models.JSONField(default=dict, blank=True)
    ledger_rebuilt = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-event_at', '-created_at']
        indexes = [
            models.Index(fields=['project', 'event_type', '-event_at'], name='pc_com_event_project_idx'),
            models.Index(fields=['source_type', 'source_id'], name='pc_com_event_source_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.pk and CommercialEvent.objects.filter(pk=self.pk).exists():
            raise ValueError('Commercial events are immutable.')
        return super().save(*args, **kwargs)


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
