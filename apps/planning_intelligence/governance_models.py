"""Governance, collaboration, and formal review records for schedule versions."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

from .schedule_models import ScheduleActivity, ScheduleVersion


class GovernanceItem(BaseModel):
    TYPE_CHOICES = [
        ('change_request', 'Change Request'), ('decision', 'Decision'),
        ('action', 'Action'), ('risk', 'Risk'), ('issue', 'Issue'),
    ]
    STATUS_CHOICES = [
        ('open', 'Open'), ('in_review', 'In Review'), ('approved', 'Approved'),
        ('rejected', 'Rejected'), ('implemented', 'Implemented'), ('closed', 'Closed'),
    ]
    PRIORITY_CHOICES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')]

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='governance_items')
    activity = models.ForeignKey(
        ScheduleActivity, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='governance_items',
    )
    item_type = models.CharField(max_length=24, choices=TYPE_CHOICES, default='action')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open', db_index=True)
    priority = models.CharField(max_length=12, choices=PRIORITY_CHOICES, default='medium', db_index=True)
    due_date = models.DateField(null=True, blank=True)
    schedule_impact_days = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cost_impact = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_governance_items_owned',
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_governance_items_raised',
    )
    resolution = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['status', '-priority', 'due_date', '-created_at']
        indexes = [
            models.Index(fields=['version', 'status']),
            models.Index(fields=['owner', 'status']),
        ]


class ScheduleReview(BaseModel):
    STATUS_CHOICES = [
        ('pending', 'Pending'), ('approved', 'Approved'), ('changes_requested', 'Changes Requested'),
        ('rejected', 'Rejected'), ('cancelled', 'Cancelled'),
    ]

    version = models.ForeignKey(ScheduleVersion, on_delete=models.CASCADE, related_name='governance_reviews')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='pending', db_index=True)
    due_date = models.DateField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='schedule_reviews_requested',
    )
    requested_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['version', 'status'])]


class ScheduleReviewDecision(BaseModel):
    STATUS_CHOICES = [
        ('pending', 'Pending'), ('approved', 'Approved'),
        ('changes_requested', 'Changes Requested'), ('rejected', 'Rejected'),
    ]

    review = models.ForeignKey(ScheduleReview, on_delete=models.CASCADE, related_name='decisions')
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='schedule_review_decisions',
    )
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='pending', db_index=True)
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']
        unique_together = [('review', 'reviewer')]


class GovernanceComment(BaseModel):
    item = models.ForeignKey(
        GovernanceItem, on_delete=models.CASCADE, null=True, blank=True, related_name='comments',
    )
    review = models.ForeignKey(
        ScheduleReview, on_delete=models.CASCADE, null=True, blank=True, related_name='comments',
    )
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies',
    )
    body = models.TextField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_governance_comments',
    )
    mentioned_user_ids = models.JSONField(default=list, blank=True)
    is_resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_governance_comments_resolved',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        indexes = [models.Index(fields=['item', 'is_resolved'])]
