"""
Excel Document Models for Quality Checking
Models to store uploaded Excel technical datasheets and validation issues
Designed for Borouge EU3 H2 Extraction Unit electrical datasheets
"""

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
import json


class UploadedExcelDocument(models.Model):
    """
    Model to store uploaded Excel technical datasheets
    Supports multiple equipment types: UPS, VFD, Cables, NER, etc.
    """
    
    STATUS_CHOICES = [
        ('pending', 'Pending Validation'),
        ('processing', 'Processing'),
        ('validated', 'Validated'),
        ('failed', 'Validation Failed'),
        ('error', 'Processing Error'),
    ]
    
    EQUIPMENT_TYPE_CHOICES = [
        ('ups', 'LV Variable Frequency Drive / Static AC UPS System'),
        ('vfd', 'LV Variable Frequency Drive'),
        ('power_cable', 'LV Power Cable'),
        ('control_cable', 'LV Control Cable'),
        ('earthing_cable', 'LV Earthing Cable'),
        ('ner', 'Neutral Earthing Resistor'),
        ('transformer', 'Transformer'),
        ('motor', 'Motor'),
        ('switchgear', 'Switchgear'),
        ('mcc', 'Motor Control Center'),
        ('panel', 'Distribution Panel'),
        ('unknown', 'Unknown Equipment Type'),
    ]
    
    # File Information
    filename = models.CharField(max_length=500)
    file_path = models.CharField(max_length=1000, help_text='Path to stored file (local or S3)')
    file_size = models.BigIntegerField(help_text='File size in bytes')
    file_hash = models.CharField(max_length=64, unique=True, db_index=True, 
                                 help_text='SHA-256 hash for duplicate detection')
    
    # Equipment Identification
    equipment_type = models.CharField(
        max_length=50,
        choices=EQUIPMENT_TYPE_CHOICES,
        default='unknown',
        db_index=True
    )
    
    # Document Control Information (extracted from Excel)
    company_doc_number = models.CharField(max_length=200, blank=True, db_index=True)
    contractor_doc_number = models.CharField(max_length=200, blank=True)
    rejlers_doc_number = models.CharField(max_length=200, blank=True)
    document_title = models.TextField(blank=True)
    classification_code = models.CharField(max_length=100, blank=True)
    revision = models.CharField(max_length=50, blank=True)
    doc_status = models.CharField(max_length=100, blank=True)
    doc_purpose = models.CharField(max_length=200, blank=True)
    project_name = models.CharField(max_length=300, blank=True)
    project_location = models.CharField(max_length=300, blank=True)
    agreement_number = models.CharField(max_length=200, blank=True)
    
    # Parsed Data (stored as JSON)
    parsed_data = models.JSONField(
        default=dict,
        blank=True,
        help_text='Structured JSON data extracted from Excel sheets'
    )
    
    # Metadata
    sheet_names = models.JSONField(
        default=list,
        blank=True,
        help_text='List of sheet names in the Excel file'
    )
    
    # Validation Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True
    )
    validation_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='Overall validation score (0-100)'
    )
    error_count = models.IntegerField(default=0)
    warning_count = models.IntegerField(default=0)
    info_count = models.IntegerField(default=0)
    
    # Processing Details
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)
    
    # User Tracking
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_excel_documents'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    # Soft Delete
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'electrical_excel_documents'
        verbose_name = 'Excel Document'
        verbose_name_plural = 'Excel Documents'
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['equipment_type', 'status']),
            models.Index(fields=['uploaded_at', 'status']),
            models.Index(fields=['company_doc_number', 'revision']),
        ]

    def __str__(self):
        return f"{self.filename} - {self.get_equipment_type_display()}"
    
    def get_summary(self) -> dict:
        """Get summary information for the document"""
        return {
            'id': self.id,
            'filename': self.filename,
            'equipment_type': self.get_equipment_type_display(),
            'company_doc_number': self.company_doc_number,
            'revision': self.revision,
            'status': self.get_status_display(),
            'validation_score': float(self.validation_score) if self.validation_score else None,
            'error_count': self.error_count,
            'warning_count': self.warning_count,
            'info_count': self.info_count,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'uploaded_by': self.uploaded_by.get_full_name() if self.uploaded_by else None,
        }


class ValidationIssue(models.Model):
    """
    Model to store validation issues found during quality checking
    Each issue represents a specific problem found in a document
    """
    
    SEVERITY_CHOICES = [
        ('error', 'Error'),
        ('warning', 'Warning'),
        ('info', 'Info'),
    ]
    
    # Link to document
    document = models.ForeignKey(
        UploadedExcelDocument,
        on_delete=models.CASCADE,
        related_name='validation_issues'
    )
    
    # Issue Location
    sheet_name = models.CharField(max_length=200, blank=True, db_index=True)
    section = models.CharField(max_length=300, blank=True, db_index=True)
    item = models.CharField(max_length=500, blank=True)
    row_number = models.IntegerField(null=True, blank=True)
    column_name = models.CharField(max_length=100, blank=True)
    
    # Issue Details
    severity = models.CharField(
        max_length=10,
        choices=SEVERITY_CHOICES,
        db_index=True
    )
    code = models.CharField(
        max_length=50,
        db_index=True,
        help_text='Machine-readable issue code (e.g., DOC_CTRL_001, TECH_VAL_042)'
    )
    message = models.TextField(help_text='Human-readable issue description')
    
    # Expected vs Actual
    expected_value = models.TextField(blank=True)
    actual_value = models.TextField(blank=True)
    
    # Additional Context
    rule_name = models.CharField(max_length=200, blank=True)
    category = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text='Category: document_control, technical_content, consistency, standards'
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    # Resolution tracking
    is_acknowledged = models.BooleanField(default=False)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acknowledged_issues'
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)

    class Meta:
        db_table = 'electrical_validation_issues'
        verbose_name = 'Validation Issue'
        verbose_name_plural = 'Validation Issues'
        ordering = ['severity', '-created_at']
        indexes = [
            models.Index(fields=['document', 'severity']),
            models.Index(fields=['category', 'severity']),
            models.Index(fields=['code']),
        ]

    def __str__(self):
        return f"[{self.severity.upper()}] {self.code} - {self.sheet_name}"
    
    def to_dict(self) -> dict:
        """Convert issue to dictionary format"""
        return {
            'id': self.id,
            'document_id': self.document_id,
            'sheet_name': self.sheet_name,
            'section': self.section,
            'item': self.item,
            'row_number': self.row_number,
            'column_name': self.column_name,
            'severity': self.severity,
            'code': self.code,
            'message': self.message,
            'expected': self.expected_value,
            'actual': self.actual_value,
            'rule_name': self.rule_name,
            'category': self.category,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_acknowledged': self.is_acknowledged,
        }


class SheetMetadata(models.Model):
    """
    Model to store metadata about individual sheets in Excel documents
    Helps with navigation and understanding document structure
    """
    
    document = models.ForeignKey(
        UploadedExcelDocument,
        on_delete=models.CASCADE,
        related_name='sheet_metadata'
    )
    
    sheet_name = models.CharField(max_length=200)
    sheet_index = models.IntegerField()
    sheet_type = models.CharField(
        max_length=50,
        choices=[
            ('cover', 'Cover Sheet'),
            ('revision_history', 'Revision History'),
            ('holds', 'Holds Sheet'),
            ('toc', 'Table of Contents'),
            ('technical_data', 'Technical Data'),
            ('notes', 'Notes'),
            ('abbreviations', 'Abbreviations'),
            ('other', 'Other'),
        ],
        default='other'
    )
    
    row_count = models.IntegerField(default=0)
    column_count = models.IntegerField(default=0)
    has_data = models.BooleanField(default=True)
    
    # Summary of content
    description = models.TextField(blank=True)
    key_sections = models.JSONField(default=list, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'electrical_sheet_metadata'
        verbose_name = 'Sheet Metadata'
        verbose_name_plural = 'Sheet Metadata'
        ordering = ['document', 'sheet_index']
        unique_together = ['document', 'sheet_name']

    def __str__(self):
        return f"{self.document.filename} - {self.sheet_name}"


class ParsedItem(models.Model):
    """
    Model to store individual parsed line items from technical data sheets
    Represents a single row in the DESCRIPTION/UNIT/SPECIFIED DATA/VENDOR DATA table
    """
    
    document = models.ForeignKey(
        UploadedExcelDocument,
        on_delete=models.CASCADE,
        related_name='parsed_items'
    )
    
    sheet_name = models.CharField(max_length=200, db_index=True)
    section = models.CharField(max_length=300, blank=True, db_index=True)
    
    # Line item details
    sl_no = models.CharField(max_length=50, blank=True)
    description = models.TextField()
    unit = models.CharField(max_length=100, blank=True)
    specified_design_data = models.TextField(blank=True)
    vendor_data = models.TextField(blank=True)
    
    # Parsed position
    row_number = models.IntegerField()
    
    # Metadata
    is_section_header = models.BooleanField(default=False)
    is_empty = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'electrical_parsed_items'
        verbose_name = 'Parsed Item'
        verbose_name_plural = 'Parsed Items'
        ordering = ['document', 'sheet_name', 'row_number']
        indexes = [
            models.Index(fields=['document', 'section']),
            models.Index(fields=['sheet_name', 'section']),
        ]

    def __str__(self):
        return f"{self.sheet_name} - Row {self.row_number}: {self.description[:50]}"
