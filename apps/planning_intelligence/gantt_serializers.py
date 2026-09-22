"""Sparse Gantt commands: never trust a client-supplied complete schedule."""
from rest_framework import serializers
from .work_breakdown_serializers import PlanningTimingEditSerializer, WorkBreakdownDependencySerializer


class ActivityEditSerializer(serializers.Serializer):
    task_id = serializers.CharField(max_length=64)
    duration_days = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, max_value=36525,
                                             allow_null=True, required=False)
    timing_edit = PlanningTimingEditSerializer(required=False)
    dependency_details = WorkBreakdownDependencySerializer(many=True, max_length=2000, required=False)

    def validate(self, data):
        if len(data) == 1:
            raise serializers.ValidationError('Choose a duration, date or dependency change.')
        return data


class GanttEditSerializer(ActivityEditSerializer):
    task_id = serializers.CharField(max_length=64, required=False)
    revision = serializers.IntegerField(min_value=0)
    updates = ActivityEditSerializer(many=True, min_length=1, max_length=2000, required=False)

    def validate(self, data):
        if 'updates' in data:
            if set(data) != {'revision', 'updates'}:
                raise serializers.ValidationError('Use a single activity edit or an atomic list of edits.')
            ids = [row['task_id'] for row in data['updates']]
            if len(ids) != len(set(ids)):
                raise serializers.ValidationError('An activity may occur only once in a change.')
        elif not data.get('task_id') or not set(data) & {'duration_days', 'timing_edit', 'dependency_details'}:
            raise serializers.ValidationError('Choose an activity and a duration, date or dependency change.')
        return data
