"""Stored approval artwork shared by the official PO document renderers."""

import base64
from io import BytesIO
from pathlib import Path
import re

from PIL import Image

from apps.users.digital_stamps import JARMO_PROFILE_EMAIL, digital_stamp_profile


DEFAULT_APPROVAL_STAMP_REFERENCE = '/assets/procurement/commercial-license-stamp.png'
APPROVAL_STAMP_PATH = Path(__file__).resolve().parent.parent / 'assets' / 'commercial-license-stamp.png'


def approval_image_stream(value):
    """Decode a recorded image, never a PDF evidence URL or a profile lookup."""
    raw = str(value or '')
    if not raw.startswith('data:image/') or ';base64,' not in raw:
        return None
    try:
        stream = BytesIO(base64.b64decode(raw.split(',', 1)[1], validate=True))
        with Image.open(stream) as image:
            image.verify()
        stream.seek(0)
        return stream
    except (ValueError, OSError):
        return None


def approval_stamp_stream(value):
    """Resolve the existing company seal without fetching arbitrary URLs.

    Older internally signed orders predate stamp capture. Their renderer may
    use the company seal after validating the recorded final signature. An
    explicit PDF/source reference stays evidence, not a replacement image.
    """
    reference = str(value or '').strip()
    if reference in ('', DEFAULT_APPROVAL_STAMP_REFERENCE):
        return BytesIO(APPROVAL_STAMP_PATH.read_bytes())
    return approval_image_stream(reference)


def completed_jarmo_profile_artwork(order):
    """Display Jarmo's profile artwork on his already-approved completed POs.

    This is a document presentation policy, not an approval action. Recorded
    names, dates, signatures and original source PDFs are never rewritten.
    Read the profile each time so a saved replacement is reflected in exports.
    """
    from .approval_integrity import purchase_order_signature_issue

    name = re.sub(r'\s+', ' ', str(getattr(order, 'approved_by_name', '') or '')).strip().casefold()
    if (str(getattr(order, 'status', '')).strip().casefold() != 'completed'
            or name != 'jarmo suominen'
            or not (getattr(order, 'approved_date', None) or getattr(order, 'approved_at', None))
            or purchase_order_signature_issue(order)):
        return None, None

    rows = [row for row in (getattr(order, 'approval_log', None) or []) if isinstance(row, dict)]
    internal = [row for row in rows if not row.get('external') and not row.get('evidence_document_id')]
    if any(str(row.get('status', '')).strip().casefold() != 'approved' for row in internal):
        return None, None
    external = [row for row in rows if row.get('external') or row.get('evidence_document_id')]
    if external and not any(
        str(row.get('status', '')).strip().casefold() == 'approved'
        and row.get('signature_verified') is not False
        and row.get('approval_evidence_complete') is not False
        and re.sub(r'\s+', ' ', str(row.get('approver') or row.get('approved_by_name') or '')).strip().casefold() == name
        for row in external
    ):
        return None, None

    profile = digital_stamp_profile()
    if profile is None:
        return None, None
    actor_id = str(getattr(order, 'approved_by_id', '') or '')
    if actor_id and actor_id != str(profile.user_id):
        return None, None
    if internal:
        def level(item):
            index, row = item
            try:
                return max(0, int(row.get('level')))
            except (ValueError, TypeError):
                return index

        final_level = max(level(item) for item in enumerate(internal))
        final_rows = [row for index, row in enumerate(internal) if level((index, row)) == final_level]
        # Source-only imports have no internal assignments. For internal
        # workflows, the final recorded actor must be this exact account.
        for row in final_rows:
            identifiers = [str(row[key]) for key in ('approved_by_id', 'user_id', 'signature_user_id') if row.get(key)]
            emails = [str(row[key]).strip().casefold() for key in (
                'approved_by_email', 'approver_email', 'user_email', 'signature_user_email',
            ) if row.get(key)]
            if (not identifiers and not emails
                    or any(value != JARMO_PROFILE_EMAIL for value in emails)
                    or not emails and any(value != str(profile.user_id) for value in identifiers)):
                return None, None
    signature = approval_image_stream(profile.signature_image)
    stamp = approval_image_stream(profile.stamp_image)
    if signature is None:
        return None, stamp
    return signature, stamp or approval_stamp_stream(DEFAULT_APPROVAL_STAMP_REFERENCE)
