import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.rbac.permissions import HasDisciplineAccess

from .models import CrossRecommendationLink
from .serializers import LinkCreateSerializer, CrossRecommendationLinkSerializer
from .services.recommendation_engine import get_recommendations

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasDisciplineAccess])
def recommendations(request):
    """Get cross-feature recommendations with discipline-based access control"""
    recommendations.module_required = 'cross_recommendation'
    source_type = (request.query_params.get('source_type') or '').strip().lower()
    if source_type not in {'pid', 'pfd'}:
        return Response({'error': 'source_type must be pid or pfd'}, status=status.HTTP_400_BAD_REQUEST)

    source_document_id = request.query_params.get('document_id')
    project_id = request.query_params.get('project_id')
    query = request.query_params.get('query', '')

    try:
        limit = int(request.query_params.get('limit', 8))
    except ValueError:
        limit = 8

    data = get_recommendations(
        source_type=source_type,
        source_document_id=source_document_id,
        project_id=project_id,
        query=query,
        user=request.user,
        limit=max(1, min(limit, 20)),
    )
    return Response(data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasDisciplineAccess])
def create_or_update_link(request):
    """Create/update recommendation link with discipline-based access control"""
    create_or_update_link.module_required = 'cross_recommendation'
    serializer = LinkCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    if data['source_type'] == data['target_type']:
        return Response({'error': 'source_type and target_type must be different'}, status=status.HTTP_400_BAD_REQUEST)

    link, _created = CrossRecommendationLink.objects.update_or_create(
        source_type=data['source_type'],
        source_document_id=data['source_document_id'],
        target_type=data['target_type'],
        target_document_id=data['target_document_id'],
        defaults={
            'project_id': data.get('project_id'),
            'score': data.get('score', 0.0),
            'reason': data.get('reason', ''),
            'decision': data.get('decision', CrossRecommendationLink.Decision.ACCEPTED),
            'created_by': request.user,
        },
    )

    try:
        from .tasks import sync_s3_snapshot
        sync_s3_snapshot.delay()
    except Exception as exc:
        logger.warning('[CrossRecommendation] Async snapshot trigger failed: %s', exc)

    return Response(CrossRecommendationLinkSerializer(link).data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_snapshot(request):
    try:
        from .tasks import sync_s3_snapshot
        task = sync_s3_snapshot.delay()
        return Response({'message': 'Snapshot sync queued', 'task_id': str(task.id)})
    except Exception as exc:
        logger.exception('[CrossRecommendation] Failed to queue snapshot sync: %s', exc)
        return Response({'error': 'Failed to queue sync', 'detail': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
