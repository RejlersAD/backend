"""Explicit command inputs; approval and provenance fields are server controlled."""
from rest_framework import serializers


class StrictCommandSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({key: 'Unknown or server-controlled field.' for key in set(data) - set(self.fields)})
        return super().to_internal_value(data)


class PlanningProfileInputSerializer(StrictCommandSerializer):
    revision = serializers.IntegerField(min_value=1, required=False)
    code = serializers.RegexField(r'^[A-Za-z0-9_.:-]+$', max_length=64, required=False, trim_whitespace=False)
    name = serializers.CharField(max_length=160, required=False)
    workflow_template_id = serializers.IntegerField(min_value=1, required=False)
    dependency_template_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    final_gate_label = serializers.CharField(max_length=120, required=False)
    approved_dependency_rule_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=2000, required=False)
    stage_duration_overrides = serializers.JSONField(required=False)
    wbs_convention = serializers.JSONField(required=False)
    calendar_policy = serializers.JSONField(required=False)
    progress_policy = serializers.JSONField(required=False)
    resource_policy = serializers.JSONField(required=False)


class PlanningProfileDecisionSerializer(StrictCommandSerializer):
    revision = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=4000)


class PlanningProfileSelectionSerializer(StrictCommandSerializer):
    profile_id = serializers.IntegerField(min_value=1)
    selection_revision = serializers.IntegerField(min_value=0)
    reason = serializers.CharField(max_length=4000)
