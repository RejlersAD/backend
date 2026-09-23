"""Workbook-led executive portfolio; immutable snapshot facts and explicit scope."""
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed
from .access import can_upload_workbook
from .scope import IDENTITY, workbook_scope

logger = logging.getLogger(__name__)
CENT = Decimal('0.01')
METRICS = (
    ('total_revenue_actual', 'Total revenue actual', 'actual_revenue', 'actual_revenue_aed',
     'POC revenue for the current reporting month through the workbook reporting date.'),
    ('current_forecast', 'Current forecast', 'forecast_revenue', 'current_forecast_aed',
     'POC current forecast for the reporting month.'),
    ('pm_forecast', 'PM forecast', 'pm_forecast', 'pm_forecast_aed',
     'Project manager forecast for the reporting month.'),
    ('variance', 'Forecast variance', 'variance', 'forecast_variance_aed',
     'Current forecast minus PM forecast for the same reporting month.'),
    ('total_backlog', 'Total backlog', 'backlog', 'backlog_aed',
     'Workbook Backlog Without PT values across the report rows; no additional inclusion-flag filter.'),
    ('total_poc_risk', 'Total POC risk', 'poc_risk', 'poc_risk_aed',
     'Positive POC overclaim exposure in the primary POC section; resource-deputation cells have a different meaning.'),
)
PROJECT_FIELDS = ('contract_value_aed', 'recognized_revenue_aed', 'poc_pct', 'eddr_pct', 'target_margin_pct',
                  'forecast_margin_pct', 'ld_exposure_aed', 'prolongation_cost_aed')
DEFINITIONS = [item[4] for item in METRICS] + [
    'Workbook monetary values are saved AED amounts. Connected invoices retain their recorded currencies; no currency conversion is applied.',
    'Blank or invalid facts remain missing. A known subtotal is not a complete total.',
    'Workbook identities are reconciled to registered projects by exact project codes. Unmatched or ambiguous identities require review; reporting does not create or update operational records.',
    'Connected departmental records retain their own permissions, source dates and currencies. Workbook forecasts do not replace recorded invoices, approvals or project progress.',
    'Capacity and published PM KPI scores are workbook-wide facts and are withheld for row filters or limited access.',
]


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _amount(value):
    number = _number(value)
    return str(number.quantize(CENT)) if number is not None else None


def _sum(values, *, unit='currency'):
    values = [_number(value) for value in values]
    known = [value for value in values if value is not None]
    missing = len(values) - len(known)
    subtotal = _amount(sum(known, Decimal(0))) if known else None
    return {'value': subtotal if values and not missing else None, 'known_value': subtotal,
            'status': 'partial' if missing else 'available' if values else 'unavailable',
            'missing_count': missing, 'included_rows': len(values), 'unit': unit,
            'currency': 'AED' if unit == 'currency' else None, 'basis': 'source_rows'}


def _primary(row):
    return (row.get('extra') or {}).get('section') != 'resource_deputation'


def _facts(row):
    extra = row.get('extra') or {}
    executive = extra.get('executive') or {}
    overclaim = _number(row.get('overclaim_aed')) if _primary(row) else None
    return {
        'actual_revenue': row.get('period_revenue_aed'),
        'forecast_revenue': executive.get('current_forecast_aed'),
        'pm_forecast': executive.get('pm_forecast_aed'),
        'variance': executive.get('forecast_variance_aed'),
        'backlog': row.get('backlog_without_pt_aed'),
        'poc_risk': max(overclaim, Decimal(0)) if overclaim is not None else None,
    }


def _metrics(rows):
    return {key: _sum(_facts(row)[key] for row in rows if key != 'poc_risk' or _primary(row))
            for _, _, key, _, _ in METRICS}


def _identity(row):
    return {key: row.get(key) or '' for key in IDENTITY}


def _signed_poc_gap(row):
    if not _primary(row):
        return None
    poc, progress, contract = (_number(row.get(key)) for key in ('poc_pct', 'eddr_pct', 'contract_value_aed'))
    return (poc - progress) * contract / 100 if all(value is not None for value in (poc, progress, contract)) else None


def _project(row):
    result = _identity(row)
    extra = (row.get('extra') or {}).get('executive') or {}
    result.update(id=str(row['id']), source_row=row['source_row'], currency='AED',
                  original_currency=row.get('currency') or None,
                  **{key: _amount(value) for key, value in _facts(row).items()},
                  **{key: _amount(row.get(key)) for key in PROJECT_FIELDS},
                  signed_overclaim_aed=_amount(_signed_poc_gap(row)))
    for key in ('direct_cost_aed', 'labor_cost_aed', 'other_cost_aed', 'head_office_cost_aed',
                'resource_cost_aed', 'actual_manhours', 'delay_days', 'ld_min_pct', 'ld_max_pct',
                'extension_resources_per_day', 'extension_cost_per_hour_aed'):
        result[key] = _amount(extra.get(key))
    for key in ('ld_possible', 'ld_frequency', 'remarks', 'award_status', 'priority',
                'eddr_status', 'actual_completion', 'handover_date'):
        result[key] = extra.get(key)
    result['section'] = (row.get('extra') or {}).get('section') or 'primary'
    result['invoicing'] = (row.get('extra') or {}).get('invoicing')
    for key in ('start_date', 'contractual_finish', 'forecast_finish'):
        value = row.get(key)
        result[key] = value.isoformat() if value else None
    return result


def _breakdown(rows, field):
    groups = defaultdict(list)
    for row in rows:
        groups[row.get(field) or 'Unassigned'].append(row)
    result = []
    for label, members in sorted(groups.items(), key=lambda item: item[0].casefold()):
        metrics = _metrics(members)
        result.append({'label': label, 'project_count': len({row['project_code'] for row in members}),
                       'row_count': len(members), **{key: value['value'] for key, value in metrics.items()},
                       'coverage': metrics})
    return result


def _month(value):
    text = str(value or '')
    return text[:7] + '-01' if len(text) >= 10 and text[4] == '-' and text[7] == '-' else None


def _forecast(rows, current_period):
    observations, periods = [], set()
    for row in rows:
        extra = row.get('extra') or {}
        actual = {}
        # Several saved cutoffs can belong to one month; the latest is the
        # month's to-date observation, not an additional month's revenue.
        for value in sorted(extra.get('history') or [], key=lambda item: item.get('date') or ''):
            period = _month(value.get('date'))
            if period:
                actual[period] = value.get('period_revenue_aed')
        forecast = {}
        for value in extra.get('forecasts') or []:
            period = _month(value.get('period'))
            if period:
                forecast[period] = None if period in forecast else value.get('revenue_aed')
        executive = extra.get('executive') or {}
        pm = {_month(executive.get('period')): executive.get('pm_forecast_aed')}
        periods.update(actual)
        periods.update(forecast)
        periods.add(current_period)
        observations.append({'actual_revenue': actual, 'forecast_revenue': forecast, 'pm_forecast': pm})
    result = []
    for period in sorted(periods):
        metrics = {key: _sum(item[key].get(period) for item in observations)
                   for key in ('actual_revenue', 'forecast_revenue', 'pm_forecast')}
        result.append({'period': period, **{key: value['value'] for key, value in metrics.items()},
                       'known_values': {key: value['known_value'] for key, value in metrics.items()},
                       'coverage': {key: {'included_rows': value['included_rows'],
                                         'known_rows': value['included_rows'] - value['missing_count'],
                                         'missing_count': value['missing_count'], 'status': value['status']}
                                    for key, value in metrics.items()},
                       'status': 'partial' if any(value['missing_count'] for value in metrics.values()) else 'available',
                       'missing_count': {key: value['missing_count'] for key, value in metrics.items()}})
    return result


def _risks(rows, metrics):
    totals = {'poc': metrics['poc_risk'],
              'ld': _sum(row.get('ld_exposure_aed') for row in rows),
              'prolongation': _sum(row.get('prolongation_cost_aed') for row in rows),
              'net_overclaim': _sum(_signed_poc_gap(row) for row in rows if _primary(row))}
    records = []
    for row in rows:
        for kind, label, value in (
            ('poc', 'POC overclaim', _facts(row)['poc_risk']),
            ('ld', 'Liquidated damages exposure', row.get('ld_exposure_aed')),
            ('prolongation', 'Prolongation cost', row.get('prolongation_cost_aed')),
        ):
            number = _number(value)
            if number is not None and number > 0:
                records.append({**_identity(row), 'id': f"{row['id']}-{kind}", 'type': kind,
                                'label': label, 'amount': _amount(number), 'currency': 'AED',
                                'detail': f'{label} recorded in the uploaded POC workbook.'})
    records.sort(key=lambda item: (-Decimal(item['amount']), item['project_code'], item['type']))
    missing = sum(totals[key]['missing_count'] for key in ('poc', 'ld', 'prolongation'))
    return {'rows': records, 'total_rows': len(records), 'totals': totals, 'missing_count': missing,
            'status': 'partial' if missing else 'available' if rows else 'unavailable'}


def _invoicing(rows, warnings=()):
    fields = ('contract_value_aed', 'invoiced_aed', 'balance_aed', 'comparison_revenue_aed', 'variance_aed')
    comparison_fields = ('comparison_revenue_aed', 'variance_aed')
    records, eligible = [], []
    coverage = {'included_rows': 0, 'excluded_rows': 0, 'unknown_inclusion_rows': 0, 'unmatched_rows': 0}
    for row in rows:
        invoice = (row.get('extra') or {}).get('invoicing')
        if not invoice:
            coverage['unmatched_rows'] += 1
            continue
        if invoice and invoice.get('included') is False:
            coverage['excluded_rows'] += 1
            continue
        if invoice.get('included') is True:
            eligible.append(invoice)
            coverage['included_rows'] += 1
        else:
            coverage['unknown_inclusion_rows'] += 1
        records.append({**_identity(row), **invoice,
                        **{key: _amount(invoice.get(key)) for key in (*fields, 'invoice_pct', 'variance_pct')}})
    totals = {key: _sum(value.get(key) for value in eligible) for key in fields}
    periods = defaultdict(list)
    for invoice in eligible:
        try:
            baseline = date.fromisoformat(str(invoice.get('comparison_date'))).isoformat()
        except ValueError:
            baseline = None
        periods[baseline].append(invoice)
    comparison_dates = sorted(period for period in periods if period)
    unknown_dates = len(periods.get(None, []))
    mixed_dates = len(comparison_dates) > 1
    period_status = ('mixed' if mixed_dates else 'unknown' if unknown_dates else
                     'single' if comparison_dates else 'unavailable')
    comparison_periods = []
    for period, members in sorted(periods.items(), key=lambda item: item[0] or '9999'):
        group_totals = {key: _sum(invoice.get(key) for invoice in members) for key in comparison_fields}
        if period is None:
            for metric in group_totals.values():
                metric.update(value=None, status='partial', basis='unknown_comparison_period',
                              known_value_label='Subtotal with unknown comparison dates')
        comparison_periods.append({'comparison_date': period, 'included_rows': len(members), 'totals': group_totals})
    for key in comparison_fields:
        totals[key].update(comparison_dates=comparison_dates, unknown_period_count=unknown_dates,
                           mixed_comparison_periods=mixed_dates, period_status=period_status)
        if mixed_dates or unknown_dates:
            totals[key].update(value=None, status='partial',
                               basis='mixed_comparison_periods' if mixed_dates else 'unknown_comparison_period',
                               known_value_label='Mixed-period subtotal' if mixed_dates else 'Subtotal with unknown comparison dates')
    source_warnings = [item for item in warnings if isinstance(item, dict) and item.get('sheet') == 'INVOICING. STATUS']
    if mixed_dates or unknown_dates:
        source_warnings.append({'sheet': 'INVOICING. STATUS', 'code': 'mixed_comparison_periods' if mixed_dates else 'unknown_comparison_period',
                                'comparison_dates': comparison_dates, 'unknown_period_count': unknown_dates})
    return {'rows': records, 'total_rows': len(records), 'totals': totals,
            'status': 'partial' if coverage['unmatched_rows'] or coverage['unknown_inclusion_rows'] or any(value['status'] == 'partial' for value in totals.values()) else 'available' if eligible else 'unavailable',
            'comparison_dates': comparison_dates, 'comparison_period_status': period_status,
            'comparison_periods': comparison_periods, 'coverage': coverage, 'warnings': source_warnings,
            'description': 'Totals cover matched invoice rows explicitly included by the source. Revenue comparisons require one known baseline date; dated subtotals remain separate.'}


def _pm_performance(rows, reconciliation, allow_global):
    groups = defaultdict(list)
    for row in rows:
        groups[row.get('pm') or 'Unassigned'].append(row)
    published = reconciliation.get('pm_kpi') or {}
    kpis = {str(item.get('pm') or '').casefold(): item for item in published.get('entries') or []} if allow_global else {}
    result = []
    for group in _breakdown(rows, 'pm'):
        members = groups[group['label']]
        costs = _sum((row.get('extra') or {}).get('executive', {}).get('direct_cost_aed') for row in members)
        revenue, cost = _number(group['actual_revenue']), _number(costs['value'])
        margin = _amount((revenue - cost) / revenue * 100) if revenue and cost is not None else None
        kpi = kpis.get(group['label'].casefold())
        result.append({**group, 'pm': group['label'], 'direct_cost_aed': costs['value'],
                       'delivered_margin_pct': margin, 'cost_coverage': costs,
                       'kpi': {**kpi, 'period': published.get('period'), 'period_label': published.get('period_label')}
                       if kpi else None})
    return {'rows': result, 'status': 'available' if rows else 'unavailable',
            'kpi_status': 'available' if kpis else 'restricted_scope' if not allow_global else 'unavailable',
            'kpi_period': published.get('period') if allow_global else None,
            'kpi_period_label': published.get('period_label') if allow_global else None,
            'weights': published.get('weights') or {} if allow_global else {},
            'description': 'Revenue and cost derive from the filtered POC rows. Published PM KPI scores retain their source period; CPI is a workbook score, not an inferred EVM index.',
            'warnings': published.get('warnings') or [] if allow_global else []}


def _capacity(reconciliation, allow_global):
    description = 'Workbook-wide dated resource plan in manhours; no conversion to headcount or FTE.'
    if not allow_global:
        return {'rows': [], 'staffing': [], 'unit': 'manhours', 'status': 'restricted_scope',
                'description': description + ' Clear filters and use full-source access to view this plan.'}
    source = reconciliation.get('capacity') or {}
    records = []
    for value in source.get('periods') or []:
        demand, capacity = _number(value.get('demand_manhours')), _number(value.get('adjusted_capacity_manhours'))
        records.append({**value, 'gap_manhours': _amount(capacity - demand)
                        if demand is not None and capacity is not None else None})
    missing = sum(value.get(key) is None for value in records
                  for key in ('demand_manhours', 'gross_capacity_manhours', 'adjusted_capacity_manhours'))
    return {'rows': records, 'staffing': source.get('staffing') or [], 'unit': 'manhours',
            'status': 'partial' if missing else 'available' if records else 'unavailable',
            'missing_count': missing, 'description': description, 'warnings': source.get('warnings') or []}


def _empty(status, *, enabled=False, description=''):
    return {'enabled': enabled, 'status': status, 'source': None, 'currency': 'AED', 'period': None,
            'scope': {'label': 'Accessible workbook rows', 'full_source': False, 'filtered': False},
            'can_upload': False, 'kpis': [], 'filters': {'business_units': [], 'clients': [], 'project_managers': []},
            'breakdowns': {'business_unit': [], 'client': [], 'project_manager': []}, 'forecast': [],
            'projects': {'rows': [], 'total_rows': None, 'returned_rows': 0, 'truncated': False},
            'risks': {'rows': [], 'total_rows': None, 'totals': {}, 'status': status, 'missing_count': None},
            'invoicing': {'rows': [], 'totals': {}, 'status': status},
            'connections': {'rows': [], 'totals': {}, 'status': status},
            'pm_performance': {'rows': [], 'status': status},
            'capacity': {'rows': [], 'status': status, 'unit': 'manhours'},
            'definitions': DEFINITIONS, 'description': description}


def build_revenue_dashboard(user, *, search='', pm='', business_unit='', client='', limit=200, offset=0):
    """Read the complete authorized immutable scope before paginating its register."""
    if not module_action_allowed(user, 'project_control', 'read'):
        return _empty('restricted', enabled=True, description='Project Control read access is required.')
    try:
        from .models import PortfolioSource
        with transaction.atomic():
            source = PortfolioSource.objects.select_related('active_snapshot').filter(key='poc').first()
            if source is None or source.active_snapshot is None:
                empty = _empty('unavailable', description='No portfolio workbook has been imported.')
                empty['can_upload'] = can_upload_workbook(user)
                return empty
            snapshot = source.active_snapshot
            scope = workbook_scope(snapshot, user, search=search, pm=pm, business_unit=business_unit, client=client)
            full_source, all_rows, rows = scope['full_source'], scope['all_rows'], scope['rows']
            choices, filtered = scope['filters'], scope['filtered']
            allow_global = full_source and not filtered
            metrics = _metrics(rows)
            reconciliation = snapshot.reconciliation or {}
            summary = reconciliation.get('executive_summary') or {}
            summary_matches, summary_mismatches = [], []
            if allow_global and str(summary.get('reporting_date'))[:10] == snapshot.reporting_date.isoformat():
                for _, _, key, summary_key, _ in METRICS:
                    saved = _number((summary.get('totals') or {}).get(summary_key))
                    known = _number(metrics[key]['known_value'])
                    if saved is not None and known is not None:
                        if abs(saved - known) <= CENT * 2:
                            summary_matches.append(key)
                            if metrics[key]['missing_count']:
                                metrics[key].update(value=_amount(saved), basis='cached_workbook_summary')
                        else:
                            summary_mismatches.append(key)
            kpis = [{**metrics[key], 'id': identifier, 'label': label, 'description': description,
                     'period': snapshot.reporting_date.isoformat()[:7]}
                    for identifier, label, key, _, description in METRICS]
            age = max(0, (timezone.localdate() - snapshot.reporting_date).days)
            stale = age > int(getattr(settings, 'PORTFOLIO_REPORT_STALE_DAYS', 7))
            count = len(rows)
            risks = _risks(rows, metrics)
            risks.update(history=reconciliation.get('risk_history') or [] if allow_global else [],
                         poc_history=reconciliation.get('poc_risk_history') or [] if allow_global else [],
                         history_status='available' if allow_global and (reconciliation.get('risk_history') or reconciliation.get('poc_risk_history'))
                         else 'unavailable' if allow_global else 'restricted_scope')
            if allow_global and str(summary.get('reporting_date'))[:10] == snapshot.reporting_date.isoformat():
                for key, summary_key in (('ld', 'ld_exposure_aed'), ('prolongation', 'prolongation_cost_aed')):
                    saved = _number((summary.get('totals') or {}).get(summary_key))
                    metric = risks['totals'][key]
                    known = _number(metric['known_value'])
                    if saved is not None and known is not None:
                        if abs(saved - known) <= CENT * 2:
                            summary_matches.append(summary_key)
                            if metric['missing_count']:
                                metric.update(value=_amount(saved), basis='cached_workbook_summary')
                        else:
                            summary_mismatches.append(summary_key)
            try:
                from .connections import build_project_connections
                with transaction.atomic():
                    connections = build_project_connections(user, rows, limit=limit, offset=offset)
            except Exception:
                logger.exception('Portfolio project connections unavailable')
                connections = {'status': 'error', 'rows': [], 'totals': {},
                               'description': 'Recorded project connections could not be loaded. Refresh to retry.'}
            return {
                'enabled': True, 'status': 'unavailable' if not rows else 'partial' if stale or source.last_error or summary_mismatches or any(item['status'] == 'partial' for item in kpis) else 'available',
                'currency': 'AED', 'period': snapshot.reporting_date.isoformat()[:7],
                'source': {'kind': 'sharepoint' if source.remote_identity else 'manual',
                           'file_name': snapshot.file_name, 'reporting_date': snapshot.reporting_date.isoformat(),
                           'imported_at': snapshot.imported_at.isoformat(), 'snapshot_id': snapshot.pk,
                           'parser_version': snapshot.parser_version, 'is_stale': stale,
                           'has_validation_warnings': bool(snapshot.warnings)},
                'scope': {'label': 'Uploaded portfolio source' if full_source else 'Workbook rows matched to accessible registered projects',
                          'full_source': full_source, 'filtered': filtered, 'accessible_rows': len(all_rows),
                          'row_count': count, 'project_count': len({row['project_code'] for row in rows})},
                'can_upload': full_source, 'kpis': kpis, 'filters': choices,
                'connections': connections,
                'breakdowns': {'business_unit': _breakdown(rows, 'business_unit'),
                               'client': _breakdown(rows, 'client'), 'project_manager': _breakdown(rows, 'pm')},
                'forecast': _forecast(rows, _month(snapshot.reporting_date)),
                'projects': {'rows': [_project(row) for row in rows[offset:offset + limit]],
                             'total_rows': count, 'returned_rows': len(rows[offset:offset + limit]),
                             'truncated': offset + limit < count, 'offset': offset, 'limit': limit},
                'risks': risks, 'invoicing': _invoicing(rows, snapshot.warnings if allow_global else
                                                       [warning for row in rows for warning in (row.get('extra') or {}).get('warnings', [])]),
                'pm_performance': _pm_performance(rows, reconciliation, allow_global),
                'capacity': _capacity(reconciliation, allow_global), 'definitions': DEFINITIONS,
                'reconciliation': {'summary_matches': summary_matches, 'summary_mismatches': summary_mismatches,
                                   'summary_scope': 'full_source' if allow_global else 'withheld'},
            }
    except Exception:
        logger.exception('Executive workbook portfolio unavailable')
        return _empty('error', enabled=True, description='The uploaded portfolio could not be read. Try again later.')
