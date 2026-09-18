"""Editable, dependency-safe work breakdown draft input."""
from collections import deque

from rest_framework import serializers


class WorkBreakdownTaskSerializer(serializers.Serializer):
    id = serializers.RegexField(r'^[A-Za-z0-9_-]+$', max_length=64)
    discipline = serializers.RegexField(r'^[A-Za-z0-9_-]+$', max_length=64)
    title = serializers.CharField(max_length=500)
    owner = serializers.CharField(max_length=120, allow_blank=True, default='')
    effort_hours = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, allow_null=True, default=None,
    )
    depends_on = serializers.ListField(
        child=serializers.CharField(max_length=64), max_length=2000, default=list,
    )
    acceptance_criteria = serializers.CharField(max_length=10000, allow_blank=True, default='')
    reviewer = serializers.CharField(max_length=120, allow_blank=True, default='')
    assignee_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    reviewer_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    task_type = serializers.ChoiceField(choices=['task', 'deliverable'], required=False)
    due_date = serializers.DateField(allow_null=True, required=False)
    priority = serializers.ChoiceField(choices=['low', 'medium', 'high', 'critical'], required=False)
    status = serializers.CharField(read_only=True)
    progress_percent = serializers.IntegerField(read_only=True)
    source_references = serializers.ListField(read_only=True)
    duration_days = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, allow_null=True, required=False,
    )
    planned_start_date = serializers.DateField(allow_null=True, required=False)


class WorkBreakdownSaveSerializer(serializers.Serializer):
    intelligence_run_id = serializers.IntegerField(min_value=1)
    preview_confirmed_at = serializers.CharField(max_length=64)
    revision = serializers.IntegerField(min_value=0)
    tasks = WorkBreakdownTaskSerializer(many=True, max_length=2000)
    advance = serializers.BooleanField(default=False)

    def validate_tasks(self, tasks):
        ids = [task['id'] for task in tasks]
        if len(set(ids)) != len(ids):
            raise serializers.ValidationError('Each task must have a unique ID.')
        by_id = {task['id']: task for task in tasks}
        successors = {key: [] for key in ids}
        incoming = {}
        for task in tasks:
            dependencies = task['depends_on']
            if len(set(dependencies)) != len(dependencies):
                raise serializers.ValidationError('A task cannot repeat a dependency.')
            if task['id'] in dependencies:
                raise serializers.ValidationError('A task cannot depend on itself.')
            if set(dependencies) - set(by_id):
                raise serializers.ValidationError('Dependencies must refer to tasks in this work breakdown.')
            incoming[task['id']] = len(dependencies)
            for predecessor in dependencies:
                successors[predecessor].append(task['id'])
            if task['effort_hours'] is not None:
                task['effort_hours'] = float(task['effort_hours'])
            if task.get('duration_days') is not None:
                task['duration_days'] = float(task['duration_days'])
            if task.get('planned_start_date') is not None:
                task['planned_start_date'] = task['planned_start_date'].isoformat()
        queue = deque(key for key in ids if incoming[key] == 0)
        visited = 0
        while queue:
            key = queue.popleft()
            visited += 1
            for successor in successors[key]:
                incoming[successor] -= 1
                if incoming[successor] == 0:
                    queue.append(successor)
        if visited != len(ids):
            raise serializers.ValidationError('Dependencies must not form a circular chain.')
        return tasks


class WorkBreakdownDisciplineSerializer(serializers.Serializer):
    code = serializers.RegexField(r'^[A-Za-z0-9_-]+$', max_length=64)
    name = serializers.CharField(max_length=120)


class ManualWorkBreakdownSaveSerializer(WorkBreakdownSaveSerializer):
    intelligence_run_id = None
    preview_confirmed_at = None
    disciplines = WorkBreakdownDisciplineSerializer(many=True, max_length=100, required=False)

    def validate_disciplines(self, values):
        codes = [value['code'] for value in values]
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError('Each workstream must have a unique code.')
        return values
