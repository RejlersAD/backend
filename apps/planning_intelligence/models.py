"""
RADAI Project Planning Application — Models.

Deliberately lean: PlanningProject + PlanningFile are relational; the heavy
generated artefacts (WBS / activities / EDDR / validation / narrative) are
stored as JSON on PlanningGeneration so the schema can evolve without
migrations, matching the pattern used by apps.process_datasheet.

File storage: PlanningFile.file uses a dedicated
apps.core.storage_backends.PlanningIntelligenceStorage (S3 in
production/when USE_S3=True, local filesystem fallback in dev) so every
uploaded reference document is durably stored in the project's AWS S3
bucket under `media/planning_intelligence/...`, isolated from other
features' storage folders.
"""
import os
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

from .config import FILE_CATEGORIES, PARSE_STATUS_CHOICES


def _planning_file_upload_path(instance, filename):
    project_ref = str(instance.project_id or 'unassigned')
    return f'{project_ref}/{instance.category}/{uuid.uuid4().hex}_{filename}'


def get_planning_file_storage():
    """Return PlanningIntelligenceStorage (S3) when USE_S3 is enabled, otherwise
    local FileSystemStorage. Must be a *callable* (not a plain string) — Django's
    FileField only lazily resolves the `storage=` kwarg when it is callable.
    """
    if os.environ.get('USE_S3', 'False').lower() == 'true':
        try:
            from apps.core.storage_backends import PlanningIntelligenceStorage
            return PlanningIntelligenceStorage()
        except Exception:
            pass
    from django.core.files.storage import FileSystemStorage
    return FileSystemStorage(location=str(getattr(settings, 'MEDIA_ROOT', 'media')) + '/planning_intelligence')


class PlanningProject(BaseModel):
    """A single planning workspace — one FEED/DEFINE project being planned."""
    name = models.CharField(max_length=255, default='Untitled Planning Project')
    client = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    phase = models.CharField(max_length=100, blank=True, help_text='e.g. FEED / DEFINE')

    effective_date = models.DateField(null=True, blank=True)
    duration_months = models.PositiveIntegerField(default=10)

    # Calendar & review-cycle overrides — falls back to config.DEFAULT_* when empty.
    calendar_overrides = models.JSONField(default=dict, blank=True)
    review_cycle_overrides = models.JSONField(default=dict, blank=True)

    # BYOK (Bring Your Own Key) — optional per-project Claude/Anthropic
    # configuration: {'enabled': bool, 'provider': 'anthropic', 'model': str,
    # 'api_key_encrypted': str, 'key_updated_at': iso str}. The encrypted key
    # is written/read only via services/byok_crypto.py and is never exposed
    # through the API (see serializers.PlanningProjectSerializer). Empty dict
    # (the default) means "no BYOK configured — deterministic engine only".
    ai_settings = models.JSONField(default=dict, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_projects_created',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class PlanningFile(BaseModel):
    """An uploaded reference / project document (SOW, WBS, MDR, EDDR, etc.)."""
    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='files')
    category = models.CharField(max_length=40, choices=FILE_CATEGORIES, default='other')
    file = models.FileField(
        upload_to=_planning_file_upload_path,
        storage=get_planning_file_storage,
        max_length=512,
    )
    original_filename = models.CharField(max_length=512, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)

    parse_status = models.CharField(max_length=12, choices=PARSE_STATUS_CHOICES, default='pending')
    extracted_text = models.TextField(blank=True)
    confidence_score = models.FloatField(default=0.0)
    parse_error = models.TextField(blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_files_uploaded',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['project', 'category'])]

    def __str__(self):
        return f'{self.project_id} · {self.category} · {self.original_filename}'


class PlanningGeneration(BaseModel):
    """
    One generation run's output — project intelligence + WBS + activities +
    EDDR + validation + narrative + manhours, all versioned per project.
    """
    project = models.ForeignKey(PlanningProject, on_delete=models.CASCADE, related_name='generations')
    version = models.PositiveIntegerField(default=1)

    intelligence = models.JSONField(default=dict, blank=True, help_text='Extracted project intelligence')
    wbs = models.JSONField(default=list, blank=True)
    activities = models.JSONField(default=list, blank=True)
    logic_matrix = models.JSONField(default=list, blank=True)
    eddr = models.JSONField(default=list, blank=True)
    milestones = models.JSONField(default=list, blank=True)
    manhours = models.JSONField(default=dict, blank=True)
    validation = models.JSONField(default=list, blank=True)
    narrative = models.TextField(blank=True)

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planning_generations_created',
    )

    class Meta:
        ordering = ['-created_at']
        unique_together = [('project', 'version')]

    def __str__(self):
        return f'{self.project_id} · v{self.version}'
