"""
Project Organizer — Shared, cross-tool project registry
==========================================================

Generic, RBAC-aware project organiser any engineering tool can plug into
without inventing its own project CRUD. Mirrors the proven schema used by
``apps.spec_customization.SpecProject`` / ``apps.non_teff_metadata.NonTeffProject``.

``Project`` is a real, cross-tool entity — a user picks/creates ONE project
and any adopting tool (HMB Extractor first, more later) can log work against
it via ``ProjectActivity`` without needing its own child model.

Status lifecycle (soft-coded — adjust without code changes):
    active → on_hold → completed → archived
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Project(models.Model):
    STATUS_ACTIVE    = 'active'
    STATUS_ON_HOLD   = 'on_hold'
    STATUS_COMPLETED = 'completed'
    STATUS_ARCHIVED  = 'archived'

    STATUS_CHOICES = [
        (STATUS_ACTIVE,    'Active'),
        (STATUS_ON_HOLD,   'On hold'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_ARCHIVED,  'Archived'),
    ]

    project_id   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name         = models.CharField(max_length=255)
    code         = models.CharField(max_length=64, blank=True, db_index=True)
    client       = models.CharField(max_length=128, blank=True)
    plant        = models.CharField(max_length=128, blank=True)
    discipline   = models.CharField(max_length=64, blank=True)
    description  = models.TextField(blank=True)
    status       = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    tags         = models.JSONField(default=list, blank=True)
    metadata     = models.JSONField(default=dict, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='organizer_projects',
    )

    class Meta:
        ordering = ['-updated_at', '-created_at']
        verbose_name = 'Project'
        verbose_name_plural = 'Projects'
        indexes = [
            models.Index(fields=['status', '-updated_at']),
            models.Index(fields=['created_by', '-updated_at']),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.status}] ({self.project_id})"


class ProjectActivity(models.Model):
    """Append-only cross-tool activity log — any adopting tool records a row
    here after a successful run, so a project's history spans every tool
    that touched it (no per-tool child model needed)."""

    project    = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='activity')
    tool_code  = models.CharField(max_length=64, db_index=True)   # e.g. 'hmb_extractor'
    summary    = models.CharField(max_length=255)
    metadata   = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='organizer_project_activity',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Project Activity'
        verbose_name_plural = 'Project Activity'
        indexes = [
            models.Index(fields=['project', '-created_at']),
            models.Index(fields=['tool_code', '-created_at']),
        ]

    def __str__(self) -> str:
        return f"[{self.tool_code}] {self.summary} ({self.project_id})"
