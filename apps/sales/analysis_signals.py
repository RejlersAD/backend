"""Source changes leave a durable refresh intent for already enabled reports."""
from django.db import transaction
from django.db.models import F, Q
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.db.utils import OperationalError, ProgrammingError
from rest_framework.exceptions import PermissionDenied
import logging

from .models import Deal, OpportunityDocument, OpportunityWorkspaceUpload, SalesEmailIntake


UPLOAD_SOURCE_FIELDS = (
    'status', 'name', 'folder_key', 'size', 'sha256', 'provider', 'storage_fingerprint',
    'storage_encoding', 'stored_sha256', 'result', 'document_id', 'version_number', 'workspace_id',
)


@receiver(pre_save, sender=OpportunityWorkspaceUpload)
def record_analysis_upload_state(sender, instance, raw=False, update_fields=None, **kwargs):
    instance._analysis_previous = None
    if raw or not instance.pk or (update_fields is not None and not set(update_fields) & set(UPLOAD_SOURCE_FIELDS)):
        return
    instance._analysis_previous = OpportunityWorkspaceUpload.objects.filter(pk=instance.pk).values(
        *UPLOAD_SOURCE_FIELDS).first()


@receiver(post_save, sender=Deal)
@receiver(post_save, sender=SalesEmailIntake)
@receiver(post_delete, sender=SalesEmailIntake)
@receiver(post_save, sender=OpportunityDocument)
@receiver(post_delete, sender=OpportunityDocument)
@receiver(post_save, sender=OpportunityWorkspaceUpload)
@receiver(post_delete, sender=OpportunityWorkspaceUpload)
def refresh_opportunity_analysis(sender, instance, signal=None, raw=False, created=False, update_fields=None, **kwargs):
    if raw:
        return
    if sender is OpportunityWorkspaceUpload and signal is post_delete and instance.status != 'ready':
        return
    if sender is OpportunityWorkspaceUpload and signal is post_save:
        previous = getattr(instance, '_analysis_previous', None)
        if not created and previous is None:
            return
        if instance.status != 'ready' and (previous is None or previous['status'] != 'ready'):
            return
        if previous and all(previous[field] == getattr(instance, field) for field in UPLOAD_SOURCE_FIELDS):
            return
    if sender is Deal:
        opportunity_id = instance.pk
    elif sender is SalesEmailIntake:
        opportunity_id = instance.opportunity_id
    else:
        opportunity_id = instance.workspace.opportunity_id
    if opportunity_id:
        def invalidate():
            from .opportunity_analysis import authority, mark_dirty, request_analysis
            from .opportunity_workspace import _actor
            try:
                mark_dirty(opportunity_id)
                if sender is OpportunityWorkspaceUpload and instance.actor_id:
                    upload = OpportunityWorkspaceUpload.objects.filter(pk=instance.pk, status='ready').filter(
                        Q(document__isnull=True) | Q(document__head_upload_id=F('pk'))).first()
                    if upload:
                        actor = _actor(upload.actor_id)
                        deal = Deal.objects.get(pk=opportunity_id)
                        try:
                            authority(deal, actor)
                        except PermissionDenied:
                            logging.getLogger(__name__).info('Opportunity upload actor cannot enable analysis')
                        else:
                            request_analysis(deal, actor)
            except (OperationalError, ProgrammingError):
                # Optional analysis must not turn a successful existing source
                # write into an apparent failure during migration/outage.
                logging.getLogger(__name__).warning('Opportunity analysis refresh unavailable')
        transaction.on_commit(invalidate)
