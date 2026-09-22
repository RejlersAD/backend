from rest_framework import serializers
from .operational_control_serializers import StrictSerializer


class DelayEvidenceSerializer(StrictSerializer):
    reference = serializers.CharField(max_length=4000)
    document_version_id = serializers.UUIDField(required=False, allow_null=True)
    fact_id = serializers.UUIDField(required=False, allow_null=True)


class DelayChangeSerializer(StrictSerializer):
    event_id = serializers.IntegerField(min_value=1)
    field = serializers.ChoiceField(choices=['remaining_duration_days', 'remaining_not_before', 'relationship'])
    activity_id = serializers.IntegerField(min_value=1, required=False)
    predecessor_id = serializers.IntegerField(min_value=1, required=False)
    successor_id = serializers.IntegerField(min_value=1, required=False)
    expected_before = serializers.JSONField(allow_null=True)
    value = serializers.JSONField(allow_null=True)
    evidence = serializers.CharField(max_length=4000)
    reason = serializers.CharField(max_length=4000)

    def validate(self, data):
        required = {'predecessor_id', 'successor_id'} if data['field'] == 'relationship' else {'activity_id'}
        if required - set(data):
            raise serializers.ValidationError({key: 'An exact baseline activity is required.' for key in required - set(data)})
        if (data['field'] == 'relationship' and 'activity_id' in data
                or data['field'] != 'relationship' and {'predecessor_id', 'successor_id'} & set(data)):
            raise serializers.ValidationError('Use either the changed activity or the exact relationship endpoints.')
        return data


class RecoveryScenarioSerializer(StrictSerializer):
    id = serializers.RegexField(r'^[A-Za-z0-9_-]{1,64}$')
    name = serializers.CharField(max_length=255)
    changes = DelayChangeSerializer(many=True, allow_empty=False, max_length=5000)


class DelayRecommendationSerializer(StrictSerializer):
    selected_scenario_id = serializers.CharField(max_length=64, allow_null=True, allow_blank=True, required=False)
    requested_extension_calendar_days = serializers.IntegerField(min_value=0, max_value=36600, allow_null=True, required=False)
    contract_clause_reference = serializers.CharField(max_length=4000, allow_blank=True, required=False)
    notice_reference = serializers.CharField(max_length=4000, allow_blank=True, required=False)
    causation_assessment = serializers.CharField(max_length=8000, allow_blank=True, required=False)
    concurrency_assessment = serializers.CharField(max_length=8000, allow_blank=True, required=False)
    mitigation_assessment = serializers.CharField(max_length=8000, allow_blank=True, required=False)
    basis = serializers.CharField(max_length=8000, allow_blank=True, required=False)


class DelayCommandSerializer(StrictSerializer):
    action = serializers.ChoiceField(choices=['create_event', 'update_event', 'create_case', 'save_case',
        'calculate_case', 'submit_case', 'approve_case', 'return_case', 'reject_case', 'revise_case'])
    baseline_id = serializers.IntegerField(min_value=1, required=False)
    event_id = serializers.IntegerField(min_value=1, required=False)
    case_id = serializers.IntegerField(min_value=1, required=False)
    revision = serializers.IntegerField(min_value=1, required=False)
    reference_report_id = serializers.IntegerField(min_value=1, required=False)
    title = serializers.CharField(max_length=255, required=False)
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(max_length=8000, allow_blank=True, required=False)
    start_date = serializers.DateField(allow_null=True, required=False)
    end_date = serializers.DateField(allow_null=True, required=False)
    status = serializers.ChoiceField(choices=['recorded', 'closed'], required=False)
    activity_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=20000, required=False)
    evidence = DelayEvidenceSerializer(many=True, allow_empty=False, max_length=50, required=False)
    governance_item_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    risk_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    event_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=1000, required=False)
    changes = DelayChangeSerializer(many=True, max_length=5000, required=False)
    scenarios = RecoveryScenarioSerializer(many=True, max_length=20, required=False)
    recommendation = DelayRecommendationSerializer(required=False)
    source_fingerprint = serializers.CharField(min_length=64, max_length=64, required=False)
    reason = serializers.CharField(max_length=4000, required=False, allow_blank=True)
