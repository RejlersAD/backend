from rest_framework import serializers
from .models import CrossToolRegistry


class CrossToolRegistrySerializer(serializers.ModelSerializer):
    uploaded_by_email = serializers.SerializerMethodField()

    class Meta:
        model = CrossToolRegistry
        fields = [
            'doc_type', 'doc_id', 'project_id', 'project_name',
            'file_name', 'status', 's3_path', 'registered_at',
            'uploaded_by_email',
        ]
        read_only_fields = fields

    def get_uploaded_by_email(self, obj):
        return obj.uploaded_by.email if obj.uploaded_by else None
