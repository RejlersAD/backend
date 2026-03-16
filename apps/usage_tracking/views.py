"""
Usage Analytics API Views
Clean, read-only endpoints for the internal sales dashboard.
All endpoints accept ?range=1d|7d|30d|90d (default 7d).
"""
from datetime import timedelta

from django.db.models import Avg, Count, Max, OuterRef, Subquery
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UsageLog

# Lazy import for RBAC to avoid circular-import issues (imported inside the view method)
def _get_user_profile_model():
    from apps.rbac.models import UserProfile  # noqa
    return UserProfile


# ---------------------------------------------------------------------------
# Soft-coded range config - add new ranges here without touching views
# ---------------------------------------------------------------------------
RANGE_DAYS = {
    '1d':  1,
    '7d':  7,
    '30d': 30,
    '90d': 90,
}
ACTIVE_NOW_MINUTES = 15


def _parse_range(request):
    """Return (start_datetime, days_int) from ?range= query param."""
    key = request.query_params.get('range', '7d')
    days = RANGE_DAYS.get(key, 7)
    return timezone.now() - timedelta(days=days), days


class UsageOverviewView(APIView):
    """
    GET /api/v1/usage/overview/
    Summary KPIs: total requests, unique users, active now, avg response time.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        start, days = _parse_range(request)
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        active_cutoff = timezone.now() - timedelta(minutes=ACTIVE_NOW_MINUTES)

        qs       = UsageLog.objects.filter(timestamp__gte=start)
        today_qs = UsageLog.objects.filter(timestamp__gte=today_start)
        active_qs = UsageLog.objects.filter(timestamp__gte=active_cutoff)

        total = qs.count()
        return Response({
            'period_days':       days,
            'total_requests':    total,
            'today_requests':    today_qs.count(),
            'total_users':       qs.values('user_email').distinct().count(),
            'today_users':       today_qs.values('user_email').distinct().count(),
            'active_now':        active_qs.values('user_email').distinct().count(),
            'avg_response_ms':   round(qs.aggregate(a=Avg('response_time_ms'))['a'] or 0),
            'success_rate':      round(
                qs.filter(success=True).count() / max(total, 1) * 100, 1
            ),
        })


class DisciplineUsageView(APIView):
    """
    GET /api/v1/usage/disciplines/
    Requests and unique users grouped by discipline, sorted by volume.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        start, _ = _parse_range(request)
        data = (
            UsageLog.objects
            .filter(timestamp__gte=start)
            .values('discipline_key', 'discipline_label')
            .annotate(
                total_requests=Count('id'),
                unique_users=Count('user_email', distinct=True),
                avg_response_ms=Avg('response_time_ms'),
            )
            .order_by('-total_requests')
        )
        return Response(list(data))


class TopUsersView(APIView):
    """
    GET /api/v1/usage/top-users/?limit=10
    Most active users in the period.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        start, _ = _parse_range(request)
        limit = min(int(request.query_params.get('limit', 10)), 50)
        data = (
            UsageLog.objects
            .filter(timestamp__gte=start)
            .values('user_email', 'user_full_name')
            .annotate(
                total_requests=Count('id'),
                avg_response_ms=Avg('response_time_ms'),
                disciplines_used=Count('discipline_key', distinct=True),
                last_seen=Max('timestamp'),
            )
            .order_by('-total_requests')[:limit]
        )
        return Response([
            {
                **row,
                'last_seen': row['last_seen'].isoformat() if row['last_seen'] else None,
                'avg_response_ms': round(row['avg_response_ms'] or 0),
            }
            for row in data
        ])


class TrendsView(APIView):
    """
    GET /api/v1/usage/trends/
    Daily time series of requests and unique users.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        start, _ = _parse_range(request)
        data = (
            UsageLog.objects
            .filter(timestamp__gte=start)
            .annotate(date=TruncDate('timestamp'))
            .values('date')
            .annotate(
                requests=Count('id'),
                unique_users=Count('user_email', distinct=True),
                avg_response_ms=Avg('response_time_ms'),
            )
            .order_by('date')
        )
        return Response([
            {
                'date':            str(row['date']),
                'requests':        row['requests'],
                'users':           row['unique_users'],
                'avg_response_ms': round(row['avg_response_ms'] or 0),
            }
            for row in data
        ])


class ActiveNowView(APIView):
    """
    GET /api/v1/usage/active-now/
    Users who made at least one request in the last 15 minutes.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cutoff = timezone.now() - timedelta(minutes=ACTIVE_NOW_MINUTES)
        data = (
            UsageLog.objects
            .filter(timestamp__gte=cutoff)
            .values('user_email', 'user_full_name')
            .annotate(
                last_seen=Max('timestamp'),
                requests=Count('id'),
                last_discipline=Max('discipline_label'),
            )
            .order_by('-last_seen')
        )
        return Response([
            {
                **row,
                'last_seen': row['last_seen'].isoformat() if row['last_seen'] else None,
            }
            for row in data
        ])


class AllUsersView(APIView):
    """
    GET /api/v1/usage/all-users/
    Full user roster from RBAC database joined with UsageLog aggregates.
    Returns ALL registered users — even those with zero usage — so the
    sales team can see who has and hasn't engaged with the platform.
    Accepts ?range= same as other views (default 7d).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        start, _ = _parse_range(request)
        UserProfile = _get_user_profile_model()

        # Build a subquery dict keyed by email for UsageLog aggregates in range
        usage_by_email = {}
        usage_qs = (
            UsageLog.objects
            .filter(timestamp__gte=start)
            .values('user_email')
            .annotate(
                total_requests=Count('id'),
                last_seen=Max('timestamp'),
                disciplines_used=Count('discipline_key', distinct=True),
            )
        )
        for row in usage_qs:
            usage_by_email[row['user_email']] = row

        # Fetch all non-deleted profiles with related user + roles
        profiles = (
            UserProfile.objects
            .filter(is_deleted=False)
            .select_related('user')
            .prefetch_related('roles')
            .order_by('user__first_name', 'user__last_name')
        )

        results = []
        for profile in profiles:
            u = profile.user
            email = u.email
            usage = usage_by_email.get(email, {})
            role_names = [r.name for r in profile.roles.all()]
            last_seen = usage.get('last_seen')
            results.append({
                'email':            email,
                'full_name':        f"{u.first_name} {u.last_name}".strip() or email,
                'department':       profile.department or '',
                'job_title':        profile.job_title or '',
                'employee_id':      profile.employee_id or '',
                'status':           profile.status,
                'roles':            role_names,
                'is_active':        u.is_active,
                'last_login_at':    profile.last_login_at.isoformat() if profile.last_login_at else None,
                # Usage stats (zero-filled if never used in period)
                'total_requests':   usage.get('total_requests', 0),
                'disciplines_used': usage.get('disciplines_used', 0),
                'last_seen':        last_seen.isoformat() if last_seen else None,
            })

        # Sort: active users with usage first, then inactive / never-used at bottom
        results.sort(key=lambda x: (-x['total_requests'], x['full_name'].lower()))
        return Response(results)
