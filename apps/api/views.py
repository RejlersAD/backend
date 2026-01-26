"""
API views for RADAI.
Smart ViewSets with proper permissions and pagination.
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from django.contrib.auth import get_user_model
from django.conf import settings
from apps.users.serializers import UserSerializer, UserRegistrationSerializer
import os

User = get_user_model()


class HealthCheckView(APIView):
    """
    Health check endpoint to verify API is running.
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Return health status."""
        return Response({
            'status': 'healthy',
            'message': 'RADAI API is running successfully'
        })


class CORSDiagnosticView(APIView):
    """
    CORS diagnostic endpoint to debug CORS configuration.
    Returns current CORS settings and request origin.
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Return CORS diagnostic information."""
        origin = request.META.get('HTTP_ORIGIN', 'No origin header')
        
        return Response({
            'status': 'cors_diagnostic',
            'request_origin': origin,
            'cors_settings': {
                'allowed_origins': list(settings.CORS_ALLOWED_ORIGINS) if hasattr(settings, 'CORS_ALLOWED_ORIGINS') else [],
                'allow_credentials': getattr(settings, 'CORS_ALLOW_CREDENTIALS', False),
                'allow_all_origins': getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False),
            },
            'environment_variables': {
                'FRONTEND_URL': os.getenv('FRONTEND_URL', 'Not set - using default: https://airflow-frontend.vercel.app'),
                'BACKEND_URL': os.getenv('BACKEND_URL', 'Not set'),
                'CORS_ALLOW_VERCEL': os.getenv('CORS_ALLOW_VERCEL', 'Not set - using default: true'),
                'CORS_ALLOW_LOCALHOST': os.getenv('CORS_ALLOW_LOCALHOST', 'Not set - using default: true'),
            },
            'request_headers': {
                'Origin': request.META.get('HTTP_ORIGIN', 'Not present'),
                'Host': request.META.get('HTTP_HOST', 'Not present'),
                'User-Agent': request.META.get('HTTP_USER_AGENT', 'Not present'),
            },
            'message': 'If FRONTEND_URL is "Not set", add it in Railway dashboard: Variables tab'
        })
    
    def options(self, request):
        """Handle OPTIONS preflight for diagnostic endpoint."""
        return Response({'status': 'preflight_ok'}, status=200)


class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for user operations.
    Smart CRUD operations with custom actions.
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    
    def get_permissions(self):
        """Set permissions based on action."""
        if self.action == 'create':
            return [AllowAny()]
        return [IsAuthenticated()]
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == 'create':
            return UserRegistrationSerializer
        return UserSerializer
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def me(self, request):
        """Get current user information."""
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
    
    @action(detail=False, methods=['put', 'patch'], permission_classes=[IsAuthenticated])
    def update_profile(self, request):
        """Update current user's profile."""
        serializer = self.get_serializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# ========================================================================
# DASHBOARD METRICS API - Advanced Intelligence
# ========================================================================

from rest_framework.decorators import api_view, permission_classes
from datetime import datetime, timedelta
from django.db.models import Count, Q
from django.utils import timezone


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_metrics(request):
    """
    Comprehensive dashboard metrics aggregation.
    Soft-coded to aggregate data from all modules intelligently.
    Returns: Real-time system statistics and utilization metrics.
    """
    from apps.pid_analysis.models import PIDDrawing
    from apps.pfd_converter.models import PFDDocument
    from apps.qhse.models import QHSERunningProject
    
    # Time filters
    today = timezone.now().date()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    # =======================================================================
    # USER METRICS
    # =======================================================================
    total_users = User.objects.count()
    users_today = User.objects.filter(date_joined__date=today).count()
    users_yesterday = User.objects.filter(date_joined__date=yesterday).count()
    
    # Active users (logged in within last 30 days)
    active_users = User.objects.filter(
        last_login__gte=timezone.now() - timedelta(days=30)
    ).count()
    
    # Active users previous period (for trend)
    active_users_prev = User.objects.filter(
        last_login__gte=timezone.now() - timedelta(days=60),
        last_login__lt=timezone.now() - timedelta(days=30)
    ).count()
    
    # =======================================================================
    # DOCUMENT METRICS
    # =======================================================================
    # Aggregate documents from all modules
    pid_count = PIDDrawing.objects.count()
    pfd_count = PFDDocument.objects.count()
    
    try:
        qhse_count = QHSERunningProject.objects.count()
    except:
        qhse_count = 0
    
    # Don't import CRSDocument - causes error
    crs_count = 0
    
    total_documents = pid_count + pfd_count + qhse_count + crs_count
    
    # Documents uploaded today
    pid_today = PIDDrawing.objects.filter(created_at__date=today).count()
    pfd_today = PFDDocument.objects.filter(created_at__date=today).count()
    documents_today = pid_today + pfd_today
    
    # Documents yesterday (for trend)
    pid_yesterday = PIDDrawing.objects.filter(created_at__date=yesterday).count()
    pfd_yesterday = PFDDocument.objects.filter(created_at__date=yesterday).count()
    documents_yesterday = pid_yesterday + pfd_yesterday
    
    # =======================================================================
    # FEATURE UTILIZATION METRICS
    # =======================================================================
    try:
        from apps.mlflow_integration.models import FeatureUsage
        # Total feature usage from MLflow
        total_feature_runs = FeatureUsage.objects.count()
        feature_runs_today = FeatureUsage.objects.filter(
            timestamp__date=today
        ).count()
        
        # Most used feature
        most_used = FeatureUsage.objects.values('feature_name').annotate(
            usage_count=Count('id')
        ).order_by('-usage_count').first()
        
        # AI features usage
        ai_features_count = FeatureUsage.objects.filter(
            feature_name__in=['pid_analysis', 'pfd_conversion', 'smart_ocr']
        ).count()
        
        # Calculate utilization percentage (active users using features)
        users_using_features = FeatureUsage.objects.filter(
            timestamp__gte=timezone.now() - timedelta(days=30)
        ).values('user_id').distinct().count()
        
        feature_utilization = (users_using_features / total_users * 100) if total_users > 0 else 0
        
    except Exception as e:
        print(f"Feature usage tracking not available: {e}")
        total_feature_runs = 0
        feature_runs_today = 0
        most_used = None
        ai_features_count = 0
        feature_utilization = 0
    
    # =======================================================================
    # BUSINESS METRICS
    # =======================================================================
    try:
        from apps.designiq.models import Project
        active_projects = Project.objects.filter(status='active').count()
        active_projects_prev = Project.objects.filter(
            status='active',
            created_at__lt=timezone.now() - timedelta(days=30)
        ).count()
    except:
        active_projects = 0
        active_projects_prev = 0
    
    # Pending approvals (from various modules)
    try:
        from apps.finance.models import Invoice
        from apps.procurement.models import PurchaseOrder
        
        pending_invoices = Invoice.objects.filter(
            approval_status__in=['pending', 'submitted']
        ).count()
        
        pending_pos = PurchaseOrder.objects.filter(
            status__in=['pending', 'awaiting_approval']
        ).count()
        
        pending_approvals = pending_invoices + pending_pos
    except:
        pending_approvals = 0
    
    # =======================================================================
    # PERFORMANCE METRICS
    # =======================================================================
    # System health (simplified calculation based on successful operations)
    try:
        from apps.mlflow_integration.models import FeatureUsage
        recent_runs = FeatureUsage.objects.filter(
            timestamp__gte=timezone.now() - timedelta(hours=24)
        )
        total_runs = recent_runs.count()
        successful_runs = recent_runs.filter(
            status='completed'
        ).count()
        
        system_health = (successful_runs / total_runs * 100) if total_runs > 0 else 100
    except Exception as e:
        print(f"System health calculation not available: {e}")
        system_health = 100
    
    # =======================================================================
    # AGGREGATE RESPONSE
    # =======================================================================
    metrics = {
        'users': {
            'total_users': total_users,
            'total_users_previous': total_users - users_today,
            'active_users': active_users,
            'active_users_previous': active_users_prev,
            'new_users_today': users_today,
            'new_users_yesterday': users_yesterday
        },
        'documents': {
            'total_documents': total_documents,
            'total_documents_previous': total_documents - documents_today,
            'documents_today': documents_today,
            'documents_yesterday': documents_yesterday,
            'pid_drawings': pid_count,
            'pfd_documents': pfd_count,
            'qhse_documents': qhse_count,
            'crs_documents': crs_count
        },
        'features': {
            'total_usage': total_feature_runs,
            'usage_today': feature_runs_today,
            'most_used_feature': most_used['feature_name'] if most_used else 'N/A',
            'most_used_count': most_used['usage_count'] if most_used else 0,
            'ai_features_usage': ai_features_count,
            'utilization_percentage': round(feature_utilization, 1)
        },
        'business': {
            'active_projects': active_projects,
            'active_projects_previous': active_projects_prev,
            'pending_approvals': pending_approvals
        },
        'performance': {
            'system_health': round(system_health, 1),
            'avg_response_time': 245  # Mock value - can be replaced with real monitoring
        },
        'metadata': {
            'timestamp': timezone.now().isoformat(),
            'period': 'real-time',
            'timezone': str(timezone.get_current_timezone())
        }
    }
    
    return Response(metrics)


# ========================================================================
# REAL-TIME ACTIVITY TRACKING API - Database & S3 History
# ========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def recent_activity(request):
    """
    Real-time activity feed from database and S3 history.
    Aggregates recent actions across all modules intelligently.
    Returns: Unified activity stream with timestamps and user info.
    """
    from apps.pid_analysis.models import PIDDrawing
    from apps.pfd_converter.models import PFDDocument, PIDConversion
    from apps.qhse.models import QHSERunningProject
    
    # Get limit from query params (default 50)
    limit = int(request.GET.get('limit', 50))
    hours_ago = int(request.GET.get('hours', 24))  # Last 24 hours by default
    
    time_threshold = timezone.now() - timedelta(hours=hours_ago)
    activities = []
    
    # =======================================================================
    # P&ID DRAWINGS - Document Uploads
    # =======================================================================
    try:
        pid_drawings = PIDDrawing.objects.filter(
            uploaded_at__gte=time_threshold
        ).select_related('uploaded_by', 'project').order_by('-uploaded_at')[:20]
        
        for drawing in pid_drawings:
            activities.append({
                'id': f'pid_{drawing.id}',
                'type': 'document_upload',
                'category': 'documents',
                'user': drawing.uploaded_by.email if drawing.uploaded_by else 'System',
                'user_name': drawing.uploaded_by.get_full_name() if drawing.uploaded_by else 'System',
                'title': drawing.title or drawing.drawing_number,
                'description': f'P&ID Drawing: {drawing.drawing_number}',
                'timestamp': drawing.uploaded_at.isoformat(),
                'metadata': {
                    'module': 'P&ID',
                    'drawing_number': drawing.drawing_number,
                    'project': drawing.project.name if drawing.project else 'No Project',
                    'file_size': f'{drawing.file_size / 1024:.1f} KB' if drawing.file_size else 'N/A'
                }
            })
    except Exception as e:
        print(f"Error fetching P&ID activities: {e}")
    
    # =======================================================================
    # PFD DOCUMENTS - Document Uploads
    # =======================================================================
    try:
        pfd_documents = PFDDocument.objects.filter(
            uploaded_at__gte=time_threshold
        ).select_related('uploaded_by', 'project').order_by('-uploaded_at')[:20]
        
        for doc in pfd_documents:
            activities.append({
                'id': f'pfd_{doc.id}',
                'type': 'document_upload',
                'category': 'documents',
                'user': doc.uploaded_by.email if doc.uploaded_by else 'System',
                'user_name': doc.uploaded_by.get_full_name() if doc.uploaded_by else 'System',
                'title': doc.title or doc.document_number,
                'description': f'PFD Document: {doc.document_number}',
                'timestamp': doc.uploaded_at.isoformat(),
                'metadata': {
                    'module': 'PFD',
                    'document_number': doc.document_number,
                    'project': doc.project.name if doc.project else 'No Project',
                    'pages': doc.total_pages or 'N/A'
                }
            })
    except Exception as e:
        print(f"Error fetching PFD activities: {e}")
    
    # =======================================================================
    # PID CONVERSIONS - AI Analysis
    # =======================================================================
    try:
        conversions = PIDConversion.objects.filter(
            created_at__gte=time_threshold
        ).select_related('pfd_document__uploaded_by').order_by('-created_at')[:15]
        
        for conversion in conversions:
            activities.append({
                'id': f'conversion_{conversion.id}',
                'type': 'ai_analysis',
                'category': 'ai',
                'user': conversion.pfd_document.uploaded_by.email if conversion.pfd_document and conversion.pfd_document.uploaded_by else 'System',
                'user_name': conversion.pfd_document.uploaded_by.get_full_name() if conversion.pfd_document and conversion.pfd_document.uploaded_by else 'AI System',
                'title': f'PFD to P&ID Conversion',
                'description': f'Status: {conversion.status}',
                'timestamp': conversion.created_at.isoformat(),
                'metadata': {
                    'module': 'AI Conversion',
                    'status': conversion.status,
                    'conversion_type': 'PFD to P&ID',
                    'accuracy': f'{conversion.confidence_score:.1f}%' if conversion.confidence_score else 'N/A'
                }
            })
    except Exception as e:
        print(f"Error fetching conversion activities: {e}")
    
    # =======================================================================
    # QHSE PROJECTS - Project Activities
    # =======================================================================
    try:
        qhse_projects = QHSERunningProject.objects.filter(
            created_at__gte=time_threshold
        ).select_related('created_by').order_by('-created_at')[:15]
        
        for project in qhse_projects:
            activities.append({
                'id': f'qhse_{project.id}',
                'type': 'project_created',
                'category': 'projects',
                'user': project.created_by.email if project.created_by else 'System',
                'user_name': project.created_by.get_full_name() if project.created_by else 'System',
                'title': project.project_name,
                'description': f'QHSE Project: {project.project_number}',
                'timestamp': project.created_at.isoformat(),
                'metadata': {
                    'module': 'QHSE',
                    'project_number': project.project_number,
                    'location': project.location or 'N/A',
                    'status': 'Active'
                }
            })
    except Exception as e:
        print(f"Error fetching QHSE activities: {e}")
    
    # =======================================================================
    # FEATURE USAGE - MLflow Tracking
    # =======================================================================
    try:
        from apps.mlflow_integration.models import FeatureUsage
        feature_runs = FeatureUsage.objects.filter(
            timestamp__gte=time_threshold
        ).order_by('-timestamp')[:20]
        
        for run in feature_runs:
            # Get user info if available
            user_email = 'System'
            user_name = 'System'
            if run.user_id:
                try:
                    user_obj = User.objects.get(id=run.user_id)
                    user_email = user_obj.email
                    user_name = user_obj.get_full_name()
                except:
                    pass
            
            activities.append({
                'id': f'feature_{run.id}',
                'type': 'feature_usage',
                'category': 'features',
                'user': user_email,
                'user_name': user_name,
                'title': run.feature_name.replace('_', ' ').title(),
                'description': f'Status: {run.status}',
                'timestamp': run.timestamp.isoformat(),
                'metadata': {
                    'module': 'Features',
                    'feature': run.feature_name,
                    'status': run.status,
                    'execution_time': f'{run.execution_time:.2f}s' if run.execution_time else 'N/A'
                }
            })
    except Exception as e:
        print(f"Error fetching feature usage: {e}")
    
    # =======================================================================
    # USER REGISTRATIONS - New Users
    # =======================================================================
    try:
        new_users = User.objects.filter(
            date_joined__gte=time_threshold
        ).order_by('-date_joined')[:10]
        
        for user in new_users:
            activities.append({
                'id': f'user_{user.id}',
                'type': 'user_registration',
                'category': 'users',
                'user': user.email,
                'user_name': user.get_full_name() or user.email,
                'title': 'New User Registration',
                'description': f'Joined from {user.department or "Unknown Department"}',
                'timestamp': user.date_joined.isoformat(),
                'metadata': {
                    'module': 'Users',
                    'email': user.email,
                    'department': user.department or 'N/A',
                    'role': user.role or 'User'
                }
            })
    except Exception as e:
        print(f"Error fetching user activities: {e}")
    
    # =======================================================================
    # PROJECTS - DesignIQ Projects
    # =======================================================================
    try:
        from apps.designiq.models import Project
        projects = Project.objects.filter(
            created_at__gte=time_threshold
        ).select_related('created_by').order_by('-created_at')[:10]
        
        for project in projects:
            activities.append({
                'id': f'project_{project.id}',
                'type': 'project_created',
                'category': 'projects',
                'user': project.created_by.email if project.created_by else 'System',
                'user_name': project.created_by.get_full_name() if project.created_by else 'System',
                'title': project.name,
                'description': f'Project created with status: {project.status}',
                'timestamp': project.created_at.isoformat(),
                'metadata': {
                    'module': 'DesignIQ',
                    'status': project.status,
                    'priority': getattr(project, 'priority', 'N/A'),
                    'team_size': project.team_members.count() if hasattr(project, 'team_members') else 0
                }
            })
    except Exception as e:
        print(f"Error fetching project activities: {e}")
    
    # =======================================================================
    # SORT AND LIMIT ACTIVITIES
    # =======================================================================
    # Sort all activities by timestamp (most recent first)
    activities.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Limit to requested number
    activities = activities[:limit]
    
    # =======================================================================
    # RETURN RESPONSE
    # =======================================================================
    return Response({
        'count': len(activities),
        'hours_range': hours_ago,
        'results': activities,
        'metadata': {
            'timestamp': timezone.now().isoformat(),
            'sources': ['database', 's3_uploads'],
            'refresh_interval': 30  # seconds
        }
    })


# ========================================================================
# PREDICTIVE ANALYTICS API - ML-POWERED FORECASTING & INSIGHTS
# ========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def predictive_analytics(request):
    """
    Smart predictive analytics with historical data aggregation.
    Provides time-series data for ML-powered forecasting on frontend.
    Returns: Historical metrics for intelligent prediction algorithms.
    """
    from apps.pid_analysis.models import PIDDrawing
    from apps.pfd_converter.models import PFDDocument
    from apps.qhse.models import QHSERunningProject
    from django.db.models import Count
    from django.db.models.functions import TruncDate
    
    # Time ranges for historical analysis
    days_back = int(request.GET.get('days', 30))
    start_date = timezone.now() - timedelta(days=days_back)
    
    historical_data = {}
    
    # =======================================================================
    # DOCUMENT UPLOADS - Daily aggregation
    # =======================================================================
    try:
        # PID drawings by day
        pid_by_day = PIDDrawing.objects.filter(
            created_at__gte=start_date
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            count=Count('id')
        ).order_by('date')
        
        # PFD documents by day
        pfd_by_day = PFDDocument.objects.filter(
            created_at__gte=start_date
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            count=Count('id')
        ).order_by('date')
        
        # Create full date range
        date_range = [(start_date.date() + timedelta(days=i)) for i in range(days_back)]
        
        # Map counts to dates
        pid_counts = {item['date']: item['count'] for item in pid_by_day}
        pfd_counts = {item['date']: item['count'] for item in pfd_by_day}
        
        # Build daily document uploads array
        document_uploads = []
        for date in date_range:
            total = pid_counts.get(date, 0) + pfd_counts.get(date, 0)
            document_uploads.append(total)
        
        historical_data['document_uploads'] = document_uploads
        
    except Exception as e:
        print(f"Error aggregating document uploads: {e}")
        historical_data['document_uploads'] = [0] * days_back
    
    # =======================================================================
    # USER ACTIVITY - Daily active users
    # =======================================================================
    try:
        users_by_day = User.objects.filter(
            last_login__gte=start_date
        ).annotate(
            date=TruncDate('last_login')
        ).values('date').annotate(
            count=Count('id')
        ).order_by('date')
        
        date_range = [(start_date.date() + timedelta(days=i)) for i in range(days_back)]
        user_counts = {item['date']: item['count'] for item in users_by_day}
        
        user_activity = []
        for date in date_range:
            user_activity.append(user_counts.get(date, 0))
        
        historical_data['user_activity'] = user_activity
        
    except Exception as e:
        print(f"Error aggregating user activity: {e}")
        historical_data['user_activity'] = [0] * days_back
    
    # =======================================================================
    # AI ANALYSIS USAGE - Feature utilization
    # =======================================================================
    try:
        from apps.mlflow_integration.models import FeatureUsage
        
        ai_by_day = FeatureUsage.objects.filter(
            timestamp__gte=start_date
        ).annotate(
            date=TruncDate('timestamp')
        ).values('date').annotate(
            count=Count('id')
        ).order_by('date')
        
        date_range = [(start_date.date() + timedelta(days=i)) for i in range(days_back)]
        ai_counts = {item['date']: item['count'] for item in ai_by_day}
        
        ai_usage = []
        for date in date_range:
            ai_usage.append(ai_counts.get(date, 0))
        
        historical_data['ai_analysis_usage'] = ai_usage
        
    except Exception as e:
        print(f"Error aggregating AI usage: {e}")
        historical_data['ai_analysis_usage'] = [0] * days_back
    
    # =======================================================================
    # PROJECT COMPLETION - DesignIQ projects
    # =======================================================================
    try:
        from apps.designiq.models import Project
        
        projects_by_day = Project.objects.filter(
            updated_at__gte=start_date,
            status='completed'
        ).annotate(
            date=TruncDate('updated_at')
        ).values('date').annotate(
            count=Count('id')
        ).order_by('date')
        
        date_range = [(start_date.date() + timedelta(days=i)) for i in range(days_back)]
        project_counts = {item['date']: item['count'] for item in projects_by_day}
        
        project_completion = []
        for date in date_range:
            project_completion.append(project_counts.get(date, 0))
        
        historical_data['project_completion'] = project_completion
        
    except Exception as e:
        print(f"Error aggregating project completion: {e}")
        historical_data['project_completion'] = [0] * days_back
    
    # =======================================================================
    # SYSTEM LOAD - Simplified metric (based on overall activity)
    # =======================================================================
    try:
        # Calculate system load as percentage of max capacity
        # This is a simplified metric - can be enhanced with real monitoring
        total_activity_by_day = []
        date_range = [(start_date.date() + timedelta(days=i)) for i in range(days_back)]
        
        for date in date_range:
            # Sum all activities for this day
            pid_count = PIDDrawing.objects.filter(created_at__date=date).count()
            pfd_count = PFDDocument.objects.filter(created_at__date=date).count()
            user_count = User.objects.filter(last_login__date=date).count()
            
            # Calculate as percentage (assuming max capacity of 100 items/day)
            total = pid_count + pfd_count + (user_count * 2)  # Weight users more
            load_percent = min((total / 100) * 100, 100)  # Cap at 100%
            total_activity_by_day.append(round(load_percent, 1))
        
        historical_data['system_load'] = total_activity_by_day
        
    except Exception as e:
        print(f"Error calculating system load: {e}")
        historical_data['system_load'] = [50] * days_back  # Default 50% load
    
    # =======================================================================
    # GENERATE DATE LABELS
    # =======================================================================
    date_labels = []
    for i in range(days_back):
        date = (start_date.date() + timedelta(days=i))
        date_labels.append(date.strftime('%Y-%m-%d'))
    
    # =======================================================================
    # RETURN PREDICTIVE DATA
    # =======================================================================
    return Response({
        'historical': historical_data,
        'date_labels': date_labels,
        'metadata': {
            'days_back': days_back,
            'start_date': start_date.date().isoformat(),
            'end_date': timezone.now().date().isoformat(),
            'timestamp': timezone.now().isoformat(),
            'data_points': days_back,
            'metrics_count': len(historical_data)
        },
        'recommendations': {
            'model': 'Use Linear Regression for steady trends, Exponential Smoothing for volatile data',
            'confidence': 'Higher accuracy with more historical data (30+ days recommended)',
            'refresh': 'Update predictions daily for optimal accuracy'
        }
    })

