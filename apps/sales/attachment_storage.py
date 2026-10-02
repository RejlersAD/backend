"""Private opportunity objects. Never expose a storage URL or use public media."""
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage


def attachment_storage():
    # Reuse the application's established private artifact storage boundary.
    if isinstance(default_storage, FileSystemStorage):
        root = Path(getattr(settings, 'SALES_ATTACHMENT_ROOT', '') or Path(settings.BASE_DIR) / 'private' / 'sales-attachments').resolve()
        public_root = Path(settings.MEDIA_ROOT).resolve()
        if root.is_relative_to(public_root) or public_root.is_relative_to(root):
            raise OSError('Private attachment storage overlaps public media.')
        storage = FileSystemStorage(location=root)
        identity = ['filesystem', str(root)]
    else:
        if (getattr(default_storage, 'default_acl', None) != 'private'
                or not getattr(default_storage, 'querystring_auth', False)
                or getattr(default_storage, 'object_parameters', {}).get('ACL', 'private') != 'private'):
            raise OSError('Private attachment storage is unavailable.')
        storage = default_storage
        identity = [storage.__class__.__module__, storage.__class__.__name__,
                    getattr(storage, 'bucket_name', ''), getattr(storage, 'location', ''),
                    getattr(storage, 'endpoint_url', '')]
    return storage, hashlib.sha256(json.dumps(identity).encode()).hexdigest()
