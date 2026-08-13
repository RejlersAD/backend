"""
Onboarding & Offboarding Serializers
Transforms model data to/from JSON for API responses
"""
from rest_framework import serializers
from .models import (
    OnboardingRecord, OffboardingRecord, Equipment,
    Document, AccessProvisioning, Checklist,
    ExitRequest, ExitActivity, ExitClearance, NoticePeriodPolicy
)


class EquipmentSerializer(serializers.ModelSerializer):
    """Equipment assignment/return tracking"""
    
    class Meta:
        model = Equipment
        fields = [
            'id', 'equipment_type', 'item_name', 'serial_number', 'asset_tag',
            'assigned_date', 'returned_date', 'condition', 'notes',
            'created_at', 'updated_at'
        ]


class DocumentSerializer(serializers.ModelSerializer):
    """Document collection tracking with file upload support"""
    verified_by_name = serializers.CharField(source='verified_by.get_full_name', read_only=True)
    file = serializers.FileField(write_only=True, required=False)
    
    class Meta:
        model = Document
        fields = [
            'id', 'document_type', 'document_name', 'file_path', 'file_url',
            'file_size', 'file_mime_type', 'original_filename',
            'submitted', 'verified', 'verified_by', 'verified_by_name', 'verified_date',
            'notes', 'created_at', 'updated_at', 'file'
        ]
        read_only_fields = ['file_path', 'file_url', 'file_size', 'file_mime_type', 'original_filename']


class AccessProvisioningSerializer(serializers.ModelSerializer):
    """System access provisioning/revocation tracking"""
    assigned_by_name = serializers.CharField(source='assigned_by.get_full_name', read_only=True)
    
    class Meta:
        model = AccessProvisioning
        fields = [
            'id', 'access_type', 'access_name', 'account_username',
            'provisioned', 'provisioned_date', 'revoked', 'revoked_date',
            'assigned_by', 'assigned_by_name', 'notes',
            'created_at', 'updated_at'
        ]


class ChecklistSerializer(serializers.ModelSerializer):
    """Checklist item tracking"""
    completed_by_name = serializers.CharField(source='completed_by.get_full_name', read_only=True)
    
    class Meta:
        model = Checklist
        fields = [
            'id', 'task_name', 'description', 'completed', 'completed_date',
            'completed_by', 'completed_by_name', 'due_date', 'priority',
            'created_at', 'updated_at'
        ]


class OnboardingRecordSerializer(serializers.ModelSerializer):
    """
    Onboarding record with nested equipment, documents, access, and checklist
    Includes passport photo upload support
    """
    equipment = EquipmentSerializer(many=True, read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)
    access_records = AccessProvisioningSerializer(many=True, read_only=True)
    checklist_items = ChecklistSerializer(many=True, read_only=True)
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    
    # Passport photo upload
    photo = serializers.ImageField(write_only=True, required=False, help_text='Passport size photo (JPG/PNG, max 5MB)')
    
    days_until_joining = serializers.SerializerMethodField()
    days_since_initiated = serializers.SerializerMethodField()
    
    # ✅ Engineer profile from EmployeeMaster (read-only for HR visibility)
    engineer_profile = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = OnboardingRecord
        fields = [
            'id', 'employee_name', 'employee_email', 'employee_id', 'user',
            'position', 'department', 'reporting_manager', 'branch',
            'joining_date', 'initiated_date', 'target_completion_date', 'actual_completion_date',
            'status', 'progress_percentage',
            'created_by', 'created_by_name', 'assigned_to', 'assigned_to_name',
            'notes', 'created_at', 'updated_at',
            'equipment', 'documents', 'access_records', 'checklist_items',
            'days_until_joining', 'days_since_initiated',
            'photo', 'photo_file_path', 'photo_url', 'photo_file_size', 'photo_mime_type', 'photo_original_filename',
            'engineer_profile'
        ]
        read_only_fields = ['photo_file_path', 'photo_url', 'photo_file_size', 'photo_mime_type', 'photo_original_filename']
    
    def get_days_until_joining(self, obj):
        """Calculate days until joining date"""
        from datetime import date
        delta = obj.joining_date - date.today()
        return delta.days
    
    def get_days_since_initiated(self, obj):
        """Calculate days since onboarding was initiated"""
        from datetime import date
        delta = date.today() - obj.initiated_date.date()
        return delta.days
    
    def get_engineer_profile(self, obj):
        """Fetch engineer_profile from EmployeeMaster for HR visibility"""
        if obj.user:
            try:
                from apps.hr_core.models import EmployeeMaster
                employee = EmployeeMaster.objects.filter(user=obj.user).first()
                if employee and employee.engineer_profile:
                    return employee.engineer_profile
            except Exception as e:
                print(f"[WARNING] Failed to fetch engineer_profile: {str(e)}")
        return {}


class OnboardingRecordListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list views (no nested data)
    """
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    days_until_joining = serializers.SerializerMethodField()
    
    # Counts
    equipment_count = serializers.IntegerField(read_only=True)
    documents_count = serializers.IntegerField(read_only=True)
    access_count = serializers.IntegerField(read_only=True)
    checklist_count = serializers.IntegerField(read_only=True)
    checklist_completed_count = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = OnboardingRecord
        fields = [
            'id', 'employee_name', 'employee_email', 'employee_id',
            'position', 'department', 'reporting_manager', 'branch',
            'joining_date', 'initiated_date', 'target_completion_date', 'actual_completion_date',
            'status', 'progress_percentage',
            'created_by_name', 'assigned_to_name',
            'created_at', 'updated_at',
            'days_until_joining',
            'equipment_count', 'documents_count', 'access_count',
            'checklist_count', 'checklist_completed_count',
            'photo_url', 'photo_original_filename'
        ]
    
    def get_days_until_joining(self, obj):
        from datetime import date
        delta = obj.joining_date - date.today()
        return delta.days


class OffboardingRecordSerializer(serializers.ModelSerializer):
    """
    Offboarding record with nested equipment, documents, access, and checklist
    """
    equipment = EquipmentSerializer(many=True, read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)
    access_records = AccessProvisioningSerializer(many=True, read_only=True)
    checklist_items = ChecklistSerializer(many=True, read_only=True)
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    
    days_until_exit = serializers.SerializerMethodField()
    days_since_initiated = serializers.SerializerMethodField()
    
    class Meta:
        model = OffboardingRecord
        fields = [
            'id', 'employee_name', 'employee_email', 'employee_id', 'user',
            'position', 'department', 'reporting_manager', 'branch',
            'exit_reason', 'exit_reason_detail', 'last_working_day', 'notice_period_days',
            'initiated_date', 'target_completion_date', 'actual_completion_date',
            'status', 'progress_percentage',
            'created_by', 'created_by_name', 'assigned_to', 'assigned_to_name',
            'notes', 'created_at', 'updated_at',
            'equipment', 'documents', 'access_records', 'checklist_items',
            'days_until_exit', 'days_since_initiated'
        ]
    
    def get_days_until_exit(self, obj):
        """Calculate days until last working day"""
        from datetime import date
        delta = obj.last_working_day - date.today()
        return delta.days
    
    def get_days_since_initiated(self, obj):
        """Calculate days since offboarding was initiated"""
        from datetime import date
        delta = date.today() - obj.initiated_date.date()
        return delta.days


class OffboardingRecordListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list views (no nested data)
    """
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    days_until_exit = serializers.SerializerMethodField()
    
    # Counts
    equipment_count = serializers.IntegerField(read_only=True)
    documents_count = serializers.IntegerField(read_only=True)
    access_count = serializers.IntegerField(read_only=True)
    checklist_count = serializers.IntegerField(read_only=True)
    checklist_completed_count = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = OffboardingRecord
        fields = [
            'id', 'employee_name', 'employee_email', 'employee_id',
            'position', 'department', 'reporting_manager', 'branch',
            'exit_reason', 'last_working_day', 'initiated_date', 'target_completion_date', 'actual_completion_date',
            'status', 'progress_percentage',
            'created_by_name', 'assigned_to_name',
            'created_at', 'updated_at',
            'days_until_exit',
            'equipment_count', 'documents_count', 'access_count',
            'checklist_count', 'checklist_completed_count'
        ]
    
    def get_days_until_exit(self, obj):
        from datetime import date
        delta = obj.last_working_day - date.today()
        return delta.days


# ═══════════════════════════════════════════════════════════════════════════
# EXIT/RESIGNATION WORKFLOW SERIALIZERS
# ═══════════════════════════════════════════════════════════════════════════

class ExitActivitySerializer(serializers.ModelSerializer):
    """Activity log serializer for exit requests"""
    performed_by_name = serializers.CharField(source='performed_by.get_full_name', read_only=True)
    activity_type_display = serializers.CharField(source='get_activity_type_display', read_only=True)
    
    class Meta:
        from .models import ExitActivity
        model = ExitActivity
        fields = [
            'id', 'activity_type', 'activity_type_display', 'activity_description',
            'performed_by', 'performed_by_name', 'metadata', 'activity_date'
        ]
        read_only_fields = ['activity_date']


class ExitClearanceSerializer(serializers.ModelSerializer):
    """Department clearance serializer"""
    department_display = serializers.CharField(source='get_department_display', read_only=True)
    clearance_status_display = serializers.CharField(source='get_clearance_status_display', read_only=True)
    cleared_by_name = serializers.CharField(source='cleared_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        from .models import ExitClearance
        model = ExitClearance
        fields = [
            'id', 'department', 'department_display', 'clearance_status', 'clearance_status_display',
            'cleared_by', 'cleared_by_name', 'clearance_date', 'pending_items', 'comments',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']


class NoticePeriodPolicySerializer(serializers.ModelSerializer):
    """Notice period policy configuration serializer"""
    buyout_calculation_display = serializers.CharField(source='get_buyout_calculation_display', read_only=True)
    
    class Meta:
        from .models import NoticePeriodPolicy
        model = NoticePeriodPolicy
        fields = [
            'id', 'designation_level', 'department', 'standard_notice_days', 'minimum_notice_days',
            'buyout_allowed', 'buyout_calculation', 'buyout_calculation_display',
            'is_active', 'effective_from', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']


class ExitRequestSerializer(serializers.ModelSerializer):
    """
    Full exit request serializer with nested activities and clearances
    """
    # Related user fields
    employee_name = serializers.CharField(read_only=True)
    employee_email = serializers.EmailField(read_only=True)
    reporting_manager_name = serializers.CharField(source='reporting_manager.get_full_name', read_only=True, allow_null=True)
    manager_approved_by_name = serializers.CharField(source='manager_approved_by.get_full_name', read_only=True, allow_null=True)
    hr_approved_by_name = serializers.CharField(source='hr_approved_by.get_full_name', read_only=True, allow_null=True)
    exit_interview_conducted_by_name = serializers.CharField(
        source='exit_interview_conducted_by.get_full_name', read_only=True, allow_null=True
    )
    
    # Display fields
    request_type_display = serializers.CharField(source='get_request_type_display', read_only=True)
    overall_status_display = serializers.CharField(source='get_overall_status_display', read_only=True)
    exit_process_status_display = serializers.CharField(source='get_exit_process_status_display', read_only=True)
    manager_approval_status_display = serializers.CharField(source='get_manager_approval_status_display', read_only=True)
    hr_approval_status_display = serializers.CharField(source='get_hr_approval_status_display', read_only=True)
    
    # Nested data
    activities = ExitActivitySerializer(many=True, read_only=True)
    clearances = ExitClearanceSerializer(many=True, read_only=True)
    
    # File upload
    resignation_letter_file = serializers.FileField(write_only=True, required=False, help_text='Resignation letter upload')
    
    # Computed fields
    actual_notice_days = serializers.SerializerMethodField()
    days_until_lwd = serializers.SerializerMethodField()
    clearance_completion_percentage = serializers.SerializerMethodField()
    
    class Meta:
        from .models import ExitRequest
        model = ExitRequest
        fields = [
            # Basic Info
            'id', 'user', 'employee_name', 'employee_email', 'employee_id', 'position', 'department',
            'reporting_manager', 'reporting_manager_name',
            # Request Details
            'request_type', 'request_type_display', 'exit_reason', 'exit_reason_detail',
            'resignation_letter', 'resignation_letter_url', 'resignation_letter_file',
            # Notice Period
            'proposed_last_working_day', 'notice_period_days', 'standard_notice_period',
            'notice_period_buyout', 'notice_period_buyout_days',
            # Manager Approval
            'manager_approval_status', 'manager_approval_status_display',
            'manager_approved_by', 'manager_approved_by_name', 'manager_approval_date', 'manager_comments',
            # HR Approval
            'hr_approval_status', 'hr_approval_status_display',
            'hr_approved_by', 'hr_approved_by_name', 'hr_approval_date', 'hr_comments', 'final_approved_lwd',
            # Status
            'overall_status', 'overall_status_display',
            'exit_process_status', 'exit_process_status_display',
            # Exit Interview
            'exit_interview_completed', 'exit_interview_date',
            'exit_interview_conducted_by', 'exit_interview_conducted_by_name', 'exit_interview_feedback',
            # Withdrawal
            'withdrawn_at', 'withdrawal_reason',
            # Integration
            'offboarding_record',
            # Metadata
            'created_at', 'updated_at',
            # Nested
            'activities', 'clearances',
            # Computed
            'actual_notice_days', 'days_until_lwd', 'clearance_completion_percentage'
        ]
        read_only_fields = [
            'employee_name', 'employee_email', 'employee_id', 'position', 'department',
            'resignation_letter', 'resignation_letter_url',
            'manager_approved_by', 'manager_approval_date',
            'hr_approved_by', 'hr_approval_date',
            'exit_interview_conducted_by', 'exit_interview_date',
            'withdrawn_at', 'offboarding_record',
            'created_at', 'updated_at'
        ]
    
    def get_actual_notice_days(self, obj):
        """Calculate actual notice days"""
        try:
            return obj.calculate_notice_days()
        except:
            return None
    
    def get_days_until_lwd(self, obj):
        """Days until last working day"""
        from datetime import date
        lwd = obj.final_approved_lwd or obj.proposed_last_working_day
        delta = lwd - date.today()
        return delta.days
    
    def get_clearance_completion_percentage(self, obj):
        """Calculate clearance completion percentage"""
        total_clearances = obj.clearances.count()
        if total_clearances == 0:
            return 0
        cleared = obj.clearances.filter(clearance_status='cleared').count()
        return int((cleared / total_clearances) * 100)


class ExitRequestListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for exit request list views
    """
    employee_name = serializers.CharField(read_only=True)
    position = serializers.CharField(read_only=True)
    department = serializers.CharField(read_only=True)
    request_type_display = serializers.CharField(source='get_request_type_display', read_only=True)
    overall_status_display = serializers.CharField(source='get_overall_status_display', read_only=True)
    manager_approval_status_display = serializers.CharField(source='get_manager_approval_status_display', read_only=True)
    hr_approval_status_display = serializers.CharField(source='get_hr_approval_status_display', read_only=True)
    
    # Counts
    activities_count = serializers.IntegerField(read_only=True)
    clearances_count = serializers.IntegerField(read_only=True)
    clearances_completed_count = serializers.IntegerField(read_only=True)
    
    days_until_lwd = serializers.SerializerMethodField()
    
    class Meta:
        from .models import ExitRequest
        model = ExitRequest
        fields = [
            'id', 'employee_name', 'position', 'department',
            'request_type', 'request_type_display', 'exit_reason',
            'proposed_last_working_day', 'final_approved_lwd',
            'overall_status', 'overall_status_display',
            'manager_approval_status', 'manager_approval_status_display',
            'hr_approval_status', 'hr_approval_status_display',
            'created_at', 'updated_at',
            'activities_count', 'clearances_count', 'clearances_completed_count',
            'days_until_lwd'
        ]
    
    def get_days_until_lwd(self, obj):
        from datetime import date
        lwd = obj.final_approved_lwd or obj.proposed_last_working_day
        delta = lwd - date.today()
        return delta.days
