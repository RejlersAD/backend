"""
S3 PFD/PID API Views
Provides endpoints to interact with the existing S3 bucket structure
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.http import HttpResponse
import logging

from ..services.s3_pfd_manager import get_s3_pfd_manager

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_s3_pfd_files(request):
    """
    List all PFD files from S3 bucket
    GET /api/v1/pfd/s3/pfd-files/
    
    Query params:
        - prefix: Filter by filename prefix
        - limit: Max files to return (default 100)
    """
    try:
        prefix = request.query_params.get('prefix', '')
        limit = int(request.query_params.get('limit', 100))
        
        manager = get_s3_pfd_manager()
        files = manager.list_pfd_files(prefix=prefix, limit=limit)
        
        return Response({
            'success': True,
            'count': len(files),
            'files': files,
            'bucket_info': manager.get_bucket_structure_info()
        })
        
    except Exception as e:
        logger.error(f"Error listing S3 PFD files: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_s3_pid_files(request):
    """
    List all P&ID files from S3 bucket
    GET /api/v1/pfd/s3/pid-files/
    
    Query params:
        - prefix: Filter by filename prefix
        - limit: Max files to return (default 100)
    """
    try:
        prefix = request.query_params.get('prefix', '')
        limit = int(request.query_params.get('limit', 100))
        
        manager = get_s3_pfd_manager()
        files = manager.list_pid_files(prefix=prefix, limit=limit)
        
        return Response({
            'success': True,
            'count': len(files),
            'files': files,
            'bucket_info': manager.get_bucket_structure_info()
        })
        
    except Exception as e:
        logger.error(f"Error listing S3 P&ID files: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_s3_file(request):
    """
    Download a file from S3 (PFD or P&ID)
    GET /api/v1/pfd/s3/download/?key=<s3_key>
    """
    try:
        s3_key = request.query_params.get('key')
        
        if not s3_key:
            return Response({
                'success': False,
                'error': 'Missing s3_key parameter'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        manager = get_s3_pfd_manager()
        
        # Determine if it's PFD or PID based on key
        if '/PFD/' in s3_key:
            file_buffer = manager.get_pfd_file(s3_key)
        elif '/PID/' in s3_key:
            file_buffer = manager.get_pid_file(s3_key)
        else:
            return Response({
                'success': False,
                'error': 'Invalid S3 key - must be in PFD or PID folder'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if file_buffer is None:
            return Response({
                'success': False,
                'error': 'File not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Determine filename and content type
        filename = s3_key.split('/')[-1]
        content_type = manager._get_content_type(filename)
        
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


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_pfd_to_s3(request):
    """
    Upload a new PFD file to S3
    POST /api/v1/pfd/s3/upload-pfd/
    
    Body:
        - file: File upload
        - filename: Optional custom filename
        - metadata: Optional JSON metadata
    """
    try:
        uploaded_file = request.FILES.get('file')
        
        if not uploaded_file:
            return Response({
                'success': False,
                'error': 'No file provided'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get filename
        filename = request.POST.get('filename', uploaded_file.name)
        
        # Get metadata
        import json
        metadata = json.loads(request.POST.get('metadata', '{}'))
        metadata['uploaded_by'] = request.user.username
        metadata['user_id'] = request.user.id
        
        # Upload to S3
        manager = get_s3_pfd_manager()
        result = manager.upload_pfd_file(
            file_content=uploaded_file.read(),
            filename=filename,
            metadata=metadata
        )
        
        if result['success']:
            return Response({
                'success': True,
                'message': 'PFD uploaded successfully',
                'file': result
            })
        else:
            return Response({
                'success': False,
                'error': result.get('error', 'Upload failed')
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        logger.error(f"Error uploading PFD: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def convert_pfd_to_pid(request):
    """
    Convert a PFD to P&ID and save to S3
    POST /api/v1/pfd/s3/convert/
    
    Body:
        - pfd_key: S3 key of the PFD file
        - output_format: Format (pdf, dxf, png) - default pdf
        - conversion_options: Optional conversion settings
    """
    try:
        pfd_key = request.data.get('pfd_key')
        output_format = request.data.get('output_format', 'pdf')
        conversion_options = request.data.get('conversion_options', {})
        
        if not pfd_key:
            return Response({
                'success': False,
                'error': 'Missing pfd_key parameter'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        manager = get_s3_pfd_manager()
        
        # Get PFD file from S3
        pfd_buffer = manager.get_pfd_file(pfd_key)
        
        if pfd_buffer is None:
            return Response({
                'success': False,
                'error': 'PFD file not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # TODO: Implement actual conversion logic
        # For now, we'll create a placeholder conversion
        # Import the converter
        from apps.pfd.conversion.pfd_to_pid_converter import PFDToPIDConverter
        
        converter = PFDToPIDConverter()
        
        # Analyze PFD
        analysis = converter.analyze_pfd(pfd_buffer)
        
        # Generate P&ID content (placeholder - would be actual drawing)
        pid_content = f"P&ID Conversion from {pfd_key}\n".encode('utf-8')
        pid_content += f"Equipment: {len(analysis['equipment'])}\n".encode('utf-8')
        pid_content += f"Instruments: {len(analysis['instruments'])}\n".encode('utf-8')
        
        # Save P&ID to S3
        metadata = {
            'converted_by': request.user.username,
            'user_id': str(request.user.id),
            'equipment_count': str(len(analysis['equipment'])),
            'instrument_count': str(len(analysis['instruments']))
        }
        
        result = manager.save_pid_conversion(
            pfd_key=pfd_key,
            pid_content=pid_content,
            output_format=output_format,
            metadata=metadata
        )
        
        if result['success']:
            return Response({
                'success': True,
                'message': 'Conversion completed successfully',
                'pfd': {'s3_key': pfd_key},
                'pid': result,
                'analysis': analysis
            })
        else:
            return Response({
                'success': False,
                'error': result.get('error', 'Conversion failed')
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        logger.error(f"Error converting PFD to P&ID: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_conversion_pair(request):
    """
    Get both PFD and its corresponding P&ID
    GET /api/v1/pfd/s3/conversion-pair/?pfd_key=<key>
    """
    try:
        pfd_key = request.query_params.get('pfd_key')
        
        if not pfd_key:
            return Response({
                'success': False,
                'error': 'Missing pfd_key parameter'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        manager = get_s3_pfd_manager()
        pair = manager.get_conversion_pair(pfd_key)
        
        return Response({
            'success': True,
            'pfd': {
                's3_key': pair['pfd']['s3_key'],
                'exists': pair['pfd']['exists'],
                'download_url': manager.get_presigned_url(pair['pfd']['s3_key']) if pair['pfd']['exists'] else None
            },
            'pid': {
                's3_key': pair['pid']['s3_key'],
                'exists': pair['pid']['exists'],
                'download_url': manager.get_presigned_url(pair['pid']['s3_key']) if pair['pid']['exists'] else None
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting conversion pair: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_bucket_info(request):
    """
    Get information about the S3 bucket structure
    GET /api/v1/pfd/s3/bucket-info/
    """
    try:
        manager = get_s3_pfd_manager()
        info = manager.get_bucket_structure_info()
        
        return Response({
            'success': True,
            'bucket_info': info
        })
        
    except Exception as e:
        logger.error(f"Error getting bucket info: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_presigned_url(request):
    """
    Get a presigned URL for direct download
    GET /api/v1/pfd/s3/presigned-url/?key=<s3_key>&expiration=3600
    """
    try:
        s3_key = request.query_params.get('key')
        expiration = int(request.query_params.get('expiration', 3600))
        
        if not s3_key:
            return Response({
                'success': False,
                'error': 'Missing key parameter'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        manager = get_s3_pfd_manager()
        url = manager.get_presigned_url(s3_key, expiration)
        
        return Response({
            'success': True,
            'url': url,
            'expiration': expiration
        })
        
    except Exception as e:
        logger.error(f"Error generating presigned URL: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
