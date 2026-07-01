"""
Personal Dashboard API Views
Returns role-scoped data bundles for each authenticated user.
Super Admin/Staff → handled by frontend (global dashboard).
All other roles → personalized data scoped to their context.
"""
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

User = get_user_model()

# ─── Soft-coded configuration ────────────────────────────────────────────────
ACTIVITY_FEED_LIMIT = 10          # Recent activities to return
NOTIFICATION_PREVIEW_LIMIT = 5    # Recent notifications to preview
MODULES_LAST_USED_DAYS = 30       # Days to look back for last-used module stats
USAGE_CHART_DAYS = 30             # Days for usage sparkline
TEAM_ACTIVE_HOURS = 8             # Hours window for "active today" team count
PENDING_ACTIONS_LIMIT = 10        # Max pending actions to return
APPROVAL_CATEGORY_NAME = 'APPROVAL'   # Notification category name for approval requests
# ─────────────────────────────────────────────────────────────────────────────


def _get_primary_role_code(user):
    """Return the lowest-level (most privileged) role code for the user."""
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.select_related().prefetch_related('roles').get(user=user)
        roles = profile.roles.filter(is_active=True).order_by('level')
        if roles.exists():
            return roles.first().code
    except Exception:
        pass
    if user.is_superuser or user.is_staff:
        return 'super_admin'
    return 'viewer'


def _get_user_module_codes(user):
    """Return list of module codes accessible to this user."""
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.prefetch_related('roles__modules').get(user=user)
        codes = set()
        for role in profile.roles.filter(is_active=True):
            for mod in role.modules.all():
                codes.add(mod.code)
        return list(codes)
    except Exception:
        return []


def _get_activity_feed(user, role_code):
    """Return user-scoped activity feed."""
    try:
        from apps.activity.models import SystemActivity
        if role_code in ('super_admin', 'administrator'):
            # Admins can see their org's activities
            qs = SystemActivity.objects.filter(user=user).order_by('-timestamp')[:ACTIVITY_FEED_LIMIT]
        else:
            qs = SystemActivity.objects.filter(user=user).order_by('-timestamp')[:ACTIVITY_FEED_LIMIT]

        return [
            {
                'id': a.id,
                'type': a.activity_type,
                'category': a.category,
                'description': a.description,
                'timestamp': a.timestamp.isoformat(),
                'success': a.success,
                'severity': a.severity,
            }
            for a in qs
        ]
    except Exception as e:
        logger.warning('activity feed error: %s', e)
        return []


def _get_notifications_summary(user):
    """Return unread count + recent notification previews."""
    try:
        from apps.notifications.models import Notification
        unread_qs = Notification.objects.filter(recipient=user, is_read=False)
        unread_count = unread_qs.count()
        recent = (
            Notification.objects
            .filter(recipient=user)
            .select_related('category')
            .order_by('-created_at')[:NOTIFICATION_PREVIEW_LIMIT]
        )
        return {
            'unread_count': unread_count,
            'recent': [
                {
                    'id': n.id,
                    'title': n.title,
                    'message': n.message[:120],
                    'priority': n.priority,
                    'category': n.category.name if n.category_id else None,
                    'is_read': n.is_read,
                    'created_at': n.created_at.isoformat(),
                }
                for n in recent
            ],
        }
    except Exception as e:
        logger.warning('notifications summary error: %s', e)
        return {'unread_count': 0, 'recent': []}


def _get_usage_stats(user):
    """Return user's personal 30-day usage aggregated by discipline."""
    try:
        from apps.usage_tracking.models import UsageLog
        cutoff = timezone.now() - timedelta(days=USAGE_CHART_DAYS)
        logs = (
            UsageLog.objects
            .filter(user_email=user.email, timestamp__gte=cutoff)
            .values('discipline_label', 'discipline_key')
            .annotate(
                count=__import__('django.db.models', fromlist=['Count']).Count('id')
            )
            .order_by('-count')
        )
        from apps.usage_tracking.models import UsageLog
        from django.db.models import Count as DjCount
        logs = (
            UsageLog.objects
            .filter(user_email=user.email, timestamp__gte=cutoff)
            .values('discipline_label', 'discipline_key')
            .annotate(count=DjCount('id'))
            .order_by('-count')
        )
        total = UsageLog.objects.filter(user_email=user.email, timestamp__gte=cutoff).count()
        today = UsageLog.objects.filter(
            user_email=user.email,
            timestamp__gte=timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        ).count()
        return {
            'total_30d': total,
            'today': today,
            'by_discipline': list(logs[:8]),
        }
    except Exception as e:
        logger.warning('usage stats error: %s', e)
        return {'total_30d': 0, 'today': 0, 'by_discipline': []}


def _get_my_modules(user):
    """Return accessible modules enriched with last-used timestamp."""
    try:
        from apps.rbac.models import UserProfile, Module
        from apps.usage_tracking.models import UsageLog
        from django.db.models import Max

        profile = UserProfile.objects.prefetch_related('roles__modules').get(user=user)
        modules = {}
        for role in profile.roles.filter(is_active=True):
            for mod in role.modules.all():
                if mod.code not in modules:
                    modules[mod.code] = {'code': mod.code, 'name': mod.name, 'last_used': None}

        # Enrich with last-used from usage logs
        cutoff = timezone.now() - timedelta(days=MODULES_LAST_USED_DAYS)
        for code, mod in modules.items():
            last = UsageLog.objects.filter(
                user_email=user.email,
                discipline_key=code,
                timestamp__gte=cutoff,
            ).aggregate(last=Max('timestamp'))['last']
            mod['last_used'] = last.isoformat() if last else None

        # Sort by last_used desc (None at end)
        result = sorted(
            modules.values(),
            key=lambda m: (m['last_used'] is None, m['last_used'] or ''),
            reverse=False,
        )
        # Reverse so most-recently-used comes first
        result = sorted(
            modules.values(),
            key=lambda m: m['last_used'] or '0000',
            reverse=True,
        )
        return result
    except Exception as e:
        logger.warning('my modules error: %s', e)
        return []


def _get_pending_actions(user):
    """Return items needing user's attention (approvals, reviews)."""
    actions = []
    try:
        from apps.notifications.models import Notification
        approvals = Notification.objects.filter(
            recipient=user,
            is_read=False,
            category__name=APPROVAL_CATEGORY_NAME,
        ).order_by('-created_at')[:PENDING_ACTIONS_LIMIT]
        for n in approvals:
            actions.append({
                'id': n.id,
                'type': 'approval',
                'title': n.title,
                'message': n.message[:100],
                'priority': n.priority,
                'created_at': n.created_at.isoformat(),
            })
    except Exception as e:
        logger.warning('pending actions error: %s', e)
    return actions


def _get_team_snapshot(user):
    """Manager only — team members active today."""
    try:
        from apps.rbac.models import UserProfile
        from apps.activity.models import SystemActivity

        profile = UserProfile.objects.get(user=user)
        dept = profile.department or ''
        if not dept:
            return None

        cutoff = timezone.now() - timedelta(hours=TEAM_ACTIVE_HOURS)
        team_profiles = UserProfile.objects.filter(department=dept).exclude(user=user)
        team_count = team_profiles.count()

        # Active today = had a system activity in last TEAM_ACTIVE_HOURS hours
        active_user_ids = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff, user__in=team_profiles.values('user'))
            .values_list('user_id', flat=True)
            .distinct()
        )
        active_count = active_user_ids.count()

        return {
            'department': dept,
            'team_total': team_count,
            'active_today': active_count,
        }
    except Exception as e:
        logger.warning('team snapshot error: %s', e)
        return None


def _build_kpis(user, role_code, usage_stats, notifications_summary):
    """Build role-appropriate KPI list."""
    kpis = []

    # Universal KPIs
    kpis.append({
        'key': 'ai_calls_today',
        'label': 'AI Calls Today',
        'value': usage_stats.get('today', 0),
        'icon': 'sparkles',
        'color': 'blue',
    })
    kpis.append({
        'key': 'ai_calls_30d',
        'label': 'AI Calls (30 days)',
        'value': usage_stats.get('total_30d', 0),
        'icon': 'chart',
        'color': 'indigo',
    })
    kpis.append({
        'key': 'unread_notifications',
        'label': 'Unread Notifications',
        'value': notifications_summary.get('unread_count', 0),
        'icon': 'bell',
        'color': 'amber',
    })

    if role_code in ('manager', 'administrator'):
        kpis.append({
            'key': 'pending_approvals',
            'label': 'Pending Approvals',
            'value': 0,  # Populated below via pending_actions count
            'icon': 'check',
            'color': 'emerald',
        })

    return kpis


class PersonalDashboardView(APIView):
    """
    GET /api/v1/dashboard/personal/
    Returns a role-scoped data bundle for the authenticated user.
    Super Admin / is_superuser → 204 (frontend keeps global view).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Super admin / staff → frontend handles global view
        if user.is_superuser or user.is_staff:
            return Response({'redirect': 'global'}, status=204)

        role_code = _get_primary_role_code(user)

        # Also redirect super_admin role to global view
        if role_code == 'super_admin':
            return Response({'redirect': 'global'}, status=204)

        # Gather data in order
        usage_stats = _get_usage_stats(user)
        notifications_summary = _get_notifications_summary(user)
        activity_feed = _get_activity_feed(user, role_code)
        my_modules = _get_my_modules(user)
        pending_actions = _get_pending_actions(user)
        kpis = _build_kpis(user, role_code, usage_stats, notifications_summary)

        # Update pending approvals KPI with actual count
        for kpi in kpis:
            if kpi['key'] == 'pending_approvals':
                kpi['value'] = len(pending_actions)

        # Team snapshot only for managers
        team_snapshot = None
        if role_code in ('manager', 'administrator'):
            team_snapshot = _get_team_snapshot(user)

        # Build user context from profile
        try:
            from apps.rbac.models import UserProfile
            profile = UserProfile.objects.get(user=user)
            avatar_url = profile.profile_photo.url if profile.profile_photo else None
            department = profile.department or ''
            job_title = profile.job_title or ''
        except Exception:
            avatar_url = None
            department = ''
            job_title = ''

        payload = {
            'user_context': {
                'id': user.id,
                'name': user.get_full_name() or user.username,
                'email': user.email,
                'role_code': role_code,
                'department': department,
                'job_title': job_title,
                'avatar_url': avatar_url,
            },
            'kpis': kpis,
            'activity_feed': activity_feed,
            'notifications': notifications_summary,
            'my_modules': my_modules,
            'pending_actions': pending_actions,
            'usage_stats': usage_stats,
            'team_snapshot': team_snapshot,
            'generated_at': timezone.now().isoformat(),
        }

        return Response(payload)


class PersonalInsightsView(APIView):
    """
    GET /api/v1/dashboard/personal/insights/
    Returns the latest AI-generated insights for the authenticated user.
    If no insights exist yet, returns empty list (Celery task will populate).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        now = timezone.now()

        try:
            from .models import UserDashboardInsight, INSIGHT_MAX_ACTIVE
            insights = (
                UserDashboardInsight.objects
                .filter(user=user, is_active=True, expires_at__gt=now)
                .order_by('-generated_at')[:INSIGHT_MAX_ACTIVE]
            )
            data = [
                {
                    'id': ins.id,
                    'title': ins.title,
                    'body': ins.body,
                    'insight_type': ins.insight_type,
                    'icon_key': ins.icon_key,
                    'generated_at': ins.generated_at.isoformat(),
                }
                for ins in insights
            ]
        except Exception as e:
            logger.warning('insights fetch error: %s', e)
            data = []

        return Response({'insights': data, 'count': len(data)})
