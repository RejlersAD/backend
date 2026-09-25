"""Authorized PO discovery and invoice handoff, without creating financial records."""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import DecimalField, Exists, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination

from apps.procurement.models import PurchaseOrder
from apps.procurement.services.purchase_order_lifecycle import (
    PROGRESSED_STATUSES, require_purchase_order_approval,
)
from apps.rbac.action_policy import module_action_allowed
from apps.finance.models import Invoice, InvoicePurchaseOrderAllocation


class PurchaseOrderHandoffPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


def require_purchase_order_read(user):
    # Finance access alone must not disclose the procurement order register.
    if not module_action_allowed(user, 'procurement_orders', 'read'):
        raise PermissionDenied('Purchase Order read permission is required.')


def approved_order(order):
    if order.status not in PROGRESSED_STATUSES:
        return False
    try:
        require_purchase_order_approval(order)
    except ValidationError:
        return False
    return True


def purchase_order_queryset(user):
    require_purchase_order_read(user)
    amount_field = DecimalField(max_digits=18, decimal_places=2)
    allocations = InvoicePurchaseOrderAllocation.objects.filter(
        purchase_order_id=OuterRef('pk'), currency__iexact=OuterRef('currency'),
    ).values('purchase_order_id').annotate(total=Sum('allocated_amount')).values('total')
    inconsistent = InvoicePurchaseOrderAllocation.objects.filter(purchase_order_id=OuterRef('pk')).filter(
        ~Q(currency__iexact=OuterRef('currency')) | ~Q(invoice__currency__iexact=OuterRef('currency'))
        | ~Q(invoice__vendor_id=OuterRef('vendor_id')) | Q(invoice__vendor_id__isnull=True)
        | Q(allocated_amount__lte=0)
    )
    return PurchaseOrder.objects.filter(status__in=PROGRESSED_STATUSES).select_related(
        'vendor', 'enterprise_project',
    ).annotate(
        handoff_allocated=Coalesce(Subquery(allocations, output_field=amount_field),
                                  Value(Decimal('0')), output_field=amount_field),
        handoff_allocation_issue=Exists(inconsistent),
    ).annotate(handoff_remaining=F('total_amount') - F('handoff_allocated')).order_by('-created_at', '-id')


def order_choice(order, *, user=None, include_receiving=False, request=None):
    remaining = max(Decimal('0'), order.handoff_remaining)
    result = {
        'id': str(order.pk), 'po_number': order.po_number, 'title': order.title,
        'status': order.status, 'vendor_id': str(order.vendor_id), 'vendor_name': order.vendor.name,
        'currency': order.currency, 'total_amount': format(order.total_amount, '.2f'),
        'allocated_amount': format(order.handoff_allocated, '.2f'),
        'remaining_amount': None if order.handoff_allocation_issue else format(remaining, '.2f'),
        'allocation_issue': 'Existing invoice allocations need review.' if order.handoff_allocation_issue else '',
        'enterprise_project_id': str(order.enterprise_project_id) if order.enterprise_project_id else None,
        'can_import_invoice': bool(remaining > 0 and not order.handoff_allocation_issue and user
                                   and module_action_allowed(user, 'finance_incoming', 'create')),
    }
    if include_receiving:
        from apps.procurement.services.receiving import receiving_summary
        result['receiving'] = receiving_summary(order, request=request)
    return result


def purchase_order_choices(user, params, *, awaiting=False):
    queryset = purchase_order_queryset(user)
    if awaiting:
        queryset = queryset.filter(Q(handoff_remaining__gt=0) | Q(handoff_allocation_issue=True))
    search = str(params.get('search') or '').strip()
    if search:
        queryset = queryset.filter(Q(po_number__icontains=search) | Q(title__icontains=search)
                                   | Q(vendor__name__icontains=search))
    for parameter, field in (('id', 'pk'), ('vendor', 'vendor_id')):
        if params.get(parameter):
            from uuid import UUID
            try:
                value = UUID(str(params[parameter]))
            except (ValueError, TypeError, AttributeError):
                raise ValidationError({parameter: 'Choose a valid identifier.'})
            queryset = queryset.filter(**{field: value})
    # Approval evidence is a domain rule (including reviewed source documents),
    # not equivalent to the lifecycle status or a JSON status flag.
    return [order for order in queryset if approved_order(order)]


def confirmed_po_references(invoice):
    return [{'id': str(row.purchase_order_id), 'po_number': row.purchase_order.po_number}
            for row in invoice.po_allocations.all()]


def validate_new_allocation(order, *, vendor_id, currency, amount):
    """Recheck current evidence and balances while the caller holds the PO lock."""
    from apps.procurement.config import THREE_WAY_MATCHING_CONFIG
    if not approved_order(order):
        raise ValidationError({'purchase_order_id': 'Choose an approved, issued purchase order.'})
    if str(order.vendor_id) != str(vendor_id):
        raise ValidationError({'purchase_order_id': 'The purchase order supplier does not match the invoice.'})
    if str(order.currency).upper() != str(currency).upper():
        raise ValidationError({'purchase_order_id': 'The purchase order currency does not match the invoice.'})
    if not amount.is_finite() or amount <= 0:
        raise ValidationError({'allocated_amount': 'Enter a finite amount greater than zero.'})
    if amount.as_tuple().exponent < -2:
        raise ValidationError({'allocated_amount': 'Use at most two decimal places.'})
    if order.invoice_allocations.filter(
        ~Q(currency__iexact=order.currency) | ~Q(invoice__currency__iexact=order.currency)
        | ~Q(invoice__vendor_id=order.vendor_id) | Q(invoice__vendor_id__isnull=True)
        | Q(allocated_amount__lte=0)
    ).exists():
        raise ValidationError({'purchase_order_id': 'Existing invoice allocations need review before adding another.'})
    allocated = order.invoice_allocations.filter(currency__iexact=order.currency).aggregate(
        total=Sum('allocated_amount'))['total'] or Decimal('0')
    remaining = max(Decimal('0'), order.total_amount - allocated)
    tolerance = Decimal(str(THREE_WAY_MATCHING_CONFIG.get('tolerance_percentage', 5)))
    if amount > remaining * (Decimal('1') + tolerance / Decimal('100')):
        raise ValidationError({'allocated_amount': 'The amount exceeds the remaining purchase order value and configured tolerance.'})


class StaleInvoice(APIException):
    status_code = 409
    default_detail = 'The invoice changed. Refresh it before confirming the purchase order.'


def invoice_open_for_matching(invoice):
    return not (invoice.procurement_status in ('approved_for_payment', 'closed', 'rejected')
                or invoice.status in ('approved', 'processed', 'rejected')
                or invoice.payment_status in ('paid', 'partial', 'cancelled'))


def matching_capabilities(invoice, user):
    return {
        'can_allocate_purchase_order': bool(invoice_open_for_matching(invoice)
            and module_action_allowed(user, 'finance_incoming', 'update')
            and module_action_allowed(user, 'procurement_orders', 'read')),
        'can_recheck_match': bool(invoice.po_allocations.exists()
            and module_action_allowed(user, 'finance_incoming', 'create')),
    }


@transaction.atomic
def allocate_purchase_order(invoice_id, user, payload):
    require_purchase_order_read(user)
    if not module_action_allowed(user, 'finance_incoming', 'update'):
        raise PermissionDenied('Incoming Invoice update permission is required.')
    if payload.get('confirm_po_match') is not True:
        raise ValidationError({'confirm_po_match': 'Confirm the selected purchase order.'})
    invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
    token = payload.get('expected_updated_at')
    try:
        expected = parse_datetime(str(token)) if token else None
    except ValueError:
        expected = None
    if expected is None:
        raise ValidationError({'expected_updated_at': 'Refresh the invoice before confirming the purchase order.'})
    if expected != invoice.updated_at:
        raise StaleInvoice()
    if not invoice_open_for_matching(invoice):
        raise ValidationError({'invoice': 'This invoice is no longer open for purchase order matching.'})
    try:
        order = PurchaseOrder.objects.select_for_update().get(pk=payload.get('purchase_order_id'))
    except (PurchaseOrder.DoesNotExist, ValueError, TypeError, DjangoValidationError):
        raise ValidationError({'purchase_order_id': 'Choose an available purchase order.'})
    if invoice.po_allocations.filter(purchase_order=order).exists():
        raise ValidationError({'purchase_order_id': 'This invoice is already allocated to the selected PO.'})
    try:
        amount = Decimal(str(payload.get('allocated_amount')))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({'allocated_amount': 'Enter a valid allocation amount.'})
    validate_new_allocation(order, vendor_id=invoice.vendor_id, currency=invoice.currency, amount=amount)
    from apps.procurement.services.project_relationships import resolve_invoice_purchase_order
    try:
        result = resolve_invoice_purchase_order(
            invoice_id=invoice.pk, purchase_order_id=order.pk, allocated_amount=amount,
            user=user, reason=payload.get('reason') or '',
        )
    except DjangoValidationError as exc:
        raise ValidationError(getattr(exc, 'message_dict', None) or exc.messages) from exc
    # Even when match state stays unchanged, the new allocation invalidates a
    # previously read invoice's edit token.
    invoice.save(update_fields=['updated_at'])
    return result
