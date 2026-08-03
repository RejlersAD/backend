"""
Usage Analytics API Views
Clean, read-only endpoints for the internal sales dashboard.
All endpoints accept ?range=1d|7d|30d|90d (default 7d).
"""
from datetime import timedelta, date
import calendar

from django.db.models import Avg, Count, Max, Q, Sum
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncQuarter
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

# ---------------------------------------------------------------------------
# Utilisation Report — soft-coded AI cost model
# Update these numbers here ONLY — nothing else needs changing.
# ---------------------------------------------------------------------------
AI_COST_CONFIG = {
    'activity_types':          ['ai_analysis', 'ml_prediction'],  # which SystemActivity types count as AI
    'avg_tokens_per_call':     1_500,   # estimated average tokens per AI invocation
    'cost_per_1k_input_tokens':  0.01, # USD – e.g. GPT-4o input
    'cost_per_1k_output_tokens': 0.03, # USD – e.g. GPT-4o output
    'input_fraction':            0.40, # assumed fraction that is input tokens
    'output_fraction':           0.60, # assumed fraction that is output tokens
    'model_assumed':           'GPT-4o (estimated)',
}

# Blended cost per AI call derived from the config above (pre-computed at startup)
def _ai_cost_per_call():
    c = AI_COST_CONFIG
    avg = c['avg_tokens_per_call']
    return (
        avg * c['input_fraction']  / 1000 * c['cost_per_1k_input_tokens'] +
        avg * c['output_fraction'] / 1000 * c['cost_per_1k_output_tokens']
    )

# ---------------------------------------------------------------------------
# Utilisation Report — period definitions
# Add a new period type here; the view handles the rest automatically.
# ---------------------------------------------------------------------------
REPORT_PERIODS = {
    'daily': {
        'label':    'Daily',
        'trunc_fn': TruncDate,
        'fmt':      lambda d: str(d),
    },
    'weekly': {
        'label':    'Weekly',
        'trunc_fn': TruncWeek,
        'fmt':      lambda d: f"W/C {d.strftime('%d %b %Y')}",
    },
    'monthly': {
        'label':    'Monthly',
        'trunc_fn': TruncMonth,
        'fmt':      lambda d: d.strftime('%b %Y'),
    },
    'quarterly': {
        'label':    'Quarterly',
        'trunc_fn': TruncQuarter,
        'fmt':      lambda d: f"Q{((d.month - 1) // 3) + 1} {d.year}",
    },
}

UNASSIGNED_DEPT = 'General / Unassigned'


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
    Full user roster joined with UsageLog stats and RBAC profile data.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        start, _ = _parse_range(request)

        # UsageLog stats keyed by email
        stats_map = {
            r['user_email']: r
            for r in (
                UsageLog.objects
                .filter(timestamp__gte=start)
                .values('user_email')
                .annotate(
                    total_requests=Count('id'),
                    last_seen=Max('timestamp'),
                    disciplines_used=Count('discipline_key', distinct=True),
                    avg_response_ms=Avg('response_time_ms'),
                )
            )
        }

        # RBAC profile data keyed by user_id (graceful — skipped if rbac not installed)
        profile_map = {}
        try:
            from apps.rbac.models import UserProfile
            for p in UserProfile.objects.prefetch_related('roles').select_related('user'):
                profile_map[p.user_id] = p
        except Exception:
            pass

        users = User.objects.values(
            'id', 'email', 'first_name', 'last_name',
            'is_active', 'date_joined', 'last_login',
        )
        result = []
        for u in users:
            stats   = stats_map.get(u['email'], {})
            profile = profile_map.get(u['id'])
            last_seen  = stats.get('last_seen')
            last_login = u.get('last_login')

            # Soft-coded status derivation
            if not u['is_active']:
                status = 'inactive'
            elif last_seen and (timezone.now() - last_seen).days <= 7:
                status = 'active'
            elif last_login and (timezone.now() - last_login).days <= 30:
                status = 'recent'
            else:
                status = 'dormant'

            result.append({
                'id':              u['id'],
                'email':           u['email'],
                'full_name':       f"{u['first_name']} {u['last_name']}".strip(),
                'is_active':       u['is_active'],
                'date_joined':     u['date_joined'].isoformat() if u['date_joined'] else None,
                'last_login':      last_login.isoformat() if last_login else None,
                'total_requests':  stats.get('total_requests', 0),
                'last_seen':       last_seen.isoformat() if last_seen else None,
                'disciplines_used': stats.get('disciplines_used', 0),
                'avg_response_ms': round(stats.get('avg_response_ms') or 0),
                'department':      getattr(profile, 'department', '') or '',
                'job_title':       getattr(profile, 'job_title', '') or '',
                'roles':           [r.name for r in profile.roles.all()] if profile else [],
                'status':          status,
            })
        result.sort(key=lambda x: x['total_requests'], reverse=True)
        return Response(result)


# Soft-coded category display labels for DbEventsView
_CATEGORY_LABELS = {
    'authentication':  'Authentication',
    'authorization':   'Authorization',
    'data_management': 'Data Management',
    'system_operation': 'System Operation',
    'security':        'Security',
    'api':             'API',
    'ml_ai':           'ML / AI',
    'communication':   'Communication',
    'maintenance':     'Maintenance',
}


class DbEventsView(APIView):
    """
    GET /api/v1/usage/db-events/
    SystemActivity events — logins, uploads, AI calls, errors, etc.
    Accepts: ?range=7d  ?category=  ?severity=  ?limit=50
    Returns: { events, total_in_period, category_summary }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.activity.models import SystemActivity
        start, _ = _parse_range(request)
        category = request.query_params.get('category', '')
        severity = request.query_params.get('severity', '')
        limit    = min(int(request.query_params.get('limit', 50)), 500)

        base_qs = SystemActivity.objects.filter(timestamp__gte=start)
        total   = base_qs.count()

        # Category summary (always over full unfiltered set)
        cat_summary = [
            {
                'category': c['category'],
                'label':    _CATEGORY_LABELS.get(c['category'], c['category'].replace('_', ' ').title()),
                'count':    c['count'],
            }
            for c in base_qs.values('category').annotate(count=Count('id')).order_by('-count')
        ]

        # Apply optional filters for event list
        qs = base_qs
        if category:
            qs = qs.filter(category=category)
        if severity:
            qs = qs.filter(severity=severity)

        rows = qs.values(
            'id', 'activity_type', 'category', 'severity',
            'user_email', 'user_full_name', 'ip_address',
            'description', 'timestamp', 'duration_ms', 'success',
        ).order_by('-timestamp')[:limit]

        events = [
            {
                **row,
                'timestamp':      row['timestamp'].isoformat() if row['timestamp'] else None,
                'category_label': _CATEGORY_LABELS.get(row['category'], row['category'].replace('_', ' ').title()),
                'ip_address':     str(row['ip_address']) if row['ip_address'] else None,
            }
            for row in rows
        ]

        return Response({
            'events':           events,
            'total_in_period':  total,
            'category_summary': cat_summary,
        })


class SessionsView(APIView):
    """
    GET /api/v1/usage/sessions/
    Returns { active_sessions, recent_sessions, active_count }.
    Active  = requests in last 15 min.
    Recent  = requests in last 7 days.
    Each session row is enriched with all fields the SessionsPanel expects.
    """
    permission_classes = [IsAuthenticated]
    _RECENT_DAYS = 7

    def get(self, request):
        active_cutoff = timezone.now() - timedelta(minutes=ACTIVE_NOW_MINUTES)
        recent_cutoff = timezone.now() - timedelta(days=self._RECENT_DAYS)

        def _build_rows(qs, is_active):
            rows = (
                qs
                .values('user_email', 'user_full_name')
                .annotate(
                    requests=Count('id'),
                    last_seen=Max('timestamp'),
                    current_discipline=Max('discipline_label'),
                    last_path=Max('request_path'),
                )
                .order_by('-last_seen')
            )
            result = []
            for row in rows:
                last_seen = row['last_seen']
                result.append({
                    'id':               row['user_email'],
                    'user_email':       row['user_email'],
                    'user_name':        row['user_full_name'] or row['user_email'],
                    'is_active':        is_active,
                    'requests':         row['requests'],
                    'last_activity':    last_seen.isoformat() if last_seen else None,
                    'current_page':     row.get('last_path') or row.get('current_discipline') or '—',
                    'current_discipline': row.get('current_discipline', ''),
                    # Fields not available in UsageLog — return sensible defaults
                    'browser':          '—',
                    'os':               '—',
                    'device_type':      'Web',
                    'ip_address':       None,
                    'duration_label':   '—',
                })
            return result

        active_rows = _build_rows(
            UsageLog.objects.filter(timestamp__gte=active_cutoff), is_active=True
        )
        recent_rows = _build_rows(
            UsageLog.objects.filter(timestamp__gte=recent_cutoff), is_active=False
        )

        return Response({
            'active_sessions': active_rows,
            'recent_sessions': recent_rows,
            'active_count':    len(active_rows),
        })


class UtilisationReportView(APIView):
    """
    GET /api/v1/usage/report/
    Comprehensive utilisation report for management / sales.

    Query params:
        period  = daily | weekly | monthly (default) | quarterly
        anchor  = YYYY-MM  (default = current month; ignored for daily/weekly which auto-span 30/90 days)
        dept    = <department name> to filter to a single department (optional)

    Response shape:
        {
          report_meta, summary, by_department[], by_user[],
          by_discipline[], trends[], ai_cost_model
        }
    """
    permission_classes = [IsAuthenticated]

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------
    @staticmethod
    def _resolve_date_range(period: str, anchor: str):
        """Return (start_date, end_date) based on period + anchor."""
        now = timezone.now()
        if period == 'daily':
            end   = now
            start = now - timedelta(days=30)
        elif period == 'weekly':
            end   = now
            start = now - timedelta(days=90)
        elif period == 'quarterly':
            if anchor:
                try:
                    y, m = map(int, anchor.split('-'))
                    q = (m - 1) // 3
                    start = date(y, q * 3 + 1, 1)
                    eq_m  = start.month + 2
                    eq_y  = y + (1 if eq_m > 12 else 0)
                    eq_m  = eq_m - 12 if eq_m > 12 else eq_m
                    last_day = calendar.monthrange(eq_y, eq_m)[1]
                    end = date(eq_y, eq_m, last_day)
                    start = timezone.make_aware(
                        timezone.datetime(start.year, start.month, start.day)
                    )
                    end = timezone.make_aware(
                        timezone.datetime(end.year, end.month, end.day, 23, 59, 59)
                    )
                    return start, end
                except Exception:
                    pass
            # Default: current quarter
            q = (now.month - 1) // 3
            start = timezone.make_aware(
                timezone.datetime(now.year, q * 3 + 1, 1)
            )
            end = now
        else:
            # monthly (default)
            if anchor:
                try:
                    y, m = map(int, anchor.split('-'))
                    last_day = calendar.monthrange(y, m)[1]
                    start = timezone.make_aware(timezone.datetime(y, m, 1))
                    end   = timezone.make_aware(timezone.datetime(y, m, last_day, 23, 59, 59))
                    return start, end
                except Exception:
                    pass
            # Default: current month
            start = timezone.make_aware(
                timezone.datetime(now.year, now.month, 1)
            )
            end = now
        return start, end

    @staticmethod
    def _build_dept_map():
        """Return {email: dept_label} from UserProfile gracefully."""
        dept_map = {}
        job_map  = {}
        try:
            from apps.rbac.models import UserProfile
            for p in UserProfile.objects.only('user__email', 'department', 'job_title').select_related('user'):
                email = p.user.email
                dept_map[email] = p.department.strip() if p.department and p.department.strip() else UNASSIGNED_DEPT
                job_map[email]  = p.job_title or ''
        except Exception:
            pass
        return dept_map, job_map

    # ---------------------------------------------------------------------------
    # Main handler
    # ---------------------------------------------------------------------------
    def get(self, request):
        period     = request.query_params.get('period', 'monthly')
        anchor     = request.query_params.get('anchor', '')
        dept_filter = request.query_params.get('dept', '')

        if period not in REPORT_PERIODS:
            period = 'monthly'

        period_cfg  = REPORT_PERIODS[period]
        start, end  = self._resolve_date_range(period, anchor)
        cost_per_call = _ai_cost_per_call()

        # ── department + job lookup ───────────────────────────────────────────
        dept_map, job_map = self._build_dept_map()

        # ── base UsageLog queryset ───────────────────────────────────────────
        base_qs = UsageLog.objects.filter(timestamp__gte=start, timestamp__lte=end)
        if dept_filter:
            # collect emails in that department
            dept_emails = [e for e, d in dept_map.items() if d == dept_filter]
            base_qs = base_qs.filter(user_email__in=dept_emails)

        # ── AI events from SystemActivity ────────────────────────────────────
        try:
            from apps.activity.models import SystemActivity
            ai_qs = SystemActivity.objects.filter(
                timestamp__gte=start,
                timestamp__lte=end,
                activity_type__in=AI_COST_CONFIG['activity_types'],
            )
            if dept_filter:
                ai_qs = ai_qs.filter(user_email__in=dept_emails)
        except Exception:
            ai_qs = None

        # ── Per-user UsageLog aggregation ────────────────────────────────────
        user_usage = {
            r['user_email']: r
            for r in (
                base_qs
                .values('user_email', 'user_full_name')
                .annotate(
                    requests=Count('id'),
                    success_count=Count('id', filter=Q(success=True)),
                    avg_ms=Avg('response_time_ms'),
                    last_seen=Max('timestamp'),
                    disciplines_used=Count('discipline_key', distinct=True),
                )
            )
        }

        # ── Per-user AI aggregation ──────────────────────────────────────────
        ai_per_user = {}
        if ai_qs is not None:
            for r in ai_qs.values('user_email').annotate(ai_calls=Count('id')):
                ai_per_user[r['user_email']] = r['ai_calls']

        # ── Build by_user list ────────────────────────────────────────────────
        all_emails = set(user_usage) | set(ai_per_user)
        by_user = []
        for email in all_emails:
            u      = user_usage.get(email, {})
            ai_cnt = ai_per_user.get(email, 0)
            dept   = dept_map.get(email, UNASSIGNED_DEPT)
            last_s = u.get('last_seen')
            by_user.append({
                'email':           email,
                'full_name':       u.get('user_full_name') or email,
                'department':      dept,
                'job_title':       job_map.get(email, ''),
                'requests':        u.get('requests', 0),
                'ai_calls':        ai_cnt,
                'estimated_cost':  round(ai_cnt * cost_per_call, 4),
                'avg_response_ms': round(u.get('avg_ms') or 0),
                'disciplines_used':u.get('disciplines_used', 0),
                'last_seen':       last_s.isoformat() if last_s else None,
            })
        by_user.sort(key=lambda x: x['requests'], reverse=True)

        # ── Build by_department list ──────────────────────────────────────────
        dept_agg = {}
        for u in by_user:
            d = u['department']
            if d not in dept_agg:
                dept_agg[d] = {
                    'department':   d,
                    'user_count':   0,
                    'requests':     0,
                    'ai_calls':     0,
                    'estimated_cost': 0.0,
                    'disciplines_used': set(),
                }
            dept_agg[d]['user_count']      += 1
            dept_agg[d]['requests']        += u['requests']
            dept_agg[d]['ai_calls']        += u['ai_calls']
            dept_agg[d]['estimated_cost']  += u['estimated_cost']
            dept_agg[d]['disciplines_used'].add(u['disciplines_used'])

        by_department = []
        for d, v in sorted(dept_agg.items(), key=lambda x: x[1]['requests'], reverse=True):
            top_users = sorted(
                [u for u in by_user if u['department'] == d],
                key=lambda x: x['requests'], reverse=True
            )[:3]
            by_department.append({
                **v,
                'estimated_cost':  round(v['estimated_cost'], 4),
                'disciplines_used': max(v['disciplines_used'], default=0),
                'top_users': [
                    {'email': u['email'], 'full_name': u['full_name'], 'requests': u['requests']}
                    for u in top_users
                ],
            })

        # ── By discipline ─────────────────────────────────────────────────────
        by_discipline = [
            {
                'discipline_key':   r['discipline_key'],
                'discipline_label': r['discipline_label'] or r['discipline_key'],
                'requests':         r['requests'],
                'unique_users':     r['unique_users'],
                'avg_response_ms':  round(r['avg_ms'] or 0),
            }
            for r in (
                base_qs
                .values('discipline_key', 'discipline_label')
                .annotate(
                    requests=Count('id'),
                    unique_users=Count('user_email', distinct=True),
                    avg_ms=Avg('response_time_ms'),
                )
                .order_by('-requests')
            )
        ]

        # ── Trend series ──────────────────────────────────────────────────────
        trunc_fn = period_cfg['trunc_fn']
        fmt_fn   = period_cfg['fmt']
        trend_qs = (
            base_qs
            .annotate(bucket=trunc_fn('timestamp'))
            .values('bucket')
            .annotate(
                requests=Count('id'),
                unique_users=Count('user_email', distinct=True),
                avg_ms=Avg('response_time_ms'),
            )
            .order_by('bucket')
        )

        # AI trend (by same buckets)
        ai_trend_map = {}
        if ai_qs is not None:
            for r in (
                ai_qs
                .annotate(bucket=trunc_fn('timestamp'))
                .values('bucket')
                .annotate(ai_calls=Count('id'))
            ):
                ai_trend_map[r['bucket']] = r['ai_calls']

        trends = []
        for row in trend_qs:
            bucket = row['bucket']
            ai_cnt = ai_trend_map.get(bucket, 0)
            trends.append({
                'label':           fmt_fn(bucket) if bucket else '—',
                'requests':        row['requests'],
                'unique_users':    row['unique_users'],
                'avg_response_ms': round(row['avg_ms'] or 0),
                'ai_calls':        ai_cnt,
                'estimated_cost':  round(ai_cnt * cost_per_call, 4),
            })

        # ── Summary ───────────────────────────────────────────────────────────
        total_requests = sum(u['requests'] for u in by_user)
        total_success  = sum(r.get('success_count', 0) for r in user_usage.values())
        total_ai       = sum(u['ai_calls'] for u in by_user)
        unique_users   = len(by_user)
        most_active_dept = by_department[0]['department'] if by_department else '—'
        avg_ms_all = 0
        if total_requests:
            total_ms_sum = sum((u.get('avg_response_ms', 0) * u['requests']) for u in by_user)
            avg_ms_all = round(total_ms_sum / total_requests)

        summary = {
            'total_requests':      total_requests,
            'unique_users':        unique_users,
            'total_ai_calls':      total_ai,
            'estimated_ai_cost':   round(total_ai * cost_per_call, 4),
            'avg_response_ms':     avg_ms_all,
            'success_rate':        round(total_success / max(total_requests, 1) * 100, 1),
            'most_active_dept':    most_active_dept,
            'total_disciplines':   len(by_discipline),
        }

        return Response({
            'report_meta': {
                'period':       period,
                'period_label': period_cfg['label'],
                'anchor':       anchor or timezone.now().strftime('%Y-%m'),
                'start':        start.isoformat(),
                'end':          end.isoformat(),
                'generated_at': timezone.now().isoformat(),
                'dept_filter':  dept_filter or None,
            },
            'summary':       summary,
            'by_department': by_department,
            'by_user':       by_user,
            'by_discipline': by_discipline,
            'trends':        trends,
            'ai_cost_model': {
                'model_assumed':             AI_COST_CONFIG['model_assumed'],
                'avg_tokens_per_call':       AI_COST_CONFIG['avg_tokens_per_call'],
                'cost_per_1k_input_tokens':  AI_COST_CONFIG['cost_per_1k_input_tokens'],
                'cost_per_1k_output_tokens': AI_COST_CONFIG['cost_per_1k_output_tokens'],
                'blended_cost_per_call':     round(cost_per_call, 6),
                'note': 'AI usage is estimated from SystemActivity ai_analysis + ml_prediction events. Token counts are averages — actual usage may vary.',
            },
        })
