"""
PID Analysis History API
Provides user-specific upload and analysis history with download capabilities
Implements soft-coded, reusable patterns
"""
import logging
from datetime import datetime, timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q, Count, Avg
from django.utils import timezone
from .models import PIDDrawing, PIDAnalysisReport
from django.http import FileResponse, HttpResponse
import os

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pid_history_overview(request):
    """
    Get user's PID analysis history overview
    
    GET /api/v1/pid/history/
    
    Returns:
        {
            "success": true,
            "data": {
                "profile": {
                    "username": str,
                    "total_uploads": int,
                    "total_analyses": int,
                    "total_issues_found": int,
                    "avg_issues_per_drawing": float
                },
                "recent_uploads": [...],
                "recent_analyses": [...]
            }
        }
    """
    try:
        user = request.user
        
        # Get user statistics
        drawings = PIDDrawing.objects.filter(uploaded_by=user)
        reports = PIDAnalysisReport.objects.filter(pid_drawing__uploaded_by=user)
        
        total_issues = sum(report.total_issues for report in reports)
        avg_issues = reports.aggregate(Avg('total_issues'))['total_issues__avg'] or 0
        
        # Recent uploads (last 10)
        recent_uploads = drawings.order_by('-uploaded_at')[:10].values(
            'id',
            'original_filename',
            'drawing_number',
            'drawing_title',
            'revision',
            'project_name',
            'file_size',
            'uploaded_at',
            'status'
        )
        
        # Recent analyses (last 10 completed)
        recent_analyses = []
        for report in reports.select_related('pid_drawing').order_by('-created_at')[:10]:
            recent_analyses.append({
                'id': report.id,
                'drawing_id': report.pid_drawing.id,
                'drawing_number': report.pid_drawing.drawing_number,
                'drawing_title': report.pid_drawing.drawing_title,
                'filename': report.pid_drawing.original_filename,
                'total_issues': report.total_issues,
                'created_at': report.created_at.isoformat(),
                'can_download': True
            })
        
        return Response({
            'success': True,
            'data': {
                'profile': {
                    'username': user.get_full_name() or user.email or user.username,
                    'email': user.email,
                    'total_uploads': drawings.count(),
                    'total_analyses': reports.count(),
                    'total_issues_found': total_issues,
                    'avg_issues_per_drawing': round(avg_issues, 1)
                },
                'recent_uploads': list(recent_uploads),
                'recent_analyses': recent_analyses
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error fetching PID history overview: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pid_all_uploads(request):
    """
    Get all user's PID uploads with pagination
    
    GET /api/v1/pid/history/uploads/?page=1&limit=50
    """
    try:
        user = request.user
        page = int(request.query_params.get('page', 1))
        limit = int(request.query_params.get('limit', 50))
        offset = (page - 1) * limit
        
        # Get all uploads for user
        drawings = PIDDrawing.objects.filter(uploaded_by=user).order_by('-uploaded_at')
        total_count = drawings.count()
        
        drawings_page = drawings[offset:offset + limit]
        
        uploads_data = []
        for drawing in drawings_page:
            # Check if analysis exists
            has_analysis = hasattr(drawing, 'analysis_report')
            report_id = drawing.analysis_report.id if has_analysis else None
            total_issues = drawing.analysis_report.total_issues if has_analysis else 0
            
            uploads_data.append({
                'id': drawing.id,
                'filename': drawing.original_filename,
                'drawing_number': drawing.drawing_number,
                'drawing_title': drawing.drawing_title,
                'revision': drawing.revision,
                'project_name': drawing.project_name,
                'file_size': drawing.file_size,
                'uploaded_at': drawing.uploaded_at.isoformat(),
                'status': drawing.status,
                'has_analysis': has_analysis,
                'report_id': report_id,
                'total_issues': total_issues,
                'file_url': drawing.file.url if drawing.file else None
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
        logger.error(f"Error fetching PID uploads: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pid_all_analyses(request):
    """
    Get all user's completed analyses
    
    GET /api/v1/pid/history/analyses/?page=1&limit=50
    """
    try:
        user = request.user
        page = int(request.query_params.get('page', 1))
        limit = int(request.query_params.get('limit', 50))
        offset = (page - 1) * limit
        
        # Get all reports for user
        reports = PIDAnalysisReport.objects.filter(
            pid_drawing__uploaded_by=user
        ).select_related('pid_drawing').order_by('-created_at')
        
        total_count = reports.count()
        reports_page = reports[offset:offset + limit]
        
        analyses_data = []
        for report in reports_page:
            analyses_data.append({
                'id': report.id,
                'drawing_id': report.pid_drawing.id,
                'drawing_number': report.pid_drawing.drawing_number,
                'drawing_title': report.pid_drawing.drawing_title,
                'filename': report.pid_drawing.original_filename,
                'project_name': report.pid_drawing.project_name,
                'revision': report.pid_drawing.revision,
                'total_issues': report.total_issues,
                'created_at': report.created_at.isoformat(),
                'analysis_duration': str(report.pid_drawing.analysis_completed_at - report.pid_drawing.analysis_started_at) if report.pid_drawing.analysis_completed_at and report.pid_drawing.analysis_started_at else None,
                'can_download_pdf': True,
                'can_download_excel': True,
                'can_view_online': True
            })
        
        return Response({
            'success': True,
            'data': {
                'analyses': analyses_data,
                'pagination': {
                    'page': page,
                    'limit': limit,
                    'total_count': total_count,
                    'total_pages': (total_count + limit - 1) // limit
                }
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error fetching PID analyses: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_pid_drawing(request, drawing_id):
    """
    Download original PID drawing file
    
    GET /api/v1/pid/history/download/drawing/{drawing_id}/
    """
    try:
        # Security: Only allow user to download their own drawings
        drawing = PIDDrawing.objects.filter(
            id=drawing_id,
            uploaded_by=request.user
        ).first()
        
        if not drawing:
            return Response({
                'success': False,
                'error': 'Drawing not found or access denied'
            }, status=status.HTTP_404_NOT_FOUND)
        
        if not drawing.file:
            return Response({
                'success': False,
                'error': 'File not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Return file for download
        try:
            response = FileResponse(
                drawing.file.open('rb'),
                content_type='application/pdf'
            )
            response['Content-Disposition'] = f'attachment; filename="{drawing.original_filename}"'
            return response
        except Exception as file_error:
            logger.error(f"Error opening file: {str(file_error)}")
            return Response({
                'success': False,
                'error': 'File could not be opened'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        logger.error(f"Error downloading PID drawing: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_pid_report(request, report_id):
    """
    Download analysis report (PDF/Excel/CSV)
    
    GET /api/v1/pid/history/download/report/{report_id}/?format=pdf
    """
    try:
        # Security: Only allow user to download their own reports
        report = PIDAnalysisReport.objects.filter(
            id=report_id,
            pid_drawing__uploaded_by=request.user
        ).select_related('pid_drawing').first()
        
        if not report:
            return Response({
                'success': False,
                'error': 'Report not found or access denied'
            }, status=status.HTTP_404_NOT_FOUND)
        
        export_format = request.query_params.get('format', 'pdf')
        
        # Use existing export service
        from .export_service import PIDReportExportService
        
        exporter = PIDReportExportService(report)
        
        if export_format == 'pdf':
            file_content, filename = exporter.export_to_pdf()
            content_type = 'application/pdf'
        elif export_format == 'excel':
            file_content, filename = exporter.export_to_excel()
            content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif export_format == 'csv':
            file_content, filename = exporter.export_to_csv()
            content_type = 'text/csv'
        else:
            return Response({
                'success': False,
                'error': 'Invalid format. Use: pdf, excel, or csv'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Return file
        response = HttpResponse(file_content, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Exception as e:
        logger.error(f"Error downloading PID report: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_pid_drawing(request, drawing_id):
    """
    Delete a PID drawing and its associated analysis
    
    DELETE /api/v1/pid/history/delete/drawing/{drawing_id}/
    """
    try:
        # Security: Only allow user to delete their own drawings
        drawing = PIDDrawing.objects.filter(
            id=drawing_id,
            uploaded_by=request.user
        ).first()
        
        if not drawing:
            return Response({
                'success': False,
                'error': 'Drawing not found or access denied'
            }, status=status.HTTP_404_NOT_FOUND)
        
        filename = drawing.original_filename
        
        # Delete file from storage (S3 or local)
        if drawing.file:
            drawing.file.delete(save=False)
        
        # Delete drawing (cascades to report and issues)
        drawing.delete()
        
        logger.info(f"User {request.user.id} deleted PID drawing {drawing_id}: {filename}")
        
        return Response({
            'success': True,
            'message': f'Drawing "{filename}" deleted successfully'
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error deleting PID drawing: {str(e)}", exc_info=True)
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
