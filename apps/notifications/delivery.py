"""Revalidate queued notifications against the current recipient and approval."""

from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError


def safe_action_url(value):
    """Browser notification actions stay within the configured RADAI origin."""
    value = str(value or '').strip()
    if not value or '\\' in value or any(ord(char) < 32 for char in value):
        return '/notifications'
    try:
        parsed = urlsplit(value)
        frontend = urlsplit(str(getattr(settings, 'FRONTEND_URL', '') or ''))
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme, parsed.netloc) != (frontend.scheme, frontend.netloc):
                return '/notifications'
            if parsed.scheme not in ('https', 'http'):
                return '/notifications'
        elif not value.startswith('/') or value.startswith('//'):
            return '/notifications'
        return urlunsplit(('', '', parsed.path or '/', parsed.query, parsed.fragment))
    except ValueError:
        return '/notifications'


def notification_action_url(notification):
    metadata = getattr(notification, 'metadata', None) or {}
    if isinstance(metadata, dict) and _is_approval_assignment(metadata):
        if metadata.get('enquiry_id'):
            return safe_action_url(f"/admin/enquiries/{metadata['enquiry_id']}")
        if metadata.get('po_id'):
            return safe_action_url(f"/procurement/orders/{metadata['po_id']}")
        if metadata.get('pr_id'):
            return safe_action_url(f"/procurement/requisitions/{metadata['pr_id']}")
    return safe_action_url(getattr(notification, 'action_url', None))


def absolute_action_url(value):
    frontend = str(getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    return frontend + safe_action_url(value)


def _is_approval_assignment(metadata):
    return bool(metadata.get('requires_action') or metadata.get('workflow_task_id')) or metadata.get('event_type') in {
        'approval_assignment', 'hr_approval_assignment', 'proposal_approval_assignment', 'enquiry_approval_assignment',
    }


def _is_legacy_leave_assignment(notification, metadata):
    # The same leave_request_id is used for employee result messages. Only
    # the historical approval sender's exact title/label identifies a task.
    return bool(metadata.get('leave_request_id')) and (
        _is_approval_assignment(metadata)
        or (str(getattr(notification, 'title', '') or '').strip() == 'HR leave approval required'
            and str(getattr(notification, 'action_label', '') or '').strip() == 'Review leave')
    )


def _is_actionable_notice(notification, metadata):
    if _is_approval_assignment(metadata):
        return True
    if metadata.get('requires_action') is False:
        return False
    event = str(metadata.get('event_type') or '').strip().lower()
    if event in {'purchase_order_created', 'po_created', 'approval_result', 'hr_request_result', 'proposal_result'}:
        return False
    title = str(getattr(notification, 'title', '') or '').strip().lower()
    if title in {
        'leave request updated', 'overtime request updated',
        'technical proposal approved', 'technical proposal rejected', 'technical proposal issued',
        'technical proposal approval reassigned', 'technical proposal review reassigned',
        'technical proposal returned for revision', 'new purchase order created',
    }:
        return False
    category = getattr(notification, 'category', None)
    return getattr(category, 'name', None) == 'APPROVAL'


def _hr_assignment_issue(notification, metadata):
    if metadata.get('workflow_task_id'):
        from apps.hr_core.models import HRWorkflowTask
        from apps.hr_core.workflows import HRWorkflowService

        task = HRWorkflowTask.objects.select_related(
            'stage', 'instance__employee', 'instance__definition', 'assigned_to',
        ).filter(pk=metadata['workflow_task_id']).first()
        if task is None or not HRWorkflowService.task_is_current(task):
            return 'approval_no_longer_current'
        if not HRWorkflowService.can_act(task, notification.recipient):
            return 'approval_no_longer_assigned'
        if not any(user.pk == notification.recipient_id for user in HRWorkflowService._task_recipients(task)):
            return 'approval_recipient_changed'
        return ''
    from apps.payroll.models import LeaveRequest
    from apps.payroll.services.leave_approval import can_review

    request = LeaveRequest.objects.select_related('workflow_instance', 'canonical_employee').filter(
        pk=metadata['leave_request_id'],
    ).first()
    return '' if request and can_review(request, notification.recipient) else 'approval_no_longer_assigned'


def _proposal_assignment_issue(notification, metadata):
    from apps.planning_intelligence.access import can_decide_proposal_task
    from apps.planning_intelligence.models import TechnicalProposal

    proposal = TechnicalProposal.objects.select_related('project', 'schedule_version__schedule').filter(
        pk=metadata['proposal_id'],
    ).first()
    if proposal is None:
        return 'approval_no_longer_current'
    task_type = metadata.get('task_type') or (
        'approval' if str(getattr(notification, 'title', '') or '').strip() == 'Technical proposal approval required'
        else 'review'
    )
    tasks = proposal.workflow_tasks.filter(
        is_deleted=False, status='pending', assigned_to_id=notification.recipient_id, task_type=task_type,
    )
    if metadata.get('proposal_task_id'):
        tasks = tasks.filter(pk=metadata['proposal_task_id'])
    task = tasks.order_by('-created_at', '-pk').first()
    if task is None or not can_decide_proposal_task(proposal, notification.recipient, task):
        return 'approval_no_longer_assigned'
    # Historical notices did not store task IDs. A new assignment to the same
    # employee must not revive an old request from a previous review cycle.
    notice_date = getattr(notification, 'created_at', None)
    if not metadata.get('proposal_task_id') and notice_date and task.created_at > notice_date:
        return 'approval_assignment_changed'
    return ''


def _offboarding_assignment_issue(notification, metadata):
    from apps.onboarding.models import OffboardingRecord
    from apps.onboarding.rbac import can_decide_exit_project

    record = OffboardingRecord.objects.filter(pk=metadata['offboarding_id']).first()
    return '' if record and can_decide_exit_project(record, notification.recipient) else 'approval_no_longer_assigned'


def _payroll_assignment_issue(notification, metadata):
    from apps.finance.payroll_workflow import PayrollWorkflow, PayrollWorkflowService

    workflow = PayrollWorkflow.objects.filter(pk=metadata['finance_payroll_workflow_id']).first()
    return '' if workflow and PayrollWorkflowService.can_review(workflow, notification.recipient) else 'approval_no_longer_assigned'


def _enquiry_assignment_issue(notification, metadata):
    from apps.core.models import Enquiry
    from apps.core.enquiry_workflow import can_approve_enquiry
    enquiry = Enquiry.objects.select_related('assigned_to').filter(pk=metadata['enquiry_id']).first()
    if enquiry is None or not can_approve_enquiry(notification.recipient, enquiry):
        return 'approval_no_longer_assigned'
    if not enquiry.assigned_at or not metadata.get('assigned_at'):
        return 'approval_context_invalid'
    if metadata['assigned_at'] != enquiry.assigned_at.isoformat():
        return 'approval_assignment_changed'
    return ''


def approval_assignment_issue(notification):
    """Return a skip reason when this particular approval request is obsolete."""
    metadata = getattr(notification, 'metadata', None) or {}
    if not isinstance(metadata, dict):
        return 'approval_context_invalid' if getattr(getattr(notification, 'category', None), 'name', None) == 'APPROVAL' else ''
    if metadata.get('workflow_task_id') or _is_legacy_leave_assignment(notification, metadata):
        try:
            return _hr_assignment_issue(notification, metadata)
        except (KeyError, TypeError, ValueError, DjangoValidationError, ValidationError):
            return 'approval_context_invalid'
    if not _is_actionable_notice(notification, metadata):
        return ''
    if metadata.get('enquiry_id'):
        try:
            return _enquiry_assignment_issue(notification, metadata)
        except (KeyError, TypeError, ValueError, DjangoValidationError, ValidationError):
            return 'approval_context_invalid'
    if metadata.get('finance_payroll_workflow_id'):
        try:
            return _payroll_assignment_issue(notification, metadata)
        except (KeyError, TypeError, ValueError, DjangoValidationError, ValidationError):
            return 'approval_context_invalid'
    if metadata.get('offboarding_id') and metadata.get('action_type') == 'offboarding_project_manager_decision':
        try:
            return _offboarding_assignment_issue(notification, metadata)
        except (KeyError, TypeError, ValueError, DjangoValidationError, ValidationError):
            return 'approval_context_invalid'
    if metadata.get('proposal_id'):
        try:
            return _proposal_assignment_issue(notification, metadata)
        except (KeyError, TypeError, ValueError, DjangoValidationError, ValidationError):
            return 'approval_context_invalid'
    if not metadata.get('po_id') and not metadata.get('pr_id'):
        return 'approval_context_unsupported'
    try:
        expected_level = int(metadata['approval_level'])
        recipient = notification.recipient
        if metadata.get('po_id'):
            from apps.procurement.models import PurchaseOrder
            from apps.procurement.services import purchase_order_approvals as approvals
            from apps.procurement.services.approval_eligibility import MODULE_PO, eligible_stage_assignee

            order = PurchaseOrder.objects.filter(pk=metadata['po_id']).first()
            if order is None or not approvals.can_approve(order, recipient):
                return 'approval_no_longer_assigned'
            for index, entry in approvals._active_entries(order.approval_log or []):
                if not approvals._entry_matches_user(entry, recipient):
                    continue
                if not eligible_stage_assignee(recipient, entry, MODULE_PO):
                    continue
                if approvals._entry_level(entry, index) != expected_level:
                    continue
                if str(entry.get('stage') or '') != str(metadata.get('approval_stage') or ''):
                    continue
                if str(entry.get('assignment_id') or '') != str(metadata.get('assignment_id') or ''):
                    continue
                return ''
        else:
            from apps.procurement.models import PurchaseRequisition
            from apps.procurement.services.requisition_workflow import RequisitionWorkflowService as approvals
            from apps.procurement.services.approval_eligibility import MODULE_PR, eligible_stage_assignee

            requisition = PurchaseRequisition.objects.filter(pk=metadata['pr_id']).first()
            if requisition is None or not approvals.can_approve(requisition, recipient):
                return 'approval_no_longer_assigned'
            workflow = approvals._workflow(requisition)
            active_level, stages = approvals._active_level_stages(requisition, workflow)
            if active_level == expected_level and any(
                approvals._stage_matches_user(stage, recipient)
                and eligible_stage_assignee(recipient, stage, MODULE_PR)
                and str(stage.get('assignment_id') or '') == str(metadata.get('assignment_id') or '')
                for _, stage in stages
            ):
                return ''
    except (KeyError, TypeError, ValueError, DjangoValidationError, ValidationError):
        return 'approval_context_invalid'
    return 'approval_assignment_changed'


def delivery_issue(notification, channel):
    """Run immediately before each attempted delivery, including retries."""
    recipient = notification.recipient
    if not getattr(recipient, 'is_active', True):
        return 'recipient_inactive'
    try:
        profile = getattr(recipient, 'rbac_profile', None)
    except ObjectDoesNotExist:
        profile = None
    if profile is not None and (profile.is_deleted or profile.status != 'active'):
        return 'recipient_inactive'
    if getattr(notification, 'status', None) == 'ARCHIVED':
        return 'notification_archived'
    expires_at = getattr(notification, 'expires_at', None)
    if expires_at and expires_at <= timezone.now():
        return 'notification_expired'
    if channel == 'web_push' and (
        not notification.send_in_app or notification.is_read or notification.status != 'SENT'
    ):
        return 'notification_not_unread'
    if channel == 'email' and (not notification.send_email or notification.email_sent):
        return 'email_not_required'
    try:
        preferences = getattr(recipient, 'notification_preferences', None)
    except ObjectDoesNotExist:
        preferences = None
    if preferences is not None:
        if channel == 'web_push' and not preferences.enable_in_app:
            return 'channel_disabled'
        if channel == 'email' and not preferences.enable_email:
            return 'channel_disabled'
    return approval_assignment_issue(notification)
