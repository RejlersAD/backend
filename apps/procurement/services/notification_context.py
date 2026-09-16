"""Saved procurement details shared by every Teams notification path."""

from decimal import Decimal, InvalidOperation

from .employee_display import employee_display_name


def _text(*values, default='Not specified'):
    return next((str(value).strip() for value in values if value is not None and str(value).strip()), default)


def _value(amount, currency):
    """Display the recorded total without applying VAT or discounts again."""
    if amount is None:
        return 'Not specified'
    try:
        number = Decimal(str(amount))
        if not number.is_finite():
            return 'Not specified'
        return f'{currency} {number:,.2f}'.strip()
    except (InvalidOperation, ValueError):
        return 'Not specified'


def _requisition_project(pr):
    project = getattr(pr, 'enterprise_project', None)
    details = getattr(pr, 'project_details', None)
    details = [item for item in details if isinstance(item, dict)] if isinstance(details, list) else []
    names = [_text(item.get('project_name'), item.get('name'), default='') for item in details]
    codes = [_text(item.get('project_number'), item.get('project_code'), item.get('code'), default='') for item in details]
    return {
        'project_name': _text(
            getattr(project, 'name', None), ', '.join(dict.fromkeys(name for name in names if name)),
            getattr(pr, 'project_department', None),
        ),
        'project_id': _text(
            getattr(project, 'code', None), ', '.join(dict.fromkeys(code for code in codes if code)),
            getattr(pr, 'project', None),
        ),
    }


def requisition_teams_context(pr, *, approval_level=None):
    """Describe a PR, including its newest linked PO when one exists."""
    po_number = ''
    orders = getattr(pr, 'purchase_orders', None)
    if orders is not None:
        # Match the PR serializer and consume an existing related-object cache.
        latest = max(orders.all(), key=lambda order: order.created_at, default=None)
        po_number = getattr(latest, 'po_number', '')
    vendor = getattr(pr, 'vendor', None)
    issuer = getattr(pr, 'issued_by', None)
    currency = _text(getattr(pr, 'currency', None), default='')
    return {
        'request_name': f'Purchase Requisition {pr.pr_number}',
        'po_number': _text(po_number, getattr(pr, 'po_number_reference', None), default='Not issued'),
        **_requisition_project(pr),
        'description': _text(getattr(pr, 'description_reason', None), getattr(pr, 'product_service', None)),
        'service': _text(getattr(pr, 'product_service', None), getattr(pr, 'price_description', None),
                         getattr(pr, 'title', None), getattr(pr, 'description_reason', None)),
        'vendor': _text(getattr(vendor, 'name', None), getattr(pr, 'supplier_name', None),
                        getattr(pr, 'preferred_supplier_if_any', None)),
        'value': _value(getattr(pr, 'total_price', None), currency),
        'currency': currency,
        'submitted_by': employee_display_name(issuer) if issuer else 'Not specified',
        'approval_level': approval_level,
    }


def purchase_order_teams_context(order, *, approval_level=None):
    """Describe the PO using its canonical project, vendor, and recorded total."""
    project = getattr(order, 'enterprise_project', None)
    legacy_project = getattr(order, 'project', None)
    requisition = getattr(order, 'pr_reference', None)
    pr_project = _requisition_project(requisition)
    vendor = getattr(order, 'vendor', None)
    creator = getattr(order, 'created_by', None)
    currency = _text(getattr(order, 'currency', None), default='')
    return {
        'request_name': f'Purchase Order {order.po_number}',
        'po_number': order.po_number,
        'project_name': _text(getattr(project, 'name', None), getattr(legacy_project, 'project_name', None),
                              pr_project['project_name']),
        'project_id': _text(getattr(project, 'code', None), getattr(legacy_project, 'project_number', None),
                            getattr(order, 'project_number', None), getattr(order, 'rad_project_no', None),
                            pr_project['project_id']),
        'description': _text(getattr(order, 'description', None), getattr(order, 'scope_of_services', None),
                             getattr(requisition, 'description_reason', None), getattr(order, 'title', None)),
        'service': _text(getattr(order, 'title', None), getattr(requisition, 'product_service', None),
                         getattr(order, 'scope_of_services', None), getattr(order, 'description', None)),
        'vendor': _text(getattr(vendor, 'name', None)),
        'value': _value(getattr(order, 'total_amount', None), currency),
        'currency': currency,
        'submitted_by': employee_display_name(creator) if creator else 'Not specified',
        'approval_level': approval_level,
    }
