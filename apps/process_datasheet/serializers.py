"""
Process Datasheet Serializers
API data serialization for datasheets
"""
from rest_framework import serializers
from .models import (
    EquipmentType,
    ProcessDatasheet,
    DatasheetRevision,
    DatasheetTemplate,
    DatasheetValidationRule,
    DatasheetExtractionJob,
    PumpCalculationData
)
from django.contrib.auth import get_user_model

User = get_user_model()


class EquipmentTypeSerializer(serializers.ModelSerializer):
    """Serializer for Equipment Type"""
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = EquipmentType
        fields = [
            'id', 'code', 'name', 'icon', 'description', 'category',
            'configuration', 'template_file', 'calculation_module',
            'status', 'version', 'applicable_standards',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class EquipmentTypeListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing equipment types"""
    
    class Meta:
        model = EquipmentType
        fields = [
            'id', 'code', 'name', 'icon', 'category',
            'version', 'status'
        ]


class DatasheetRevisionSerializer(serializers.ModelSerializer):
    """Serializer for Datasheet Revisions"""
    
    revised_by_name = serializers.CharField(source='revised_by.get_full_name', read_only=True)
    
    class Meta:
        model = DatasheetRevision
        fields = [
            'id', 'revision_number', 'description',
            'data_snapshot', 'changes', 'pages_affected',
            'revised_by', 'revised_by_name', 'revision_date'
        ]
        read_only_fields = ['id', 'revision_date']


class ProcessDatasheetSerializer(serializers.ModelSerializer):
    """Complete serializer for Process Datasheet"""
    
    equipment_type_detail = EquipmentTypeSerializer(source='equipment_type', read_only=True)
    prepared_by_name = serializers.CharField(source='prepared_by.get_full_name', read_only=True)
    checked_by_name = serializers.CharField(source='checked_by.get_full_name', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True)
    revisions = DatasheetRevisionSerializer(many=True, read_only=True)
    
    class Meta:
        model = ProcessDatasheet
        fields = [
            'id', 'document_number', 'contractor_document_number', 'title',
            'equipment_type', 'equipment_type_detail',
            'tag_number', 'service_description', 'location',
            'project_name', 'project_number', 'unit_number', 'area',
            'data', 'calculated_values',
            'validation_status', 'validation_results', 'validation_score',
            'extraction_metadata',
            'status', 'document_class', 'revision',
            'pid_drawing_number', 'line_number', 'material_spec', 'related_documents',
            'source_files', 'generated_pdf',
            'holds', 'comments',
            'prepared_by', 'prepared_by_name', 'date_prepared',
            'checked_by', 'checked_by_name', 'date_checked',
            'approved_by', 'approved_by_name', 'date_approved',
            'revisions',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'calculated_values', 'validation_status', 
            'validation_results', 'validation_score',
            'created_at', 'updated_at'
        ]


class ProcessDatasheetListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing datasheets"""
    
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    equipment_type_icon = serializers.CharField(source='equipment_type.icon', read_only=True)
    prepared_by_name = serializers.CharField(source='prepared_by.get_full_name', read_only=True)
    
    class Meta:
        model = ProcessDatasheet
        fields = [
            'id', 'document_number', 'tag_number', 'title',
            'equipment_type', 'equipment_type_name', 'equipment_type_icon',
            'status', 'revision', 'validation_score',
            'prepared_by_name', 'updated_at'
        ]


class ProcessDatasheetCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating new datasheets"""
    
    class Meta:
        model = ProcessDatasheet
        fields = [
            'document_number', 'contractor_document_number', 'title',
            'equipment_type', 'tag_number', 'service_description', 'location',
            'project_name', 'project_number', 'unit_number', 'area',
            'data',
            'pid_drawing_number', 'line_number', 'material_spec'
        ]
    
    def create(self, validated_data):
        # Set prepared_by from request user
        validated_data['prepared_by'] = self.context['request'].user
        return super().create(validated_data)


class DatasheetTemplateSerializer(serializers.ModelSerializer):
    """Serializer for Datasheet Templates"""
    
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = DatasheetTemplate
        fields = [
            'id', 'name', 'description',
            'equipment_type', 'equipment_type_name',
            'template_data', 'usage_count',
            'is_global', 'created_by', 'created_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'usage_count', 'created_at', 'updated_at']


class DatasheetValidationRuleSerializer(serializers.ModelSerializer):
    """Serializer for Validation Rules"""
    
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = DatasheetValidationRule
        fields = [
            'id', 'rule_id', 'name', 'description',
            'equipment_type', 'equipment_type_name',
            'condition', 'severity', 'message',
            'is_active', 'applies_to_projects',
            'created_by', 'created_by_name', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class DatasheetExtractionJobSerializer(serializers.ModelSerializer):
    """Serializer for Extraction Jobs"""
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    pdf_file = serializers.FileField(write_only=False, required=False)
    
    class Meta:
        model = DatasheetExtractionJob
        fields = [
            'id', 'equipment_type', 'equipment_type_name', 'pdf_file',
            'datasheet', 'job_type', 'extraction_mode', 'source_files',
            'status', 'progress',
            'extracted_data', 'confidence_scores',
            'error_message', 'retry_count',
            'started_at', 'completed_at',
            'created_by', 'created_by_name', 'created_at'
        ]
        read_only_fields = [
            'id', 'status', 'progress', 'extracted_data',
            'confidence_scores', 'error_message',
            'started_at', 'completed_at', 'created_at',
            'equipment_type_name', 'created_by_name'
        ]


class PumpCalculationDataSerializer(serializers.ModelSerializer):
    """Complete serializer for Pump Calculation Data"""
    
    prepared_by_name = serializers.CharField(source='prepared_by.get_full_name', read_only=True)
    checked_by_name = serializers.CharField(source='checked_by.get_full_name', read_only=True)
    calculation_summary = serializers.ReadOnlyField()
    
    class Meta:
        model = PumpCalculationData
        fields = [
            'id', 'agreement_no', 'project_no', 'document_no', 'revision',
            'document_class', 'tag_no', 'service', 'motor_classification',
            'temperature', 'fluid_viscosity_at_temp', 'hp',
            'pump_centerline_elevation', 'elevation_source_btl',
            # Discharge Pressure Calculations
            'destination_description', 'flow_type', 'destination_pressure', 
            'destination_elevation', 'line_friction_loss', 'flow_meter_del_p',
            'other_losses', 'control_valve', 'misc_item', 'contingency',
            'total_discharge_pressure',
            # Control Valve Delta P Check
            'density', 'cv_max', 'cv_min', 'cv_ratio', 'total_frictional_losses',
            'dynamic_losses_30_percent', 'cv_pressure_drop', 'cv_rangeability',
            'cv_ratio_within_range', 'cv_pressure_drop_check',
            # Suction Pressure Calculations
            'source_op_pressure', 'suction_elm', 'inline_inst_losses', 'line_fric_losses',
            'control_valve_suction', 'misc_items_suction', 'total_suction_losses', 'total_suction_pressure',
            # Power Consumption Per Pump
            'hydraulic_power', 'pump_efficiency', 'break_horse_power', 'motor_rating',
            'motor_efficiency', 'power_consumption', 'type_of_motor',
            # NPSH Availability
            'suction_pressure_npsh', 'vapor_pressure', 'npsha', 'safety_margin_npsha',
            'npsha_with_safety_margin',
            # Additional data sections
            'general_data', 'control_valve_delta_p', 'suction_pressure_calculations', 'power_consumption_per_pump',
            'npsh_availability', 'general_notes',
            'calculation_results', 'calculation_summary',
            'status', 'source_files',
            'prepared_by', 'prepared_by_name',
            'checked_by', 'checked_by_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'calculation_summary', 'created_at', 'updated_at'
        ]


class PumpCalculationDataListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing pump calculations"""
    
    prepared_by_name = serializers.CharField(source='prepared_by.get_full_name', read_only=True)
    
    class Meta:
        model = PumpCalculationData
        fields = [
            'id', 'document_no', 'tag_no', 'service', 'project_no',
            'status', 'revision', 'temperature', 'hp',
            'total_discharge_pressure', 'destination_description',
            'prepared_by_name', 'updated_at'
        ]


class PumpCalculationDataCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating new pump calculations"""
    
    class Meta:
        model = PumpCalculationData
        fields = [
            'id',  # CRITICAL: ID must be included for frontend to track created records
            'agreement_no', 'project_no', 'document_no', 'revision',
            'document_class', 'tag_no', 'service', 'motor_classification',
            'temperature', 'fluid_viscosity_at_temp', 'hp',
            'pump_centerline_elevation', 'elevation_source_btl',
            
            # Template Data Sheet Fields
            'company_name', 'site', 'unit', 'manufacturer', 'model',
            
            # Liquid Characteristics (Max/Min)
            'liquid_type', 'vapor_pressure_max', 'vapor_pressure_min',
            'density_max', 'density_min', 'viscosity_max', 'viscosity_min',
            'temperature_max', 'temperature_min',
            
            # Operating Conditions (Max/Normal/Min)
            'flow_rate_max', 'flow_rate_normal', 'flow_rate_min',
            'suction_pressure_max', 'suction_pressure_normal', 'suction_pressure_min',
            'discharge_pressure_max', 'discharge_pressure_normal', 'discharge_pressure_min',
            'differential_pressure_max', 'differential_pressure_normal', 'differential_pressure_min',
            'differential_head_max', 'differential_head_normal', 'differential_head_min',
            
            # NPSH (Max/Min)
            'npsh_available_max', 'npsh_available_min', 'npsh_required',
            
            # Pump Performance (Max/Normal/Min)
            'pump_efficiency_max', 'pump_efficiency_normal', 'pump_efficiency_min',
            'bhp_max', 'bhp_normal', 'bhp_min',
            'absorbed_power_max', 'absorbed_power_normal', 'absorbed_power_min',
            
            # Motor/Driver Data
            'driver_type', 'motor_rating', 'motor_voltage', 'motor_speed', 'motor_efficiency',
            
            # Construction Materials
            'casing', 'impeller', 'shaft', 'bearings', 'mechanical_seal',
            
            # Discharge Pressure Calculations
            'destination_description', 'flow_type', 'destination_pressure', 
            'destination_elevation', 'line_friction_loss', 'flow_meter_del_p',
            'other_losses', 'control_valve', 'misc_item', 'contingency',
            'total_discharge_pressure',
            # Control Valve Delta P Check
            'density', 'cv_max', 'cv_min', 'cv_ratio', 'total_frictional_losses',
            'dynamic_losses_30_percent', 'cv_pressure_drop', 'cv_rangeability',
            'cv_ratio_within_range', 'cv_pressure_drop_check',
            # Suction Pressure Calculations
            'source_op_pressure', 'suction_elm', 'inline_inst_losses', 'line_fric_losses',
            'control_valve_suction', 'misc_items_suction', 'total_suction_losses', 'total_suction_pressure',
            # Power Consumption Per Pump
            'hydraulic_power', 'pump_efficiency', 'break_horse_power',
            'power_consumption', 'type_of_motor',
            # NPSH Availability
            'suction_pressure_npsh', 'vapor_pressure', 'npsha', 'safety_margin_npsha',
            'npsha_with_safety_margin',
            # Additional data sections
            'general_data', 'control_valve_delta_p', 'suction_pressure_calculations', 'power_consumption_per_pump',
            'npsh_availability', 'general_notes',
            'created_at', 'updated_at'  # Include timestamps for tracking
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']  # These are set automatically by the database
    
    def create(self, validated_data):
        # Set prepared_by from request user
        validated_data['prepared_by'] = self.context['request'].user
        return super().create(validated_data)
