"""Append-only planner explanations for an exact schedule logic snapshot."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class ScheduleLogicReview(models.Model):
    project = models.ForeignKey('planning_intelligence.PlanningProject', on_delete=models.CASCADE,
                                related_name='logic_reviews')
    version = models.ForeignKey('planning_intelligence.ScheduleVersion', null=True, blank=True,
                                on_delete=models.CASCADE, related_name='logic_reviews')
    fingerprint = models.CharField(max_length=64)
    group_id = models.CharField(max_length=64)
    rationale = models.TextField(max_length=5000)
    capacity_basis = models.TextField(max_length=5000)
    duration_basis = models.TextField(max_length=5000)
    max_parallel_deliverables = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                    on_delete=models.SET_NULL, related_name='schedule_logic_reviews')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['project', 'version', 'fingerprint', 'group_id'],
                                name='plan_logic_review_lookup')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Schedule logic reviews are append-only; record a new review.')
        if self.version_id and self.version.schedule.project_id != self.project_id:
            raise ValidationError('The review version must belong to its project.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Schedule logic reviews are append-only.')
