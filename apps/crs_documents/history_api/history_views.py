"""
User History API Views - CRS Documents
Provides API endpoints for user file history and activity tracking

Endpoints:
- GET /api/v1/crs/documents/history/ - Get user's activity history
- GET /api/v1/crs/documents/history/uploads/ - Get user's uploaded files
- GET /api/v1/crs/documents/history/exports/ - Get user's exported files
- GET /api/v1/crs/documents/history/profile/ - Get user's storage profile
- GET /api/v1/crs/documents/history/download/<path>/ - Download a file from history
- DELETE /api/v1/crs/documents/history/delete/ - Delete a file from storage
- POST /api/v1/crs/documents/history/share/ - Generate shareable link
- GET /api/v1/crs/documents/history/config/ - Get history actions configuration

Does NOT modify existing views or core logic
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.http import HttpResponse
import logging
import json
import os
from datetime import datetime, timedelta

from apps.crs_documents.helpers.user_storage import get_user_storage, UserStorageManager

logger = logging.getLogger(__name__)

# Load history configuration
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'history_config.json')
try:
    with open(CONFIG_PATH, 'r') as f:
        HISTORY_CONFIG = json.load(f)
except Exception as e:
    logger.warning(f"Failed to load history config: {e}, using defaults")
    HISTORY_CONFIG = {
        "upload_actions": {"download": {"enabled": True}, "delete": {"enabled": True}},
        "export_actions": {"download": {"enabled": True}, "delete": {"enabled": True}},
        "security": {"allow_delete": True, "require_confirmation_for_delete": True}
    }


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


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_history_config(request):
    """
    Get history actions configuration
    Returns enabled actions and UI preferences
    """
    try:
        return Response({
            'success': True,
            'config': HISTORY_CONFIG
        })
    except Exception as e:
        logger.error(f"Error getting history config: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_from_history(request):
    """
    Delete a file from user's S3 storage
    
    Body params:
        - key: S3 key of the file to delete
    """
    try:
        # Check if delete is allowed
        if not HISTORY_CONFIG.get('security', {}).get('allow_delete', True):
            return Response({
                'success': False,
                'error': 'Delete operation is disabled'
            }, status=status.HTTP_403_FORBIDDEN)
        
        s3_key = request.data.get('key', '')
        
        if not s3_key:
            return Response({
                'success': False,
                'error': 'Missing s3 key parameter'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        storage = get_user_storage(request.user)
        
        # Security check - ensure user can only delete their files
        if not s3_key.startswith(f"users/{request.user.id}/"):
            return Response({
                'success': False,
                'error': 'Access denied - file does not belong to user'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Delete file from S3
        result = storage.delete_file(s3_key)
        
        if result:
            # Log activity
            storage.log_activity('delete', {
                'file_key': s3_key,
                'timestamp': datetime.now().isoformat()
            })
            
            return Response({
                'success': True,
                'message': 'File deleted successfully'
            })
        else:
            return Response({
                'success': False,
                'error': 'Failed to delete file'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_share_link(request):
    """
    Generate a presigned URL for sharing a file
    
    Body params:
        - key: S3 key of the file to share
        - duration_hours: Link validity duration (default: 1 hour, max: 24 hours)
    """
    try:
        # Check if share links are allowed
        if not HISTORY_CONFIG.get('security', {}).get('allow_share_links', True):
            return Response({
                'success': False,
                'error': 'Share links are disabled'
            }, status=status.HTTP_403_FORBIDDEN)
        
        s3_key = request.data.get('key', '')
        duration_hours = int(request.data.get('duration_hours', 1))
        
        # Enforce max duration from config
        max_duration = HISTORY_CONFIG.get('security', {}).get('max_share_link_duration_hours', 1)
        duration_hours = min(duration_hours, max_duration)
        
        if not s3_key:
            return Response({
                'success': False,
                'error': 'Missing s3 key parameter'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        storage = get_user_storage(request.user)
        
        # Security check
        if not s3_key.startswith(f"users/{request.user.id}/"):
            return Response({
                'success': False,
                'error': 'Access denied - file does not belong to user'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Generate presigned URL
        share_url = storage.generate_presigned_url(s3_key, expiration=duration_hours * 3600)
        
        if share_url:
            expiration_time = datetime.now() + timedelta(hours=duration_hours)
            
            # Log activity
            storage.log_activity('share', {
                'file_key': s3_key,
                'timestamp': datetime.now().isoformat(),
                'expires_at': expiration_time.isoformat()
            })
            
            return Response({
                'success': True,
                'share_url': share_url,
                'expires_at': expiration_time.isoformat(),
                'expires_in_hours': duration_hours
            })
        else:
            return Response({
                'success': False,
                'error': 'Failed to generate share link'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        logger.error(f"Error generating share link: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_file_metadata(request):
    """
    Get detailed metadata for a file
    
    Query params:
        - key: S3 key of the file
    """
    try:
        s3_key = request.query_params.get('key', '')
        
        if not s3_key:
            return Response({
                'success': False,
                'error': 'Missing s3 key parameter'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        storage = get_user_storage(request.user)
        
        # Security check
        if not s3_key.startswith(f"users/{request.user.id}/"):
            return Response({
                'success': False,
                'error': 'Access denied'
            }, status=status.HTTP_403_FORBIDDEN)
        
        metadata = storage.get_file_metadata(s3_key)
        
        if metadata:
            return Response({
                'success': True,
                'metadata': metadata
            })
        else:
            return Response({
                'success': False,
                'error': 'File not found or failed to get metadata'
            }, status=status.HTTP_404_NOT_FOUND)
        
    except Exception as e:
        logger.error(f"Error getting file metadata: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
