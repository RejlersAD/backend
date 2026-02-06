from rest_framework import serializers
from django.conf import settings
from django.contrib.auth import get_user_model
from .models import ElectricalEquipmentType, ElectricalDatasheet, DatasheetRevisionHistory, DatasheetComment

User = get_user_model()


class ElectricalEquipmentTypeSerializer(serializers.ModelSerializer):
    """Serializer for ElectricalEquipmentType model"""
    
    class Meta:
        model = ElectricalEquipmentType
        fields = [
            'id', 'name', 'code', 'description', 'icon', 'category',
            'standards', 'sections', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']


class UserSerializer(serializers.ModelSerializer):
    """Simple User serializer for nested representations"""
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name']
    
    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class UserSerializer(serializers.ModelSerializer):
    """Simple User serializer for nested representations"""
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name']
    
    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class DatasheetCommentSerializer(serializers.ModelSerializer):
    """Serializer for DatasheetComment model"""
    commented_by = UserSerializer(read_only=True)
    replies = serializers.SerializerMethodField()
    
    class Meta:
        model = DatasheetComment
        fields = [
            'id', 'datasheet', 'comment_text', 'field_id',
            'commented_by', 'commented_at', 'parent_comment',
            'is_resolved', 'replies'
        ]
        read_only_fields = ['commented_by', 'commented_at']
    
    def get_replies(self, obj):
        if obj.replies.exists():
            return DatasheetCommentSerializer(obj.replies.all(), many=True).data
        return []


class DatasheetRevisionHistorySerializer(serializers.ModelSerializer):
    """Serializer for DatasheetRevisionHistory model"""
    revised_by = UserSerializer(read_only=True)
    
    class Meta:
        model = DatasheetRevisionHistory
        fields = [
            'id', 'datasheet', 'revision_number', 'form_data',
            'status', 'revision_notes', 'revised_by', 'revised_at'
        ]
        read_only_fields = ['revised_by', 'revised_at']


class ElectricalDatasheetSerializer(serializers.ModelSerializer):
    """Serializer for ElectricalDatasheet model"""
    equipment_type_detail = ElectricalEquipmentTypeSerializer(source='equipment_type', read_only=True)
    created_by_detail = UserSerializer(source='created_by', read_only=True)
    updated_by_detail = UserSerializer(source='updated_by', read_only=True)
    reviewed_by_detail = UserSerializer(source='reviewed_by', read_only=True)
    approved_by_detail = UserSerializer(source='approved_by', read_only=True)
    comments_count = serializers.SerializerMethodField()
    
    class Meta:
        model = ElectricalDatasheet
        fields = [
            'id', 'equipment_type', 'equipment_type_detail', 'tag_number',
            'service_description', 'location', 'form_data', 'status',
            'revision_number', 'revision_notes', 'project_name', 'project_number',
            'discipline', 'attachments', 'created_by', 'created_by_detail',
            'updated_by', 'updated_by_detail', 'reviewed_by', 'reviewed_by_detail',
            'approved_by', 'approved_by_detail', 'created_at', 'updated_at',
            'reviewed_at', 'approved_at', 'is_deleted', 'comments_count'
        ]
        read_only_fields = [
            'created_by', 'updated_by', 'created_at', 'updated_at',
            'reviewed_at', 'approved_at', 'is_deleted'
        ]
    
    def get_comments_count(self, obj):
        return obj.comments.filter(parent_comment__isnull=True).count()
    
    def validate_tag_number(self, value):
        """Validate tag number format and uniqueness"""
        if self.instance:
            # If updating, exclude current instance from uniqueness check
            if ElectricalDatasheet.objects.filter(tag_number=value).exclude(id=self.instance.id).exists():
                raise serializers.ValidationError("This tag number already exists.")
        else:
            if ElectricalDatasheet.objects.filter(tag_number=value).exists():
                raise serializers.ValidationError("This tag number already exists.")
        return value
    
    def create(self, validated_data):
        """Override create to set created_by"""
        user = self.context['request'].user
        validated_data['created_by'] = user
        validated_data['updated_by'] = user
        return super().create(validated_data)
    
    def update(self, instance, validated_data):
        """Override update to set updated_by"""
        user = self.context['request'].user
        validated_data['updated_by'] = user
        return super().update(instance, validated_data)


class ElectricalDatasheetListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views"""
    equipment_type_name = serializers.CharField(source='equipment_type.name', read_only=True)
    equipment_type_code = serializers.CharField(source='equipment_type.code', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = ElectricalDatasheet
        fields = [
            'id', 'tag_number', 'service_description', 'location',
            'equipment_type', 'equipment_type_name', 'equipment_type_code',
            'status', 'revision_number', 'project_name', 'project_number',
            'created_by_name', 'updated_by_name', 'created_at', 'updated_at'
        ]
    
    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None
    
    def get_updated_by_name(self, obj):
        if obj.updated_by:
            return obj.updated_by.get_full_name() or obj.updated_by.username
        return None


class ElectricalDatasheetCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating datasheets with validation"""
    
    class Meta:
        model = ElectricalDatasheet
        fields = [
            'equipment_type', 'tag_number', 'service_description', 'location',
            'form_data', 'status', 'revision_notes', 'project_name',
            'project_number', 'attachments'
        ]
    
    def validate(self, data):
        """Validate required fields based on equipment type configuration"""
        equipment_type = data.get('equipment_type') or (self.instance.equipment_type if self.instance else None)
        
        if equipment_type and data.get('form_data'):
            form_data = data['form_data']
            
            # Get equipment type configuration
            sections = equipment_type.sections
            
            # Validate required fields
            for section in sections:
                for field in section.get('fields', []):
                    if field.get('required') and not form_data.get(field['id']):
                        raise serializers.ValidationError({
                            field['id']: f"{field['label']} is required."
                        })
                    
                    # Validate field patterns if specified
                    if field.get('validation') and form_data.get(field['id']):
                        import re
                        pattern = field['validation'].get('pattern')
                        if pattern and not re.match(pattern, str(form_data[field['id']])):
                            message = field['validation'].get('message', 'Invalid format')
                            raise serializers.ValidationError({
                                field['id']: message
                            })
        
        return data
