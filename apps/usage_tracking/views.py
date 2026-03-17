"""
Usage Analytics API Views
Clean, read-only endpoints for the internal sales dashboard.
All endpoints accept ?range=1d|7d|30d|90d (default 7d).
"""
from datetime import timedelta

from django.db.models import Avg, Count, Max
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UsageLog


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
