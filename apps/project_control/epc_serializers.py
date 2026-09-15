from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.core.project_models import CURRENCY_CHOICES
from .epc_models import EPC_LINK_TYPES, EPC_SCOPE_TYPES, IntegratedBaseline, WBSActivityLink, control_scope


class EpcSetupSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=255)
    client_name = serializers.CharField(max_length=255)
    owner = serializers.PrimaryKeyRelatedField(queryset=get_user_model().objects.filter(is_active=True))
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    currency = serializers.ChoiceField(choices=CURRENCY_CHOICES)
    scope_type = serializers.ChoiceField(choices=EPC_SCOPE_TYPES)

    def validate(self, attrs):
        if attrs['end_date'] < attrs['start_date']:
            raise serializers.ValidationError({'end_date': 'Finish must be on or after start.'})
        return attrs


class ActivityLinkInputSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=1, required=False)
    wbs_node = serializers.IntegerField(min_value=1)
    activity = serializers.IntegerField(min_value=1)
    link_type = serializers.ChoiceField(choices=EPC_LINK_TYPES)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=4000, default='')


class WBSActivityLinkSerializer(serializers.ModelSerializer):
    wbs_code = serializers.CharField(source='wbs_node.code', read_only=True)
    wbs_name = serializers.CharField(source='wbs_node.name', read_only=True)
    activity_code = serializers.CharField(source='activity.external_id', read_only=True)
    activity_name = serializers.CharField(source='activity.name', read_only=True)
    version = serializers.IntegerField(source='activity.version_id', read_only=True)
    control_role = serializers.SerializerMethodField()

    def get_control_role(self, obj):
        return 'owned' if obj.link_type in control_scope(obj.project.scope_type)['owned_phases'] else 'dependency'

    class Meta:
        model = WBSActivityLink
        fields = ['id', 'wbs_node', 'wbs_code', 'wbs_name', 'activity', 'activity_code',
                  'activity_name', 'version', 'link_type', 'control_role', 'notes']
        read_only_fields = fields


class RequisitionLinkInputSerializer(serializers.Serializer):
    requisition = serializers.UUIDField()
    wbs_node = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(min_length=10, max_length=500)
    review_confirmed = serializers.BooleanField(default=False)


class BaselineInputSerializer(serializers.Serializer):
    schedule_baseline = serializers.IntegerField(min_value=1)
    budget_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)
    name = serializers.CharField(max_length=255)
    data_date = serializers.DateField()

    def validate_budget_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError('Select each budget only once.')
        return value


class IntegratedBaselineSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntegratedBaseline
        fields = '__all__'
        read_only_fields = [field.name for field in IntegratedBaseline._meta.fields]
