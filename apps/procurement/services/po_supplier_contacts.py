"""Read a named supplier's contact section without borrowing another firm's details."""

from __future__ import annotations

import re


_LEGAL_WORDS = {'llc', 'ltd', 'limited', 'inc', 'incorporated', 'company', 'co', 'corporation', 'corp'}
_GENERIC_WORDS = {
    'global', 'international', 'engineering', 'consultants', 'consultancy', 'services',
    'solutions', 'trading', 'industrial', 'industries', 'group', 'general', 'the',
}
_FIELD = re.compile(
    r'\b(?:(?:Seller|Supplier)\s+)?(?P<field>Contact\s+Person|Contact|Name|E[- ]?mail|'
    r'Phone|Telephone|Tel\.?|Mobile|Mob\.?|Address|Country)\s*:', re.IGNORECASE,
)
_EMAIL = re.compile(r'[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}', re.IGNORECASE)
_OTHER_PARTY = re.compile(
    r'^\s*(?:RAD\b|Buyer\b|Customer\b|Client\b|Invoicing\b|Invoice\b|'
    r'(?:Technical|Commercial)\s+(?:Focal|Contact)|Project\s+Manager\b)', re.IGNORECASE,
)


def _words(value):
    value = re.sub(r'\bL\s*\.\s*L\s*\.\s*C\s*\.?', ' LLC ', value, flags=re.IGNORECASE)
    return tuple(word for word in re.findall(r'[a-z0-9]+', value.lower()) if word not in _LEGAL_WORDS)


def _heading(line):
    """Only explicit company headings can establish ownership of a block."""
    match = re.match(r'^\s*(?:M\s*/\s*s\.?|Messrs\.?)\s+(.+?)\s*:?\s*$', line, re.IGNORECASE)
    if match:
        return match.group(1).rstrip(':').strip()
    if line.strip().endswith(':') and not _FIELD.match(line.strip()):
        return line.strip()[:-1].strip()
    return None


def _unique(values):
    values = list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
    return values[0] if len(values) == 1 else ''


def _person(value):
    value = re.split(r'[,;|]', value, maxsplit=1)[0].strip()
    if not (2 <= len(value.split()) <= 8) or len(value) > 160:
        return ''
    if re.fullmatch(r"[A-Za-zÀ-ž][A-Za-zÀ-ž .'’\-]*", value):
        return value
    return ''


def _phone(value):
    value = value.splitlines()[0].strip(' ;|')
    if not re.fullmatch(r'\+?[\d ()\-.]+', value):
        return ''
    digits = re.sub(r'\D', '', value)
    return ('+' if value.startswith('+') else '') + digits if 7 <= len(digits) <= 15 else ''


def parse_vendor_contact_block(text: str, vendor_name: str) -> dict[str, str]:
    """Return labelled seller fields from one unambiguous company block.

    A short company heading is accepted only for the known supplier's first
    distinctive name and when no other company heading shares that name.
    No missing phone, email, or country is inferred from the surrounding PO.
    """
    identity = _words(str(vendor_name or ''))
    if not identity:
        return {}
    lines = str(text or '').replace('\r\n', '\n').splitlines()
    headings = [(index, name, _words(name)) for index, line in enumerate(lines)
                if (name := _heading(line))]
    exact = [(index, name) for index, name, words in headings if words == identity]
    if exact:
        matches = exact
    else:
        brand = identity[0]
        if len(brand) < 4 or brand in _GENERIC_WORDS:
            return {}
        related = [(index, name, words) for index, name, words in headings if brand in words]
        matches = [(index, name) for index, name, words in related if words == (brand,)] if len(related) == 1 else []
    if len(matches) != 1:
        return {}

    start = matches[0][0] + 1
    block = []
    for line in lines[start:]:
        if (_heading(line) is not None or _OTHER_PARTY.match(line)
                or re.match(r'^\s*(?:---\s*Page\s+\d+\s*---|\d+\s*[.)]\s*[A-Z])', line)):
            break
        block.append(line)
        if len(block) >= 20:
            break
    body = '\n'.join(block).strip()
    if not body:
        return {}
    fields = list(_FIELD.finditer(body))
    captured = {}
    for index, match in enumerate(fields):
        end = fields[index + 1].start() if index + 1 < len(fields) else len(body)
        key = re.sub(r'[\s.\-]', '', match['field'].lower())
        value = body[match.end():end].strip(' \n\t;|')
        captured.setdefault(key, []).append(value)

    result = {}
    names = [_person(value.splitlines()[0]) for key in ('contactperson', 'contact', 'name')
             for value in captured.get(key, []) if value]
    if not any(names):
        first_line = body.splitlines()[0]
        first_label = _FIELD.search(first_line)
        names = [_person(first_line[:first_label.start()] if first_label else first_line)]
    if person := _unique(names):
        result['seller_contact_person'] = person
    if email := _unique([email for value in captured.get('email', []) for email in _EMAIL.findall(value)]):
        result['seller_email'] = email
    phone_values = [_phone(value) for key in ('phone', 'telephone', 'tel') for value in captured.get(key, []) if value]
    if not any(phone_values):
        phone_values = [_phone(value) for key in ('mobile', 'mob') for value in captured.get(key, []) if value]
    if phone := _unique(phone_values):
        result['seller_phone'] = phone
    address = _unique([' '.join(value.split()) for value in captured.get('address', []) if len(value) <= 500])
    if address:
        result['seller_address'] = address
    country = _unique([' '.join(value.split()) for value in captured.get('country', []) if len(value) <= 80])
    if re.search(r'\b(?:U\.?\s*A\.?\s*E\.?|United Arab Emirates)\b', country or address, re.IGNORECASE):
        country = 'United Arab Emirates'
    if country:
        result['seller_country'] = country
    return result
