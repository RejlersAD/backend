"""
Usage Tracking Async Tasks

Background tasks for:
- Aggregating summary statistics
- Cleaning old logs
- Updating cached metrics

USAGE:
1. With Celery (production):
   - Celery beat will run these tasks automatically
   
2. Without Celery (development):
   - Run manually via management command:
     python manage.py aggregate_usage_stats
"""

import logging
from datetime import timedelta
from django.utils import timezone
from django.core.cache import cache
from django.db.models import Count, Sum

logger = logging.getLogger(__name__)

# Celery setup (if available)
try:
    from celery import shared_task
    CELERY_AVAILABLE = True
except ImportError:
    CELERY_AVAILABLE = False
    # Fallback decorator
    def shared_task(**kwargs):
        def decorator(func):
            return func
        return decorator


@shared_task(name='usage_tracking.aggregate_summaries')
def aggregate_usage_summaries():
    """
    Aggregate usage statistics into summary tables.
    
    Should run every 10-15 minutes via Celery beat.
    """
    try:
        from .models import DepartmentUsageSummary, FeatureUsageSummary, UserUsageLog
        
        logger.info("[UsageTracking] Starting summary aggregation...")
        
        # Get all unique departments and features
        departments = UserUsageLog.objects.values_list('department', flat=True).distinct()
        features = UserUsageLog.objects.values_list('feature_name', flat=True).distinct()
        
        # Update department summaries
        for department in departments:
            if department:
                summary, created = DepartmentUsageSummary.objects.get_or_create(
                    department=department
                )
                summary.update_metrics()
                logger.debug(f"[UsageTracking] Updated department: {department}")
        
        # Update feature summaries
        for feature in features:
            if feature:
                summary, created = FeatureUsageSummary.objects.get_or_create(
                    feature_name=feature
                )
                summary.update_metrics()
                logger.debug(f"[UsageTracking] Updated feature: {feature}")
        
        # Clear cached summaries
        cache.delete("usage:global_summary")
        
        logger.info("[UsageTracking] ✅ Summary aggregation completed")
        
        return {
            'status': 'success',
            'departments_updated': len(departments),
            'features_updated': len(features)
        }
        
    except Exception as e:
        logger.error(f"[UsageTracking] ❌ Aggregation failed: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


@shared_task(name='usage_tracking.cleanup_old_logs')
def cleanup_old_usage_logs(days=90):
    """
    Clean up old usage logs to prevent database bloat.
    
    Default: Keep 90 days of detailed logs.
    Summaries are kept indefinitely.
    
    Should run daily via Celery beat.
    
    Args:
        days (int): Number of days to keep
    """
    try:
        from .models import UserUsageLog
        
        cutoff_date = timezone.now() - timedelta(days=days)
        
        old_logs = UserUsageLog.objects.filter(timestamp__lt=cutoff_date)
        count = old_logs.count()
        
        if count > 0:
            logger.info(f"[UsageTracking] Deleting {count} logs older than {days} days...")
            old_logs.delete()
            logger.info(f"[UsageTracking] ✅ Deleted {count} old logs")
        else:
            logger.info("[UsageTracking] No old logs to delete")
        
        return {
            'status': 'success',
            'deleted_count': count
        }
        
    except Exception as e:
        logger.error(f"[UsageTracking] ❌ Cleanup failed: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


@shared_task(name='usage_tracking.update_cached_metrics')
def update_cached_usage_metrics():
    """
    Update frequently-accessed metrics in Redis cache.
    
    This improves dashboard performance by pre-calculating metrics.
    Should run every 5 minutes via Celery beat.
    """
    try:
        from .models import UserUsageLog, DepartmentUsageSummary, FeatureUsageSummary
        from django.db.models import Count, Sum, Avg
        
        logger.info("[UsageTracking] Updating cached metrics...")
        
        # Global stats
        total_requests = UserUsageLog.objects.count()
        total_users = UserUsageLog.objects.values('user').distinct().count()
        
        cache.set('usage:global:total_requests', total_requests, 300)
        cache.set('usage:global:total_users', total_users, 300)
        
        # Today's stats
        today = timezone.now().date()
        today_requests = UserUsageLog.objects.filter(timestamp__date=today).count()
        today_users = UserUsageLog.objects.filter(
            timestamp__date=today
        ).values('user').distinct().count()
        
        cache.set('usage:today:requests', today_requests, 300)
        cache.set('usage:today:users', today_users, 300)
        
        # Top 5 departments
        top_depts = DepartmentUsageSummary.objects.order_by('-total_requests')[:5]
        cache.set('usage:top_departments', list(top_depts.values()), 300)
        
        # Top 5 features
        top_features = FeatureUsageSummary.objects.order_by('-popularity_score')[:5]
        cache.set('usage:top_features', list(top_features.values()), 300)
        
        logger.info("[UsageTracking] ✅ Cached metrics updated")
        
        return {'status': 'success'}
        
    except Exception as e:
        logger.error(f"[UsageTracking] ❌ Cache update failed: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


@shared_task(name='usage_tracking.generate_daily_report')
def generate_daily_usage_report():
    """
    Generate daily usage report and optionally send to admins.
    
    Should run daily at midnight via Celery beat.
    """
    try:
        from .models import UserUsageLog
        from django.db.models import Count, Sum, Avg
        
        yesterday = timezone.now().date() - timedelta(days=1)
        
        logs = UserUsageLog.objects.filter(timestamp__date=yesterday)
        
        stats = logs.aggregate(
            total_requests=Count('id'),
            unique_users=Count('user', distinct=True),
            total_tokens=Sum('tokens_used'),
            avg_time=Avg('processing_time'),
        )
        
        # Top users
        top_users = logs.values('user__username').annotate(
            count=Count('id')
        ).order_by('-count')[:5]
        
        # Top features
        top_features = logs.values('feature_name').annotate(
            count=Count('id')
        ).order_by('-count')[:5]
        
        report = {
            'date': yesterday,
            'total_requests': stats['total_requests'],
            'unique_users': stats['unique_users'],
            'total_tokens': stats['total_tokens'],
            'avg_processing_time': round(stats['avg_time'] or 0, 3),
            'top_users': list(top_users),
            'top_features': list(top_features),
        }
        
        logger.info(f"[UsageTracking] Daily report generated for {yesterday}")
        logger.info(f"[UsageTracking] {report}")
        
        # TODO: Optionally send email to admins
        # from django.core.mail import send_mail
        # send_mail(...)
        
        return report
        
    except Exception as e:
        logger.error(f"[UsageTracking] ❌ Daily report failed: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


def run_all_sync():
    """
    Run all tasks synchronously (for development/testing).
    
    Usage:
        from apps.usage_tracking.tasks import run_all_sync
        run_all_sync()
    """
    logger.info("[UsageTracking] Running all tasks synchronously...")
    
    result1 = aggregate_usage_summaries()
    logger.info(f"Aggregation: {result1}")
    
    result2 = update_cached_usage_metrics()
    logger.info(f"Cache update: {result2}")
    
    result3 = cleanup_old_usage_logs(days=90)
    logger.info(f"Cleanup: {result3}")
    
    logger.info("[UsageTracking] ✅ All tasks completed")


# ============================================================================
# CELERY BEAT SCHEDULE (add to settings.py)
# ============================================================================
"""
To enable automatic task execution, add this to settings.py:

from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'aggregate-usage-stats': {
        'task': 'usage_tracking.aggregate_summaries',
        'schedule': crontab(minute='*/15'),  # Every 15 minutes
    },
    'update-cached-metrics': {
        'task': 'usage_tracking.update_cached_metrics',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
    'cleanup-old-logs': {
        'task': 'usage_tracking.cleanup_old_logs',
        'schedule': crontab(hour=3, minute=0),  # Daily at 3 AM
    },
    'generate-daily-report': {
        'task': 'usage_tracking.generate_daily_report',
        'schedule': crontab(hour=0, minute=5),  # Daily at 00:05
    },
}
"""
