"""
PFD Converter Serializers
"""
from rest_framework import serializers
from .models import PFDDocument, PIDConversion, ConversionFeedback


class PFDDocumentSerializer(serializers.ModelSerializer):
    """Serializer for PFD documents"""
    uploaded_by_name = serializers.CharField(source='uploaded_by.username', read_only=True)
    processing_time = serializers.SerializerMethodField()
    conversion_count = serializers.SerializerMethodField()
    
    class Meta:
        model = PFDDocument
        fields = [
            'id', 'uploaded_by', 'uploaded_by_name',
            'document_title', 'document_number', 'revision',
            'project_name', 'project_code',
            'file', 'file_name', 'file_size', 'file_type',
            'status', 'processing_started_at', 'processing_completed_at',
            'processing_duration', 'processing_time',
            'extracted_data', 'conversion_notes', 'error_message',
            'conversion_count', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'uploaded_by', 'file_size', 'file_type',
            'processing_started_at', 'processing_completed_at',
            'processing_duration', 'extracted_data', 'created_at', 'updated_at'
        ]
    
    def get_processing_time(self, obj):
        """Get formatted processing time"""
        if obj.processing_duration:
            if obj.processing_duration < 60:
                return f"{obj.processing_duration:.1f}s"
            return f"{obj.processing_duration/60:.1f}min"
        return None
    
    def get_conversion_count(self, obj):
        """Get number of P&ID conversions"""
        return obj.pid_conversions.count()


class PIDConversionSerializer(serializers.ModelSerializer):
    """Serializer for P&ID conversions"""
    pfd_document_number = serializers.CharField(source='pfd_document.document_number', read_only=True)
    converted_by_name = serializers.CharField(source='converted_by.username', read_only=True)
    reviewed_by_name = serializers.CharField(source='reviewed_by.username', read_only=True, allow_null=True)
    generation_time = serializers.SerializerMethodField()
    feedback_count = serializers.SerializerMethodField()
    average_rating = serializers.SerializerMethodField()
    
    class Meta:
        model = PIDConversion
        fields = [
            'id', 'pfd_document', 'pfd_document_number',
            'converted_by', 'converted_by_name',
            'pid_drawing_number', 'pid_title', 'pid_revision',
            'pid_file', 'preview_image',
            'status', 'generation_started_at', 'generation_completed_at',
            'generation_duration', 'generation_time',
            'equipment_list', 'instrument_list', 'piping_details',
            'safety_systems', 'design_parameters', 'compliance_checks',
            'reviewed_by', 'reviewed_by_name', 'reviewed_at', 'review_notes',
            'confidence_score', 'feedback_count', 'average_rating',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'converted_by', 'generation_started_at',
            'generation_completed_at', 'generation_duration',
            'created_at', 'updated_at'
        ]
    
    def get_generation_time(self, obj):
        """Get formatted generation time"""
        if obj.generation_duration:
            if obj.generation_duration < 60:
                return f"{obj.generation_duration:.1f}s"
            return f"{obj.generation_duration/60:.1f}min"
        return None
    
    def get_feedback_count(self, obj):
        """Get feedback count"""
        return obj.feedback.count()
    
    def get_average_rating(self, obj):
        """Get average rating from feedback"""
        feedbacks = obj.feedback.all()
        if not feedbacks:
            return None
        avg = sum(f.rating for f in feedbacks) / len(feedbacks)
        return round(avg, 1)


class ConversionFeedbackSerializer(serializers.ModelSerializer):
    """Serializer for conversion feedback"""
    user_name = serializers.CharField(source='user.username', read_only=True)
    conversion_pid_number = serializers.CharField(source='conversion.pid_drawing_number', read_only=True)
    
    class Meta:
        model = ConversionFeedback
        fields = [
            'id', 'conversion', 'conversion_pid_number',
            'user', 'user_name',
            'rating', 'accuracy_rating', 'completeness_rating', 'usability_rating',
            'comments', 'suggested_improvements', 'issues_found',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


class PFDUploadSerializer(serializers.Serializer):
    """Serializer for PFD file upload"""
    file = serializers.FileField()
    document_title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    document_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    revision = serializers.CharField(max_length=50, required=False, allow_blank=True)
    project_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    project_code = serializers.CharField(max_length=100, required=False, allow_blank=True)


class ConversionRequestSerializer(serializers.Serializer):
    """Serializer for conversion request"""
    pfd_document_id = serializers.UUIDField()
    pid_drawing_number = serializers.CharField(max_length=100)
    pid_title = serializers.CharField(max_length=255)
    pid_revision = serializers.CharField(max_length=50, default='A')
    auto_generate = serializers.BooleanField(default=True)
