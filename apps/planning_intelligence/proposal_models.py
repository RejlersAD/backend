"""Controlled enterprise technical proposals generated from planning versions."""
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

from .models import PlanningGeneration, PlanningProject, get_planning_file_storage
from .schedule_models import ScheduleVersion


def _proposal_export_upload_path(instance, filename):
    project_id = instance.proposal.project_id if instance.proposal_id else 'unassigned'
    proposal_number = instance.proposal.proposal_number if instance.proposal_id else 'proposal'
    return f'{project_id}/proposals/{proposal_number}/{uuid.uuid4().hex}_{filename}'


class TechnicalProposal(BaseModel):
    STATUS_CHOICES = [
        ('draft', 'Draft'), ('internal_review', 'Internal Review'),
        ('approval_review', 'Awaiting Approval'), ('rejected', 'Rejected'),
        ('approved', 'Approved'), ('issued', 'Issued'), ('superseded', 'Superseded'),
    ]

    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='technical_proposals')
    schedule_version = models.ForeignKey(
        ScheduleVersion, on_delete=models.PROTECT, related_name='technical_proposals',
    )
    source_generation = models.ForeignKey(
        PlanningGeneration, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='technical_proposals',
    )
    proposal_number = models.CharField(max_length=64)
    revision = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=255)
    client_name = models.CharField(max_length=255, blank=True)
    opportunity_reference = models.CharField(max_length=120, blank=True)
    client_reference = models.CharField(max_length=120, blank=True)
    tender_title = models.CharField(max_length=255, blank=True)
    submission_date = models.DateField(null=True, blank=True)
    validity_date = models.DateField(null=True, blank=True)
    validity_days = models.PositiveIntegerField(default=120)
    bid_focal_point = models.JSONField(default=dict, blank=True)
    submission_address = models.JSONField(default=dict, blank=True)
    signatory = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    sections = models.JSONField(default=list, blank=True)
    branding = models.JSONField(default=dict, blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='technical_proposals_created',
    )
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='technical_proposals_checked',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='technical_proposals_approved',
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='technical_proposals_to_review',
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='technical_proposals_to_approve',
    )
    review_due_date = models.DateField(null=True, blank=True)
    approval_due_date = models.DateField(null=True, blank=True)
    review_submitted_at = models.DateTimeField(null=True, blank=True)
    review_completed_at = models.DateTimeField(null=True, blank=True)
    approval_submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    review_comments = models.TextField(blank=True)
    approval_comments = models.TextField(blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-revision', '-created_at']
        unique_together = [('project', 'revision'), ('project', 'proposal_number')]
        indexes = [models.Index(fields=['project', '-created_at']), models.Index(fields=['status', '-created_at'])]

    def __str__(self):
        return f'{self.proposal_number} Rev {self.revision}'


class ProposalExportRecord(BaseModel):
    FORMAT_CHOICES = [('pdf', 'PDF'), ('docx', 'Microsoft Word')]

    proposal = models.ForeignKey(TechnicalProposal, on_delete=models.CASCADE, related_name='export_records')
    export_format = models.CharField(max_length=8, choices=FORMAT_CHOICES)
    filename = models.CharField(max_length=255)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    file = models.FileField(
        upload_to=_proposal_export_upload_path, storage=get_planning_file_storage,
        max_length=512, blank=True,
    )
    is_issued_artifact = models.BooleanField(default=False, db_index=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='proposal_exports_requested',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['proposal', '-created_at'])]


class ProposalWorkflowTask(BaseModel):
    TASK_CHOICES = [('review', 'Technical Review'), ('approval', 'Approval')]
    STATUS_CHOICES = [
        ('pending', 'Pending'), ('completed', 'Completed'), ('returned', 'Returned'),
        ('rejected', 'Rejected'), ('cancelled', 'Cancelled'),
    ]

    proposal = models.ForeignKey(TechnicalProposal, on_delete=models.CASCADE, related_name='workflow_tasks')
    task_type = models.CharField(max_length=16, choices=TASK_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending', db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='proposal_workflow_tasks',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='proposal_workflow_tasks_assigned',
    )
    due_date = models.DateField(null=True, blank=True)
    comments = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['assigned_to', 'status', 'due_date'], name='plan_prop_task_inbox_idx'),
            models.Index(fields=['proposal', '-created_at'], name='plan_prop_task_prop_idx'),
        ]
