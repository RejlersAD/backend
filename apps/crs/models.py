from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class CRSDocument(models.Model):
    """Model for CRS Document that contains comments extracted from PDFs"""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_review', 'In Review'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('archived', 'Archived'),
    ]
    
    document_name = models.CharField(max_length=500)
    document_number = models.CharField(max_length=100, blank=True, null=True)
    pdf_file = models.FileField(upload_to='crs/pdfs/', null=True, blank=True)
    google_sheet_id = models.CharField(max_length=200, blank=True, null=True)
    google_sheet_url = models.URLField(blank=True, null=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    total_comments = models.IntegerField(default=0)
    resolved_comments = models.IntegerField(default=0)
    pending_comments = models.IntegerField(default=0)
    
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='crs_documents_uploaded')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='crs_documents_assigned')
    
    project_name = models.CharField(max_length=300, blank=True, null=True)
    contractor_name = models.CharField(max_length=300, blank=True, null=True)
    revision_number = models.CharField(max_length=50, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'crs_documents'
        ordering = ['-created_at']
        verbose_name = 'CRS Document'
        verbose_name_plural = 'CRS Documents'
    
    def __str__(self):
        return f"{self.document_name} - {self.get_status_display()}"
    
    @property
    def completion_percentage(self):
        """Calculate completion percentage based on resolved comments"""
        if self.total_comments == 0:
            return 0
        return round((self.resolved_comments / self.total_comments) * 100, 2)
    
    @property
    def is_completed(self):
        """Check if all comments are resolved"""
        return self.total_comments > 0 and self.resolved_comments == self.total_comments


class CRSComment(models.Model):
    """Model for individual comments extracted from CRS document"""
    
    COMMENT_TYPE_CHOICES = [
        ('red_comment', 'Red Comment'),
        ('yellow_box', 'Yellow Box'),
        ('annotation', 'Annotation'),
        ('shape', 'Shape'),
        ('other', 'Other'),
    ]
    
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
        ('rejected', 'Rejected'),
    ]
    
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]
    
    document = models.ForeignKey(CRSDocument, on_delete=models.CASCADE, related_name='comments')
    
    serial_number = models.IntegerField()
    page_number = models.IntegerField()
    clause_number = models.CharField(max_length=100, blank=True, null=True)
    comment_text = models.TextField()
    
    comment_type = models.CharField(max_length=20, choices=COMMENT_TYPE_CHOICES, default='other')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    
    color_rgb = models.JSONField(null=True, blank=True, help_text='RGB color values')
    bbox = models.JSONField(null=True, blank=True, help_text='Bounding box coordinates')
    
    contractor_response = models.TextField(blank=True, null=True)
    contractor_response_date = models.DateTimeField(null=True, blank=True)
    contractor_responder = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='contractor_responses')
    
    company_response = models.TextField(blank=True, null=True)
    company_response_date = models.DateTimeField(null=True, blank=True)
    company_responder = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='company_responses')
    
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_crs_comments')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    notes = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'crs_comments'
        ordering = ['document', 'serial_number']
        verbose_name = 'CRS Comment'
        verbose_name_plural = 'CRS Comments'
        unique_together = [['document', 'serial_number']]
    
    def __str__(self):
        return f"Comment #{self.serial_number} - Page {self.page_number} - {self.document.document_name}"
    
    @property
    def has_contractor_response(self):
        """Check if contractor has responded"""
        return bool(self.contractor_response and self.contractor_response.strip())
    
    @property
    def has_company_response(self):
        """Check if company has responded"""
        return bool(self.company_response and self.company_response.strip())
    
    @property
    def is_fully_responded(self):
        """Check if both contractor and company have responded"""
        return self.has_contractor_response and self.has_company_response


class CRSActivity(models.Model):
    """Model to track all activities/changes on CRS documents and comments"""
    
    ACTION_CHOICES = [
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('status_changed', 'Status Changed'),
        ('comment_added', 'Comment Added'),
        ('response_added', 'Response Added'),
        ('assigned', 'Assigned'),
        ('exported', 'Exported to Google Sheets'),
        ('imported', 'Imported from PDF'),
    ]
    
    document = models.ForeignKey(CRSDocument, on_delete=models.CASCADE, related_name='activities')
    comment = models.ForeignKey(CRSComment, on_delete=models.CASCADE, null=True, blank=True, related_name='activities')
    
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    description = models.TextField()
    
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    performed_at = models.DateTimeField(auto_now_add=True)
    
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    
    class Meta:
        db_table = 'crs_activities'
        ordering = ['-performed_at']
        verbose_name = 'CRS Activity'
        verbose_name_plural = 'CRS Activities'
    
    def __str__(self):
        return f"{self.get_action_display()} - {self.document.document_name} - {self.performed_at.strftime('%Y-%m-%d %H:%M')}"


class GoogleSheetConfig(models.Model):
    """Model to store Google Sheets API configuration"""
    
    name = models.CharField(max_length=200)
    client_id = models.CharField(max_length=500, blank=True, null=True)
    client_secret = models.CharField(max_length=500, blank=True, null=True)
    credentials_json = models.TextField(blank=True, null=True, help_text='Full credentials JSON')
    token_json = models.TextField(blank=True, null=True, help_text='OAuth token JSON')
    
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'crs_google_sheet_configs'
        ordering = ['-created_at']
        verbose_name = 'Google Sheet Configuration'
        verbose_name_plural = 'Google Sheet Configurations'
    
    def __str__(self):
        return f"{self.name} - {'Active' if self.is_active else 'Inactive'}"
