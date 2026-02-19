from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.core.cache import cache
from django.db.models import Q, Count, Avg
import asyncio
import json
from typing import List, Dict

from .models import (
    DocumentEmbedding, RecommendationHistory, 
    UserRecommendationPreferences, DocumentUploadPattern,
    AIModelUsageTracking
)
from .serializers import (
    DocumentEmbeddingSerializer, RecommendationHistorySerializer,
    UserRecommendationPreferencesSerializer, RecommendationResultSerializer
)
from .ai_recommendation_engine import get_recommendation_engine, RecommendationResult
from apps.core.permissions import IsAuthenticatedUser

import logging
logger = logging.getLogger(__name__)

class RecommendationAPIViewSet(viewsets.ViewSet):
    """
    RADAI AI-powered recommendation system API endpoints
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recommendation_engine = get_recommendation_engine()
    
    @action(detail=False, methods=['post'], url_path='analyze-upload')
    def analyze_upload(self, request):
        """
        Analyze uploaded document and provide AI recommendations
        
        POST /api/v1/recommendations/analyze-upload/
        
        Form data:
        - file: Document file
        - document_type: Type of document
        - project_code: Optional project code
        """
        try:
            # Validate input
            if 'file' not in request.FILES:
                return Response(
                    {'error': 'No file provided'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            file_obj = request.FILES['file']
            document_type = request.data.get('document_type', 'unknown')
            project_code = request.data.get('project_code')
            
            # Check user preferences
            user_prefs = self._get_user_preferences(request.user.id)
            if not self._should_analyze_document(document_type, user_prefs):
                return Response({
                    'recommendations': [],
                    'message': 'Analysis disabled for this document type in user preferences'
                })
            
            # Run async analysis
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                recommendations = loop.run_until_complete(
                    self.recommendation_engine.analyze_upload(
                        file_obj,
                        file_obj.name,
                        document_type,
                        request.user.id,
                        project_code
                    )
                )
            finally:
                loop.close()
            
            # Store recommendations in database
            self._save_recommendations(request.user, recommendations, file_obj.name)
            
            # Update user patterns
            self._update_upload_patterns(request.user, document_type)
            
            # Serialize results
            serialized_recommendations = [
                RecommendationResultSerializer(rec).data for rec in recommendations
            ]
            
            return Response({
                'recommendations': serialized_recommendations,
                'total_count': len(recommendations),
                'analysis_timestamp': timezone.now().isoformat(),
                'user_preferences_applied': True
            })
            
        except Exception as e:
            logger.error(f"Error in analyze_upload: {str(e)}")
            return Response(
                {'error': 'Analysis failed', 'details': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='history')
    def recommendation_history(self, request):
        """
        Get user's recommendation history
        
        GET /api/v1/recommendations/history/
        Query params:
        - recommendation_type: Filter by type
        - days: Number of days to look back (default: 30)
        - page: Page number
        - page_size: Items per page (default: 20)
        """
        try:
            # Parse query parameters
            recommendation_type = request.query_params.get('recommendation_type')
            days = int(request.query_params.get('days', 30))
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 20))
            
            # Build query
            queryset = RecommendationHistory.objects.filter(user=request.user)
            
            if recommendation_type:
                queryset = queryset.filter(recommendation_type=recommendation_type)
            
            if days > 0:
                from django.utils import timezone
                from datetime import timedelta
                since_date = timezone.now() - timedelta(days=days)
                queryset = queryset.filter(created_at__gte=since_date)
            
            # Paginate
            total_count = queryset.count()
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            
            history = queryset.order_by('-created_at')[start_idx:end_idx]
            serializer = RecommendationHistorySerializer(history, many=True)
            
            return Response({
                'recommendations': serializer.data,
                'pagination': {
                    'page': page,
                    'page_size': page_size,
                    'total_count': total_count,
                    'total_pages': (total_count + page_size - 1) // page_size
                }
            })
            
        except Exception as e:
            logger.error(f"Error fetching recommendation history: {str(e)}")
            return Response(
                {'error': 'Failed to fetch history'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='feedback')
    def submit_feedback(self, request):
        """
        Submit feedback on a recommendation
        
        POST /api/v1/recommendations/feedback/
        {
            "recommendation_id": "123",
            "action": "accepted|dismissed|viewed|followed_link|implemented_suggestion",
            "feedback_score": 1-5,
            "feedback_text": "Optional feedback text"
        }
        """
        try:
            recommendation_id = request.data.get('recommendation_id')
            user_action = request.data.get('action')
            feedback_score = request.data.get('feedback_score')
            feedback_text = request.data.get('feedback_text')
            
            if not recommendation_id or not user_action:
                return Response(
                    {'error': 'recommendation_id and action are required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Find and update recommendation
            try:
                recommendation = RecommendationHistory.objects.get(
                    id=recommendation_id,
                    user=request.user
                )
                
                recommendation.user_action = user_action
                recommendation.user_feedback_score = feedback_score
                recommendation.user_feedback_text = feedback_text
                recommendation.responded_at = timezone.now()
                recommendation.save()
                
                # Update recommendation engine learning
                self._update_recommendation_learning(recommendation)
                
                return Response({
                    'message': 'Feedback submitted successfully',
                    'recommendation_id': recommendation_id
                })
                
            except RecommendationHistory.DoesNotExist:
                return Response(
                    {'error': 'Recommendation not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
        except Exception as e:
            logger.error(f"Error submitting feedback: {str(e)}")
            return Response(
                {'error': 'Failed to submit feedback'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get', 'put'], url_path='preferences')
    def user_preferences(self, request):
        """
        Get or update user recommendation preferences
        
        GET /api/v1/recommendations/preferences/
        PUT /api/v1/recommendations/preferences/
        """
        try:
            prefs, created = UserRecommendationPreferences.objects.get_or_create(
                user=request.user
            )
            
            if request.method == 'GET':
                serializer = UserRecommendationPreferencesSerializer(prefs)
                return Response(serializer.data)
            
            elif request.method == 'PUT':
                serializer = UserRecommendationPreferencesSerializer(
                    prefs, 
                    data=request.data,
                    partial=True
                )
                
                if serializer.is_valid():
                    serializer.save()
                    
                    # Clear cache to apply new preferences
                    cache.delete(f"user_prefs_{request.user.id}")
                    
                    return Response(serializer.data)
                else:
                    return Response(
                        serializer.errors,
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
        except Exception as e:
            logger.error(f"Error handling user preferences: {str(e)}")
            return Response(
                {'error': 'Failed to handle preferences'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='similar-documents')
    def find_similar_documents(self, request):
        """
        Find documents similar to a given document
        
        GET /api/v1/recommendations/similar-documents/
        Query params:
        - document_id: ID of the reference document
        - limit: Number of similar documents to return (default: 10)
        - min_similarity: Minimum similarity threshold (default: 0.7)
        """
        try:
            document_id = request.query_params.get('document_id')
            limit = int(request.query_params.get('limit', 10))
            min_similarity = float(request.query_params.get('min_similarity', 0.7))
            
            if not document_id:
                return Response(
                    {'error': 'document_id is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Find similar documents using the recommendation engine
            similar_docs = self._find_similar_documents_by_id(
                document_id, limit, min_similarity
            )
            
            return Response({
                'similar_documents': similar_docs,
                'reference_document_id': document_id,
                'similarity_threshold': min_similarity,
                'total_found': len(similar_docs)
            })
            
        except Exception as e:
            logger.error(f"Error finding similar documents: {str(e)}")
            return Response(
                {'error': 'Failed to find similar documents'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='statistics')
    def recommendation_statistics(self, request):
        """
        Get recommendation system statistics
        
        GET /api/v1/recommendations/statistics/
        """
        try:
            # Get user-specific stats
            user_stats = self._get_user_statistics(request.user.id)
            
            # Get system-wide stats (for admins only)
            system_stats = {}
            if request.user.is_staff:
                system_stats = self._get_system_statistics()
            
            # Get AI model usage stats
            ai_stats = self.recommendation_engine.get_recommendation_statistics()
            
            return Response({
                'user_statistics': user_stats,
                'system_statistics': system_stats,
                'ai_statistics': ai_stats,
                'generated_at': timezone.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Error fetching statistics: {str(e)}")
            return Response(
                {'error': 'Failed to fetch statistics'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='batch-analyze')
    def batch_analyze_documents(self, request):
        """
        Analyze multiple documents for recommendations
        
        POST /api/v1/recommendations/batch-analyze/
        Form data with multiple files
        """
        try:
            if not request.FILES:
                return Response(
                    {'error': 'No files provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            results = []
            
            for file_key, file_obj in request.FILES.items():
                try:
                    # Extract document type from form data or filename
                    document_type = request.data.get(f'{file_key}_type', 'unknown')
                    project_code = request.data.get('project_code')
                    
                    # Run analysis
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    try:
                        recommendations = loop.run_until_complete(
                            self.recommendation_engine.analyze_upload(
                                file_obj,
                                file_obj.name,
                                document_type,
                                request.user.id,
                                project_code
                            )
                        )
                    finally:
                        loop.close()
                    
                    results.append({
                        'filename': file_obj.name,
                        'document_type': document_type,
                        'recommendations': [
                            RecommendationResultSerializer(rec).data 
                            for rec in recommendations
                        ],
                        'recommendation_count': len(recommendations)
                    })
                    
                except Exception as file_error:
                    results.append({
                        'filename': file_obj.name if hasattr(file_obj, 'name') else 'unknown',
                        'error': str(file_error),
                        'recommendations': []
                    })
            
            return Response({
                'batch_results': results,
                'total_files': len(request.FILES),
                'successful_analyses': len([r for r in results if 'error' not in r])
            })
            
        except Exception as e:
            logger.error(f"Error in batch analysis: {str(e)}")
            return Response(
                {'error': 'Batch analysis failed'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    # Helper methods
    
    def _get_user_preferences(self, user_id: int) -> UserRecommendationPreferences:
        """Get user preferences with caching"""
        cache_key = f"user_prefs_{user_id}"
        prefs = cache.get(cache_key)
        
        if prefs is None:
            prefs, _ = UserRecommendationPreferences.objects.get_or_create(
                user_id=user_id
            )
            cache.set(cache_key, prefs, timeout=3600)  # 1 hour
        
        return prefs
    
    def _should_analyze_document(self, document_type: str, user_prefs: UserRecommendationPreferences) -> bool:
        """Check if document should be analyzed based on user preferences"""
        type_mapping = {
            'duplicate': user_prefs.enable_duplicate_alerts,
            'similar': user_prefs.enable_similarity_suggestions,
            'quality': user_prefs.enable_quality_checks,
            'completion': user_prefs.enable_auto_completion,
        }
        
        # If any recommendation type is enabled, analyze the document
        return any(type_mapping.values())
    
    def _save_recommendations(
        self, 
        user, 
        recommendations: List[RecommendationResult], 
        source_filename: str
    ):
        """Save recommendations to database"""
        for rec in recommendations:
            RecommendationHistory.objects.create(
                user=user,
                source_document_id=f"temp_{source_filename}_{int(timezone.now().timestamp())}",
                recommendation_type=rec.recommendation_type,
                confidence_score=rec.confidence_score,
                ai_reasoning=rec.ai_reasoning,
                recommended_documents=[asdict(doc) for doc in rec.recommended_documents],
                suggested_actions=rec.suggested_actions,
                metadata=rec.metadata
            )
    
    def _update_upload_patterns(self, user, document_type: str):
        """Update user upload patterns for better recommendations"""
        pattern, created = DocumentUploadPattern.objects.get_or_create(
            user=user,
            document_type=document_type,
            defaults={
                'last_upload': timezone.now(),
                'upload_count': 1
            }
        )
        
        if not created:
            pattern.upload_count += 1
            pattern.last_upload = timezone.now()
            pattern.save()
    
    def _update_recommendation_learning(self, recommendation: RecommendationHistory):
        """Update recommendation engine based on user feedback"""
        # This could update model weights or preferences based on feedback
        pass
    
    def _find_similar_documents_by_id(
        self, 
        document_id: str, 
        limit: int, 
        min_similarity: float
    ) -> List[Dict]:
        """Find similar documents for a given document ID"""
        # Implementation would query the database and calculate similarities
        return []
    
    def _get_user_statistics(self, user_id: int) -> Dict:
        """Get user-specific recommendation statistics"""
        total_recommendations = RecommendationHistory.objects.filter(user_id=user_id).count()
        
        recommendations_by_type = RecommendationHistory.objects.filter(
            user_id=user_id
        ).values('recommendation_type').annotate(count=Count('id'))
        
        avg_confidence = RecommendationHistory.objects.filter(
            user_id=user_id
        ).aggregate(avg_confidence=Avg('confidence_score'))['avg_confidence'] or 0
        
        feedback_given = RecommendationHistory.objects.filter(
            user_id=user_id,
            user_action__isnull=False
        ).count()
        
        return {
            'total_recommendations_received': total_recommendations,
            'recommendations_by_type': {
                item['recommendation_type']: item['count'] 
                for item in recommendations_by_type
            },
            'average_confidence_score': round(avg_confidence, 3),
            'feedback_provided': feedback_given,
            'feedback_rate': round(feedback_given / max(total_recommendations, 1), 3)
        }
    
    def _get_system_statistics(self) -> Dict:
        """Get system-wide recommendation statistics (admin only)"""
        return {
            'total_users_with_recommendations': RecommendationHistory.objects.values('user').distinct().count(),
            'total_recommendations_generated': RecommendationHistory.objects.count(),
            'ai_model_usage': AIModelUsageTracking.objects.values('model_type').annotate(count=Count('id')),
            'average_system_confidence': RecommendationHistory.objects.aggregate(
                avg=Avg('confidence_score')
            )['avg'] or 0,
        }