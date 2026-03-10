"""
Usage Tracking API Views

ENDPOINTS:
- GET /api/v1/usage/user/{id} - User-specific usage
- GET /api/v1/usage/department/{name} - Department usage
- GET /api/v1/usage/feature/{feature} - Feature usage
- GET /api/v1/usage/summary - Global summary
- GET /api/v1/usage/sales-report - Sales/management report
- GET /api/v1/usage/top-users - Top users ranking
- GET /api/v1/usage/trends - Usage trends over time
"""

import logging
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Count, Sum, Avg, Q, F
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import UserUsageLog, DepartmentUsageSummary, FeatureUsageSummary
from .serializers import (
    UserUsageLogSerializer,
    DepartmentUsageSummarySerializer,
    FeatureUsageSummarySerializer,
    UserUsageSummarySerializer,
    TopUsersSerializer,
    UsageSummarySerializer,
    SalesReportSerializer,
    UsageTimeSeriesSerializer,
)
from .permissions import (
    IsAdminOrOwn,
    IsAdminOrDepartmentHead,
    IsAdminOnly,
    CanViewUsageData,
)

User = get_user_model()
logger = logging.getLogger(__name__)


class UsageTrackingViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Main ViewSet for usage tracking data.
    
    Provides comprehensive usage analytics with security controls.
    """
    
    queryset = UserUsageLog.objects.all()
    serializer_class = UserUsageLogSerializer
    permission_classes = [IsAuthenticated, CanViewUsageData]
    
    def get_queryset(self):
        """Filter queryset based on user permissions"""
        user = self.request.user
        
        # Admins see everything
        if user.is_staff or user.is_superuser:
            return UserUsageLog.objects.all()
        
        # Department heads see their department
        if self._is_department_head(user):
            user_dept = self._get_user_department(user)
            return UserUsageLog.objects.filter(department=user_dept)
        
        # Regular users see only their own data
        return UserUsageLog.objects.filter(user=user)
    
    @action(detail=False, methods=['get'], url_path='user/(?P<user_id>[0-9]+)')
    def user_usage(self, request, user_id=None):
        """
        GET /api/v1/usage/user/{id}
        
        Get detailed usage statistics for a specific user.
        """
        try:
            # Security check
            if not (request.user.is_staff or request.user.is_superuser or str(request.user.id) == user_id):
                return Response(
                    {'error': 'Permission denied'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            target_user = User.objects.get(id=user_id)
            
            # Try cache first
            cache_key = f"usage:user_summary:{user_id}"
            cached_data = cache.get(cache_key)
            if cached_data:
                return Response(cached_data)
            
            # Calculate statistics
            logs = UserUsageLog.objects.filter(user=target_user)
            
            stats = logs.aggregate(
                total_requests=Count('id'),
                total_tokens=Sum('tokens_used'),
                total_time=Sum('processing_time'),
                avg_time=Avg('processing_time'),
                total_errors=Count('id', filter=Q(status='error')),
            )
            
            # Most used features
            features = logs.values('feature_name').annotate(
                count=Count('id')
            ).order_by('-count')[:5]
            
            # Period-specific stats
            today = timezone.now().date()
            today_requests = logs.filter(timestamp__date=today).count()
            
            month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0)
            month_requests = logs.filter(timestamp__gte=month_start).count()
            
            # Calculate rates
            total_requests = stats['total_requests'] or 0
            error_rate = 0
            if total_requests > 0:
                error_rate = (stats['total_errors'] / total_requests) * 100
            
            # Prepare response
            data = {
                'user_id': target_user.id,
                'username': target_user.username,
                'email': target_user.email,
                'department': self._get_user_department(target_user),
                'total_requests': total_requests,
                'total_tokens': stats['total_tokens'] or 0,
                'total_processing_time': round(stats['total_time'] or 0, 2),
                'avg_processing_time': round(stats['avg_time'] or 0, 3),
                'total_errors': stats['total_errors'] or 0,
                'error_rate': round(error_rate, 2),
                'success_rate': round(100 - error_rate, 2),
                'today_requests': today_requests,
                'this_month_requests': month_requests,
                'most_used_features': list(features),
                'last_activity': logs.order_by('-timestamp').first().timestamp if logs.exists() else None,
            }
            
            serializer = UserUsageSummarySerializer(data)
            
            # Cache for 5 minutes
            cache.set(cache_key, serializer.data, 300)
            
            return Response(serializer.data)
            
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"[UsageTracking] Error in user_usage: {e}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='department/(?P<department>[^/]+)',
            permission_classes=[IsAuthenticated, IsAdminOrDepartmentHead])
    def department_usage(self, request, department=None):
        """
        GET /api/v1/usage/department/{name}
        
        Get usage statistics for a specific department.
        """
        try:
            # Security check for department heads
            if not (request.user.is_staff or request.user.is_superuser):
                user_dept = self._get_user_department(request.user)
                if user_dept != department:
                    return Response(
                        {'error': 'Permission denied'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # Get or create summary
            summary, created = DepartmentUsageSummary.objects.get_or_create(
                department=department
            )
            
            # Update if stale (older than 10 minutes)
            if created or (timezone.now() - summary.last_updated) > timedelta(minutes=10):
                summary.update_metrics()
            
            serializer = DepartmentUsageSummarySerializer(summary)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"[UsageTracking] Error in department_usage: {e}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='feature/(?P<feature_name>[^/]+)')
    def feature_usage(self, request, feature_name=None):
        """
        GET /api/v1/usage/feature/{feature}
        
        Get usage statistics for a specific feature.
        """
        try:
            # Get or create summary
            summary, created = FeatureUsageSummary.objects.get_or_create(
                feature_name=feature_name
            )
            
            # Update if stale
            if created or (timezone.now() - summary.last_updated) > timedelta(minutes=10):
                summary.update_metrics()
            
            serializer = FeatureUsageSummarySerializer(summary)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"[UsageTracking] Error in feature_usage: {e}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated, IsAdminOnly])
    def summary(self, request):
        """
        GET /api/v1/usage/summary
        
        Get global usage summary for management dashboard.
        Admin only.
        """
        try:
            # Check cache first
            cache_key = "usage:global_summary"
            cached_data = cache.get(cache_key)
            if cached_data:
                return Response(cached_data)
            
            # Calculate global statistics
            all_logs = UserUsageLog.objects.all()
            
            stats = all_logs.aggregate(
                total_requests=Count('id'),
                unique_users=Count('user', distinct=True),
                unique_departments=Count('department', distinct=True),
                unique_features=Count('feature_name', distinct=True),
                total_tokens=Sum('tokens_used'),
                total_time=Sum('processing_time'),
                avg_time=Avg('processing_time'),
                total_errors=Count('id', filter=Q(status='error')),
            )
            
            # Calculate rates
            total_req = stats['total_requests'] or 0
            error_rate = 0
            if total_req > 0:
                error_rate = (stats['total_errors'] / total_req) * 100
            
            # Period statistics
            today = timezone.now().date()
            week_ago = timezone.now() - timedelta(days=7)
            month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0)
            
            today_requests = all_logs.filter(timestamp__date=today).count()
            week_requests = all_logs.filter(timestamp__gte=week_ago).count()
            month_requests = all_logs.filter(timestamp__gte=month_start).count()
            
            # Top departments
            top_depts = DepartmentUsageSummary.objects.order_by('-total_requests')[:5]
            top_depts_data = [
                {
                    'department': d.department,
                    'total_requests': d.total_requests,
                    'total_tokens': d.total_tokens,
                    'total_users': d.total_users,
                }
                for d in top_depts
            ]
            
            # Top features
            top_features = FeatureUsageSummary.objects.order_by('-popularity_score')[:5]
            top_features_data = [
                {
                    'feature_name': f.feature_name,
                    'total_requests': f.total_requests,
                    'total_tokens': f.total_tokens,
                    'total_users': f.total_users,
                }
                for f in top_features
            ]
            
            # Top users
            top_users_data = self._get_top_users(5)
            
            # Daily trend (last 7 days)
            daily_trend = self._get_daily_trend(7)
            
            # Hourly distribution (today)
            hourly_dist = self._get_hourly_distribution()
            
            # Prepare response
            data = {
                'total_requests': total_req,
                'total_users': stats['unique_users'] or 0,
                'total_departments': stats['unique_departments'] or 0,
                'total_features': stats['unique_features'] or 0,
                'total_tokens': stats['total_tokens'] or 0,
                'total_processing_time': round(stats['total_time'] or 0, 2),
                'avg_processing_time': round(stats['avg_time'] or 0, 3),
                'total_errors': stats['total_errors'] or 0,
                'error_rate': round(error_rate, 2),
                'success_rate': round(100 - error_rate, 2),
                'today_requests': today_requests,
                'this_week_requests': week_requests,
                'this_month_requests': month_requests,
                'top_departments': top_depts_data,
                'top_features': top_features_data,
                'top_users': top_users_data,
                'daily_trend': daily_trend,
                'hourly_distribution': hourly_dist,
            }
            
            serializer = UsageSummarySerializer(data)
            
            # Cache for 5 minutes
            cache.set(cache_key, serializer.data, 300)
            
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"[UsageTracking] Error in summary: {e}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated, IsAdminOnly])
    def sales_report(self, request):
        """
        GET /api/v1/usage/sales-report?type=monthly
        
        Generate sales/management report with insights.
        Admin only.
        
        Query params:
        - type: 'daily', 'weekly', 'monthly' (default: 'monthly')
        """
        try:
            report_type = request.query_params.get('type', 'monthly')
            
            # Determine date range
            end_date = timezone.now()
            if report_type == 'daily':
                start_date = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
            elif report_type == 'weekly':
                start_date = end_date - timedelta(days=7)
            else:  # monthly
                start_date = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            # Current period stats
            current_logs = UserUsageLog.objects.filter(timestamp__gte=start_date)
            current_stats = current_logs.aggregate(
                total_requests=Count('id'),
                unique_users=Count('user', distinct=True),
                total_tokens=Sum('tokens_used'),
            )
            
            # Previous period stats (for growth calculation)
            period_length = (end_date - start_date).days
            prev_start = start_date - timedelta(days=period_length)
            prev_end = start_date
            
            prev_logs = UserUsageLog.objects.filter(
                timestamp__gte=prev_start,
                timestamp__lt=prev_end
            )
            prev_stats = prev_logs.aggregate(
                total_requests=Count('id'),
                unique_users=Count('user', distinct=True),
                total_tokens=Sum('tokens_used'),
            )
            
            # Calculate growth
            user_growth = self._calculate_growth(
                prev_stats['unique_users'], 
                current_stats['unique_users']
            )
            request_growth = self._calculate_growth(
                prev_stats['total_requests'],
                current_stats['total_requests']
            )
            token_growth = self._calculate_growth(
                prev_stats['total_tokens'],
                current_stats['total_tokens']
            )
            
            # Department breakdown
            dept_stats = current_logs.values('department').annotate(
                requests=Count('id'),
                tokens=Sum('tokens_used'),
                users=Count('user', distinct=True)
            ).order_by('-requests')
            
            # Feature adoption
            feature_stats = current_logs.values('feature_name').annotate(
                requests=Count('id'),
                tokens=Sum('tokens_used'),
                users=Count('user', distinct=True)
            ).order_by('-requests')
            
            # User engagement levels
            user_request_counts = current_logs.values('user').annotate(
                count=Count('id')
            )
            
            high_engagement = sum(1 for u in user_request_counts if u['count'] > 50)
            medium_engagement = sum(1 for u in user_request_counts if 10 <= u['count'] <= 50)
            low_engagement = sum(1 for u in user_request_counts if u['count'] < 10)
            
            # Performance metrics
            perf_stats = current_logs.aggregate(
                avg_time=Avg('processing_time'),
                error_count=Count('id', filter=Q(status='error')),
            )
            
            total_curr = current_stats['total_requests'] or 1
            reliability = ((total_curr - (perf_stats['error_count'] or 0)) / total_curr) * 100
            
            # Generate insights
            insights = self._generate_insights(
                current_stats,
                prev_stats,
                dept_stats,
                feature_stats,
                user_request_counts
            )
            
            # Prepare response
            data = {
                'report_date': end_date.date(),
                'report_type': report_type,
                'total_active_users': current_stats['unique_users'] or 0,
                'total_requests': current_stats['total_requests'] or 0,
                'total_tokens_consumed': current_stats['total_tokens'] or 0,
                'total_departments': current_logs.values('department').distinct().count(),
                'user_growth': round(user_growth, 2),
                'request_growth': round(request_growth, 2),
                'token_growth': round(token_growth, 2),
                'department_stats': list(dept_stats),
                'feature_stats': list(feature_stats),
                'high_engagement_users': high_engagement,
                'medium_engagement_users': medium_engagement,
                'low_engagement_users': low_engagement,
                'avg_response_time': round(perf_stats['avg_time'] or 0, 3),
                'system_reliability': round(reliability, 2),
                'insights': insights,
            }
            
            serializer = SalesReportSerializer(data)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"[UsageTracking] Error in sales_report: {e}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def top_users(self, request):
        """
        GET /api/v1/usage/top-users?limit=10
        
        Get top users by request count.
        """
        try:
            limit = int(request.query_params.get('limit', 10))
            
            # Check permissions
            if not (request.user.is_staff or request.user.is_superuser):
                # Department heads see their department only
                if self._is_department_head(request.user):
                    user_dept = self._get_user_department(request.user)
                    top_users = self._get_top_users(limit, department=user_dept)
                else:
                    return Response(
                        {'error': 'Permission denied'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            else:
                top_users = self._get_top_users(limit)
            
            serializer = TopUsersSerializer(top_users, many=True)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"[UsageTracking] Error in top_users: {e}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def trends(self, request):
        """
        GET /api/v1/usage/trends?days=30&granularity=daily
        
        Get usage trends over time.
        
        Query params:
        - days: Number of days to look back (default: 30)
        - granularity: 'hourly' or 'daily' (default: 'daily')
        """
        try:
            days = int(request.query_params.get('days', 30))
            granularity = request.query_params.get('granularity', 'daily')
            
            if granularity == 'hourly':
                trend_data = self._get_hourly_trend(days)
            else:
                trend_data = self._get_daily_trend(days)
            
            serializer = UsageTimeSeriesSerializer(trend_data, many=True)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"[UsageTracking] Error in trends: {e}", exc_info=True)
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    # ========== Helper Methods ==========
    
    def _get_user_department(self, user):
        """Extract user's department"""
        if hasattr(user, 'profile') and hasattr(user.profile, 'department'):
            return user.profile.department
        if hasattr(user, 'department'):
            return user.department
        if user.groups.exists():
            return user.groups.first().name
        return 'Unknown'
    
    def _is_department_head(self, user):
        """Check if user is a department head"""
        if hasattr(user, 'role') and 'head' in user.role.lower():
            return True
        if user.groups.filter(name__icontains='head').exists():
            return True
        return False
    
    def _get_top_users(self, limit=10, department=None):
        """Get top users by request count"""
        logs = UserUsageLog.objects.all()
        
        if department:
            logs = logs.filter(department=department)
        
        user_stats = logs.values('user', 'user__username', 'user__email', 'department').annotate(
            total_requests=Count('id'),
            total_tokens=Sum('tokens_used'),
            avg_time=Avg('processing_time'),
            last_activity=Max('timestamp')
        ).order_by('-total_requests')[:limit]
        
        return [
            {
                'user_id': u['user'],
                'username': u['user__username'],
                'email': u['user__email'],
                'department': u['department'] or 'Unknown',
                'total_requests': u['total_requests'],
                'total_tokens': u['total_tokens'] or 0,
                'avg_processing_time': round(u['avg_time'] or 0, 3),
                'last_activity': u['last_activity'],
            }
            for u in user_stats
        ]
    
    def _get_daily_trend(self, days=7):
        """Get daily usage trend"""
        from django.db.models.functions import TruncDate
        
        start_date = timezone.now() - timedelta(days=days)
        
        logs = UserUsageLog.objects.filter(timestamp__gte=start_date)
        
        daily_data = logs.annotate(
            date=TruncDate('timestamp')
        ).values('date').annotate(
            requests=Count('id'),
            users=Count('user', distinct=True),
            tokens=Sum('tokens_used'),
            avg_time=Avg('processing_time'),
            errors=Count('id', filter=Q(status='error'))
        ).order_by('date')
        
        return [
            {
                'date': d['date'],
                'requests': d['requests'],
                'users': d['users'],
                'tokens': d['tokens'] or 0,
                'avg_response_time': round(d['avg_time'] or 0, 3),
                'error_rate': round((d['errors'] / d['requests'] * 100) if d['requests'] > 0 else 0, 2),
            }
            for d in daily_data
        ]
    
    def _get_hourly_trend(self, days=1):
        """Get hourly usage trend"""
        from django.db.models.functions import ExtractHour
        
        start_date = timezone.now() - timedelta(days=days)
        
        logs = UserUsageLog.objects.filter(timestamp__gte=start_date)
        
        hourly_data = logs.annotate(
            hour=ExtractHour('timestamp')
        ).values('hour').annotate(
            requests=Count('id'),
            users=Count('user', distinct=True),
            tokens=Sum('tokens_used'),
            avg_time=Avg('processing_time'),
            errors=Count('id', filter=Q(status='error'))
        ).order_by('hour')
        
        return [
            {
                'date': timezone.now().date(),
                'hour': d['hour'],
                'requests': d['requests'],
                'users': d['users'],
                'tokens': d['tokens'] or 0,
                'avg_response_time': round(d['avg_time'] or 0, 3),
                'error_rate': round((d['errors'] / d['requests'] * 100) if d['requests'] > 0 else 0, 2),
            }
            for d in hourly_data
        ]
    
    def _get_hourly_distribution(self):
        """Get hourly distribution for today"""
        from django.db.models.functions import ExtractHour
        
        today = timezone.now().date()
        
        logs = UserUsageLog.objects.filter(timestamp__date=today)
        
        hourly_dist = logs.annotate(
            hour=ExtractHour('timestamp')
        ).values('hour').annotate(
            count=Count('id')
        ).order_by('hour')
        
        return list(hourly_dist)
    
    def _calculate_growth(self, old_value, new_value):
        """Calculate growth percentage"""
        if not old_value or old_value == 0:
            return 100.0 if new_value and new_value > 0 else 0.0
        
        return ((new_value - old_value) / old_value) * 100
    
    def _generate_insights(self, current_stats, prev_stats, dept_stats, feature_stats, user_counts):
        """Generate AI-like insights for sales report"""
        insights = []
        
        # User growth insight
        curr_users = current_stats['unique_users'] or 0
        prev_users = prev_stats['unique_users'] or 0
        if curr_users > prev_users:
            growth = round(((curr_users - prev_users) / prev_users * 100) if prev_users > 0 else 0, 1)
            insights.append(f"User base grew by {growth}% - Active user engagement is increasing")
        elif curr_users < prev_users:
            insights.append("User activity declined - Consider re-engagement campaigns")
        
        # Top department insight
        if dept_stats:
            top_dept = dept_stats[0]
            insights.append(f"{top_dept['department']} is the most active department with {top_dept['requests']} requests")
        
        # Top feature insight
        if feature_stats:
            top_feature = feature_stats[0]
            insights.append(f"{top_feature['feature_name']} is the most popular feature")
        
        # Engagement insight
        total_users = len(user_counts)
        if total_users > 0:
            active_users = sum(1 for u in user_counts if u['count'] > 10)
            engagement_rate = (active_users / total_users) * 100
            insights.append(f"{round(engagement_rate, 1)}% of users are actively engaged (>10 requests)")
        
        return insights


# Import Max for aggregation
from django.db.models import Max
