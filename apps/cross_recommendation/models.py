"""Cross-feature recommendation persistence models."""

import uuid

from django.conf import settings
from django.db import models


class CrossRecommendationLink(models.Model):
    class DocType(models.TextChoices):
        PID = 'pid', 'P&ID'
        PFD = 'pfd', 'PFD'

    class Decision(models.TextChoices):
        SUGGESTED = 'suggested', 'Suggested'
        ACCEPTED = 'accepted', 'Accepted'
        REJECTED = 'rejected', 'Rejected'
        AUTO = 'auto', 'Auto Matched'

    link_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    source_type = models.CharField(max_length=10, choices=DocType.choices)
    source_document_id = models.UUIDField(db_index=True)
    target_type = models.CharField(max_length=10, choices=DocType.choices)
    target_document_id = models.UUIDField(db_index=True)

    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    score = models.FloatField(default=0.0)
    reason = models.CharField(max_length=255, blank=True)
    decision = models.CharField(max_length=20, choices=Decision.choices, default=Decision.SUGGESTED)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cross_recommendation_links',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cross_recommendation_links'
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['source_type', 'source_document_id', 'target_type', 'target_document_id'],
                name='uq_cross_recommendation_link_pair',
            )
        ]
        indexes = [
            models.Index(fields=['source_type', 'source_document_id']),
            models.Index(fields=['target_type', 'target_document_id']),
            models.Index(fields=['project_id', '-updated_at']),
            models.Index(fields=['decision']),
        ]

    def __str__(self):
        return (
            f'{self.source_type}:{self.source_document_id} -> '
            f'{self.target_type}:{self.target_document_id} ({self.decision})'
        )
