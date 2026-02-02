"""
DesignIQ Serializers - API Data Serialization
"""

from rest_framework import serializers
from .models import DesignProject, DesignAnalysis, DesignOptimization, DesignTemplate, EngineeringListItem, LIST_TYPES


class DesignAnalysisSerializer(serializers.ModelSerializer):
    """Serializer for Design Analysis"""
    
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    resolved_by_name = serializers.CharField(source='resolved_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = DesignAnalysis
        fields = [
            'id', 'project', 'analysis_type', 'title', 'description', 'severity',
            'severity_display', 'ai_finding', 'ai_recommendation', 'ai_confidence',
            'standard_reference', 'code_section', 'is_resolved', 'resolved_by',
            'resolved_by_name', 'resolved_at', 'resolution_notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class DesignOptimizationSerializer(serializers.ModelSerializer):
    """Serializer for Design Optimization"""
    
    impact_display = serializers.CharField(source='get_impact_display', read_only=True)
    implemented_by_name = serializers.CharField(source='implemented_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = DesignOptimization
        fields = [
            'id', 'project', 'category', 'title', 'description', 'impact', 'impact_display',
            'estimated_cost_savings', 'estimated_efficiency_gain', 'implementation_effort',
            'implementation_notes', 'is_implemented', 'implemented_by', 'implemented_by_name',
            'implemented_at', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class DesignProjectListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing projects"""
    
    design_type_display = serializers.CharField(source='get_design_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    analyses_count = serializers.IntegerField(source='analyses.count', read_only=True)
    optimizations_count = serializers.IntegerField(source='optimizations.count', read_only=True)
    
    class Meta:
        model = DesignProject
        fields = [
            'id', 'project_name', 'design_type', 'design_type_display', 'description',
            'status', 'status_display', 'created_by', 'created_by_name', 'organization',
            'ai_confidence_score', 'analyses_count', 'optimizations_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class DesignProjectDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for single project with all related data"""
    
    design_type_display = serializers.CharField(source='get_design_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    analyses = DesignAnalysisSerializer(many=True, read_only=True)
    optimizations = DesignOptimizationSerializer(many=True, read_only=True)
    
    class Meta:
        model = DesignProject
        fields = [
            'id', 'project_name', 'design_type', 'design_type_display', 'description',
            'status', 'status_display', 'created_by', 'created_by_name', 'organization',
            'design_parameters', 'ai_analysis_results', 'ai_confidence_score',
            'ai_recommendations', 'input_file', 'output_file', 'processing_time',
            'error_message', 'analyses', 'optimizations', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class DesignProjectCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating new projects"""
    
    class Meta:
        model = DesignProject
        fields = [
            'id', 'project_name', 'design_type', 'description', 'organization',
            'design_parameters', 'input_file'
        ]
        read_only_fields = ['id']
    
    def create(self, validated_data):
        """Set created_by from request user"""
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['created_by'] = request.user
        return super().create(validated_data)


class DesignTemplateSerializer(serializers.ModelSerializer):
    """Serializer for Design Templates"""
    
    design_type_display = serializers.CharField(source='get_design_type_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = DesignTemplate
        fields = [
            'id', 'name', 'design_type', 'design_type_display', 'description',
            'template_data', 'parameters_schema', 'is_public', 'created_by',
            'created_by_name', 'usage_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'usage_count', 'created_at', 'updated_at']


class DesignAnalysisCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating analyses"""
    
    class Meta:
        model = DesignAnalysis
        fields = [
            'project', 'analysis_type', 'title', 'description', 'severity',
            'ai_finding', 'ai_recommendation', 'ai_confidence',
            'standard_reference', 'code_section'
        ]
    
    def validate_project(self, value):
        """Ensure user has access to the project"""
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            if value.created_by != request.user and not request.user.is_staff:
                raise serializers.ValidationError("You don't have access to this project")
        return value


class EngineeringListItemSerializer(serializers.ModelSerializer):
    """Serializer for Engineering List Items (Line List, Equipment List, etc.)"""
    
    list_type_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    updated_by_name = serializers.CharField(source='updated_by.get_full_name', read_only=True, allow_null=True)
    validated_by_name = serializers.CharField(source='validated_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = EngineeringListItem
        fields = [
            'id', 'project', 'list_type', 'list_type_display', 'item_tag', 'description',
            'status', 'status_display', 'data', 'created_by', 'created_by_name',
            'updated_by', 'updated_by_name', 'notes', 'attachments', 'is_validated',
            'validation_notes', 'validated_at', 'validated_by', 'validated_by_name',
            'version', 'revision_history', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'version', 'revision_history']
    
    def get_list_type_display(self, obj):
        """Get human-readable list type name"""
        return LIST_TYPES.get(obj.list_type, {}).get('name', obj.list_type)
    
    def create(self, validated_data):
        """Set created_by from request user"""
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)
    
    def update(self, instance, validated_data):
        """Set updated_by and track version"""
        user = self.context['request'].user
        validated_data['updated_by'] = user
        
        # Track changes if significant fields changed
        if 'data' in validated_data and validated_data['data'] != instance.data:
            instance.increment_version(user, notes='Data updated via API')
        
        return super().update(instance, validated_data)


class EngineeringListItemListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing items"""
    
    list_type_display = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = EngineeringListItem
        fields = [
            'id', 'project', 'list_type', 'list_type_display', 'item_tag',
            'description', 'status', 'status_display', 'data', 'created_by_name',
            'is_validated', 'version', 'created_at', 'updated_at'
        ]
    
    def get_list_type_display(self, obj):
        return LIST_TYPES.get(obj.list_type, {}).get('name', obj.list_type)


class ListTypeConfigSerializer(serializers.Serializer):
    """Serializer for list type configuration"""
    
    code = serializers.CharField()
    name = serializers.CharField()
    icon = serializers.CharField()
    description = serializers.CharField()
    default_fields = serializers.ListField(child=serializers.CharField())

