"""Evidence-linked delay cases and immutable calculation/review records."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class DelayEvent(models.Model):
    project = models.ForeignKey('planning_intelligence.PlanningProject', on_delete=models.PROTECT, related_name='delay_events')
    baseline = models.ForeignKey('planning_intelligence.ScheduleBaseline', on_delete=models.PROTECT, related_name='delay_events')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, default='recorded', choices=[('recorded', 'Recorded'), ('closed', 'Closed')])
    activity_ids = models.JSONField(default=list)
    evidence = models.JSONField(default=list)
    governance_item = models.ForeignKey('planning_intelligence.GovernanceItem', on_delete=models.PROTECT, null=True, blank=True, related_name='delay_events')
    risk = models.ForeignKey('planning_intelligence.PlanningRiskRecord', on_delete=models.PROTECT, null=True, blank=True, related_name='delay_events')
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='delay_events_created')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='delay_events_updated')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values('project_id', 'baseline_id').first()
            if previous and (previous['project_id'] != self.project_id or previous['baseline_id'] != self.baseline_id):
                raise ValidationError('An event cannot be moved to a different project or baseline.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Delay events retain their evidence history; close the event instead.')


class DelayAnalysisCase(models.Model):
    project = models.ForeignKey('planning_intelligence.PlanningProject', on_delete=models.PROTECT, related_name='delay_cases')
    baseline = models.ForeignKey('planning_intelligence.ScheduleBaseline', on_delete=models.PROTECT, related_name='delay_cases')
    reference_report = models.ForeignKey('planning_intelligence.OperationalControlReport', on_delete=models.PROTECT, related_name='delay_cases')
    supersedes = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='revisions')
    name = models.CharField(max_length=255)
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft', choices=[
        ('draft', 'Draft'), ('calculated', 'Calculated'), ('submitted', 'Submitted'),
        ('approved', 'Reviewed'), ('rejected', 'Rejected')])
    event_ids = models.JSONField(default=list)
    changes = models.JSONField(default=list)
    scenarios = models.JSONField(default=list)
    recommendation = models.JSONField(default=dict)
    current_run = models.ForeignKey('planning_intelligence.DelayAnalysisRun', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    submission_fingerprint = models.CharField(max_length=64, blank=True)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='delay_cases_created')
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='delay_cases_edited')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name='delay_cases_submitted')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name='delay_cases_reviewed')
    submitted_at = models.DateTimeField(null=True)
    reviewed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values().first()
            if previous:
                if any(previous[key] != getattr(self, key) for key in ('project_id', 'baseline_id', 'reference_report_id')):
                    raise ValidationError('Create a new case to change the baseline or published reference report.')
                if previous['status'] in {'approved', 'rejected'} and any(
                    previous[field.attname] != getattr(self, field.attname) for field in self._meta.concrete_fields):
                    raise ValidationError('Reviewed delay cases are immutable; create a revision.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Delay analysis review history cannot be deleted.')


class DelayAnalysisRun(models.Model):
    case = models.ForeignKey(DelayAnalysisCase, on_delete=models.PROTECT, related_name='runs')
    case_revision = models.PositiveIntegerField()
    fingerprint = models.CharField(max_length=64)
    input_snapshot = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='delay_runs_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError('Delay analysis runs are immutable; calculate a new run.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Delay analysis runs cannot be deleted.')
