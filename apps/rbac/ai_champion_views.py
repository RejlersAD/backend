"""
AI Champion — REST API endpoints
================================

Tracking endpoints (any authenticated user):
    POST /rbac/ai-champion/track/activity/
    POST /rbac/ai-champion/track/ai-usage/

Analytics endpoints (admin/super-admin):
    GET  /rbac/ai-champion/leaderboard/?days=30
    GET  /rbac/ai-champion/champion/current/
    GET  /rbac/ai-champion/champion/history/
    GET  /rbac/ai-champion/cost-report/?days=30
    GET  /rbac/ai-champion/monthly-summary/?year=2026&month=4
    GET  /rbac/ai-champion/user/<id>/score/?days=30
    POST /rbac/ai-champion/recompute/?year=2026&month=4   (admin only)
    GET  /rbac/ai-champion/export/csv/?days=30
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .ai_champion_models import (
    AIPricingConfig,
    AIUsageLog,
    ActivityEvent,
    MonthlyChampion,
)
from .ai_champion_service import (
    SCORING_WEIGHTS,
    BADGE_TIERS,
    compute_scores,
    cost_breakdown,
    resolve_cost_for_request,
    select_monthly_champion,
    tier_for,
)
from .permissions import IsSuperAdmin, IsAdmin

User = get_user_model()


# ---------------------------------------------------------------------------
# Serializers — used for input validation only (output is plain dicts)
# ---------------------------------------------------------------------------
class TrackActivitySerializer(serializers.Serializer):
    application = serializers.CharField(max_length=64)
    module = serializers.CharField(max_length=64, required=False, allow_blank=True)
    feature = serializers.CharField(max_length=64, required=False, allow_blank=True)
    action_type = serializers.CharField(max_length=32, required=False, default='other')
    session_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    duration_ms = serializers.IntegerField(required=False, default=0, min_value=0)
    success = serializers.BooleanField(required=False, default=True)
    metadata = serializers.JSONField(required=False, default=dict)


class MonthlyAwardSerializer(serializers.Serializer):
    selected_user_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=20, required=False, allow_empty=True)
    year = serializers.IntegerField(min_value=2000, max_value=9998)
    month = serializers.IntegerField(min_value=1, max_value=12)
    fingerprint = serializers.CharField(max_length=64, required=False)
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class TrackAIUsageSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=32)
    model_name = serializers.CharField(max_length=128)
    application = serializers.CharField(max_length=64, required=False, allow_blank=True)
    feature = serializers.CharField(max_length=64, required=False, allow_blank=True)
    request_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    tokens_input = serializers.IntegerField(min_value=0)
    tokens_output = serializers.IntegerField(min_value=0)
    cost_usd = serializers.DecimalField(
        max_digits=12, decimal_places=6, required=False, default=None,
        min_value=Decimal('0')
    )
    latency_ms = serializers.IntegerField(required=False, default=0, min_value=0)
    success = serializers.BooleanField(required=False, default=True)
    error_code = serializers.CharField(max_length=64, required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _window_from_days(request, default_days: int = 30) -> tuple[datetime, datetime]:
    try:
        days = int(request.query_params.get('days', default_days))
    except (TypeError, ValueError):
        days = default_days
    days = max(1, min(days, 365))  # soft-coded clamp
    end = timezone.now()
    start = end - timedelta(days=days)
    return start, end


def _user_brief(user) -> dict:
    if user is None:
        return {'id': None, 'email': '', 'name': '', 'avatar_url': ''}
    full = (f"{getattr(user, 'first_name', '') or ''} "
            f"{getattr(user, 'last_name', '') or ''}").strip()
    return {
        'id': user.id,
        'email': getattr(user, 'email', ''),
        'name': full or getattr(user, 'email', ''),
        'avatar_url': getattr(user, 'profile_photo', '') or '',
    }


# ---------------------------------------------------------------------------
# ViewSet
# ---------------------------------------------------------------------------
class AIChampionViewSet(viewsets.ViewSet):
    """
    All AI Champion endpoints — tracking, leaderboard, cost analytics,
    historical winners, and CSV export.
    """
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # Admin gating for analytics actions
        admin_actions = {
            'leaderboard', 'current', 'history', 'cost_report',
            'monthly_summary', 'export_csv', 'adoption_dashboard', 'monthly_award', 'workforce_adoption', 'outcomes', 'measurements', 'live_activity',
        }
        if self.action == 'recompute' or (self.action == 'monthly_award' and self.request.method == 'POST'):
            return [IsAuthenticated(), IsSuperAdmin()]
        if self.action in admin_actions:
            return [IsAuthenticated(), IsAdmin()]
        return [IsAuthenticated()]

    @action(detail=False, methods=['get', 'post'], url_path='monthly-award')
    def monthly_award(self, request):
        from .monthly_champion_service import monthly_report, publish
        from .models import UserProfile
        now = timezone.now()
        params = request.data if request.method == 'POST' else {
            'year': request.query_params.get('year', now.year),
            'month': request.query_params.get('month', now.month),
        }
        if request.method == 'GET' and 'selected_user_ids' in request.query_params:
            params['selected_user_ids'] = [uid for uid in request.query_params.get('selected_user_ids', '').split(',') if uid]
        serializer = MonthlyAwardSerializer(data=params)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if request.method == 'POST':
            return Response(publish(values['year'], values['month'], values.get('fingerprint', ''),
                                    values.get('reason', ''), request.user, values.get('selected_user_ids')), status=201)
        root = IsSuperAdmin().has_permission(request, self)
        user_ids = None
        if not root:
            org_id = getattr(getattr(request.user, 'rbac_profile', None), 'organization_id', None)
            user_ids = list(UserProfile.objects.filter(organization_id=org_id, is_deleted=False)
                            .values_list('user_id', flat=True)) if org_id else []
        return Response(monthly_report(values['year'], values['month'], user_ids, can_publish=root, request=request, selected_user_ids=values.get('selected_user_ids')))

    @action(detail=False, methods=['get'], url_path='adoption-dashboard')
    def adoption_dashboard(self, request):
        from .ai_adoption_service import adoption_dashboard
        from .models import UserProfile
        start, end = _window_from_days(request)
        user_ids = None
        if not IsSuperAdmin().has_permission(request, self):
            profile = getattr(request.user, 'rbac_profile', None)
            org_id = getattr(profile, 'organization_id', None)
            user_ids = (UserProfile.objects.filter(organization_id=org_id, is_deleted=False)
                        .values_list('user_id', flat=True)) if org_id is not None else []
        result = adoption_dashboard(start, end, user_ids=user_ids)
        result['scope'] = 'All organizations' if user_ids is None else 'Your organization'
        return Response(result)

    @action(detail=False, methods=['get'], url_path='workforce-adoption')
    def workforce_adoption(self, request):
        from .ai_workforce_service import workforce_adoption
        organization_id = None
        if not IsSuperAdmin().has_permission(request, self):
            organization_id = getattr(getattr(request.user, 'rbac_profile', None), 'organization_id', None)
            if organization_id is None:
                return Response(workforce_adoption(request.query_params.get('week'), []))
        return Response(workforce_adoption(request.query_params.get('week'), organization_id=organization_id))

    @action(detail=False, methods=['get'], url_path='measurements')
    def measurements(self, request):
        from .ai_measurement_service import measurement_report
        organization_id = None
        if not IsSuperAdmin().has_permission(request, self):
            organization_id = getattr(getattr(request.user, 'rbac_profile', None), 'organization_id', None)
            if organization_id is None:
                return Response({'detail': 'An organization profile is required.'}, status=403)
        try:
            days = max(1, min(365, int(request.query_params.get('days', 30))))
            page = max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            return Response({'detail': 'Invalid reporting period or page.'}, status=400)
        return Response(measurement_report(days, organization_id, page, request.query_params.get('module', ''),
                        request.query_params.get('search', '')[:200], request.query_params.get('status', '')))

    @action(detail=False, methods=['get'], url_path='live-activity')
    def live_activity(self, request):
        from rest_framework import serializers
        from .ai_live_activity import live_activity

        class Query(serializers.Serializer):
            search = serializers.CharField(max_length=120, required=False, allow_blank=True, default='')
            state = serializers.ChoiceField(choices=['', 'processing', 'recent', 'attention', 'quiet'], default='')
            page = serializers.IntegerField(min_value=1, default=1)
            page_size = serializers.ChoiceField(choices=[10, 25, 50], default=10)
            user = serializers.IntegerField(min_value=1, required=False)
            module = serializers.CharField(max_length=120, allow_blank=True, default='')
            ordering = serializers.ChoiceField(choices=['signal', 'user', '-user', 'state', '-state', 'latest', '-latest'], default='signal')
            timeline_limit = serializers.ChoiceField(choices=[20, 100], default=20)

        query = Query(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        org = None
        if not IsSuperAdmin().has_permission(request, self):
            org = getattr(getattr(request.user, 'rbac_profile', None), 'organization_id', None)
            if org is None:
                return Response({'detail': 'An organization profile is required.'}, status=403)
        return Response(live_activity(org, values['search'], values['state'], values['page'],
                                      values['page_size'], values.get('user'), module=values['module'],
                                      ordering=values['ordering'], timeline_limit=values['timeline_limit']))

    @action(detail=False, methods=['get', 'post', 'patch'], url_path='outcomes')
    def outcomes(self, request):
        from .ai_outcome_models import AIOutcomeEvidence
        from .ai_outcome_service import submit, review, serialize
        from .ai_measurement_service import monetary_values
        from .ai_cohort import DEFAULT_MODULES
        from .models import Module
        from django.conf import settings
        from django.db.models import Sum, F
        rows = AIOutcomeEvidence.objects.select_related('submitted_by', 'module')
        if not IsSuperAdmin().has_permission(request, self):
            org = getattr(getattr(request.user, 'rbac_profile', None), 'organization_id', None)
            rows = rows.filter(organization_id=org) if org else rows.none()
        if request.method == 'POST':
            return Response(submit(request.data, request.user, IsSuperAdmin().has_permission(request, self)), status=201)
        if request.method == 'PATCH':
            return Response(review(request.data, request.user, rows))
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except (ValueError, TypeError):
            return Response({'detail': 'Invalid page.'}, status=400)
        approved = rows.filter(status='approved')
        def minutes(kind):
            return approved.filter(measurement=kind).aggregate(total=Sum(F('baseline_minutes') - F('ai_minutes') - F('review_minutes') - F('rework_minutes')))['total']
        return Response({'count': rows.count(), 'page': page,
                         'results': [serialize(r, request.user.pk) for r in rows[(page-1)*10:page*10]],
                         'totals': {'approved': approved.count(), 'pending': rows.filter(status='pending').count(),
                                    'measured_minutes_saved': minutes('measured'), 'self_reported_minutes_saved': minutes('self_reported')},
                         'estimated_values': monetary_values(approved),
                         'modules': list(Module.objects.filter(is_active=True, code__in=getattr(settings, 'AI_ADOPTION_MODULE_APPLICATIONS', DEFAULT_MODULES)).values('code', 'name'))})

    # -------------------------------------------------------------------
    # POST /track/activity/
    # -------------------------------------------------------------------
    @action(detail=False, methods=['post'], url_path='track/activity')
    def track_activity(self, request):
        ser = TrackActivitySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        evt = ActivityEvent.objects.create(user=request.user, **ser.validated_data)
        return Response({'id': str(evt.id), 'timestamp': evt.timestamp.isoformat()},
                        status=status.HTTP_201_CREATED)

    # -------------------------------------------------------------------
    # POST /track/ai-usage/
    # -------------------------------------------------------------------
    @action(detail=False, methods=['post'], url_path='track/ai-usage')
    def track_ai_usage(self, request):
        ser = TrackAIUsageSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        cost = data.get('cost_usd')
        if cost is None:
            cost = resolve_cost_for_request(
                data['provider'], data['model_name'],
                data['tokens_input'], data['tokens_output'],
            )
        log = AIUsageLog.objects.create(
            user=request.user,
            provider=data['provider'],
            model_name=data['model_name'],
            application=data.get('application', ''),
            feature=data.get('feature', ''),
            request_id=data.get('request_id', ''),
            provenance='client',
            tokens_input=data['tokens_input'],
            tokens_output=data['tokens_output'],
            cost_usd=cost,
            latency_ms=data.get('latency_ms', 0),
            success=data.get('success', True),
            error_code=data.get('error_code', ''),
        )
        return Response({
            'id': str(log.id),
            'cost_usd': float(log.cost_usd),
            'total_tokens': log.total_tokens,
        }, status=status.HTTP_201_CREATED)

    # -------------------------------------------------------------------
    # GET /leaderboard/?days=30&limit=20
    # -------------------------------------------------------------------
    @action(detail=False, methods=['get'])
    def leaderboard(self, request):
        start, end = _window_from_days(request, default_days=30)
        try:
            limit = max(1, min(int(request.query_params.get('limit', 20)), 100))
        except (TypeError, ValueError):
            limit = 20

        ranked = compute_scores(start, end)[:limit]
        users_map = {
            u.id: u for u in User.objects.filter(id__in=[r['user_id'] for r in ranked])
        }
        for idx, row in enumerate(ranked, start=1):
            row['rank'] = idx
            row['user'] = _user_brief(users_map.get(row['user_id']))

        return Response({
            'window': {'start': start.isoformat(), 'end': end.isoformat()},
            'weights': SCORING_WEIGHTS,
            'badge_tiers': BADGE_TIERS,
            'count': len(ranked),
            'results': ranked,
        })

    # -------------------------------------------------------------------
    # GET /champion/current/
    # -------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='champion/current')
    def current(self, request):
        latest = MonthlyChampion.objects.order_by(
            '-period_year', '-period_month', 'rank'
        ).first()
        if not latest:
            # Fallback: live compute for current month-to-date
            now = timezone.now()
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            ranked = compute_scores(start, now)
            if not ranked:
                return Response({'champion': None, 'podium': [], 'live': True})
            users_map = {u.id: u for u in User.objects.filter(
                id__in=[r['user_id'] for r in ranked[:3]]
            )}
            podium = [{**r, 'rank': i + 1, 'user': _user_brief(users_map.get(r['user_id']))}
                      for i, r in enumerate(ranked[:3])]
            return Response({
                'live': True,
                'period': {'year': now.year, 'month': now.month},
                'champion': podium[0],
                'podium': podium,
            })

        period_y, period_m = latest.period_year, latest.period_month
        podium_qs = MonthlyChampion.objects.filter(
            period_year=period_y, period_month=period_m
        ).order_by('rank').select_related('user')

        def serialize(c: MonthlyChampion) -> dict:
            return {
                'rank': c.rank,
                'user': _user_brief(c.user),
                'champion_score': c.champion_score,
                'tier': c.badge_tier,
                'tier_label': tier_for(c.champion_score)['label'],
                'citation': c.citation,
                'breakdown': {
                    'usage_frequency': c.usage_frequency_score,
                    'feature_diversity': c.feature_diversity_score,
                    'time_spent': c.time_spent_score,
                    'ai_utilization': c.ai_utilization_score,
                    'cost_efficiency': c.cost_efficiency_score,
                    'success_rate': c.success_rate_score,
                },
                'stats': {
                    'total_actions': c.total_actions,
                    'total_ai_requests': c.total_ai_requests,
                    'total_ai_cost_usd': float(c.total_ai_cost_usd),
                    'distinct_features_used': c.distinct_features_used,
                    'total_session_minutes': c.total_session_minutes,
                    'success_rate': c.success_rate,
                },
            }
        podium = [serialize(c) for c in podium_qs]
        return Response({
            'live': False,
            'period': {'year': period_y, 'month': period_m},
            'champion': podium[0] if podium else None,
            'podium': podium,
        })

    # -------------------------------------------------------------------
    # GET /champion/history/?limit=12
    # -------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='champion/history')
    def history(self, request):
        try:
            limit = max(1, min(int(request.query_params.get('limit', 12)), 36))
        except (TypeError, ValueError):
            limit = 12

        winners = (
            MonthlyChampion.objects.filter(rank=1)
            .order_by('-period_year', '-period_month')
            .select_related('user')[:limit]
        )
        results = [{
            'period': {'year': w.period_year, 'month': w.period_month},
            'user': _user_brief(w.user),
            'champion_score': w.champion_score,
            'tier': w.badge_tier,
            'citation': w.citation,
        } for w in winners]
        return Response({'count': len(results), 'results': results})

    # -------------------------------------------------------------------
    # GET /cost-report/?days=30
    # -------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='cost-report')
    def cost_report(self, request):
        start, end = _window_from_days(request, default_days=30)
        return Response(cost_breakdown(start, end))

    # -------------------------------------------------------------------
    # GET /monthly-summary/?year=2026&month=4
    # -------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='monthly-summary')
    def monthly_summary(self, request):
        try:
            year = int(request.query_params.get('year', timezone.now().year))
            month = int(request.query_params.get('month', timezone.now().month))
        except (TypeError, ValueError):
            return Response({'detail': 'Invalid year/month'}, status=400)
        if not 1 <= month <= 12:
            return Response({'detail': 'month must be between 1 and 12'}, status=400)
        start = datetime(year, month, 1, tzinfo=dt_timezone.utc)
        end = (datetime(year + 1, 1, 1, tzinfo=dt_timezone.utc)
               if month == 12 else datetime(year, month + 1, 1, tzinfo=dt_timezone.utc))

        breakdown = cost_breakdown(start, end)
        ranked = compute_scores(start, end)[:10]
        return Response({
            'period': {'year': year, 'month': month},
            'cost': breakdown,
            'top_users': ranked,
        })

    # -------------------------------------------------------------------
    # GET /user/<id>/score/?days=30
    # -------------------------------------------------------------------
    @action(detail=True, methods=['get'], url_path='score')
    def user_score(self, request, pk=None):
        try:
            target = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'detail': 'User not found'}, status=404)
        # User can read own score; admins can read any
        if request.user.id != target.id and not request.user.is_staff:
            from .permissions import IsAdmin as _IsAdmin
            if not _IsAdmin().has_permission(request, self):
                return Response({'detail': 'Forbidden'}, status=403)

        start, end = _window_from_days(request, default_days=30)
        ranked = compute_scores(start, end)
        match = next((r for r in ranked if r['user_id'] == target.id), None)
        rank = next((i for i, r in enumerate(ranked, start=1)
                     if r['user_id'] == target.id), None)
        return Response({
            'user': _user_brief(target),
            'window': {'start': start.isoformat(), 'end': end.isoformat()},
            'rank': rank,
            'cohort_size': len(ranked),
            'score': match,
        })

    # -------------------------------------------------------------------
    # POST /recompute/?year=2026&month=4
    # -------------------------------------------------------------------
    @action(detail=False, methods=['post'])
    def recompute(self, request):
        try:
            year = int(request.query_params.get('year') or request.data.get('year'))
            month = int(request.query_params.get('month') or request.data.get('month'))
        except (TypeError, ValueError):
            return Response({'detail': 'year and month are required'}, status=400)
        if not 1 <= month <= 12:
            return Response({'detail': 'month must be between 1 and 12'}, status=400)
        created = select_monthly_champion(year, month)
        return Response({
            'period': {'year': year, 'month': month},
            'created_count': len(created),
            'champion_user_id': created[0].user_id if created else None,
        }, status=201)

    # -------------------------------------------------------------------
    # GET /export/csv/?days=30
    # -------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='export/csv')
    def export_csv(self, request):
        start, end = _window_from_days(request, default_days=30)
        ranked = compute_scores(start, end)
        users_map = {u.id: u for u in User.objects.filter(
            id__in=[r['user_id'] for r in ranked]
        )}
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="ai-champion-leaderboard-'
            f'{start.date()}_to_{end.date()}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow([
            'Rank', 'User Email', 'Name', 'Champion Score', 'Tier',
            'Total Actions', 'AI Requests', 'AI Cost (USD)', 'Tokens',
            'Distinct Features', 'Session Minutes', 'Success Rate %',
        ])
        for idx, r in enumerate(ranked, start=1):
            u = users_map.get(r['user_id'])
            brief = _user_brief(u)
            s = r['stats']
            writer.writerow([
                idx, brief['email'], brief['name'],
                r['champion_score'], r['tier_label'],
                s['total_actions'], s['total_ai_requests'],
                f"{s['total_ai_cost_usd']:.4f}", s['total_tokens'],
                s['distinct_features_used'], s['total_session_minutes'],
                f"{s['success_rate']:.2f}",
            ])
        return response
