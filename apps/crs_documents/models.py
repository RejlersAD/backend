"""
CRS Documents Models
"""

from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class CRSDocument(models.Model):
    """Model for CRS Documents"""
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('review', 'Under Review'),
        ('approved', 'Approved'),
        ('archived', 'Archived'),
    ]
    
    DEPARTMENT_CHOICES = [
        ('engineering', 'Engineering'),
        ('operations', 'Operations'),
        ('safety', 'Safety'),
        ('quality', 'Quality Assurance'),
        ('procurement', 'Procurement'),
        ('maintenance', 'Maintenance'),
    ]
    
    # Basic Info
    document_number = models.CharField(max_length=100, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    version = models.CharField(max_length=20, default='1.0')
    
    # Classification
    department = models.CharField(max_length=50, choices=DEPARTMENT_CHOICES)
    category = models.CharField(max_length=100)
    tags = models.JSONField(default=list, blank=True)
    
    # Status & Workflow
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    
    # File
    file = models.FileField(upload_to='crs_documents/')
    file_size = models.IntegerField(default=0)  # in bytes
    file_type = models.CharField(max_length=50, blank=True)
    
    # Metadata
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='uploaded_crs_docs')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_crs_docs')
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Compliance
    compliance_standards = models.JSONField(default=list, blank=True)
    review_notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'CRS Document'
        verbose_name_plural = 'CRS Documents'
        
    def __str__(self):
        return f"{self.document_number} - {self.title}"


class CRSDocumentVersion(models.Model):
    """Track document versions"""
    
    document = models.ForeignKey(CRSDocument, on_delete=models.CASCADE, related_name='versions')
    version_number = models.CharField(max_length=20)
    file = models.FileField(upload_to='crs_documents/versions/')
    change_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        
    def __str__(self):
        return f"{self.document.document_number} v{self.version_number}"
