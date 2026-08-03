"""
Django REST Framework Serializers for Electrical Checklist
Professional project-based system with full validation
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import ChecklistProject, ChecklistProjectMember, ChecklistExtractionJob

User = get_user_model()

# Soft-coded fallback used to label a checklist job when the user hasn't given
# it an explicit name yet (extracted_data['checklist_name']). Keeping this as a
# module-level format string avoids hardcoding the label wherever jobs are listed.
DEFAULT_CHECKLIST_NAME_FORMAT = "Checklist #{id} — {date}"

# Soft-coded project name length constraints (kept in sync with the frontend
# PROJECT_NAME_MIN_LENGTH / MAX_LENGTH constants in electricalChecklist.config.js).
PROJECT_NAME_MIN_LENGTH = 3
PROJECT_NAME_MAX_LENGTH = 200


class UserBriefSerializer(serializers.ModelSerializer):
    """Brief user info for project members"""
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'full_name', 'first_name', 'last_name']
        
    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class ChecklistProjectMemberSerializer(serializers.ModelSerializer):
    """Project team member serializer"""
    user = UserBriefSerializer(read_only=True)
    user_id = serializers.IntegerField(write_only=True)
    added_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = ChecklistProjectMember
        fields = [
            'id', 'user', 'user_id', 'role', 
            'added_at', 'added_by_name'
        ]
        read_only_fields = ['id', 'added_at']
        
    def get_added_by_name(self, obj):
        if obj.added_by:
            return obj.added_by.get_full_name() or obj.added_by.username
        return None


class ChecklistProjectListSerializer(serializers.ModelSerializer):
    """
    List view serializer for projects
    Lightweight with key stats
    """
    owner_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    latest_activity = serializers.SerializerMethodField()
    
    class Meta:
        model = ChecklistProject
        fields = [
            'id', 'project_code', 'project_name', 'description',
            'location', 'client_name', 'status', 'template_id',
            'owner_name', 'created_by_name', 'member_count',
            'total_checklists', 'total_fields_extracted',
            'total_signatures_found', 'avg_confidence_score',
            'created_at', 'updated_at', 'latest_activity',
            'start_date', 'end_date', 'tags'
        ]
        
    def get_owner_name(self, obj):
        return obj.owner.get_full_name() or obj.owner.username
        
    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() or obj.created_by.username
        
    def get_member_count(self, obj):
        return obj.project_members.count()
        
    def get_latest_activity(self, obj):
        latest_job = obj.checklist_jobs.order_by('-updated_at').first()
        if latest_job:
            return latest_job.updated_at.isoformat()
        return obj.updated_at.isoformat()


class ChecklistProjectDetailSerializer(serializers.ModelSerializer):
    """
    Detailed view serializer for projects
    Includes full member list and settings
    """
    owner = UserBriefSerializer(read_only=True)
    created_by = UserBriefSerializer(read_only=True)
    members_list = ChecklistProjectMemberSerializer(
        source='project_members',
        many=True,
        read_only=True
    )
    recent_jobs = serializers.SerializerMethodField()
    
    class Meta:
        model = ChecklistProject
        fields = [
            'id', 'project_code', 'project_name', 'description',
            'location', 'client_name', 'status', 'template_id',
            'settings', 'tags', 's3_folder',
            'owner', 'created_by', 'members_list',
            'start_date', 'end_date',
            'total_checklists', 'total_fields_extracted',
            'total_signatures_found', 'avg_confidence_score',
            'created_at', 'updated_at', 'recent_jobs'
        ]
        read_only_fields = ['id', 'project_code', 's3_folder', 'created_at', 'updated_at']
        
    def get_recent_jobs(self, obj):
        """Get 5 most recent jobs"""
        jobs = obj.checklist_jobs.order_by('-created_at')[:5]
        return ChecklistExtractionJobBriefSerializer(jobs, many=True).data


class ChecklistProjectCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating new projects
    Validates all required fields
    """
    class Meta:
        model = ChecklistProject
        fields = [
            'project_name', 'description', 'location', 'client_name',
            'template_id', 'settings', 'tags',
            'start_date', 'end_date', 'status'
        ]
        
    def validate_project_name(self, value):
        """Validate project name length and characters"""
        value = value.strip()
        if len(value) < PROJECT_NAME_MIN_LENGTH:
            raise serializers.ValidationError(
                f"Project name must be at least {PROJECT_NAME_MIN_LENGTH} characters long"
            )
        if len(value) > PROJECT_NAME_MAX_LENGTH:
            raise serializers.ValidationError(
                f"Project name cannot exceed {PROJECT_NAME_MAX_LENGTH} characters"
            )
        return value
    
    def validate_settings(self, value):
        """Validate settings dictionary structure"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Settings must be a dictionary")
        
        # Ensure required keys exist with defaults
        default_settings = {
            'extract_signatures': True,
            'require_approval': False,
            'auto_generate_excel': True,
            's3_storage': True,
            'notification_enabled': True
        }
        
        # Merge with defaults
        return {**default_settings, **value}
    
    def create(self, validated_data):
        """Create project with auto-assigned owner and creator"""
        request = self.context.get('request')
        validated_data['owner'] = request.user
        validated_data['created_by'] = request.user
        return super().create(validated_data)


class ChecklistExtractionJobBriefSerializer(serializers.ModelSerializer):
    """Brief job info for lists"""
    user_name = serializers.SerializerMethodField()
    checklist_name = serializers.SerializerMethodField()
    cost_usd = serializers.SerializerMethodField()
    
    class Meta:
        model = ChecklistExtractionJob
        fields = [
            'id', 'status', 'progress', 'fields_extracted',
            'signatures_found', 'confidence_score',
            'file_count', 'total_pages',
            'created_at', 'completed_at', 'user_name', 'checklist_name',
            'cost_usd'
        ]
        
    def get_user_name(self, obj):
        return obj.user.get_full_name() or obj.user.username

    def get_checklist_name(self, obj):
        name = (obj.extracted_data or {}).get('checklist_name')
        if name:
            return name
        return DEFAULT_CHECKLIST_NAME_FORMAT.format(
            id=obj.id,
            date=obj.created_at.strftime('%b %d, %Y') if obj.created_at else ''
        )

    def get_cost_usd(self, obj):
        """Exact $ cost of this job's OpenAI Vision API usage (0.0 for OCR-only jobs)."""
        return (obj.extracted_data or {}).get('cost_usd', 0.0)


class ChecklistExtractionJobDetailSerializer(serializers.ModelSerializer):
    """Detailed job serializer with full data"""
    project = ChecklistProjectListSerializer(read_only=True)
    user = UserBriefSerializer(read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    pdf_urls = serializers.SerializerMethodField()
    excel_url = serializers.SerializerMethodField()
    
    class Meta:
        model = ChecklistExtractionJob
        fields = [
            'id', 'project', 'user', 'template_id',
            'status', 'progress', 'file_count', 'total_pages',
            'fields_extracted', 'signatures_found', 'confidence_score',
            'extracted_data', 'pdf_s3_keys', 'pdf_urls',
            'excel_s3_key', 'excel_file_size', 'excel_url',
            'requires_approval', 'approved_by_name', 'approved_at',
            'created_at', 'updated_at', 'completed_at',
            'error_message'
        ]
        
    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.username
        return None
        
    def get_pdf_urls(self, obj):
        """Generate presigned URLs for PDFs"""
        from .s3_service import get_s3_service
        s3 = get_s3_service()
        return [s3.generate_presigned_url(key) for key in obj.pdf_s3_keys if key]
        
    def get_excel_url(self, obj):
        """Generate presigned URL for Excel"""
        if obj.excel_s3_key:
            from .s3_service import get_s3_service
            s3 = get_s3_service()
            return s3.generate_presigned_url(obj.excel_s3_key, expiration=300)
        return None


class ChecklistExtractionJobCreateSerializer(serializers.Serializer):
    """
    Serializer for creating extraction jobs
    Handles file upload and project context
    """
    project_id = serializers.IntegerField(required=True)
    template_id = serializers.CharField(default='ups_battery_inspection')
    extract_signatures = serializers.BooleanField(default=True)
    requires_approval = serializers.BooleanField(default=False)
    
    def validate_project_id(self, value):
        """Validate project exists and user has access"""
        request = self.context.get('request')
        try:
            project = ChecklistProject.objects.get(id=value, is_deleted=False)
            
            # Check if user is owner or member
            if project.owner != request.user and not project.members.filter(id=request.user.id).exists():
                raise serializers.ValidationError("You don't have access to this project")
                
            return value
            
        except ChecklistProject.DoesNotExist:
            raise serializers.ValidationError("Project not found")
