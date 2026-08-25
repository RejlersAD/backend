"""Persistent document-intelligence evidence, provenance, and review workflow."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

from .models import PlanningFile, PlanningProject


class DocumentProfile(BaseModel):
    """One durable extraction/classification profile per uploaded document."""

    file = models.OneToOneField(PlanningFile, on_delete=models.CASCADE, related_name='document_profile')
    declared_category = models.CharField(max_length=40, blank=True)
    detected_category = models.CharField(max_length=40, blank=True)
    classification_confidence = models.FloatField(default=0)
    extension = models.CharField(max_length=16, blank=True)
    mime_type = models.CharField(max_length=128, blank=True)
    language = models.CharField(max_length=16, default='en')
    page_count = models.PositiveIntegerField(default=0)
    word_count = models.PositiveIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    extraction_method = models.CharField(max_length=64, blank=True)
    quality_flags = models.JSONField(default=list, blank=True)
    classified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']


class DocumentIntelligenceRun(BaseModel):
    STATUS_CHOICES = [
        ('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed'),
    ]

    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='intelligence_runs')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='running')
    engine_version = models.CharField(max_length=32, default='2.0')
    source_file_ids = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    fact_count = models.PositiveIntegerField(default=0)
    conflict_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='document_intelligence_runs_requested',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['project', '-created_at'])]


class IntelligenceFact(BaseModel):
    TYPE_CHOICES = [
        ('project_name', 'Project Name'), ('effective_date', 'Effective Date'),
        ('duration_months', 'Duration Months'), ('client', 'Client'),
        ('location', 'Location'), ('discipline', 'Discipline'),
        ('deliverable', 'Deliverable'), ('hse_study', 'HSE Study'),
        ('milestone', 'Milestone'), ('calendar', 'Calendar'),
        ('review_cycle', 'Review Cycle'), ('requirement', 'Requirement'),
        ('exclusion', 'Exclusion'),
    ]
    METHOD_CHOICES = [('deterministic', 'Deterministic'), ('ai', 'AI'), ('manual', 'Manual')]
    STATUS_CHOICES = [
        ('detected', 'Detected'), ('confirmed', 'Confirmed'), ('rejected', 'Rejected'),
        ('conflicted', 'Conflicted'), ('superseded', 'Superseded'),
    ]

    run = models.ForeignKey(DocumentIntelligenceRun, on_delete=models.CASCADE, related_name='facts')
    source_file = models.ForeignKey(
        PlanningFile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='intelligence_facts',
    )
    fact_type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    key = models.CharField(max_length=160, db_index=True)
    value = models.JSONField()
    normalized_value = models.CharField(max_length=500, blank=True)
    confidence = models.FloatField(default=0)
    extraction_method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='deterministic')
    source_excerpt = models.CharField(max_length=1000, blank=True)
    source_locator = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='detected', db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='intelligence_facts_reviewed',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['fact_type', '-confidence', 'id']
        indexes = [models.Index(fields=['run', 'fact_type', 'status'])]


class IntelligenceConflict(BaseModel):
    STATUS_CHOICES = [('open', 'Open'), ('resolved', 'Resolved'), ('ignored', 'Ignored')]

    run = models.ForeignKey(DocumentIntelligenceRun, on_delete=models.CASCADE, related_name='conflicts')
    key = models.CharField(max_length=160, db_index=True)
    conflict_type = models.CharField(max_length=40, default='value_mismatch')
    fact_ids = models.JSONField(default=list, blank=True)
    description = models.CharField(max_length=500)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='open', db_index=True)
    resolution = models.JSONField(default=dict, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='intelligence_conflicts_resolved',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['status', 'key']
        indexes = [models.Index(fields=['run', 'status'])]
