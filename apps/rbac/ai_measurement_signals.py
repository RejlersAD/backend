from django.db.models.signals import post_save
from django.dispatch import receiver
from .ai_telemetry import bind_result


@receiver(post_save, sender='pid_analysis.PIDAnalysisReport')
def link_pid_result(sender, instance, created, **kwargs):
    if created:
        bind_result('pid_analysis.PIDAnalysisReport', instance.pk)


@receiver(post_save, sender='pfd_converter.PIDConversion')
def link_conversion_result(sender, instance, **kwargs):
    if instance.status in ('completed', 'approved'):
        bind_result('pfd_converter.PIDConversion', instance.pk)


@receiver(post_save, sender='designiq.ProcessedPIDOutput')
def link_design_result(sender, instance, created, **kwargs):
    if created:
        bind_result('designiq.ProcessedPIDOutput', instance.pk)
