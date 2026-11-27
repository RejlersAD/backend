"""Assign an approver to talent requisitions stuck at a stage with nobody assigned.

Run after an administrator gives someone the HR approver position or the Enquiry Management
'approve' permission. Requisitions that still have nobody eligible are listed and left as they are.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.enquiry_workflow import assign_unassigned_requisition_stage
from apps.core.models import Enquiry


class Command(BaseCommand):
    help = 'Assign approvers to pending talent requisition stages that have none.'

    def handle(self, *args, **options):
        assigned = waiting = 0
        pending = Enquiry.objects.filter(inquiry_type='talent_requisition', approval_status='pending').exclude(
            status__in=['resolved', 'closed', 'spam']).order_by('pk')
        for row in pending:
            with transaction.atomic():
                enquiry = Enquiry.objects.select_for_update().get(pk=row.pk)
                stages = (enquiry.requisition_details or {}).get('approval_stages') or []
                index = int((enquiry.requisition_details or {}).get('current_stage', 0) or 0)
                if not (0 <= index < len(stages)) or stages[index].get('approver_id'):
                    continue
                approver = assign_unassigned_requisition_stage(enquiry)
            if approver:
                assigned += 1
                self.stdout.write(f'{enquiry.reference}: assigned to {approver.get_full_name() or approver.username}')
            else:
                waiting += 1
                self.stdout.write(f'{enquiry.reference}: still no eligible HR approver')
        self.stdout.write(self.style.SUCCESS(f'Assigned {assigned}; still waiting {waiting}.'))
