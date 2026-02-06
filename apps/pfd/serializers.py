"""
PFD Project Serializers
"""

from rest_framework import serializers
from .models import PFDProject, PFDUpload, PFDVerificationReport, PFDIssue


class PFDUploadSerializer(serializers.ModelSerializer):
    """Serializer for PFD uploads"""
    uploaded_by_name = serializers.CharField(source='uploaded_by.get_full_name', read_only=True)
    uploaded_by_email = serializers.CharField(source='uploaded_by.email', read_only=True)
    
    class Meta:
        model = PFDUpload
        fields = [
            'id',
            'upload_id',
            'project',
            'file_name',
            'file_path',
            'file_size',
            'drawing_number',
            'drawing_revision',
            'drawing_title',
            'project_name_field',
            'status',
            'verification_results',
            'uploaded_by',
            'uploaded_by_name',
            'uploaded_by_email',
            'uploaded_at',
            'processed_at',
        ]
        read_only_fields = ['id', 'upload_id', 'uploaded_at', 'processed_at']


class PFDProjectListSerializer(serializers.ModelSerializer):
    """Serializer for listing PFD projects"""
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)

    reference_docs_uploaded = serializers.SerializerMethodField()
    
    def get_reference_docs_uploaded(self, obj):
        """Check if reference documents have been uploaded"""
        if not obj.reference_documents:
            return False
        required_docs = ['bfd', 'process_description', 'process_design_basis', 
                        'operation_control_philosophy', 'scope_of_work', 
                        'legends_symbols', 'equipment_data_sheet']
        return any(obj.reference_documents.get(doc) for doc in required_docs)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    pfd_count = serializers.SerializerMethodField()
    reference_docs_count = serializers.SerializerMethodField()
    
    class Meta:
        model = PFDProject
        fields = [
            'id',
            'project_id',
            'project_name',
            'description',
            'pfd_count',
            'reference_docs_count',
            'created_by',
            'created_by_name',
            'created_by_email',
            'created_at',
            'updated_at',
            'is_active',
            'reference_docs_uploaded',
        ]
        read_only_fields = ['id', 'project_id', 'created_at', 'updated_at']
    
    def get_pfd_count(self, obj):
        """Count of PFD uploads in this project"""
        return obj.pfd_uploads.count()
    
    def get_reference_docs_count(self, obj):
        """Count of uploaded reference documents"""
        if not obj.reference_documents:
            return 0
        return len([v for v in obj.reference_documents.values() if v])


class PFDProjectDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for PFD project with uploads"""
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)

    reference_docs_uploaded = serializers.SerializerMethodField()
    
    def get_reference_docs_uploaded(self, obj):
        """Check if reference documents have been uploaded"""
        if not obj.reference_documents:
            return False
        required_docs = ['bfd', 'process_description', 'process_design_basis', 
                        'operation_control_philosophy', 'scope_of_work', 
                        'legends_symbols', 'equipment_data_sheet']
        return any(obj.reference_documents.get(doc) for doc in required_docs)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    pfd_uploads = PFDUploadSerializer(many=True, read_only=True)
    pfd_count = serializers.SerializerMethodField()
    reference_docs_count = serializers.SerializerMethodField()
    
    class Meta:
        model = PFDProject
        fields = [
            'id',
            'project_id',
            'project_name',
            'description',
            'reference_documents',
            'pfd_count',
            'reference_docs_count',
            'pfd_uploads',
            'created_by',
            'created_by_name',
            'created_by_email',
            'created_at',
            'updated_at',
            'is_active',
            'reference_docs_uploaded',
        ]
        read_only_fields = ['id', 'project_id', 'created_at', 'updated_at']
    
    def get_pfd_count(self, obj):
        """Count of PFD uploads in this project"""
        return obj.pfd_uploads.count()
    
    def get_reference_docs_count(self, obj):
        """Count of uploaded reference documents"""
        if not obj.reference_documents:
            return 0
        return len([v for v in obj.reference_documents.values() if v])



class PFDProjectCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating PFD projects"""
    name = serializers.CharField(source='project_name', write_only=True)
    
    class Meta:
        model = PFDProject
        fields = ['name', 'description']
    
    def create(self, validated_data):
        """Create project with current user"""
        # Handle both User and UserProfile
        user = self.context['request'].user
        if hasattr(user, 'user'):
            # UserProfile - get the actual User
            user = user.user
        
        validated_data['created_by'] = user
        return super().create(validated_data)


class PFDIssueSerializer(serializers.ModelSerializer):
    """Serializer for PFD verification issues"""
    
    class Meta:
        model = PFDIssue
        fields = [
            'id',
            'serial_number',
            'issue_found',
            'action_required',
            'severity',
            'category',
            'status',
            'approval',
            'remark',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class IssueUpdateSerializer(serializers.Serializer):
    """Serializer for bulk updating issue status"""
    issue_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True
    )
    status = serializers.ChoiceField(
        choices=['pending', 'approved', 'ignored'],
        required=False
    )
    approval = serializers.CharField(required=False, allow_blank=True)
    remark = serializers.CharField(required=False, allow_blank=True)


class PFDVerificationReportSerializer(serializers.ModelSerializer):
    """Serializer for PFD verification reports"""
    issues = PFDIssueSerializer(many=True, read_only=True)
    pfd_upload_info = serializers.SerializerMethodField()
    
    class Meta:
        model = PFDVerificationReport
        fields = [
            'id',
            'pfd_upload',
            'pfd_upload_info',
            'total_issues',
            'critical_count',
            'major_count',
            'minor_count',
            'observation_count',
            'approved_count',
            'ignored_count',
            'pending_count',
            'report_data',
            'extracted_drawing_number',
            'extracted_revision',
            'extracted_project_name',
            'extracted_client_name',
            'issues',
            'generated_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'generated_at', 'updated_at']
    
    def get_pfd_upload_info(self, obj):
        """Get basic PFD upload information"""
        return {
            'upload_id': obj.pfd_upload.upload_id,
            'file_name': obj.pfd_upload.file_name,
            'drawing_number': obj.pfd_upload.drawing_number,
            'drawing_revision': obj.pfd_upload.drawing_revision,
            'drawing_title': obj.pfd_upload.drawing_title,
        }
