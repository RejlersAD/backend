"""Explicit, finite inputs; omitted quantities are never converted into zero."""
from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({key: 'Unknown field.' for key in set(data) - set(self.fields)})
        return super().to_internal_value(data)


def quantity(**kwargs):
    return serializers.DecimalField(max_digits=18, decimal_places=3, min_value=0,
                                    required=False, allow_null=True, **kwargs)


class PlannedValuePointSerializer(StrictSerializer):
    date = serializers.DateField()
    value = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=0)


class EarningActivitySerializer(StrictSerializer):
    activity_id = serializers.IntegerField(min_value=1)
    method = serializers.ChoiceField(choices=['manual_percent', 'zero_hundred', 'fifty_fifty', 'quantity'])
    weight = quantity()
    budget = quantity()
    planned_quantity = quantity()
    quantity_unit = serializers.CharField(max_length=32, required=False, allow_blank=True)
    pv_method = serializers.ChoiceField(choices=['not_specified', 'working_day_linear', 'explicit_points'], default='not_specified')
    planned_value = PlannedValuePointSerializer(many=True, required=False)


class EarningDefinitionSerializer(StrictSerializer):
    currency = serializers.RegexField(r'^[A-Z]{3}$', required=False, allow_null=True)
    activities = EarningActivitySerializer(many=True, allow_empty=False)


class OperationalObservationSerializer(StrictSerializer):
    activity_id = serializers.IntegerField(min_value=1)
    actual_start = serializers.DateField(required=False, allow_null=True)
    actual_finish = serializers.DateField(required=False, allow_null=True)
    physical_progress_pct = quantity(max_value=100)
    installed_quantity = quantity()
    remaining_duration_days = quantity()
    evidence = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=4000, required=False, allow_blank=True)


class OperationalPeriodSerializer(StrictSerializer):
    name = serializers.CharField(max_length=100)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    data_date = serializers.DateField()


class OperationalCommandSerializer(StrictSerializer):
    action = serializers.ChoiceField(choices=['create_policy', 'approve_policy', 'create_report', 'save_report',
        'submit_report', 'publish_report', 'return_report', 'correction_report'])
    baseline_id = serializers.IntegerField(min_value=1, required=False)
    policy_id = serializers.IntegerField(min_value=1, required=False)
    report_id = serializers.IntegerField(min_value=1, required=False)
    reporting_period_id = serializers.IntegerField(min_value=1, required=False)
    revision = serializers.IntegerField(min_value=1, required=False)
    name = serializers.CharField(max_length=160, required=False)
    definition = EarningDefinitionSerializer(required=False)
    period = OperationalPeriodSerializer(required=False)
    observations = OperationalObservationSerializer(many=True, required=False)
    cost_coverage_confirmed = serializers.BooleanField(required=False)
    notes = serializers.CharField(max_length=8000, required=False, allow_blank=True)
    source_fingerprint = serializers.CharField(max_length=64, min_length=64, required=False)
    reason = serializers.CharField(max_length=4000, required=False, allow_blank=True)
