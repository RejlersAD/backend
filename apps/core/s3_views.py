"""
S3 API Views
REST API endpoints for S3 file operations
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import status
import logging

from .s3_service import get_s3_service, S3Service

logger = logging.getLogger(__name__)


class S3HealthCheckView(APIView):
    """Check S3 connection health"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Test S3 connectivity and return bucket info"""
        try:
            s3 = get_s3_service()
            size_info = s3.get_bucket_size()
            
            if size_info['success']:
                return Response({
                    'status': 'healthy',
                    'bucket': s3.bucket_name,
                    'region': s3.region,
                    'total_files': size_info['total_count'],
                    'total_size_mb': size_info['total_size_mb']
                })
            else:
                return Response({
                    'status': 'error',
                    'error': size_info.get('error')
                }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
                
        except Exception as e:
            logger.error(f"[S3HealthCheck] Error: {str(e)}")
            return Response({
                'status': 'error',
                'error': str(e)
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)


class S3FileListView(APIView):
    """List files in S3 bucket"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """
        List files in S3
        Query params:
        - folder_type: pid_drawings, crs_documents, etc.
        - prefix: Custom prefix filter
        - max_keys: Maximum number of files to return
        """
        try:
            s3 = get_s3_service()
            
            folder_type = request.query_params.get('folder_type')
            prefix = request.query_params.get('prefix')
            max_keys = int(request.query_params.get('max_keys', 100))
            
            result = s3.list_files(folder_type=folder_type, prefix=prefix, max_keys=max_keys)
            
            if result['success']:
                return Response(result)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.error(f"[S3FileList] Error: {str(e)}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class S3FileUploadView(APIView):
    """Upload files to S3"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def post(self, request):
        """
        Upload a file to S3
        Form data:
        - file: The file to upload
        - folder_type: Target folder (pid_drawings, crs_documents, etc.)
        - filename: Optional custom filename
        """
        try:
            s3 = get_s3_service()
            
            file_obj = request.FILES.get('file')
            if not file_obj:
                return Response({
                    'success': False,
                    'error': 'No file provided'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            folder_type = request.data.get('folder_type', 'temp')
            filename = request.data.get('filename')
            
            # Add user metadata
            metadata = {
                'uploaded_by': str(request.user.id),
                'uploaded_by_email': request.user.email,
            }
            
            result = s3.upload_file(
                file_obj=file_obj,
                folder_type=folder_type,
                filename=filename,
                metadata=metadata
            )
            
            if result['success']:
                logger.info(f"[S3Upload] User {request.user.email} uploaded file to {result['key']}")
                return Response(result, status=status.HTTP_201_CREATED)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.error(f"[S3Upload] Error: {str(e)}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class S3FileDownloadView(APIView):
    """Get presigned download URL for S3 file"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """
        Get presigned URL for file download
        Query params:
        - key: S3 object key
        - expiration: URL expiration in seconds (default 3600)
        """
        try:
            s3 = get_s3_service()
            
            s3_key = request.query_params.get('key')
            if not s3_key:
                return Response({
                    'success': False,
                    'error': 'S3 key is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            expiration = int(request.query_params.get('expiration', 3600))
            
            # Check if file exists
            if not s3.file_exists(s3_key):
                return Response({
                    'success': False,
                    'error': 'File not found'
                }, status=status.HTTP_404_NOT_FOUND)
            
            url = s3.get_presigned_url(s3_key, expiration)
            file_info = s3.get_file_info(s3_key)
            
            return Response({
                'success': True,
                'key': s3_key,
                'url': url,
                'expiration_seconds': expiration,
                'file_info': file_info if file_info['success'] else None
            })
            
        except Exception as e:
            logger.error(f"[S3Download] Error: {str(e)}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class S3FileDeleteView(APIView):
    """Delete files from S3"""
    permission_classes = [IsAuthenticated]
    
    def delete(self, request):
        """
        Delete a file from S3
        Query params:
        - key: S3 object key to delete
        """
        try:
            s3 = get_s3_service()
            
            s3_key = request.query_params.get('key')
            if not s3_key:
                return Response({
                    'success': False,
                    'error': 'S3 key is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Check if file exists
            if not s3.file_exists(s3_key):
                return Response({
                    'success': False,
                    'error': 'File not found'
                }, status=status.HTTP_404_NOT_FOUND)
            
            result = s3.delete_file(s3_key)
            
            if result['success']:
                logger.info(f"[S3Delete] User {request.user.email} deleted {s3_key}")
                return Response(result)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.error(f"[S3Delete] Error: {str(e)}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class S3PresignedUploadView(APIView):
    """Get presigned URL for direct browser upload to S3"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        """
        Get presigned POST URL for direct upload
        Body:
        - folder_type: Target folder
        - filename: Desired filename
        - content_type: MIME type of file
        """
        try:
            s3 = get_s3_service()
            
            folder_type = request.data.get('folder_type', 'temp')
            filename = request.data.get('filename')
            content_type = request.data.get('content_type')
            
            if not filename:
                return Response({
                    'success': False,
                    'error': 'Filename is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Build S3 key
            folder = s3._get_folder(folder_type)
            s3_key = f"{folder}{s3._generate_unique_filename(filename)}"
            
            result = s3.get_presigned_upload_url(s3_key, content_type)
            
            if result['success']:
                result['key'] = s3_key
                return Response(result)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.error(f"[S3PresignedUpload] Error: {str(e)}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class S3FileInfoView(APIView):
    """Get file metadata from S3"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """
        Get file info/metadata
        Query params:
        - key: S3 object key
        """
        try:
            s3 = get_s3_service()
            
            s3_key = request.query_params.get('key')
            if not s3_key:
                return Response({
                    'success': False,
                    'error': 'S3 key is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            result = s3.get_file_info(s3_key)
            
            if result['success']:
                return Response(result)
            else:
                return Response(result, status=status.HTTP_404_NOT_FOUND)
                
        except Exception as e:
            logger.error(f"[S3FileInfo] Error: {str(e)}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class S3FileCopyView(APIView):
    """Copy or move files within S3"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        """
        Copy or move a file within S3
        Body:
        - source_key: Source S3 key
        - dest_key: Destination S3 key
        - operation: 'copy' or 'move'
        """
        try:
            s3 = get_s3_service()
            
            source_key = request.data.get('source_key')
            dest_key = request.data.get('dest_key')
            operation = request.data.get('operation', 'copy')
            
            if not source_key or not dest_key:
                return Response({
                    'success': False,
                    'error': 'source_key and dest_key are required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            if operation == 'move':
                result = s3.move_file(source_key, dest_key)
            else:
                result = s3.copy_file(source_key, dest_key)
            
            if result['success']:
                logger.info(f"[S3{operation.title()}] User {request.user.email} {operation}d {source_key} to {dest_key}")
                return Response(result)
            else:
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.error(f"[S3Copy] Error: {str(e)}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class S3FolderListView(APIView):
    """List available S3 folders"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Get list of available folder types"""
        s3 = get_s3_service()
        
        return Response({
            'success': True,
            'folders': list(s3.FOLDERS.keys()),
            'folder_paths': s3.FOLDERS
        })
