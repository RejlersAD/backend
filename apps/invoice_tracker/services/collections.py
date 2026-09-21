"""Current collection queues and original-currency reporting; never write on GET."""
from datetime import timedelta

from django.db.models import Count, Max, Q
from django.db.models.functions import Trim, Upper
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from apps.finance.services.command_center import _source_summary
from .receivable_balance import annotate_receivable_balance


QUEUE_KEYS = ('all', 'open', 'overdue', 'due_soon', 'partial', 'paid')
AGE_KEYS = ('current', 'days_1_30', 'days_31_60', 'days_61_90', 'over90', 'unknown_due_date')
FILTER_OPTION_LIMIT = 200


def _open():
    return (~Q(payment_status__in=['paid', 'cancelled', 'credit_note'])
            & (Q(calculated_receivable_balance__gt=0) | Q(calculated_receivable_balance__isnull=True)))


def queue_conditions(as_of):
    unsettled = _open()
    sunday = as_of + timedelta(days=6 - as_of.weekday())
    return {
        'all': Q(), 'open': unsettled,
        'overdue': unsettled & Q(due_date__lt=as_of),
        'due_soon': unsettled & Q(due_date__gte=as_of, due_date__lte=sunday),
        'partial': unsettled & (Q(payment_status='partial') | Q(actual_payment_received__gt=0)),
        'paid': Q(payment_status='paid') | (~Q(payment_status__in=['cancelled', 'credit_note'])
                                          & Q(calculated_receivable_balance=0, invoice_amount__gt=0)),
    }


def _age_conditions(as_of):
    return {
        'current': Q(due_date__gte=as_of),
        'days_1_30': Q(due_date__lt=as_of, due_date__gte=as_of - timedelta(days=30)),
        'days_31_60': Q(due_date__lt=as_of - timedelta(days=30), due_date__gte=as_of - timedelta(days=60)),
        'days_61_90': Q(due_date__lt=as_of - timedelta(days=60), due_date__gte=as_of - timedelta(days=90)),
        'over90': Q(due_date__lt=as_of - timedelta(days=90)),
        'unknown_due_date': Q(due_date__isnull=True),
    }


def _date_param(params, name):
    raw = params.get(name)
    if not raw:
        return None
    try:
        value = parse_date(raw)
    except (TypeError, ValueError):
        value = None
    if value is None or value.isoformat() != raw:
        raise ValidationError({name: 'Use a valid YYYY-MM-DD date.'})
    return value


def filter_collection_queryset(qs, params, *, as_of, include_queue=True):
    """Add filters without changing the shared register's existing visibility."""
    queue = params.get('queue') or 'all'
    if queue not in QUEUE_KEYS:
        raise ValidationError({'queue': 'Choose all, open, overdue, due_soon, partial or paid.'})
    qs = annotate_receivable_balance(qs)
    currency = params.get('currency')
    if currency:
        currency = currency.strip().upper()
        qs = qs.annotate(collection_currency=Upper(Trim('currency')))
        qs = qs.filter(Q(collection_currency='') | Q(currency__isnull=True)) if currency == 'UNSPECIFIED' else qs.filter(collection_currency=currency)
    if params.get('company'):
        # Match the overview's recorded-company facet without changing stored
        # import values or broadening an exact company match to its branches.
        qs = qs.annotate(collection_company=Trim('company')).filter(
            collection_company=params['company'].strip(),
        )
    if params.get('pm'):
        qs = qs.filter(pm=params['pm'])
    for start_name, end_name, field in [('date_from', 'date_to', 'invoice_date'), ('due_from', 'due_to', 'due_date')]:
        start, end = _date_param(params, start_name), _date_param(params, end_name)
        if start and end and start > end:
            raise ValidationError({end_name: 'The end date must be on or after the start date.'})
        if start:
            qs = qs.filter(**{field + '__gte': start})
        if end:
            qs = qs.filter(**{field + '__lte': end})
    age = params.get('ageing')
    if age:
        if age not in AGE_KEYS:
            raise ValidationError({'ageing': 'Choose a supported contractual due-date bucket.'})
        qs = qs.filter(_open() & _age_conditions(as_of)[age])
    if include_queue:
        qs = qs.filter(queue_conditions(as_of)[queue])
    return qs


def _options(source, field):
    options = source.exclude(**{field: ''}).order_by(field).values(field).annotate(count=Count('pk'))
    total = options.count()
    rows = [{'value': row[field], 'count': row['count']} for row in options[:FILTER_OPTION_LIMIT]]
    return rows, total


def build_collections_summary(source, *, full_source, queue='all', generated_at=None):
    now = generated_at or timezone.now()
    as_of = timezone.localtime(now).date()
    source = annotate_receivable_balance(source)
    conditions = queue_conditions(as_of)
    counts = source.aggregate(**{key: Count('pk', filter=condition) for key, condition in conditions.items()})
    # Credit notes remain in the all-register count but are not collectible.
    health = _source_summary('receivables', source.exclude(payment_status='credit_note'), as_of)
    partial_rows = source.filter(conditions['partial']).order_by().values('currency').annotate(count=Count('pk'))
    partial_counts = {}
    for item in partial_rows:
        currency = (item['currency'] or '').strip().upper() or 'UNSPECIFIED'
        partial_counts[currency] = partial_counts.get(currency, 0) + item['count']
    for row in health['by_currency']:
        row['partial_count'] = partial_counts.get(row['currency'], 0)
    health['partial_count'] = counts['partial']
    companies, company_count = _options(full_source, 'company')
    managers, manager_count = _options(full_source, 'pm')
    currencies = sorted({(item or '').strip().upper() or 'UNSPECIFIED'
                         for item in source.order_by().values_list('currency', flat=True).distinct()})
    updated = source.aggregate(updated=Max('updated_at'))['updated']
    return {
        'schema_version': '1.0', 'generated_at': now.isoformat(), 'as_of_date': as_of.isoformat(),
        'source_updated_at': updated.isoformat() if updated else None,
        'source_timestamp_kind': 'record_updated_at',
        'due_soon_through': (as_of + timedelta(days=6 - as_of.weekday())).isoformat(),
        'selected_queue': queue, 'filtered_count': counts[queue], 'counts': counts,
        'currency_conversion_applied': False, 'currencies': currencies,
        'collection_health': health,
        'scope': {
            'summary': 'All authorized records matching filters and search, before the queue selection.',
            'register': 'The selected queue is applied to the paginated register.',
            'filter_options': 'Recorded companies and project managers in the authorized register; up to 200 values each.',
            'consolidated': False,
        },
        'queue_definitions': {
            'all': 'All register records, including cancelled invoices and credit notes.',
            'open': 'Positive or unknown Invoice Amount (L) minus Actual Payment Received (AA), treating missing payments as zero. Missing Invoice Amount stays unknown; stored balance and grand total are not substitutes. Paid, cancelled and credit-note records are excluded.',
            'overdue': 'Open invoices with a contractual due date before today; counts include unknown balances.',
            'due_soon': 'Open invoices due today through the end of the current local week (Sunday).',
            'partial': 'Open invoices recorded as partially paid or with a positive recorded payment received.',
            'paid': 'Recorded paid status, or a calculated zero balance with a positive Invoice Amount (L), excluding cancelled and credit-note records.',
        },
        'filter_options': {
            'companies': companies, 'project_managers': managers,
            'companies_count': company_count, 'project_managers_count': manager_count,
            'truncated': {'companies': company_count > FILTER_OPTION_LIMIT, 'project_managers': manager_count > FILTER_OPTION_LIMIT},
        },
        'unavailable_metrics': [
            {'id': identifier, 'status': 'unavailable', 'value': None, 'reason': reason}
            for identifier, reason in [
                ('dso', 'Approved sales and average receivables for a defined period are not connected.'),
                ('promises', 'A dated promise-to-pay workflow is not connected.'),
                ('disputes', 'A governed invoice-dispute workflow is not connected.'),
                ('contacted', 'Collection contact events and their dates are not recorded.'),
            ]
        ],
    }
