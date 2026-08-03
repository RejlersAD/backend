"""
API Views for S3 Document Management
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .s3_document_manager import get_s3_manager


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def s3_storage_stats(request):
    """
    Get S3 storage statistics
    
    GET /api/v1/pfd/s3/stats/
    GET /api/v1/pfd/s3/stats/?project_code=ABC123
    """
    s3_manager = get_s3_manager()
    
    if not s3_manager.enabled:
        return Response({
            'success': False,
            'message': 'S3 storage is not enabled'
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    project_code = request.query_params.get('project_code')
    stats = s3_manager.get_storage_stats(project_code)
    
    if 'error' in stats:
        return Response({
            'success': False,
            'error': stats['error']
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return Response({
        'success': True,
        'stats': stats,
        'project_code': project_code if project_code else 'all'
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_project_documents(request):
    """
    List documents for a project
    
    GET /api/v1/pfd/s3/documents/?project_code=ABC123
    GET /api/v1/pfd/s3/documents/?project_code=ABC123&type=pfd
    """
    s3_manager = get_s3_manager()
    
    if not s3_manager.enabled:
        return Response({
            'success': False,
            'message': 'S3 storage is not enabled'
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    project_code = request.query_params.get('project_code')
    doc_type = request.query_params.get('type')
    
    if not project_code:
        return Response({
            'success': False,
            'error': 'project_code parameter is required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    documents = s3_manager.list_project_documents(project_code, doc_type)
    
    return Response({
        'success': True,
        'project_code': project_code,
        'document_type': doc_type if doc_type else 'all',
        'count': len(documents),
        'documents': documents
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_document_url(request):
    """
    Generate presigned URL for document access
    
    POST /api/v1/pfd/s3/generate-url/
    Body: { "s3_key": "documents/pfd/2026/01/ABC123/file.pdf", "expiration": 3600 }
    """
    s3_manager = get_s3_manager()
    
    if not s3_manager.enabled:
        return Response({
            'success': False,
            'message': 'S3 storage is not enabled'
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    s3_key = request.data.get('s3_key')
    expiration = request.data.get('expiration', 3600)
    
    if not s3_key:
        return Response({
            'success': False,
            'error': 's3_key is required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    url = s3_manager.generate_presigned_url(s3_key, expiration)
    
    if not url:
        return Response({
            'success': False,
            'error': 'Failed to generate presigned URL'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return Response({
        'success': True,
        's3_key': s3_key,
        'url': url,
        'expiration_seconds': expiration
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_s3_document(request, s3_key):
    """
    Delete document from S3
    
    DELETE /api/v1/pfd/s3/delete/<s3_key>/
    """
    s3_manager = get_s3_manager()
    
    if not s3_manager.enabled:
        return Response({
            'success': False,
            'message': 'S3 storage is not enabled'
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    # Security: Check if user has permission to delete
    # You can add more sophisticated permission checks here
    
    success = s3_manager.delete_document(s3_key)
    
    if success:
        return Response({
            'success': True,
            'message': 'Document deleted successfully'
        })
    else:
        return Response({
            'success': False,
            'error': 'Failed to delete document'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
