"""
Onboarding & Offboarding Serializers
Transforms model data to/from JSON for API responses
"""
from rest_framework import serializers
from .models import (
    OnboardingRecord, OffboardingRecord, Equipment,
    Document, AccessProvisioning, Checklist
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
    """Document collection tracking"""
    verified_by_name = serializers.CharField(source='verified_by.get_full_name', read_only=True)
    
    class Meta:
        model = Document
        fields = [
            'id', 'document_type', 'document_name', 'file_path',
            'submitted', 'verified', 'verified_by', 'verified_by_name', 'verified_date',
            'notes', 'created_at', 'updated_at'
        ]


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
    """
    equipment = EquipmentSerializer(many=True, read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)
    access_records = AccessProvisioningSerializer(many=True, read_only=True)
    checklist_items = ChecklistSerializer(many=True, read_only=True)
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    
    days_until_joining = serializers.SerializerMethodField()
    days_since_initiated = serializers.SerializerMethodField()
    
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
            'days_until_joining', 'days_since_initiated'
        ]
    
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
            'joining_date', 'initiated_date', 'target_completion_date',
            'status', 'progress_percentage',
            'created_by_name', 'assigned_to_name',
            'created_at', 'updated_at',
            'days_until_joining',
            'equipment_count', 'documents_count', 'access_count',
            'checklist_count', 'checklist_completed_count'
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
            'exit_reason', 'last_working_day', 'initiated_date', 'target_completion_date',
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
