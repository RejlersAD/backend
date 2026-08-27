"""Controlled review, approval, issue, and notification workflow for proposals."""
from __future__ import annotations

import hashlib

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.notifications.services import NotificationService

from ..access import (
    can_approve_proposal, can_write_project, proposal_approver_users,
    proposal_reviewer_users,
)
from ..models import ProposalExportRecord, ProposalWorkflowTask, TechnicalProposal
from .audit import record_event
from .proposal_exports import generate_proposal_export


def _notify(recipient, *, sender, title, message, proposal, priority='HIGH'):
    if not recipient:
        return
    NotificationService.create_notification(
        recipient=recipient, sender=sender, title=title, message=message,
        category='APPROVAL', priority=priority, send_email=False,
        action_url=f'/proposal-workspace/{proposal.project_id}',
        action_label='Open Proposal',
        metadata={
            'proposal_id': proposal.id, 'project_id': proposal.project_id,
            'proposal_number': proposal.proposal_number, 'revision': proposal.revision,
        },
    )


def _cancel_pending_tasks(proposal, task_type=None):
    rows = proposal.workflow_tasks.filter(status='pending')
    if task_type:
        rows = rows.filter(task_type=task_type)
    rows.update(status='cancelled', completed_at=timezone.now(), updated_at=timezone.now())


@transaction.atomic
def submit_for_review(proposal_id, actor, *, reviewer_id, due_date=None, comments=''):
    proposal = TechnicalProposal.objects.select_for_update().select_related('project').get(pk=proposal_id)
    if proposal.status != 'draft':
        raise ValidationError('Only a draft proposal can be submitted for review.')
    if not can_write_project(actor, proposal.project):
        raise PermissionDenied('You cannot submit this proposal for review.')
    reviewer = proposal_reviewer_users(proposal.project).filter(pk=reviewer_id).first()
    if not reviewer:
        raise ValidationError({'reviewer': 'Select an active member of this project.'})
    if reviewer.pk == proposal.created_by_id:
        raise ValidationError({'reviewer': 'The proposal author cannot review their own proposal.'})

    _cancel_pending_tasks(proposal)
    task = ProposalWorkflowTask.objects.create(
        proposal=proposal, task_type='review', assigned_to=reviewer,
        assigned_by=actor, due_date=due_date, comments=comments,
    )
    proposal.status = 'internal_review'
    proposal.reviewer = reviewer
    proposal.review_due_date = due_date
    proposal.review_submitted_at = timezone.now()
    proposal.review_comments = comments
    proposal.checked_by = None
    proposal.review_completed_at = None
    proposal.save(update_fields=[
        'status', 'reviewer', 'review_due_date', 'review_submitted_at',
        'review_comments', 'checked_by', 'review_completed_at', 'updated_at',
    ])
    _notify(
        reviewer, sender=actor, title='Technical proposal review required',
        message=f'{proposal.proposal_number} Rev {proposal.revision} is awaiting your technical review.',
        proposal=proposal,
    )
    record_event(
        project=proposal.project, actor=actor, action='proposal.review_submitted', entity=proposal,
        after={'reviewer_id': reviewer.id, 'due_date': str(due_date or ''), 'task_id': task.id},
    )
    return proposal


@transaction.atomic
def reassign_reviewer(proposal_id, actor, *, reviewer_id, due_date=None, comments=''):
    proposal = TechnicalProposal.objects.select_for_update(of=('self',)).select_related('project').get(pk=proposal_id)
    if proposal.status != 'internal_review':
        raise ValidationError('The reviewer can only be changed while technical review is active.')
    if not can_write_project(actor, proposal.project):
        raise PermissionDenied('You cannot change the reviewer for this proposal.')
    reviewer = proposal_reviewer_users(proposal.project).filter(pk=reviewer_id).first()
    if not reviewer:
        raise ValidationError({'reviewer': 'Select an active RADAI user.'})
    if reviewer.pk == proposal.created_by_id:
        raise ValidationError({'reviewer': 'The proposal author cannot review their own proposal.'})
    if reviewer.pk == proposal.reviewer_id:
        raise ValidationError({'reviewer': 'Select a different reviewer.'})

    previous_reviewer = proposal.reviewer
    _cancel_pending_tasks(proposal, 'review')
    task = ProposalWorkflowTask.objects.create(
        proposal=proposal, task_type='review', assigned_to=reviewer,
        assigned_by=actor, due_date=due_date or proposal.review_due_date, comments=comments,
    )
    proposal.reviewer = reviewer
    proposal.review_due_date = due_date or proposal.review_due_date
    proposal.review_comments = comments
    proposal.save(update_fields=['reviewer', 'review_due_date', 'review_comments', 'updated_at'])
    _notify(
        previous_reviewer, sender=actor, title='Technical proposal review reassigned',
        message=f'Your review assignment for {proposal.proposal_number} Rev {proposal.revision} was reassigned.',
        proposal=proposal, priority='NORMAL',
    )
    _notify(
        reviewer, sender=actor, title='Technical proposal review required',
        message=f'{proposal.proposal_number} Rev {proposal.revision} has been assigned to you for technical review.',
        proposal=proposal,
    )
    record_event(
        project=proposal.project, actor=actor, action='proposal.reviewer_reassigned', entity=proposal,
        before={'reviewer_id': getattr(previous_reviewer, 'id', None)},
        after={'reviewer_id': reviewer.id, 'due_date': str(proposal.review_due_date or ''), 'task_id': task.id},
    )
    return proposal


@transaction.atomic
def reassign_approver(proposal_id, actor, *, approver_id, due_date=None, comments=''):
    proposal = TechnicalProposal.objects.select_for_update(of=('self',)).select_related('project').get(pk=proposal_id)
    if proposal.status != 'approval_review':
        raise ValidationError('The approver can only be changed while approval is active.')
    if not (can_write_project(actor, proposal.project) or proposal.checked_by_id == actor.id):
        raise PermissionDenied('You cannot change the approver for this proposal.')
    approver = proposal_approver_users(proposal.project).filter(pk=approver_id).first()
    if not approver:
        raise ValidationError({'approver': 'Select an authorized project approver.'})
    if approver.pk in (proposal.created_by_id, proposal.checked_by_id):
        raise ValidationError({'approver': 'The approver must be different from the author and reviewer.'})
    if approver.pk == proposal.approver_id:
        raise ValidationError({'approver': 'Select a different approver.'})

    previous_approver = proposal.approver
    _cancel_pending_tasks(proposal, 'approval')
    task = ProposalWorkflowTask.objects.create(
        proposal=proposal, task_type='approval', assigned_to=approver,
        assigned_by=actor, due_date=due_date or proposal.approval_due_date, comments=comments,
    )
    proposal.approver = approver
    proposal.approval_due_date = due_date or proposal.approval_due_date
    proposal.approval_comments = comments
    proposal.save(update_fields=['approver', 'approval_due_date', 'approval_comments', 'updated_at'])
    _notify(
        previous_approver, sender=actor, title='Technical proposal approval reassigned',
        message=f'Your approval assignment for {proposal.proposal_number} Rev {proposal.revision} was reassigned.',
        proposal=proposal, priority='NORMAL',
    )
    _notify(
        approver, sender=actor, title='Technical proposal approval required',
        message=f'{proposal.proposal_number} Rev {proposal.revision} has been assigned to you for approval.',
        proposal=proposal,
    )
    record_event(
        project=proposal.project, actor=actor, action='proposal.approver_reassigned', entity=proposal,
        before={'approver_id': getattr(previous_approver, 'id', None)},
        after={'approver_id': approver.id, 'due_date': str(proposal.approval_due_date or ''), 'task_id': task.id},
    )
    return proposal


@transaction.atomic
def reviewer_decision(proposal_id, actor, *, decision, comments='', approver_id=None, due_date=None):
    # Lock only the proposal row. created_by/reviewer/approver are nullable;
    # joining them in a SELECT ... FOR UPDATE makes PostgreSQL reject the
    # query with "cannot be applied to the nullable side of an outer join".
    proposal = TechnicalProposal.objects.select_for_update(of=('self',)).select_related('project').get(pk=proposal_id)
    if proposal.status != 'internal_review' or proposal.reviewer_id != actor.id:
        raise PermissionDenied('This proposal is not assigned to you for technical review.')
    task = proposal.workflow_tasks.filter(task_type='review', status='pending', assigned_to=actor).first()
    if not task:
        raise ValidationError('The active review task was not found.')
    if decision not in ('return', 'complete'):
        raise ValidationError({'decision': 'Use return or complete.'})
    if not comments.strip() and decision == 'return':
        raise ValidationError({'comments': 'Return comments are required.'})

    now = timezone.now()
    task.status = 'returned' if decision == 'return' else 'completed'
    task.comments = comments
    task.completed_at = now
    task.save(update_fields=['status', 'comments', 'completed_at', 'updated_at'])
    proposal.checked_by = actor
    proposal.review_completed_at = now
    proposal.review_comments = comments

    if decision == 'return':
        proposal.status = 'draft'
        proposal.save(update_fields=[
            'status', 'checked_by', 'review_completed_at', 'review_comments', 'updated_at',
        ])
        _notify(
            proposal.created_by, sender=actor, title='Technical proposal returned for revision',
            message=f'{proposal.proposal_number} Rev {proposal.revision} was returned with review comments.',
            proposal=proposal,
        )
        action = 'proposal.review_returned'
    else:
        approver = proposal_approver_users(proposal.project).filter(pk=approver_id).first()
        if not approver:
            raise ValidationError({'approver': 'Select an authorized project approver.'})
        if approver.pk in (proposal.created_by_id, actor.id):
            raise ValidationError({'approver': 'The approver must be different from the author and reviewer.'})
        approval_task = ProposalWorkflowTask.objects.create(
            proposal=proposal, task_type='approval', assigned_to=approver,
            assigned_by=actor, due_date=due_date, comments=comments,
        )
        proposal.status = 'approval_review'
        proposal.approver = approver
        proposal.approval_due_date = due_date
        proposal.approval_submitted_at = now
        proposal.approval_comments = ''
        proposal.save(update_fields=[
            'status', 'checked_by', 'review_completed_at', 'review_comments',
            'approver', 'approval_due_date', 'approval_submitted_at',
            'approval_comments', 'updated_at',
        ])
        _notify(
            approver, sender=actor, title='Technical proposal approval required',
            message=f'{proposal.proposal_number} Rev {proposal.revision} completed technical review and is awaiting your approval.',
            proposal=proposal,
        )
        action = 'proposal.review_completed'
        task = approval_task
    record_event(
        project=proposal.project, actor=actor, action=action, entity=proposal,
        after={'decision': decision, 'comments': comments, 'task_id': task.id},
    )
    return proposal


@transaction.atomic
def approver_decision(proposal_id, actor, *, decision, comments=''):
    proposal = TechnicalProposal.objects.select_for_update(of=('self',)).select_related('project').get(pk=proposal_id)
    if proposal.status != 'approval_review' or proposal.approver_id != actor.id:
        raise PermissionDenied('This proposal is not assigned to you for approval.')
    if not can_approve_proposal(actor, proposal.project):
        raise PermissionDenied('Your project role is not authorized to approve proposals.')
    if actor.id in (proposal.created_by_id, proposal.checked_by_id):
        raise PermissionDenied('The author or technical reviewer cannot approve this proposal.')
    if decision not in ('approve', 'return', 'reject'):
        raise ValidationError({'decision': 'Use approve, return, or reject.'})
    if decision in ('return', 'reject') and not comments.strip():
        raise ValidationError({'comments': 'Comments are required for return or rejection.'})

    task = proposal.workflow_tasks.filter(task_type='approval', status='pending', assigned_to=actor).first()
    if not task:
        raise ValidationError('The active approval task was not found.')
    now = timezone.now()
    task.status = {'approve': 'completed', 'return': 'returned', 'reject': 'rejected'}[decision]
    task.comments = comments
    task.completed_at = now
    task.save(update_fields=['status', 'comments', 'completed_at', 'updated_at'])
    proposal.approval_comments = comments

    if decision == 'approve':
        proposal.status = 'approved'
        proposal.approved_by = actor
        proposal.approved_at = now
        recipient = proposal.created_by
        title = 'Technical proposal approved'
        message = f'{proposal.proposal_number} Rev {proposal.revision} has been approved and is ready to issue.'
    elif decision == 'reject':
        proposal.status = 'rejected'
        proposal.rejected_at = now
        recipient = proposal.created_by
        title = 'Technical proposal rejected'
        message = f'{proposal.proposal_number} Rev {proposal.revision} was rejected. Review the approval comments.'
    else:
        proposal.status = 'internal_review'
        recipient = proposal.reviewer
        title = 'Technical proposal returned by approver'
        message = f'{proposal.proposal_number} Rev {proposal.revision} requires further technical review.'
        ProposalWorkflowTask.objects.create(
            proposal=proposal, task_type='review', assigned_to=proposal.reviewer,
            assigned_by=actor, due_date=proposal.review_due_date, comments=comments,
        )
    proposal.save(update_fields=[
        'status', 'approval_comments', 'approved_by', 'approved_at', 'rejected_at', 'updated_at',
    ])
    _notify(recipient, sender=actor, title=title, message=message, proposal=proposal)
    record_event(
        project=proposal.project, actor=actor, action=f'proposal.approval_{decision}', entity=proposal,
        after={'decision': decision, 'comments': comments, 'task_id': task.id},
    )
    return proposal


@transaction.atomic
def issue_proposal(proposal_id, actor):
    proposal = TechnicalProposal.objects.select_for_update().select_related('project').get(pk=proposal_id)
    if proposal.status != 'approved':
        raise ValidationError('Only an approved proposal can be issued.')
    if not can_approve_proposal(actor, proposal.project):
        raise PermissionDenied('Only an authorized project approver can issue this proposal.')

    proposal.export_records.filter(is_issued_artifact=True, is_deleted=False).update(
        is_deleted=True, deleted_at=timezone.now(), updated_at=timezone.now(),
    )
    issued_files = []
    for export_format in ('pdf', 'docx'):
        content, _content_type, filename = generate_proposal_export(proposal, export_format)
        record = ProposalExportRecord(
            proposal=proposal, export_format=export_format, filename=filename,
            size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
            requested_by=actor, is_issued_artifact=True,
        )
        record.file.save(filename, ContentFile(content), save=False)
        record.save()
        issued_files.append(record)

    proposal.status = 'issued'
    proposal.issued_at = timezone.now()
    proposal.save(update_fields=['status', 'issued_at', 'updated_at'])
    _notify(
        proposal.created_by, sender=actor, title='Technical proposal issued',
        message=f'{proposal.proposal_number} Rev {proposal.revision} was issued. Controlled PDF and Word files are available in the Proposal Register.',
        proposal=proposal, priority='NORMAL',
    )
    record_event(
        project=proposal.project, actor=actor, action='proposal.issued', entity=proposal,
        after={'files': [row.filename for row in issued_files]},
    )
    return proposal


@transaction.atomic
def reopen_rejected(proposal_id, actor, *, comments=''):
    proposal = TechnicalProposal.objects.select_for_update().select_related('project').get(pk=proposal_id)
    if proposal.status != 'rejected' or not can_write_project(actor, proposal.project):
        raise PermissionDenied('You cannot reopen this rejected proposal.')
    _cancel_pending_tasks(proposal)
    proposal.status = 'draft'
    proposal.approver = None
    proposal.approved_by = None
    proposal.approved_at = None
    proposal.save(update_fields=['status', 'approver', 'approved_by', 'approved_at', 'updated_at'])
    record_event(
        project=proposal.project, actor=actor, action='proposal.reopened', entity=proposal,
        after={'comments': comments},
    )
    return proposal
