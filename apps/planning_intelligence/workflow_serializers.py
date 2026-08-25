"""API serializers for versioned workflow and engineering-logic configuration."""
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from .access import can_final_approve_defaults, can_write_project
from .models import (
    EngineeringDependencyRule, EngineeringDependencyTemplate, ProjectScheduleConfiguration,
    ScheduleDefaultProposal, WorkflowStage, WorkflowTemplate, WorkflowTemplateOverride,
)


def _validate_template_scope(request, template, project, label):
    if template.project_id not in (None, project.id):
        raise serializers.ValidationError({label: 'Template must be a system template or belong to this project.'})
    if template.status != 'active':
        raise serializers.ValidationError({label: 'Only an active template can be selected.'})


class WorkflowStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowStage
        fields = [
            'id', 'sequence', 'code', 'name', 'activity_name_template', 'duration_days',
            'responsible_party', 'activity_type', 'relationship_to_previous', 'lag_days',
            'progress_weight', 'is_release_gate',
        ]
        read_only_fields = ['id']

    def validate_code(self, value):
        value = value.strip().upper().replace(' ', '_')
        if not value:
            raise serializers.ValidationError('Stage code is required.')
        return value

    def validate_activity_name_template(self, value):
        if '{deliverable}' not in value:
            raise serializers.ValidationError('Activity name template must contain {deliverable}.')
        unsupported = set()
        for token in value.split('{')[1:]:
            key = token.split('}', 1)[0]
            if key and key not in {'deliverable', 'stage', 'discipline'}:
                unsupported.add(key)
        if unsupported:
            raise serializers.ValidationError(f'Unsupported placeholders: {", ".join(sorted(unsupported))}.')
        return value


class WorkflowTemplateSerializer(serializers.ModelSerializer):
    stages = WorkflowStageSerializer(many=True)
    stage_count = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowTemplate
        fields = [
            'id', 'project', 'code', 'name', 'description', 'version', 'status',
            'is_system', 'is_default', 'supersedes', 'created_by', 'stages',
            'stage_count', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'version', 'status', 'is_system', 'is_default', 'supersedes',
            'created_by', 'created_at', 'updated_at',
        ]

    def get_stage_count(self, obj):
        return obj.stages.filter(is_deleted=False).count()

    def validate_code(self, value):
        return value.strip().upper().replace(' ', '_')

    def validate_stages(self, value):
        if not value:
            raise serializers.ValidationError('At least one workflow stage is required.')
        sequences = [row['sequence'] for row in value]
        codes = [row['code'].strip().upper().replace(' ', '_') for row in value]
        if len(sequences) != len(set(sequences)):
            raise serializers.ValidationError('Stage sequences must be unique.')
        if sorted(sequences) != list(range(1, len(sequences) + 1)):
            raise serializers.ValidationError('Stage sequences must be consecutive and start at 1.')
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError('Stage codes must be unique.')
        if value[0].get('relationship_to_previous'):
            raise serializers.ValidationError('The first stage cannot have a predecessor relationship.')
        if any(not row.get('relationship_to_previous') for row in value[1:]):
            raise serializers.ValidationError('Every stage after the first requires a predecessor relationship.')
        weight = sum((Decimal(str(row.get('progress_weight') or 0)) for row in value), Decimal('0'))
        if abs(weight - Decimal('100')) > Decimal('0.01'):
            raise serializers.ValidationError('Workflow progress weights must total 100%.')
        return value

    def validate_project(self, value):
        request = self.context.get('request')
        if value is None:
            raise serializers.ValidationError('Project is required for custom templates.')
        if request and not can_write_project(request.user, value):
            raise serializers.ValidationError('You cannot create workflow templates for this project.')
        return value

    def validate(self, attrs):
        if self.instance:
            for field in ('project', 'code', 'version', 'supersedes'):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError({field: 'Create a new template revision instead of changing template identity.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        stages = validated_data.pop('stages')
        template = WorkflowTemplate.objects.create(**validated_data)
        WorkflowStage.objects.bulk_create([
            WorkflowStage(template=template, **row) for row in stages
        ])
        return template

    @transaction.atomic
    def update(self, instance, validated_data):
        stages = validated_data.pop('stages', None)
        instance = super().update(instance, validated_data)
        if stages is not None:
            instance.stages.all().delete()
            WorkflowStage.objects.bulk_create([
                WorkflowStage(template=instance, **row) for row in stages
            ])
        return instance


class EngineeringDependencyRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngineeringDependencyRule
        fields = [
            'id', 'sequence', 'predecessor_code', 'predecessor_name',
            'predecessor_stage_code', 'successor_code', 'successor_name',
            'successor_stage_code', 'relationship_type', 'lag_days', 'rationale',
            'source_reference', 'requires_confirmation',
        ]
        read_only_fields = ['id']

    def validate(self, attrs):
        predecessor = attrs.get('predecessor_code', getattr(self.instance, 'predecessor_code', '')).strip().upper()
        successor = attrs.get('successor_code', getattr(self.instance, 'successor_code', '')).strip().upper()
        if predecessor == successor:
            raise serializers.ValidationError('A dependency must connect two different deliverables.')
        attrs['predecessor_code'] = predecessor
        attrs['successor_code'] = successor
        for field in ('predecessor_stage_code', 'successor_stage_code'):
            if field in attrs:
                attrs[field] = attrs[field].strip().upper().replace(' ', '_')
        return attrs


class EngineeringDependencyTemplateSerializer(serializers.ModelSerializer):
    rules = EngineeringDependencyRuleSerializer(many=True)
    rule_count = serializers.SerializerMethodField()
    open_confirmation_count = serializers.SerializerMethodField()

    class Meta:
        model = EngineeringDependencyTemplate
        fields = [
            'id', 'project', 'code', 'name', 'discipline', 'description', 'version',
            'status', 'is_system', 'is_default', 'supersedes', 'created_by', 'rules',
            'rule_count', 'open_confirmation_count', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'version', 'status', 'is_system', 'is_default', 'supersedes',
            'created_by', 'created_at', 'updated_at',
        ]

    def get_rule_count(self, obj):
        return obj.rules.filter(is_deleted=False).count()

    def get_open_confirmation_count(self, obj):
        return obj.rules.filter(is_deleted=False, requires_confirmation=True).count()

    def validate_code(self, value):
        return value.strip().upper().replace(' ', '_')

    def validate_rules(self, value):
        if not value:
            raise serializers.ValidationError('At least one engineering dependency is required.')
        keys = []
        for row in value:
            keys.append((
                row['predecessor_code'].strip().upper(), row['predecessor_stage_code'].strip().upper(),
                row['successor_code'].strip().upper(), row['successor_stage_code'].strip().upper(),
                row['relationship_type'],
            ))
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError('Dependency release gates must be unique.')
        return value

    def validate_project(self, value):
        request = self.context.get('request')
        if value is None:
            raise serializers.ValidationError('Project is required for custom templates.')
        if request and not can_write_project(request.user, value):
            raise serializers.ValidationError('You cannot create dependency templates for this project.')
        return value

    def validate(self, attrs):
        if self.instance:
            for field in ('project', 'code', 'version', 'supersedes'):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError({field: 'Create a new template revision instead of changing template identity.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        rules = validated_data.pop('rules')
        template = EngineeringDependencyTemplate.objects.create(**validated_data)
        EngineeringDependencyRule.objects.bulk_create([
            EngineeringDependencyRule(template=template, **row) for row in rules
        ])
        return template

    @transaction.atomic
    def update(self, instance, validated_data):
        rules = validated_data.pop('rules', None)
        instance = super().update(instance, validated_data)
        if rules is not None:
            instance.rules.all().delete()
            EngineeringDependencyRule.objects.bulk_create([
                EngineeringDependencyRule(template=instance, **row) for row in rules
            ])
        return instance


class WorkflowTemplateOverrideSerializer(serializers.ModelSerializer):
    workflow_template_name = serializers.CharField(source='workflow_template.name', read_only=True)

    class Meta:
        model = WorkflowTemplateOverride
        fields = [
            'id', 'configuration', 'scope_type', 'scope_key', 'workflow_template',
            'workflow_template_name', 'priority', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_scope_key(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Discipline or deliverable key is required.')
        return value

    def validate(self, attrs):
        configuration = attrs.get('configuration', getattr(self.instance, 'configuration', None))
        template = attrs.get('workflow_template', getattr(self.instance, 'workflow_template', None))
        request = self.context.get('request')
        if configuration and request and not can_write_project(request.user, configuration.project):
            raise serializers.ValidationError('You cannot modify this project configuration.')
        if configuration and template:
            _validate_template_scope(request, template, configuration.project, 'workflow_template')
        return attrs


class ProjectScheduleConfigurationSerializer(serializers.ModelSerializer):
    overrides = WorkflowTemplateOverrideSerializer(many=True, read_only=True)
    workflow_stage_count = serializers.SerializerMethodField()
    dependency_rule_count = serializers.SerializerMethodField()

    class Meta:
        model = ProjectScheduleConfiguration
        fields = [
            'id', 'project', 'workflow_template', 'dependency_template',
            'standard_task_count', 'configuration_version', 'settings', 'updated_by',
            'workflow_stage_count', 'dependency_rule_count', 'overrides',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'configuration_version', 'updated_by', 'created_at', 'updated_at']

    def get_workflow_stage_count(self, obj):
        return obj.workflow_template.stages.filter(is_deleted=False).count()

    def get_dependency_rule_count(self, obj):
        if not obj.dependency_template_id:
            return 0
        return obj.dependency_template.rules.filter(is_deleted=False).count()

    def validate_project(self, value):
        request = self.context.get('request')
        if request and not can_write_project(request.user, value):
            raise serializers.ValidationError('You cannot configure this planning workspace.')
        return value

    def validate(self, attrs):
        project = attrs.get('project', getattr(self.instance, 'project', None))
        if self.instance and 'project' in attrs and attrs['project'] != self.instance.project:
            raise serializers.ValidationError({'project': 'A project configuration cannot be moved to another project.'})
        workflow = attrs.get('workflow_template', getattr(self.instance, 'workflow_template', None))
        dependency = attrs.get('dependency_template', getattr(self.instance, 'dependency_template', None))
        task_count = attrs.get('standard_task_count')
        if project and workflow:
            _validate_template_scope(self.context.get('request'), workflow, project, 'workflow_template')
            actual_count = workflow.stages.filter(is_deleted=False).count()
            if task_count is None:
                attrs['standard_task_count'] = actual_count
            elif task_count != actual_count:
                raise serializers.ValidationError({
                    'standard_task_count': f'Task count must match the selected template ({actual_count}).',
                })
        if project and dependency:
            _validate_template_scope(self.context.get('request'), dependency, project, 'dependency_template')
        settings = attrs.get('settings', getattr(self.instance, 'settings', {}) or {}) or {}
        confirmed = settings.get('confirmed_dependency_rule_ids', [])
        if not isinstance(confirmed, list) or any(not str(value).isdigit() for value in confirmed):
            raise serializers.ValidationError({
                'settings': 'confirmed_dependency_rule_ids must be a list of rule IDs.',
            })
        if dependency and confirmed:
            valid_rule_ids = set(dependency.rules.filter(is_deleted=False).values_list('id', flat=True))
            unknown = {int(value) for value in confirmed} - valid_rule_ids
            if unknown:
                raise serializers.ValidationError({
                    'settings': 'One or more confirmed dependency rules do not belong to the selected template.',
                })
        return attrs


class ScheduleDefaultProposalSerializer(serializers.ModelSerializer):
    proposed_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    can_approve = serializers.SerializerMethodField()
    tests_passed = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleDefaultProposal
        fields = [
            'id', 'project', 'configuration', 'title', 'rationale',
            'base_configuration_version', 'proposed_values', 'test_results',
            'tests_passed', 'status', 'proposed_by', 'proposed_by_name',
            'decided_by', 'decided_by_name', 'decided_at', 'decision_comment',
            'can_approve', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    @staticmethod
    def _name(user):
        if not user:
            return ''
        return user.get_full_name() or user.email or user.username

    def get_proposed_by_name(self, obj):
        return self._name(obj.proposed_by)

    def get_decided_by_name(self, obj):
        return self._name(obj.decided_by)

    def get_can_approve(self, obj):
        request = self.context.get('request')
        return bool(request and can_final_approve_defaults(request.user, obj.project))

    def get_tests_passed(self, obj):
        return bool(obj.test_results) and all(row.get('status') == 'passed' for row in obj.test_results)
