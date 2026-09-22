"""Risk management state with immutable source statements and audit history."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class PlanningRiskRecord(models.Model):
    version = models.ForeignKey('ScheduleVersion', on_delete=models.PROTECT, related_name='planning_risks')
    source_key = models.CharField(max_length=128)
    title = models.CharField(max_length=255)
    description = models.TextField()
    provenance = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default='open', choices=[(value, value.title()) for value in ('open', 'monitoring', 'mitigated', 'closed')])
    priority = models.CharField(max_length=16, null=True, blank=True, choices=[(value, value.title()) for value in ('low', 'medium', 'high', 'critical')])
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='planning_risks_owned')
    response = models.TextField(blank=True)
    resolution = models.TextField(blank=True)
    probability_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    cost_impact = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)])
    impact_currency = models.CharField(max_length=3, blank=True)
    schedule_impact_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)])
    impact_basis = models.TextField(blank=True)
    mitigation_due_date = models.DateField(null=True, blank=True)
    mitigation_status = models.CharField(max_length=16, default='not_planned', choices=[
        (value, value.replace('_', ' ').title()) for value in ('not_planned', 'planned', 'in_progress', 'completed')])
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['version', 'source_key'], name='unique_planning_risk_source'),
            models.CheckConstraint(check=models.Q(probability_percent__isnull=True) | models.Q(
                probability_percent__gte=0, probability_percent__lte=100), name='planning_risk_probability_range'),
            models.CheckConstraint(check=models.Q(cost_impact__isnull=True) | models.Q(cost_impact__gte=0),
                name='planning_risk_cost_nonnegative'),
            models.CheckConstraint(check=models.Q(schedule_impact_days__isnull=True) | models.Q(schedule_impact_days__gte=0),
                name='planning_risk_delay_nonnegative'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            immutable = ('version_id', 'source_key', 'title', 'description', 'provenance')
            previous = type(self).objects.filter(pk=self.pk).values(*immutable).first()
            if previous and any(previous[key] != getattr(self, key) for key in immutable):
                raise ValidationError('Risk source statements are immutable; record management decisions separately.')
        return super().save(*args, **kwargs)
