"""Read one configured SharePoint workbook into the portfolio snapshot store.

HTTP runs outside database transactions. A short source lease is checked again
by the importer at publication, so an expired worker cannot publish stale data.
"""
import base64
from dataclasses import dataclass
from datetime import timedelta
from email.utils import parsedate_to_datetime
import hashlib
import os
import re
from urllib.parse import quote, urlsplit
import uuid

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .importer import import_workbook
from .models import PortfolioSource


GRAPH_URL = 'https://graph.microsoft.com/v1.0'
HTTP_TIMEOUT = (10, 45)
MAX_FILE_BYTES = 25 * 1024 * 1024
LEASE_SECONDS = 900
MAX_RETRY_SECONDS = 900


class PortfolioSyncError(RuntimeError):
    """An intentionally sanitized error safe to store or return to operators."""


class TransientGraphError(PortfolioSyncError):
    def __init__(self, status_code, retry_after=None):
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f'SharePoint returned HTTP {status_code}; synchronization can be retried.')

    def __reduce__(self):
        # Celery may serialize retry exceptions across worker/result boundaries.
        return type(self), (self.status_code, self.retry_after)


def _setting(name, default=''):
    return getattr(settings, name, os.environ.get(name, default))


def sync_enabled():
    return str(_setting('PORTFOLIO_SYNC_ENABLED', False)).strip().lower() in {'true', '1', 'yes', 'on'}


@dataclass(frozen=True, repr=False)
class SharePointConfiguration:
    tenant_id: str
    client_id: str
    client_secret: str
    drive_id: str
    item_id: str

    @classmethod
    def configured(cls, *, require_item=True):
        values = [str(_setting('PORTFOLIO_SHAREPOINT_' + name)).strip()
                  for name in ('TENANT_ID', 'CLIENT_ID', 'CLIENT_SECRET', 'DRIVE_ID', 'ITEM_ID')]
        if not all(values if require_item else values[:3]):
            raise PortfolioSyncError('SharePoint portfolio configuration is incomplete.')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', values[0]):
            raise PortfolioSyncError('SharePoint tenant identifier is invalid.')
        return cls(*values)

    @property
    def identity(self):
        identity = '\n'.join((self.tenant_id.lower(), self.drive_id, self.item_id))
        return hashlib.sha256(identity.encode('utf-8')).hexdigest()

    @property
    def item_url(self):
        return f'{GRAPH_URL}/drives/{quote(self.drive_id, safe="")}/items/{quote(self.item_id, safe="")}'


def _retry_after(value):
    if not value:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        try:
            moment = parsedate_to_datetime(str(value))
            if moment.tzinfo is None:
                return None
            seconds = int((moment - timezone.now()).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return min(MAX_RETRY_SECONDS, max(1, seconds))


def _check_status(response, expected):
    if response.status_code in expected:
        return
    if response.status_code == 429 or 500 <= response.status_code <= 599:
        raise TransientGraphError(response.status_code, _retry_after(response.headers.get('Retry-After')))
    raise PortfolioSyncError(f'SharePoint returned HTTP {response.status_code}; check access and source configuration.')


def _json(response):
    try:
        payload = response.json()
    except (ValueError, TypeError):
        raise PortfolioSyncError('SharePoint returned an invalid JSON response.') from None
    if not isinstance(payload, dict):
        raise PortfolioSyncError('SharePoint returned an invalid JSON response.')
    return payload


def _download_url_allowed(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.fragment
                and any(host.endswith('.' + suffix) for suffix in
                        ('sharepoint.com', 'sharepoint-df.com', '1drv.com', 'onedrive.com', 'storage.live.com')))
    except ValueError:
        return False


class SharePointWorkbookClient:
    def __init__(self, configuration):
        self.configuration = configuration
        self._access_token = None

    def token(self):
        if self._access_token is None:
            with requests.post(
                f'https://login.microsoftonline.com/{quote(self.configuration.tenant_id, safe="")}/oauth2/v2.0/token',
                data={'grant_type': 'client_credentials', 'client_id': self.configuration.client_id,
                      'client_secret': self.configuration.client_secret, 'scope': 'https://graph.microsoft.com/.default'},
                timeout=HTTP_TIMEOUT, allow_redirects=False,
            ) as response:
                _check_status(response, {200})
                token = _json(response).get('access_token')
                if not isinstance(token, str) or not token:
                    raise PortfolioSyncError('SharePoint authentication did not return an access token.')
                self._access_token = token
        return self._access_token

    def resolve_link(self, share_url):
        encoded = base64.urlsafe_b64encode(share_url.encode('utf-8')).decode('ascii').rstrip('=')
        with requests.get(
            f'{GRAPH_URL}/shares/u!{encoded}/driveItem',
            headers={'Authorization': f'Bearer {self.token()}', 'Accept': 'application/json'},
            params={'$select': 'id,name,parentReference'}, timeout=HTTP_TIMEOUT, allow_redirects=False,
        ) as response:
            _check_status(response, {200})
            data = _json(response)
        item_id = data.get('id')
        drive_id = data.get('parentReference', {}).get('driveId')
        if not isinstance(item_id, str) or not item_id or not isinstance(drive_id, str) or not drive_id:
            raise PortfolioSyncError('The SharePoint sharing link did not resolve to a drive item.')
        return {'drive_id': drive_id, 'item_id': item_id}

    def metadata(self, etag=None):
        headers = {'Authorization': f'Bearer {self.token()}', 'Accept': 'application/json'}
        if etag:
            headers['If-None-Match'] = etag
        with requests.get(self.configuration.item_url, headers=headers,
                          params={'$select': 'id,name,eTag,size,parentReference,file'},
                          timeout=HTTP_TIMEOUT, allow_redirects=False) as response:
            _check_status(response, {200, 304})
            if response.status_code == 304:
                return None
            data = _json(response)
        if (data.get('id') != self.configuration.item_id
                or data.get('parentReference', {}).get('driveId') != self.configuration.drive_id):
            raise PortfolioSyncError('SharePoint returned a different source file identity.')
        if not isinstance(data.get('eTag'), str) or not data['eTag'] or not data.get('file'):
            raise PortfolioSyncError('SharePoint workbook metadata is incomplete.')
        if not isinstance(data.get('name'), str) or not data['name'].lower().endswith('.xlsx'):
            raise PortfolioSyncError('The configured SharePoint source must be an XLSX workbook.')
        size = data.get('size')
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_FILE_BYTES:
            raise PortfolioSyncError('SharePoint workbook is empty or exceeds the 25 MB import limit.')
        return data

    def download(self, expected_size):
        url = self.configuration.item_url + '/content'
        headers = {'Authorization': f'Bearer {self.token()}', 'Accept': 'application/octet-stream'}
        for redirect in range(4):
            with requests.get(url, headers=headers, stream=True, timeout=HTTP_TIMEOUT, allow_redirects=False) as response:
                _check_status(response, {200, 301, 302, 303, 307, 308})
                if response.status_code != 200:
                    target = response.headers.get('Location', '')
                    if redirect == 3 or not _download_url_allowed(target):
                        raise PortfolioSyncError('SharePoint returned an unsupported workbook download redirect.')
                    url = target
                    # Preauthenticated download links never receive our Graph credential.
                    headers = {'Accept': 'application/octet-stream'}
                    continue
                length = response.headers.get('Content-Length')
                if length:
                    try:
                        length = int(length)
                    except (ValueError, TypeError):
                        raise PortfolioSyncError('SharePoint download length is invalid.') from None
                    if length > MAX_FILE_BYTES or length < 0:
                        raise PortfolioSyncError('SharePoint workbook exceeds the 25 MB import limit.')
                chunks, received = [], 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > MAX_FILE_BYTES:
                        raise PortfolioSyncError('SharePoint workbook exceeds the 25 MB import limit.')
                    chunks.append(chunk)
                if received != expected_size:
                    raise PortfolioSyncError('SharePoint workbook changed or its download was incomplete; retry synchronization.')
                return b''.join(chunks)
        raise PortfolioSyncError('SharePoint workbook download could not be completed.')


def _lease(source_key):
    now = timezone.now()
    with transaction.atomic():
        source, _ = PortfolioSource.objects.get_or_create(key=source_key)
        source = PortfolioSource.objects.select_for_update().get(pk=source.pk)
        if source.sync_token and (source.sync_expires_at is None or source.sync_expires_at > now):
            return None
        token = uuid.uuid4()
        source.sync_token = token
        source.sync_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        source.last_attempt_at = now
        source.save(update_fields=['sync_token', 'sync_expires_at', 'last_attempt_at'])
        return source, token


def _require_lease(source_id, token):
    if not PortfolioSource.objects.filter(pk=source_id, sync_token=token,
                                          sync_expires_at__gt=timezone.now()).exists():
        raise PortfolioSyncError('The portfolio synchronization lease expired; a newer worker may own this source.')


def sync_sharepoint(*, source_key='poc', dry_run=False):
    """Manual sync is allowed when scheduling is disabled; configuration is required."""
    from .workbook import PARSER_VERSION

    source_key = str(source_key).strip()
    if not source_key or len(source_key) > 80:
        raise PortfolioSyncError('Portfolio source key is empty or too long.')
    acquired = _lease(source_key)
    if acquired is None:
        return {'status': 'busy', 'source_key': source_key}
    source, token = acquired
    try:
        configuration = SharePointConfiguration.configured()
        client = SharePointWorkbookClient(configuration)
        cached = bool(source.active_snapshot_id and source.remote_identity == configuration.identity
                      and source.etag and source.active_snapshot.parser_version == PARSER_VERSION)
        before = client.metadata(source.etag if cached else None)
        _require_lease(source.pk, token)
        if before is None:
            if not cached:
                raise PortfolioSyncError('SharePoint returned unchanged status without a matching published source.')
            with transaction.atomic():
                current = PortfolioSource.objects.select_for_update().get(pk=source.pk)
                _require_lease(current.pk, token)
                if (current.active_snapshot_id != source.active_snapshot_id
                        or current.remote_identity != configuration.identity or current.etag != source.etag):
                    raise PortfolioSyncError('The published portfolio changed during synchronization; retry synchronization.')
                current.last_success_at = timezone.now()
                current.last_error = ''
                current.save(update_fields=['last_success_at', 'last_error'])
            return {'status': 'unchanged', 'source_key': source_key}
        content = client.download(before['size'])
        after = client.metadata()
        if after is None or any(before[key] != after[key] for key in ('id', 'eTag', 'size', 'name')):
            raise PortfolioSyncError('SharePoint workbook changed during download; retry synchronization.')
        _require_lease(source.pk, token)
        # Publication and its remote version marker commit together. Every HTTP
        # request has already completed before this database transaction starts.
        with transaction.atomic():
            try:
                result = import_workbook(content, source_key=source_key, original_filename=before['name'],
                                         dry_run=dry_run, expected_sync_token=token)
            except (ValueError, TypeError):
                raise PortfolioSyncError('Portfolio workbook validation failed; review the import preview before retrying.') from None
            if dry_run:
                return {'status': 'validated', 'source_key': source_key, 'import': result}
            current = PortfolioSource.objects.select_for_update().get(pk=source.pk)
            _require_lease(current.pk, token)
            if str(current.active_snapshot_id) != str(result.get('snapshot_id')) or not current.active_snapshot_id:
                raise PortfolioSyncError('The imported workbook was not published as the active portfolio snapshot.')
            current.etag = before['eTag']
            current.remote_identity = configuration.identity
            current.last_success_at = timezone.now()
            current.last_error = ''
            current.save(update_fields=['etag', 'remote_identity', 'last_success_at', 'last_error'])
        return {'status': 'synchronized', 'source_key': source_key, 'import': result}
    except Exception as exc:
        if isinstance(exc, PortfolioSyncError):
            error = exc
        elif isinstance(exc, requests.RequestException):
            error = PortfolioSyncError('SharePoint connection failed; check network connectivity and retry synchronization.')
        else:
            error = PortfolioSyncError('Portfolio synchronization failed; the latest completed snapshot remains available.')
        PortfolioSource.objects.filter(pk=source.pk, sync_token=token).update(last_error=str(error))
        raise error from None
    finally:
        PortfolioSource.objects.filter(pk=source.pk, sync_token=token).update(sync_token=None, sync_expires_at=None)


def resolve_sharepoint_link(share_url=None):
    """Resolve an explicitly configured link without redeeming or changing access."""
    share_url = str(share_url if share_url is not None else _setting('PORTFOLIO_SHAREPOINT_URL')).strip()
    if not _download_url_allowed(share_url):
        raise PortfolioSyncError('Configure a valid HTTPS SharePoint sharing URL before resolving its identifiers.')
    configuration = SharePointConfiguration.configured(require_item=False)
    try:
        return SharePointWorkbookClient(configuration).resolve_link(share_url)
    except PortfolioSyncError:
        raise
    except requests.RequestException:
        raise PortfolioSyncError('SharePoint link resolution failed; check network connectivity and configured application access.') from None
    except (ValueError, TypeError, AttributeError):
        raise PortfolioSyncError('SharePoint link resolution returned an invalid drive item.') from None
