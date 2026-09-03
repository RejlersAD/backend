"""
HR Core Serializers - Employee Master API Serialization
"""
from rest_framework import serializers
from apps.hr_core.models import (
    EmployeeIdentityAlias,
    EmployeeMaster,
    HRWorkflowDefinition,
    HRWorkflowEvent,
    HRWorkflowInstance,
    HRWorkflowStage,
    HRWorkflowTask,
)
from apps.users.serializers import UserSerializer


class EmployeeMasterListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for employee lists.
    Used in dropdowns, search results, etc.
    """
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    display_name = serializers.CharField(source='get_display_name', read_only=True)
    
    class Meta:
        model = EmployeeMaster
        fields = [
            'id',
            'employee_number',
            'employee_code',
            'email',
            'full_name',
            'display_name',
            'department',
            'designation',
            'employment_status',
            'photo_url',
        ]
        read_only_fields = fields


class EmployeeMasterDetailSerializer(serializers.ModelSerializer):
    """
    Full employee details serializer.
    Used for employee profile, onboarding, etc.
    """
    user = UserSerializer(read_only=True)
    manager = EmployeeMasterListSerializer(read_only=True)
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    display_name = serializers.CharField(source='get_display_name', read_only=True)
    
    # Photo upload field (write-only)
    photo = serializers.ImageField(write_only=True, required=False)
    
    class Meta:
        model = EmployeeMaster
        fields = [
            # Identity
            'id',
            'employee_number',
            'employee_code',
            'emp_code',
            'email',
            
            # Relations
            'user',
            'manager',
            
            # Personal
            'first_name',
            'last_name',
            'preferred_given_name',
            'initials',
            'full_name',
            'display_name',
            'date_of_birth',
            
            # Photo
            'photo',  # write-only
            'photo_url',
            'photo_file_size',
            'photo_mime_type',
            'photo_uploaded_at',
            
            # Organization
            'department',
            'division',
            'business_unit',
            'business_area',
            'office',
            'branch',
            
            # Job
            'job_title_uae',
            'job_title_finland',
            'designation',
            
            # Employment
            'join_date',
            'probation_end_date',
            'confirmation_date',
            'exit_date',
            'employment_status',
            
            # Salary
            'current_base_salary',
            'currency',
            
            # Contact
            'phone_number',
            'country',
            'city',
            'address',
            'postal_code',
            
            # Banking
            'bank_account_number',
            'bank_name',
            'iban',
            'swift_code',
            
            # Tax
            'pan_number',
            'uan_number',
            'tax_id',
            
            # Integration
            'employment_id',
            'candidate_id',
            'account_name',
            
            # Flags
            'is_test_person',
            'protected_identity',
            'not_signed',
            
            # Metadata
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'employee_number',
            'employee_code',
            'emp_code',
            'photo_url',
            'photo_file_size',
            'photo_mime_type',
            'photo_uploaded_at',
            'created_at',
            'updated_at',
        ]
    
    def create(self, validated_data):
        """Not used - employee creation goes through EmployeeService."""
        raise NotImplementedError("Use EmployeeService.create_employee() instead")
    
    def update(self, instance, validated_data):
        """Update employee, handling photo upload if present."""
        photo = validated_data.pop('photo', None)
        
        # Update all other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        
        # Handle photo upload separately through service
        if photo:
            from apps.hr_core.services import EmployeeService
            request_user = self.context.get('request').user if self.context.get('request') else None
            EmployeeService.upload_employee_photo(instance, photo, uploaded_by=request_user)
        
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        can_view_sensitive = bool(user and (user.is_staff or user.is_superuser or user.id == instance.user_id))
        if user and not can_view_sensitive:
            try:
                roles = set(user.rbac_profile.roles.filter(is_active=True).values_list('code', flat=True))
                can_view_sensitive = bool(roles & {'hr_manager', 'hr_admin', 'payroll_admin', 'finance_manager'})
            except Exception:
                pass
        if not can_view_sensitive:
            for field in (
                'current_base_salary', 'currency', 'bank_account_number', 'bank_name',
                'iban', 'swift_code', 'pan_number', 'uan_number', 'tax_id',
            ):
                data.pop(field, None)
        return data


class EmployeeIdentityAliasSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeIdentityAlias
        fields = [
            'id', 'employee', 'source', 'identifier_type', 'value',
            'is_primary', 'verified_at', 'metadata', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class HRWorkflowStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = HRWorkflowStage
        fields = [
            'id', 'definition', 'code', 'name', 'sequence', 'approver_type',
            'approver_value', 'due_after_hours', 'escalate_after_hours',
            'escalation_role_code', 'require_comment_on_reject', 'configuration',
        ]


class HRWorkflowDefinitionSerializer(serializers.ModelSerializer):
    stages = HRWorkflowStageSerializer(many=True, read_only=True)

    class Meta:
        model = HRWorkflowDefinition
        fields = [
            'id', 'code', 'name', 'version', 'description', 'subject_type',
            'is_active', 'configuration', 'stages', 'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']


class HRWorkflowTaskSerializer(serializers.ModelSerializer):
    stage_name = serializers.CharField(source='stage.name', read_only=True)
    stage_code = serializers.CharField(source='stage.code', read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    can_act = serializers.SerializerMethodField()

    class Meta:
        model = HRWorkflowTask
        fields = [
            'id', 'stage', 'stage_name', 'stage_code', 'assigned_to',
            'assigned_to_name', 'assigned_role_code', 'status', 'due_at',
            'reminder_sent_at', 'escalated_at', 'decided_by', 'decided_by_name',
            'decided_at', 'decision_note', 'can_act', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    @staticmethod
    def _name(user):
        return (user.get_full_name().strip() or user.email) if user else None

    def get_assigned_to_name(self, obj):
        return self._name(obj.assigned_to)

    def get_decided_by_name(self, obj):
        return self._name(obj.decided_by)

    def get_can_act(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        from .workflows import HRWorkflowService

        return obj.status == 'pending' and HRWorkflowService.can_act(obj, request.user)


class HRWorkflowEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = HRWorkflowEvent
        fields = [
            'id', 'event_type', 'actor', 'actor_name', 'stage_code',
            'note', 'metadata', 'created_at',
        ]
        read_only_fields = fields

    def get_actor_name(self, obj):
        return (obj.actor.get_full_name().strip() or obj.actor.email) if obj.actor else None


class HRWorkflowInstanceSerializer(serializers.ModelSerializer):
    definition_code = serializers.CharField(source='definition.code', read_only=True)
    definition_name = serializers.CharField(source='definition.name', read_only=True)
    employee_name = serializers.CharField(source='employee.get_display_name', read_only=True)
    current_stage_name = serializers.CharField(source='current_stage.name', read_only=True)
    tasks = HRWorkflowTaskSerializer(many=True, read_only=True)
    events = HRWorkflowEventSerializer(many=True, read_only=True)

    class Meta:
        model = HRWorkflowInstance
        fields = [
            'id', 'definition', 'definition_code', 'definition_name', 'employee',
            'employee_name', 'subject_type', 'subject_id', 'status', 'current_stage',
            'current_stage_name', 'requested_by', 'completed_at', 'context',
            'tasks', 'events', 'created_at', 'updated_at',
        ]
        read_only_fields = fields
