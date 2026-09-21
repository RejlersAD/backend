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


logger = logging.getLogger(__name__)
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


def _selected(queryset, currency, company=''):
    source = queryset.annotate(normalized_currency=Upper(Trim('currency')))
    source = source.filter(normalized_currency='' if currency == 'UNSPECIFIED' else currency)
    if company:
        source = source.annotate(recorded_company=Trim('company')).filter(recorded_company=company)
    return source


def _available_filters(queryset):
    return {
        'companies': sorted({value.strip() for value in queryset.order_by().values_list('company', flat=True).distinct() if value.strip()}, key=str.casefold),
        'currencies': sorted({(value or '').strip().upper() or 'UNSPECIFIED' for value in queryset.order_by().values_list('currency', flat=True).distinct()}),
    }


def _summary(queryset, *, kind, currency, as_of, month_keys):
    """One streamed scan retains only grouped amounts and five priority rows."""
    ar = kind == 'receivables'
    metric_names = ['unpaid', 'overdue', 'over30', 'over90']
    metrics = {name: _Metric() for name in metric_names}
    ageing = {key: _Metric() for key, _ in BUCKETS}
    overdue_months = {month: _Metric() for month in month_keys}
    cohorts = {month: {'paid': _Metric(), 'unpaid': _Metric()} for month in month_keys}
    customers = {}
    priority = []
    exclusions = {'overdue_outside_window': 0, 'invoice_date_unknown': 0,
                  'invoice_date_outside_window': 0}
    fields = ['id', 'invoice_number', 'invoice_date', 'due_date', 'payment_status']
    fields += (['account', 'pm', 'balance_to_be_received', 'actual_payment_received'] if ar
               else ['total_amount', 'paid_amount', 'procurement_status'])
    metadata = queryset.aggregate(invoice_count=Count('pk'), updated=Max('updated_at'))
    for row in queryset.order_by().values(*fields).iterator(chunk_size=1000):
        if row['payment_status'] in ('cancelled', 'credit_note'):
            continue
        if not ar and row['procurement_status'] in ('rejected', 'closed'):
            continue
        if ar:
            balance = row['balance_to_be_received']
        else:
            balance = (row['total_amount'] - row['paid_amount']
                       if row['total_amount'] is not None and row['paid_amount'] is not None else None)
        is_open = row['payment_status'] != 'paid' and (balance is None or balance > 0)
        invoice_date = row['invoice_date']
        invoice_month = invoice_date.strftime('%Y-%m') if invoice_date else None
        if ar:
            if invoice_month in cohorts and invoice_date <= as_of:
                cohorts[invoice_month]['paid'].add(row['actual_payment_received'])
                if is_open:
                    cohorts[invoice_month]['unpaid'].add(balance)
            elif invoice_date is None:
                exclusions['invoice_date_unknown'] += 1
            else:
                exclusions['invoice_date_outside_window'] += 1
        if not is_open:
            continue
        days = (as_of - row['due_date']).days if row['due_date'] else None
        bucket = _bucket(days)
        metrics['unpaid'].add(balance)
        ageing[bucket].add(balance)
        if days is not None and days > 0:
            metrics['overdue'].add(balance)
            due_month = row['due_date'].strftime('%Y-%m')
            if due_month in overdue_months:
                overdue_months[due_month].add(balance)
            else:
                exclusions['overdue_outside_window'] += 1
        if days is not None and days > 30:
            metrics['over30'].add(balance)
        if days is not None and days > 90:
            metrics['over90'].add(balance)
        if ar:
            account = row['account'].strip() or 'Customer not recorded'
            customer = customers.setdefault(account, {
                'unpaid': _Metric(), 'overdue': _Metric(),
                'buckets': {key: _Metric() for key, _ in BUCKETS},
            })
            customer['unpaid'].add(balance)
            customer['buckets'][bucket].add(balance)
            if days is not None and days > 0:
                customer['overdue'].add(balance)
            priority.append({
                'id': row['id'], 'account': account, 'invoice_number': row['invoice_number'],
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
    for account, customer in customers.items():
        total = customer['unpaid']
        customer_rows.append({
            'account': account, **serialize(total),
            'overdue': serialize(customer['overdue']),
            'buckets': {key: serialize(value) for key, value in customer['buckets'].items()},
            'share_percentage': round(float(total.total / metrics['unpaid'].total * 100), 2)
            if known_currency and total.count > total.missing and metrics['unpaid'].total > 0 else None,
        })
    customer_rows.sort(key=lambda item: (item['known_amount'] is None, -Decimal(item['known_amount'] or '0'), item['account'].casefold()))
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
    for kind, module in [('receivables', 'finance_outgoing'), ('payables', 'finance_incoming')]:
        if not module_action_allowed(user, module, 'read'):
            sources[kind] = _source('restricted', 'Read access to this invoice register is required.', kind=kind)
            continue
        if kind == 'payables' and company:
            sources[kind] = _source('unavailable', 'Supplier invoices do not record a comparable company. Clear the company filter to compare bills.', kind=kind)
            continue
        try:
            with transaction.atomic():
                queryset = CustomerInvoice.objects.all() if kind == 'receivables' else _payable_queryset(user)
                available_filters = _available_filters(queryset) if kind == 'receivables' else None
                result = _summary(_selected(queryset, currency, company), kind=kind, currency=currency,
                                  as_of=as_of, month_keys=month_keys)
            if available_filters is not None:
                filters.update(available_filters)
            summaries[kind] = result
            sources[kind] = result['source']
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
        'status': next(iter(states)) if len(states) == 1 else 'partial',
        'filters': filters, 'sources': sources,
        'kpis': ar.get('kpis', {key: _unknown_metric() for key in ['unpaid', 'overdue', 'over30', 'over90']}),
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
            'balance_basis': 'Current recorded invoice balances aged against the selected reference date; this is not a historical balance sheet.',
            'currency': 'Amounts remain in their original invoice currency. No currency conversion is applied.',
            'company': 'Company recorded on the customer invoice; this is not a verified legal-entity consolidation.',
            'period': 'The period limits chart months only. KPI cards and customer ageing include all current open balances.',
            'unpaid': 'Positive or unknown balances on unsettled customer invoices; paid, cancelled and credit-note records are excluded.',
            'ageing': 'Due today and future due dates are current. Overdue thresholds are strictly more than 0, 30 and 90 days. Unknown due dates remain separate.',
            'known_amount': 'Subtotal of recorded balances; missing amounts remain unknown. An empty eligible group is zero.',
            'shares': 'Customer shares use the recorded unpaid subtotal; they are partial when balances are missing.',
            'overdue_by_month': 'Current overdue balances grouped by contractual due month within the selected window; not historical monthly balances.',
            'paid_unpaid_by_month': 'Current recorded receipts and open balances grouped by invoice issue month; not monthly cash flow. Cancelled and credit-note records are excluded.',
            'priority': 'Up to five open invoices ordered by days past due, recorded balance and invoice number. Owner is the recorded project manager, not an assigned collection owner.',
            'payables': 'Active supplier invoice balances preserve the existing finance visibility scope; rejected and closed records are excluded.',
        },
    }
