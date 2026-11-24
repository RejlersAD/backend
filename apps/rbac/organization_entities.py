"""The two RADAI organizations, defined once.

RADAI serves exactly two legal entities. Stored values everywhere stay the
short codes (``RAD`` / ``RIN``) that existing workflows already use; only the
display names come from here, so labels are identical in every module.

Organization rows (rbac.Organization) map to the codes through ORGANIZATION_CODES.
"""
import re

RAD = 'RAD'
RIN = 'RIN'

ENTITY_NAMES = {
    RAD: 'Rejlers Abu Dhabi',
    RIN: 'Rejlers India (RIN)',
}

# Django ``choices`` for any branch / entity field.
ENTITY_CHOICES = [(code, name) for code, name in ENTITY_NAMES.items()]

# rbac.Organization.code for each entity (RAD keeps its existing production code).
ORGANIZATION_CODES = {
    RAD: 'REJ_UAE',
    RIN: 'REJ_RIN',
}

DEFAULT_ENTITY = RAD

# 'India' or 'RIN' anywhere, or a trailing country code 'IN' ("Rejlers IN").
_INDIA = re.compile(r'\b(india|rin)\b|\bin\s*$', re.IGNORECASE)


ORGANIZATION_COUNTRIES = {RAD: 'United Arab Emirates', RIN: 'India'}


def organization_for(entity=DEFAULT_ENTITY):
    """The rbac.Organization row for RAD or RIN, created if missing (never 'Default Organization')."""
    from apps.rbac.models import Organization
    code = ORGANIZATION_CODES[entity]
    org = Organization.objects.filter(code=code).first() or Organization.objects.filter(name__iexact=ENTITY_NAMES[entity]).first()
    if org is None:
        org = Organization.objects.create(code=code, name=ENTITY_NAMES[entity], is_active=True,
                                          country=ORGANIZATION_COUNTRIES[entity])
    return org


def entity_for(value):
    """Map any legacy branch / organization text to RAD or RIN.

    'Rejlers IN', 'Rejlers India', 'RIN', 'Rejlers International Engineering
    Solutions IN' -> RIN. Every other non-empty value ('Rejlers AB',
    'Abu Dhabi', 'RAD', 'Default Organization', ...) -> RAD. Empty -> None.
    """
    text = str(value or '').strip()
    if not text:
        return None
    if text.upper() in ENTITY_NAMES:
        return text.upper()
    return RIN if _INDIA.search(text.replace('_', ' ')) else RAD
