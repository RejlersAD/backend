"""Serializers for integrations, export audit, and enterprise retention."""
from urllib.parse import urlparse

from rest_framework import serializers

from .models import IntegrationDelivery, IntegrationEndpoint, PlanningRetentionPolicy, ScheduleExportRecord


class IntegrationEndpointSerializer(serializers.ModelSerializer):
    secret = serializers.CharField(write_only=True, required=False, allow_blank=False, max_length=1000)
    secret_configured = serializers.SerializerMethodField()

    class Meta:
        model = IntegrationEndpoint
        fields = [
            'id', 'project', 'name', 'target_url', 'export_format', 'auth_type', 'secret',
            'secret_configured', 'event_types', 'is_active', 'timeout_seconds',
            'last_success_at', 'last_failure_at', 'last_error', 'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'secret_configured', 'last_success_at', 'last_failure_at', 'last_error',
            'created_by', 'created_at', 'updated_at',
        ]

    def get_secret_configured(self, obj):
        return bool(obj.secret_encrypted)

    def validate_target_url(self, value):
        parsed = urlparse(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise serializers.ValidationError('Use a credential-free HTTPS URL.')
        if parsed.port and parsed.port != 443:
            raise serializers.ValidationError('Use the standard HTTPS port.')
        return value

    def validate_timeout_seconds(self, value):
        if value < 3 or value > 60:
            raise serializers.ValidationError('Timeout must be between 3 and 60 seconds.')
        return value

    def validate(self, attrs):
        auth_type = attrs.get('auth_type', getattr(self.instance, 'auth_type', 'none'))
        if auth_type != 'none' and not attrs.get('secret') and not getattr(self.instance, 'secret_encrypted', ''):
            raise serializers.ValidationError({'secret': 'A credential is required for this authentication type.'})
        return attrs


class IntegrationDeliverySerializer(serializers.ModelSerializer):
    endpoint_name = serializers.CharField(source='endpoint.name', read_only=True)

    class Meta:
        model = IntegrationDelivery
        fields = '__all__'
        read_only_fields = [field.name for field in IntegrationDelivery._meta.fields] + ['endpoint_name']


class ScheduleExportRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleExportRecord
        fields = '__all__'
        read_only_fields = [field.name for field in ScheduleExportRecord._meta.fields]


class PlanningRetentionPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningRetentionPolicy
        fields = [
            'id', 'project', 'export_history_days', 'delivery_history_days',
            'completed_job_days', 'legal_hold', 'updated_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'project', 'updated_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        for field in ('export_history_days', 'delivery_history_days', 'completed_job_days'):
            value = attrs.get(field, getattr(self.instance, field, 0))
            if value < 30 or value > 3650:
                raise serializers.ValidationError({field: 'Retention must be between 30 and 3650 days.'})
        return attrs
