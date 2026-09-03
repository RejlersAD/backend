"""Background jobs for the reusable HR workflow engine."""

from celery import shared_task


@shared_task(name='hr_core.process_overdue_workflows')
def process_overdue_workflows():
    from .workflows import HRWorkflowService

    return HRWorkflowService.process_overdue_tasks()
