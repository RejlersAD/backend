"""Opt-in calculations for the VAT treatment explicitly confirmed by a user.

Never use these helpers to infer or rewrite existing financial records.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VAT_PERCENTAGE = Decimal('5.00')
CENT = Decimal('0.01')
ZERO = Decimal('0.00')
MAX_MONEY = Decimal('9999999999999.99')
CONFIRMED_BASES = {'exclusive', 'inclusive', 'none'}


def decimal_amount(value):
    if value in (None, ''):
        return None
    try:
        result = Decimal(str(value).replace(',', ''))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def money(value):
    result = decimal_amount(value)
    if result is None or result < 0:
        raise ValueError('Amounts must be valid non-negative numbers.')
    if result > MAX_MONEY:
        raise ValueError('Amount exceeds the supported monetary range.')
    result = result.quantize(CENT, rounding=ROUND_HALF_UP)
    return result


def vat_totals(net_amount):
    net = money(net_amount)
    tax = money(net * VAT_PERCENTAGE / Decimal('100'))
    return {'net_amount': net, 'tax_amount': tax, 'total_amount': money(net + tax),
            'vat_percentage': VAT_PERCENTAGE}


def confirmed_totals(entered_amount, vat_basis, discount=ZERO):
    amount = discounted_net(entered_amount, discount)
    if vat_basis == 'exclusive':
        return vat_totals(amount)
    if vat_basis == 'inclusive':
        net = money(amount / Decimal('1.05'))
        return {'net_amount': net, 'tax_amount': money(amount - net),
                'total_amount': amount, 'vat_percentage': VAT_PERCENTAGE}
    if vat_basis == 'none':
        return {'net_amount': amount, 'tax_amount': ZERO,
                'total_amount': amount, 'vat_percentage': ZERO}
    raise ValueError('Confirm whether VAT applies and whether the entered price includes VAT.')


def discounted_net(subtotal, discount=ZERO):
    return money(max(ZERO, money(subtotal) - money(discount or ZERO)))


def items_subtotal(items):
    """Return a complete usable subtotal, rounding each discounted line once."""
    if not isinstance(items, list) or not items:
        return None
    total = ZERO
    for row in items:
        if not isinstance(row, dict):
            return None
        quantity = decimal_amount(row.get('quantity', row.get('qty')))
        price = decimal_amount(row.get('unit_price', row.get('price')))
        discount = decimal_amount(row.get('discount', row.get('line_discount', row.get('discount_amount', 0))) or 0)
        if quantity is not None and quantity > 0 and price is not None and price >= 0 and discount is not None and discount >= 0:
            amount = max(ZERO, quantity * price - discount)
        else:
            amount = decimal_amount(row.get('total', row.get('line_total')))
        if amount is None or amount < 0:
            return None
        total += money(amount)
    return money(total)


def financial_changes(instance, values, kind):
    if 'currency' in values and values['currency'] != getattr(instance, 'currency', None):
        return True
    fields = ('total_price', 'net_total_excl_vat') if kind == 'pr' else (
        'net_amount', 'total_amount', 'tax_amount', 'vat_percentage', 'discount_amount',
    )
    if any(name in values and decimal_amount(values[name]) != decimal_amount(getattr(instance, name, None))
           for name in fields):
        return True
    if 'items' in values and item_financials(values['items']) != item_financials(getattr(instance, 'items', [])):
        return True
    if kind == 'pr' and 'price_remarks_data' in values:
        old, new = getattr(instance, 'price_remarks_data', {}) or {}, values['price_remarks_data'] or {}
        if any(decimal_amount(new.get(name, 0)) != decimal_amount(old.get(name, 0))
               for name in ('discount_amount', 'discount_percentage')):
            return True
    return False


def item_financials(items):
    """Ignore descriptive fields and row order, while detecting price/quantity edits."""
    rows = []
    for row in items or []:
        if not isinstance(row, dict):
            continue
        values = [decimal_amount(row.get(key, row.get(alias, default))) for key, alias, default in (
            ('quantity', 'qty', 1), ('unit_price', 'price', 0),
            ('discount', 'line_discount', row.get('discount_amount', 0)), ('total', 'line_total', None),
        )]
        if values[2] is None:
            values[2] = ZERO
        if values[3] is None and all(value is not None for value in values[:3]):
            values[3] = money(max(ZERO, values[0] * values[1] - values[2]))
        rows.append(tuple('' if value is None else str(value.normalize()) for value in values))
    return sorted(rows)


def apply_confirmed_input(values, instance, kind):
    """Calculate confirmed tax choices; PR quotations may remain unconfirmed."""
    supplied_amount = values.pop('entered_amount', None)
    basis = values.get('vat_basis')
    if kind == 'pr':
        saved_basis = getattr(instance, 'vat_basis', 'unconfirmed')
        if basis is None and saved_basis in CONFIRMED_BASES:
            basis = saved_basis
        if basis in (None, 'unconfirmed') and saved_basis not in CONFIRMED_BASES:
            # The PR form records quoted prices without requiring a tax
            # decision. Keep the submitted net/total as entered; never infer
            # VAT or turn unconfirmed historical prices into a 5% calculation.
            if supplied_amount is not None:
                amount = money(supplied_amount)
                values.setdefault('net_total_excl_vat', amount)
                values.setdefault('total_price', amount)
            return values
    if instance is not None and supplied_amount is None and not financial_changes(instance, values, kind):
        if basis is None or basis == getattr(instance, 'vat_basis', 'unconfirmed'):
            return values
    if basis not in CONFIRMED_BASES:
        if supplied_amount is not None or (instance is not None and financial_changes(instance, values, kind)):
            raise ValueError('Confirm whether VAT applies and whether the entered price includes VAT before changing amounts.')
        return values
    metadata = values.get('price_remarks_data', getattr(instance, 'price_remarks_data', {})) or {}
    discount = metadata.get('discount_amount', 0) if kind == 'pr' else values.get(
        'discount_amount', getattr(instance, 'discount_amount', 0),
    )
    items = values.get('items', getattr(instance, 'items', [])) or []
    partial_pr_lines = kind == 'pr' and any(
        not isinstance(row, dict)
        or row.get('quantity', row.get('qty')) in (None, '')
        or row.get('unit_price', row.get('price')) in (None, '')
        for row in items
    )
    if (partial_pr_lines and instance is not None and supplied_amount is None
            and basis == getattr(instance, 'vat_basis', 'unconfirmed')
            and not any(
                field in values and decimal_amount(values[field]) != decimal_amount(getattr(instance, field, None))
                for field in ('total_price', 'net_total_excl_vat')
            )):
        # Updating incomplete detail does not authorize recalculating legacy
        # header amounts that were recorded under the same VAT choice.
        return values
    subtotal = supplied_amount
    if subtotal is None and 'items' in values and not partial_pr_lines:
        subtotal = items_subtotal(values['items'])
    if subtotal is None:
        field = ('total_price' if kind == 'pr' else 'total_amount') if basis == 'inclusive' else (
            'net_total_excl_vat' if kind == 'pr' else 'net_amount'
        )
        # A submitted canonical net/gross already excludes order discount.
        if values.get(field) is not None:
            subtotal, discount = values[field], 0
        elif (partial_pr_lines and not {'total_price', 'net_total_excl_vat'}.intersection(values)
              and getattr(instance, field, None) is not None):
            # Incomplete quantities/prices cannot replace recorded header money
            # with a partial quoted subtotal or apply the discount a second time.
            subtotal, discount = getattr(instance, field), 0
    if subtotal is None:
        if instance is None and all(values.get(field) is None for field in (
            'total_price', 'net_total_excl_vat', 'total_amount', 'net_amount',
        )):
            return values  # An empty PR draft may record its user's choice.
        raise ValueError('Enter the price amount to confirm its VAT treatment.')
    totals = confirmed_totals(subtotal, basis, discount)
    if kind == 'pr':
        values.update(net_total_excl_vat=totals['net_amount'], total_price=totals['total_amount'])
    else:
        values.update(totals)
    return values
