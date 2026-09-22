"""Invoice-date performance from complete authorised source rows.

Invoiced revenue is the recorded invoice amount, not recognised accounting
revenue. Receipt and outstanding cohorts describe today's source records;
they do not reconstruct historical cash flows or historical balances.
"""
import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.db import transaction
from django.db.models.functions import Trim, Upper
from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed


logger = logging.getLogger(__name__)
ZERO = Decimal('0')
QUANTUM = Decimal('0.01')
AMOUNTS = ('invoiced', 'received', 'outstanding')
COVERAGE_COUNTS = (
    'source_row_count', 'eligible_invoice_count', 'excluded_internal_count',
    'excluded_cancelled_count', 'excluded_credit_note_count',
    'missing_invoice_date_count', 'future_invoice_date_count',
    'outside_window_count', 'missing_invoice_amount_count', 'missing_receipt_count',
    'overpaid_invoice_count', 'negative_invoice_amount_count', 'negative_receipt_count',
)
DEFINITIONS = {
    'invoiced': 'Invoiced revenue is recorded Invoice Amount in its original currency, grouped by invoice issue month. Values retain the source tax basis; no tax removal or foreign exchange conversion is assumed. This is invoice value, not recognised accounting revenue.',
    'received': 'Current cumulative Actual Payment Received against invoices issued in each month. Missing receipts remain unknown. This is not cash collected during that calendar month; the source has no complete payment-event ledger.',
    'outstanding': 'Sum of max(Invoice Amount minus Actual Payment Received, 0) per eligible invoice. Paid-labelled invoices remain included; overpayments never offset other invoices. Missing invoice amounts or receipts remain unknown.',
    'collection_rate': 'Recorded receipts divided by invoiced value for the same invoice-date cohort, only when both are complete and invoiced value is positive. Rates above 100% indicate recorded overpayments; values are not silently capped.',
    'scope': 'Complete authorised external customer invoice register, including paid invoices. Internal, cancelled and credit-note rows are excluded. Missing and future invoice dates are excluded and disclosed. Monetary amounts remain in the selected original currency.',
    'period': 'Monthly cohorts cover the last 12 calendar months through the invoice-date cutoff. YTD means 1 January through that cutoff, not an assumed fiscal year. Current receipt values are not reconstructed as of a historical date.',
    'estimate': 'Illustrative estimate: the average invoiced value of the three completed calendar months before the cutoff, repeated for the next 12 months. Zero months are included. Requires dated source history covering all three months and complete invoiced amounts. This is not an approved Finance forecast or a prediction of secured revenue.',
    'margin': 'Operating margin uses only Finance-approved recognised revenue and matching operating costs for the same month, currency and workspace: (recognised revenue minus operating costs) / recognised revenue. Invoice values and supplier-invoice totals are not substitutes.',
    'plan': 'Only approved Finance inputs with approval evidence are used. Budget and forecast are on the same recorded invoice-value basis and original currency. Workspace plans are withheld when filtering one customer.',
}
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'data' / 'invoice_performance_summary.json'


def _money(value):
    return str(value.quantize(QUANTUM, rounding=ROUND_HALF_UP))


def _month_offset(month, offset):
    serial = month.year * 12 + month.month - 1 + offset
    return date(serial // 12, serial % 12 + 1, 1)


def _unknown_metric():
    return {'amount': None, 'known_amount': None, 'count': None, 'missing_count': None, 'partial': True}


def _unknown_rate():
    return {'value': None, 'status': 'unavailable', 'unit': 'percent'}


@dataclass
class _Amount:
    total: Decimal = ZERO
    count: int = 0
    missing: int = 0

    def add(self, value):
        self.count += 1
        if value is None:
            self.missing += 1
        else:
            self.total += value

    def result(self, currency_known=True):
        partial = bool(self.missing or not currency_known)
        known = self.count == 0 or self.count > self.missing
        return {'amount': _money(self.total) if not partial else None,
                'known_amount': _money(self.total) if known and currency_known else None,
                'count': self.count, 'missing_count': self.missing, 'partial': partial}

    def merge(self, value):
        self.total += Decimal(value['total'])
        self.count += value['count']
        self.missing += value['missing']


def _cohort():
    return {name: _Amount() for name in AMOUNTS}


def _cohort_result(cohort, currency_known=True):
    result = {name: value.result(currency_known) for name, value in cohort.items()}
    invoiced, received = result['invoiced']['amount'], result['received']['amount']
    result['collection_rate'] = _unknown_rate()
    if invoiced is not None and received is not None and Decimal(invoiced) > 0:
        result['collection_rate'] = {'value': float((Decimal(received) / Decimal(invoiced) * 100).quantize(QUANTUM)),
                                     'status': 'available', 'unit': 'percent'}
    return result


def _empty(currency, company, as_of, status, reason):
    return {
        'schema_version': '1.0', 'status': status, 'reason': reason,
        'currency': currency, 'company': company, 'as_of_date': as_of.isoformat(),
        'period_basis': 'calendar_year', 'basis': 'invoice_date',
        'currency_conversion_applied': False, 'source_updated_at': None,
        'source': {'kind': 'customer_invoice_register', 'label': 'Authorised customer invoice register',
                   'route': None if status == 'restricted' else '/finance/outgoing-invoices'},
        'kpis': {**{key: _unknown_metric() for key in ('monthly_invoiced', 'ytd_invoiced', 'ytd_received', 'ytd_outstanding')},
                 'collection_rate': _unknown_rate()},
        'monthly': [],
        'forecast': {'status': 'unavailable', 'rows': [], 'method': None, 'basis_months': [], 'description': DEFINITIONS['estimate']},
        'budget': {'status': 'unavailable', 'rows': [], 'description': DEFINITIONS['plan']},
        'operating_margin': {'status': 'unavailable', 'rows': [], 'description': DEFINITIONS['margin']},
        'coverage': {**dict.fromkeys(COVERAGE_COUNTS, None), 'first_invoice_date': None, 'last_invoice_date': None},
        'definitions': dict(DEFINITIONS),
    }


def _read_invoice_rows(currency, company):
    from apps.invoice_tracker.models import CustomerInvoice

    queryset = CustomerInvoice.objects.annotate(normalized_currency=Upper(Trim('currency')))
    queryset = queryset.filter(normalized_currency='' if currency == 'UNSPECIFIED' else currency)
    if company:
        queryset = queryset.annotate(recorded_company=Trim('company')).filter(recorded_company=company)
    return queryset.order_by().values('category', 'payment_status', 'invoice_date', 'invoice_amount',
                                      'actual_payment_received', 'updated_at').iterator(chunk_size=1000)


def aggregate_invoice_days(rows):
    """Build aggregate-only currency/day buckets without retaining invoice identities."""
    groups = {}
    for row in rows:
        invoice_date = row.get('invoice_date')
        key = ((row.get('currency') or '').strip().upper() or 'UNSPECIFIED', invoice_date.isoformat() if invoice_date else None)
        group = groups.setdefault(key, {'currency': key[0], 'invoice_date': key[1],
                                        'coverage': dict.fromkeys(COVERAGE_COUNTS, 0), 'metrics': _cohort()})
        coverage = group['coverage']
        coverage['source_row_count'] += 1
        if row.get('category') != 'external':
            coverage['excluded_internal_count'] += 1
            continue
        if row.get('payment_status') in ('cancelled', 'credit_note'):
            coverage[f"excluded_{row['payment_status']}_count"] += 1
            continue
        coverage['eligible_invoice_count'] += 1
        invoiced, received = row.get('invoice_amount'), row.get('actual_payment_received')
        for value, missing_key, negative_key in (
            (invoiced, 'missing_invoice_amount_count', 'negative_invoice_amount_count'),
            (received, 'missing_receipt_count', 'negative_receipt_count'),
        ):
            if value is None:
                coverage[missing_key] += 1
            elif value < ZERO:
                coverage[negative_key] += 1
        invoiced = invoiced if invoiced is not None and invoiced >= ZERO else None
        received = received if received is not None and received >= ZERO else None
        outstanding = max(invoiced - received, ZERO) if invoiced is not None and received is not None else None
        if invoiced is not None and received is not None and received > invoiced:
            coverage['overpaid_invoice_count'] += 1
        for name, value in {'invoiced': invoiced, 'received': received, 'outstanding': outstanding}.items():
            group['metrics'][name].add(value)
    return [{**row, 'metrics': {key: {'total': str(value.total), 'count': value.count, 'missing': value.missing}
                                for key, value in row['metrics'].items()}}
            for _, row in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or ''))]


def _load_workbook_snapshot():
    """Only use a dated aggregate generated from the exact audited Finance workbook."""
    from .workbook_summary import SNAPSHOT_PATH as workbook_path
    try:
        # Actual daily Finance aggregates belong in private deployment storage,
        # not source control. A configured path takes precedence without falling
        # back to an older local artifact if the configured file cannot be read.
        configured_path = os.environ.get('EXECUTIVE_INVOICE_PERFORMANCE_SNAPSHOT_PATH', '').strip()
        snapshot_path = Path(configured_path) if configured_path else SNAPSHOT_PATH
        with snapshot_path.open(encoding='utf-8') as source:
            snapshot = json.load(source)
        with workbook_path.open(encoding='utf-8') as source:
            workbook = json.load(source)
        if (snapshot.get('schema_version') != '1.0'
                or snapshot.get('source', {}).get('sha256') != workbook.get('source', {}).get('sha256')
                or not isinstance(snapshot.get('days'), list)
                or sum(row['coverage']['source_row_count'] for row in snapshot['days']) != workbook.get('invoice_count')):
            return None
        for row in snapshot['days']:
            if not isinstance(row['currency'], str) or not row['currency'] or set(row['metrics']) != set(AMOUNTS):
                return None
            if row['invoice_date']:
                date.fromisoformat(row['invoice_date'])
            for key in COVERAGE_COUNTS:
                if not isinstance(row['coverage'][key], int) or row['coverage'][key] < 0:
                    return None
            for metric in row['metrics'].values():
                if (not Decimal(metric['total']).is_finite() or Decimal(metric['total']) < ZERO
                        or not isinstance(metric['count'], int) or not isinstance(metric['missing'], int)
                        or not 0 <= metric['missing'] <= metric['count']
                        or metric['count'] != row['coverage']['eligible_invoice_count']):
                    return None
        return snapshot
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError):
        return None


def _finance_inputs(user, currency, company, months):
    if company:
        return {}, 'unavailable', 'Workspace Finance inputs do not establish this customer’s budget, forecast or operating margin.'
    if not module_action_allowed(user, 'finance_overview', 'read'):
        return {}, 'restricted', 'Finance overview read access is required for approved planning and accounting inputs.'
    try:
        from apps.finance.models import ExecutiveFinancePeriod
        with transaction.atomic():
            rows = list(ExecutiveFinancePeriod.objects.filter(
                currency=currency, month__in=months, status='approved',
                approved_by__isnull=False, approved_at__isnull=False, approved_at__lte=timezone.now(),
            ).exclude(source_reference='').values(
                'month', 'budget_invoiced', 'forecast_invoiced', 'recognised_revenue',
                'operating_costs', 'actual_through', 'source_reference', 'approved_at',
            ))
        return {row['month'].strftime('%Y-%m'): row for row in rows if row['source_reference'].strip()}, 'available', None
    except (ImportError, LookupError):
        return {}, 'unavailable', 'Approved monthly Finance inputs are not connected.'
    except Exception:
        logger.exception('Executive approved Finance inputs unavailable')
        return {}, 'error', 'Approved monthly Finance inputs could not be read.'


def _apply_finance_inputs(result, inputs, status, reason, future_months):
    budget_rows, margin_rows = [], []
    as_of = date.fromisoformat(result['as_of_date'])
    for month in result['monthly']:
        row = inputs.get(month['month'], {})
        budget, forecast = row.get('budget_invoiced'), row.get('forecast_invoiced')
        month['budget'] = _money(budget) if budget is not None and budget >= ZERO else None
        month['forecast'] = _money(forecast) if forecast is not None and forecast >= ZERO else None
        month['operating_margin'] = None
        month['ytd_operating_margin'] = None
        if month['budget'] is not None:
            budget_rows.append({'month': month['month'], 'value': month['budget']})
        revenue, costs = row.get('recognised_revenue'), row.get('operating_costs')
        actual_through = row.get('actual_through')
        if (revenue is not None and revenue > ZERO and costs is not None and costs >= ZERO
                and actual_through is not None and actual_through <= as_of
                and actual_through.strftime('%Y-%m') == month['month']):
            margin = float(((revenue - costs) / revenue * 100).quantize(QUANTUM))
            month['operating_margin'] = margin
            margin_rows.append({'month': month['month'], 'value': margin,
                                'recognised_revenue': _money(revenue), 'operating_costs': _money(costs),
                                'actual_through': actual_through.isoformat()})
        month_date = date.fromisoformat(month['month'] + '-01')
        calendar_keys = [date(month_date.year, value, 1).strftime('%Y-%m') for value in range(1, month_date.month + 1)]
        calendar_inputs = [inputs.get(key, {}) for key in calendar_keys]
        complete = True
        for key, record in zip(calendar_keys, calendar_inputs):
            period = date.fromisoformat(key + '-01')
            next_month = _month_offset(period, 1)
            period_end = date.fromordinal(next_month.toordinal() - 1)
            expected_through = min(period_end, as_of)
            if (record.get('recognised_revenue') is None or record['recognised_revenue'] < ZERO
                    or record.get('operating_costs') is None or record['operating_costs'] < ZERO
                    or record.get('actual_through') != expected_through):
                complete = False
                break
        if complete:
            revenue = sum((record['recognised_revenue'] for record in calendar_inputs), ZERO)
            costs = sum((record['operating_costs'] for record in calendar_inputs), ZERO)
            if revenue > ZERO:
                month['ytd_operating_margin'] = float(((revenue - costs) / revenue * 100).quantize(QUANTUM))
    result['budget'] = {'status': 'approved' if budget_rows else status if status != 'available' else 'unavailable',
                        'rows': budget_rows, 'description': reason or DEFINITIONS['plan']}
    result['operating_margin'] = {'status': 'approved' if margin_rows else status if status != 'available' else 'unavailable',
                                  'rows': margin_rows, 'description': reason or DEFINITIONS['margin'],
                                  'ytd': {'value': result['monthly'][-1]['ytd_operating_margin'],
                                          'status': 'approved' if result['monthly'][-1]['ytd_operating_margin'] is not None else 'unavailable',
                                          'unit': 'percent'}}
    approved_forecast = []
    for month in future_months:
        row = inputs.get(month, {})
        value = row.get('forecast_invoiced')
        approved_forecast.append({'month': month, 'value': _money(value) if value is not None and value >= ZERO else None})
    if any(row['value'] is not None for row in approved_forecast):
        result['forecast'] = {'status': 'approved', 'rows': approved_forecast, 'method': 'finance_approved_invoice_forecast',
                              'basis_months': [], 'partial': any(row['value'] is None for row in approved_forecast),
                              'description': DEFINITIONS['plan']}


def build_invoice_performance(user, *, currency='AED', company='', as_of=None):
    """Read all matching invoices; preserve source authorization and null values."""
    as_of = as_of or timezone.localdate()
    currency = (currency or '').strip().upper() or 'UNSPECIFIED'
    company = (company or '').strip()
    result = _empty(currency, company, as_of, 'restricted', 'Read access to customer invoices is required.')
    if not module_action_allowed(user, 'finance_outgoing', 'read'):
        return result
    try:
        with transaction.atomic():
            return _build(user, currency, company, as_of)
    except Exception:
        logger.exception('Executive invoice performance source unavailable')
        return _empty(currency, company, as_of, 'error', 'The authorised invoice performance source could not be read.')


def _build(user, currency, company, as_of):
    current = as_of.replace(day=1)
    months = [_month_offset(current, index) for index in range(-11, 1)]
    keys = [month.strftime('%Y-%m') for month in months]
    future = [_month_offset(current, index).strftime('%Y-%m') for index in range(1, 13)]
    cohorts = {key: _cohort() for key in keys}
    ytd = _cohort()
    coverage = dict.fromkeys(COVERAGE_COUNTS, 0)
    first_date, last_date, source_updated = None, None, None
    snapshot = _load_workbook_snapshot() if not company else None
    source_rows = (row for row in snapshot['days'] if row['currency'] == currency) if snapshot else _read_invoice_rows(currency, company)
    for row in source_rows:
        if snapshot:
            daily = row['coverage']
            for key in ('source_row_count', 'excluded_internal_count', 'excluded_cancelled_count', 'excluded_credit_note_count'):
                coverage[key] += daily[key]
            if not daily['eligible_invoice_count']:
                continue
            invoice_date = date.fromisoformat(row['invoice_date']) if row['invoice_date'] else None
            if invoice_date is None:
                coverage['missing_invoice_date_count'] += daily['eligible_invoice_count']
                continue
            if invoice_date > as_of:
                coverage['future_invoice_date_count'] += daily['eligible_invoice_count']
                continue
            for key in ('eligible_invoice_count', 'missing_invoice_amount_count', 'missing_receipt_count',
                        'overpaid_invoice_count', 'negative_invoice_amount_count', 'negative_receipt_count'):
                coverage[key] += daily[key]
            first_date = min(first_date, invoice_date) if first_date else invoice_date
            last_date = max(last_date, invoice_date) if last_date else invoice_date
            month = invoice_date.strftime('%Y-%m')
            if month in cohorts:
                for key in AMOUNTS:
                    cohorts[month][key].merge(row['metrics'][key])
            else:
                coverage['outside_window_count'] += daily['eligible_invoice_count']
            if invoice_date.year == as_of.year:
                for key in AMOUNTS:
                    ytd[key].merge(row['metrics'][key])
            continue
        coverage['source_row_count'] += 1
        if row['updated_at'] and (source_updated is None or row['updated_at'] > source_updated):
            source_updated = row['updated_at']
        if row['category'] != 'external':
            coverage['excluded_internal_count'] += 1
            continue
        if row['payment_status'] in ('cancelled', 'credit_note'):
            coverage[f"excluded_{row['payment_status']}_count"] += 1
            continue
        invoice_date = row['invoice_date']
        if invoice_date is None:
            coverage['missing_invoice_date_count'] += 1
            continue
        if invoice_date > as_of:
            coverage['future_invoice_date_count'] += 1
            continue
        coverage['eligible_invoice_count'] += 1
        first_date = min(first_date, invoice_date) if first_date else invoice_date
        last_date = max(last_date, invoice_date) if last_date else invoice_date
        invoiced, received = row['invoice_amount'], row['actual_payment_received']
        for value, missing_key, negative_key in (
            (invoiced, 'missing_invoice_amount_count', 'negative_invoice_amount_count'),
            (received, 'missing_receipt_count', 'negative_receipt_count'),
        ):
            if value is None:
                coverage[missing_key] += 1
            elif value < ZERO:
                coverage[negative_key] += 1
        invoiced = invoiced if invoiced is not None and invoiced >= ZERO else None
        received = received if received is not None and received >= ZERO else None
        outstanding = max(invoiced - received, ZERO) if invoiced is not None and received is not None else None
        if invoiced is not None and received is not None and received > invoiced:
            coverage['overpaid_invoice_count'] += 1
        values = {'invoiced': invoiced, 'received': received, 'outstanding': outstanding}
        month = invoice_date.strftime('%Y-%m')
        if month in cohorts:
            for key, value in values.items():
                cohorts[month][key].add(value)
        else:
            coverage['outside_window_count'] += 1
        if invoice_date.year == as_of.year:
            for key, value in values.items():
                ytd[key].add(value)
    coverage.update({'first_invoice_date': first_date.isoformat() if first_date else None,
                     'last_invoice_date': last_date.isoformat() if last_date else None})
    result = _empty(currency, company, as_of, 'unavailable', 'No dated external customer invoices are available in this currency and scope.')
    result['coverage'] = coverage
    result['source_updated_at'] = source_updated.isoformat() if source_updated else None
    if snapshot:
        result['source'] = {**snapshot['source'], 'kind': 'finance_workbook',
                            'label': 'Finance invoice workbook · verified original currency', 'route': '/finance/outgoing-invoices',
                            'timestamp_basis': 'snapshot_generation_time_not_invoice_update'}
        result['source_updated_at'] = None
        result['workbook_coverage'] = {**snapshot.get('coverage', {}), 'scope': 'all currencies before invoice-date filtering'}
        result['definitions']['scope'] = 'Finance external invoice workbook, including paid invoices, aggregated directly from recorded cells. Cancelled and credit-note rows are excluded. Unknown or conflicting currencies are withheld. Invoice dates after the cutoff and missing dates are excluded and disclosed.'
        result['definitions']['source_date'] = 'The workbook snapshot timestamp records aggregate generation, not when Finance last updated an invoice. Coverage first/last invoice dates describe the dated source population.'
    if not coverage['eligible_invoice_count']:
        return result
    known_currency = currency != 'UNSPECIFIED'
    result['monthly'] = [{'month': month, **_cohort_result(cohorts[month], known_currency),
                          'partial_period': month == current.strftime('%Y-%m'),
                          'budget': None, 'forecast': None, 'operating_margin': None} for month in keys]
    ytd_values = _cohort_result(ytd, known_currency)
    result['kpis'] = {'monthly_invoiced': result['monthly'][-1]['invoiced'],
                      'ytd_invoiced': ytd_values['invoiced'], 'ytd_received': ytd_values['received'],
                      'ytd_outstanding': ytd_values['outstanding'], 'collection_rate': ytd_values['collection_rate']}
    incomplete = not known_currency or any(coverage[key] for key in (
        'missing_invoice_date_count', 'missing_invoice_amount_count', 'missing_receipt_count',
        'negative_invoice_amount_count', 'negative_receipt_count'))
    result['status'] = 'partial' if incomplete else 'available'
    result['reason'] = 'Missing or invalid source values are excluded or remain unknown; see coverage.' if incomplete else None
    baseline = [_month_offset(current, index).strftime('%Y-%m') for index in (-3, -2, -1)]
    baseline_amounts = [cohorts[month]['invoiced'].result(known_currency)['amount'] for month in baseline]
    if (not company and known_currency and not coverage['missing_invoice_date_count']
            and first_date <= _month_offset(current, -3)
            and all(amount is not None for amount in baseline_amounts)):
        average = sum((Decimal(amount) for amount in baseline_amounts), ZERO) / 3
        if average > ZERO:
            result['forecast'] = {'status': 'estimated', 'rows': [{'month': month, 'value': _money(average)} for month in future],
                                  'method': 'three_completed_calendar_month_average', 'basis_months': baseline,
                                  'partial': False, 'description': DEFINITIONS['estimate']}
    input_months = [*months, *[_month_offset(current, index) for index in range(1, 13)]]
    inputs, planning_status, reason = _finance_inputs(user, currency, company, input_months)
    _apply_finance_inputs(result, inputs, planning_status, reason, future)
    return result
