"""Background jobs for the reusable HR workflow engine."""

from celery import shared_task


@shared_task(name='hr_core.process_overdue_workflows')
def process_overdue_workflows():
    from .workflows import HRWorkflowService

    return HRWorkflowService.process_overdue_tasks()


@shared_task(name='hr_core.sync_rejlers_positions', soft_time_limit=240, time_limit=300)
def sync_rejlers_positions():
    from django.conf import settings
    if not settings.RECRUITMENT_CAREERS_SYNC_ENABLED:
        return {'status': 'disabled'}
    from .recruitment_sync import sync_positions
    return sync_positions()
