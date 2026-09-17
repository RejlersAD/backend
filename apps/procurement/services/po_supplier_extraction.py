"""Read supplier-owned cover fields without consuming buyer contact details."""

import re


_EMAIL = r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}'
_FIELD_END = (
    r'\bSeller\s+(?:Address|Country|Contact(?:\s+Person)?|E[- ]?mail|Phone|Telephone|Reference|Fax)\b'
    r'|\b(?:Quote\s+Ref(?:erence)?\.?|(?:Trade\s+)?License\s+No\.?|Invoicing|Invoice\s+Address'
    r'|Buyer(?:\s+(?:Reference|Address))?|Payment|Delivery|Purchase\s+Summary)\b'
)
_LEGAL_END = r'(?:L\.?\s*L\.?\s*C\.?|(?:Pte|Pty)\.?\s+Ltd\.?|Limited|Ltd\.?|L\.?\s*L\.?\s*P\.?|Inc\.?)'


def seller_header(text):
    cover = re.split(r'---\s*Page\s+[2-9]\d*\s*---', text or '', maxsplit=1, flags=re.I)[0]
    seller = re.search(r'\bSeller\s*:', cover, re.I)
    if not seller:
        return ''
    return re.split(r'\b(?:Invoicing|Invoice\s+Address|Buyer|Purchase\s+Summary|Payment)\b',
                    cover[seller.start():], maxsplit=1, flags=re.I)[0]


def _value(section, label):
    match = re.search(label, section, re.I)
    if not match:
        return ''
    value = re.split(_FIELD_END, section[match.end():], maxsplit=1, flags=re.I)[0]
    return re.sub(r'\s+', ' ', value).strip(' :|\t\r\n')


def _bounded(value, limit=300):
    return value if len(value) <= limit else ''


def _email(value):
    match = re.search(_EMAIL, value, re.I)
    return match.group() if match else ''


def _phone(value):
    match = re.match(r'[+\d][\d ()+.-]+', value)
    number = match.group().strip(' .-') if match else ''
    return number if 7 <= len(re.sub(r'\D', '', number)) <= 15 else ''


def extract_seller_cover_details(text):
    section = seller_header(text)
    reference = _value(section, r'\bSeller\s+Reference\s*:')
    contact = _bounded(_value(section, r'\bSeller\s+Contact(?:\s+Person)?\s*:'), 150)
    if not contact:
        # A reference column can contain a general mailbox before its person.
        person = re.search(r'\b(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][A-Z .\'-]*?(?=\s+' + _EMAIL + r'|$)', reference, re.I)
        contact = _bounded(person.group().strip(), 150) if person else ''
    address = _bounded(_value(section, r'\bSeller\s+Address\s*:'))
    if not address:
        # Scans may put the first address line between "Seller" and "Address".
        split = re.search(r'\bSeller[ \t]+([^\n]+)\n[ \t]*Address[ \t]*:[ \t]*([^\n]*)', section, re.I)
        if split:
            parts = [re.split(_FIELD_END, part, maxsplit=1, flags=re.I)[0].strip() for part in split.groups()]
            address = _bounded(' '.join(part for part in parts if part))
    country = _bounded(_value(section, r'\bSeller\s+Country\s*:'), 100)
    if not country and re.search(r'\b(?:U\.?A\.?E\.?|United Arab Emirates)\b', address, re.I):
        country = 'United Arab Emirates'
    license_no = _value(section, r'\b(?:Trade\s+)?License\s+No\.?\s*:?')
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9 /-]{0,49}', license_no, re.I) or not re.search(r'\d', license_no):
        license_no = ''
    return {
        'vendor_license_no': license_no,
        'seller_contact_person': contact,
        'seller_email': _email(_value(section, r'\bSeller\s+E[- ]?mail\s*:')) or _email(reference),
        'seller_phone': _phone(_value(section, r'\bSeller\s+(?:Phone|Telephone)\s*:')),
        'seller_address': address,
        'seller_country': country,
    }


def needs_seller_layout(text, fields):
    section = seller_header(text)
    for field, label in (
        ('vendor_license_no', r'\b(?:Trade\s+)?License\s+No\b'),
        ('seller_address', r'\bSeller\s+(?:Address|[^\n]+\nAddress)\b'),
        ('seller_contact_person', r'\bSeller\s+(?:Contact|Reference)\b'),
    ):
        if not fields.get(field) and re.search(label, section, re.I):
            return True
    return False


def complete_wrapped_seller_name(text, name):
    """Retain a legal-name continuation split beside the reference column."""
    if not name or re.search(_LEGAL_END + r'\s*$', name, re.I):
        return name
    reference = re.search(r'\bSeller\s+Reference\s*:[^\n]*\n\s*([^\n]+)', seller_header(text), re.I)
    if not reference:
        return name
    continuation = re.match(r'([A-Z][A-Z &.\'-]{0,65}?' + _LEGAL_END + r')(?=\s|$)', reference.group(1), re.I)
    if not continuation:
        return name
    return f'{name} {continuation.group(1)}'.strip()
