"""I-06: append-only validation, release snapshots and engineering audit."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class EvidenceQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Engineering evidence is append-only.')

    def delete(self):
        raise ValidationError('Engineering evidence is append-only.')


class EvidenceRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    objects = EvidenceQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ['-created_at', '-id']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Engineering evidence is append-only.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Engineering evidence is append-only.')


class ValidationRun(EvidenceRecord):
    job = models.ForeignKey('spec_customization.PaperSpecExtractionJob', on_delete=models.PROTECT, related_name='validation_runs')
    input_sha256 = models.CharField(max_length=64)
    preview_sha256 = models.CharField(max_length=64)
    results = models.JSONField(default=dict)
    severity_counts = models.JSONField(default=dict)
    error_count = models.PositiveIntegerField(default=0)
    trigger_source = models.CharField(max_length=64, default='manual')


class SpecRelease(EvidenceRecord):
    job = models.ForeignKey('spec_customization.PaperSpecExtractionJob', on_delete=models.PROTECT, related_name='spec_releases')
    state = models.CharField(max_length=16, choices=[('draft', 'Draft'), ('frozen', 'Frozen'), ('invalidated', 'Invalidated')])
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='successors')
    validation = models.ForeignKey(ValidationRun, null=True, blank=True, on_delete=models.PROTECT, related_name='releases')
    manifest = models.JSONField(default=dict)
    blockers = models.JSONField(default=list)
    spec_bytes = models.BinaryField(default=bytes)
    cat_bytes = models.BinaryField(default=bytes)

    class Meta(EvidenceRecord.Meta):
        constraints = [models.UniqueConstraint(fields=['parent', 'state'], name='s3d_release_parent_state_unique')]


class SpecAuditEvent(EvidenceRecord):
    # Retain identity if an unreleased job is deleted.
    job_identity = models.UUIDField(db_index=True)
    event = models.CharField(max_length=64)
    details = models.JSONField(default=dict)


class ImportAttempt(EvidenceRecord):
    release = models.ForeignKey(SpecRelease, on_delete=models.PROTECT, related_name='import_attempts')
    sp3d_version = models.CharField(max_length=128)
    import_profile = models.CharField(max_length=256)
    disposable_target = models.CharField(max_length=256)
    status = models.CharField(max_length=16, choices=[('planned', 'Planned'), ('succeeded', 'Succeeded'), ('failed', 'Failed'), ('reconciled', 'Reconciled')])
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='followups')
    rejection_count = models.PositiveIntegerField(default=0)
    result_details = models.JSONField(default=dict)
    measurements = models.JSONField(default=dict)
    report_name = models.CharField(max_length=256, blank=True)
    report_sha256 = models.CharField(max_length=64, blank=True)
    report_bytes = models.BinaryField(default=bytes)
