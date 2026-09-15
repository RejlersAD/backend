"""Read-only invoice operations aggregates; no FX or financial-ledger substitutes."""
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Case, Count, DecimalField, F, Max, Min, Q, Sum, Value, When
from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed


logger = logging.getLogger(__name__)
MONEY = DecimalField(max_digits=24, decimal_places=2)
BUCKETS = (
    ('current', 'Not past due'), ('days_1_30', '1–30 days'),
    ('days_31_60', '31–60 days'), ('days_61_90', '61–90 days'),
    ('over90', 'Over 90 days'), ('unknown_due_date', 'Due date unknown'),
)
ROUTES = {'receivables': '/finance/outgoing-invoices', 'payables': '/finance/incoming-invoices'}
SOURCE_NAMES = {'receivables': 'Customer invoice register', 'payables': 'Supplier invoice register'}
PROCESS_LABELS = {
    'review': 'Invoices needing review', 'unmatched': 'Unmatched invoices',
    'exception': 'Matching exceptions', 'ready_for_payment': 'Ready for payment',
    'verified': 'Verified matches', 'on_hold': 'Invoices on hold',
}
UNAVAILABLE = {
    'cash_position': 'Approved bank or treasury balances are not connected.',
    'net_working_capital': 'Inventory and other current assets and liabilities are not connected.',
    'dso': 'Approved sales and average receivables for a defined period are not connected.',
    'forecast': 'An approved cash forecast is not connected.',
}


def _money(value):
    return str(value.quantize(Decimal('0.01')))


def _currency(value):
    return (value or '').strip().upper() or 'UNSPECIFIED'


def _empty_source(kind, status, reason):
    return {
        'status': status, 'source': SOURCE_NAMES[kind], 'reason': reason,
        'route': None if status == 'restricted' else ROUTES[kind],
        'source_updated_at': None, 'source_timestamp_kind': 'record_updated_at',
        'invoice_count': None, 'open_count': None, 'overdue_count': None,
        'over60_count': None, 'unknown_due_date_count': None,
        'missing_balance_count': None, 'missing_currency_count': None, 'oldest_due_date': None, 'by_currency': [],
        'balance_coverage': {'known_count': None, 'total_count': None, 'percentage': None},
    }


def _payable_queryset(user):
    # Same row filter as InvoiceViewSet, without its data-access audit writer.
    from apps.finance.models import Invoice
    from apps.rbac.data_visibility_mixin import build_visibility_filter
    return Invoice.objects.filter(build_visibility_filter(
        user=user, module_code='finance', owner_field='submitted_by',
    ))


def _conditions(as_of):
    return {
        'current': Q(due_date__gte=as_of),
        'days_1_30': Q(due_date__lt=as_of, due_date__gte=as_of - timedelta(days=30)),
        'days_31_60': Q(due_date__lt=as_of - timedelta(days=30), due_date__gte=as_of - timedelta(days=60)),
        'days_61_90': Q(due_date__lt=as_of - timedelta(days=60), due_date__gte=as_of - timedelta(days=90)),
        'over90': Q(due_date__lt=as_of - timedelta(days=90)),
        'unknown_due_date': Q(due_date__isnull=True),
        'overdue': Q(due_date__lt=as_of),
        'due_30d': Q(due_date__gte=as_of, due_date__lte=as_of + timedelta(days=30)),
    }


def _source_summary(kind, source, as_of):
    active = source.exclude(payment_status__in=['paid', 'cancelled'])
    if kind == 'receivables':
        active = active.exclude(payment_status='credit_note')
        balance = F('balance_to_be_received')
    else:
        active = active.exclude(procurement_status__in=['rejected', 'closed'])
        balance = Case(
            When(Q(total_amount__isnull=True) | Q(paid_amount__isnull=True), then=Value(None)),
            default=F('total_amount') - F('paid_amount'), output_field=MONEY,
        )
    outstanding = active.annotate(recorded_balance=balance).filter(
        Q(recorded_balance__gt=0) | Q(recorded_balance__isnull=True),
    )
    conditions = _conditions(as_of)
    summaries = outstanding.order_by().values('currency').annotate(
        invoice_count=Count('pk'),
        missing_balance_count=Count('pk', filter=Q(recorded_balance__isnull=True)),
        total=Sum('recorded_balance'),
        oldest_due_date=Min('due_date', filter=Q(due_date__lt=as_of)),
        **{key: Sum('recorded_balance', filter=condition) for key, condition in conditions.items()},
        **{key + '_count': Count('pk', filter=condition) for key, condition in conditions.items()},
    )
    grouped = {}
    for row in summaries:
        item = grouped.setdefault(_currency(row['currency']), {
            'counts': defaultdict(int), 'amounts': defaultdict(Decimal), 'oldest_due_date': None,
        })
        for key in ['invoice_count', 'missing_balance_count', *[name + '_count' for name in conditions]]:
            item['counts'][key] += row[key]
        for key in ['total', *conditions]:
            if row[key] is not None:
                item['amounts'][key] += row[key]
        candidate = row['oldest_due_date']
        if candidate and (item['oldest_due_date'] is None or candidate < item['oldest_due_date']):
            item['oldest_due_date'] = candidate
    rows = []
    for currency, item in sorted(grouped.items()):
        complete = not item['counts']['missing_balance_count'] and currency != 'UNSPECIFIED'
        row = {
            'currency': currency, 'status': 'available' if complete else 'incomplete',
            **dict(item['counts']),
            'missing_currency_count': item['counts']['invoice_count'] if currency == 'UNSPECIFIED' else 0,
            'oldest_due_date': item['oldest_due_date'].isoformat() if item['oldest_due_date'] else None,
            'outstanding': _money(item['amounts']['total']) if complete else None,
            'overdue': _money(item['amounts']['overdue']) if complete else None,
            'due_30d': _money(item['amounts']['due_30d']) if complete else None,
            'buckets': [{'id': key, 'label': label, 'count': item['counts'][key + '_count'],
                         'amount': _money(item['amounts'][key]) if complete else None}
                        for key, label in BUCKETS],
        }
        rows.append(row)
    metadata = source.aggregate(invoice_count=Count('pk'), updated=Max('updated_at'))
    missing = sum(row['missing_balance_count'] for row in rows)
    missing_currency = sum(row['missing_currency_count'] for row in rows)
    open_count = sum(row['invoice_count'] for row in rows)
    dates = [row['oldest_due_date'] for row in rows if row['oldest_due_date']]
    return {
        'status': 'incomplete' if missing or missing_currency else 'available', 'source': SOURCE_NAMES[kind],
        'route': ROUTES[kind], 'reason': 'Missing balances or currencies withhold the affected monetary totals.' if missing or missing_currency else None,
        'source_updated_at': metadata['updated'].isoformat() if metadata['updated'] else None,
        'source_timestamp_kind': 'record_updated_at',
        'invoice_count': metadata['invoice_count'],
        'open_count': open_count,
        'balance_coverage': {'known_count': open_count - missing, 'total_count': open_count,
                             'percentage': round((open_count - missing) * 100 / open_count, 1) if open_count else None,
                             'definition': 'Recorded balance completeness among unsettled positive-or-unknown invoice balances; this is not overall data quality.'},
        'overdue_count': sum(row['overdue_count'] for row in rows),
        'over60_count': sum(row['days_61_90_count'] + row['over90_count'] for row in rows),
        'missing_balance_count': missing,
        'missing_currency_count': missing_currency,
        'unknown_due_date_count': sum(row['unknown_due_date_count'] for row in rows),
        'oldest_due_date': min(dates) if dates else None, 'by_currency': rows,
        'definition': ('Unsettled positive recorded balances and invoices with unknown balances; paid and cancelled records excluded. Counts include unknown balances. Negative and zero balances are excluded.'
                       + (' Supplier invoices marked rejected or closed are also excluded; these are active workflow balances, not an approved liability ledger.' if kind == 'payables' else ' Credit-note records are also excluded.')),
        'due_30d_definition': 'Contractual due dates from today through 30 days ahead, inclusive; this is not a payment forecast.',
    }


def _process(source):
    active = source.exclude(payment_status__in=['paid', 'cancelled']).exclude(procurement_status__in=['rejected', 'closed'])
    conditions = {
        'review': Q(procurement_status__in=['ocr_review', 'ready_for_matching', 'procurement_review', 'finance_review']),
        'unmatched': Q(match_status='unmatched'), 'exception': Q(match_status='exception'),
        'ready_for_payment': Q(procurement_status='approved_for_payment') & ~Q(payment_status='on_hold') & ~Q(match_status='exception'),
        'verified': Q(match_status='verified'), 'on_hold': Q(payment_status='on_hold'),
    }
    counts = active.aggregate(eligible_count=Count('pk'), **{
        key: Count('pk', filter=value) for key, value in conditions.items()
    })
    return {
        'status': 'available', 'counts': counts, 'route': ROUTES['payables'],
        'denominator': counts['eligible_count'],
        'definition': 'Active supplier invoices excluding paid, cancelled, rejected and closed records. Queue counts overlap; verified matching does not imply payment approval.',
        'metrics': [{'id': key, 'label': label, 'count': counts[key],
                     'percentage': round(counts[key] * 100 / counts['eligible_count'], 1) if counts['eligible_count'] else None,
                     'denominator': counts['eligible_count']} for key, label in PROCESS_LABELS.items()],
    }


def _net_rows(sources):
    currencies = sorted({row['currency'] for source in sources.values() for row in source['by_currency']})
    result = []
    for currency in currencies:
        amounts = []
        for kind in ['receivables', 'payables']:
            source = sources[kind]
            row = next((item for item in source['by_currency'] if item['currency'] == currency), None)
            # An absent currency is a known zero only after a successful complete
            # source read; never substitute for a denied or failed source.
            if source['status'] not in ['available', 'incomplete'] or source['missing_currency_count'] or (row and row['status'] != 'available'):
                amounts.append(None)
            else:
                amounts.append(Decimal(row['outstanding']) if row else Decimal('0'))
        available = all(amount is not None for amount in amounts)
        result.append({'currency': currency, 'status': 'available' if available else 'unavailable',
                       'net_invoice_exposure': _money(amounts[0] - amounts[1]) if available else None})
    return result


def build_command_center(user):
    from apps.invoice_tracker.models import CustomerInvoice

    now = timezone.now()
    as_of = timezone.localtime(now).date()
    sources = {}
    process = {'status': 'restricted', 'counts': None, 'metrics': [], 'denominator': None, 'route': None}
    for kind, module in [('receivables', 'finance_outgoing'), ('payables', 'finance_incoming')]:
        if not module_action_allowed(user, module, 'read'):
            sources[kind] = _empty_source(kind, 'restricted', 'Read access to this invoice register is required.')
            continue
        try:
            with transaction.atomic():
                queryset = CustomerInvoice.objects.all() if kind == 'receivables' else _payable_queryset(user)
                sources[kind] = _source_summary(kind, queryset, as_of)
                if kind == 'payables':
                    process = _process(queryset)
        except Exception:
            logger.exception('Finance command center %s source failed', kind)
            sources[kind] = _empty_source(kind, 'error', 'The invoice register could not be read.')
            if kind == 'payables':
                process = {'status': 'error', 'counts': None, 'metrics': [], 'denominator': None, 'route': ROUTES[kind]}
    actions = []
    if sources['receivables']['overdue_count']:
        actions.append({'id': 'receivables_overdue', 'label': 'Review overdue receivables',
                        'count': sources['receivables']['overdue_count'], 'route': ROUTES['receivables'],
                        'reason': 'Unsettled customer invoices have contractual due dates in the past.'})
    if process['status'] == 'available':
        for key in ['review', 'exception', 'unmatched', 'ready_for_payment', 'on_hold']:
            if process['counts'][key]:
                actions.append({'id': 'payables_' + key, 'label': PROCESS_LABELS[key],
                                'count': process['counts'][key], 'route': ROUTES['payables'],
                                'reason': 'Recorded supplier invoice workflow queue; review the authoritative register.'})
    for action in actions:
        action.update({'owner': None, 'due_date': None, 'scope': 'all_currencies', 'can_decide': False})
    states = {source['status'] for source in sources.values()}
    status = 'available' if states == {'available'} else next(iter(states)) if len(states) == 1 else 'partial'
    return {
        'schema_version': '1.0', 'generated_at': now.isoformat(), 'as_of_date': as_of.isoformat(),
        'status': status, 'currency_conversion_applied': False,
        'scope': {'label': 'Authorized invoice registers', 'consolidated': False,
                  'description': 'Current operational snapshot. Each register requires its own read grant; supplier invoices retain their existing team visibility.'},
        'currencies': sorted({row['currency'] for source in sources.values() for row in source['by_currency']}),
        'sources': sources, 'by_currency': _net_rows(sources),
        'net_invoice_exposure_definition': 'Recorded receivables less payables in the same original currency; not cash or net working capital.',
        'process': process, 'actions': actions, 'action_count': len(actions), 'actions_truncated': False,
        'actions_definition': 'Aggregate workflow queues across all currencies; counts can overlap. These are not personal approval assignments.',
        'trends': {'status': 'unavailable', 'series': [], 'reason': 'Historical balance snapshots and an approved cash-flow series are not connected.'},
        'unavailable_metrics': [{'id': key, 'status': 'unavailable', 'value': None, 'reason': reason} for key, reason in UNAVAILABLE.items()],
    }
