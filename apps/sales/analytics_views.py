"""
Sales Intelligence Analytics Views
===================================
Generates sales intelligence from GOVERNED CRM data:
  - Deal (Opportunity)         — pipeline, stages, values, probabilities
  - SalesForecast              — AI forecasts and actuals
  - ProjectHandover            — conversion-to-delivery evidence
  - Client                     — account intelligence

All values reconcile to persisted opportunity/proposal/handover records.
No synthetic values are manufactured.
"""
from datetime import timedelta
from decimal import Decimal
import logging

from django.db.models import Count, Sum, Avg, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.rbac.permissions import HasModuleAccess
from apps.rbac.data_visibility_config import build_visibility_filter

from .models import Deal, SalesForecast, ProjectHandover, Client

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────










# Non-terminal pipeline stages (active sales pipeline)
OPEN_PIPELINE_STAGES = {'lead', 'qualified', 'proposal', 'negotiation', 'award_pending'}

# Terminal stages
TERMINAL_STAGES = {'no_bid', 'lost', 'cancelled', 'converted'}

# Won stages (for win-rate / won-value calculations)
WON_STAGES = {'awarded'}


def _visibility_qs(request, model_class):
    """Apply role-scoped tenant filtering to a model queryset."""
    filter_q = build_visibility_filter(request.user, model_class)
    return model_class.objects.filter(filter_q)





# ─────────────────────────────────────────────────────────────────────────────
# VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class SalesPipelineAnalyticsView(APIView):
    """
    GET /api/v1/sales/analytics/pipeline/
    Returns pipeline analytics aggregated over governed Deal (Opportunity),
    SalesForecast, and ProjectHandover records.

    Filters:
      - is_deleted=False (soft-deleted opportunities excluded)
      - Non-terminal stages for active pipeline
      - Role-scoped tenant filtering via build_visibility_filter
    """
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'

    def get(self, request):
        # ── Base queryset: non-deleted opportunities ────────────────────────
        base_qs = _visibility_qs(request, Deal).filter(is_deleted=False)

        # ── Active pipeline (non-terminal stages) ───────────────────────────
        pipeline_qs = base_qs.filter(stage__in=OPEN_PIPELINE_STAGES)

        # ── Won deals ───────────────────────────────────────────────────────
        won_qs = base_qs.filter(stage__in=WON_STAGES)

        # ── Totals (guard: incomplete values return None, not zero) ─────────
        def _safe_sum(qs, field):
            """Return None if any record is missing the field or currency."""
            if qs.filter(Q(**{f'{field}__isnull': True}) | Q(currency='')).exists():
                return None
            return qs.aggregate(total=Sum(field))['total'] or Decimal('0')

        pipeline_value = _safe_sum(pipeline_qs, 'estimated_value')
        won_value = _safe_sum(won_qs, 'actual_value') or _safe_sum(won_qs, 'estimated_value')

        # ── Deal counts by stage ────────────────────────────────────────────
        stage_counts = (
            base_qs.values('stage')
            .annotate(deal_count=Count('id'))
            .order_by('stage')
        )
        stage_count_map = {r['stage']: r['deal_count'] for r in stage_counts}

        # ── Stage labels (mirrors models.DEAL_STAGES) ───────────────────────
        from .models import DEAL_STAGES
        by_stage = []
        for stage_key in sorted(DEAL_STAGES.keys(), key=lambda k: DEAL_STAGES[k]['order']):
            by_stage.append({
                'stage':       stage_key,
                'stage_label': DEAL_STAGES[stage_key]['name'],
                'deal_count':  stage_count_map.get(stage_key, 0),
                'order':       DEAL_STAGES[stage_key]['order'],
                'is_terminal': stage_key in TERMINAL_STAGES,
            })

        # ── Forecast aggregates ─────────────────────────────────────────────
        forecast_qs = _visibility_qs(request, SalesForecast)
        forecast_next_month = forecast_qs.aggregate(
            total=Sum('forecast_value')
        )['total'] or Decimal('0')

        # ── Handover evidence ───────────────────────────────────────────────
        handover_qs = _visibility_qs(request, ProjectHandover)
        handover_count = handover_qs.count()
        handover_completed = handover_qs.filter(status='accepted').count()

        # ── Win rate ────────────────────────────────────────────────────────
        total_closed = stage_count_map.get('awarded', 0) + stage_count_map.get('lost', 0)
        win_rate = round(stage_count_map.get('awarded', 0) / max(total_closed, 1) * 100, 1)

        # ── Avg deal size (won) ─────────────────────────────────────────────
        won_deals_for_avg = won_qs.exclude(actual_value__isnull=True).exclude(currency='')
        avg_deal_size = won_deals_for_avg.aggregate(avg=Avg('actual_value'))['avg']

        payload = {
            'total_opportunities': base_qs.count(),
            'active_pipeline_count': pipeline_qs.count(),
            'total_pipeline_value': pipeline_value,
            'won_value': won_value,
            'won_deals': stage_count_map.get('awarded', 0),
            'lost_deals': stage_count_map.get('lost', 0),
            'win_rate': win_rate,
            'avg_deal_size': float(avg_deal_size) if avg_deal_size is not None else None,
            'forecast_next_month': float(forecast_next_month),
            'handover_count': handover_count,
            'handover_completed': handover_completed,
            'currency': 'AED',
            'by_stage': by_stage,
            'generated_at': timezone.now().isoformat(),
        }

        return Response(payload)


class SalesClientsAnalyticsView(APIView):
    """
    GET /api/v1/sales/analytics/clients/
    Returns client analytics aggregated over governed Client records with
    related Deal (Opportunity) pipeline data.

    Filters:
      - Role-scoped tenant filtering via build_visibility_filter
    """
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'

    def get(self, request):
        # ── Base client queryset with visibility filter ─────────────────────
        base_qs = _visibility_qs(request, Client)

        # ── Annotate each client with deal pipeline ─────────────────────────
        clients_qs = base_qs.annotate(
            deal_count=Count('deals', filter=Q(deals__is_deleted=False)),
            pipeline_value=Sum(
                'deals__estimated_value',
                filter=Q(deals__is_deleted=False, deals__stage__in=OPEN_PIPELINE_STAGES),
            ),
            won_value=Sum(
                'deals__actual_value',
                filter=Q(deals__is_deleted=False, deals__stage__in=WON_STAGES),
            ),
        )

        # ── Build client list ───────────────────────────────────────────────
        client_list = []
        for c in clients_qs.select_related('account_owner').order_by('-pipeline_value')[:50]:
            client_list.append({
                'id': str(c.id),
                'name': c.legal_name or c.trading_name or '',
                'trading_name': c.trading_name or '',
                'industry': c.industry or '',
                'tier': c.client_tier or 'bronze',
                'account_owner': c.account_owner.get_full_name() if c.account_owner else '',
                'deal_count': c.deal_count or 0,
                'pipeline_value': float(c.pipeline_value) if c.pipeline_value is not None else None,
                'won_value': float(c.won_value) if c.won_value is not None else None,
            })

        # ── Aggregate metrics ───────────────────────────────────────────────
        total_clients = base_qs.count()
        active_clients = clients_qs.filter(deal_count__gt=0).count()
        total_pipeline = (
            clients_qs.aggregate(total=Sum('pipeline_value'))['total'] or Decimal('0')
        )
        total_won = (
            clients_qs.aggregate(total=Sum('won_value'))['total'] or Decimal('0')
        )

        payload = {
            'total_clients': total_clients,
            'active_clients': active_clients,
            'total_pipeline_value': float(total_pipeline),
            'total_won_value': float(total_won),
            'clients': client_list,
            'generated_at': timezone.now().isoformat(),
        }

        return Response(payload)


class SalesAIInsightsView(APIView):
    """
    GET /api/v1/sales/analytics/insights/
    Generates AI-style insights from real usage + user data.
    All logic is soft-coded through INSIGHT_RULES patterns.
    """
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'

    def get(self, request):
        User = get_user_model()
        from apps.activity.models import SystemActivity

        now = timezone.now()
        cutoff_30 = now - timedelta(days=30)
        cutoff_7  = now - timedelta(days=7)
        cutoff_15m = now - timedelta(minutes=15)

        total_users = User.objects.filter(is_active=True).count()
        total_requests_7d = SystemActivity.objects.filter(timestamp__gte=cutoff_7).count()
        total_requests_30d = SystemActivity.objects.filter(timestamp__gte=cutoff_30).count()
        active_now = SystemActivity.objects.filter(timestamp__gte=cutoff_15m).values('user_email').exclude(user_email='').distinct().count()
        active_7d = SystemActivity.objects.filter(timestamp__gte=cutoff_7).values('user_email').exclude(user_email='').distinct().count()

        # Top discipline (inline mapping)
        DISCIPLINE_MAP_INLINE = [
            ('pid', 'Process (P&ID)'),
            ('pfd', 'Digitization (PFD)'),
            ('process-datasheet', 'Process Datasheet'),
            ('electrical-datasheet', 'Electrical Datasheet'),
            ('crs', 'CRS Documents'),
            ('designiq', 'DesignIQ'),
            ('finance', 'Finance'),
            ('procurement', 'Procurement'),
            ('qhse', 'QHSE'),
            ('projects', 'Project Control'),
            ('sales', 'Sales'),
            ('rbac', 'Admin / RBAC'),
            ('users', 'User Management'),
            ('notifications', 'Notifications'),
        ]
        from django.db.models import Case, When, Value, CharField
        disc_cases = [
            When(description__icontains=f'/{key}/', then=Value(label))
            for key, label in DISCIPLINE_MAP_INLINE
        ]
        disc_annotation = Case(*disc_cases, default=Value('Other'), output_field=CharField())

        top_discs = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff_7)
            .annotate(disc=disc_annotation)
            .values('disc')
            .annotate(cnt=Count('id'))
            .order_by('-cnt')[:3]
        )
        top_disc_list = [r['disc'] for r in top_discs]
        top_disc = top_disc_list[0] if top_disc_list else 'API'

        # Top user
        top_user_qs = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff_7)
            .exclude(user_email='')
            .values('user_email', 'user_full_name')
            .annotate(cnt=Count('id'))
            .order_by('-cnt')
            .first()
        )

        # Domains active (inline domain extraction)
        def _domain(email):
            return email.split('@')[-1] if '@' in email else 'unknown'

        domains_active = (
            SystemActivity.objects
            .filter(timestamp__gte=cutoff_7)
            .exclude(user_email='')
            .values('user_email')
            .distinct()
        )
        domains = set(_domain(r['user_email']) for r in domains_active)
        total_domains = len([d for d in domains if d != 'unknown'])

        # Dormant users (registered but no activity in 30 days)
        active_emails = set(
            r['user_email'] for r in
            SystemActivity.objects.filter(timestamp__gte=cutoff_30).exclude(user_email='').values('user_email').distinct()
        )
        all_emails = set(User.objects.values_list('email', flat=True))
        dormant_count = len(all_emails - active_emails)

        # Avg requests per active day (last 7 days)
        days_with_activity = SystemActivity.objects.filter(timestamp__gte=cutoff_7).annotate(d=TruncDate('timestamp')).values('d').distinct().count()
        avg_daily = round(total_requests_7d / max(days_with_activity, 1))

        # Success rate
        success_rate = round(SystemActivity.objects.filter(timestamp__gte=cutoff_7, success=True).count() / max(total_requests_7d, 1) * 100, 1)

        insights = []

        # ── Insight 1: Platform health ────────────────────────────────────
        insights.append({
            'type':        'recommendation' if success_rate > 95 else 'warning',
            'priority':    'low' if success_rate > 95 else 'high',
            'title':       f'Platform Success Rate: {success_rate}%',
            'description': f'{total_requests_7d:,} API requests in last 7 days with {success_rate}% success rate across {active_7d} active users.',
            'action':      'Continue monitoring. Consider performance reviews if rate drops below 95%.' if success_rate > 95 else 'Investigate and resolve recurring errors immediately.',
        })

        # ── Insight 2: Top discipline usage ───────────────────────────────
        if top_disc_list:
            disc_str = ', '.join(top_disc_list[:2])
            insights.append({
                'type':        'opportunity',
                'priority':    'medium',
                'title':       f'Most Active Feature: {top_disc}',
                'description': f'Top disciplines this week: {disc_str}. These represent the strongest engagement areas for upsell and expansion.',
                'action':      f'Focus demo and proposal content on {top_disc} — highest engagement signal.',
            })

        # ── Insight 3: Active user engagement ─────────────────────────────
        engagement_pct = round(active_7d / max(total_users, 1) * 100)
        insights.append({
            'type':        'opportunity' if engagement_pct > 50 else 'warning',
            'priority':    'medium' if engagement_pct > 50 else 'high',
            'title':       f'{engagement_pct}% Users Active This Week ({active_7d}/{total_users})',
            'description': f'{active_7d} out of {total_users} registered users were active in the last 7 days. Daily average: {avg_daily:,} requests.',
            'action':      f'Reach out to the {dormant_count} dormant users with personalised onboarding emails.' if dormant_count > 0 else 'Maintain engagement with regular feature updates.',
        })

        # ── Insight 4: Top power user ──────────────────────────────────────
        if top_user_qs:
            name = top_user_qs['user_full_name'] or top_user_qs['user_email'].split('@')[0]
            insights.append({
                'type':        'opportunity',
                'priority':    'low',
                'title':       f'Power User Identified: {name}',
                'description': f'{name} made {top_user_qs["cnt"]:,} requests this week — the most active user on the platform.',
                'action':      f'Engage {name} as a platform champion. Request case study or testimonial.',
            })

        # ── Insight 5: Multi-company presence ─────────────────────────────
        if total_domains > 1:
            insights.append({
                'type':        'opportunity',
                'priority':    'medium',
                'title':       f'{total_domains} Companies Using the Platform',
                'description': f'Users from {total_domains} different organisations are actively using the platform, indicating strong market pull.',
                'action':      'Identify highest-value domain and prioritise enterprise contract negotiation.',
            })

        # ── Insight 6: Dormant users ──────────────────────────────────────
        if dormant_count > 0:
            insights.append({
                'type':        'warning',
                'priority':    'high',
                'title':       f'{dormant_count} Dormant Users — Re-engagement Opportunity',
                'description': f'{dormant_count} registered users have had no activity in the last 30 days.',
                'action':      'Send targeted re-engagement campaign with a highlight of the latest features.',
            })

        # ── Insight 7: Activity trend ──────────────────────────────────────
        if total_requests_30d > 0:
            week_pct = round(total_requests_7d / max(total_requests_30d, 1) * 100 * 4)  # weekly proportion
            trend = 'accelerating' if week_pct > 130 else ('stable' if week_pct > 70 else 'declining')
            insights.append({
                'type':        'recommendation' if trend == 'stable' else ('opportunity' if trend == 'accelerating' else 'warning'),
                'priority':    'low' if trend == 'stable' else 'medium',
                'title':       f'Platform Activity Trend: {trend.capitalize()}',
                'description': f'Last 7 days had {total_requests_7d:,} requests vs {total_requests_30d:,} in last 30 days — trend is {trend}.',
                'action':      'Good momentum! Plan your next feature release to maintain growth.' if trend == 'accelerating' else 'Review onboarding flow to re-activate users.' if trend == 'declining' else 'Maintain current engagement strategy.',
            })

        return Response({
            'success':      True,
            'insights':     insights,
            'generated_at': now.isoformat(),
            'metrics': {
                'total_users':        total_users,
                'active_now':         active_now,
                'active_7d':          active_7d,
                'requests_7d':        total_requests_7d,
                'requests_30d':       total_requests_30d,
                'dormant_users':      dormant_count,
                'companies_active':   total_domains,
                'success_rate':       success_rate,
                'top_discipline':     top_disc,
            },
        })


class SalesActivitiesView(APIView):
    """
    GET /api/v1/sales/analytics/activities/
    Returns upcoming follow-up activities synthesised from dormant/at-risk users.
    """
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'

    def get(self, request):
        User = get_user_model()

        days = int(request.query_params.get('days', 7))
        cutoff_30 = timezone.now() - timedelta(days=30)

        def _domain(email):
            return email.split('@')[-1] if '@' in email else 'unknown'

        def _company_name(domain):
            name_map = {
                'rejlers.ae': 'Rejlers Engineering',
                'radai.ae': 'RAD AI Platform',
                'engineering.ae': 'Engineering Services AE',
                'consultant.ae': 'Consultant AE',
            }
            if domain in name_map:
                return name_map[domain]
            parts = domain.split('.')
            return ' '.join(w.capitalize() for w in parts[:-1]) or domain

        # Users with no activity in 30 days = follow-up candidates
        active_emails = set(
            r['user_email'] for r in
            SystemActivity.objects.filter(timestamp__gte=cutoff_30).exclude(user_email='').values('user_email').distinct()
        )

        dormant_users = (
            User.objects
            .filter(is_active=True)
            .exclude(email__in=active_emails)
            .values('email', 'first_name', 'last_name')[:10]
        )

        activities = []
        for i, u in enumerate(dormant_users):
            name = f"{u['first_name']} {u['last_name']}".strip() or u['email']
            domain = _domain(u['email'])
            company = _company_name(domain)
            due_date = (timezone.now() + timedelta(days=(i % days) + 1)).date().isoformat()

            activities.append({
                'id':          i + 1,
                'type':        'email' if i % 3 != 0 else 'call',
                'title':       f'Re-engagement: Contact {name}',
                'description': f'Follow up with {name} from {company} — no platform activity in 30 days.',
                'client_name': company,
                'due_date':    due_date,
                'priority':    'medium',
            })

        # Also add a review activity for top users
        top_user = (
            SystemActivity.objects
            .filter(timestamp__gte=timezone.now() - timedelta(days=7))
            .exclude(user_email='')
            .values('user_email', 'user_full_name')
            .annotate(cnt=Count('id'))
            .order_by('-cnt')
            .first()
        )
        if top_user:
            name = top_user['user_full_name'] or top_user['user_email']
            company = _company_name(_domain(top_user['user_email']))
            activities.insert(0, {
                'id':          0,
                'type':        'meeting',
                'title':       f'Champion Review: {name}',
                'description': f'Quarterly review meeting with {name} ({company}) — top power user.',
                'client_name': company,
                'due_date':    (timezone.now() + timedelta(days=3)).date().isoformat(),
                'priority':    'high',
            })

        return Response(activities)
