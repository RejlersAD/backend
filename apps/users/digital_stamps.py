"""Owner-scoped digital company seals, retained as lossless profile artwork."""

import base64
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


JARMO_PROFILE_EMAIL = 'jarmo.suominen@rejlers.ae'
MAX_STAMP_BYTES = 10 * 1024 * 1024
MAX_STAMP_PIXELS = 25_000_000
MAX_STAMP_SIDE = 4096


def digital_stamp_profile(user=None, *, for_update=False):
    """Return the unique eligible account; a caller can request only itself."""
    from apps.rbac.models import UserProfile

    if user is not None and (
        not user.is_authenticated or not user.is_active
        or str(user.email or '').strip().casefold() != JARMO_PROFILE_EMAIL
    ):
        return None
    profiles = UserProfile.objects.filter(
        user__email__iexact=JARMO_PROFILE_EMAIL,
        user__is_active=True, status='active', is_deleted=False,
    ).only('user_id', 'signature_image', 'stamp_image', 'stamp_updated_at')
    if for_update:
        profiles = profiles.select_for_update()
    profiles = list(profiles[:2])
    if len(profiles) != 1 or user is not None and profiles[0].user_id != user.pk:
        return None
    return profiles[0]


def stamp_image_data_url(upload):
    """Keep uploaded ink, transparency and useful resolution without OCR."""
    if not upload:
        raise ValueError('Choose a digital stamp image to upload.')
    if upload.size > MAX_STAMP_BYTES:
        raise ValueError('Digital stamp image must be 10 MB or smaller.')
    if upload.content_type not in ('image/png', 'image/jpeg', 'image/jpg'):
        raise ValueError('Use a PNG or JPEG image for the digital stamp.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(upload) as header:
                if header.format not in ('PNG', 'JPEG'):
                    raise ValueError('Use a PNG or JPEG image for the digital stamp.')
                if header.width * header.height > MAX_STAMP_PIXELS:
                    raise ValueError('Digital stamp image dimensions are too large; use 25 megapixels or fewer.')
                if getattr(header, 'n_frames', 1) != 1:
                    raise ValueError('Use a single, still digital stamp image.')
                header.verify()
            upload.seek(0)
            with Image.open(upload) as original:
                source = ImageOps.exif_transpose(original).convert('RGBA')
                if not source.getchannel('A').getbbox():
                    raise ValueError('The digital stamp image is fully transparent. Choose a visible stamp.')
                source.thumbnail((MAX_STAMP_SIDE, MAX_STAMP_SIDE), Image.Resampling.LANCZOS)
                output = BytesIO()
                source.save(output, format='PNG', optimize=True)
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError) as error:
        raise ValueError('The uploaded file is not a valid PNG or JPEG image.') from error
    content = output.getvalue()
    if len(content) > MAX_STAMP_BYTES:
        raise ValueError('The processed digital stamp exceeds 10 MB. Choose an image with smaller dimensions.')
    return 'data:image/png;base64,' + base64.b64encode(content).decode('ascii')
