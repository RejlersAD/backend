"""Bounded, read-only Microsoft Graph transport for durable mailbox sync.

Continuation URLs are private checkpoint data: validate their resource scope,
then send the original URL without rebuilding its opaque query string.
"""

import json
import math
import re
from datetime import timezone as datetime_timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote, urlsplit

import requests
from django.utils import timezone

from .microsoft_graph import (
    SalesGraphConfigurationError, SalesMailboxReadError, SalesMicrosoftGraphService,
)


class SalesMailboxSyncError(SalesMailboxReadError):
    """Safe worker error; never includes provider text, tokens or source URLs."""

    MESSAGES = {
        'checkpoint_expired': 'The mailbox checkpoint expired. A fresh sync is required.',
        'throttled': 'Microsoft requested a delay before the next mailbox sync.',
        'authorization_required': 'Mailbox authorization needs attention before sync can continue.',
        'source_unavailable': 'The mailbox source is no longer available.',
        'provider_unavailable': 'Microsoft mailbox access is temporarily unavailable.',
        'invalid_response': 'Microsoft returned invalid mailbox sync data. The page was not accepted.',
    }

    def __init__(self, *, status_code=502, code='invalid_response', retry_after=None,
                 retry_after_exceeds_limit=False):
        super().__init__(self.MESSAGES[code], status_code=status_code)
        self.code = code
        self.retry_after = retry_after
        # Never clamp an excessive delay and accidentally retry before Microsoft
        # allows it. The worker blocks this condition for operator review.
        self.retry_after_exceeds_limit = retry_after_exceeds_limit


class SalesMailboxSyncGraphService(SalesMicrosoftGraphService):
    SYNC_PAGE_SIZE = 50
    MAX_CURSOR_LENGTH = 16000
    MAX_RETRY_AFTER = 86400
    MAX_REQUEST_SECONDS = 30
    MAX_RESPONSE_BYTES = 6_000_000
    MAX_ERROR_BYTES = 64000
    ID_PATTERN = re.compile(r'[A-Za-z0-9_+=/-]{1,512}\Z')
    EXPIRED_CHECKPOINT_CODES = frozenset({
        'syncstatenotfound', 'errorsyncstatenotfound', 'errorinvalidsyncstatedata',
        'invaliddeltatoken', 'resyncrequired',
    })

    @classmethod
    def _valid_id(cls, value):
        return isinstance(value, str) and cls.ID_PATTERN.fullmatch(value) is not None

    def _folders_url(self, folder_id=None):
        # Reuse the application-only, fixed public-Graph base guard.
        self._mailbox_messages_url()
        mailbox = quote(self.connection.mailbox_address, safe='')
        root = f'{self.base_url}/users/{mailbox}/mailFolders'
        if folder_id is None:
            return f'{root}/delta'
        if not self._valid_id(folder_id):
            raise SalesMailboxSyncError()
        return f'{root}/{quote(folder_id, safe="")}/messages/delta'

    def _validate_delta_link(self, link, folder_id=None):
        self._folders_url(folder_id)
        if (
            not isinstance(link, str) or not link or len(link) > self.MAX_CURSOR_LENGTH
            or link.count('?') != 1 or '#' in link or '\\' in link
            or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in link)
            or re.search(r'%(?![0-9A-Fa-f]{2})', link)
        ):
            raise SalesMailboxSyncError()
        try:
            parsed = urlsplit(link)
            # Decode only to compare the entire expected path. Never split an
            # encoded ID or reconstruct the URL subsequently sent to Microsoft.
            path = unquote(parsed.path, encoding='utf-8', errors='strict')
            mailbox = self.connection.mailbox_address
            mailbox_key = mailbox.replace("'", "''")
            user_paths = (f'/v1.0/users/{mailbox}', f"/v1.0/users('{mailbox_key}')")
            folder_paths = ('/mailFolders/delta', '/mailfolders/delta')
            if folder_id is not None:
                folder_paths = tuple(
                    suffix
                    for collection in ('mailFolders', 'mailfolders')
                    for suffix in (
                        f'/{collection}/{folder_id}/messages/delta',
                        f"/{collection}('{folder_id}')/messages/delta",
                    )
                )
            expected_paths = {user + folder for user in user_paths for folder in folder_paths}
            valid = (
                parsed.scheme == 'https' and parsed.netloc == 'graph.microsoft.com'
                and path in expected_paths and bool(parsed.query) and not parsed.fragment
            )
        except (UnicodeError, ValueError):
            valid = False
        if not valid:
            raise SalesMailboxSyncError()
        return link

    def _read_delta_page(self, *, folder_id=None, cursor=''):
        url = self._folders_url(folder_id)
        if not isinstance(cursor, str):
            raise SalesMailboxSyncError()
        params = {'$select': 'id'}
        if cursor:
            url = self._validate_delta_link(cursor, folder_id)
            params = None
        payload = self._read_mailbox_json(url, params=params)
        values = payload.get('value')
        if not isinstance(values, list) or len(values) > self.SYNC_PAGE_SIZE:
            raise SalesMailboxSyncError()
        records = []
        for value in values:
            if (
                not isinstance(value, dict) or not self._valid_id(value.get('id'))
                or ('@removed' in value and not isinstance(value['@removed'], dict))
            ):
                raise SalesMailboxSyncError()
            records.append({'id': value['id'], 'removed': '@removed' in value})
        next_link = payload.get('@odata.nextLink')
        delta_link = payload.get('@odata.deltaLink')
        if (next_link is None) == (delta_link is None):
            raise SalesMailboxSyncError()
        if next_link is not None:
            next_link = self._validate_delta_link(next_link, folder_id)
            if next_link == url:
                raise SalesMailboxSyncError()
        else:
            # An unchanged delta link with an empty page is a legitimate poll.
            delta_link = self._validate_delta_link(delta_link, folder_id)
        return {'records': records, 'next_link': next_link, 'delta_link': delta_link}

    def read_folder_changes(self, cursor=''):
        """Read one page from the mailbox folder hierarchy delta collection."""
        return self._read_delta_page(cursor=cursor)

    def read_message_changes(self, folder_id, cursor=''):
        """Read one page of IDs/removals; the worker fetches source separately."""
        if not self._valid_id(folder_id):
            raise SalesMailboxSyncError()
        return self._read_delta_page(folder_id=folder_id, cursor=cursor)

    def _retry_delay(self, response):
        value = response.headers.get('Retry-After', '')
        if not isinstance(value, str) or not value.strip():
            return None, False
        value = value.strip()
        if len(value) > 128:
            return None, True
        if re.fullmatch(r'[0-9]+', value):
            delay = int(value)
        else:
            try:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=datetime_timezone.utc)
                delay = max(0, math.ceil((stamp - timezone.now()).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                return None, False
        return (None, True) if delay > self.MAX_RETRY_AFTER else (delay, False)

    def _response_payload(self, response, *, limit):
        """Cap decoded download bytes before allocating/parsing the full body."""
        data = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                if len(data) + len(chunk) > limit:
                    raise SalesMailboxSyncError()
                data.extend(chunk)
        except requests.RequestException:
            raise SalesMailboxSyncError(status_code=503, code='provider_unavailable') from None
        try:
            payload = json.loads(data)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise SalesMailboxSyncError() from None
        if not isinstance(payload, dict):
            raise SalesMailboxSyncError()
        return payload

    def _read_mailbox_json(self, url, *, params=None, body_format=None, request_timeout=None):
        """Read a constructed/validated URL, including inherited source capture."""
        try:
            token = self.token()
        except SalesGraphConfigurationError:
            raise SalesMailboxSyncError(status_code=503, code='authorization_required') from None
        except (requests.RequestException, RuntimeError, ValueError, TypeError, AttributeError):
            raise SalesMailboxSyncError(status_code=503, code='provider_unavailable') from None
        headers = {
            'Authorization': f'Bearer {token}', 'Accept': 'application/json',
            'Prefer': f'IdType="ImmutableId", odata.maxpagesize={self.SYNC_PAGE_SIZE}',
        }
        if body_format in {'text', 'html'}:
            headers['Prefer'] += f', outlook.body-content-type="{body_format}"'
        timeout = self.timeout if request_timeout is None else request_timeout
        timeout = max(0.1, min(timeout, self.MAX_REQUEST_SECONDS))
        try:
            response = requests.request(
                'GET', url, params=params, headers=headers, timeout=timeout,
                allow_redirects=False, stream=True,
            )
        except requests.RequestException:
            raise SalesMailboxSyncError(status_code=503, code='provider_unavailable') from None
        try:
            status = response.status_code
            if status != 200:
                retry_after, excessive_delay = self._retry_delay(response)
                code = 'provider_unavailable'
                if status in {401, 403}:
                    code = 'authorization_required'
                elif status == 429:
                    code = 'throttled'
                elif status == 410:
                    code = 'checkpoint_expired'
                elif status in {400, 404, 409, 412}:
                    error_code = ''
                    try:
                        payload = self._response_payload(response, limit=self.MAX_ERROR_BYTES)
                        error = payload.get('error')
                        error_code = error.get('code', '') if isinstance(error, dict) else ''
                    except SalesMailboxSyncError as exc:
                        if exc.code != 'invalid_response':
                            raise
                    if isinstance(error_code, str) and error_code.casefold() in self.EXPIRED_CHECKPOINT_CODES:
                        code = 'checkpoint_expired'
                    else:
                        code = 'source_unavailable' if status == 404 else 'invalid_response'
                raise SalesMailboxSyncError(
                    status_code=status, code=code, retry_after=retry_after,
                    retry_after_exceeds_limit=excessive_delay,
                )
            return self._response_payload(response, limit=self.MAX_RESPONSE_BYTES)
        finally:
            response.close()
