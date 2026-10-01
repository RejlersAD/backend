"""Small, bounded Graph transport. Never accepts a client-provided remote URL."""
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import re
from tempfile import SpooledTemporaryFile
from time import monotonic
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID

from django.conf import settings
import requests


class WorkspaceError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def valid_name(value, max_length=200):
    return bool(isinstance(value, str) and 0 < len(value) <= max_length and value == value.strip()
                and value not in {'.', '..'} and not value.endswith('.')
                and not re.search(r'[\x00-\x1f\x7f"*:<>?/\\|]', value))


@dataclass(frozen=True)
class WorkspaceConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    hostname: str
    drive_id: str
    root_item_id: str
    root_path: str

    @property
    def fingerprint(self):
        # Rotation changes credentials, not destination or application authority.
        values = (self.tenant_id, self.client_id, self.hostname, self.drive_id,
                  self.root_item_id, self.root_path)
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def workspace_config():
    if not getattr(settings, 'SALES_WORKSPACE_ENABLED', False):
        return None
    values = {field: str(getattr(settings, f'SALES_WORKSPACE_{field.upper()}', '')).strip()
              for field in WorkspaceConfig.__dataclass_fields__}
    try:
        UUID(values['tenant_id'])
        UUID(values['client_id'])
    except (ValueError, TypeError):
        return None
    if (not all(values.values())
            or not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.sharepoint\.com', values['hostname'])
            or not values['root_path'].startswith('/sites/')
            or any(not valid_name(part) for part in values['root_path'].split('/')[1:])
            or values['root_path'].split('/')[-1] != 'Opportunities'
            or any('/' in values[key] or len(values[key]) > 255 for key in ('drive_id', 'root_item_id'))):
        return None
    return WorkspaceConfig(**values)


class WorkspaceGraph:
    graph_root = 'https://graph.microsoft.com/v1.0'
    max_response_bytes = 2 * 1024 * 1024
    item_fields = ('id,name,webUrl,folder,file,remoteItem,parentReference,size,lastModifiedDateTime,'
                   'createdDateTime,createdBy,lastModifiedBy,publication,eTag')

    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.token = None

    def _authorization(self):
        if self.token is None:
            token = self._json('POST', f'https://login.microsoftonline.com/{self.config.tenant_id}/oauth2/v2.0/token',
                               authenticated=False, data={
                                   'client_id': self.config.client_id, 'client_secret': self.config.client_secret,
                                   'grant_type': 'client_credentials', 'scope': 'https://graph.microsoft.com/.default',
                               })
            self.token = token.get('access_token')
            if not isinstance(self.token, str) or not self.token:
                raise WorkspaceError('authentication_failed')
        return {'Authorization': f'Bearer {self.token}'}

    def _json(self, method, url, *, authenticated=True, **kwargs):
        headers = dict(kwargs.pop('headers', {}))
        if authenticated:
            headers.update(self._authorization())
        try:
            started = monotonic()
            with self.session.request(method, url, headers=headers, timeout=(5, 25),
                                      allow_redirects=False, stream=True, **kwargs) as response:
                if response.status_code == 404:
                    raise WorkspaceError('remote_missing')
                if response.status_code == 409:
                    raise WorkspaceError('name_conflict')
                if response.status_code in (401, 403):
                    raise WorkspaceError('remote_access_denied')
                if response.status_code == 429:
                    raise WorkspaceError('remote_busy')
                if not 200 <= response.status_code < 300:
                    raise WorkspaceError('remote_unavailable')
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes or monotonic() - started > 35:
                        raise WorkspaceError('invalid_remote_response')
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise WorkspaceError('invalid_remote_response')
                return result
        except (requests.RequestException, ValueError):
            raise WorkspaceError('remote_unavailable') from None

    def path(self, item_id):
        return f'/drives/{quote(self.config.drive_id, safe="")}/items/{quote(item_id, safe="")}'

    def safe_web_url(self, value):
        if not isinstance(value, str) or len(value) > 2000:
            raise WorkspaceError('invalid_remote_response')
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise WorkspaceError('invalid_remote_response') from None
        decoded = unquote(parsed.path)
        if (parsed.scheme != 'https' or parsed.hostname != self.config.hostname
                or parsed.username or parsed.password or port not in (None, 443)
                or '\\' in decoded or any(part in ('.', '..') for part in decoded.split('/'))
                or not (decoded == self.config.root_path or decoded.startswith(self.config.root_path + '/'))):
            raise WorkspaceError('remote_scope_changed')
        return value

    def item(self, item_id):
        item = self._json('GET', self.graph_root + self.path(item_id), params={'$select': self.item_fields})
        if item.get('id') != item_id:
            raise WorkspaceError('remote_scope_changed')
        return item

    def validate_item(self, item, *, name=None, parent_id=None, folder=True):
        if (not isinstance(item, dict) or not isinstance(item.get('parentReference'), dict)
                or item.get('remoteItem') is not None
                or not isinstance(item.get('folder' if folder else 'file'), dict)
                or not isinstance(item.get('id'), str) or not item['id'] or len(item['id']) > 255
                or not valid_name(item.get('name'), max_length=400)
                or (name is not None and item.get('name') != name)
                or (parent_id is not None and item.get('parentReference', {}).get('id') != parent_id)
                or item.get('parentReference', {}).get('driveId') != self.config.drive_id):
            raise WorkspaceError('remote_scope_changed')
        self.safe_web_url(item.get('webUrl'))
        return item

    def verify_root(self):
        item = self.validate_item(self.item(self.config.root_item_id), name='Opportunities')
        if unquote(urlsplit(item['webUrl']).path) != self.config.root_path:
            raise WorkspaceError('remote_scope_changed')
        return item

    def create_folder(self, parent_id, name):
        if not valid_name(name):
            raise WorkspaceError('invalid_folder_name')
        item = self._json('POST', self.graph_root + self.path(parent_id) + '/children', json={
            'name': name, 'folder': {}, '@microsoft.graph.conflictBehavior': 'fail',
        })
        return self.validate_item(item, name=name, parent_id=parent_id)

    def _collection(self, item_id, relationship, next_link=None, fields=None):
        expected = self.graph_root + self.path(item_id) + '/' + relationship
        if next_link:
            try:
                parsed, base = urlsplit(next_link), urlsplit(expected)
            except (ValueError, TypeError):
                raise WorkspaceError('invalid_cursor') from None
            if (parsed.scheme != 'https' or parsed.netloc != base.netloc or parsed.path != base.path or parsed.fragment
                    or len(next_link) > 8192):
                raise WorkspaceError('invalid_cursor')
        data = self._json('GET', next_link or expected,
                          **({} if next_link else {'params': {'$top': 100, '$select': fields or self.item_fields}}))
        if (not isinstance(data.get('value'), list) or len(data['value']) > 100
                or any(not isinstance(item, dict) for item in data['value'])
                or (data.get('@odata.nextLink') is not None and not isinstance(data['@odata.nextLink'], str))):
            raise WorkspaceError('invalid_remote_response')
        return data

    def children(self, parent_id, next_link=None):
        return self._collection(parent_id, 'children', next_link)

    def versions(self, item_id, next_link=None):
        return self._collection(item_id, 'versions', next_link,
                                fields='id,lastModifiedDateTime,lastModifiedBy,size,publication')

    def _download_url(self, value):
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except (ValueError, TypeError):
            raise WorkspaceError('invalid_remote_response') from None
        if (not isinstance(value, str) or len(value) > 16384
                or parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
                or port not in (None, 443) or '\\' in value or re.search(r'[\s\x00-\x1f\x7f]', value)
                or not (parsed.hostname == self.config.hostname
                        or bool(re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.files\.1drv\.com', parsed.hostname or '')))):
            raise WorkspaceError('invalid_remote_response')
        return value

    def download(self, item_id, expected_size, max_bytes):
        """Fully validate a bounded spool before exposing any bytes to the caller."""
        output = SpooledTemporaryFile(max_size=1024 * 1024, mode='w+b')

        def receive(response):
            if response.status_code in (401, 403):
                raise WorkspaceError('remote_access_denied')
            if response.status_code == 404:
                raise WorkspaceError('remote_missing')
            if response.status_code != 200:
                raise WorkspaceError('remote_unavailable')
            raw_length = response.headers.get('Content-Length')
            if raw_length is not None:
                if not str(raw_length).isdigit() or int(raw_length) != expected_size:
                    raise WorkspaceError('document_changed')
            received = 0
            for chunk in response.iter_content(65536):
                received += len(chunk)
                if (max_bytes is not None and received > max_bytes) or received > expected_size:
                    raise WorkspaceError('invalid_remote_response')
                output.write(chunk)
            if received != expected_size:
                raise WorkspaceError('document_changed')

        try:
            if (not isinstance(expected_size, int) or isinstance(expected_size, bool)
                    or expected_size < 0 or (max_bytes is not None and expected_size > max_bytes)):
                raise WorkspaceError('download_too_large')
            with self.session.request('GET', self.graph_root + self.path(item_id) + '/content',
                                      headers=self._authorization(), allow_redirects=False,
                                      stream=True, timeout=(5, 25)) as response:
                if response.status_code == 302:
                    destination = self._download_url(response.headers.get('Location'))
                else:
                    receive(response)
                    destination = None
            if destination:
                # Signed URLs are consumed immediately, never returned or logged.
                # Do not forward the Graph bearer or follow subsequent redirects.
                with self.session.request('GET', destination, headers={'Accept-Encoding': 'identity'},
                                          allow_redirects=False, stream=True, timeout=(5, 25)) as response:
                    receive(response)
            output.seek(0)
            return output
        except Exception as exc:
            output.close()
            if isinstance(exc, WorkspaceError):
                raise
            if isinstance(exc, (requests.RequestException, ValueError, OSError)):
                raise WorkspaceError('remote_unavailable') from None
            raise

    def upload(self, parent_id, name, content, *, size=None, before_chunk=None):
        if isinstance(content, bytes):
            size, content = len(content), BytesIO(content)
        if type(size) is not int or size <= 0:
            raise WorkspaceError('invalid_remote_response')
        if before_chunk:
            before_chunk()
        endpoint = self.graph_root + self.path(parent_id) + ':/' + quote(name, safe='') + ':/createUploadSession'
        session = self._json('POST', endpoint, json={'item': {
            '@microsoft.graph.conflictBehavior': 'fail', 'name': name,
        }})
        upload_url = session.get('uploadUrl', '')
        try:
            parsed = urlsplit(upload_url)
            port = parsed.port
        except (ValueError, TypeError):
            raise WorkspaceError('invalid_remote_response') from None
        if (not isinstance(upload_url, str) or len(upload_url) > 16384
                or parsed.scheme != 'https' or parsed.username or parsed.password or port not in (None, 443)
                or not (parsed.hostname == self.config.hostname
                        or bool(re.fullmatch(r'[a-z0-9.-]+\.up\.1drv\.com', parsed.hostname or '')))):
            raise WorkspaceError('invalid_remote_response')
        # 5 MiB is a multiple of Graph's 320 KiB alignment and below its 60 MiB
        # per-fragment ceiling. Only this fixed fragment enters memory.
        offset, started = 0, False
        try:
            while offset < size:
                if before_chunk:
                    before_chunk()
                chunk = content.read(min(5 * 1024 * 1024, size - offset))
                if not chunk or len(chunk) != min(5 * 1024 * 1024, size - offset):
                    raise WorkspaceError('invalid_remote_response')
                started = True
                result = self._json('PUT', upload_url, authenticated=False, data=chunk, headers={
                    'Content-Length': str(len(chunk)), 'Content-Range': f'bytes {offset}-{offset + len(chunk) - 1}/{size}',
                    'Content-Type': 'application/octet-stream',
                })
                offset += len(chunk)
                if offset < size and result.get('nextExpectedRanges') != [f'{offset}-']:
                    raise WorkspaceError('invalid_remote_response')
            item = self.validate_item(result, name=name, parent_id=parent_id, folder=False)
            if type(item.get('size')) is not int or item['size'] != size:
                raise WorkspaceError('invalid_remote_response')
            return item
        except Exception as exc:
            # Any attempted fragment can have committed remotely, including a
            # lost final response. Never turn that into a blind fresh upload.
            exc.upload_started = started
            raise


def file_projection(graph, item):
    graph.safe_web_url(item.get('webUrl'))
    publication = item.get('publication') if isinstance(item.get('publication'), dict) else {}
    file_data = item.get('file') if isinstance(item.get('file'), dict) else {}
    size = item.get('size')
    return {'id': item['id'], 'name': item['name'], 'storage_provider': 'sharepoint',
            'size': size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
            'modified_at': source_text(item.get('lastModifiedDateTime')), 'web_url': item['webUrl'],
            'mime_type': source_text(file_data.get('mimeType')),
            'version': source_text(publication.get('versionId')),
            'publication_level': publication_level(publication),
            'created_at': source_text(item.get('createdDateTime')),
            'created_by': identity_name(item.get('createdBy')),
            'modified_by': identity_name(item.get('lastModifiedBy'))}


def source_text(value):
    return value if isinstance(value, str) and 0 < len(value) <= 500 and not re.search(r'[\x00-\x1f\x7f]', value) else None


def identity_name(value):
    if not isinstance(value, dict):
        return None
    for key in ('user', 'application'):
        identity = value.get(key)
        if isinstance(identity, dict) and source_text(identity.get('displayName')):
            return identity['displayName']
    return None


def publication_level(publication):
    return publication.get('level') if publication.get('level') in ('published', 'checkout') else None


def version_projection(item, current_version):
    version_id = source_text(item.get('id'))
    if not version_id:
        raise WorkspaceError('invalid_remote_response')
    publication = item.get('publication') if isinstance(item.get('publication'), dict) else {}
    size = item.get('size')
    return {'id': version_id, 'modified_at': source_text(item.get('lastModifiedDateTime')),
            'modified_by': identity_name(item.get('lastModifiedBy')),
            'size': size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
            'publication_level': publication_level(publication),
            'is_current': version_id == current_version if current_version else None}
