"""Serializers for schedule governance, discussion, and approval workflows."""
from django.utils import timezone
from rest_framework import serializers

from apps.users.models import User

from .models import GovernanceComment, GovernanceItem, ScheduleReview, ScheduleReviewDecision


class GovernanceUserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'name']

    def get_name(self, obj):
        return obj.get_full_name() or obj.email


class GovernanceCommentSerializer(serializers.ModelSerializer):
    author = GovernanceUserSerializer(read_only=True)
    resolved_by = GovernanceUserSerializer(read_only=True)

    class Meta:
        model = GovernanceComment
        fields = [
            'id', 'item', 'review', 'parent', 'body', 'author', 'mentioned_user_ids',
            'is_resolved', 'resolved_by', 'resolved_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class GovernanceItemSerializer(serializers.ModelSerializer):
    owner = GovernanceUserSerializer(read_only=True)
    raised_by = GovernanceUserSerializer(read_only=True)
    comments = GovernanceCommentSerializer(many=True, read_only=True)
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = GovernanceItem
        fields = [
            'id', 'version', 'activity', 'item_type', 'title', 'description', 'status',
            'priority', 'due_date', 'schedule_impact_days', 'cost_impact', 'owner',
            'raised_by', 'resolution', 'closed_at', 'metadata', 'comments',
            'is_overdue', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_is_overdue(self, obj):
        return bool(obj.due_date and obj.due_date < timezone.localdate() and obj.status not in {'closed', 'implemented', 'rejected'})


class GovernanceItemInputSerializer(serializers.Serializer):
    item_type = serializers.ChoiceField(choices=GovernanceItem.TYPE_CHOICES)
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    priority = serializers.ChoiceField(choices=GovernanceItem.PRIORITY_CHOICES, default='medium')
    due_date = serializers.DateField(required=False, allow_null=True)
    activity = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    owner = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    schedule_impact_days = serializers.DecimalField(max_digits=10, decimal_places=2, default=0)
    cost_impact = serializers.DecimalField(max_digits=16, decimal_places=2, default=0)


class GovernanceItemUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=GovernanceItem.STATUS_CHOICES, required=False)
    priority = serializers.ChoiceField(choices=GovernanceItem.PRIORITY_CHOICES, required=False)
    owner = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    due_date = serializers.DateField(required=False, allow_null=True)
    resolution = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class ScheduleReviewDecisionSerializer(serializers.ModelSerializer):
    reviewer = GovernanceUserSerializer(read_only=True)

    class Meta:
        model = ScheduleReviewDecision
        fields = ['id', 'reviewer', 'status', 'comment', 'decided_at', 'created_at', 'updated_at']
        read_only_fields = fields


class ScheduleReviewSerializer(serializers.ModelSerializer):
    requested_by = GovernanceUserSerializer(read_only=True)
    decisions = ScheduleReviewDecisionSerializer(many=True, read_only=True)
    comments = GovernanceCommentSerializer(many=True, read_only=True)

    class Meta:
        model = ScheduleReview
        fields = [
            'id', 'version', 'title', 'description', 'status', 'due_date',
            'requested_by', 'requested_at', 'completed_at', 'decisions', 'comments',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ScheduleReviewInputSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    due_date = serializers.DateField(required=False, allow_null=True)
    reviewer_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=20,
    )

    def validate_reviewer_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError('Each reviewer may be assigned only once.')
        return value


class ReviewDecisionInputSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=['approved', 'changes_requested', 'rejected'])
    comment = serializers.CharField(required=False, allow_blank=True, default='', max_length=4000)

    def validate(self, attrs):
        if attrs['decision'] != 'approved' and not attrs.get('comment', '').strip():
            raise serializers.ValidationError({'comment': 'A comment is required for this decision.'})
        return attrs


class GovernanceCommentInputSerializer(serializers.Serializer):
    item = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    review = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    parent = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    body = serializers.CharField(max_length=8000)
    mentioned_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, default=list, max_length=50,
    )

    def validate(self, attrs):
        if bool(attrs.get('item')) == bool(attrs.get('review')):
            raise serializers.ValidationError('Provide exactly one item or review target.')
        return attrs
