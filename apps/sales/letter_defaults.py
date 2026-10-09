"""Company letterhead defaults for generated sales letters.

Values follow the standard Rejlers correspondence template. Every value can be
overridden per deployment through the ``SALES_LETTER_DEFAULTS`` Django setting
(merged as a shallow dict over these defaults) and the logo through
``SALES_LETTER_LOGO_PATH``.
"""

from pathlib import Path

from django.conf import settings

DEFAULT_LETTERHEAD = {
    'sender_name': 'Mr. Jarmo Suominen',
    'sender_company': 'Rejlers International Engineering Solutions AB',
    'focal_contact': 'Ms. Anam Abbas',
    'contact_number': '02 639 7449 / 0564153910',
    'fax': '02 639 7448',
    'email': 'uae.sales@rejlers.ae',
    'adnoc_unified_code': '0010001089',
    'signature_name': 'Jarmo Suominen',
    'signature_titles': [
        'Senior Vice President, Middle East Region',
        'CEO, Rejlers Abu Dhabi',
    ],
    'footer_company': 'Rejlers International Engineering Solutions AB',
    'footer_address': (
        'Millennium Tower, 13th Floor, Hamdan Street, P.O. Box 39317, '
        'Abu Dhabi, United Arab Emirates'
    ),
    'footer_contact': 'Tel: +971 2 639 7449 | Fax: +971 2 639 7448 | www.rejlers.ae',
    # Header line shown above the opportunity code in every response letter.
    'response_label': 'EOI Response',
}


def get_letterhead_defaults():
    """Return the merged letterhead defaults for this deployment."""
    overrides = getattr(settings, 'SALES_LETTER_DEFAULTS', None) or {}
    merged = {**DEFAULT_LETTERHEAD, **overrides}
    signature_titles = overrides.get('signature_titles')
    if signature_titles is not None:
        merged['signature_titles'] = list(signature_titles)
    return merged


def get_logo_path():
    """Resolve the letterhead logo file, or None when unavailable."""
    configured = getattr(settings, 'SALES_LETTER_LOGO_PATH', None)
    candidates = []
    if configured:
        candidates.append(Path(configured))
    base_dir = Path(getattr(settings, 'BASE_DIR', ''))
    # Use the official Rejlers PR/PO logo as specified
    candidates.append(base_dir.parent / 'frontend' / 'public' / 'assets' / 'procurement' / 'rejlers-pr-po-logo.png')
    candidates.append(base_dir / 'frontend' / 'public' / 'assets' / 'procurement' / 'rejlers-pr-po-logo.png')
    # Backend apps path (where the logo actually exists)
    candidates.append(base_dir / 'apps' / 'procurement' / 'assets' / 'rejlers-pr-po-logo.png')
    # Fallback to old locations
    candidates.append(base_dir.parent / 'frontend' / 'public' / 'assets' / 'images' / 'rejlers-logo.png')
    candidates.append(base_dir / 'frontend' / 'public' / 'assets' / 'images' / 'rejlers-logo.png')
    for candidate in candidates:
        try:
            if candidate and candidate.is_file():
                return candidate
        except OSError:
            continue
    return None
