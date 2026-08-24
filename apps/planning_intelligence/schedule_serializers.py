"""API serializers for the relational scheduling engine."""
from rest_framework import serializers

from .access import can_write_project
from .models import (
    ActivityAssignment, ActivityProgressUpdate, ActivityRelationship, CalendarException, Schedule,
    ScheduleActivity, ScheduleBaseline, ScheduleCalculationRun, ScheduleResource,
    ScheduleControlSnapshot, ScheduleVersion, ScheduleWBSNode, WorkCalendar,
)


def _project_for_version(version):
    return version.schedule.project


def _validate_mutable_version(version):
    if version.status in {'approved', 'baselined', 'superseded'}:
        raise serializers.ValidationError('Approved, baselined, and superseded schedule versions are immutable.')


def _validate_version_access(serializer, version):
    request = serializer.context.get('request')
    if request and not can_write_project(request.user, _project_for_version(version)):
        raise serializers.ValidationError('You cannot modify this schedule version.')


class CalendarExceptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalendarException
        fields = '__all__'
        read_only_fields = ['id', 'is_deleted', 'deleted_at', 'created_at', 'updated_at']

    def validate_calendar(self, value):
        request = self.context.get('request')
        if request and not can_write_project(request.user, value.project):
            raise serializers.ValidationError('You cannot modify this project calendar.')
        return value


class WorkCalendarSerializer(serializers.ModelSerializer):
    exceptions = serializers.SerializerMethodField()

    class Meta:
        model = WorkCalendar
        fields = [
            'id', 'project', 'name', 'working_weekdays', 'hours_per_day', 'timezone',
            'is_default', 'exceptions', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_working_weekdays(self, value):
        if not value or any(not isinstance(day, int) or day < 0 or day > 6 for day in value):
            raise serializers.ValidationError('Provide one or more unique weekday integers from 0 (Monday) to 6 (Sunday).')
        if len(value) != len(set(value)):
            raise serializers.ValidationError('Working weekdays must be unique.')
        return sorted(value)

    def get_exceptions(self, obj):
        return CalendarExceptionSerializer(obj.exceptions.filter(is_deleted=False), many=True).data

    def validate_project(self, value):
        request = self.context.get('request')
        if request and not can_write_project(request.user, value):
            raise serializers.ValidationError('You cannot modify calendars for this project.')
        return value


class ScheduleSerializer(serializers.ModelSerializer):
    version_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Schedule
        fields = [
            'id', 'project', 'name', 'code', 'status', 'planned_start', 'data_date',
            'default_calendar', 'created_by', 'version_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'version_count', 'created_at', 'updated_at']

    def validate(self, attrs):
        project = attrs.get('project', getattr(self.instance, 'project', None))
        calendar = attrs.get('default_calendar', getattr(self.instance, 'default_calendar', None))
        request = self.context.get('request')
        if project and request and not can_write_project(request.user, project):
            raise serializers.ValidationError({'project': 'You cannot modify schedules for this project.'})
        if calendar and project and calendar.project_id != project.id:
            raise serializers.ValidationError({'default_calendar': 'Calendar must belong to the schedule project.'})
        return attrs


class ScheduleVersionSerializer(serializers.ModelSerializer):
    activity_count = serializers.IntegerField(read_only=True)
    relationship_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ScheduleVersion
        fields = [
            'id', 'schedule', 'version', 'status', 'parent_version', 'source_generation',
            'change_summary', 'calculated_at', 'calculated_finish', 'created_by',
            'activity_count', 'relationship_count', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ScheduleWBSNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleWBSNode
        fields = [
            'id', 'version', 'parent', 'code', 'name', 'level', 'sort_order',
            'discipline', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        version = attrs.get('version', getattr(self.instance, 'version', None))
        parent = attrs.get('parent', getattr(self.instance, 'parent', None))
        _validate_mutable_version(version)
        _validate_version_access(self, version)
        if parent and parent.version_id != version.id:
            raise serializers.ValidationError({'parent': 'Parent WBS node must belong to the same version.'})
        return attrs


class ScheduleActivitySerializer(serializers.ModelSerializer):
    is_milestone = serializers.BooleanField(read_only=True)

    class Meta:
        model = ScheduleActivity
        fields = [
            'id', 'version', 'wbs_node', 'calendar', 'external_id', 'name',
            'activity_type', 'is_milestone', 'duration_days', 'discipline',
            'responsible_role', 'constraint_type', 'constraint_date', 'planned_start',
            'planned_finish', 'early_start', 'early_finish', 'late_start', 'late_finish',
            'total_float_days', 'free_float_days', 'is_critical', 'sort_order',
            'metadata', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'planned_start', 'planned_finish', 'early_start', 'early_finish',
            'late_start', 'late_finish', 'total_float_days', 'free_float_days',
            'is_critical', 'created_at', 'updated_at',
        ]

    def validate(self, attrs):
        version = attrs.get('version', getattr(self.instance, 'version', None))
        wbs = attrs.get('wbs_node', getattr(self.instance, 'wbs_node', None))
        calendar = attrs.get('calendar', getattr(self.instance, 'calendar', None))
        constraint_type = attrs.get('constraint_type', getattr(self.instance, 'constraint_type', 'none'))
        constraint_date = attrs.get('constraint_date', getattr(self.instance, 'constraint_date', None))
        _validate_mutable_version(version)
        _validate_version_access(self, version)
        if wbs and wbs.version_id != version.id:
            raise serializers.ValidationError({'wbs_node': 'WBS node must belong to the same version.'})
        if calendar and calendar.project_id != _project_for_version(version).id:
            raise serializers.ValidationError({'calendar': 'Calendar must belong to the schedule project.'})
        if constraint_type != 'none' and not constraint_date:
            raise serializers.ValidationError({'constraint_date': 'A date is required for this constraint.'})
        return attrs


class ActivityRelationshipSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityRelationship
        fields = [
            'id', 'version', 'predecessor', 'successor', 'relationship_type',
            'lag_days', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        version = attrs.get('version', getattr(self.instance, 'version', None))
        predecessor = attrs.get('predecessor', getattr(self.instance, 'predecessor', None))
        successor = attrs.get('successor', getattr(self.instance, 'successor', None))
        _validate_mutable_version(version)
        _validate_version_access(self, version)
        if predecessor == successor:
            raise serializers.ValidationError('An activity cannot depend on itself.')
        if predecessor.version_id != version.id or successor.version_id != version.id:
            raise serializers.ValidationError('Both activities must belong to the relationship version.')
        return attrs


class ScheduleResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleResource
        fields = '__all__'
        read_only_fields = ['id', 'is_deleted', 'deleted_at', 'created_at', 'updated_at']

    def validate_project(self, value):
        request = self.context.get('request')
        if request and not can_write_project(request.user, value):
            raise serializers.ValidationError('You cannot modify resources for this project.')
        return value


class ActivityAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityAssignment
        fields = '__all__'
        read_only_fields = ['id', 'is_deleted', 'deleted_at', 'created_at', 'updated_at']
        validators = []

    def validate(self, attrs):
        activity = attrs.get('activity', getattr(self.instance, 'activity', None))
        resource = attrs.get('resource', getattr(self.instance, 'resource', None))
        _validate_mutable_version(activity.version)
        _validate_version_access(self, activity.version)
        if resource.project_id != _project_for_version(activity.version).id:
            raise serializers.ValidationError('Resource and activity must belong to the same project.')
        duplicate = ActivityAssignment.objects.filter(
            activity=activity, resource=resource, is_deleted=False,
        )
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError('This resource is already assigned to the activity.')
        return attrs


class ScheduleBaselineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleBaseline
        fields = '__all__'
        read_only_fields = [field.name for field in ScheduleBaseline._meta.fields]


class ScheduleCalculationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleCalculationRun
        fields = '__all__'
        read_only_fields = [field.name for field in ScheduleCalculationRun._meta.fields]


class BulkActivityEditSerializer(serializers.Serializer):
    expected_updated_at = serializers.DateTimeField()
    activities = serializers.ListField(child=serializers.DictField(), min_length=1, max_length=500)

    def validate_activities(self, value):
        allowed = {
            'id', 'wbs_node', 'calendar', 'external_id', 'name', 'activity_type',
            'duration_days', 'discipline', 'responsible_role', 'constraint_type',
            'constraint_date', 'sort_order', 'metadata',
        }
        for index, row in enumerate(value):
            if not row.get('id'):
                raise serializers.ValidationError(f'Row {index + 1} requires an activity id.')
            unknown = set(row) - allowed
            if unknown:
                raise serializers.ValidationError(
                    f'Row {index + 1} contains unsupported fields: {", ".join(sorted(unknown))}.'
                )
        identifiers = [row['id'] for row in value]
        if len(identifiers) != len(set(identifiers)):
            raise serializers.ValidationError('Each activity may appear only once per batch.')
        return value


class ActivityProgressUpdateSerializer(serializers.ModelSerializer):
    external_id = serializers.CharField(source='activity.external_id', read_only=True)
    activity_name = serializers.CharField(source='activity.name', read_only=True)

    class Meta:
        model = ActivityProgressUpdate
        fields = [
            'id', 'version', 'activity', 'external_id', 'activity_name', 'data_date',
            'physical_progress_pct', 'remaining_duration_days', 'actual_start',
            'actual_finish', 'forecast_finish', 'actual_hours', 'actual_cost',
            'notes', 'reported_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ActivityProgressRowSerializer(serializers.Serializer):
    activity = serializers.IntegerField(min_value=1)
    physical_progress_pct = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100)
    remaining_duration_days = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False, allow_null=True,
    )
    actual_start = serializers.DateField(required=False, allow_null=True)
    actual_finish = serializers.DateField(required=False, allow_null=True)
    forecast_finish = serializers.DateField(required=False, allow_null=True)
    actual_hours = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, default=0)
    actual_cost = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, default=0)
    notes = serializers.CharField(required=False, allow_blank=True, default='', max_length=4000)

    def validate(self, attrs):
        if attrs.get('actual_start') and attrs.get('actual_finish') and attrs['actual_finish'] < attrs['actual_start']:
            raise serializers.ValidationError('Actual finish cannot be earlier than actual start.')
        if attrs.get('actual_finish') and attrs['physical_progress_pct'] < 100:
            raise serializers.ValidationError('An activity with an actual finish must be 100% complete.')
        return attrs


class BulkProgressUpdateSerializer(serializers.Serializer):
    data_date = serializers.DateField()
    updates = serializers.ListField(child=ActivityProgressRowSerializer(), min_length=1, max_length=500)

    def validate_updates(self, value):
        identifiers = [row['activity'] for row in value]
        if len(identifiers) != len(set(identifiers)):
            raise serializers.ValidationError('Each activity may appear only once per progress update.')
        return value


class ControlDateSerializer(serializers.Serializer):
    data_date = serializers.DateField(required=False)


class ScheduleControlSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleControlSnapshot
        fields = '__all__'
        read_only_fields = [field.name for field in ScheduleControlSnapshot._meta.fields]
