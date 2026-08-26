"""Relational scheduling domain for CPM calculation and controlled baselines."""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import BaseModel

from .models import PlanningGeneration, PlanningProject


class WorkCalendar(BaseModel):
    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='work_calendars')
    name = models.CharField(max_length=120)
    working_weekdays = models.JSONField(default=list, help_text='ISO weekdays: Monday=0 through Sunday=6.')
    hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, default=8)
    timezone = models.CharField(max_length=64, default='Asia/Dubai')
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']
        unique_together = [('project', 'name')]

    def __str__(self):
        return f'{self.project_id} · {self.name}'


class CalendarException(BaseModel):
    calendar = models.ForeignKey(WorkCalendar, on_delete=models.CASCADE, related_name='exceptions')
    date = models.DateField()
    is_working = models.BooleanField(default=False)
    working_hours = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    name = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['date']
        unique_together = [('calendar', 'date')]


class Schedule(BaseModel):
    STATUS_CHOICES = [
        ('draft', 'Draft'), ('active', 'Active'), ('on_hold', 'On Hold'),
        ('completed', 'Completed'), ('archived', 'Archived'),
    ]

    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='schedules')
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft')
    planned_start = models.DateField()
    data_date = models.DateField(null=True, blank=True)
    default_calendar = models.ForeignKey(
        WorkCalendar, on_delete=models.SET_NULL, null=True, blank=True, related_name='schedules',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedules_created',
    )

    class Meta:
        ordering = ['-created_at']
        unique_together = [('project', 'code')]

    def __str__(self):
        return f'{self.code} · {self.name}'


class ScheduleVersion(BaseModel):
    STATUS_CHOICES = [
        ('draft', 'Draft'), ('calculated', 'Calculated'), ('approved', 'Approved'),
        ('baselined', 'Baselined'), ('superseded', 'Superseded'),
    ]

    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft')
    parent_version = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='child_versions',
    )
    source_generation = models.OneToOneField(
        PlanningGeneration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_version',
    )
    change_summary = models.CharField(max_length=255, blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)
    calculated_finish = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_versions_created',
    )

    class Meta:
        ordering = ['-version']
        unique_together = [('schedule', 'version')]


class ScheduleWBSNode(BaseModel):
    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='wbs_nodes')
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='children',
    )
    code = models.CharField(max_length=128)
    name = models.CharField(max_length=255)
    level = models.PositiveSmallIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0)
    discipline = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ['sort_order', 'code']
        unique_together = [('version', 'code')]


class ScheduleActivity(BaseModel):
    TYPE_CHOICES = [
        ('task', 'Task'), ('start_milestone', 'Start Milestone'),
        ('finish_milestone', 'Finish Milestone'), ('level_of_effort', 'Level of Effort'),
    ]
    CONSTRAINT_CHOICES = [
        ('none', 'None'), ('start_no_earlier', 'Start No Earlier Than'),
        ('start_no_later', 'Start No Later Than'), ('finish_no_later', 'Finish No Later Than'),
        ('must_start', 'Must Start On'), ('must_finish', 'Must Finish On'),
    ]

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='activities')
    wbs_node = models.ForeignKey(
        ScheduleWBSNode, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities',
    )
    calendar = models.ForeignKey(
        WorkCalendar, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities',
    )
    external_id = models.CharField(max_length=64)
    name = models.CharField(max_length=500)
    activity_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='task')
    duration_days = models.DecimalField(
        max_digits=10, decimal_places=2, default=1, validators=[MinValueValidator(0)],
    )
    discipline = models.CharField(max_length=64, blank=True)
    responsible_role = models.CharField(max_length=120, blank=True)
    constraint_type = models.CharField(max_length=24, choices=CONSTRAINT_CHOICES, default='none')
    constraint_date = models.DateField(null=True, blank=True)
    planned_start = models.DateField(null=True, blank=True)
    planned_finish = models.DateField(null=True, blank=True)
    early_start = models.DateField(null=True, blank=True)
    early_finish = models.DateField(null=True, blank=True)
    late_start = models.DateField(null=True, blank=True)
    late_finish = models.DateField(null=True, blank=True)
    total_float_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    free_float_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_critical = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['sort_order', 'external_id']
        unique_together = [('version', 'external_id')]
        indexes = [models.Index(fields=['version', 'is_critical'])]

    @property
    def is_milestone(self):
        return self.activity_type in ('start_milestone', 'finish_milestone')


class ActivityRelationship(BaseModel):
    TYPE_CHOICES = [('FS', 'Finish to Start'), ('SS', 'Start to Start'), ('FF', 'Finish to Finish'), ('SF', 'Start to Finish')]

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='relationships')
    predecessor = models.ForeignKey(ScheduleActivity, on_delete=models.CASCADE, related_name='successor_links')
    successor = models.ForeignKey(ScheduleActivity, on_delete=models.CASCADE, related_name='predecessor_links')
    relationship_type = models.CharField(max_length=2, choices=TYPE_CHOICES, default='FS')
    lag_days = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['successor__sort_order', 'predecessor__sort_order']
        unique_together = [('predecessor', 'successor', 'relationship_type')]


class ScheduleResource(BaseModel):
    TYPE_CHOICES = [('labor', 'Labor'), ('equipment', 'Equipment'), ('material', 'Material')]

    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='schedule_resources')
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    resource_type = models.CharField(max_length=16, choices=TYPE_CHOICES, default='labor')
    role = models.CharField(max_length=120, blank=True)
    unit = models.CharField(max_length=32, default='hour')
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    capacity_units_per_day = models.DecimalField(
        max_digits=12, decimal_places=2, default=8,
        help_text='Maximum available units per working day for concurrency checks.',
    )

    class Meta:
        ordering = ['code']
        unique_together = [('project', 'code')]


class ActivityAssignment(BaseModel):
    activity = models.ForeignKey(ScheduleActivity, on_delete=models.CASCADE, related_name='assignments')
    resource = models.ForeignKey(ScheduleResource, on_delete=models.CASCADE, related_name='assignments')
    planned_units = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    budgeted_hours = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    budgeted_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        unique_together = [('activity', 'resource')]


class ScheduleBaseline(BaseModel):
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='baselines')
    source_version = models.ForeignKey(ScheduleVersion, on_delete=models.PROTECT, related_name='baselines')
    name = models.CharField(max_length=255)
    data_date = models.DateField(null=True, blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_baselines_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('schedule', 'name')]


class ScheduleCalculationRun(BaseModel):
    STATUS_CHOICES = [('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed')]

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='calculation_runs')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='running')
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    activity_count = models.PositiveIntegerField(default=0)
    critical_activity_count = models.PositiveIntegerField(default=0)
    project_finish = models.DateField(null=True, blank=True)
    issues = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_calculations_requested',
    )

    class Meta:
        ordering = ['-created_at']


class ScheduleAssuranceReview(BaseModel):
    """Durable Phase 3 assessment of one exact calculated schedule state."""

    STATUS_CHOICES = [
        ('draft', 'Draft'), ('ready', 'Ready'), ('approved', 'Approved'),
        ('superseded', 'Superseded'),
    ]

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='assurance_reviews')
    calculation_run = models.ForeignKey(
        ScheduleCalculationRun, on_delete=models.PROTECT, related_name='assurance_reviews',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft', db_index=True)
    network_validation = models.JSONField(default=dict, blank=True)
    contract_scenarios = models.JSONField(default=dict, blank=True)
    resource_validation = models.JSONField(default=dict, blank=True)
    change_comparison = models.JSONField(default=dict, blank=True)
    blockers = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    calculated_state_at = models.DateTimeField()
    input_fingerprint = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_assurance_reviews_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['version', 'status'])]


class ActivityProgressUpdate(BaseModel):
    """Cumulative status for one activity at a formal schedule data date."""

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='progress_updates')
    activity = models.ForeignKey(ScheduleActivity, on_delete=models.CASCADE, related_name='progress_updates')
    data_date = models.DateField()
    physical_progress_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    remaining_duration_days = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    actual_start = models.DateField(null=True, blank=True)
    actual_finish = models.DateField(null=True, blank=True)
    forecast_finish = models.DateField(null=True, blank=True)
    actual_hours = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)],
    )
    actual_cost = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)],
    )
    notes = models.TextField(blank=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='activity_progress_reported',
    )

    class Meta:
        ordering = ['-data_date', 'activity__sort_order']
        unique_together = [('activity', 'data_date')]
        indexes = [models.Index(fields=['version', '-data_date'])]


class DailyFieldUpdate(BaseModel):
    """Governed field report that becomes schedule progress only after approval."""

    STATUS_CHOICES = [
        ('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    MEASUREMENT_CHOICES = [
        ('manual', 'Manual percent'), ('quantity', 'Installed quantity'),
        ('zero_hundred', '0/100'), ('fifty_fifty', '50/50'),
        ('weighted_steps', 'Weighted steps'),
    ]

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='daily_field_updates')
    activity = models.ForeignKey(ScheduleActivity, on_delete=models.CASCADE, related_name='daily_field_updates')
    report_date = models.DateField(db_index=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft', db_index=True)
    measurement_method = models.CharField(max_length=24, choices=MEASUREMENT_CHOICES, default='manual')
    physical_progress_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    installed_quantity = models.DecimalField(
        max_digits=16, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    planned_quantity = models.DecimalField(
        max_digits=16, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    quantity_unit = models.CharField(max_length=32, blank=True)
    remaining_duration_days = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)],
    )
    actual_start = models.DateField(null=True, blank=True)
    actual_finish = models.DateField(null=True, blank=True)
    forecast_finish = models.DateField(null=True, blank=True)
    actual_hours = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)],
    )
    actual_cost = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)],
    )
    work_location = models.CharField(max_length=255, blank=True)
    constraints = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    evidence = models.FileField(upload_to='planning_field_evidence/%Y/%m/%d/', null=True, blank=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='daily_field_updates_reported',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='daily_field_updates_reviewed',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)
    applied_progress_update = models.ForeignKey(
        ActivityProgressUpdate, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='source_field_updates',
    )

    class Meta:
        ordering = ['-report_date', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['activity', 'report_date'], condition=models.Q(is_deleted=False),
                name='unique_active_daily_field_update',
            ),
        ]
        indexes = [
            models.Index(fields=['version', '-report_date']),
            models.Index(fields=['version', 'status']),
        ]


class ScheduleControlSnapshot(BaseModel):
    """Immutable EVM and forecast result captured for a schedule data date."""

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='control_snapshots')
    data_date = models.DateField()
    bac = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    planned_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    earned_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    actual_cost = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    schedule_variance = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    cost_variance = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    spi = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    cpi = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    eac = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    etc = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    vac = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    progress_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    planned_progress_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    forecast_finish = models.DateField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_control_snapshots_captured',
    )

    class Meta:
        ordering = ['-data_date', '-created_at']
        unique_together = [('version', 'data_date')]
        indexes = [models.Index(fields=['version', '-data_date'])]
