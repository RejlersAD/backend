"""Validated inputs for a project brief and its editable setup preview."""
from datetime import date
from rest_framework import serializers

from .work_breakdown_serializers import WorkBreakdownSaveSerializer, WorkBreakdownTaskSerializer


PROJECT_TYPES = [
    ('engineering', 'Engineering'), ('software', 'Software / IT'),
    ('internal', 'Internal / Department'), ('business', 'Business / Operations'),
]


class ProjectSetupAISettingsSerializer(serializers.Serializer):
    api_key = serializers.CharField(max_length=2048, write_only=True, required=False, trim_whitespace=True)
    model = serializers.RegexField(r'^[A-Za-z0-9][A-Za-z0-9._:-]*$', max_length=128)


class ProjectSetupBriefSerializer(serializers.Serializer):
    code = serializers.RegexField(r'^[A-Za-z0-9][A-Za-z0-9_. -]*$', max_length=50)
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(max_length=16000)
    project_type = serializers.ChoiceField(choices=PROJECT_TYPES)
    department = serializers.CharField(max_length=120, allow_blank=True, default='')
    phase = serializers.CharField(max_length=100)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    project_manager_id = serializers.IntegerField(min_value=1)
    team_member_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=100, default=list)
    generation_mode = serializers.ChoiceField(choices=['ai', 'template'], default='ai')

    def validate(self, data):
        if data['end_date'] <= data['start_date']:
            raise serializers.ValidationError({'end_date': 'Choose an end date after the start date.'})
        if (data['end_date'] - data['start_date']).days > 3660:
            raise serializers.ValidationError({'end_date': 'Limit this planning phase to ten years.'})
        data['team_member_ids'] = list(dict.fromkeys(data['team_member_ids']))
        return data


class SetupTaskSerializer(WorkBreakdownTaskSerializer):
    duration_days = serializers.IntegerField(min_value=1, max_value=3650)
    planned_start_date = serializers.DateField()
    due_date = serializers.DateField()


class SetupWorkstreamSerializer(serializers.Serializer):
    code = serializers.RegexField(r'^[A-Za-z0-9_-]+$', max_length=64)
    name = serializers.CharField(max_length=120)


class SetupMilestoneSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    target_date = serializers.DateField()


class ProjectSetupPlanSerializer(serializers.Serializer):
    project = serializers.DictField()
    tasks = SetupTaskSerializer(many=True, min_length=1, max_length=120)
    disciplines = SetupWorkstreamSerializer(many=True, min_length=1, max_length=50)
    milestones = SetupMilestoneSerializer(many=True, max_length=30)
    assumptions = serializers.ListField(child=serializers.CharField(max_length=2000), max_length=30)
    risks = serializers.ListField(child=serializers.CharField(max_length=2000), max_length=30)

    def validate_tasks(self, tasks):
        tasks = WorkBreakdownSaveSerializer().validate_tasks(tasks)
        for task in tasks:
            task['duration_days'] = int(task['duration_days'])
            task['planned_start_date'] = date.fromisoformat(task['planned_start_date'])
        return tasks

    def validate(self, data):
        brief = self.context['brief']
        groups = [item['code'] for item in data['disciplines']]
        if len(groups) != len(set(groups)):
            raise serializers.ValidationError({'disciplines': 'Each workstream needs a unique code.'})
        people = {*brief['team_member_ids'], brief['project_manager_id']}
        for task in data['tasks']:
            if task['discipline'] not in groups:
                raise serializers.ValidationError({'tasks': 'Every task must belong to a listed workstream.'})
            for field in ('assignee_id', 'reviewer_id'):
                if task.get(field) is not None and task[field] not in people:
                    raise serializers.ValidationError({'tasks': 'Assign only employees selected in the project brief.'})
            if task.get('assignee_id') and task.get('assignee_id') == task.get('reviewer_id'):
                raise serializers.ValidationError({'tasks': 'Choose a reviewer other than the task assignee.'})
            if task['planned_start_date'] < brief['start_date'] or task['due_date'] < task['planned_start_date']:
                raise serializers.ValidationError({'tasks': 'Task dates must start within the project and finish after their start.'})
            if (task['due_date'] - brief['start_date']).days > 3660:
                raise serializers.ValidationError({'tasks': 'Task dates exceed the ten-year planning limit.'})
        for milestone in data['milestones']:
            if not brief['start_date'] <= milestone['target_date'] <= brief['end_date']:
                raise serializers.ValidationError({'milestones': 'Milestone targets must fall within the project dates.'})
        scope = serializers.CharField(max_length=16000).run_validation(data['project'].get('scope_summary'))
        exclusions = serializers.CharField(max_length=16000, allow_blank=True).run_validation(data['project'].get('exclusions', ''))
        # Project identity, dates and selected employees come from the signed brief.
        data['project'] = {**brief, 'scope_summary': scope, 'exclusions': exclusions, 'planning_mode': 'manual'}
        return data


class ProjectSetupCreateSerializer(serializers.Serializer):
    preview_token = serializers.CharField(max_length=250000)
    plan = serializers.JSONField(required=False)
