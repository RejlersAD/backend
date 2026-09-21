"""Paged current customer invoices with totals over the entire filtered register.

Home invoice amounts are persisted AED values. Foreign-currency outstanding
balances have no persisted AED equivalent and must not be converted on read;
a calculated zero balance is zero without a currency conversion.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Case, Count, DateField, F, Func, IntegerField, Max, Q, Sum, Value, When
from django.db.models.functions import Coalesce, NullIf, Trim
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound

from apps.rbac.action_policy import module_action_allowed
from apps.invoice_tracker.services.receivable_balance import annotate_receivable_balance
from .command_center import MONEY, ROUTES
from .receivables_dashboard import _Metric, _money, _selected, _unknown_metric


logger = logging.getLogger(__name__)
SORT_FIELDS = {
    'company': 'register_customer_company', 'account': 'register_customer_company',
    'invoice_number': 'invoice_number',
    'invoice_date': 'invoice_date', 'due_date': 'due_date',
    'invoice_sent_date': 'invoice_sent_date', 'project_name': 'project_name',
    'payment_terms': 'payment_terms', 'pm': 'pm', 'remarks': 'remarks',
    'days_overdue': 'register_days_overdue', 'payment_date': 'payment_date',
    'actual_payment_received': 'register_receipts',
    'invoice_amount': 'register_amount', 'invoice_amount_aed': 'invoice_amount_aed',
    'payment_status': 'payment_status', 'currency': 'normalized_currency',
    'amount': 'register_amount', 'amount_home': 'invoice_amount_aed',
    'amount_due_home': 'register_amount_due_home',
}
ORDERINGS = tuple(prefix + field for field in SORT_FIELDS for prefix in ('', '-'))
METRIC_FIELDS = {
    'amount': 'register_amount', 'amount_home': 'invoice_amount_aed',
    'amount_due_home': 'register_amount_due_home',
    'actual_payment_received': 'register_receipts',
}


class _DateDifferenceDays(Func):
    """Date subtraction in days, without a persisted or stale overdue field."""

    template = '(%(expressions)s)'
    arg_joiner = ' - '
    output_field = IntegerField()

    def as_sqlite(self, compiler, connection, **extra_context):
        return self.as_sql(
            compiler, connection,
            template='CAST(django_timestamp_diff(%(expressions)s) / 86400000000 AS INTEGER)',
            arg_joiner=', ', **extra_context,
        )


def _response(currency, company, page, page_size, ordering, status, as_of, reason=None):
    return {
        'schema_version': '1.0', 'status': status, 'as_of_date': as_of.isoformat(),
        'source': {
            'status': status, 'reason': reason,
            'route': None if status == 'restricted' else ROUTES['receivables'],
            'source_updated_at': None,
        },
        'currency': currency, 'home_currency': 'AED', 'currency_conversion_applied': False,
        'filters': {'company': company, 'currency': currency}, 'ordering': ordering,
        'pagination': {'page': page, 'page_size': page_size, 'count': None,
                       'pages': None, 'has_next': False, 'has_previous': False},
        'rows': [],
        'totals': {key: {**_unknown_metric(), 'currency': currency if key in ('amount', 'actual_payment_received') else 'AED'}
                   for key in METRIC_FIELDS},
        'definitions': {
            'scope': 'Current customer invoice register, including paid and unpaid invoices. Cancelled and credit-note records are excluded. Company and original currency filters apply.',
            'customer': 'Customer uses the recorded COMPANY column, trimmed of surrounding spaces. The original account is retained separately and is never a fallback customer name. Customer ordering uses company; account is accepted as a legacy ordering alias.',
            'period': 'Dashboard period and ageing reference date do not filter this register. It shows current recorded invoice amounts and payment statuses.',
            'amount': 'Recorded Invoice Amount (L), invoice_amount, in the original currency. Missing values remain unknown; grand total is not a substitute. Zero amounts remain zero.',
            'amount_home': 'Stored invoice_amount_aed only. This endpoint does not apply or refresh exchange rates.',
            'amount_due_home': 'Invoice Amount (L) minus Actual Payment Received (AA), treating a missing payment as zero, is shown in home currency for original AED invoices. Missing Invoice Amount stays unknown; stored balance and grand total are not substitutes. A calculated zero balance is zero in home currency without conversion. Other foreign or unspecified-currency balances remain unknown because no home-currency balance is stored.',
            'actual_payment_received': 'Rows preserve recorded Actual Payment Received (AA), including null when blank. The total treats blank receipts as zero, matching the balance formula, in the original invoice currency.',
            'days_overdue': 'Days past the contractual due date against as_of_date, calculated on read. Missing due dates remain unknown; paid invoices and due dates on or after the reference date show zero. The selected date changes ageing only and does not filter this current register.',
            'totals': 'Grand totals cover all matching invoices, across all pages. Recorded subtotals omit missing invoice amounts. Only blank receipts are treated as zero, under the documented receipt formula.',
            'status': 'The recorded invoice payment status is displayed unchanged; it is not recalculated from an ageing reference date.',
        },
    }


def build_customer_invoice_register(user, *, currency='AED', company='', page=1, page_size=8,
                                    ordering='-invoice_date', as_of=None):
    from apps.invoice_tracker.models import CustomerInvoice, PaymentStatus

    as_of = as_of or timezone.localdate()
    if not module_action_allowed(user, 'finance_outgoing', 'read'):
        return _response(currency, company, page, page_size, ordering, 'restricted', as_of,
                         'Read access to the customer invoice register is required.')
    try:
        with transaction.atomic():
            source = annotate_receivable_balance(_selected(CustomerInvoice.objects.exclude(
                payment_status__in=['cancelled', 'credit_note'],
            ), currency, company)).annotate(
                register_customer_company=NullIf(Trim('company'), Value('')),
                register_amount=F('invoice_amount'),
                register_receipts=Coalesce('actual_payment_received', Value(Decimal('0')), output_field=MONEY),
                register_days_overdue=Case(
                    When(due_date__isnull=True, then=Value(None)),
                    When(Q(payment_status='paid') | Q(due_date__gte=as_of), then=Value(0)),
                    default=_DateDifferenceDays(Value(as_of, output_field=DateField()), F('due_date')),
                    output_field=IntegerField(),
                ),
                register_amount_due_home=Case(
                    When(normalized_currency='AED', then=F('calculated_receivable_balance')),
                    When(calculated_receivable_balance=0, then=Value(Decimal('0'))),
                    default=Value(None), output_field=MONEY,
                ),
            )
            summary = source.aggregate(
                count=Count('pk'), updated=Max('updated_at'),
                **{key: Sum(field) for key, field in METRIC_FIELDS.items()},
                **{key + '_missing': Count('pk', filter=Q(**{field + '__isnull': True}))
                   for key, field in METRIC_FIELDS.items()},
            )
            count = summary['count']
            pages = max(1, (count + page_size - 1) // page_size)
            if page > pages:
                raise NotFound('This invoice page is out of range.')
            totals = {}
            for key in METRIC_FIELDS:
                metric = _Metric(total=summary[key] if summary[key] is not None else Decimal('0'),
                                 count=count, missing=summary[key + '_missing'])
                original_currency = key in ('amount', 'actual_payment_received')
                totals[key] = {**metric.result(currency_known=currency != 'UNSPECIFIED' or not original_currency),
                               'currency': currency if original_currency else 'AED'}
            status = 'incomplete' if any(metric['partial'] for metric in totals.values()) else 'available'
            data = _response(currency, company, page, page_size, ordering, status, as_of,
                             'Some amounts are not recorded. Recorded subtotals exclude missing values.' if status == 'incomplete' else None)
            data['totals'] = totals
            data['source']['source_updated_at'] = summary['updated'].isoformat() if summary['updated'] else None
            data['pagination'].update(count=count, pages=pages, has_next=page < pages, has_previous=page > 1)
            sort_field = SORT_FIELDS[ordering.lstrip('-')]
            # Nulls sort last in both directions, with a stable unique tie-break.
            sort = F(sort_field).desc(nulls_last=True) if ordering.startswith('-') else F(sort_field).asc(nulls_last=True)
            offset = (page - 1) * page_size
            rows = source.order_by(sort, 'id').values(
                'id', 'account', 'company', 'invoice_number', 'invoice_date', 'due_date',
                'payment_status', 'normalized_currency', 'invoice_amount',
                'register_amount', 'invoice_amount_aed', 'register_amount_due_home',
                'invoice_sent_date', 'project_name', 'payment_terms', 'pm',
                'register_days_overdue', 'payment_date', 'actual_payment_received', 'remarks',
            )[offset:offset + page_size]
            status_labels = dict(PaymentStatus.choices)
            for row in rows:
                row_currency = row['normalized_currency'] or 'UNSPECIFIED'
                amount = row['register_amount'] if row_currency != 'UNSPECIFIED' else None
                data['rows'].append({
                    'id': row['id'], 'account': row['account'],
                    'company': row['company'].strip(), 'customer': row['company'].strip() or 'Customer not recorded',
                    'invoice_number': row['invoice_number'],
                    'invoice_date': row['invoice_date'].isoformat() if row['invoice_date'] else None,
                    'invoice_sent_date': row['invoice_sent_date'].isoformat() if row['invoice_sent_date'] else None,
                    'due_date': row['due_date'].isoformat() if row['due_date'] else None,
                    'payment_date': row['payment_date'].isoformat() if row['payment_date'] else None,
                    'project_name': row['project_name'], 'payment_terms': row['payment_terms'],
                    'pm': row['pm'], 'days_overdue': row['register_days_overdue'], 'remarks': row['remarks'],
                    'payment_status': row['payment_status'],
                    'payment_status_label': status_labels.get(row['payment_status'], row['payment_status']),
                    'currency': row_currency,
                    'amount': _money(amount) if amount is not None else None,
                    'invoice_amount': _money(amount) if amount is not None else None,
                    'amount_home': _money(row['invoice_amount_aed']) if row['invoice_amount_aed'] is not None else None,
                    'invoice_amount_aed': _money(row['invoice_amount_aed']) if row['invoice_amount_aed'] is not None else None,
                    'actual_payment_received': (_money(row['actual_payment_received'])
                                                if row['actual_payment_received'] is not None and row_currency != 'UNSPECIFIED' else None),
                    'amount_due_home': _money(row['register_amount_due_home']) if row['register_amount_due_home'] is not None else None,
                    'amount_basis': 'invoice_amount' if row['invoice_amount'] is not None else 'not_recorded',
                    'amount_due_home_basis': ('invoice_amount_less_actual_payment_received' if row_currency == 'AED'
                                              else 'zero_calculated_balance' if row['register_amount_due_home'] == 0
                                              else 'not_recorded_for_foreign_currency'),
                })
            return data
    except APIException:
        raise
    except Exception:
        logger.exception('Dashboard customer invoice register could not be read')
        return _response(currency, company, page, page_size, ordering, 'error', as_of,
                         'The customer invoice register could not be read.')
