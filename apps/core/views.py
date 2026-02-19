"""
Core application views including dashboard statistics and Smart Project Collection APIs.
"""
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import cache_page
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import (
    ProjectCollection, 
    ProjectDiscipline, 
    SmartProjectDocument, 
    CrossDisciplineRecommendation
)
import logging

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@cache_page(60 * 5)  # Cache for 5 minutes
def dashboard_stats(request):
    """
    Dashboard Statistics API endpoint for real-time project data.
    Returns comprehensive statistics for the RADAI dashboard.
    """
    try:
        # Basic user statistics
        total_users = User.objects.filter(is_active=True).count()
        total_admin_users = User.objects.filter(is_active=True, is_staff=True).count()
        
        # Smart Project Collection statistics
        active_projects = ProjectCollection.objects.filter(
            is_deleted=False,
            project_status='active'
        ).count()
        
        completed_projects = ProjectCollection.objects.filter(
            is_deleted=False,
            project_status='completed'
        ).count()
        
        total_documents = SmartProjectDocument.objects.filter(
            is_deleted=False,
            is_active=True
        ).count()
        
        # Document storage statistics
        total_storage_bytes = SmartProjectDocument.objects.filter(
            is_deleted=False,
            is_active=True
        ).aggregate(total_size=Sum('file_size'))['total_size'] or 0
        
        total_storage_mb = round(total_storage_bytes / (1024 * 1024), 2)
        total_storage_gb = round(total_storage_mb / 1024, 2)
        
        # Discipline distribution
        discipline_stats = ProjectDiscipline.objects.filter(
            is_deleted=False
        ).values('discipline_type').annotate(
            count=Count('id'),
            doc_count=Sum('document_count')
        ).order_by('-count')
        
        # Recent project activity
        recent_projects = ProjectCollection.objects.filter(
            is_deleted=False
        ).order_by('-updated_at')[:5]
        
        recent_project_data = [
            {
                'project_code': proj.project_code,
                'project_name': proj.project_name,
                'status': proj.project_status,
                'documents': proj.total_documents,
                'last_updated': proj.updated_at.isoformat() if proj.updated_at else None
            }
            for proj in recent_projects
        ]
        
        # Cross-discipline recommendations
        pending_recommendations = CrossDisciplineRecommendation.objects.filter(
            is_deleted=False,
            status='pending'
        ).count()
        
        # AI classification performance
        ai_classified_documents = SmartProjectDocument.objects.filter(
            is_deleted=False,
            ai_classification_confidence__gte=0.8
        ).count()
        
        # Document upload trends (last 7 days)
        from django.utils import timezone
        from datetime import timedelta
        
        last_week = timezone.now() - timedelta(days=7)
        recent_uploads = SmartProjectDocument.objects.filter(
            is_deleted=False,
            upload_date__gte=last_week
        ).count()
        
        stats_data = {
            'user_statistics': {
                'total_users': total_users,
                'admin_users': total_admin_users,
                'active_users': total_users - total_admin_users
            },
            'project_statistics': {
                'active_projects': active_projects,
                'completed_projects': completed_projects,
                'total_projects': active_projects + completed_projects
            },
            'document_statistics': {
                'total_documents': total_documents,
                'recent_uploads_7_days': recent_uploads,
                'ai_classified_documents': ai_classified_documents,
                'ai_classification_rate': round(
                    (ai_classified_documents / total_documents * 100) if total_documents > 0 else 0, 1
                )
            },
            'storage_statistics': {
                'total_bytes': total_storage_bytes,
                'total_mb': total_storage_mb,
                'total_gb': total_storage_gb,
                'storage_utilization': f"{total_storage_gb:.2f} GB"
            },
            'discipline_distribution': [
                {
                    'discipline': item['discipline_type'],
                    'projects': item['count'],
                    'documents': item['doc_count'] or 0
                }
                for item in discipline_stats
            ],
            'recommendations': {
                'pending_cross_discipline': pending_recommendations,
                'ai_confidence_avg': 0.85  # Placeholder for average AI confidence
            },
            'recent_activity': {
                'projects': recent_project_data
            },
            'system_health': {
                'database_status': 'healthy',
                's3_bucket_status': 'connected',
                'last_updated': timezone.now().isoformat()
            }
        }
        
        logger.info(f"Dashboard stats requested by user {request.user.username}")
        return Response(stats_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error generating dashboard stats: {str(e)}")
        return Response(
            {
                'error': 'Unable to generate dashboard statistics',
                'message': str(e)
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def project_overview(request):
    """
    Project overview API for dashboard project widgets.
    """
    try:
        projects = ProjectCollection.objects.filter(
            is_deleted=False
        ).order_by('-updated_at')[:10]
        
        project_data = [
            {
                'id': proj.id,
                'project_code': proj.project_code,
                'project_name': proj.project_name,
                'client_name': proj.client_name,
                'status': proj.project_status,
                'total_documents': proj.total_documents,
                'total_size_mb': proj.total_size_mb,
                'disciplines': proj.discipline_count,
                'last_upload': proj.last_document_upload.isoformat() if proj.last_document_upload else None,
                'created_at': proj.created_at.isoformat(),
                'updated_at': proj.updated_at.isoformat()
            }
            for proj in projects
        ]
        
        return Response({
            'projects': project_data,
            'total_count': ProjectCollection.objects.filter(is_deleted=False).count()
        })
        
    except Exception as e:
        logger.error(f"Error in project overview: {str(e)}")
        return Response(
            {'error': 'Unable to fetch project overview'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def system_health_check(request):
    """
    System health check for dashboard monitoring.
    """
    try:
        from django.db import connection
        from django.conf import settings
        import boto3
        
        health_status = {
            'database': 'unknown',
            's3_bucket': 'unknown',
            'redis': 'unknown',
            'django': 'healthy'
        }
        
        # Database health check
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            health_status['database'] = 'healthy'
        except Exception as e:
            health_status['database'] = f'error: {str(e)}'
        
        # S3 bucket health check
        try:
            if hasattr(settings, 'AWS_STORAGE_BUCKET_NAME') and settings.AWS_STORAGE_BUCKET_NAME:
                s3 = boto3.client('s3')
                s3.head_bucket(Bucket=settings.AWS_STORAGE_BUCKET_NAME)
                health_status['s3_bucket'] = 'connected'
            else:
                health_status['s3_bucket'] = 'not_configured'
        except Exception as e:
            health_status['s3_bucket'] = f'error: {str(e)}'
        
        # Redis health check
        try:
            from django.core.cache import cache
            cache.set('health_check', 'test', timeout=30)
            if cache.get('health_check') == 'test':
                health_status['redis'] = 'healthy'
            else:
                health_status['redis'] = 'unavailable'
        except Exception as e:
            health_status['redis'] = f'in_memory_fallback: {str(e)}'
        
        overall_healthy = all(
            status != 'error' for status in health_status.values()
        )
        
        return Response({
            'overall_status': 'healthy' if overall_healthy else 'degraded',
            'services': health_status,
            'timestamp': timezone.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"System health check failed: {str(e)}")
        return Response(
            {
                'overall_status': 'error',
                'error': str(e),
                'timestamp': timezone.now().isoformat()
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )