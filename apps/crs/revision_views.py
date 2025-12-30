"""
CRS Multiple Revision Views
AI-powered revision chain management with intelligent insights
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.utils import timezone
from django.db.models import Q, Count, Sum, Avg
from django.shortcuts import get_object_or_404

from .revision_models import (
    CRSRevisionChain, CRSRevision, CRSCommentLink,
    CRSAIInsight, CRSRevisionActivity
)
from .models import CRSDocument, CRSComment, CRSActivity
from .revision_serializers import (
    CRSRevisionChainListSerializer, CRSRevisionChainDetailSerializer,
    CRSRevisionChainCreateSerializer, CRSRevisionSerializer,
    CRSRevisionCreateSerializer, CRSCommentLinkSerializer,
    CRSCommentLinkCreateSerializer, CRSAIInsightSerializer,
    CRSAIInsightFeedbackSerializer, CRSRevisionActivitySerializer
)
from .ai_service import CRSRevisionAIService

import logging

logger = logging.getLogger(__name__)


class CRSRevisionChainViewSet(viewsets.ModelViewSet):
    """
    ViewSet for CRS Revision Chain management
    Handles multi-revision tracking and AI-powered insights
    """
    
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Get chains with optional filtering"""
        queryset = CRSRevisionChain.objects.all()
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by risk level
        risk_filter = self.request.query_params.get('risk_level')
        if risk_filter:
            queryset = queryset.filter(risk_level=risk_filter)
        
        # Filter by project
        project = self.request.query_params.get('project')
        if project:
            queryset = queryset.filter(project_name__icontains=project)
        
        # Search
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(chain_id__icontains=search) |
                Q(document_title__icontains=search) |
                Q(document_number__icontains=search) |
                Q(project_name__icontains=search)
            )
        
        return queryset.prefetch_related('revisions', 'revisions__document')
    
    def get_serializer_class(self):
        """Use detailed serializer for retrieve, list serializer for list"""
        if self.action == 'retrieve':
            return CRSRevisionChainDetailSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return CRSRevisionChainCreateSerializer
        return CRSRevisionChainListSerializer
    
    def perform_create(self, serializer):
        """Create chain and log activity"""
        chain = serializer.save(created_by=self.request.user)
        
        # Log activity
        CRSRevisionActivity.objects.create(
            chain=chain,
            action='chain_created',
            description=f'Revision chain created: {chain.chain_id}',
            performed_by=self.request.user
        )
    
    @action(detail=True, methods=['post'])
    def add_revision(self, request, pk=None):
        """
        Add a new revision to the chain
        POST /api/v1/crs/revision-chains/{id}/add_revision/
        Body: {
            "document_id": 123,
            "revision_label": "Rev 2",
            "parent_revision_id": 456,  // optional
            "submitted_date": "2025-01-15T10:00:00Z",
            "notes": "Additional comments from client"
        }
        """
        chain = self.get_object()
        
        document_id = request.data.get('document_id')
        revision_label = request.data.get('revision_label')
        parent_revision_id = request.data.get('parent_revision_id')
        submitted_date = request.data.get('submitted_date')
        notes = request.data.get('notes', '')
        
        if not document_id or not revision_label:
            return Response(
                {"error": "document_id and revision_label are required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            document = CRSDocument.objects.get(id=document_id)
        except CRSDocument.DoesNotExist:
            return Response(
                {"error": "Document not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if document already has a revision
        if hasattr(document, 'revision_info'):
            return Response(
                {"error": "This document is already linked to a revision"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get parent revision if specified
        parent_revision = None
        if parent_revision_id:
            try:
                parent_revision = CRSRevision.objects.get(id=parent_revision_id, chain=chain)
            except CRSRevision.DoesNotExist:
                return Response(
                    {"error": "Parent revision not found in this chain"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # Calculate revision number
        revision_number = chain.total_revisions + 1
        
        with transaction.atomic():
            # Create revision
            revision = CRSRevision.objects.create(
                chain=chain,
                document=document,
                revision_number=revision_number,
                revision_label=revision_label,
                parent_revision=parent_revision,
                submitted_date=submitted_date or timezone.now(),
                status='submitted',
                notes=notes
            )
            
            # Update chain
            chain.total_revisions += 1
            chain.current_revision_number = revision_number
            chain.save()
            
            # If there's a parent revision, detect comment links
            if parent_revision:
                self._auto_link_comments(parent_revision, revision, request.user)
            
            # Calculate AI metrics
            self._calculate_revision_ai_metrics(revision)
            self._update_chain_ai_metrics(chain)
            
            # Log activity
            CRSRevisionActivity.objects.create(
                chain=chain,
                revision=revision,
                action='revision_added',
                description=f'Added {revision_label} to chain',
                performed_by=request.user,
                new_value={'revision_number': revision_number, 'revision_label': revision_label}
            )
        
        return Response({
            "success": True,
            "message": f"Revision {revision_label} added successfully",
            "data": CRSRevisionSerializer(revision).data
        }, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def analyze_chain(self, request, pk=None):
        """
        Run comprehensive AI analysis on the chain
        POST /api/v1/crs/revision-chains/{id}/analyze_chain/
        """
        chain = self.get_object()
        
        with transaction.atomic():
            # Update AI metrics
            self._update_chain_ai_metrics(chain)
            
            # Generate AI insights
            insights = self._generate_chain_insights(chain, request.user)
            
            # Log activity
            CRSRevisionActivity.objects.create(
                chain=chain,
                action='ai_insight_generated',
                description=f'AI analysis completed, generated {len(insights)} insights',
                performed_by=request.user
            )
        
        return Response({
            "success": True,
            "message": "Chain analysis completed",
            "data": {
                "chain": CRSRevisionChainDetailSerializer(chain).data,
                "insights": CRSAIInsightSerializer(insights, many=True).data
            }
        })
    
    @action(detail=True, methods=['get'])
    def revision_timeline(self, request, pk=None):
        """
        Get timeline view of all revisions in the chain
        GET /api/v1/crs/revision-chains/{id}/revision_timeline/
        """
        chain = self.get_object()
        revisions = chain.revisions.all().order_by('revision_number')
        
        timeline_data = []
        for revision in revisions:
            timeline_data.append({
                'revision': CRSRevisionSerializer(revision).data,
                'activities': CRSRevisionActivitySerializer(
                    chain.activities.filter(revision=revision),
                    many=True
                ).data[:10]  # Last 10 activities per revision
            })
        
        return Response({
            "chain_id": chain.chain_id,
            "timeline": timeline_data
        })
    
    @action(detail=True, methods=['get'])
    def statistics(self, request, pk=None):
        """
        Get comprehensive statistics for the chain
        GET /api/v1/crs/revision-chains/{id}/statistics/
        """
        chain = self.get_object()
        revisions = chain.revisions.all()
        
        stats = {
            'chain_info': {
                'chain_id': chain.chain_id,
                'status': chain.status,
                'risk_level': chain.risk_level,
                'ai_risk_score': chain.ai_risk_score,
                'rejection_risk_percentage': chain.rejection_risk_percentage
            },
            'revision_stats': {
                'total_revisions': chain.total_revisions,
                'current_revision': chain.current_revision_number,
                'max_allowed': chain.max_allowed_revisions,
                'remaining_revisions': chain.max_allowed_revisions - chain.current_revision_number
            },
            'comment_stats': {
                'total_comments_all_revisions': sum(r.total_comments for r in revisions),
                'total_new_comments': sum(r.total_new_comments for r in revisions),
                'total_carryover_comments': sum(r.total_carryover_comments for r in revisions),
                'total_resolved': sum(r.total_resolved_comments for r in revisions),
                'overall_resolution_rate': 0
            },
            'revision_breakdown': []
        }
        
        # Calculate overall resolution rate
        total_comments = stats['comment_stats']['total_comments_all_revisions']
        if total_comments > 0:
            stats['comment_stats']['overall_resolution_rate'] = round(
                (stats['comment_stats']['total_resolved'] / total_comments) * 100, 2
            )
        
        # Per-revision breakdown
        for revision in revisions:
            stats['revision_breakdown'].append({
                'revision_label': revision.revision_label,
                'revision_number': revision.revision_number,
                'status': revision.status,
                'total_comments': revision.total_comments,
                'new_comments': revision.total_new_comments,
                'carryover_comments': revision.total_carryover_comments,
                'resolved_comments': revision.total_resolved_comments,
                'resolution_percentage': round((revision.total_resolved_comments / revision.total_comments * 100), 2) if revision.total_comments > 0 else 0,
                'complexity_score': revision.ai_complexity_score,
                'estimated_hours': revision.ai_estimated_resolution_time_hours
            })
        
        return Response(stats)
    
    @action(detail=False, methods=['get'])
    def dashboard_summary(self, request):
        """
        Get dashboard summary of all chains
        GET /api/v1/crs/revision-chains/dashboard_summary/
        """
        chains = self.get_queryset()
        
        summary = {
            'total_chains': chains.count(),
            'by_status': {},
            'by_risk_level': {},
            'critical_chains': [],
            'near_rejection': []
        }
        
        # Count by status
        for choice in CRSRevisionChain.STATUS_CHOICES:
            count = chains.filter(status=choice[0]).count()
            summary['by_status'][choice[1]] = count
        
        # Count by risk level
        for choice in CRSRevisionChain.RISK_LEVEL_CHOICES:
            count = chains.filter(risk_level=choice[0]).count()
            summary['by_risk_level'][choice[1]] = count
        
        # Critical chains (high/critical risk)
        critical = chains.filter(risk_level__in=['high', 'critical']).order_by('-ai_risk_score')[:10]
        summary['critical_chains'] = CRSRevisionChainListSerializer(critical, many=True).data
        
        # Near rejection (close to max revisions)
        near_rejection = [c for c in chains if c.is_near_rejection]
        summary['near_rejection'] = CRSRevisionChainListSerializer(near_rejection, many=True).data
        
        return Response(summary)
    
    # Helper methods
    
    def _auto_link_comments(self, parent_revision, child_revision, user):
        """Automatically detect and link comments between revisions using AI"""
        parent_comments = parent_revision.document.comments.all()
        child_comments = child_revision.document.comments.all()
        
        # Use AI service to detect links
        potential_links = CRSRevisionAIService.detect_comment_links(
            parent_comments, child_comments, threshold=60.0
        )
        
        # Create comment links
        for link_data in potential_links:
            CRSCommentLink.objects.create(
                source_revision=parent_revision,
                target_revision=child_revision,
                source_comment_id=link_data['source_comment_id'],
                target_comment_id=link_data['target_comment_id'],
                link_type=link_data['link_type'],
                similarity_score=link_data['similarity_score'],
                ai_detected=True,
                ai_confidence=link_data['ai_confidence'],
                created_by=user
            )
        
        # Update carryover count
        child_revision.total_carryover_comments = len(potential_links)
        child_revision.total_new_comments = child_comments.count() - len(potential_links)
        child_revision.save()
        
        # Log activity
        CRSRevisionActivity.objects.create(
            chain=child_revision.chain,
            revision=child_revision,
            action='comment_linked',
            description=f'AI detected {len(potential_links)} comment links',
            performed_by=user,
            new_value={'links_created': len(potential_links)}
        )
    
    def _calculate_revision_ai_metrics(self, revision):
        """Calculate AI metrics for a revision"""
        # Complexity score
        complexity = CRSRevisionAIService.calculate_complexity_score(revision)
        
        # Estimated resolution time
        estimated_hours = CRSRevisionAIService.estimate_resolution_time(revision)
        
        # Critical issues count
        critical_count = revision.document.comments.filter(priority__in=['high', 'critical']).count()
        
        revision.ai_complexity_score = complexity
        revision.ai_estimated_resolution_time_hours = estimated_hours
        revision.ai_critical_issues_count = critical_count
        revision.save()
    
    def _update_chain_ai_metrics(self, chain):
        """Update AI metrics for the entire chain"""
        # Calculate risk score
        risk_score = CRSRevisionAIService.calculate_risk_score(chain)
        risk_level = CRSRevisionAIService.determine_risk_level(risk_score)
        
        # Generate recommendation
        recommendation = CRSRevisionAIService.generate_risk_recommendation(chain)
        
        # Predict completion date
        predicted_date = CRSRevisionAIService.predict_completion_date(chain)
        
        chain.ai_risk_score = risk_score
        chain.risk_level = risk_level
        chain.ai_recommendation = recommendation
        chain.ai_predicted_completion_date = predicted_date
        chain.save()
    
    def _generate_chain_insights(self, chain, user):
        """Generate AI insights for the chain"""
        insights = []
        
        # Risk assessment insight
        if chain.ai_risk_score >= 50:
            insights.append(
                CRSAIInsight.objects.create(
                    chain=chain,
                    insight_type='risk_assessment',
                    severity='critical' if chain.ai_risk_score >= 75 else 'warning',
                    title=f'Risk Level: {chain.get_risk_level_display()}',
                    description=f'Current risk score is {chain.ai_risk_score}/100. {chain.ai_recommendation}',
                    confidence_score=95.0,
                    recommended_action=chain.ai_recommendation
                )
            )
        
        # Near rejection alert
        if chain.is_near_rejection:
            insights.append(
                CRSAIInsight.objects.create(
                    chain=chain,
                    insight_type='escalation_alert',
                    severity='critical',
                    title='⚠️ Approaching Maximum Revisions',
                    description=f'This chain is at revision {chain.current_revision_number} of {chain.max_allowed_revisions} allowed. Immediate action required to prevent project rejection.',
                    confidence_score=100.0,
                    recommended_action='Schedule emergency review meeting with stakeholders'
                )
            )
        
        # Pattern detection for latest revision
        latest_revision = chain.revisions.order_by('-revision_number').first()
        if latest_revision:
            comments = latest_revision.document.comments.all()
            if comments.exists():
                patterns = CRSRevisionAIService.analyze_comment_patterns(comments)
                
                if patterns.get('top_keywords'):
                    keyword_list = ', '.join([k['word'] for k in patterns['top_keywords'][:5]])
                    insights.append(
                        CRSAIInsight.objects.create(
                            chain=chain,
                            revision=latest_revision,
                            insight_type='pattern_detection',
                            severity='info',
                            title='Common Comment Themes Detected',
                            description=f'Most frequent topics in {latest_revision.revision_label}: {keyword_list}',
                            confidence_score=85.0,
                            recommended_action='Focus resolution efforts on these common themes'
                        )
                    )
        
        return insights


class CRSRevisionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for individual CRS Revision management
    """
    
    queryset = CRSRevision.objects.all()
    serializer_class = CRSRevisionSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter by chain if specified"""
        queryset = super().get_queryset()
        chain_id = self.request.query_params.get('chain_id')
        if chain_id:
            queryset = queryset.filter(chain_id=chain_id)
        return queryset.select_related('document', 'chain', 'parent_revision')
    
    @action(detail=True, methods=['get'])
    def comment_links(self, request, pk=None):
        """
        Get all comment links for this revision
        GET /api/v1/crs/revisions/{id}/comment_links/
        """
        revision = self.get_object()
        
        links_from = CRSCommentLink.objects.filter(source_revision=revision)
        links_to = CRSCommentLink.objects.filter(target_revision=revision)
        
        return Response({
            'revision_label': revision.revision_label,
            'links_from_previous': CRSCommentLinkSerializer(links_from, many=True).data,
            'links_to_next': CRSCommentLinkSerializer(links_to, many=True).data
        })
    
    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """
        Update revision status
        POST /api/v1/crs/revisions/{id}/update_status/
        Body: { "status": "completed" }
        """
        revision = self.get_object()
        new_status = request.data.get('status')
        
        if not new_status:
            return Response(
                {"error": "status is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        valid_statuses = [choice[0] for choice in CRSRevision.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {"error": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        old_status = revision.status
        revision.status = new_status
        
        # Set completion date if status is completed
        if new_status == 'completed' and not revision.completed_date:
            revision.completed_date = timezone.now()
        
        revision.save()
        
        # Log activity
        CRSRevisionActivity.objects.create(
            chain=revision.chain,
            revision=revision,
            action='status_changed',
            description=f'Revision status changed from {old_status} to {new_status}',
            performed_by=request.user,
            old_value={'status': old_status},
            new_value={'status': new_status}
        )
        
        return Response({
            "success": True,
            "message": f"Status updated to {new_status}",
            "data": CRSRevisionSerializer(revision).data
        })


class CRSCommentLinkViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing comment links across revisions
    """
    
    queryset = CRSCommentLink.objects.all()
    serializer_class = CRSCommentLinkSerializer
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'create':
            return CRSCommentLinkCreateSerializer
        return CRSCommentLinkSerializer
    
    def perform_create(self, serializer):
        """Create link and log activity"""
        link = serializer.save(created_by=self.request.user)
        
        CRSRevisionActivity.objects.create(
            chain=link.source_revision.chain,
            revision=link.target_revision,
            action='comment_linked',
            description=f'Comment #{link.source_comment.serial_number} linked to #{link.target_comment.serial_number}',
            performed_by=self.request.user
        )


class CRSAIInsightViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing AI insights
    """
    
    queryset = CRSAIInsight.objects.filter(is_active=True)
    serializer_class = CRSAIInsightSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter by chain, revision, or severity"""
        queryset = super().get_queryset()
        
        chain_id = self.request.query_params.get('chain_id')
        if chain_id:
            queryset = queryset.filter(chain_id=chain_id)
        
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        
        severity = self.request.query_params.get('severity')
        if severity:
            queryset = queryset.filter(severity=severity)
        
        insight_type = self.request.query_params.get('insight_type')
        if insight_type:
            queryset = queryset.filter(insight_type=insight_type)
        
        return queryset.order_by('-created_at')
    
    @action(detail=True, methods=['post'])
    def submit_feedback(self, request, pk=None):
        """
        Submit feedback on an AI insight
        POST /api/v1/crs/ai-insights/{id}/submit_feedback/
        Body: { "was_helpful": true, "user_feedback": "Very helpful!" }
        """
        insight = self.get_object()
        serializer = CRSAIInsightFeedbackSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        insight.was_helpful = serializer.validated_data['was_helpful']
        insight.user_feedback = serializer.validated_data.get('user_feedback', '')
        insight.save()
        
        return Response({
            "success": True,
            "message": "Feedback submitted successfully"
        })
    
    @action(detail=True, methods=['post'])
    def dismiss(self, request, pk=None):
        """
        Dismiss an AI insight
        POST /api/v1/crs/ai-insights/{id}/dismiss/
        """
        insight = self.get_object()
        insight.is_active = False
        insight.dismissed_by = request.user
        insight.dismissed_at = timezone.now()
        insight.save()
        
        return Response({
            "success": True,
            "message": "Insight dismissed"
        })


class CRSRevisionActivityViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing revision activities
    """
    
    queryset = CRSRevisionActivity.objects.all()
    serializer_class = CRSRevisionActivitySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter by chain or revision"""
        queryset = super().get_queryset()
        
        chain_id = self.request.query_params.get('chain_id')
        if chain_id:
            queryset = queryset.filter(chain_id=chain_id)
        
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        
        return queryset.order_by('-performed_at')
