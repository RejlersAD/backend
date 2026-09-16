"""Renew saved original PR links without granting access to unrelated files."""

import re
from copy import deepcopy
from datetime import date
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files.storage import default_storage

from .document_filenames import build_procurement_pdf_filename


SIGNED_PR_TYPE = 'signed_purchase_requisition_pdf'


def _record_storage_key(requisition, value, sha256=''):
    """Match this PR's current folder or its historical generated filename.

    As with original PO sources, saved metadata alone must not authorize an
    unrelated storage path. New uploads have a UUID folder; older imports used
    a year folder and a filename containing the PR number or saved PDF digest.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        return ''
    if '\\' in value or '%' in value or value.startswith('/'):
        return ''
    parts = value.split('/')
    if any(part in ('', '.', '..') for part in parts):
        return ''
    if str(PurePosixPath(value)) != value:
        return ''

    root = 'procurement/signed_requisitions/'
    prefix = f'{root}{requisition.pk}/'
    if value.startswith(prefix):
        # The UUID folder binds the source to this record even if its editable
        # PR number changes. Keep validating the generated filename structure.
        remainder = value[len(prefix):]
        pattern = r'(\d{4})/[A-Za-z0-9._-]+_Purchase_Requisition_\1-\d{2}-\d{2}(?:_[A-Za-z0-9]{7})?\.pdf'
        return value if re.fullmatch(pattern, remainder) else ''
    remainder = value[len(root):] if value.startswith(root) else ''
    # The full generated prefix prevents PR-1 from authorizing PR-10's PDF.
    template = build_procurement_pdf_filename(requisition.pr_number, 'pr', date(2000, 1, 1))
    name_prefix = template.removesuffix('2000-01-01.pdf')
    pattern = rf'(\d{{4}})/{re.escape(name_prefix)}\1-\d{{2}}-\d{{2}}(?:_[A-Za-z0-9]{{7}})?\.pdf'
    if re.fullmatch(pattern, remainder):
        return value
    # Before standardized filenames, imports used a content digest prefix.
    # It must match this record's saved attachment digest, not the URL alone.
    if isinstance(sha256, str) and re.fullmatch(r'[a-fA-F0-9]{64}', sha256):
        digest_pattern = rf'(?:\d{{4}}|unknown)/{sha256[:12]}_[\w.-]+\.pdf'
        if re.fullmatch(digest_pattern, remainder, flags=re.IGNORECASE):
            return value
    return ''


def _legacy_url_key(requisition, value, sha256=''):
    if not isinstance(value, str) or not value:
        return ''
    try:
        source = urlsplit(value)
        media = urlsplit(str(settings.MEDIA_URL))
    except ValueError:
        return ''
    if source.username or source.password:
        return ''
    if source.scheme not in ('http', 'https', '') or (source.netloc and not source.scheme):
        return ''
    if not source.netloc and source.scheme:
        return ''
    bases = [media]
    # django-storages may generate endpoint/bucket/media URLs even though
    # MEDIA_URL uses bucket.endpoint/media. Trust only the active storage's
    # exact endpoint, bucket and location, never arbitrary S3 hosts/buckets.
    try:
        endpoint = getattr(default_storage, 'endpoint_url', '')
        bucket = getattr(default_storage, 'bucket_name', '')
        location = getattr(default_storage, 'location', '')
        if all(isinstance(part, str) for part in (endpoint, bucket, location)) and endpoint and bucket:
            endpoint = urlsplit(endpoint)
            if endpoint.scheme in ('http', 'https') and endpoint.netloc and not (
                endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            ):
                prefix = '/'.join(part.strip('/') for part in (endpoint.path, bucket, location) if part.strip('/'))
                bases.append(endpoint._replace(path='/' + prefix + '/'))
    except (OSError, ValueError, NotImplementedError, BotoCoreError, ClientError):
        pass
    path = unquote(source.path)
    for base in bases:
        base_path = base.path.rstrip('/') + '/'
        if source.scheme == base.scheme and source.netloc == base.netloc and path.startswith(base_path):
            key = _record_storage_key(requisition, path[len(base_path):], sha256)
            if key:
                return key
    return ''


def requisition_source_key(requisition, attachment):
    """Resolve only a designated, saved original belonging to this PR."""
    if not isinstance(attachment, dict) or SIGNED_PR_TYPE not in (
        attachment.get('type'), attachment.get('document_type'),
    ):
        return ''
    digest = attachment.get('sha256')
    explicit_key = attachment.get('storage_key') or attachment.get('s3_key')
    if explicit_key:
        return _record_storage_key(requisition, explicit_key, digest)
    return next((candidate for candidate in (
        _legacy_url_key(requisition, attachment.get('url'), digest),
        _legacy_url_key(requisition, attachment.get('s3_url'), digest),
    ) if candidate), '')


def requisition_original_source(requisition, attachment_index):
    """Look up an original by its position in this PR's saved attachments."""
    attachments = requisition.attachments or []
    try:
        index = int(attachment_index)
    except (TypeError, ValueError):
        return None
    if not isinstance(attachments, list) or index < 0 or index >= len(attachments):
        return None
    attachment = attachments[index]
    key = requisition_source_key(requisition, attachment)
    if not key:
        return None
    return {
        'storage_key': key,
        'filename': str(attachment.get('filename') or 'Uploaded PR.pdf'),
    }


def refreshed_requisition_attachments(requisition):
    """Return a response copy with fresh URLs; never persist temporary links."""
    attachments = deepcopy(requisition.attachments or [])
    if not isinstance(attachments, list):
        return attachments
    for attachment in attachments:
        if not isinstance(attachment, dict) or SIGNED_PR_TYPE not in (
            attachment.get('type'), attachment.get('document_type'),
        ):
            continue
        key = requisition_source_key(requisition, attachment)
        fresh_url = ''
        if key:
            try:
                fresh_url = default_storage.url(key)
            except (OSError, ValueError, NotImplementedError, BotoCoreError, ClientError):
                # The rest of the recommendation remains usable if its file
                # storage is unavailable; the UI reports the missing link.
                pass
        attachment['url'] = fresh_url
        attachment['s3_url'] = fresh_url
    return attachments
