"""Project-authorized planning policies, independent of extracted document facts."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class PlanningProfile(models.Model):
    project = models.ForeignKey('planning_intelligence.PlanningProject', on_delete=models.PROTECT,
                                related_name='planning_profiles')
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=160)
    version = models.PositiveIntegerField()
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft', choices=[
        ('draft', 'Draft'), ('proposed', 'Proposed'), ('approved', 'Approved'), ('rejected', 'Rejected')])
    supersedes = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='revisions')
    definition = models.JSONField(default=dict)
    content_fingerprint = models.CharField(max_length=64)
    approved_snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='planning_profiles_created')
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='planning_profiles_proposed')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='planning_profiles_approved')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='planning_profiles_decided')
    proposal_reason = models.TextField(blank=True)
    decision_reason = models.TextField(blank=True)
    proposed_at = models.DateTimeField(null=True)
    approved_at = models.DateTimeField(null=True)
    decided_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code', '-version']
        constraints = [models.UniqueConstraint(fields=['project', 'code', 'version'], name='uniq_planning_profile_version')]

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values().first()
            if old and old['status'] == 'approved':
                mutable = {'updated_at'}
                if any(getattr(self, field.attname) != old[field.attname]
                       for field in self._meta.concrete_fields if field.attname not in mutable):
                    raise ValidationError('Approved planning profiles are immutable. Create a new version.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Planning profile versions retain approval history and cannot be deleted.')


class ProjectPlanningProfileSelection(models.Model):
    project = models.OneToOneField('planning_intelligence.PlanningProject', on_delete=models.PROTECT,
                                  related_name='planning_profile_selection')
    profile = models.ForeignKey(PlanningProfile, on_delete=models.PROTECT, related_name='selections')
    revision = models.PositiveIntegerField(default=1)
    approved_snapshot = models.JSONField(default=dict)
    content_fingerprint = models.CharField(max_length=64)
    selected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='planning_profile_selections')
    reason = models.TextField()
    selected_at = models.DateTimeField(auto_now=True)
