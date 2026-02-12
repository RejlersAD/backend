"""
Pump Hydraulic Calculation Data Model
Stores comprehensive pump calculation data in PostgreSQL
"""
from django.db import models
from django.contrib.auth.models import User
import uuid


class PumpCalculationData(models.Model):
    """
    Comprehensive pump hydraulic calculation data sheet model
    Based on industry-standard pump data sheet requirements
    """
    
    # Primary key and metadata
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pump_calculations')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Project Information Fields
    agreement_no = models.CharField(max_length=100, help_text="Agreement/Contract Number")
    project_no = models.CharField(max_length=100, help_text="Project Number")
    document_no = models.CharField(max_length=100, help_text="Document Number")
    revision = models.CharField(max_length=20, help_text="Document Revision")
    document_class = models.CharField(
        max_length=20,
        choices=[
            ('confidential', 'Confidential'),
            ('internal', 'Internal'),
            ('public', 'Public'),
            ('restricted', 'Restricted')
        ],
        help_text="Document Classification"
    )
    tag_no = models.CharField(max_length=100, help_text="Equipment Tag Number")
    service = models.CharField(max_length=200, help_text="Service Description")
    motor_classification = models.CharField(
        max_length=50,
        choices=[
            ('class_i_div_1', 'Class I, Div 1'),
            ('class_i_div_2', 'Class I, Div 2'),
            ('class_ii_div_1', 'Class II, Div 1'),
            ('class_ii_div_2', 'Class II, Div 2'),
            ('non_hazardous', 'Non-Hazardous')
        ],
        help_text="Motor Electrical Classification"
    )
    
    # Process Conditions
    temperature = models.DecimalField(
        max_digits=8, 
        decimal_places=2, 
        help_text="Operating Temperature (°C)"
    )
    fluid_viscosity_at_temp = models.DecimalField(
        max_digits=10, 
        decimal_places=4, 
        help_text="Fluid Viscosity at Temperature (cP)"
    )
    hp = models.DecimalField(
        max_digits=8, 
        decimal_places=2, 
        help_text="Horsepower (HP)"
    )
    pump_centerline_elevation = models.DecimalField(
        max_digits=8, 
        decimal_places=3, 
        help_text="Pump Centerline Elevation from Grade (m)"
    )
    elevation_source_btl = models.DecimalField(
        max_digits=8, 
        decimal_places=3, 
        help_text="Elevation of Source BTL from Pump Centerline (m)"
    )
    
    # Calculation Status
    status = models.CharField(
        max_length=20,
        choices=[
            ('draft', 'Draft'),
            ('in_review', 'In Review'),
            ('approved', 'Approved'),
            ('superseded', 'Superseded')
        ],
        default='draft'
    )
    
    # Soft-coded JSON field for additional data
    additional_data = models.JSONField(default=dict, blank=True)
    
    class Meta:
        db_table = 'pump_calculation_data'
        ordering = ['-created_at']
        verbose_name = 'Pump Calculation Data'
        verbose_name_plural = 'Pump Calculation Data'
        indexes = [
            models.Index(fields=['tag_no']),
            models.Index(fields=['project_no']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.tag_no} - {self.project_no} (Rev. {self.revision})"
    
    def save(self, *args, **kwargs):
        # Auto-generate document number if not provided
        if not self.document_no and self.project_no and self.tag_no:
            self.document_no = f"PDS-{self.project_no}-{self.tag_no}"
        super().save(*args, **kwargs)
    
    @property
    def calculation_summary(self):
        """Return summary of key calculation parameters"""
        return {
            'tag_no': self.tag_no,
            'service': self.service,
            'hp': float(self.hp) if self.hp else None,
            'temperature': float(self.temperature) if self.temperature else None,
            'status': self.status
        }