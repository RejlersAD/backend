"""
CRS Multiple Revision Serializers
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from .revision_models import (
    CRSRevisionChain, CRSRevision, CRSCommentLink, 
    CRSAIInsight, CRSRevisionActivity
)
from .models import CRSDocument, CRSComment
from .serializers import CRSDocumentSerializer

User = get_user_model()


class CRSRevisionChainListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing chains"""
    
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    risk_percentage = serializers.SerializerMethodField()
    latest_revision_date = serializers.SerializerMethodField()
    
    class Meta:
        model = CRSRevisionChain
        fields = [
            'id', 'chain_id', 'project_name', 'document_number', 'document_title',
            'contractor_name', 'department', 'status', 'risk_level',
            'total_revisions', 'current_revision_number', 'max_allowed_revisions',
            'ai_risk_score', 'risk_percentage', 'is_near_rejection',
            'created_by', 'created_by_name', 'latest_revision_date',
            'created_at', 'updated_at'
        ]
    
    def get_risk_percentage(self, obj):
        return obj.rejection_risk_percentage
    
    def get_latest_revision_date(self, obj):
        latest_rev = obj.revisions.order_by('-revision_number').first()
        return latest_rev.updated_at if latest_rev else None


class CRSRevisionSerializer(serializers.ModelSerializer):
    """Serializer for individual revisions"""
    
    document_details = CRSDocumentSerializer(source='document', read_only=True)
    parent_revision_label = serializers.CharField(source='parent_revision.revision_label', read_only=True, allow_null=True)
    resolution_percentage = serializers.SerializerMethodField()
    
    class Meta:
        model = CRSRevision
        fields = [
            'id', 'chain', 'document', 'document_details',
            'revision_number', 'revision_label', 'parent_revision', 'parent_revision_label',
            'status', 'submitted_date', 'received_date', 'completed_date',
            'total_new_comments', 'total_carryover_comments', 'total_comments',
            'total_resolved_comments', 'resolution_percentage',
            'ai_complexity_score', 'ai_estimated_resolution_time_hours', 'ai_critical_issues_count',
            'created_at', 'updated_at', 'notes'
        ]
    
    def get_resolution_percentage(self, obj):
        if obj.total_comments == 0:
            return 0
        return round((obj.total_resolved_comments / obj.total_comments) * 100, 2)


class CRSRevisionChainDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for single chain with all revisions"""
    
    revisions = CRSRevisionSerializer(many=True, read_only=True)
    created_by_details = serializers.SerializerMethodField()
    risk_percentage = serializers.SerializerMethodField()
    total_comments_all_revisions = serializers.SerializerMethodField()
    total_resolved_all_revisions = serializers.SerializerMethodField()
    overall_completion_percentage = serializers.SerializerMethodField()
    
    class Meta:
        model = CRSRevisionChain
        fields = [
            'id', 'chain_id', 'project_name', 'document_number', 'document_title',
            'contractor_name', 'department', 'status', 'risk_level',
            'total_revisions', 'current_revision_number', 'max_allowed_revisions',
            'ai_risk_score', 'ai_recommendation', 'ai_predicted_completion_date',
            'risk_percentage', 'is_near_rejection', 'rejection_risk_percentage',
            'total_comments_all_revisions', 'total_resolved_all_revisions',
            'overall_completion_percentage',
            'created_by', 'created_by_details', 'created_at', 'updated_at',
            'notes', 'revisions'
        ]
    
    def get_created_by_details(self, obj):
        if obj.created_by:
            return {
                'id': obj.created_by.id,
                'name': obj.created_by.get_full_name(),
                'email': obj.created_by.email
            }
        return None
    
    def get_risk_percentage(self, obj):
        return obj.rejection_risk_percentage
    
    def get_total_comments_all_revisions(self, obj):
        return sum(r.total_comments for r in obj.revisions.all())
    
    def get_total_resolved_all_revisions(self, obj):
        return sum(r.total_resolved_comments for r in obj.revisions.all())
    
    def get_overall_completion_percentage(self, obj):
        total = self.get_total_comments_all_revisions(obj)
        if total == 0:
            return 0
        resolved = self.get_total_resolved_all_revisions(obj)
        return round((resolved / total) * 100, 2)


class CRSCommentLinkSerializer(serializers.ModelSerializer):
    """Serializer for comment links across revisions"""
    
    source_revision_label = serializers.CharField(source='source_revision.revision_label', read_only=True)
    target_revision_label = serializers.CharField(source='target_revision.revision_label', read_only=True)
    source_comment_text = serializers.CharField(source='source_comment.comment_text', read_only=True)
    target_comment_text = serializers.CharField(source='target_comment.comment_text', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = CRSCommentLink
        fields = [
            'id', 'source_revision', 'source_revision_label', 'target_revision', 'target_revision_label',
            'source_comment', 'source_comment_text', 'target_comment', 'target_comment_text',
            'link_type', 'similarity_score', 'ai_detected', 'ai_confidence',
            'created_by', 'created_by_name', 'created_at', 'notes'
        ]


class CRSAIInsightSerializer(serializers.ModelSerializer):
    """Serializer for AI insights"""
    
    chain_id = serializers.CharField(source='chain.chain_id', read_only=True, allow_null=True)
    revision_label = serializers.CharField(source='revision.revision_label', read_only=True, allow_null=True)
    comment_serial = serializers.IntegerField(source='comment.serial_number', read_only=True, allow_null=True)
    dismissed_by_name = serializers.CharField(source='dismissed_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = CRSAIInsight
        fields = [
            'id', 'chain', 'chain_id', 'revision', 'revision_label',
            'comment', 'comment_serial', 'insight_type', 'severity',
            'title', 'description', 'confidence_score', 'ai_model_used',
            'recommended_action', 'suggested_response',
            'was_helpful', 'user_feedback', 'is_active',
            'dismissed_by', 'dismissed_by_name', 'dismissed_at',
            'created_at', 'updated_at'
        ]


class CRSRevisionActivitySerializer(serializers.ModelSerializer):
    """Serializer for revision activities"""
    
    chain_id = serializers.CharField(source='chain.chain_id', read_only=True)
    revision_label = serializers.CharField(source='revision.revision_label', read_only=True, allow_null=True)
    performed_by_name = serializers.CharField(source='performed_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = CRSRevisionActivity
        fields = [
            'id', 'chain', 'chain_id', 'revision', 'revision_label',
            'action', 'description', 'performed_by', 'performed_by_name',
            'performed_at', 'old_value', 'new_value'
        ]


# Create/Update Serializers

class CRSRevisionChainCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating new revision chains"""
    
    class Meta:
        model = CRSRevisionChain
        fields = [
            'chain_id', 'project_name', 'document_number', 'document_title',
            'contractor_name', 'department', 'max_allowed_revisions', 'notes'
        ]
    
    def validate_chain_id(self, value):
        """Ensure chain_id is unique"""
        if CRSRevisionChain.objects.filter(chain_id=value).exists():
            raise serializers.ValidationError("Chain ID already exists")
        return value


class CRSRevisionCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating new revisions"""
    
    class Meta:
        model = CRSRevision
        fields = [
            'chain', 'document', 'revision_number', 'revision_label',
            'parent_revision', 'submitted_date', 'notes'
        ]
    
    def validate(self, data):
        """Validate revision creation"""
        chain = data.get('chain')
        revision_number = data.get('revision_number')
        
        # Check if revision number already exists in chain
        if CRSRevision.objects.filter(chain=chain, revision_number=revision_number).exists():
            raise serializers.ValidationError({
                'revision_number': f'Revision {revision_number} already exists in this chain'
            })
        
        # Ensure parent revision exists if specified
        parent = data.get('parent_revision')
        if parent and parent.chain != chain:
            raise serializers.ValidationError({
                'parent_revision': 'Parent revision must belong to the same chain'
            })
        
        return data


class CRSCommentLinkCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating comment links"""
    
    class Meta:
        model = CRSCommentLink
        fields = [
            'source_revision', 'target_revision', 'source_comment',
            'target_comment', 'link_type', 'similarity_score', 'notes'
        ]


class CRSAIInsightFeedbackSerializer(serializers.Serializer):
    """Serializer for submitting feedback on AI insights"""
    
    was_helpful = serializers.BooleanField()
    user_feedback = serializers.CharField(required=False, allow_blank=True)
