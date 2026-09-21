"""Risk management state with immutable source statements and audit history."""
from django.conf import settings
from django.core.exceptions import ValidationError
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
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        constraints = [models.UniqueConstraint(fields=['version', 'source_key'], name='unique_planning_risk_source')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            immutable = ('version_id', 'source_key', 'title', 'description', 'provenance')
            previous = type(self).objects.filter(pk=self.pk).values(*immutable).first()
            if previous and any(previous[key] != getattr(self, key) for key in immutable):
                raise ValidationError('Risk source statements are immutable; record management decisions separately.')
        return super().save(*args, **kwargs)
