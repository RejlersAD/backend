"""Optional, approved-content-protected opening statement for PO documents."""

from rest_framework.exceptions import ValidationError


INTRODUCTION_KEY = 'order_introduction'
MAX_INTRODUCTION_LENGTH = 10000
BUYER_NAME = 'Rejlers International Engineering Solutions'


def validate_order_introduction(contacts):
    """Validate only the optional statement; do not add defaults to saved data."""
    if INTRODUCTION_KEY not in contacts:
        return
    value = contacts[INTRODUCTION_KEY]
    if not isinstance(value, str) or len(value) > MAX_INTRODUCTION_LENGTH:
        raise ValidationError({
            'contact_persons': 'Enter a Buyer/Seller statement of at most 10,000 characters.',
        })
    if any((ord(char) < 32 and char not in '\n\r\t') or
           0xD800 <= ord(char) <= 0xDFFF or ord(char) in (0xFFFE, 0xFFFF)
           for char in value):
        raise ValidationError({'contact_persons': 'The Buyer/Seller statement contains unsupported characters.'})


def purchase_order_introduction(order):
    contacts = getattr(order, 'contact_persons', None)
    custom = contacts.get(INTRODUCTION_KEY) if isinstance(contacts, dict) else None
    # A saved blank string is an intentional omission. Only untouched legacy
    # records (without a text override) receive the standard introduction.
    if isinstance(custom, str):
        return custom.strip().replace('\r\n', '\n').replace('\r', '\n')
    seller = str(getattr(getattr(order, 'vendor', None), 'name', '') or '').strip() or '—'
    return f'We, {BUYER_NAME} (Buyer), issue this purchase order to {seller} (Seller).'
