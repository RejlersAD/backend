from rest_framework import serializers

from .models import CllProject, CllProjectData


class CllProjectSerializer(serializers.ModelSerializer):
    summary = serializers.SerializerMethodField()

    class Meta:
        model = CllProject
        fields = [
            'id', 'name', 'unit', 'client', 'description', 'storage',
            'created_at', 'updated_at', 'summary',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_summary(self, obj):
        data = getattr(obj, 'data', None)
        return {
            'hasData': bool(data and data.payload),
            'updatedAt': data.updated_at.isoformat() if data else None,
        }


class CllProjectDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = CllProjectData
        fields = ['payload', 'updated_at']
