"""
User History API Views - CRS Documents
Provides API endpoints for user file history and activity tracking

Endpoints:
- GET /api/v1/crs/documents/history/ - Get user's activity history
- GET /api/v1/crs/documents/history/uploads/ - Get user's uploaded files
- GET /api/v1/crs/documents/history/exports/ - Get user's exported files
- GET /api/v1/crs/documents/history/profile/ - Get user's storage profile
- GET /api/v1/crs/documents/history/download/<path>/ - Download a file from history

Does NOT modify existing views or core logic
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.http import HttpResponse
import logging

from apps.crs_documents.helpers.user_storage import get_user_storage, UserStorageManager

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_history_overview(request):
    """
    Get overview of user's storage and activity
    
    Returns:
        - profile: User storage profile
        - recent_uploads: Last 5 uploads
        - recent_exports: Last 5 exports
        - recent_activity: Last 10 activities
    """
    try:
        storage = get_user_storage(request.user)
        
        # Ensure user folders exist
        storage.ensure_user_folders()
        
        overview = {
            'profile': storage.get_user_profile() or {
                'user_id': request.user.id,
                'username': request.user.username,
                'total_uploads': 0,
                'total_exports': 0,
            },
            'recent_uploads': storage.get_user_uploads(limit=5),
            'recent_exports': storage.get_user_exports(limit=5),
            'recent_activity': storage.get_activity_history(days=7)[:10],
        }
        
        return Response({
            'success': True,
            'data': overview
        })
        
    except Exception as e:
        logger.error(f"Error getting user history overview: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_uploads(request):
    """
    Get list of user's uploaded files
    
    Query params:
        - limit: Maximum number of files (default 50)
    """
    try:
        limit = int(request.query_params.get('limit', 50))
        storage = get_user_storage(request.user)
        
        uploads = storage.get_user_uploads(limit=limit)
        
        return Response({
            'success': True,
            'count': len(uploads),
            'uploads': uploads
        })
        
    except Exception as e:
        logger.error(f"Error getting user uploads: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_exports(request):
    """
    Get list of user's exported files
    
    Query params:
        - limit: Maximum number of files (default 50)
        - format: Filter by format (xlsx, csv, json, pdf, docx)
    """
    try:
        limit = int(request.query_params.get('limit', 50))
        format_filter = request.query_params.get('format', None)
        
        storage = get_user_storage(request.user)
        exports = storage.get_user_exports(limit=limit)
        
        # Apply format filter if specified
        if format_filter:
            exports = [e for e in exports if e.get('format') == format_filter]
        
        return Response({
            'success': True,
            'count': len(exports),
            'exports': exports
        })
        
    except Exception as e:
        logger.error(f"Error getting user exports: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_profile(request):
    """Get user's storage profile"""
    try:
        storage = get_user_storage(request.user)
        storage.ensure_user_folders()
        
        profile = storage.get_user_profile()
        
        if profile:
            return Response({
                'success': True,
                'profile': profile
            })
        else:
            return Response({
                'success': True,
                'profile': {
                    'user_id': request.user.id,
                    'username': request.user.username,
                    'total_uploads': 0,
                    'total_exports': 0,
                    'message': 'New user - no history yet'
                }
            })
        
    except Exception as e:
        logger.error(f"Error getting user profile: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_activity(request):
    """
    Get user's activity history
    
    Query params:
        - days: Number of days of history (default 30)
        - action: Filter by action type (upload, export, download)
    """
    try:
        days = int(request.query_params.get('days', 30))
        action_filter = request.query_params.get('action', None)
        
        storage = get_user_storage(request.user)
        activities = storage.get_activity_history(days=days)
        
        # Apply action filter if specified
        if action_filter:
            activities = [a for a in activities if a.get('action') == action_filter]
        
        return Response({
            'success': True,
            'count': len(activities),
            'activities': activities
        })
        
    except Exception as e:
        logger.error(f"Error getting user activity: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_from_history(request):
    """
    Download a file from user's S3 storage
    
    Query params:
        - key: S3 key of the file to download
    """
    try:
        s3_key = request.query_params.get('key', '')
        
        if not s3_key:
            return Response({
                'success': False,
                'error': 'Missing s3 key parameter'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        storage = get_user_storage(request.user)
        
        # Security check - ensure user can only access their files
        if not s3_key.startswith(f"users/{request.user.id}/"):
            return Response({
                'success': False,
                'error': 'Access denied - file does not belong to user'
            }, status=status.HTTP_403_FORBIDDEN)
        
        file_buffer = storage.download_file(s3_key)
        
        if file_buffer is None:
            return Response({
                'success': False,
                'error': 'File not found or download failed'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Determine content type
        filename = s3_key.split('/')[-1]
        extension = filename.split('.')[-1].lower() if '.' in filename else ''
        
        content_types = {
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'csv': 'text/csv',
            'json': 'application/json',
            'pdf': 'application/pdf',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }
        
        content_type = content_types.get(extension, 'application/octet-stream')
        
        response = HttpResponse(
            file_buffer.getvalue(),
            content_type=content_type
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
