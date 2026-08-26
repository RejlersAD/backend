"""Persistent document-intelligence evidence, provenance, and review workflow."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

from .models import PlanningFile, PlanningProject


class DocumentProfile(BaseModel):
    """One durable extraction/classification profile per uploaded document."""

    file = models.OneToOneField(PlanningFile, on_delete=models.CASCADE, related_name='document_profile')
    declared_category = models.CharField(max_length=40, blank=True)
    detected_category = models.CharField(max_length=40, blank=True)
    classification_confidence = models.FloatField(default=0)
    extension = models.CharField(max_length=16, blank=True)
    mime_type = models.CharField(max_length=128, blank=True)
    language = models.CharField(max_length=16, default='en')
    page_count = models.PositiveIntegerField(default=0)
    word_count = models.PositiveIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    extraction_method = models.CharField(max_length=64, blank=True)
    quality_flags = models.JSONField(default=list, blank=True)
    classified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']


class DocumentIntelligenceRun(BaseModel):
    STATUS_CHOICES = [
        ('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed'),
    ]

    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='intelligence_runs')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='running')
    engine_version = models.CharField(max_length=32, default='2.0')
    source_file_ids = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    fact_count = models.PositiveIntegerField(default=0)
    conflict_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='document_intelligence_runs_requested',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['project', '-created_at'])]


class IntelligenceFact(BaseModel):
    TYPE_CHOICES = [
        ('project_name', 'Project Name'), ('effective_date', 'Effective Date'),
        ('duration_months', 'Duration Months'), ('client', 'Client'),
        ('location', 'Location'), ('discipline', 'Discipline'),
        ('deliverable', 'Deliverable'), ('hse_study', 'HSE Study'),
        ('milestone', 'Milestone'), ('calendar', 'Calendar'),
        ('review_cycle', 'Review Cycle'), ('requirement', 'Requirement'),
        ('exclusion', 'Exclusion'),
    ]
    METHOD_CHOICES = [('deterministic', 'Deterministic'), ('ai', 'AI'), ('manual', 'Manual')]
    STATUS_CHOICES = [
        ('detected', 'Detected'), ('confirmed', 'Confirmed'), ('rejected', 'Rejected'),
        ('conflicted', 'Conflicted'), ('superseded', 'Superseded'),
    ]

    run = models.ForeignKey(DocumentIntelligenceRun, on_delete=models.CASCADE, related_name='facts')
    source_file = models.ForeignKey(
        PlanningFile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='intelligence_facts',
    )
    fact_type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    key = models.CharField(max_length=160, db_index=True)
    value = models.JSONField()
    normalized_value = models.CharField(max_length=500, blank=True)
    confidence = models.FloatField(default=0)
    extraction_method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='deterministic')
    source_excerpt = models.CharField(max_length=1000, blank=True)
    source_locator = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='detected', db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='intelligence_facts_reviewed',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['fact_type', '-confidence', 'id']
        indexes = [models.Index(fields=['run', 'fact_type', 'status'])]


class IntelligenceConflict(BaseModel):
    STATUS_CHOICES = [('open', 'Open'), ('resolved', 'Resolved'), ('ignored', 'Ignored')]

    run = models.ForeignKey(DocumentIntelligenceRun, on_delete=models.CASCADE, related_name='conflicts')
    key = models.CharField(max_length=160, db_index=True)
    conflict_type = models.CharField(max_length=40, default='value_mismatch')
    fact_ids = models.JSONField(default=list, blank=True)
    description = models.CharField(max_length=500)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='open', db_index=True)
    resolution = models.JSONField(default=dict, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='intelligence_conflicts_resolved',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['status', 'key']
        indexes = [models.Index(fields=['run', 'status'])]


class DocumentAuthorityRule(BaseModel):
    """Field-specific precedence used when project documents disagree."""

    INFORMATION_CHOICES = [
        ('contract_dates', 'Contract Dates'), ('scope', 'Scope'),
        ('deliverables', 'Deliverables'), ('technical_logic', 'Technical Logic'),
        ('review_cycle', 'Review Cycle'), ('calendar', 'Calendar'),
    ]

    information_type = models.CharField(max_length=32, choices=INFORMATION_CHOICES)
    document_category = models.CharField(max_length=40)
    priority = models.PositiveSmallIntegerField(default=50)
    rationale = models.CharField(max_length=500, blank=True)
    is_system = models.BooleanField(default=True)

    class Meta:
        ordering = ['information_type', '-priority', 'document_category']
        constraints = [
            models.UniqueConstraint(
                fields=['information_type', 'document_category'],
                name='uniq_document_authority_information_category',
            ),
        ]


class ScheduleBasis(BaseModel):
    """Versioned, planner-controlled input contract for schedule generation."""

    STATUS_CHOICES = [
        ('draft', 'Draft'), ('ready', 'Ready for Approval'),
        ('approved', 'Approved'), ('superseded', 'Superseded'),
    ]

    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='schedule_bases')
    source_run = models.ForeignKey(
        DocumentIntelligenceRun, on_delete=models.PROTECT, related_name='schedule_bases',
    )
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft', db_index=True)
    project_name = models.CharField(max_length=255, blank=True)
    client = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    contractual_finish = models.DateField(null=True, blank=True)
    duration_months = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    calendar = models.JSONField(default=dict, blank=True)
    authority_snapshot = models.JSONField(default=dict, blank=True)
    readiness = models.JSONField(default=dict, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_bases_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(fields=['project', 'version'], name='uniq_project_schedule_basis_version'),
        ]


class BasisDeliverable(BaseModel):
    """Canonical deliverable with original identity and source evidence preserved."""

    STATUS_CHOICES = [
        ('needs_review', 'Needs Review'), ('confirmed', 'Confirmed'), ('excluded', 'Excluded'),
    ]

    basis = models.ForeignKey(ScheduleBasis, on_delete=models.CASCADE, related_name='deliverables')
    discipline = models.CharField(max_length=64, blank=True)
    canonical_key = models.CharField(max_length=320)
    canonical_name = models.CharField(max_length=500)
    original_title = models.CharField(max_length=500)
    document_number = models.CharField(max_length=160, blank=True)
    document_revision = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='needs_review', db_index=True)
    confidence = models.FloatField(default=0)
    source_fact_ids = models.JSONField(default=list, blank=True)
    source_references = models.JSONField(default=list, blank=True)
    aliases = models.JSONField(default=list, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='basis_deliverables_reviewed',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['discipline', 'canonical_name']
        constraints = [
            models.UniqueConstraint(
                fields=['basis', 'discipline', 'canonical_key'], name='uniq_basis_canonical_deliverable',
            ),
        ]


class GenerationPlan(BaseModel):
    """Versioned, reviewable translation from an approved basis to a network."""

    STATUS_CHOICES = [
        ('draft', 'Draft'), ('ready', 'Ready for Approval'),
        ('approved', 'Approved'), ('superseded', 'Superseded'),
    ]

    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='generation_plans')
    basis = models.ForeignKey(ScheduleBasis, on_delete=models.PROTECT, related_name='generation_plans')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft', db_index=True)
    readiness = models.JSONField(default=dict, blank=True)
    selected_scenario = models.CharField(max_length=64, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='generation_plans_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(fields=['project', 'version'], name='uniq_project_generation_plan_version'),
        ]


class PlanDeliverable(BaseModel):
    WORKFLOW_FAMILY_CHOICES = [
        ('engineering_document', 'Engineering Document'),
        ('inspection_report', 'Inspection Report'), ('technical_study', 'Technical Study'),
        ('drawing', 'Drawing'), ('plan_procedure', 'Plan / Procedure'),
        ('recurring_report', 'Recurring Report'), ('tender_package', 'Tender Package'),
        ('final_dossier', 'Final Dossier'), ('cost_estimate', 'Cost Estimate'),
    ]
    RECURRENCE_CHOICES = [('none', 'None'), ('weekly', 'Weekly'), ('monthly', 'Monthly')]

    plan = models.ForeignKey(GenerationPlan, on_delete=models.CASCADE, related_name='deliverables')
    basis_deliverable = models.ForeignKey(BasisDeliverable, on_delete=models.PROTECT, related_name='plan_entries')
    workflow_family = models.CharField(max_length=32, choices=WORKFLOW_FAMILY_CHOICES)
    recurrence = models.CharField(max_length=16, choices=RECURRENCE_CHOICES, default='none')
    recurrence_count = models.PositiveSmallIntegerField(default=1)
    scenario_code = models.CharField(max_length=64, blank=True, default='common')
    technical_sequence = models.PositiveSmallIntegerField(default=50)
    classification_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ['technical_sequence', 'basis_deliverable_id']
        constraints = [
            models.UniqueConstraint(fields=['plan', 'basis_deliverable'], name='uniq_plan_basis_deliverable'),
        ]


class GenerationPhase(BaseModel):
    plan = models.ForeignKey(GenerationPlan, on_delete=models.CASCADE, related_name='phases')
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=160)
    sequence = models.PositiveSmallIntegerField()
    duration_months = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    source_references = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['sequence']
        constraints = [models.UniqueConstraint(fields=['plan', 'code'], name='uniq_generation_plan_phase')]


class GenerationDependency(BaseModel):
    STATUS_CHOICES = [('proposed', 'Proposed'), ('confirmed', 'Confirmed'), ('rejected', 'Rejected')]
    SOURCE_CHOICES = [('document', 'Document'), ('corporate_rule', 'Corporate Rule'), ('planner', 'Planner')]
    RELATIONSHIP_CHOICES = [('FS', 'Finish to Start'), ('SS', 'Start to Start'), ('FF', 'Finish to Finish')]

    plan = models.ForeignKey(GenerationPlan, on_delete=models.CASCADE, related_name='dependencies')
    predecessor = models.ForeignKey(PlanDeliverable, on_delete=models.CASCADE, related_name='successor_links')
    successor = models.ForeignKey(PlanDeliverable, on_delete=models.CASCADE, related_name='predecessor_links')
    relationship_type = models.CharField(max_length=2, choices=RELATIONSHIP_CHOICES, default='FS')
    lag_days = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    rationale = models.CharField(max_length=500)
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='corporate_rule')
    source_references = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='proposed', db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='generation_dependencies_reviewed',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['successor__technical_sequence', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['plan', 'predecessor', 'successor', 'relationship_type'],
                name='uniq_generation_plan_dependency',
            ),
            models.CheckConstraint(check=~models.Q(predecessor=models.F('successor')), name='generation_dependency_distinct'),
        ]


class GenerationDecisionGate(BaseModel):
    plan = models.ForeignKey(GenerationPlan, on_delete=models.CASCADE, related_name='decision_gates')
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    sequence = models.PositiveSmallIntegerField(default=1)
    scenarios = models.JSONField(default=list, blank=True)
    source_references = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['sequence']
        constraints = [models.UniqueConstraint(fields=['plan', 'code'], name='uniq_generation_decision_gate')]
