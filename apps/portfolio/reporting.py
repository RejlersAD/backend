"""Database-only, authorized views of workbook-reported portfolio facts."""
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed
from .access import can_upload_workbook


logger = logging.getLogger(__name__)
MONEY_FIELDS = (
    'contract_value_aed', 'recognized_revenue_aed', 'period_revenue_aed',
    'backlog_without_pt_aed', 'backlog_with_pt_aed', 'overclaim_aed',
)
ROW_FIELDS = (
    'project_code', 'subproject_code', 'title', 'pm', 'pc', 'business_unit',
    'client', 'scope_type', 'currency', 'source_row', 'include_without_pt',
    'include_with_pt', *MONEY_FIELDS, 'poc_pct', 'eddr_pct', 'target_margin_pct',
    'forecast_margin_pct', 'ld_exposure_aed', 'prolongation_cost_aed',
    'start_date', 'contractual_finish', 'forecast_finish',
)
DESCRIPTION = (
    'Workbook-reported figures, separate from approved project-control assessments. '
    'Amounts are the workbook\'s AED values. Totals use rows marked Without PT = Yes; '
    'Backlog With PT uses With PT = Yes. Missing amounts or inclusion flags withhold '
    'the affected total. Financial POC and EDDR progress are separate measures.'
)


def _empty(status, description, *, can_upload=False):
    return {
        'status': status, 'source': None, 'scope': {'label': 'Accessible workbook rows'},
        'totals': {}, 'row_count': None, 'project_count': None,
        'returned_rows': 0, 'truncated': False, 'rows': [], 'forecast': [],
        'description': description, 'can_upload': can_upload,
    }


def _visible_rows(snapshot, user):
    from apps.core.project_models import Project
    from apps.project_control.access import accessible_enterprise_projects

    visible = accessible_enterprise_projects(user).values('code')
    known = Project.objects.values('code')
    # Prefer an exact subproject code. A hidden/deleted subproject must never
    # inherit visibility from a parent that happens to be accessible.
    visibility = Q(subproject_code__in=visible) | (
        ~Q(subproject_code__in=known) & Q(project_code__in=visible)
    )
    if user.is_superuser:
        # An administrator may reconcile unregistered workbook identities.
        # Registered deleted/inaccessible records remain excluded.
        visibility |= ~Q(subproject_code__in=known) & ~Q(project_code__in=known)
    return snapshot.rows.filter(visibility)


def _totals(rows):
    expressions = {}
    for field in MONEY_FIELDS:
        flag = 'include_with_pt' if field == 'backlog_with_pt_aed' else 'include_without_pt'
        eligible = Q(**{flag: True})
        expressions[field + '_total'] = Sum(field, filter=eligible)
        expressions[field + '_eligible'] = Count('pk', filter=eligible)
        expressions[field + '_known'] = Count(field, filter=eligible)
        expressions[field + '_unspecified'] = Count('pk', filter=Q(**{flag + '__isnull': True}))
    values = rows.aggregate(**expressions)
    totals = {}
    for field in MONEY_FIELDS:
        eligible = values[field + '_eligible']
        unknown_flags = values[field + '_unspecified']
        missing = eligible - values[field + '_known'] + unknown_flags
        complete = not missing and eligible > 0
        totals[field] = {
            'value': str(values[field + '_total'].quantize(Decimal('0.01'))) if complete else None,
            'currency': 'AED', 'status': 'available' if complete else 'incomplete' if missing else 'unavailable',
            'missing_count': missing, 'included_rows': eligible,
            'unknown_inclusion_count': unknown_flags,
        }
    return totals


def _forecast(rows):
    included = rows.filter(include_without_pt=True)
    expected = included.count()
    unknown_flags = rows.filter(include_without_pt__isnull=True).count()
    periods = defaultdict(lambda: {'sum': Decimal('0'), 'known': 0})
    for extra in included.values_list('extra', flat=True).iterator(chunk_size=500):
        observations = {}
        for observation in (extra or {}).get('forecasts', []):
            period = observation.get('period')
            if not period:
                continue
            periods[period]  # Preserve a period even when all observations are missing.
            try:
                amount = Decimal(str(observation.get('revenue_aed')))
                amount = amount if amount.is_finite() else None
            except (InvalidOperation, ValueError, TypeError):
                amount = None
            # Duplicate months are a parser violation, never additional revenue.
            observations[period] = None if period in observations else amount
        for period, amount in observations.items():
            if amount is not None:
                periods[period]['sum'] += amount
                periods[period]['known'] += 1
    return [
        {'period': period, 'revenue_aed': str(data['sum'].quantize(Decimal('0.01')))
         if data['known'] == expected and not unknown_flags else None,
         'status': 'available' if data['known'] == expected and not unknown_flags else 'incomplete',
         'missing_count': expected - data['known'] + unknown_flags}
        for period, data in sorted(periods.items())
    ]


def build_workbook_report(user, *, search='', pm='', business_unit='', client='', limit=50, offset=0):
    """Never contact Microsoft or open Excel on the dashboard request path."""
    if not module_action_allowed(user, 'project_control', 'read'):
        return _empty('restricted', 'Project Control read access is required for workbook figures.')
    if not apps.is_installed('apps.portfolio'):
        return _empty('unavailable', 'The portfolio workbook source is not configured.')
    try:
        from .models import PortfolioSource
        can_upload = can_upload_workbook(user)
        # Active version is pinned once. All following reads use its immutable ID.
        # Savepoint also isolates failures from surrounding request transactions.
        with transaction.atomic():
            source = PortfolioSource.objects.select_related('active_snapshot').filter(key='poc').first()
            if source is None or source.active_snapshot is None:
                description = ('Upload a portfolio Excel workbook to show workbook figures.' if can_upload
                               else 'No validated portfolio workbook has been imported.')
                return _empty('unavailable', description, can_upload=can_upload)
            snapshot = source.active_snapshot
            rows = _visible_rows(snapshot, user)
            for field, value in [('pm', pm), ('business_unit', business_unit), ('client', client)]:
                if value:
                    rows = rows.filter(**{field + '__iexact': value})
            if search:
                rows = rows.filter(Q(project_code__icontains=search) | Q(subproject_code__icontains=search)
                                   | Q(title__icontains=search) | Q(client__icontains=search)
                                   | Q(pm__icontains=search) | Q(business_unit__icontains=search))
            total = rows.count()
            totals = _totals(rows)
            forecast = _forecast(rows)
            records = list(rows.order_by('project_code', 'subproject_code').values(*ROW_FIELDS)[offset:offset + limit])
            for record in records:
                for field, value in record.items():
                    if isinstance(value, Decimal):
                        record[field] = str(value)
                    elif hasattr(value, 'isoformat'):
                        record[field] = value.isoformat()
            now = timezone.now()
            age = max(0, (timezone.localdate(now) - snapshot.reporting_date).days)
            stale = age > int(getattr(settings, 'PORTFOLIO_REPORT_STALE_DAYS', 7))
            from .sync import sync_enabled
            scheduled = sync_enabled()
            interval = max(60, int(getattr(settings, 'PORTFOLIO_SYNC_INTERVAL_SECONDS', 3600)))
            sync_late = bool(scheduled and source.remote_identity and source.last_success_at
                             and now - source.last_success_at > timedelta(seconds=2 * interval))
            partial = any(item['status'] == 'incomplete' for item in totals.values())
            partial |= any(item['status'] == 'incomplete' for item in forecast)
            return {
                'status': 'partial' if partial or source.last_error or stale or sync_late else 'available',
                'can_upload': can_upload,
                'source': {
                    'file_name': snapshot.file_name, 'reporting_date': snapshot.reporting_date.isoformat(),
                    'imported_at': snapshot.imported_at.isoformat(),
                    'last_uploaded_at': source.last_success_at.isoformat()
                    if not source.remote_identity and source.last_success_at else None,
                    'kind': 'sharepoint' if source.remote_identity else 'manual', 'sync_enabled': scheduled,
                    'last_success_at': source.last_success_at.isoformat()
                    if source.remote_identity and source.last_success_at else None,
                    'last_attempt_at': source.last_attempt_at.isoformat() if source.last_attempt_at else None,
                    'sync_status': 'error' if source.last_error else 'current'
                    if source.remote_identity and source.last_success_at else 'never',
                    'is_stale': stale, 'data_age_days': age, 'sync_overdue': sync_late,
                    'snapshot_id': snapshot.pk, 'has_validation_warnings': bool(snapshot.warnings),
                },
                'scope': {'label': 'Accessible registered projects and unregistered source rows for reconciliation'
                          if user.is_superuser else 'Workbook rows matched to accessible registered projects'},
                'totals': totals, 'row_count': total,
                'project_count': rows.order_by().values('project_code').distinct().count(),
                'returned_rows': len(records), 'truncated': total > len(records),
                'offset': offset, 'limit': limit, 'rows': records, 'forecast': forecast,
                'description': DESCRIPTION,
            }
    except Exception:
        logger.exception('Portfolio workbook reporting source unavailable')
        return _empty('error', 'Portfolio workbook figures could not be read. Try again later.')
