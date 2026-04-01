from rest_framework import serializers

from .models import CrossRecommendationLink


class CrossRecommendationLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = CrossRecommendationLink
        fields = [
            'link_id', 'source_type', 'source_document_id',
            'target_type', 'target_document_id', 'project_id',
            'score', 'reason', 'decision', 'created_at', 'updated_at',
        ]


class LinkCreateSerializer(serializers.Serializer):
    source_type = serializers.ChoiceField(choices=CrossRecommendationLink.DocType.choices)
    source_document_id = serializers.UUIDField()
    target_type = serializers.ChoiceField(choices=CrossRecommendationLink.DocType.choices)
    target_document_id = serializers.UUIDField()
    project_id = serializers.UUIDField(required=False, allow_null=True)
    score = serializers.FloatField(required=False, default=0.0)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    decision = serializers.ChoiceField(
        choices=CrossRecommendationLink.Decision.choices,
        required=False,
        default=CrossRecommendationLink.Decision.ACCEPTED,
    )
