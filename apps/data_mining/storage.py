"""Data Mining artifacts must never enter the anonymously served media tree."""
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage


def master_storage():
    # Inspect the actual backend: a configured S3 class can fall back to local.
    if isinstance(default_storage, FileSystemStorage):
        root = (Path(settings.BASE_DIR) / 'private' / 'data-mining').resolve()
        public_root = Path(settings.MEDIA_ROOT).resolve()
        if root.is_relative_to(public_root):
            raise OSError('Private artifact storage overlaps public media.')
        return FileSystemStorage(location=root)
    # Keep the configured S3 backend only when it declares private objects.
    # Unknown/public adapters fail closed rather than publishing source data.
    if (
        getattr(default_storage, 'default_acl', None) != 'private'
        or not getattr(default_storage, 'querystring_auth', False)
        or getattr(default_storage, 'object_parameters', {}).get('ACL', 'private') != 'private'
    ):
        raise OSError('Private artifact storage is unavailable.')
    return default_storage
