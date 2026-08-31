from celery import shared_task


@shared_task(name='core.process_enquiry_sla_escalations')
def process_enquiry_sla_escalations():
    from apps.core.enquiry_workflow import process_sla_escalations
    return {'processed': process_sla_escalations()}
