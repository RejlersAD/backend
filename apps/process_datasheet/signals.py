"""
Process Datasheet Signals
Automatic actions when datasheets are created/updated
"""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import ProcessDatasheet, DatasheetRevision


@receiver(pre_save, sender=ProcessDatasheet)
def auto_increment_revision(sender, instance, **kwargs):
    """Automatically track revision changes"""
    if instance.pk:  # Existing datasheet
        try:
            old_instance = ProcessDatasheet.objects.get(pk=instance.pk)
            
            # Check if significant data changed
            if old_instance.data != instance.data:
                # Data changed, create revision history
                if old_instance.revision == instance.revision:
                    # Revision not manually incremented, auto-increment
                    instance.revision += 1
        except ProcessDatasheet.DoesNotExist:
            pass


@receiver(post_save, sender=ProcessDatasheet)
def create_initial_revision(sender, instance, created, **kwargs):
    """Create initial revision when datasheet is first created"""
    if created:
        DatasheetRevision.objects.create(
            datasheet=instance,
            revision_number=0,
            description='Initial creation',
            data_snapshot=instance.data.copy(),
            revised_by=instance.prepared_by
        )
