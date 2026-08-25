"""RADAI Project Planning Application — DRF serializers."""
import calendar
import datetime as dt
import math
import os
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from rest_framework import serializers

from .access import can_access_enterprise_project, can_write_project
from .config import MAX_FILE_BYTES
from .models import (
    PlanningAuditEvent, PlanningFile, PlanningGeneration, PlanningJob, PlanningProject,
)


ALLOWED_PLANNING_EXTENSIONS = {
    '.pdf', '.docx', '.xlsx', '.xlsm', '.csv', '.txt', '.md', '.xer',
    '.png', '.jpg', '.jpeg', '.tif', '.tiff',
}


def _json_safe(value):
    """Normalize legacy generation snapshots for strict JSON rendering.

    DRF deliberately rejects NaN and infinite numbers. Older planning
    snapshots can contain those values inside deeply nested generated data,
    which previously made the complete generation detail endpoint return an
    HTML 500 response. Keep the snapshot readable while representing invalid
    numeric results as null.
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, str):
        return value.encode('utf-8', errors='replace').decode('utf-8')
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _add_calendar_months(value, months):
    """Shift a date by whole months and clamp month-end dates."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _decimal_calendar_months(start, end):
    """Return whole calendar months plus the exact fraction of the next month."""
    whole_months = (end.year - start.year) * 12 + end.month - start.month
    anchor = _add_calendar_months(start, whole_months)
    if anchor > end:
        whole_months -= 1
        anchor = _add_calendar_months(start, whole_months)
    next_anchor = _add_calendar_months(start, whole_months + 1)
    fraction = Decimal((end - anchor).days) / Decimal((next_anchor - anchor).days)
    return (Decimal(whole_months) + fraction).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


class PlanningFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningFile
        fields = [
            'id', 'project', 'category', 'file', 'original_filename', 'content_type',
            'size_bytes', 'parse_status', 'extracted_text', 'confidence_score',
            'parse_error', 'uploaded_by', 'created_at',
        ]
        read_only_fields = [
            'id', 'original_filename', 'content_type', 'size_bytes', 'parse_status',
            'extracted_text', 'confidence_score', 'parse_error', 'uploaded_by', 'created_at',
        ]

    def validate_file(self, value):
        if value.size > MAX_FILE_BYTES:
            raise serializers.ValidationError(f'File exceeds the {MAX_FILE_BYTES} byte limit.')
        extension = os.path.splitext(value.name or '')[1].lower()
        if extension not in ALLOWED_PLANNING_EXTENSIONS:
            allowed = ', '.join(sorted(ALLOWED_PLANNING_EXTENSIONS))
            raise serializers.ValidationError(f'Unsupported file type. Allowed extensions: {allowed}.')
        return value

    def validate_project(self, value):
        request = self.context.get('request')
        if request and not can_write_project(request.user, value):
            raise serializers.ValidationError('You cannot upload documents to this planning workspace.')
        return value


class PlanningFileListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningFile
        fields = [
            'id', 'project', 'category', 'file', 'original_filename', 'size_bytes',
            'parse_status', 'confidence_score', 'created_at',
        ]


class PlanningProjectSerializer(serializers.ModelSerializer):
    file_count = serializers.SerializerMethodField()
    latest_generation_version = serializers.SerializerMethodField()
    ai_enabled = serializers.SerializerMethodField()
    ai_provider = serializers.SerializerMethodField()
    ai_model = serializers.SerializerMethodField()
    ai_key_configured = serializers.SerializerMethodField()
    duration_days = serializers.SerializerMethodField()

    class Meta:
        model = PlanningProject
        fields = [
            'id', 'enterprise_project', 'name', 'client', 'location', 'phase', 'effective_date',
            'planned_end_date', 'duration_days', 'duration_months',
            'calendar_overrides', 'review_cycle_overrides',
            'created_by', 'file_count', 'latest_generation_version',
            'ai_enabled', 'ai_provider', 'ai_model', 'ai_key_configured',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']
        # NOTE: `ai_settings` (which holds the encrypted BYOK key) is
        # deliberately NOT included in `fields` above — it must never be
        # serialized to the API. Use the dedicated ai-settings action
        # (views.PlanningProjectViewSet.ai_settings) to read/write it.

    def validate(self, attrs):
        attrs = super().validate(attrs)
        start = attrs.get('effective_date', getattr(self.instance, 'effective_date', None))
        end = attrs.get('planned_end_date', getattr(self.instance, 'planned_end_date', None))
        if end and not start:
            raise serializers.ValidationError({
                'effective_date': 'Project start date is required when an end date is selected.',
            })
        if start and end:
            if end <= start:
                raise serializers.ValidationError({
                    'planned_end_date': 'Project end date must be after the start date.',
                })
            attrs['duration_months'] = _decimal_calendar_months(start, end)
        return attrs

    def get_duration_days(self, obj):
        if obj.effective_date and obj.planned_end_date:
            return (obj.planned_end_date - obj.effective_date).days
        return None

    def get_latest_generation_version(self, obj):
        annotated = getattr(obj, 'latest_generation_version_value', None)
        if annotated is not None:
            return annotated
        return obj.generations.filter(is_deleted=False).order_by('-version').values_list('version', flat=True).first()

    def get_file_count(self, obj):
        annotated = getattr(obj, 'active_file_count', None)
        if annotated is not None:
            return annotated
        return obj.files.filter(is_deleted=False).count()

    def get_ai_enabled(self, obj):
        return bool((obj.ai_settings or {}).get('enabled'))

    def get_ai_provider(self, obj):
        return (obj.ai_settings or {}).get('provider') or None

    def get_ai_model(self, obj):
        return (obj.ai_settings or {}).get('model') or None

    def get_ai_key_configured(self, obj):
        return bool((obj.ai_settings or {}).get('api_key_encrypted'))

    def validate_enterprise_project(self, value):
        request = self.context.get('request')
        if request and value and not can_access_enterprise_project(request.user, value, write=True):
            raise serializers.ValidationError('You cannot create a planning workspace for this enterprise project.')
        return value


class PlanningGenerationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningGeneration
        fields = ['id', 'project', 'version', 'parent_generation', 'change_summary', 'created_at']


class PlanningGenerationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningGeneration
        fields = [
            'id', 'project', 'version', 'parent_generation', 'change_summary',
            'intelligence', 'wbs', 'activities',
            'logic_matrix', 'eddr', 'milestones', 'manhours', 'validation',
            'narrative', 'generated_by', 'created_at',
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        return _json_safe(super().to_representation(instance))


class PlanningGenerationEditSerializer(serializers.Serializer):
    wbs = serializers.ListField(required=False)
    activities = serializers.ListField(required=False)
    eddr = serializers.ListField(required=False)
    manhours = serializers.DictField(required=False)
    milestones = serializers.ListField(required=False)
    narrative = serializers.CharField(required=False, allow_blank=True, max_length=100000)
    change_summary = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate_activities(self, value):
        if not all(isinstance(row, dict) for row in value):
            raise serializers.ValidationError('Every activity must be an object.')
        identifiers = [str(row.get('id', '')).strip() for row in value]
        if any(not identifier for identifier in identifiers):
            raise serializers.ValidationError('Every activity requires a non-empty id.')
        if len(identifiers) != len(set(identifiers)):
            raise serializers.ValidationError('Activity ids must be unique.')
        return value

    def validate_wbs(self, value):
        if not all(isinstance(row, dict) and row.get('code') and row.get('name') for row in value):
            raise serializers.ValidationError('Every WBS node requires code and name.')
        codes = [str(row['code']) for row in value]
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError('WBS codes must be unique.')
        return value


class PlanningJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningJob
        fields = [
            'id', 'project', 'job_type', 'status', 'progress', 'message',
            'result_data', 'result_generation', 'error_code', 'error_message',
            'requested_by', 'started_at', 'finished_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        return _json_safe(super().to_representation(instance))


class PlanningAuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningAuditEvent
        fields = [
            'id', 'project', 'actor', 'action', 'entity_type', 'entity_id',
            'before', 'after', 'metadata', 'created_at',
        ]
        read_only_fields = fields
