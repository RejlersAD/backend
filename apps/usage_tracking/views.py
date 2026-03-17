"""
Usage Analytics API Views
Clean, read-only endpoints for the internal sales dashboard.
PRIMARY DATA SOURCE: SystemActivity (activity app) — 11k+ real records.
UsageLog is kept as a secondary/legacy source.
All endpoints accept ?range=1d|7d|30d|90d (default 7d).
"""
from datetime import timedelta
from collections import defaultdict

from django.db.models import Avg, Count, Max, Case, When, Value, CharField, F, IntegerField
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DISCIPLINE_MAP

# ---------------------------------------------------------------------------
# Lazy imports to avoid circular-import issues
# ---------------------------------------------------------------------------
def _get_user_model():
    from django.contrib.auth import get_user_model
    return get_user_model()

def _get_user_profile_model():
    from apps.rbac.models import UserProfile
    return UserProfile

def _get_activity_models():
    from apps.activity.models import SystemActivity, UserSession
    return SystemActivity, UserSession

# ---------------------------------------------------------------------------
# Soft-coded range config — add new ranges here without touching views
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


def _discipline_label_case():
    """
    ORM Case/When expression — maps SystemActivity.description
    (format: 'GET /api/v1/{key}/...') to human-readable discipline label.
    Order matters: more specific paths appear first.
    """
    cases = [
        When(description__icontains=f'/{key}/', then=Value(label))
        for key, label in DISCIPLINE_MAP
    ]
    return Case(*cases, default=Value('Other'), output_field=CharField())


def _discipline_key_case():
    """Same as above but returns the key (slug)."""
    cases = [
        When(description__icontains=f'/{key}/', then=Value(key))
        for key, label in DISCIPLINE_MAP
    ]
    return Case(*cases, default=Value('other'), output_field=CharField())


class UsageOverviewView(APIView):
    """
    GET /api/v1/usage/overview/
    Summary KPIs: total requests, unique users, active now, avg response time.
    Primary source: SystemActivity (real data). Falls back gracefully.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        SystemActivity, _ = _get_activity_models()
        start, days = _parse_range(request)
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        active_cutoff = timezone.now() - timedelta(minutes=ACTIVE_NOW_MINUTES)

        qs        = SystemActivity.objects.filter(timestamp__gte=start)
        today_qs  = SystemActivity.objects.filter(timestamp__gte=today_start)
        active_qs = SystemActivity.objects.filter(timestamp__gte=active_cutoff)

        total = qs.count()
        return Response({
            'period_days':     days,
            'total_requests':  total,
            'today_requests':  today_qs.count(),
            'total_users':     qs.values('user_email').exclude(user_email='').distinct().count(),
            'today_users':     today_qs.values('user_email').exclude(user_email='').distinct().count(),
            'active_now':      active_qs.values('user_email').exclude(user_email='').distinct().count(),
            'avg_response_ms': round(qs.aggregate(a=Avg('duration_ms'))['a'] or 0),
            'success_rate':    round(qs.filter(success=True).count() / max(total, 1) * 100, 1),
        })


class DisciplineUsageView(APIView):
    """
    GET /api/v1/usage/disciplines/
    Requests and unique users grouped by discipline, sorted by volume.
    Derives discipline from API path in SystemActivity.description.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        SystemActivity, _ = _get_activity_models()
        start, _ = _parse_range(request)

        # Annotate each row with discipline then group
        data = (
            SystemActivity.objects
            .filter(timestamp__gte=start)
            .exclude(user_email='')
            .annotate(
                discipline_label=_discipline_label_case(),
                discipline_key=_discipline_key_case(),
            )
            .values('discipline_key', 'discipline_label')
            .annotate(
                total_requests=Count('id'),
                unique_users=Count('user_email', distinct=True),
                avg_response_ms=Avg('duration_ms'),
            )
            .order_by('-total_requests')
        )
        return Response(list(data))


class TopUsersView(APIView):
    """
    GET /api/v1/usage/top-users/?limit=10
    Most active users in the period, from SystemActivity.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        SystemActivity, _ = _get_activity_models()
        start, _ = _parse_range(request)
        limit = min(int(request.query_params.get('limit', 10)), 50)

        # Count requests per user + last seen
        rows = (
            SystemActivity.objects
            .filter(timestamp__gte=start)
            .exclude(user_email='')
            .values('user_email', 'user_full_name')
            .annotate(
                total_requests=Count('id'),
                avg_response_ms=Avg('duration_ms'),
                last_seen=Max('timestamp'),
            )
            .order_by('-total_requests')[:limit]
        )

        # Secondary pass: count distinct disciplines per user
        disc_map = defaultdict(set)
        disc_qs = (
            SystemActivity.objects
            .filter(timestamp__gte=start)
            .exclude(user_email='')
            .annotate(disc=_discipline_key_case())
            .values('user_email', 'disc')
        )
        for r in disc_qs:
            disc_map[r['user_email']].add(r['disc'])

        return Response([
            {
                'user_email':      row['user_email'],
                'user_full_name':  row['user_full_name'] or row['user_email'].split('@')[0],
                'total_requests':  row['total_requests'],
                'avg_response_ms': round(row['avg_response_ms'] or 0),
                'disciplines_used': len(disc_map.get(row['user_email'], set())),
                'last_seen':       row['last_seen'].isoformat() if row['last_seen'] else None,
            }
            for row in rows
        ])


class TrendsView(APIView):
    """
    GET /api/v1/usage/trends/
    Daily time series of requests and unique users from SystemActivity.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        SystemActivity, _ = _get_activity_models()
        start, _ = _parse_range(request)

        data = (
            SystemActivity.objects
            .filter(timestamp__gte=start)
            .annotate(date=TruncDate('timestamp'))
            .values('date')
            .annotate(
                requests=Count('id'),
                unique_users=Count('user_email', distinct=True),
                avg_response_ms=Avg('duration_ms'),
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
        SystemActivity, _ = _get_activity_models()
        cutoff = timezone.now() - timedelta(minutes=ACTIVE_NOW_MINUTES)

        data = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff)
            .exclude(user_email='')
            .annotate(disc=_discipline_label_case())
            .values('user_email', 'user_full_name')
            .annotate(
                last_seen=Max('timestamp'),
                requests=Count('id'),
                last_discipline=Max('disc'),
            )
            .order_by('-last_seen')
        )
        return Response([
            {
                'user_email':     row['user_email'],
                'user_full_name': row['user_full_name'] or row['user_email'].split('@')[0],
                'last_seen':      row['last_seen'].isoformat() if row['last_seen'] else None,
                'requests':       row['requests'],
                'last_discipline': row['last_discipline'] or 'Platform',
            }
            for row in data
        ])


class AllUsersView(APIView):
    """
    GET /api/v1/usage/all-users/
    Full user roster from the users table joined with SystemActivity aggregates.
    Returns ALL registered users — even those with zero usage — so the
    sales team can see who has and hasn't engaged with the platform.
    Accepts ?range= same as other views (default 7d).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        SystemActivity, _ = _get_activity_models()
        User = _get_user_model()
        start, _ = _parse_range(request)

        # Build usage aggregates from SystemActivity, keyed by user_email
        usage_by_email = {}
        disc_by_email  = defaultdict(set)

        usage_qs = (
            SystemActivity.objects
            .filter(timestamp__gte=start)
            .exclude(user_email='')
            .annotate(disc=_discipline_key_case())
            .values('user_email', 'disc')
            .annotate(
                cnt=Count('id'),
                last=Max('timestamp'),
            )
        )
        for row in usage_qs:
            email = row['user_email']
            if email not in usage_by_email:
                usage_by_email[email] = {'total_requests': 0, 'last_seen': None}
            usage_by_email[email]['total_requests'] += row['cnt']
            if not usage_by_email[email]['last_seen'] or row['last'] > usage_by_email[email]['last_seen']:
                usage_by_email[email]['last_seen'] = row['last']
            disc_by_email[email].add(row['disc'])

        # Attempt to get RBAC profiles; fall back to users table
        try:
            UserProfile = _get_user_profile_model()
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
                last_seen = usage.get('last_seen')
                results.append({
                    'email':            email,
                    'full_name':        f"{u.first_name} {u.last_name}".strip() or email,
                    'department':       profile.department or '',
                    'job_title':        profile.job_title or '',
                    'employee_id':      profile.employee_id or '',
                    'status':           profile.status,
                    'roles':            [r.name for r in profile.roles.all()],
                    'is_active':        u.is_active,
                    'last_login_at':    profile.last_login_at.isoformat() if profile.last_login_at else None,
                    'total_requests':   usage.get('total_requests', 0),
                    'disciplines_used': len(disc_by_email.get(email, set())),
                    'last_seen':        last_seen.isoformat() if last_seen else None,
                })
        except Exception:
            # Fallback: use users table directly
            users = User.objects.all().order_by('first_name', 'last_name')
            results = []
            for u in users:
                email = u.email
                usage = usage_by_email.get(email, {})
                last_seen = usage.get('last_seen')
                status = 'active' if u.is_active else 'inactive'
                results.append({
                    'email':            email,
                    'full_name':        f"{u.first_name} {u.last_name}".strip() or email,
                    'department':       '',
                    'job_title':        '',
                    'employee_id':      '',
                    'status':           status,
                    'roles':            ['Admin'] if u.is_staff else [],
                    'is_active':        u.is_active,
                    'last_login_at':    u.last_login.isoformat() if u.last_login else None,
                    'total_requests':   usage.get('total_requests', 0),
                    'disciplines_used': len(disc_by_email.get(email, set())),
                    'last_seen':        last_seen.isoformat() if last_seen else None,
                })

        # Sort: engaged users first, then alphabetically
        results.sort(key=lambda x: (-x['total_requests'], x['full_name'].lower()))
        return Response(results)


class DatabaseEventsView(APIView):
    """
    GET /api/v1/usage/db-events/
    Pulls recent SystemActivity records (all DB/user events) from the activity app.
    Soft-coded filter map: add new activity_type / category filters without touching logic.

    Query params:
      ?range=1d|7d|30d|90d   (default 7d)
      ?category=authentication|api|data_management|…  (optional)
      ?severity=info|low|normal|high|critical          (optional)
      ?limit=50 (default, max 200)
    """
    permission_classes = [IsAuthenticated]

    # Soft-coded: filter category → human label
    CATEGORY_LABELS = {
        'authentication':   'Authentication',
        'authorization':    'Authorization',
        'data_management':  'Data Management',
        'system_operation': 'System Operation',
        'security':         'Security',
        'api':              'API',
        'ml_ai':            'ML / AI',
        'communication':    'Communication',
        'maintenance':      'Maintenance',
    }

    # Soft-coded: severity → colour hint for the frontend
    SEVERITY_COLOR = {
        'info':     '#64748b',
        'low':      '#22c55e',
        'normal':   '#3b82f6',
        'high':     '#f59e0b',
        'critical': '#ef4444',
    }

    def get(self, request):
        SystemActivity, _ = _get_activity_models()
        start, _ = _parse_range(request)

        qs = SystemActivity.objects.filter(timestamp__gte=start).select_related('user')

        # Optional filters (soft-coded: just add more query-param keys here)
        category = request.query_params.get('category')
        severity = request.query_params.get('severity')
        if category and category in self.CATEGORY_LABELS:
            qs = qs.filter(category=category)
        if severity and severity in self.SEVERITY_COLOR:
            qs = qs.filter(severity=severity)

        limit = min(int(request.query_params.get('limit', 50)), 200)
        qs = qs.order_by('-timestamp')[:limit]

        events = []
        for ev in qs:
            events.append({
                'id':           ev.id,
                'activity_type':ev.activity_type,
                'category':     ev.category,
                'category_label': self.CATEGORY_LABELS.get(ev.category, ev.category),
                'severity':     ev.severity,
                'severity_color': self.SEVERITY_COLOR.get(ev.severity, '#64748b'),
                'description':  ev.description,
                'user_email':   ev.user_email or '',
                'user_full_name': ev.user_full_name or '',
                'ip_address':   str(ev.ip_address) if ev.ip_address else '',
                'success':      ev.success,
                'duration_ms':  ev.duration_ms,
                'details':      ev.details,
                'timestamp':    ev.timestamp.isoformat(),
            })

        # Also return category summary counts for the period
        category_counts = (
            SystemActivity.objects
            .filter(timestamp__gte=start)
            .values('category')
            .annotate(count=Count('id'))
            .order_by('-count')
        )

        return Response({
            'events':           events,
            'total_in_period':  SystemActivity.objects.filter(timestamp__gte=start).count(),
            'category_summary': [
                {
                    'category': r['category'],
                    'label':    self.CATEGORY_LABELS.get(r['category'], r['category']),
                    'count':    r['count'],
                }
                for r in category_counts
            ],
        })


class UserSessionsView(APIView):
    """
    GET /api/v1/usage/sessions/
    Live + recent UserSession records from the activity app.
    Shows browser, OS, device, current page, and session duration.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _, UserSession = _get_activity_models()

        # Active sessions: not expired, last activity within 30 min
        cutoff = timezone.now() - timedelta(minutes=30)
        active_qs = (
            UserSession.objects
            .filter(is_active=True, last_activity__gte=cutoff)
            .select_related('user')
            .order_by('-last_activity')
        )

        # Recent sessions (last 7 days, including expired)
        week_ago = timezone.now() - timedelta(days=7)
        recent_qs = (
            UserSession.objects
            .filter(created_at__gte=week_ago)
            .select_related('user')
            .order_by('-last_activity')[:100]
        )

        def _serialize_session(s):
            duration_s = int((s.last_activity - s.created_at).total_seconds())
            hours, rem = divmod(duration_s, 3600)
            mins = rem // 60
            return {
                'id':            str(s.id),
                'user_email':    s.user.email if s.user else '',
                'user_name':     s.user.get_full_name() if s.user else '',
                'ip_address':    str(s.ip_address) if s.ip_address else '',
                'device_type':   s.device_type or 'Unknown',
                'browser':       s.browser or 'Unknown',
                'os':            s.os or 'Unknown',
                'current_page':  s.current_page or '',
                'is_active':     s.is_active,
                'last_activity': s.last_activity.isoformat(),
                'created_at':    s.created_at.isoformat(),
                'duration_label': f"{hours}h {mins}m" if hours else f"{mins}m",
            }

        return Response({
            'active_sessions':  [_serialize_session(s) for s in active_qs],
            'recent_sessions':  [_serialize_session(s) for s in recent_qs],
            'active_count':     active_qs.count(),
        })
