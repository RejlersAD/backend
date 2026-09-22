"""Governed operational observations, separate from the immutable planning baseline."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ApprovedControlRecord(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values().first()
            if previous and previous['status'] in {'approved', 'published'}:
                if any(getattr(self, field.attname) != previous[field.attname]
                       for field in self._meta.concrete_fields):
                    raise ValidationError('Approved controls are immutable. Create a revision.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Operational control history cannot be deleted.')


class OperationalEarningPolicy(ApprovedControlRecord):
    project = models.ForeignKey('planning_intelligence.PlanningProject', on_delete=models.PROTECT,
                                related_name='operational_earning_policies')
    baseline = models.ForeignKey('planning_intelligence.ScheduleBaseline', on_delete=models.PROTECT,
                                 related_name='earning_policies')
    name = models.CharField(max_length=160)
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft', choices=[('draft', 'Draft'), ('approved', 'Approved')])
    definition = models.JSONField(default=dict)
    baseline_fingerprint = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='operational_policies_created')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                    related_name='operational_policies_approved')
    approval_reason = models.TextField(blank=True)
    approved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class OperationalControlReport(ApprovedControlRecord):
    project = models.ForeignKey('planning_intelligence.PlanningProject', on_delete=models.PROTECT,
                                related_name='operational_control_reports')
    baseline = models.ForeignKey('planning_intelligence.ScheduleBaseline', on_delete=models.PROTECT,
                                 related_name='operational_reports')
    policy = models.ForeignKey(OperationalEarningPolicy, on_delete=models.PROTECT, related_name='reports')
    reporting_period = models.ForeignKey('project_control.ReportingPeriod', on_delete=models.PROTECT,
                                         related_name='operational_reports')
    supersedes = models.ForeignKey('self', on_delete=models.PROTECT, null=True, related_name='corrections')
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft', choices=[
        ('draft', 'Draft'), ('submitted', 'Submitted'), ('published', 'Published')])
    period_snapshot = models.JSONField(default=dict)
    observations = models.JSONField(default=list)
    cost_coverage_confirmed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    submission_fingerprint = models.CharField(max_length=64, blank=True)
    publication = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='operational_reports_created')
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                   related_name='operational_reports_edited')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                     related_name='operational_reports_submitted')
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                     related_name='operational_reports_published')
    submitted_at = models.DateTimeField(null=True)
    published_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['baseline', 'reporting_period'],
            condition=models.Q(status__in=['draft', 'submitted']), name='one_open_operational_report')]
