from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import CRSDocument, CRSComment, CRSActivity, GoogleSheetConfig

User = get_user_model()


class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user serializer for CRS relations"""
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'full_name']
        read_only_fields = ['id', 'email', 'full_name']
    
    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.email


class CRSCommentSerializer(serializers.ModelSerializer):
    """Serializer for CRS Comment model"""
    contractor_responder_details = UserBasicSerializer(source='contractor_responder', read_only=True)
    company_responder_details = UserBasicSerializer(source='company_responder', read_only=True)
    assigned_to_details = UserBasicSerializer(source='assigned_to', read_only=True)
    
    has_contractor_response = serializers.BooleanField(read_only=True)
    has_company_response = serializers.BooleanField(read_only=True)
    is_fully_responded = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = CRSComment
        fields = [
            'id', 'document', 'serial_number', 'page_number', 'clause_number',
            'comment_text', 'comment_type', 'status', 'priority',
            'color_rgb', 'bbox',
            'contractor_response', 'contractor_response_date', 'contractor_responder',
            'contractor_responder_details',
            'company_response', 'company_response_date', 'company_responder',
            'company_responder_details',
            'assigned_to', 'assigned_to_details',
            'created_at', 'updated_at', 'resolved_at', 'notes',
            'has_contractor_response', 'has_company_response', 'is_fully_responded'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CRSCommentBulkCreateSerializer(serializers.Serializer):
    """Serializer for bulk creating comments from PDF extraction"""
    comments = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False
    )
    document_id = serializers.IntegerField()


class CRSActivitySerializer(serializers.ModelSerializer):
    """Serializer for CRS Activity tracking"""
    performed_by_details = UserBasicSerializer(source='performed_by', read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    
    class Meta:
        model = CRSActivity
        fields = [
            'id', 'document', 'comment', 'action', 'action_display',
            'description', 'performed_by', 'performed_by_details',
            'performed_at', 'old_value', 'new_value'
        ]
        read_only_fields = ['id', 'performed_at']


class CRSDocumentSerializer(serializers.ModelSerializer):
    """Serializer for CRS Document model"""
    uploaded_by_details = UserBasicSerializer(source='uploaded_by', read_only=True)
    assigned_to_details = UserBasicSerializer(source='assigned_to', read_only=True)
    
    completion_percentage = serializers.FloatField(read_only=True)
    is_completed = serializers.BooleanField(read_only=True)
    
    comments_summary = serializers.SerializerMethodField()
    recent_activities = serializers.SerializerMethodField()
    
    class Meta:
        model = CRSDocument
        fields = [
            'id', 'document_name', 'document_number', 'pdf_file',
            'google_sheet_id', 'google_sheet_url',
            'status', 'total_comments', 'resolved_comments', 'pending_comments',
            'uploaded_by', 'uploaded_by_details',
            'assigned_to', 'assigned_to_details',
            'project_name', 'contractor_name', 'revision_number',
            'created_at', 'updated_at', 'processed_at', 'notes',
            'completion_percentage', 'is_completed',
            'comments_summary', 'recent_activities'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'uploaded_by']
    
    def get_comments_summary(self, obj):
        """Get summary of comments by status"""
        comments = obj.comments.all()
        return {
            'total': comments.count(),
            'open': comments.filter(status='open').count(),
            'in_progress': comments.filter(status='in_progress').count(),
            'resolved': comments.filter(status='resolved').count(),
            'closed': comments.filter(status='closed').count(),
            'rejected': comments.filter(status='rejected').count(),
        }
    
    def get_recent_activities(self, obj):
        """Get 5 most recent activities"""
        activities = obj.activities.all()[:5]
        return CRSActivitySerializer(activities, many=True).data


class CRSDocumentDetailSerializer(CRSDocumentSerializer):
    """Detailed serializer with all comments included"""
    comments = CRSCommentSerializer(many=True, read_only=True)
    all_activities = CRSActivitySerializer(source='activities', many=True, read_only=True)


class GoogleSheetConfigSerializer(serializers.ModelSerializer):
    """Serializer for Google Sheet Configuration"""
    created_by_details = UserBasicSerializer(source='created_by', read_only=True)
    
    class Meta:
        model = GoogleSheetConfig
        fields = [
            'id', 'name', 'client_id', 'client_secret',
            'credentials_json', 'token_json',
            'is_active', 'created_by', 'created_by_details',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by']
        extra_kwargs = {
            'client_secret': {'write_only': True},
            'credentials_json': {'write_only': True},
            'token_json': {'write_only': True},
        }


class PDFExtractRequestSerializer(serializers.Serializer):
    """Serializer for PDF extraction request"""
    document_id = serializers.IntegerField()
    auto_create_comments = serializers.BooleanField(default=True)
    debug_mode = serializers.BooleanField(default=False)


class GoogleSheetExportSerializer(serializers.Serializer):
    """Serializer for Google Sheets export request"""
    document_id = serializers.IntegerField()
    sheet_id = serializers.CharField(max_length=200, required=False, allow_blank=True)
    start_row = serializers.IntegerField(default=9, min_value=1)
    auto_export = serializers.BooleanField(default=True)
