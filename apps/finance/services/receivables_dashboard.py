"""Current invoice balances, grouped for the receivables overview.

Dates group today's recorded balances; this is never a historical ledger or an
FX conversion. Source authorization is checked before querying either register.
"""
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Max
from django.db.models.functions import Trim, Upper
from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed
from .command_center import ROUTES, _payable_queryset
from .workbook_summary import build_workbook_summary
from .invoice_performance import build_invoice_performance
from .receivables_source import get_active_receivables_source


logger = logging.getLogger(__name__)
UNPAID_STATUSES = frozenset({'new', 'overdue', 'pending', 'partial'})
KPI_NAMES = ('unpaid', 'overdue', 'over30', 'over60', 'over90')
BUCKETS = (
    ('current', 'Current'), ('days_1_30', '1–30'),
    ('days_31_60', '31–60'), ('days_61_90', '61–90'),
    ('over90', '91+'), ('unknown_due_date', 'Unknown due date'),
)


def _money(value):
    return str(value.quantize(Decimal('0.01')))


@dataclass
class _Metric:
    total: Decimal = Decimal('0')
    count: int = 0
    missing: int = 0

    def add(self, value):
        self.count += 1
        if value is None:
            self.missing += 1
        else:
            self.total += value

    def result(self, *, currency_known=True):
        partial = bool(self.missing or (self.count and not currency_known))
        known = self.count == 0 or self.count > self.missing
        return {
            'amount': _money(self.total) if not partial else None,
            'known_amount': _money(self.total) if known and currency_known else None,
            'count': self.count, 'missing_count': self.missing, 'partial': partial,
        }


def _unknown_metric():
    return {'amount': None, 'known_amount': None, 'count': None,
            'missing_count': None, 'partial': True}


def _month_window(as_of, months):
    end = as_of.year * 12 + as_of.month - 1
    return [date(value // 12, value % 12 + 1, 1).strftime('%Y-%m')
            for value in range(end - months + 1, end + 1)]


def _bucket(days):
    if days is None:
        return 'unknown_due_date'
    if days <= 0:
        return 'current'
    if days <= 30:
        return 'days_1_30'
    if days <= 60:
        return 'days_31_60'
    if days <= 90:
        return 'days_61_90'
    return 'over90'


def _source(status, reason=None, *, kind='receivables'):
    return {
        'status': status, 'reason': reason,
        'route': None if status == 'restricted' else ROUTES[kind],
        'invoice_count': None, 'open_count': None, 'missing_balance_count': None,
        'unknown_due_date_count': None, 'source_updated_at': None,
    }


def _selected(queryset, currency, company='', *, recorded_aed=False):
    source = queryset.annotate(normalized_currency=Upper(Trim('currency')))
    if not recorded_aed:
        source = source.filter(normalized_currency='' if currency == 'UNSPECIFIED' else currency)
    if company:
        source = source.annotate(recorded_company=Trim('company')).filter(recorded_company=company)
    return source


def _available_filters(queryset):
    return {
        'companies': sorted({value.strip() for value in queryset.order_by().values_list('company', flat=True).distinct() if value.strip()}, key=str.casefold),
        'currencies': sorted({(value or '').strip().upper() or 'UNSPECIFIED' for value in queryset.order_by().values_list('currency', flat=True).distinct()}),
    }


def _summary(queryset, *, kind, currency, as_of, month_keys, source_snapshot=False, recorded_aed=False):
    """One streamed scan retains only grouped amounts and five priority rows."""
    ar = kind == 'receivables'
    metrics = {name: _Metric() for name in KPI_NAMES}
    ageing = {key: _Metric() for key, _ in BUCKETS}
    overdue_months = {month: _Metric() for month in month_keys}
    cohorts = {month: {'paid': _Metric(), 'unpaid': _Metric()} for month in month_keys}
    customers = {}
    priority = []
    exclusions = {'overdue_outside_window': 0, 'overdue_due_date_unknown': 0, 'invoice_date_unknown': 0,
                  'invoice_date_outside_window': 0}
    fields = ['id', 'invoice_number', 'invoice_date', 'due_date', 'payment_status']
    fields += (['account', 'company', 'pm', 'invoice_amount', 'actual_payment_received', 'balance_to_be_received'] if ar
               else ['total_amount', 'paid_amount', 'procurement_status'])
    if source_snapshot:
        fields += ['invoice_amount_aed', 'currency', 'balance_currency', 'actual_payment_currency', 'row_number']
    metadata = queryset.aggregate(invoice_count=Count('pk'), updated=Max('updated_at'))
    for row in queryset.order_by().values(*fields).iterator(chunk_size=1000):
        if row['payment_status'] in ('cancelled', 'credit_note'):
            continue
        if not ar and row['procurement_status'] in ('rejected', 'closed'):
            continue
        if ar:
            # Finance's dashboard formula follows the recorded Payment Status.
            # Partial invoices contribute only their recorded remaining balance.
            balance = (row['balance_to_be_received'] if row['payment_status'] == 'partial'
                       else row['invoice_amount'])
            if source_snapshot:
                if row['payment_status'] == 'partial':
                    expected_currency = 'AED' if recorded_aed else currency
                    if balance != 0 and row['balance_currency'] != expected_currency:
                        balance = None
                elif recorded_aed:
                    balance = row['invoice_amount_aed']
            eligible_status = row['payment_status'] in UNPAID_STATUSES
        else:
            balance = (row['total_amount'] - row['paid_amount']
                       if row['total_amount'] is not None and row['paid_amount'] is not None else None)
            eligible_status = row['payment_status'] != 'paid'
        # Customer totals follow Finance's status filter exactly, including
        # recorded credits and zero amounts within those statuses.
        is_open = eligible_status and (ar or balance is None or balance > 0)
        invoice_date = row['invoice_date']
        invoice_month = invoice_date.strftime('%Y-%m') if invoice_date else None
        if ar:
            if invoice_month in cohorts and invoice_date <= as_of:
                received = (row['actual_payment_received'] if source_snapshot
                            else row['actual_payment_received'] or Decimal('0'))
                if source_snapshot and received is not None and received != 0 and row['actual_payment_currency'] != currency:
                    received = None
                cohorts[invoice_month]['paid'].add(received)
                if is_open:
                    cohorts[invoice_month]['unpaid'].add(balance)
            elif invoice_date is None:
                exclusions['invoice_date_unknown'] += 1
            else:
                exclusions['invoice_date_outside_window'] += 1
        if not is_open:
            continue
        days = (as_of - row['due_date']).days if row['due_date'] else None
        is_overdue = (row['payment_status'] == 'overdue' if ar
                      else days is not None and days > 0)
        bucket = _bucket(days)
        metrics['unpaid'].add(balance)
        ageing[bucket].add(balance)
        if is_overdue:
            metrics['overdue'].add(balance)
            due_month = row['due_date'].strftime('%Y-%m') if row['due_date'] else None
            if due_month in overdue_months:
                overdue_months[due_month].add(balance)
            elif due_month is None:
                exclusions['overdue_due_date_unknown'] += 1
            else:
                exclusions['overdue_outside_window'] += 1
        if days is not None and days > 30:
            metrics['over30'].add(balance)
        if days is not None and days > 60:
            metrics['over60'].add(balance)
        if days is not None and days > 90:
            metrics['over90'].add(balance)
        if ar:
            company = row['company'].strip()
            customer = customers.setdefault(company, {
                'unpaid': _Metric(), 'overdue': _Metric(),
                'buckets': {key: _Metric() for key, _ in BUCKETS},
            })
            customer['unpaid'].add(balance)
            customer['buckets'][bucket].add(balance)
            if is_overdue:
                customer['overdue'].add(balance)
            priority.append({
                'id': f"source:{row['id']}" if source_snapshot else row['id'],
                'source_snapshot': source_snapshot,
                'source_row': row['row_number'] if source_snapshot else None,
                'invoice_route': None if source_snapshot else f"/finance/outgoing-invoices/{row['id']}",
                'account': row['account'], 'company': company,
                'customer': company or 'Customer not recorded', 'invoice_number': row['invoice_number'],
                'payment_status': row['payment_status'],
                'due_date': row['due_date'].isoformat() if row['due_date'] else None,
                'days_overdue': days, 'balance': _money(balance) if balance is not None and currency != 'UNSPECIFIED' else None,
                'owner': row['pm'].strip() or None,
            })
            priority.sort(key=lambda item: (
                -(item['days_overdue'] if item['days_overdue'] is not None else -1000000),
                -Decimal(item['balance'] or '0'), item['invoice_number'],
            ))
            del priority[5:]
    known_currency = currency != 'UNSPECIFIED'
    def serialize(metric):
        return metric.result(currency_known=known_currency)

    customer_rows = []
    for company, customer in customers.items():
        total = customer['unpaid']
        customer_rows.append({
            'company': company, 'customer': company or 'Customer not recorded',
            # Keep the aggregate API's original label key for older consumers;
            # invoice-level account remains the independently recorded value.
            'account': company or 'Customer not recorded', **serialize(total),
            'overdue': serialize(customer['overdue']),
            'buckets': {key: serialize(value) for key, value in customer['buckets'].items()},
            'share_percentage': round(float(total.total / metrics['unpaid'].total * 100), 2)
            if known_currency and total.count > total.missing and metrics['unpaid'].total > 0 else None,
        })
    customer_rows.sort(key=lambda item: (item['known_amount'] is None, -Decimal(item['known_amount'] or '0'), item['customer'].casefold()))
    missing = metrics['unpaid'].missing
    return {
        'source': {
            **_source('incomplete' if missing or (metrics['unpaid'].count and not known_currency) else 'available', kind=kind),
            'invoice_count': metadata['invoice_count'], 'open_count': metrics['unpaid'].count,
            'missing_balance_count': missing, 'unknown_due_date_count': ageing['unknown_due_date'].count,
            'source_updated_at': metadata['updated'].isoformat() if metadata['updated'] else None,
        },
        'kpis': {key: serialize(value) for key, value in metrics.items()},
        'customers': customer_rows, 'priority_invoices': priority,
        'ageing': {key: serialize(value) for key, value in ageing.items()},
        'overdue_months': {key: serialize(value) for key, value in overdue_months.items()},
        'cohorts': {month: {key: serialize(value) for key, value in values.items()} for month, values in cohorts.items()},
        'chart_exclusions': exclusions,
    }


def build_receivables_dashboard(user, *, currency='AED', company='', months=12, as_of=None):
    from apps.invoice_tracker.models import CustomerInvoice

    now = timezone.now()
    as_of = as_of or timezone.localtime(now).date()
    month_keys = _month_window(as_of, months)
    sources = {}
    summaries = {}
    filters = {'companies': [], 'currencies': [], 'company': company, 'months': months}
    reporting_basis = 'original_currency'
    reporting_snapshot = None
    for kind, module in [('receivables', 'finance_outgoing'), ('payables', 'finance_incoming')]:
        if not module_action_allowed(user, module, 'read'):
            sources[kind] = _source('restricted', 'Read access to this invoice register is required.', kind=kind)
            continue
        if kind == 'payables' and company:
            sources[kind] = _source('unavailable', 'Supplier invoices do not record a comparable company. Clear the company filter to compare bills.', kind=kind)
            continue
        try:
            with transaction.atomic():
                source_rows = get_active_receivables_source() if kind == 'receivables' else None
                snapshot = getattr(source_rows, '_receivables_snapshot', None)
                source_snapshot = source_rows is not None
                recorded_aed = source_snapshot and currency == 'AED'
                queryset = source_rows if source_snapshot else (CustomerInvoice.objects.all() if kind == 'receivables' else _payable_queryset(user))
                available_filters = _available_filters(queryset) if kind == 'receivables' else None
                result = _summary(_selected(queryset, currency, company, recorded_aed=recorded_aed), kind=kind, currency=currency,
                                  as_of=as_of, month_keys=month_keys, source_snapshot=source_snapshot,
                                  recorded_aed=recorded_aed)
                if source_snapshot:
                    if recorded_aed:
                        reporting_basis = 'recorded_aed'
                    available_filters['currencies'] = sorted(set(available_filters['currencies']) | {'AED'})
                    result['source'].update(
                        mode='workbook', snapshot_id=snapshot.pk, file_name=snapshot.file_name,
                        sheet_name=snapshot.sheet_name, sha256=snapshot.sha256,
                    )
            if available_filters is not None:
                filters.update(available_filters)
            summaries[kind] = result
            sources[kind] = result['source']
            if kind == 'receivables':
                reporting_snapshot = snapshot
        except Exception:
            logger.exception('Receivables dashboard %s source failed', kind)
            sources[kind] = _source('error', 'The invoice register could not be read.', kind=kind)
    ar = summaries.get('receivables', {})
    timestamps = [source['source_updated_at'] for source in sources.values() if source['source_updated_at']]
    states = {source['status'] for source in sources.values()}
    return {
        'schema_version': '1.0', 'generated_at': now.isoformat(),
        'source_updated_at': max(timestamps) if timestamps else None,
        'as_of_date': as_of.isoformat(), 'currency': currency, 'currency_conversion_applied': False,
        'amount_basis': reporting_basis,
        'status': next(iter(states)) if len(states) == 1 else 'partial',
        'filters': filters, 'sources': sources,
        'workbook_summary': build_workbook_summary(user, source_snapshot=reporting_snapshot),
        'invoice_performance': build_invoice_performance(user, currency=currency, company=company, as_of=as_of),
        'kpis': ar.get('kpis', {key: _unknown_metric() for key in KPI_NAMES}),
        'customers': ar.get('customers', []), 'priority_invoices': ar.get('priority_invoices', []),
        'priority_invoice_count': sources['receivables']['open_count'],
        'ageing': [{'id': key, 'label': label, **{
            kind: summaries.get(kind, {}).get('ageing', {}).get(key, _unknown_metric())
            for kind in sources}} for key, label in BUCKETS],
        'overdue_by_month': [{'month': month, **{
            kind: summaries.get(kind, {}).get('overdue_months', {}).get(month, _unknown_metric())
            for kind in sources}} for month in month_keys],
        'paid_unpaid_by_month': [{'month': month, **ar.get('cohorts', {}).get(month, {
            'paid': _unknown_metric(), 'unpaid': _unknown_metric(),
        })} for month in month_keys],
        'chart_exclusions': {kind: value['chart_exclusions'] for kind, value in summaries.items()},
        'definitions': {
            'balance_basis': 'New, Overdue and Pending invoices use Invoice Amount (L). Partial invoices use the recorded Balance to be received (Y). Missing amounts remain unknown. Current recorded amounts and payment statuses are used; the reference date changes Due Date ageing only. This is not a historical balance sheet.',
            'currency': 'Amounts remain in their original invoice currency. No currency conversion is applied.',
            'company': 'Customer identity and grouping use the recorded COMPANY column, trimmed of surrounding spaces. Account is retained separately and is never a fallback customer name. This is not a verified legal-entity consolidation.',
            'period': 'The period limits chart months only. KPI cards and customer ageing include all current open balances.',
            'unpaid': 'Payment Status must be New, Overdue, Pending or Partial. New, Overdue and Pending use Invoice Amount (L); Partial uses Balance to be received (Y). Recorded amounts retain their sign, including zero and negative amounts. Missing amounts remain unknown; other statuses are excluded.',
            'overdue': 'Invoice Amount (L) for invoices whose recorded Payment Status is Overdue, including those with a missing or future Due Date. Recorded amounts retain their sign, including zero and negative amounts. Missing amounts remain unknown; the reference date does not change payment status.',
            'over30': 'Unpaid invoice amounts whose Due Date is more than 30 days before the reference date, regardless of which eligible unpaid payment status is recorded. Partial invoices use Balance to be received (Y).',
            'over60': 'Unpaid invoice amounts whose Due Date is more than 60 days before the reference date, regardless of which eligible unpaid payment status is recorded. Partial invoices use Balance to be received (Y).',
            'over90': 'Unpaid invoice amounts whose Due Date is more than 90 days before the reference date, regardless of which eligible unpaid payment status is recorded. Partial invoices use Balance to be received (Y).',
            'ageing': 'Ageing uses the Due Date column, not Invoice Date or stored days overdue. Due today and future due dates are current. The 30+, 60+ and 90+ cards use strictly more than 30, 60 and 90 days. Unknown due dates remain separate. Ageing is independent of the status-based Overdue amount.',
            'known_amount': 'Subtotal of recorded balances; missing amounts remain unknown. An empty eligible group is zero.',
            'shares': 'Customer shares use the recorded unpaid subtotal; they are partial when balances are missing.',
            'overdue_by_month': 'Customer invoices with Payment Status Overdue, grouped by Due Date month within the selected window. Invoices with missing Due Date or a month outside the window are excluded from this chart only. Supplier overdue balances use past Due Dates. These are current amounts, not historical monthly balances.',
            'paid_unpaid_by_month': 'Actual Payment Received (AA), with blanks counted as zero, and current unpaid amounts grouped by invoice issue month; not monthly cash flow. Unpaid includes Invoice Amount (L) for New, Overdue and Pending, and Balance to be received (Y) for Partial. Cancelled and credit-note records are excluded.',
            'priority': 'Up to five open invoices ordered by days past due, recorded balance and invoice number. Owner is the recorded project manager, not an assigned collection owner.',
            'payables': 'Active supplier invoice balances preserve the existing finance visibility scope; rejected and closed records are excluded.',
            **({
                'balance_basis': 'Recorded Inv Amt. (AED), column M, for New, Overdue and Pending source rows across all original invoice currencies. Partial uses recorded Balance to be received (Y) only when its currency is AED. Missing amounts remain unknown. Workbook values and payment statuses are preserved; no exchange rates or payment statuses are recalculated.',
                'currency': 'AED shows stored Inv Amt. (AED) across all original invoice currencies. Other currency selections show original-currency source amounts. No new currency conversion is applied.',
                'unpaid': 'Sum Inv Amt. (AED), column M, where recorded Payment Status is New, Overdue or Pending, plus recorded AED Balance to be received (Y) where status is Partial. Signed amounts and zero are retained; missing or non-AED partial balances remain unknown.',
                'overdue': 'Sum Inv Amt. (AED), column M, only where recorded Payment Status is Overdue, across all original currencies. Due Date does not change this status filter. Source statuses and amounts are preserved.',
                'paid_unpaid_by_month': 'Recorded AED receipts and current unpaid amounts grouped by invoice issue month. Blank, nonnumeric and non-AED receipts without a stored AED equivalent remain unknown. This is not monthly cash flow.',
                'payables': 'Supplier comparison includes original AED supplier invoices only; no supplier currency conversion is applied.',
            } if reporting_basis == 'recorded_aed' else {}),
        },
    }
