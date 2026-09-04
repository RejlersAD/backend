"""Least-privilege Microsoft Graph client for HR-owned integrations."""
import io
import os
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone

from .models import HRPolicyDocument, MicrosoftGraphConnection, MicrosoftGraphUserLink, EmployeeMaster


class GraphConfigurationError(RuntimeError):
    pass


class MicrosoftGraphService:
    def __init__(self, connection):
        self.connection = connection
        self.base_url = str(getattr(settings, 'MICROSOFT_GRAPH_BASE_URL', 'https://graph.microsoft.com/v1.0')).rstrip('/')
        self.timeout = int(getattr(settings, 'MICROSOFT_GRAPH_TIMEOUT', 30))
        self._token = None

    @classmethod
    def active(cls):
        connection = MicrosoftGraphConnection.objects.filter(enabled=True).order_by('created_at').first()
        if not connection:
            raise GraphConfigurationError('Microsoft Graph is not enabled.')
        return cls(connection)

    def _secret(self):
        return os.environ.get('MICROSOFT_GRAPH_CLIENT_SECRET', '').strip()

    def token(self):
        if self._token:
            return self._token
        if not self.connection.tenant_id or not self.connection.client_id or not self._secret():
            raise GraphConfigurationError('Tenant ID, client ID, and MICROSOFT_GRAPH_CLIENT_SECRET are required.')
        response = requests.post(
            f'https://login.microsoftonline.com/{quote(self.connection.tenant_id)}/oauth2/v2.0/token',
            data={
                'client_id': self.connection.client_id,
                'client_secret': self._secret(),
                'scope': 'https://graph.microsoft.com/.default',
                'grant_type': 'client_credentials',
            }, timeout=self.timeout,
        )
        response.raise_for_status()
        self._token = response.json()['access_token']
        return self._token

    def request(self, method, path, *, params=None, json=None, raw=False):
        url = path if str(path).startswith('https://') else f'{self.base_url}/{str(path).lstrip("/")}'
        response = requests.request(method, url, params=params, json=json, headers={
            'Authorization': f'Bearer {self.token()}', 'Accept': 'application/json',
        }, timeout=self.timeout)
        response.raise_for_status()
        if raw:
            return response.content, response.headers.get('Content-Type', '')
        return response.json() if response.content else {}

    def health_check(self):
        try:
            organization = self.request('GET', '/organization', params={'$select': 'id,displayName'})
            self.connection.last_status = 'connected'
            self.connection.last_error = ''
            result = {'connected': True, 'organization': (organization.get('value') or [{}])[0]}
        except Exception as exc:
            self.connection.last_status = 'error'
            self.connection.last_error = str(exc)[:2000]
            result = {'connected': False, 'error': str(exc)}
        self.connection.last_health_check_at = timezone.now()
        self.connection.save(update_fields=['last_status', 'last_error', 'last_health_check_at', 'updated_at'])
        return result

    def iter_collection(self, path, params=None):
        payload = self.request('GET', path, params=params)
        while True:
            yield from payload.get('value', [])
            next_link = payload.get('@odata.nextLink')
            if not next_link:
                break
            payload = self.request('GET', next_link)

    def sync_entra_users(self):
        if not self.connection.entra_sync_enabled:
            raise GraphConfigurationError('Entra synchronization is disabled.')
        matched = unlinked = updated = 0
        users = self.iter_collection('/users', params={
            '$select': 'id,displayName,givenName,surname,mail,userPrincipalName,jobTitle,department,employeeId,accountEnabled',
            '$top': '999',
        })
        for profile in users:
            email = (profile.get('mail') or profile.get('userPrincipalName') or '').strip().lower()
            employee_id = (profile.get('employeeId') or '').strip()
            employee = EmployeeMaster.objects.filter(email__iexact=email).first() if email else None
            if not employee and employee_id:
                employee = EmployeeMaster.objects.filter(employee_number__iexact=employee_id).first()
            if not employee:
                unlinked += 1
                continue
            matched += 1
            _, created = MicrosoftGraphUserLink.objects.update_or_create(
                employee=employee,
                defaults={
                    'entra_object_id': profile['id'], 'user_principal_name': profile.get('userPrincipalName') or email,
                    'account_enabled': profile.get('accountEnabled', True), 'raw_profile': profile,
                    'last_synced_at': timezone.now(),
                },
            )
            if not created:
                updated += 1
        self.connection.last_sync_at = timezone.now()
        self.connection.last_status = 'connected'
        self.connection.last_error = ''
        self.connection.save(update_fields=['last_sync_at', 'last_status', 'last_error', 'updated_at'])
        return {'matched': matched, 'created': matched - updated, 'updated': updated, 'unlinked': unlinked}

    def send_mail(self, recipient, subject, body):
        if not self.connection.outlook_enabled or not self.connection.mail_sender:
            raise GraphConfigurationError('Outlook delivery and a default sender must be configured.')
        self.request('POST', f'/users/{quote(self.connection.mail_sender)}/sendMail', json={'message': {
            'subject': subject, 'body': {'contentType': 'Text', 'content': body},
            'toRecipients': [{'emailAddress': {'address': recipient}}],
        }, 'saveToSentItems': True})
        return {'sent': True}

    def send_teams_notification(self, recipient_entra_id, text, web_url=''):
        if not self.connection.teams_enabled or not self.connection.default_team_id:
            raise GraphConfigurationError('Teams activity delivery and a default team must be configured.')
        team_id = self.connection.default_team_id
        topic_url = web_url or f'https://graph.microsoft.com/v1.0/teams/{team_id}'
        self.request('POST', f'/teams/{quote(team_id)}/sendActivityNotification', json={
            'topic': {'source': 'entityUrl', 'value': topic_url},
            'activityType': 'systemDefault', 'previewText': {'content': text[:150]},
            'recipient': {'@odata.type': 'microsoft.graph.aadUserNotificationRecipient', 'userId': recipient_entra_id},
            'templateParameters': [{'name': 'actor', 'value': 'RADAI HR'}, {'name': 'reason', 'value': text[:150]}],
            **({'teamsAppId': self.connection.teams_app_id} if self.connection.teams_app_id else {}),
        })
        return {'sent': True}

    def sync_sharepoint_policies(self):
        c = self.connection
        if not c.sharepoint_enabled or not c.sharepoint_site_id or not c.sharepoint_drive_id:
            raise GraphConfigurationError('SharePoint site, drive, and policy synchronization must be configured.')
        folder = quote(c.sharepoint_policy_folder.strip('/'), safe='/')
        path = f'/sites/{quote(c.sharepoint_site_id)}/drives/{quote(c.sharepoint_drive_id)}/root:/{folder}:/children'
        created = updated = skipped = 0
        for item in self.iter_collection(path, params={'$select': 'id,name,webUrl,lastModifiedDateTime,file'}):
            if not item.get('file'):
                continue
            try:
                data, content_type = self.request('GET', f'/drives/{quote(c.sharepoint_drive_id)}/items/{quote(item["id"])}/content', raw=True)
                content = extract_document_text(item.get('name', ''), data, content_type)
            except (ValueError, ImportError):
                skipped += 1
                continue
            checksum = __import__('hashlib').sha256(data).hexdigest()
            policy, was_created = HRPolicyDocument.objects.update_or_create(
                sharepoint_item_id=item['id'],
                defaults={
                    'title': item.get('name', 'HR policy'), 'category': 'SharePoint policy',
                    'version': item.get('lastModifiedDateTime', '')[:10] or '1.0',
                    'status': 'published', 'visibility': 'employees', 'content': content,
                    'source_url': item.get('webUrl', ''), 'checksum': checksum,
                    'published_at': timezone.now(),
                },
            )
            created += int(was_created)
            updated += int(not was_created)
        c.last_sync_at = timezone.now()
        c.save(update_fields=['last_sync_at', 'updated_at'])
        return {'created': created, 'updated': updated, 'skipped': skipped}


def extract_document_text(filename, data, content_type=''):
    lower = filename.lower()
    if lower.endswith(('.txt', '.md', '.csv')) or str(content_type).startswith('text/'):
        return data.decode('utf-8-sig', errors='replace').strip()
    if lower.endswith('.pdf'):
        from pypdf import PdfReader
        return '\n\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data)).pages).strip()
    if lower.endswith('.docx'):
        from docx import Document
        return '\n'.join(p.text for p in Document(io.BytesIO(data)).paragraphs if p.text.strip()).strip()
    raise ValueError('Unsupported policy document format.')
