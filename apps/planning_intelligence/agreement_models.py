"""Source-grounded agreement drafts shared by the project workspace tabs."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseModel


class AgreementWorkspace(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey('planning_intelligence.PlanningProject', on_delete=models.CASCADE,
                                related_name='agreement_workspaces')
    job = models.OneToOneField('planning_intelligence.PlanningJob', on_delete=models.PROTECT,
                              null=True, blank=True, related_name='agreement_workspace')
    version = models.PositiveIntegerField(default=1)
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=[('draft', 'Draft'), ('partial', 'Partial'),
                                                     ('accepted', 'Accepted')], default='draft')
    source_fingerprint = models.CharField(max_length=64)
    source_manifest = models.JSONField(default=list)
    candidates = models.JSONField(default=list)
    exceptions = models.JSONField(default=list)
    projection = models.JSONField(default=dict)
    accepted_fact_ids = models.JSONField(default=list)
    materialization = models.JSONField(default=dict)
    analysis_metadata = models.JSONField(default=dict)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                     related_name='agreement_workspaces_requested')
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='agreement_workspaces_accepted')
    accepted_at = models.DateTimeField(null=True, blank=True)

    IMMUTABLE_FIELDS = ('project_id', 'job_id', 'version', 'source_fingerprint', 'source_manifest',
                        'candidates', 'analysis_metadata')

    class Meta:
        ordering = ['-version', '-created_at']
        constraints = [models.UniqueConstraint(fields=['project', 'version'], name='unique_agreement_workspace_version')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.filter(pk=self.pk).values(*self.IMMUTABLE_FIELDS).first()
            if previous and any(previous[field] != getattr(self, field) for field in self.IMMUTABLE_FIELDS):
                raise ValidationError('Agreement source assertions are immutable; analyze a new version instead.')
        return super().save(*args, **kwargs)
