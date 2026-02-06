from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
import json


class ElectricalEquipmentType(models.Model):
    """Model to store electrical equipment types with their configurations"""
    
    id = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=10, blank=True)
    category = models.CharField(max_length=100)
    standards = models.JSONField(default=list)
    sections = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'electrical_equipment_types'
        verbose_name = 'Electrical Equipment Type'
        verbose_name_plural = 'Electrical Equipment Types'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class ElectricalDatasheet(models.Model):
    """Model to store electrical equipment datasheets"""
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('revision_required', 'Revision Required'),
    ]

    # Basic Information
    equipment_type = models.ForeignKey(
        ElectricalEquipmentType,
        on_delete=models.PROTECT,
        related_name='datasheets'
    )
    tag_number = models.CharField(max_length=100, unique=True, db_index=True)
    service_description = models.TextField()
    location = models.CharField(max_length=200)
    
    # Form Data - stored as JSON for flexibility
    form_data = models.JSONField(default=dict)
    
    # Status and Workflow
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        db_index=True
    )
    revision_number = models.IntegerField(default=1)
    revision_notes = models.TextField(blank=True)
    
    # Metadata
    project_name = models.CharField(max_length=200, blank=True)
    project_number = models.CharField(max_length=100, blank=True, db_index=True)
    discipline = models.CharField(max_length=50, default='Electrical')
    
    # File Attachments
    attachments = models.JSONField(default=list, blank=True)
    
    # User Tracking
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='electrical_datasheets_created'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='electrical_datasheets_updated'
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='electrical_datasheets_reviewed'
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='electrical_datasheets_approved'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Soft Delete
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='electrical_datasheets_deleted'
    )

    class Meta:
        db_table = 'electrical_datasheets'
        verbose_name = 'Electrical Datasheet'
        verbose_name_plural = 'Electrical Datasheets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['equipment_type', 'status']),
            models.Index(fields=['project_number', 'status']),
            models.Index(fields=['created_at', 'status']),
        ]

    def __str__(self):
        return f"{self.tag_number} - {self.equipment_type.name}"

    def get_field_value(self, field_id):
        """Helper method to retrieve a specific field value from form_data"""
        return self.form_data.get(field_id)

    def set_field_value(self, field_id, value):
        """Helper method to set a specific field value in form_data"""
        self.form_data[field_id] = value

    def to_dict(self):
        """Convert datasheet to dictionary format"""
        return {
            'id': self.id,
            'equipment_type': {
                'id': self.equipment_type.id,
                'name': self.equipment_type.name,
                'code': self.equipment_type.code,
            },
            'tag_number': self.tag_number,
            'service_description': self.service_description,
            'location': self.location,
            'form_data': self.form_data,
            'status': self.status,
            'revision_number': self.revision_number,
            'project_name': self.project_name,
            'project_number': self.project_number,
            'created_by': self.created_by.get_full_name() if self.created_by else None,
            'updated_by': self.updated_by.get_full_name() if self.updated_by else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class DatasheetRevisionHistory(models.Model):
    """Model to track revision history of datasheets"""
    
    datasheet = models.ForeignKey(
        ElectricalDatasheet,
        on_delete=models.CASCADE,
        related_name='revision_history'
    )
    revision_number = models.IntegerField()
    form_data = models.JSONField()
    status = models.CharField(max_length=20)
    revision_notes = models.TextField(blank=True)
    
    # User and Timestamp
    revised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    revised_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'electrical_datasheet_revisions'
        verbose_name = 'Datasheet Revision'
        verbose_name_plural = 'Datasheet Revisions'
        ordering = ['-revision_number']
        unique_together = ['datasheet', 'revision_number']

    def __str__(self):
        return f"{self.datasheet.tag_number} - Rev {self.revision_number}"


class DatasheetComment(models.Model):
    """Model for comments on datasheets"""
    
    datasheet = models.ForeignKey(
        ElectricalDatasheet,
        on_delete=models.CASCADE,
        related_name='comments'
    )
    comment_text = models.TextField()
    field_id = models.CharField(max_length=100, blank=True)  # Optional field reference
    
    # User and Timestamp
    commented_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    commented_at = models.DateTimeField(auto_now_add=True)
    
    # Reply functionality
    parent_comment = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='replies'
    )
    
    is_resolved = models.BooleanField(default=False)

    class Meta:
        db_table = 'electrical_datasheet_comments'
        verbose_name = 'Datasheet Comment'
        verbose_name_plural = 'Datasheet Comments'
        ordering = ['commented_at']

    def __str__(self):
        return f"Comment on {self.datasheet.tag_number} by {self.commented_by}"
