"""Least-privilege Microsoft Graph client for Sales mailbox connectivity."""

import hashlib
import json
import os
import re
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .email_analysis import analyze_email_conversation, email_thread_role
from .email_content import MAX_HTML_CHARACTERS, project_email_body
from .graph_crypto import decrypt_token, encrypt_token, is_configured as token_encryption_configured
from .models import SalesMailboxConnection


class SalesGraphConfigurationError(RuntimeError):
    """Raised when the Sales Graph connection is incomplete."""


class SalesMailboxReadError(RuntimeError):
    """Safe public error for transient, read-only mailbox browsing."""

    def __init__(self, message, status_code=502, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SalesMicrosoftGraphService:
    MAILBOX_PAGE_SIZE = 50
    MAILBOX_CURSOR_MAX_AGE = 900
    MAILBOX_CURSOR_SALT = 'sales-mailbox-messages-v1'
    CONVERSATION_PAGE_SIZE = 25
    CONVERSATION_MAX_PAGES = 4
    CONVERSATION_MAX_MESSAGES = 100
    CONVERSATION_MAX_CHARACTERS = 2_000_000
    CONVERSATION_MAX_SECONDS = 20
    MESSAGE_FIELDS = (
        'id,subject,from,sender,receivedDateTime,sentDateTime,bodyPreview,'
        'hasAttachments,isRead,isDraft,importance'
    )
    THREAD_FIELDS = 'internetMessageHeaders,internetMessageId'
    DELEGATED_SCOPES = [
        'openid',
        'profile',
        'offline_access',
        'User.Read',
        'Mail.Read',
    ]

    def __init__(self, connection):
        self.connection = connection
        self.base_url = str(
            getattr(
                settings,
                'SALES_MICROSOFT_GRAPH_BASE_URL',
                'https://graph.microsoft.com/v1.0',
            )
        ).rstrip('/')
        self.timeout = int(getattr(settings, 'SALES_MICROSOFT_GRAPH_TIMEOUT', 30))
        self._token = None

    @classmethod
    def active(cls):
        connection = SalesMailboxConnection.objects.filter(enabled=True).order_by('created_at').first()
        if not connection:
            raise SalesGraphConfigurationError('Sales Outlook intake is not enabled.')
        return cls(connection)

    @staticmethod
    def _secret():
        return os.environ.get('RADAI_SALES_GRAPH_CLIENT_SECRET', '').strip()

    @property
    def tenant_id(self):
        """Use central delegated configuration while preserving legacy/admin records."""
        if self.connection.auth_mode == 'delegated':
            central = str(getattr(settings, 'SALES_MICROSOFT_TENANT_ID', '')).strip()
            if central:
                return central
        return self.connection.tenant_id.strip()

    @property
    def client_id(self):
        if self.connection.auth_mode == 'delegated':
            central = str(getattr(settings, 'SALES_MICROSOFT_CLIENT_ID', '')).strip()
            if central:
                return central
        return self.connection.client_id.strip()

    @classmethod
    def delegated_runtime_configuration(cls):
        """Return centrally managed values needed to start employee OAuth."""
        tenant_id = str(getattr(settings, 'SALES_MICROSOFT_TENANT_ID', '')).strip()
        client_id = str(getattr(settings, 'SALES_MICROSOFT_CLIENT_ID', '')).strip()
        missing = []
        if not tenant_id:
            missing.append('RADAI_SALES_GRAPH_TENANT_ID')
        if not client_id:
            missing.append('RADAI_SALES_GRAPH_CLIENT_ID')
        if not cls._secret():
            missing.append('RADAI_SALES_GRAPH_CLIENT_SECRET')
        if not token_encryption_configured():
            missing.append('SALES_GRAPH_TOKEN_ENCRYPTION_KEY')
        if missing:
            raise SalesGraphConfigurationError(
                'Outlook connection is not available yet. Contact your RADAI administrator.'
            )
        return tenant_id, client_id

    def _validate_configuration(self):
        missing = []
        if not self.tenant_id:
            missing.append('tenant ID')
        if not self.client_id:
            missing.append('application/client ID')
        if not self.connection.mailbox_address:
            missing.append('mailbox address')
        if not self._secret():
            missing.append('RADAI_SALES_GRAPH_CLIENT_SECRET')
        if self.connection.auth_mode == 'delegated' and not token_encryption_configured():
            missing.append('SALES_GRAPH_TOKEN_ENCRYPTION_KEY')
        if missing:
            raise SalesGraphConfigurationError(
                f'Microsoft Graph configuration is missing: {", ".join(missing)}.'
            )

    @staticmethod
    def _graph_error(response, operation):
        try:
            detail = response.json().get('error', {}).get('message', '')
        except (TypeError, ValueError, AttributeError):
            detail = ''
        suffix = f': {detail}' if detail else ''
        return RuntimeError(f'{operation} failed with HTTP {response.status_code}{suffix}')

    @property
    def redirect_uri(self):
        return str(settings.SALES_MICROSOFT_OAUTH_REDIRECT_URI)

    def delegated_authorization_url(self, state):
        self._validate_configuration()
        query = urlencode({
            'client_id': self.client_id,
            'response_type': 'code',
            'redirect_uri': self.redirect_uri,
            'response_mode': 'query',
            'scope': ' '.join(self.DELEGATED_SCOPES),
            'state': state,
            'prompt': 'select_account',
        })
        tenant = quote(self.tenant_id, safe='')
        return f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{query}'

    def _token_request(self, data):
        tenant = quote(self.tenant_id, safe='')
        response = requests.post(
            f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token',
            data=data,
            timeout=self.timeout,
        )
        if not response.ok:
            raise self._graph_error(response, 'Microsoft identity authentication')
        payload = response.json()
        if not payload.get('access_token'):
            raise RuntimeError('Microsoft identity authentication returned no access token.')
        return payload

    def complete_delegated_authorization(self, code):
        self._validate_configuration()
        payload = self._token_request({
            'client_id': self.client_id,
            'client_secret': self._secret(),
            'scope': ' '.join(self.DELEGATED_SCOPES),
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.redirect_uri,
        })
        refresh_token = payload.get('refresh_token')
        if not refresh_token:
            raise RuntimeError('Microsoft did not return an offline refresh token.')
        self._token = payload['access_token']
        profile = self.request('GET', '/me', params={
            '$select': 'id,displayName,mail,userPrincipalName',
        })
        mailbox_address = profile.get('mail') or profile.get('userPrincipalName')
        if not mailbox_address:
            raise RuntimeError('Microsoft account did not return a mailbox address.')
        self.connection.auth_mode = 'delegated'
        self.connection.mailbox_address = mailbox_address
        self.connection.encrypted_refresh_token = encrypt_token(refresh_token)
        self.connection.delegated_account_id = profile.get('id', '')
        self.connection.delegated_account_name = profile.get('displayName', '')
        self.connection.delegated_scopes = payload.get('scope', '').split()
        self.connection.connected_at = timezone.now()
        self.connection.enabled = True
        self.connection.save(update_fields=[
            'auth_mode', 'mailbox_address', 'encrypted_refresh_token',
            'delegated_account_id', 'delegated_account_name', 'delegated_scopes',
            'connected_at', 'enabled', 'updated_at',
        ])
        return self.health_check()

    def _delegated_token(self):
        refresh_token = decrypt_token(self.connection.encrypted_refresh_token)
        if not refresh_token:
            raise SalesGraphConfigurationError(
                'Outlook authorization is missing or cannot be decrypted. Reconnect your account.'
            )
        payload = self._token_request({
            'client_id': self.client_id,
            'client_secret': self._secret(),
            'scope': ' '.join(self.DELEGATED_SCOPES),
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'redirect_uri': self.redirect_uri,
        })
        rotated_refresh_token = payload.get('refresh_token')
        if rotated_refresh_token:
            self.connection.encrypted_refresh_token = encrypt_token(rotated_refresh_token)
            self.connection.save(update_fields=['encrypted_refresh_token', 'updated_at'])
        return payload['access_token']

    def token(self):
        if self._token:
            return self._token
        self._validate_configuration()
        if self.connection.auth_mode == 'delegated':
            self._token = self._delegated_token()
        else:
            payload = self._token_request({
                'client_id': self.client_id,
                'client_secret': self._secret(),
                'scope': 'https://graph.microsoft.com/.default',
                'grant_type': 'client_credentials',
            })
            self._token = payload['access_token']
        return self._token

    def request(self, method, path, *, params=None):
        url = path if str(path).startswith('https://') else f'{self.base_url}/{str(path).lstrip("/")}'
        response = requests.request(
            method,
            url,
            params=params,
            headers={
                'Authorization': f'Bearer {self.token()}',
                'Accept': 'application/json',
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise self._graph_error(response, 'Microsoft Graph mailbox request')
        return response.json() if response.content else {}

    def _mailbox_messages_url(self):
        if self.connection.auth_mode != 'application':
            raise SalesMailboxReadError('Select a shared mailbox to view its emails.', 400)
        if self.base_url != 'https://graph.microsoft.com/v1.0':
            raise SalesMailboxReadError('Shared mailbox access is not configured.', 503)
        mailbox = quote(self.connection.mailbox_address, safe='')
        return f'{self.base_url}/users/{mailbox}/messages'

    def _validate_mailbox_next_link(self, next_link):
        if not isinstance(next_link, str) or not next_link or len(next_link) > 16000:
            raise SalesMailboxReadError('Microsoft returned an invalid mailbox page.')
        try:
            parsed = urlsplit(next_link)
            expected = urlsplit(self._mailbox_messages_url())
            # Graph returns the same mailbox with a literal @ in continuations.
            # Allow only that exact equivalent, without decoding path separators.
            raw_at_path = '/v1.0/users/{}/messages'.format(
                quote(self.connection.mailbox_address, safe='@')
            )
            valid = (
                parsed.scheme == 'https'
                and parsed.netloc == 'graph.microsoft.com'
                and parsed.path in {expected.path, raw_at_path}
                and bool(parsed.query)
                and not parsed.fragment
                and not any(ord(character) < 32 for character in next_link)
            )
        except ValueError:
            valid = False
        if not valid:
            raise SalesMailboxReadError('Microsoft returned an invalid mailbox page.')
        return next_link

    def _mailbox_cursor_scope(self, user_id):
        return {
            'user': str(user_id),
            'connection': str(self.connection.pk),
            'mailbox': self.connection.mailbox_address,
            'tenant': self.connection.tenant_id,
            'client': self.connection.client_id,
        }

    def _read_mailbox_cursor(self, cursor, user_id):
        if not isinstance(cursor, str) or not cursor or len(cursor) > 24000:
            raise SalesMailboxReadError('The email page is invalid. Refresh the mailbox.', 400)
        try:
            payload = signing.loads(
                cursor, salt=self.MAILBOX_CURSOR_SALT,
                max_age=self.MAILBOX_CURSOR_MAX_AGE,
            )
        except signing.SignatureExpired:
            raise SalesMailboxReadError('The email page has expired. Refresh the mailbox.', 410) from None
        except (signing.BadSignature, ValueError, TypeError):
            raise SalesMailboxReadError('The email page is invalid. Refresh the mailbox.', 400) from None
        if not isinstance(payload, dict) or payload.get('scope') != self._mailbox_cursor_scope(user_id):
            raise SalesMailboxReadError('The email page is invalid. Refresh the mailbox.', 400)
        try:
            return self._validate_mailbox_next_link(payload.get('next'))
        except SalesMailboxReadError:
            raise SalesMailboxReadError('The email page is invalid. Refresh the mailbox.', 400) from None

    def _read_mailbox_json(self, url, *, params=None, body_format=None, request_timeout=None):
        """Only callers constructing/validating a scoped Graph URL may use this."""
        try:
            token = self.token()
        except SalesGraphConfigurationError:
            raise SalesMailboxReadError('Shared mailbox access is not configured.', 503) from None
        except (requests.RequestException, RuntimeError, ValueError, TypeError, AttributeError):
            raise SalesMailboxReadError('Microsoft mailbox access is temporarily unavailable.', 503) from None
        headers = {
            'Authorization': f'Bearer {token}', 'Accept': 'application/json',
            'Prefer': 'IdType="ImmutableId"',
        }
        if body_format in {'text', 'html'}:
            headers['Prefer'] += f', outlook.body-content-type="{body_format}"'
        try:
            response = requests.request(
                'GET', url, params=params, headers=headers,
                timeout=self.timeout if request_timeout is None else request_timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise SalesMailboxReadError('Microsoft mailbox access is temporarily unavailable.', 503) from None
        if response.status_code == 404:
            raise SalesMailboxReadError('The email or mailbox is no longer available.', 404)
        if response.status_code in {429, 503, 504}:
            raise SalesMailboxReadError('Microsoft mailbox access is temporarily unavailable. Try again later.', 503)
        if response.status_code in {401, 403}:
            raise SalesMailboxReadError('Microsoft did not allow access to this mailbox.')
        if response.status_code != 200:
            raise SalesMailboxReadError('The emails could not be loaded from Microsoft.')
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise SalesMailboxReadError('Microsoft returned an invalid mailbox response.') from None
        if not isinstance(payload, dict):
            raise SalesMailboxReadError('Microsoft returned an invalid mailbox response.')
        return payload

    @staticmethod
    def _email_address(value):
        if not isinstance(value, dict):
            return {'name': '', 'email': ''}
        address = value.get('emailAddress')
        if not isinstance(address, dict):
            return {'name': '', 'email': ''}
        return {
            'name': address.get('name') if isinstance(address.get('name'), str) else '',
            'email': address.get('address') if isinstance(address.get('address'), str) else '',
        }

    @classmethod
    def _message_direction(cls, message, mailbox_address):
        """Mailbox-relative presentation, without inferring aliases or delivery."""
        if message.get('isDraft') is True:
            return 'draft'

        def normalized(value):
            address = value.strip().casefold() if isinstance(value, str) else ''
            return address if re.fullmatch(r'[^@\s]+@[^@\s]+', address) else ''

        own_address = normalized(mailbox_address)
        if not own_address:
            return 'unknown'
        origins = {normalized(cls._email_address(message.get(key))['email']) for key in ('from', 'sender')}
        origins.discard('')
        if own_address in origins:
            return 'outgoing'
        if origins:
            for key in ('toRecipients', 'ccRecipients'):
                recipients = message.get(key)
                if isinstance(recipients, list) and any(
                    normalized(cls._email_address(recipient)['email']) == own_address for recipient in recipients
                ):
                    return 'incoming'
        return 'unknown'

    @classmethod
    def _message_projection(cls, message, *, mailbox_address=''):
        if not isinstance(message, dict) or not isinstance(message.get('id'), str) or not message['id']:
            raise SalesMailboxReadError('Microsoft returned an invalid mailbox response.')
        sender = cls._email_address(message.get('from') or message.get('sender'))
        result = {
            'id': message['id'],
            'subject': message.get('subject') if isinstance(message.get('subject'), str) else '',
            'sender_name': sender['name'],
            'sender_email': sender['email'],
            'received_at': message.get('receivedDateTime') if isinstance(message.get('receivedDateTime'), str) else None,
            'sent_at': message.get('sentDateTime') if isinstance(message.get('sentDateTime'), str) else None,
            'body_preview': message.get('bodyPreview') if isinstance(message.get('bodyPreview'), str) else '',
            'has_attachments': message.get('hasAttachments') if isinstance(message.get('hasAttachments'), bool) else None,
            'is_read': message.get('isRead') if isinstance(message.get('isRead'), bool) else None,
            'is_draft': message.get('isDraft') if isinstance(message.get('isDraft'), bool) else None,
            'direction': cls._message_direction(message, mailbox_address),
            'importance': message.get('importance') if isinstance(message.get('importance'), str) else '',
        }
        result.update(email_thread_role(result))
        return result

    @staticmethod
    def _thread_metadata(payload):
        """Retain only bounded reply identities; never expose arbitrary headers."""
        headers = payload.get('internetMessageHeaders')
        if headers is None:
            return None
        invalid = {'headers_available': False, 'invalid': True}
        if not isinstance(headers, list) or len(headers) > 200:
            return invalid
        metadata = {'headers_available': bool(headers), 'in_reply_to': [], 'references': []}
        total = 0
        for header in headers:
            if not isinstance(header, dict) or not isinstance(header.get('name'), str):
                return invalid
            name = header['name'].strip().casefold()
            if name not in {'in-reply-to', 'references'}:
                continue
            value = header.get('value')
            if not isinstance(value, str) or len(value) > 8192:
                return invalid
            total += len(value)
            if total > 16384:
                return invalid
            ids = re.findall(r'<[^<>\s]{1,512}>', value)
            remainder = re.sub(r'<[^<>\s]{1,512}>', '', value)
            if remainder.strip() or len(ids) > 100:
                return invalid
            key = 'in_reply_to' if name == 'in-reply-to' else 'references'
            metadata[key] = list(dict.fromkeys([*metadata[key], *ids]))
            if len(metadata[key]) > 100:
                return invalid
        return metadata

    def list_messages(self, *, user_id, cursor=None):
        """Read one mailbox-wide page without importing or changing messages."""
        url = self._mailbox_messages_url()
        params = {
            '$top': self.MAILBOX_PAGE_SIZE,
            '$select': f'{self.MESSAGE_FIELDS},toRecipients,ccRecipients',
            '$orderby': 'receivedDateTime desc',
        }
        if cursor is not None:
            url = self._read_mailbox_cursor(cursor, user_id)
            params = None
        payload = self._read_mailbox_json(url, params=params)
        records = payload.get('value')
        if not isinstance(records, list) or len(records) > self.MAILBOX_PAGE_SIZE:
            raise SalesMailboxReadError('Microsoft returned an invalid mailbox page.')
        results = [self._message_projection(record, mailbox_address=self.connection.mailbox_address) for record in records]
        next_cursor = None
        if payload.get('@odata.nextLink') is not None:
            next_link = self._validate_mailbox_next_link(payload['@odata.nextLink'])
            if next_link == url:
                raise SalesMailboxReadError('Microsoft returned an invalid mailbox page.')
            next_cursor = signing.dumps(
                {'scope': self._mailbox_cursor_scope(user_id), 'next': next_link},
                salt=self.MAILBOX_CURSOR_SALT, compress=True,
            )
        return {
            'mailbox_address': self.connection.mailbox_address,
            'results': results,
            'next_cursor': next_cursor,
        }

    @classmethod
    def _message_detail_projection(cls, payload, *, mailbox_address=''):
        result = cls._message_projection(payload, mailbox_address=mailbox_address)
        sender_address = cls._email_address(payload.get('sender'))['email'].strip().casefold()
        if (re.fullmatch(r'[^@\s]+@[^@\s]+', sender_address)
                and sender_address != result['sender_email'].strip().casefold()):
            result['_sender_address'] = sender_address
        metadata = cls._thread_metadata(payload)
        if metadata is not None:
            result['_thread_metadata'] = metadata
        result.update(email_thread_role(result))
        body = payload.get('body') or {}
        if not isinstance(body, dict) or not isinstance(body.get('content', ''), str):
            raise SalesMailboxReadError('Microsoft returned an invalid email body.')
        result.update(project_email_body(body.get('content', ''), body.get('contentType', '')))
        for source, target in (('toRecipients', 'to_recipients'), ('ccRecipients', 'cc_recipients')):
            recipients = payload.get(source) or []
            if not isinstance(recipients, list):
                raise SalesMailboxReadError('Microsoft returned invalid email recipients.')
            result[target] = [cls._email_address(recipient) for recipient in recipients]
        return result

    @staticmethod
    def _conversation_evidence(message):
        # Do not include direction, read/unread flags or other mailbox presentation.
        evidence = {key: message.get(key) for key in (
            'id', 'subject', 'sender_name', 'sender_email', 'received_at', 'sent_at',
            'body_text', 'body_content', 'to_recipients', 'cc_recipients', 'has_attachments', 'is_draft',
        )}
        # Preserve hashes for older/mocked sources without this optional data.
        # Actual newly available or changed reply evidence requires fresh review.
        if message.get('_thread_metadata') is not None:
            evidence['thread_metadata'] = message['_thread_metadata']
        if message.get('_sender_address') is not None:
            evidence['sender_address'] = message['_sender_address']
        return evidence

    def _read_conversation(self, selected, conversation_id, selected_characters):
        records = {selected['id']: selected}
        coverage = {'status': 'selected_only'}
        if (
            not isinstance(conversation_id, str) or not conversation_id
            or len(conversation_id) > 2048
            or any(ord(character) < 32 for character in conversation_id)
        ):
            coverage['reason'] = 'Conversation history was unavailable. Only this email and its quoted messages were reviewed.'
            return list(records.values()), coverage

        coverage = {'status': 'complete'}
        url = self._mailbox_messages_url()
        scope_filter = "conversationId eq '{}'".format(conversation_id.replace("'", "''"))
        params = {
            '$filter': scope_filter, '$top': self.CONVERSATION_PAGE_SIZE,
            '$select': f'{self.MESSAGE_FIELDS},{self.THREAD_FIELDS},conversationId,body,toRecipients,ccRecipients',
        }
        # Sorting locally avoids Graph's filter/orderby restrictions. Preserve
        # the full validated continuation rather than constructing skip values.
        visited, selected_seen = set(), False
        characters = selected_characters
        deadline = time.monotonic() + self.CONVERSATION_MAX_SECONDS
        limit_notice = 'Conversation review reached its size or time limit. Some messages may be missing.'
        try:
            for page in range(self.CONVERSATION_MAX_PAGES):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or characters > self.CONVERSATION_MAX_CHARACTERS:
                    coverage = {'status': 'partial', 'reason': limit_notice}
                    break
                payload = self._read_mailbox_json(
                    url, params=params, body_format='html',
                    request_timeout=max(0.1, min(self.timeout, remaining)),
                )
                if time.monotonic() >= deadline:
                    coverage = {'status': 'partial', 'reason': limit_notice}
                    break
                values = payload.get('value')
                if not isinstance(values, list) or len(values) > self.CONVERSATION_PAGE_SIZE:
                    raise SalesMailboxReadError('Invalid conversation page.')
                # Validate the whole page before allowing any of its evidence in.
                for value in values:
                    if (
                        not isinstance(value, dict) or value.get('conversationId') != conversation_id
                        or not isinstance(value.get('id'), str) or not value['id']
                    ):
                        raise SalesMailboxReadError('Invalid conversation scope.')
                    body = value.get('body') or {}
                    if not isinstance(body, dict) or not isinstance(body.get('content'), str):
                        raise SalesMailboxReadError('Invalid conversation body.')
                    if any(value.get(key) is not None and not isinstance(value[key], list) for key in ('toRecipients', 'ccRecipients')):
                        raise SalesMailboxReadError('Invalid conversation recipients.')
                for value in values:
                    size = len((value.get('body') or {}).get('content', ''))
                    if (
                        time.monotonic() >= deadline
                        or characters + size > self.CONVERSATION_MAX_CHARACTERS
                        or (value['id'] not in records and len(records) >= self.CONVERSATION_MAX_MESSAGES)
                    ):
                        coverage = {'status': 'partial', 'reason': limit_notice}
                        break
                    candidate = self._message_detail_projection(value, mailbox_address=self.connection.mailbox_address)
                    # This is a processing budget, including repeated provider
                    # records whose content still has to be checked for changes.
                    characters += size
                    if time.monotonic() >= deadline:
                        coverage = {'status': 'partial', 'reason': limit_notice}
                        break
                    if candidate['id'] in records:
                        if self._conversation_evidence(candidate) != self._conversation_evidence(records[candidate['id']]):
                            raise SalesMailboxReadError('Conversation changed while loading.')
                        selected_seen = selected_seen or candidate['id'] == selected['id']
                    else:
                        records[candidate['id']] = candidate
                if coverage['status'] == 'partial':
                    break
                next_link = payload.get('@odata.nextLink')
                if next_link is None:
                    if not selected_seen:
                        coverage = {'status': 'partial', 'reason': 'Conversation history may have changed while loading. Refresh to review it again.'}
                    break
                next_link = self._validate_mailbox_next_link(next_link)
                if parse_qs(urlsplit(next_link).query).get('$filter') != [scope_filter] or next_link in visited:
                    raise SalesMailboxReadError('Invalid conversation continuation.')
                visited.add(next_link)
                url, params = next_link, None
                if page == self.CONVERSATION_MAX_PAGES - 1:
                    coverage = {'status': 'partial', 'reason': limit_notice}
        except SalesMailboxReadError:
            # Keep the selected preview usable, but do not claim all evidence was
            # read or include any suspect/partially validated conversation pages.
            records = {selected['id']: selected}
            coverage = {'status': 'partial', 'reason': 'Conversation history could not be fully verified. Only this email and its quoted messages were reviewed; refresh to retry.'}

        def chronological(message):
            try:
                stamp = parse_datetime(message.get('sent_at') or message.get('received_at') or '')
                return (stamp.timestamp() if stamp is not None else float('inf'), message['id'])
            except (ValueError, TypeError, OverflowError):
                return (float('inf'), message['id'])

        return sorted(records.values(), key=chronological), coverage

    def get_message_for_capture(self, message_id):
        """Read one complete incoming source; never mutate Microsoft state.

        Capture is deliberately separate from the bounded conversation analysis:
        one durable source row represents one actual immutable Graph message.
        """
        url = self._mailbox_messages_url()
        if (
            not isinstance(message_id, str) or len(message_id) > 512
            or not re.fullmatch(r'[A-Za-z0-9_+=/-]+', message_id)
        ):
            raise SalesMailboxReadError('Select a valid email to save.', 400)
        payload = self._read_mailbox_json(
            f'{url}/{quote(message_id, safe="")}',
            params={'$select': f'{self.MESSAGE_FIELDS},conversationId,internetMessageId,body,toRecipients,ccRecipients'},
            body_format='html',
        )
        # Reject incomplete or oversized source instead of saving a truncated or
        # fabricated snapshot that later automation could mistake for evidence.
        body = payload.get('body')
        if (
            payload.get('id') != message_id
            or not isinstance(payload.get('subject'), str)
            or not isinstance(payload.get('isDraft'), bool)
            or not isinstance(payload.get('hasAttachments'), bool)
            or not isinstance(payload.get('conversationId'), str)
            or not payload['conversationId'].strip()
            or not isinstance(payload.get('internetMessageId', ''), str)
            or (payload.get('sentDateTime') is not None and not isinstance(payload.get('sentDateTime'), str))
            or not isinstance(body, dict)
            or body.get('contentType') not in {'text', 'html'}
            or not isinstance(body.get('content'), str)
            or len(body['content']) > MAX_HTML_CHARACTERS
        ):
            raise SalesMailboxReadError('Microsoft returned incomplete or unsupported email content. Nothing was saved.', code='unsupported_source')
        result = self._message_detail_projection(payload, mailbox_address=self.connection.mailbox_address)
        if result['direction'] != 'incoming':
            raise SalesMailboxReadError('Only confirmed incoming emails can be saved to intake.', 400, code='not_incoming')
        result['conversation_id'] = payload['conversationId']
        result['internet_message_id'] = payload.get('internetMessageId', '')
        result.pop('_thread_metadata', None)
        result.pop('_sender_address', None)
        return result

    def get_message(self, message_id, *, allow_ai=False, ai_scope_key='', review_context=None):
        url = self._mailbox_messages_url()
        if (
            not isinstance(message_id, str) or len(message_id) > 2048
            or not re.fullmatch(r'[A-Za-z0-9_+=/-]+', message_id)
        ):
            raise SalesMailboxReadError('Select a valid email to view.', 400)
        payload = self._read_mailbox_json(
            f'{url}/{quote(message_id, safe="")}',
            params={'$select': f'{self.MESSAGE_FIELDS},{self.THREAD_FIELDS},conversationId,body,toRecipients,ccRecipients'},
            body_format='html',
        )
        result = self._message_detail_projection(payload, mailbox_address=self.connection.mailbox_address)
        if result['id'] != message_id:
            raise SalesMailboxReadError('Microsoft returned a different email. Refresh and try again.')
        messages, coverage = self._read_conversation(
            result, payload.get('conversationId'), len((payload.get('body') or {}).get('content', '')),
        )
        reviewed = analyze_email_conversation(
            messages, selected_message_id=message_id, coverage=coverage,
            mailbox_address=self.connection.mailbox_address,
        )
        if review_context is not None:
            from .email_review_assistant import assistant_sources
            review_context['assistant_sources'] = assistant_sources(reviewed, messages)
        if ai_scope_key:
            from .email_ai_analysis import enhance_email_analysis
            reviewed = enhance_email_analysis(
                reviewed, messages, scope_key=ai_scope_key, allow_provider=allow_ai,
            )
        result['extracted_information'] = {
            **reviewed['extracted_information'], 'analysis': reviewed['analysis'],
        }
        selected_source = next((source for source in reviewed['analysis']['sources'] if source['is_selected']), None)
        if selected_source:
            for key in ('thread_role', 'thread_role_basis', 'thread_role_reason'):
                result[key] = selected_source[key]
        # Bind original messages and later amendments into the existing review
        # token. Reordering/provider read flags do not change the source revision.
        result['analysis_source_hash'] = hashlib.sha256(json.dumps({
            'coverage': coverage,
            'messages': [self._conversation_evidence(item) for item in sorted(messages, key=lambda item: item['id'])],
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        result.pop('_thread_metadata', None)
        result.pop('_sender_address', None)
        return result

    def health_check(self):
        """Authenticate and verify that the configured Inbox is readable."""
        try:
            mailbox = quote(self.connection.mailbox_address, safe='')
            mailbox_path = '/me' if self.connection.auth_mode == 'delegated' else f'/users/{mailbox}'
            folder = self.request(
                'GET',
                f'{mailbox_path}/mailFolders/inbox',
                params={'$select': 'id,displayName,totalItemCount,unreadItemCount'},
            )
            self.connection.last_status = 'connected'
            self.connection.last_error = ''
            self.connection.mailbox_display_name = folder.get('displayName', 'Inbox')
            self.connection.total_item_count = folder.get('totalItemCount')
            self.connection.unread_item_count = folder.get('unreadItemCount')
            result = {
                'connected': True,
                'mailbox_address': self.connection.mailbox_address,
                'folder': self.connection.mailbox_display_name,
                'total_item_count': self.connection.total_item_count,
                'unread_item_count': self.connection.unread_item_count,
            }
        except Exception as exc:  # Persist a governed, user-visible health result.
            self.connection.last_status = 'error'
            self.connection.last_error = str(exc)[:2000]
            result = {'connected': False, 'error': str(exc)}
        self.connection.last_health_check_at = timezone.now()
        self.connection.save(
            update_fields=[
                'last_status',
                'last_error',
                'mailbox_display_name',
                'total_item_count',
                'unread_item_count',
                'last_health_check_at',
                'updated_at',
            ]
        )
        return result

    def disconnect(self):
        self._token = None
        self.connection.encrypted_refresh_token = ''
        self.connection.delegated_account_id = ''
        self.connection.delegated_account_name = ''
        self.connection.delegated_scopes = []
        self.connection.connected_at = None
        self.connection.last_status = 'not_tested'
        self.connection.last_error = ''
        self.connection.total_item_count = None
        self.connection.unread_item_count = None
        self.connection.save(update_fields=[
            'encrypted_refresh_token', 'delegated_account_id',
            'delegated_account_name', 'delegated_scopes', 'connected_at',
            'last_status', 'last_error', 'total_item_count',
            'unread_item_count', 'updated_at',
        ])
