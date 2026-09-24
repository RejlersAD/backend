"""Optional PO document settings stored with the protected contact metadata."""

from rest_framework.exceptions import ValidationError

from .purchase_order_introduction import SHOW_INTRODUCTION_KEY, validate_order_introduction


SCOPE_HEADING_KEY = 'show_scope_heading'


def validate_order_document_options(contacts):
    validate_order_introduction(contacts)
    for key, label in ((SCOPE_HEADING_KEY, 'Show heading'), (SHOW_INTRODUCTION_KEY, 'Show introduction')):
        if key in contacts and type(contacts[key]) is not bool:
            raise ValidationError({'contact_persons': f'{label} must be true or false.'})


def show_scope_heading(order):
    contacts = getattr(order, 'contact_persons', None)
    return not isinstance(contacts, dict) or contacts.get(SCOPE_HEADING_KEY) is not False
