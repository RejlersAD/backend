"""Evidence-gated EPC work, linked to the existing project and schedule."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


EPC_PHASES = [(value, value.title()) for value in ('engineering', 'procurement', 'construction', 'commissioning')]


class WorkQuerySet(models.QuerySet):
    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
                    update_fields=None, unique_fields=None):
        if update_conflicts:
            raise ValueError('EPC work cannot be overwritten by an upsert.')
        return super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts)

    def update(self, **kwargs):
        if self.filter(status='accepted').exists():
            raise ValueError('Accepted EPC work is immutable.')
        return super().update(**kwargs)

    def delete(self):
        raise ValueError('EPC work retains its acceptance history and cannot be deleted.')

    def bulk_update(self, objs, fields, batch_size=None):
        if self.model.objects.filter(pk__in=[obj.pk for obj in objs], status='accepted').exists():
            raise ValueError('Accepted EPC work is immutable.')
        return super().bulk_update(objs, fields, batch_size=batch_size)


class EPCWorkItem(BaseModel):
    objects = WorkQuerySet.as_manager()
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT, related_name='epc_work_items')
    code = models.CharField(max_length=64)
    title = models.CharField(max_length=255)
    phase = models.CharField(max_length=20, choices=EPC_PHASES)
    wbs_node = models.ForeignKey('project_control.WBSNode', on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='epc_work_owned')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='epc_work_reviews')
    activity = models.OneToOneField('planning_intelligence.ScheduleActivity', null=True, blank=True, on_delete=models.PROTECT, related_name='epc_work_item')
    baseline = models.ForeignKey('project_control.IntegratedBaseline', null=True, blank=True, on_delete=models.PROTECT)
    documents = models.ManyToManyField('project_control.ProjectDocument', blank=True, related_name='epc_work_items')
    predecessors = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='successors')
    purchase_order = models.ForeignKey('procurement.PurchaseOrder', null=True, blank=True, on_delete=models.PROTECT)
    requires_materials = models.BooleanField(default=False)
    milestone = models.OneToOneField('core.ProjectMilestone', null=True, blank=True, on_delete=models.PROTECT, related_name='epc_work_item')
    acceptance_criteria = models.JSONField(default=list)
    evidence_note = models.TextField(blank=True)
    data_date = models.DateField()
    status = models.CharField(max_length=12, default='draft', choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('reviewed', 'Reviewed'), ('accepted', 'Accepted')])
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='epc_work_submitted')
    submitted_at = models.DateTimeField(null=True)
    reviewed_at = models.DateTimeField(null=True)
    review_note = models.TextField(blank=True)
    review_manifest = models.JSONField(default=dict)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='epc_work_accepted')
    accepted_at = models.DateTimeField(null=True)
    acceptance_manifest = models.JSONField(default=dict)
    progress_update = models.ForeignKey('planning_intelligence.ActivityProgressUpdate', null=True, on_delete=models.PROTECT)
    control_snapshot = models.ForeignKey('planning_intelligence.ScheduleControlSnapshot', null=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ['code', 'id']
        constraints = [models.UniqueConstraint(fields=['project', 'code'], name='pc_epc_work_code_uniq')]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, status='accepted').exists():
            raise ValueError('Accepted EPC work is immutable.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('EPC work retains its acceptance history and cannot be deleted.')


class EventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('EPC audit events are append-only.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError('EPC audit events are append-only.')

    def delete(self):
        raise ValueError('EPC audit events are append-only.')

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
                    update_fields=None, unique_fields=None):
        if update_conflicts:
            raise ValueError('EPC audit events are append-only.')
        return super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts)


class EPCWorkEvent(models.Model):
    objects = EventQuerySet.as_manager()
    work_item = models.ForeignKey(EPCWorkItem, on_delete=models.PROTECT, related_name='events')
    action = models.CharField(max_length=20)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    note = models.TextField(blank=True)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError('EPC audit events are append-only.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('EPC audit events are append-only.')
