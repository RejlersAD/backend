"""
PFD to P&ID Converter Models
AI-powered conversion of Process Flow Diagrams to Piping & Instrumentation Diagrams
"""
from django.db import models
from django.contrib.auth import get_user_model
from apps.core.models import TimeStampedModel
import uuid

User = get_user_model()


class PFDDocument(TimeStampedModel):
    """
    Process Flow Diagram document uploaded for conversion
    """
    STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('processing', 'Processing'),
        ('converted', 'Converted'),
        ('failed', 'Failed'),
        ('approved', 'Approved'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pfd_documents')
    
    # Document info
    document_title = models.CharField(max_length=255, blank=True)
    document_number = models.CharField(max_length=100, blank=True)
    revision = models.CharField(max_length=50, blank=True)
    project_name = models.CharField(max_length=255, blank=True)
    project_code = models.CharField(max_length=100, blank=True)
    
    # File details
    file = models.FileField(upload_to='pfd_documents/%Y/%m/%d/')
    file_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField(help_text='File size in bytes')
    file_type = models.CharField(max_length=50)
    
    # Processing status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='uploaded')
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)
    processing_duration = models.FloatField(null=True, blank=True, help_text='Duration in seconds')
    
    # AI extraction results
    extracted_data = models.JSONField(
        default=dict,
        help_text='Extracted process information from PFD'
    )
    
    # Conversion metadata
    conversion_notes = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    
    class Meta:
        db_table = 'pfd_documents'
        verbose_name = 'PFD Document'
        verbose_name_plural = 'PFD Documents'
        ordering = ['-created_at']
        
    def __str__(self):
        return f"PFD: {self.document_number or self.file_name}"


class PIDConversion(TimeStampedModel):
    """
    Generated P&ID from PFD using AI
    """
    STATUS_CHOICES = [
        ('generating', 'Generating'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('approved', 'Approved'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pfd_document = models.ForeignKey(
        PFDDocument,
        on_delete=models.CASCADE,
        related_name='pid_conversions'
    )
    converted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pid_conversions')
    
    # P&ID Details
    pid_drawing_number = models.CharField(max_length=100)
    pid_title = models.CharField(max_length=255)
    pid_revision = models.CharField(max_length=50, default='A')
    
    # Generated files
    pid_file = models.FileField(upload_to='pid_generated/%Y/%m/%d/', null=True, blank=True)
    preview_image = models.ImageField(upload_to='pid_previews/%Y/%m/%d/', null=True, blank=True)
    
    # Generation details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='generating')
    generation_started_at = models.DateTimeField(auto_now_add=True)
    generation_completed_at = models.DateTimeField(null=True, blank=True)
    generation_duration = models.FloatField(null=True, blank=True)
    
    # AI-generated content
    equipment_list = models.JSONField(default=list, help_text='List of equipment from PFD')
    instrument_list = models.JSONField(default=list, help_text='Generated instrumentation')
    piping_details = models.JSONField(default=list, help_text='Piping specifications')
    safety_systems = models.JSONField(default=list, help_text='Safety and control systems')
    
    # Design parameters
    design_parameters = models.JSONField(
        default=dict,
        help_text='Process parameters, pressures, temperatures, etc.'
    )
    
    # Validation & Compliance
    compliance_checks = models.JSONField(
        default=dict,
        help_text='Standards compliance verification'
    )
    
    # Review & approval
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_conversions'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    
    # Quality score
    confidence_score = models.FloatField(
        null=True,
        blank=True,
        help_text='AI confidence score (0-100)'
    )
    
    class Meta:
        db_table = 'pid_conversions'
        verbose_name = 'P&ID Conversion'
        verbose_name_plural = 'P&ID Conversions'
        ordering = ['-created_at']
        
    def __str__(self):
        return f"P&ID: {self.pid_drawing_number} from {self.pfd_document.document_number}"


class ConversionFeedback(TimeStampedModel):
    """
    User feedback on AI-generated P&ID conversions
    """
    RATING_CHOICES = [
        (1, 'Poor'),
        (2, 'Fair'),
        (3, 'Good'),
        (4, 'Very Good'),
        (5, 'Excellent'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversion = models.ForeignKey(
        PIDConversion,
        on_delete=models.CASCADE,
        related_name='feedback'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    
    # Feedback details
    rating = models.IntegerField(choices=RATING_CHOICES)
    accuracy_rating = models.IntegerField(choices=RATING_CHOICES, null=True, blank=True)
    completeness_rating = models.IntegerField(choices=RATING_CHOICES, null=True, blank=True)
    usability_rating = models.IntegerField(choices=RATING_CHOICES, null=True, blank=True)
    
    comments = models.TextField(blank=True)
    suggested_improvements = models.TextField(blank=True)
    
    # Issues found
    issues_found = models.JSONField(
        default=list,
        help_text='List of issues or errors found'
    )
    
    class Meta:
        db_table = 'pfd_conversion_feedback'
        verbose_name = 'Conversion Feedback'
        verbose_name_plural = 'Conversion Feedback'
        ordering = ['-created_at']
        unique_together = ['conversion', 'user']
        
    def __str__(self):
        return f"Feedback by {self.user.username} - {self.rating}/5"
