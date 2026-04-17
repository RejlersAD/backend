import uuid
from django.db import models
from django.conf import settings


class NonTeffExtractionJob(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_FAILED, 'Failed'),
    ]

    job_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    file_name = models.CharField(max_length=512, blank=True)
    file_format = models.CharField(max_length=20, blank=True)  # pdf / excel / word / autocad / other
    progress = models.IntegerField(default=0)
    status_message = models.CharField(max_length=512, default='Queued')
    result_json = models.JSONField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='non_teff_jobs',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Non-TEFF Extraction Job'
        verbose_name_plural = 'Non-TEFF Extraction Jobs'

    def __str__(self):
        return f"{self.file_name} [{self.status}] ({self.job_id})"
