"""
PFD Converter History API
Provides user-specific PFD upload and conversion history with download capabilities
Implements soft-coded, reusable patterns
"""
import logging
from datetime import datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Avg
from .models import PFDDocument, PIDConversion
from django.http import FileResponse, HttpResponse

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pfd_history_overview(request):
    """
    Get user's PFD conversion history overview
    
    GET /api/v1/pfd/history/
    """
    try:
        user = request.user
        
        # Get user statistics
        pfd_docs = PFDDocument.objects.filter(uploaded_by=user)
        conversions = PIDConversion.objects.filter(pfd_document__uploaded_by=user)
        
        completed_conversions = conversions.filter(status='completed')
        avg_confidence = completed_conversions.aggregate(Avg('confidence_score'))['confidence_score__avg'] or 0
        
        # Recent uploads
        recent_uploads = pfd_docs.order_by('-uploaded_at')[:10].values(
            'id',
            'document_number',
            'document_title',
            'revision',
            'project_name',
            'file',
            'uploaded_at',
            'file_type'
        )
        
        # Recent conversions
        recent_conversions = []
        for conversion in conversions.select_related('pfd_document').order_by('-created_at')[:10]:
            recent_conversions.append({
                'id': conversion.id,
                'pfd_id': conversion.pfd_document.id,
                'document_number': conversion.pid_drawing_number,
                'document_title': conversion.pid_title,
                'filename': f"{conversion.pid_drawing_number}.pdf",
                'status': conversion.status,
                'confidence_score': conversion.confidence_score,
                'created_at': conversion.created_at.isoformat(),
                'can_download': conversion.status == 'completed'
            })
        
        return Response({
            'success': True,
            'data': {
                'profile': {
                    'username': user.get_full_name() or user.email or user.username,
                    'email': user.email,
                    'total_uploads': pfd_docs.count(),
                    'total_conversions': conversions.count(),
                    'completed_conversions': completed_conversions.count(),
                    'avg_confidence_score': round(avg_confidence, 1)
                },
                'recent_uploads': list(recent_uploads),
                'recent_conversions': recent_conversions
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error fetching PFD history overview: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pfd_all_uploads(request):
    """
    Get all user's PFD uploads with pagination
    
    GET /api/v1/pfd/history/uploads/?page=1&limit=50
    """
    try:
        user = request.user
        page = int(request.query_params.get('page', 1))
        limit = int(request.query_params.get('limit', 50))
        offset = (page - 1) * limit
        
        pfd_docs = PFDDocument.objects.filter(uploaded_by=user).order_by('-uploaded_at')
        total_count = pfd_docs.count()
        
        docs_page = pfd_docs[offset:offset + limit]
        
        uploads_data = []
        for doc in docs_page:
            # Check if conversion exists
            has_conversion = PIDConversion.objects.filter(pfd_document=doc).exists()
            latest_conversion = PIDConversion.objects.filter(pfd_document=doc).order_by('-created_at').first()
            
            uploads_data.append({
                'id': doc.id,
                'document_number': doc.document_number,
                'document_title': doc.document_title,
                'revision': doc.revision,
                'project_name': doc.project_name,
                'file_type': doc.file_type,
                'uploaded_at': doc.uploaded_at.isoformat(),
                'has_conversion': has_conversion,
                'conversion_id': latest_conversion.id if latest_conversion else None,
                'conversion_status': latest_conversion.status if latest_conversion else None,
                'confidence_score': latest_conversion.confidence_score if latest_conversion else None
            })
        
        return Response({
            'success': True,
            'data': {
                'uploads': uploads_data,
                'pagination': {
                    'page': page,
                    'limit': limit,
                    'total_count': total_count,
                    'total_pages': (total_count + limit - 1) // limit
                }
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error fetching PFD uploads: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pfd_all_conversions(request):
    """
    Get all user's PFD to P&ID conversions
    
    GET /api/v1/pfd/history/conversions/?page=1&limit=50
    """
    try:
        user = request.user
        page = int(request.query_params.get('page', 1))
        limit = int(request.query_params.get('limit', 50))
        offset = (page - 1) * limit
        
        conversions = PIDConversion.objects.filter(
            pfd_document__uploaded_by=user
        ).select_related('pfd_document').order_by('-created_at')
        
        total_count = conversions.count()
        conversions_page = conversions[offset:offset + limit]
        
        conversions_data = []
        for conversion in conversions_page:
            conversions_data.append({
                'id': conversion.id,
                'pfd_id': conversion.pfd_document.id,
                'document_number': conversion.pid_drawing_number,
                'document_title': conversion.pid_title,
                'project_name': conversion.pfd_document.project_name,
                'revision': conversion.pfd_document.revision,
                'status': conversion.status,
                'confidence_score': conversion.confidence_score,
                'created_at': conversion.created_at.isoformat(),
                'updated_at': conversion.updated_at.isoformat(),
                'can_download': conversion.status == 'completed',
                'filename': f"{conversion.pid_drawing_number}.pdf"
            })
        
        return Response({
            'success': True,
            'data': {
                'conversions': conversions_data,
                'pagination': {
                    'page': page,
                    'limit': limit,
                    'total_count': total_count,
                    'total_pages': (total_count + limit - 1) // limit
                }
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error fetching PFD conversions: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_pfd_file(request, pfd_id):
    """
    Download original PFD file
    
    GET /api/v1/pfd/history/download/pfd/{pfd_id}/
    """
    try:
        # Security: Only allow user to download their own files
        pfd_doc = PFDDocument.objects.filter(
            id=pfd_id,
            uploaded_by=request.user
        ).first()
        
        if not pfd_doc:
            return Response({
                'success': False,
                'error': 'File not found or access denied'
            }, status=status.HTTP_404_NOT_FOUND)
        
        if not pfd_doc.file:
            return Response({
                'success': False,
                'error': 'File not available'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Return file for download
        try:
            filename = f"{pfd_doc.document_number}_{pfd_doc.file_type}.{pfd_doc.file_type}"
            response = FileResponse(
                pfd_doc.file.open('rb'),
                content_type=f'application/{pfd_doc.file_type}'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as file_error:
            logger.error(f"Error opening file: {str(file_error)}")
            return Response({
                'success': False,
                'error': 'File could not be opened'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        logger.error(f"Error downloading PFD file: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_converted_pid(request, conversion_id):
    """
    Download converted P&ID drawing
    
    GET /api/v1/pfd/history/download/pid/{conversion_id}/
    """
    try:
        # Security: Only allow user to download their own conversions
        conversion = PIDConversion.objects.filter(
            id=conversion_id,
            pfd_document__uploaded_by=request.user
        ).select_related('pfd_document').first()
        
        if not conversion:
            return Response({
                'success': False,
                'error': 'Conversion not found or access denied'
            }, status=status.HTTP_404_NOT_FOUND)
        
        if conversion.status != 'completed':
            return Response({
                'success': False,
                'error': 'Conversion not yet completed'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not conversion.pid_file:
            return Response({
                'success': False,
                'error': 'P&ID file not available'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Return P&ID PDF for download
        try:
            filename = f"{conversion.pid_drawing_number}_PID.pdf"
            response = FileResponse(
                conversion.pid_file.open('rb'),
                content_type='application/pdf'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as file_error:
            logger.error(f"Error opening P&ID file: {str(file_error)}")
            return Response({
                'success': False,
                'error': 'P&ID file could not be opened'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        logger.error(f"Error downloading converted P&ID: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_pfd_document(request, pfd_id):
    """
    Delete a PFD document and its associated conversions
    
    DELETE /api/v1/pfd/history/delete/pfd/{pfd_id}/
    """
    try:
        # Security: Only allow user to delete their own files
        pfd_doc = PFDDocument.objects.filter(
            id=pfd_id,
            uploaded_by=request.user
        ).first()
        
        if not pfd_doc:
            return Response({
                'success': False,
                'error': 'File not found or access denied'
            }, status=status.HTTP_404_NOT_FOUND)
        
        document_number = pfd_doc.document_number
        
        # Delete file from storage (S3 or local)
        if pfd_doc.file:
            pfd_doc.file.delete(save=False)
        
        # Delete document (cascades to conversions)
        pfd_doc.delete()
        
        logger.info(f"User {request.user.id} deleted PFD document {pfd_id}: {document_number}")
        
        return Response({
            'success': True,
            'message': f'Document "{document_number}" deleted successfully'
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error deleting PFD document: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
