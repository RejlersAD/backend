"""Immutable, reproducible applications of accepted evidence and planning policies."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class PlanningBuild(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('PlanningProject', on_delete=models.PROTECT, related_name='planning_builds')
    evidence_graph = models.ForeignKey('EvidenceGraph', on_delete=models.PROTECT, related_name='planning_builds')
    evidence_revision = models.PositiveIntegerField()
    profile = models.ForeignKey('PlanningProfile', on_delete=models.PROTECT, related_name='planning_builds')
    profile_selection_revision = models.PositiveIntegerField()
    source_fingerprint = models.CharField(max_length=64)
    profile_fingerprint = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    rule_version = models.CharField(max_length=64)
    options = models.JSONField(default=dict)
    evidence_snapshot = models.JSONField(default=dict)
    profile_snapshot = models.JSONField(default=dict)
    plan = models.JSONField(default=dict)
    issues = models.JSONField(default=list)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='planning_builds_created')
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Planning builds are immutable. Create a new preview.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Planning builds preserve planning provenance and cannot be deleted.')
