"""Paged current customer invoices with totals over the entire filtered register.

Home invoice amounts are persisted AED values. Foreign-currency outstanding
balances have no persisted AED equivalent and must not be converted on read;
an explicitly recorded zero balance is zero without a currency conversion.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Case, Count, F, Max, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from rest_framework.exceptions import APIException, NotFound

from apps.rbac.action_policy import module_action_allowed
from .command_center import MONEY, ROUTES
from .receivables_dashboard import _Metric, _money, _selected, _unknown_metric


logger = logging.getLogger(__name__)
SORT_FIELDS = {
    'account': 'account', 'invoice_number': 'invoice_number',
    'invoice_date': 'invoice_date', 'due_date': 'due_date',
    'payment_status': 'payment_status', 'currency': 'normalized_currency',
    'amount': 'register_amount', 'amount_home': 'invoice_amount_aed',
    'amount_due_home': 'register_amount_due_home',
}
ORDERINGS = tuple(prefix + field for field in SORT_FIELDS for prefix in ('', '-'))
METRIC_FIELDS = {
    'amount': 'register_amount', 'amount_home': 'invoice_amount_aed',
    'amount_due_home': 'register_amount_due_home',
}


def _response(currency, company, page, page_size, ordering, status, reason=None):
    return {
        'schema_version': '1.0', 'status': status,
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
        'totals': {key: {**_unknown_metric(), 'currency': currency if key == 'amount' else 'AED'}
                   for key in METRIC_FIELDS},
        'definitions': {
            'scope': 'Current customer invoice register, including paid and unpaid invoices. Cancelled and credit-note records are excluded. Company and original currency filters apply.',
            'period': 'Dashboard period and ageing reference date do not filter this register. It shows current recorded invoice amounts and payment statuses.',
            'amount': 'Recorded invoice_amount in the original currency, with grand_total used only when invoice_amount is absent. Zero amounts remain zero.',
            'amount_home': 'Stored invoice_amount_aed only. This endpoint does not apply or refresh exchange rates.',
            'amount_due_home': 'Recorded balance_to_be_received is shown in home currency for original AED invoices. An explicitly recorded zero balance is zero in home currency without conversion. Other foreign or unspecified-currency balances remain unknown because no home-currency balance is stored.',
            'totals': 'Grand totals cover all matching invoices, across all pages. Recorded subtotals omit missing amounts; absent or unknown values are never replaced with zero.',
            'status': 'The recorded invoice payment status is displayed unchanged; it is not recalculated from an ageing reference date.',
        },
    }


def build_customer_invoice_register(user, *, currency='AED', company='', page=1, page_size=8,
                                    ordering='-invoice_date'):
    from apps.invoice_tracker.models import CustomerInvoice, PaymentStatus

    if not module_action_allowed(user, 'finance_outgoing', 'read'):
        return _response(currency, company, page, page_size, ordering, 'restricted',
                         'Read access to the customer invoice register is required.')
    try:
        with transaction.atomic():
            source = _selected(CustomerInvoice.objects.exclude(
                payment_status__in=['cancelled', 'credit_note'],
            ), currency, company).annotate(
                register_amount=Coalesce('invoice_amount', 'grand_total', output_field=MONEY),
                register_amount_due_home=Case(
                    When(normalized_currency='AED', then=F('balance_to_be_received')),
                    When(balance_to_be_received=0, then=Value(Decimal('0'))),
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
                totals[key] = {**metric.result(currency_known=currency != 'UNSPECIFIED' or key != 'amount'),
                               'currency': currency if key == 'amount' else 'AED'}
            status = 'incomplete' if any(metric['partial'] for metric in totals.values()) else 'available'
            data = _response(currency, company, page, page_size, ordering, status,
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
                'payment_status', 'normalized_currency', 'invoice_amount', 'grand_total',
                'register_amount', 'invoice_amount_aed', 'register_amount_due_home',
            )[offset:offset + page_size]
            status_labels = dict(PaymentStatus.choices)
            for row in rows:
                row_currency = row['normalized_currency'] or 'UNSPECIFIED'
                amount = row['register_amount'] if row_currency != 'UNSPECIFIED' else None
                data['rows'].append({
                    'id': row['id'], 'account': row['account'].strip() or 'Customer not recorded',
                    'company': row['company'].strip(), 'invoice_number': row['invoice_number'],
                    'invoice_date': row['invoice_date'].isoformat() if row['invoice_date'] else None,
                    'due_date': row['due_date'].isoformat() if row['due_date'] else None,
                    'payment_status': row['payment_status'],
                    'payment_status_label': status_labels.get(row['payment_status'], row['payment_status']),
                    'currency': row_currency,
                    'amount': _money(amount) if amount is not None else None,
                    'amount_home': _money(row['invoice_amount_aed']) if row['invoice_amount_aed'] is not None else None,
                    'amount_due_home': _money(row['register_amount_due_home']) if row['register_amount_due_home'] is not None else None,
                    'amount_basis': 'invoice_amount' if row['invoice_amount'] is not None else 'grand_total' if row['grand_total'] is not None else 'not_recorded',
                    'amount_due_home_basis': ('balance_to_be_received' if row_currency == 'AED'
                                              else 'zero_recorded_balance' if row['register_amount_due_home'] == 0
                                              else 'not_recorded_for_foreign_currency'),
                })
            return data
    except APIException:
        raise
    except Exception:
        logger.exception('Dashboard customer invoice register could not be read')
        return _response(currency, company, page, page_size, ordering, 'error',
                         'The customer invoice register could not be read.')
