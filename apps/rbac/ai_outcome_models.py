import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class AIOutcomeEvidence(models.Model):
    """A comparable task observation, frozen after independent review."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('rbac.Organization', on_delete=models.PROTECT)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='ai_outcomes_submitted')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='ai_outcomes_reviewed')
    created_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True)
    module = models.ForeignKey('rbac.Module', on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    task_reference = models.CharField(max_length=200)
    evidence_url = models.URLField(max_length=1000)
    comparison = models.TextField()
    baseline_minutes = models.PositiveIntegerField()
    ai_minutes = models.PositiveIntegerField()
    review_minutes = models.PositiveIntegerField()
    rework_minutes = models.PositiveIntegerField()
    measurement = models.CharField(max_length=20, choices=[('measured', 'Measured'), ('self_reported', 'Self-reported')])
    status = models.CharField(max_length=20, default='pending', choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')])
    review_reason = models.TextField(blank=True)
    workflow = models.OneToOneField('rbac.AIWorkflowRun', null=True, blank=True, on_delete=models.PROTECT, related_name='outcome')
    contribution_type = models.CharField(max_length=32, default='task', choices=[('task', 'Task'), ('workflow_integration', 'Workflow integration'), ('automation_creator', 'Automation creator')])
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    value_currency = models.CharField(max_length=3, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['organization', 'module', 'task_reference'], name='unique_ai_outcome_task')]

    @property
    def saved_minutes(self):
        return self.baseline_minutes - self.ai_minutes - self.review_minutes - self.rework_minutes
