"""Least-privilege Microsoft Graph client for Sales mailbox connectivity."""

import os
from urllib.parse import quote, urlencode

import requests
from django.conf import settings
from django.utils import timezone

from .graph_crypto import decrypt_token, encrypt_token, is_configured as token_encryption_configured
from .models import SalesMailboxConnection


class SalesGraphConfigurationError(RuntimeError):
    """Raised when the Sales Graph connection is incomplete."""


class SalesMicrosoftGraphService:
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
