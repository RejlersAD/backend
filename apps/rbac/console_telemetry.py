"""Admin console projections from the application's existing telemetry pipelines."""
from datetime import timedelta

from decouple import config
from django.conf import settings
from django.db import connection
from django.db.models import Avg, Count, Max, Q
from django.utils import timezone

from apps.usage_tracking.models import UsageLog
from .models import AccessRequest, AuditLog, UserProfile


def optional_number(name, cast):
    value = config(name, default='')
    return cast(value) if value else None


def request_summary(queryset):
    return queryset.aggregate(
        total=Count('id'), failed=Count('id', filter=Q(response_status__gte=400)),
        response_time=Avg('response_time_ms'), peak=Max('response_time_ms'),
    )


def recent_requests():
    # Same observation window for API status, errors, and response times.
    return UsageLog.objects.filter(timestamp__gte=timezone.now() - timedelta(minutes=5))


def console_overview(health):
    today = timezone.localdate()
    requests = UsageLog.objects.filter(timestamp__date=today)
    profiles = UserProfile.objects.filter(is_deleted=False, user__is_active=True)
    total = profiles.count()
    active_ids = set(requests.exclude(user_id=None).values_list('user_id', flat=True))
    active_ids.update(AuditLog.objects.filter(timestamp__date=today, user__isnull=False).values_list('user_id', flat=True))
    resources = health.resource_usage if health else {}
    from apps.core.storage_telemetry import get_admin_s3_snapshot
    s3 = get_admin_s3_snapshot()
    review = AccessRequest.objects.filter(reviewed_at__isnull=False).aggregate(latest=Max('reviewed_at'))['latest']
    database_used = None
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_database_size(current_database())')
            database_used = round(cursor.fetchone()[0] / 1024 ** 3, 3)
    return {
        'active_users_today': profiles.filter(user_id__in=active_ids).count(),
        'environment': getattr(settings, 'ENVIRONMENT', 'development'),
        'region': config('RAILWAY_REPLICA_REGION', default=config('ADMIN_REGION', default=None)),
        'version': config('RAILWAY_GIT_COMMIT_SHA', default=config('APP_VERSION', default=None)),
        'deploy_state': config('ADMIN_DEPLOY_STATE', default=None),
        'database_used_gb': database_used,
        'database_total_gb': optional_number('ADMIN_DATABASE_CAPACITY_GB', float),
        'storage_used_gb': s3.get('total_size_gb'),
        'storage_total_gb': None,  # S3 has no provisioned disk-size denominator.
        's3': s3,
        'disk_used_gb': resources.get('disk_used_gb'),
        'disk_total_gb': resources.get('disk_total_gb'),
        'api_requests_count': requests.count(),
        'api_requests_limit': optional_number('ADMIN_DAILY_API_REQUEST_LIMIT', int),
        'mfa_adoption_percentage': round(profiles.filter(is_mfa_enabled=True).count() / total * 100, 1) if total else None,
        'privileged_admins': profiles.filter(Q(user__is_superuser=True) | Q(roles__code__in=['super_admin', 'admin'], roles__is_active=True)).distinct().count(),
        'last_access_review': review.isoformat() if review else None,
    }
