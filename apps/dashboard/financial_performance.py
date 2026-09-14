"""Financial-tab data from authorized A/R; financial-ledger gaps remain explicit."""
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from .executive import metric


logger = logging.getLogger(__name__)
BUCKETS = (
    ('current', 'Current'), ('days_1_30', '1–30 days'),
    ('days_31_60', '31–60 days'), ('over60', 'Over 60 days'),
    ('unknown_due_date', 'Due date unknown'),
)
UNAVAILABLE_HEADLINES = (
    ('revenue_ytd', 'Revenue YTD', 'currency', 'Recognized year-to-date revenue requires an approved accounting ledger.'),
    ('operating_profit', 'Operating profit', 'currency', 'Operating profit requires approved income-statement reporting; invoice balances and EBITA are not substitutes.'),
    ('operating_margin', 'Operating margin', 'percent', 'Operating profit and recognized revenue for the same period are not connected.'),
    ('cash_position', 'Cash position', 'currency', 'Approved treasury or bank-balance reporting is not connected; receivables are not cash.'),
    ('dso', 'DSO', 'days', 'DSO requires approved sales and average receivables over a defined reporting period.'),
)


def _aging(allowed, as_of):
    if not allowed:
        return {'status': 'restricted', 'by_currency': [], 'missing_balance_count': None,
                'unknown_due_date_count': None, 'invoice_count': None,
                'source_updated_at': None, 'as_of_date': as_of.isoformat(),
                'description': 'Outgoing-invoice read access is required.'}
    from apps.invoice_tracker.models import CustomerInvoice

    # CustomerInvoice's authorized register has shared module-wide visibility.
    # Preserve that scope and the original currency; never use the AED cache.
    source = CustomerInvoice.objects.all()
    invoices = source.exclude(payment_status__in=['paid', 'cancelled']).filter(
        Q(balance_to_be_received__gt=0) | Q(balance_to_be_received__isnull=True),
    )
    conditions = {
        'current': Q(due_date__gte=as_of),
        'days_1_30': Q(due_date__lt=as_of, due_date__gte=as_of - timedelta(days=30)),
        'days_31_60': Q(due_date__lt=as_of - timedelta(days=30), due_date__gte=as_of - timedelta(days=60)),
        'over60': Q(due_date__lt=as_of - timedelta(days=60)),
        'unknown_due_date': Q(due_date__isnull=True),
    }
    totals = invoices.values('currency').annotate(
        invoice_count=Count('pk'),
        missing_balance_count=Count('pk', filter=Q(balance_to_be_received__isnull=True)),
        unknown_due_date_count=Count('pk', filter=Q(due_date__isnull=True)),
        **{key: Sum('balance_to_be_received', filter=condition) for key, condition in conditions.items()},
    ).order_by('currency')
    grouped = {}
    for row in totals:
        currency = (row['currency'] or '').strip().upper() or 'UNSPECIFIED'
        item = grouped.setdefault(currency, {
            'invoice_count': 0, 'missing_balance_count': 0, 'unknown_due_date_count': 0,
            'buckets': defaultdict(Decimal),
        })
        for key in ['invoice_count', 'missing_balance_count', 'unknown_due_date_count']:
            item[key] += row[key]
        for key, _ in BUCKETS:
            # No matching known balances means a real zero bucket. Any missing
            # balance withholds ALL totals for its currency after grouping.
            if row[key] is not None:
                item['buckets'][key] += row[key]
    rows = []
    for currency, data in sorted(grouped.items()):
        complete = data['missing_balance_count'] == 0
        rows.append({
            'currency': currency, 'status': 'available' if complete else 'incomplete',
            'total': str(sum(data['buckets'].values(), Decimal('0')).quantize(Decimal('0.01'))) if complete else None,
            'buckets': [{'id': key, 'label': label,
                         'amount': str(data['buckets'][key].quantize(Decimal('0.01'))) if complete else None}
                        for key, label in BUCKETS],
            'missing_balance_count': data['missing_balance_count'],
            'unknown_due_date_count': data['unknown_due_date_count'],
            'invoice_count': data['invoice_count'],
        })
    missing = sum(row['missing_balance_count'] for row in rows)
    updated = source.aggregate(latest=Max('updated_at'))['latest']
    return {
        'status': 'incomplete' if missing else 'available', 'by_currency': rows,
        'missing_balance_count': missing,
        'unknown_due_date_count': sum(row['unknown_due_date_count'] for row in rows),
        'invoice_count': sum(row['invoice_count'] for row in rows),
        'source_updated_at': updated.isoformat() if updated else None,
        'as_of_date': as_of.isoformat(),
        'description': 'Unsettled positive recorded invoice balances by original currency and contractual due date; paid and cancelled invoices excluded. Missing balances withhold their currency totals.',
    }


def _receivable_metric(identifier, label, aging, bucket=None):
    complete = [row for row in aging['by_currency'] if row['status'] == 'available']
    amounts = [{'currency': row['currency'], 'amount':
                next(item['amount'] for item in row['buckets'] if item['id'] == bucket) if bucket else row['total']}
               for row in complete]
    row = metric(identifier, label, unit='currency', status='partial' if aging['status'] == 'incomplete' else aging['status'], by_currency=amounts,
                 source='Customer invoice register', route=None if aging['status'] == 'restricted' else '/finance/outgoing-invoices',
                 description=('Unpaid positive recorded balances more than 60 days past contractual due date.' if bucket
                              else 'Unpaid positive recorded customer invoice balances, including invoices with an unknown due date.'))
    row['incomplete_currencies'] = [item['currency'] for item in aging['by_currency'] if item['status'] == 'incomplete']
    if aging['status'] != 'available':
        row['reason'] = ('One or more currency totals are withheld because invoice balances are missing.'
                         if aging['status'] == 'incomplete' else aging['description'])
    return row


def build_financial_performance(user, context, finance_section):
    """Read-only extension; no ledger, cash position or historical series inferred."""
    del user  # Source access is supplied by the endpoint's effective read decisions.
    as_of = timezone.localtime(context['generated_at']).date()
    try:
        with transaction.atomic():
            aging = _aging('finance_outgoing' in context['allowed_modules'], as_of)
    except Exception:
        logger.exception('Executive financial aging source failed')
        aging = {'status': 'error', 'by_currency': [], 'missing_balance_count': None,
                 'unknown_due_date_count': None, 'invoice_count': None, 'source_updated_at': None,
                 'as_of_date': as_of.isoformat(), 'description': 'The outgoing invoice register could not be read.'}
    kpis = [metric(identifier, label, unit=unit, status='unavailable', description=description,
                   source='Approved financial source not connected')
            for identifier, label, unit, description in UNAVAILABLE_HEADLINES]
    kpis[0]['period'] = 'year_to_date'
    working_capital = [
        _receivable_metric('receivables', 'Receivables', aging),
        _receivable_metric('receivables_over60', 'Receivables over 60 days', aging, 'over60'),
        metric('unbilled_wip', 'Unbilled WIP', unit='currency', status='unavailable',
               source='Approved WIP ledger not connected',
               description='Unbilled recognized work requires an approved work-in-progress accounting source.'),
        *[dict(row) for row in kpis if row['id'] in {'cash_position', 'dso'}],
    ]
    return {
        'status': finance_section['status'],
        'source_updated_at': finance_section.get('source_updated_at'),
        'source_timestamp_kind': finance_section.get('source_timestamp_kind'),
        'kpis': kpis, 'working_capital': {'metrics': working_capital, 'aging': aging},
        'actions': list(finance_section.get('actions', [])),
        'controls': {
            'ledger_connected': False, 'source_status': aging['status'],
            'source_updated_at': finance_section.get('source_updated_at'),
            'source_timestamp_kind': finance_section.get('source_timestamp_kind'),
            'description': 'Invoice-register controls are operational. Financial-close completion, submission status and approved forecast movements are not connected.',
            'unavailable_panels': ['historical_performance', 'business_units', 'project_margins', 'forecast_bridge', 'financial_close'],
        },
    }
