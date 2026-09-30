"""Platform provider credentials; secrets never belong in public projections."""
import uuid

from django.conf import settings
from django.db import models


PROVIDER_CHOICES = [('openai', 'OpenAI'), ('anthropic', 'Anthropic'), ('gemini', 'Google Gemini')]


class AIProviderCredential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, db_index=True)
    label = models.CharField(max_length=120)
    encrypted_key = models.TextField(editable=False)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name='+', editable=False)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name='+', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_tested_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_test_status = models.CharField(max_length=20, blank=True, editable=False)
    last_test_reason = models.CharField(max_length=50, blank=True, editable=False)
    last_test_model = models.CharField(max_length=200, blank=True, editable=False)

    class Meta:
        ordering = ['provider', 'created_at', 'id']

    def __str__(self):
        return f'{self.provider}: {self.label}'


class AIProviderConfiguration(models.Model):
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, primary_key=True)
    enabled = models.BooleanField(default=True)
    selected_credential = models.ForeignKey(AIProviderCredential, null=True, blank=True,
                                           on_delete=models.SET_NULL, related_name='+')
    model = models.CharField(max_length=200, blank=True)
    revision = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name='+', editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.provider
