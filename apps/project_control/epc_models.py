"""Explicit EPC source links and immutable integrated baseline evidence."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


EPC_LINK_TYPES = [(value, label) for value, label in (
    ('engineering', 'Engineering'), ('procurement', 'Procurement'),
    ('construction', 'Construction'), ('commissioning', 'Commissioning'),
)]
EPC_SCOPE_TYPES = (('epc', 'Full EPC'), ('detailed_engineering', 'Detailed Engineering'))


def control_scope(scope_type):
    """Contractual ownership is separate from the four-phase schedule structure."""
    phases = [value for value, _ in EPC_LINK_TYPES]
    engineering = scope_type == 'detailed_engineering'
    return {
        'scope_type': scope_type,
        'owned_phases': ['engineering'] if engineering else phases,
        'dependency_phases': phases[1:] if engineering else [],
        'description': ('Engineering is the owned delivery scope. Procurement, Construction and '
                        'Commissioning are external dependencies and do not earn project progress or budget.'
                        if engineering else 'All four EPC phases are within the owned delivery scope.'),
    }


class WBSActivityLink(BaseModel):
    project = models.ForeignKey('core.Project', on_delete=models.CASCADE, related_name='epc_activity_links')
    wbs_node = models.ForeignKey('project_control.WBSNode', on_delete=models.PROTECT, related_name='activity_links')
    activity = models.OneToOneField('planning_intelligence.ScheduleActivity', on_delete=models.PROTECT, related_name='enterprise_wbs_link')
    link_type = models.CharField(max_length=20, choices=EPC_LINK_TYPES)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['wbs_node__sort_order', 'id']


class RequisitionWBSLink(BaseModel):
    project = models.ForeignKey('core.Project', on_delete=models.CASCADE, related_name='epc_requisition_links')
    requisition = models.OneToOneField('procurement.PurchaseRequisition', on_delete=models.PROTECT, related_name='enterprise_wbs_link')
    wbs_node = models.ForeignKey('project_control.WBSNode', on_delete=models.PROTECT, related_name='requisition_links')
    reason = models.CharField(max_length=500)
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-updated_at', '-id']


class ImmutableBaselineQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('Integrated baselines are immutable. Capture a new revision.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError('Integrated baselines are immutable. Capture a new revision.')

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False,
                    update_fields=None, unique_fields=None):
        if update_conflicts:
            raise ValueError('Integrated baselines are immutable. Capture a new revision.')
        return super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts)

    def delete(self):
        raise ValueError('Integrated baselines cannot be deleted.')


class IntegratedBaseline(models.Model):
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT, related_name='integrated_baselines')
    revision = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    data_date = models.DateField()
    schedule_baseline = models.ForeignKey('planning_intelligence.ScheduleBaseline', on_delete=models.PROTECT, related_name='integrated_baselines')
    currency = models.CharField(max_length=8)
    budget_total = models.DecimalField(max_digits=20, decimal_places=2)
    manifest = models.JSONField(default=dict)
    checksum = models.CharField(max_length=64)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableBaselineQuerySet.as_manager()

    class Meta:
        ordering = ['-revision', '-id']
        constraints = [
            models.UniqueConstraint(fields=['project', 'revision'], name='pc_epc_baseline_revision_uniq'),
            models.CheckConstraint(check=models.Q(revision__gte=1), name='pc_epc_revision_positive'),
            models.CheckConstraint(check=models.Q(budget_total__gt=0), name='pc_epc_baseline_budget_positive'),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError('Integrated baselines are immutable. Capture a new revision.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Integrated baselines cannot be deleted.')
