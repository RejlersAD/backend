"""Stored approval artwork shared by the official PO document renderers."""

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image


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
