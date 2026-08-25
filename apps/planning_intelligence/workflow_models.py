"""Versioned workflow and engineering-logic configuration for schedule generation."""
from django.conf import settings as django_settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel

from .models import PlanningProject


class WorkflowTemplate(BaseModel):
    """A reusable, versioned definition of the activities used for one deliverable."""

    STATUS_CHOICES = [('draft', 'Draft'), ('active', 'Active'), ('retired', 'Retired')]

    project = models.ForeignKey(
        PlanningProject, on_delete=models.CASCADE, null=True, blank=True,
        related_name='workflow_templates',
        help_text='Null for a protected corporate/system template.',
    )
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='draft', db_index=True)
    is_system = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    supersedes = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='revisions',
    )
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_workflow_templates_created',
    )

    class Meta:
        ordering = ['code', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'code', 'version'], name='uniq_project_workflow_template_version',
            ),
            models.UniqueConstraint(
                fields=['code', 'version'], condition=Q(project__isnull=True),
                name='uniq_system_workflow_template_version',
            ),
            models.CheckConstraint(
                check=Q(is_system=False) | Q(project__isnull=True),
                name='system_workflow_template_has_no_project',
            ),
        ]

    def __str__(self):
        scope = 'SYSTEM' if self.project_id is None else f'PROJECT {self.project_id}'
        return f'{scope} · {self.code} · v{self.version}'


class WorkflowStage(BaseModel):
    """One visible Primavera-style activity in a deliverable workflow."""

    ACTIVITY_TYPE_CHOICES = [
        ('task', 'Task'), ('start_milestone', 'Start Milestone'),
        ('finish_milestone', 'Finish Milestone'), ('level_of_effort', 'Level of Effort'),
    ]
    RELATIONSHIP_CHOICES = [('', 'None'), ('FS', 'Finish to Start'), ('SS', 'Start to Start'), ('FF', 'Finish to Finish'), ('SF', 'Start to Finish')]

    template = models.ForeignKey(WorkflowTemplate, on_delete=models.CASCADE, related_name='stages')
    sequence = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    activity_name_template = models.CharField(
        max_length=255, default='{deliverable} - {stage}',
        help_text='Supports {deliverable}, {stage}, and {discipline}.',
    )
    duration_days = models.DecimalField(
        max_digits=8, decimal_places=2, default=1, validators=[MinValueValidator(0)],
    )
    responsible_party = models.CharField(max_length=120, blank=True)
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPE_CHOICES, default='task')
    relationship_to_previous = models.CharField(
        max_length=2, choices=RELATIONSHIP_CHOICES, blank=True, default='FS',
    )
    lag_days = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        validators=[MinValueValidator(-365), MaxValueValidator(365)],
    )
    progress_weight = models.DecimalField(
        max_digits=7, decimal_places=4, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    is_release_gate = models.BooleanField(default=True)

    class Meta:
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['template', 'sequence'], name='uniq_workflow_stage_sequence'),
            models.UniqueConstraint(fields=['template', 'code'], name='uniq_workflow_stage_code'),
        ]

    def __str__(self):
        return f'{self.template.code} · {self.sequence} · {self.name}'


class EngineeringDependencyTemplate(BaseModel):
    """A versioned discipline deliverable network, such as the Process flow map."""

    STATUS_CHOICES = WorkflowTemplate.STATUS_CHOICES

    project = models.ForeignKey(
        PlanningProject, on_delete=models.CASCADE, null=True, blank=True,
        related_name='engineering_dependency_templates',
    )
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=160)
    discipline = models.CharField(max_length=64, default='process')
    description = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='draft', db_index=True)
    is_system = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    supersedes = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='revisions',
    )
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_dependency_templates_created',
    )

    class Meta:
        ordering = ['discipline', 'code', '-version']
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'code', 'version'], name='uniq_project_dependency_template_version',
            ),
            models.UniqueConstraint(
                fields=['code', 'version'], condition=Q(project__isnull=True),
                name='uniq_system_dependency_template_version',
            ),
            models.CheckConstraint(
                check=Q(is_system=False) | Q(project__isnull=True),
                name='system_dependency_template_has_no_project',
            ),
        ]

    def __str__(self):
        return f'{self.discipline} · {self.code} · v{self.version}'


class EngineeringDependencyRule(BaseModel):
    """A deliverable-to-deliverable link with explicit workflow release gates."""

    RELATIONSHIP_CHOICES = WorkflowStage.RELATIONSHIP_CHOICES[1:]

    template = models.ForeignKey(
        EngineeringDependencyTemplate, on_delete=models.CASCADE, related_name='rules',
    )
    sequence = models.PositiveIntegerField(default=0)
    predecessor_code = models.CharField(max_length=80)
    predecessor_name = models.CharField(max_length=255)
    predecessor_stage_code = models.CharField(max_length=40, default='FINAL_ISSUE')
    successor_code = models.CharField(max_length=80)
    successor_name = models.CharField(max_length=255)
    successor_stage_code = models.CharField(max_length=40, default='IFR')
    relationship_type = models.CharField(max_length=2, choices=RELATIONSHIP_CHOICES, default='FS')
    lag_days = models.DecimalField(
        max_digits=8, decimal_places=2, default=0,
        validators=[MinValueValidator(-365), MaxValueValidator(365)],
    )
    rationale = models.TextField(blank=True)
    source_reference = models.CharField(max_length=255, blank=True)
    requires_confirmation = models.BooleanField(default=True)

    class Meta:
        ordering = ['sequence', 'predecessor_code', 'successor_code']
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'template', 'predecessor_code', 'predecessor_stage_code',
                    'successor_code', 'successor_stage_code', 'relationship_type',
                ],
                name='uniq_engineering_dependency_gate',
            ),
            models.CheckConstraint(
                check=~Q(predecessor_code=models.F('successor_code')),
                name='dependency_rule_distinct_deliverables',
            ),
        ]

    def __str__(self):
        return f'{self.predecessor_code}:{self.predecessor_stage_code} → {self.successor_code}:{self.successor_stage_code}'


class ProjectScheduleConfiguration(BaseModel):
    """The selected workflow and engineering-network configuration for a project."""

    project = models.OneToOneField(
        PlanningProject, on_delete=models.CASCADE, related_name='schedule_configuration',
    )
    workflow_template = models.ForeignKey(
        WorkflowTemplate, on_delete=models.PROTECT, related_name='project_configurations',
    )
    dependency_template = models.ForeignKey(
        EngineeringDependencyTemplate, on_delete=models.PROTECT, null=True, blank=True,
        related_name='project_configurations',
    )
    standard_task_count = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(50)],
    )
    configuration_version = models.PositiveIntegerField(default=1)
    settings = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_schedule_configurations_updated',
    )

    class Meta:
        ordering = ['project_id']

    def __str__(self):
        return f'{self.project_id} · configuration v{self.configuration_version}'


class ScheduleDefaultProposal(BaseModel):
    """A tested, non-effective scheduling-default change awaiting final approval."""

    STATUS_CHOICES = [
        ('proposed', 'Proposed'), ('approved', 'Approved'),
        ('rejected', 'Rejected'), ('superseded', 'Superseded'),
    ]
    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='schedule_default_proposals')
    configuration = models.ForeignKey(ProjectScheduleConfiguration, on_delete=models.CASCADE, related_name='default_proposals')
    title = models.CharField(max_length=180)
    rationale = models.TextField(blank=True)
    base_configuration_version = models.PositiveIntegerField()
    proposed_values = models.JSONField(default=dict)
    test_results = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='proposed', db_index=True)
    proposed_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='planning_schedule_defaults_proposed')
    decided_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='planning_schedule_defaults_decided')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['project', 'status', '-created_at'])]

    def __str__(self):
        return f'{self.project_id} · defaults proposal {self.pk} · {self.status}'


class WorkflowTemplateOverride(BaseModel):
    """A discipline or exact-deliverable workflow override within one project."""

    SCOPE_CHOICES = [('discipline', 'Discipline'), ('deliverable', 'Deliverable')]

    configuration = models.ForeignKey(
        ProjectScheduleConfiguration, on_delete=models.CASCADE, related_name='overrides',
    )
    scope_type = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    scope_key = models.CharField(max_length=255)
    workflow_template = models.ForeignKey(
        WorkflowTemplate, on_delete=models.PROTECT, related_name='configuration_overrides',
    )
    priority = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['priority', 'scope_type', 'scope_key']
        constraints = [
            models.UniqueConstraint(
                fields=['configuration', 'scope_type', 'scope_key'],
                name='uniq_project_workflow_override_scope',
            ),
        ]

    def __str__(self):
        return f'{self.configuration.project_id} · {self.scope_type}:{self.scope_key}'
