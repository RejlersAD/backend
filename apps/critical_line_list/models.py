"""
Critical Line List (CLL) — per-project persistence.

CllProject: project header with a per-project storage choice
('database' | 's3') selected in the frontend Create/Edit dialog.

CllProjectData: the whole workspace payload (files / history / resolved /
legends / lastRunAt) stored as JSON when storage='database'. When
storage='s3', the payload is stored in S3 instead (see views.py).
"""
from django.conf import settings
from django.db import models


class CllProject(models.Model):
    STORAGE_DATABASE = 'database'
    STORAGE_S3 = 's3'
    STORAGE_CHOICES = [
        (STORAGE_DATABASE, 'Database'),
        (STORAGE_S3, 'AWS S3'),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='cll_projects',
    )
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=100)
    client = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    storage = models.CharField(
        max_length=20,
        choices=STORAGE_CHOICES,
        default=STORAGE_DATABASE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.name} ({self.unit})'


class CllProjectData(models.Model):
    project = models.OneToOneField(
        CllProject,
        on_delete=models.CASCADE,
        related_name='data',
    )
    payload = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Data for {self.project_id}'
