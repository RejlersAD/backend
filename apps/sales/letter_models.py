"""Sales Letter Templates and Generated Letters."""

from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.core.models import TimeStampedModel
import uuid
import os

User = get_user_model()


def letter_pdf_upload_path(instance, filename):
    """Generate upload path for letter PDFs."""
    return f'sales/letters/{instance.opportunity.deal_code}/{filename}'


class SalesLetterTemplate(TimeStampedModel):
    """Standard letter templates for post-bid-decision correspondence."""

    LETTER_TYPES = [
        ('eoi', 'Expression of Interest'),
        ('regret_expertise', 'Regret - Area of Expertise'),
        ('regret_manpower', 'Regret - Manpower Availability'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    letter_type = models.CharField(max_length=20, choices=LETTER_TYPES, unique=True)
    subject_template = models.TextField()
    body_template = models.TextField()
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = 'sales_letter_templates'
        ordering = ['letter_type']

    def __str__(self):
        return f"{self.get_letter_type_display()} v{self.version}"


class SalesLetter(TimeStampedModel):
    """Generated letter instance for a specific opportunity."""

    LETTER_TYPES = SalesLetterTemplate.LETTER_TYPES
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('archived', 'Archived'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opportunity = models.ForeignKey(
        'Deal', on_delete=models.PROTECT, related_name='letters'
    )
    letter_type = models.CharField(max_length=20, choices=LETTER_TYPES)
    template = models.ForeignKey(
        SalesLetterTemplate, on_delete=models.PROTECT, related_name='letters'
    )
    subject = models.CharField(max_length=300)
    body = models.TextField()
    generated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sales_letters_generated'
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    sent_to = models.EmailField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    custom_data = models.JSONField(default=dict, blank=True)
    # PDF fields
    pdf_file = models.FileField(upload_to=letter_pdf_upload_path, null=True, blank=True)
    pdf_generated_at = models.DateTimeField(null=True, blank=True)
    # DOCX fields
    docx_file = models.FileField(upload_to=letter_pdf_upload_path, null=True, blank=True)
    docx_generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'sales_letters'
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['opportunity', 'letter_type']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.opportunity.deal_code} - {self.get_letter_type_display()}"

    def mark_sent(self, recipient_email):
        self.status = 'sent'
        self.sent_to = recipient_email
        self.sent_at = timezone.now()
        self.save(update_fields=['status', 'sent_to', 'sent_at', 'updated_at'])

    def get_pdf_url(self):
        """Get the PDF file URL."""
        if self.pdf_file:
            return self.pdf_file.url
        return None