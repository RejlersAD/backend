"""Replica bytes are private, including when development uses local storage."""
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages


def replica_storage():
    alias = getattr(settings, 'FILE_REPLICA_STORAGE_ALIAS', '')
    if alias:
        return storages[alias]
    if getattr(settings, 'USE_S3', False):
        from storages.backends.s3boto3 import S3Boto3Storage
        return S3Boto3Storage(
            location='private/file-replica', default_acl='private',
            file_overwrite=False, querystring_auth=True, custom_domain=None,
        )
    return FileSystemStorage(location=getattr(
        settings, 'FILE_REPLICA_ROOT', Path(settings.BASE_DIR) / 'private' / 'file-replica',
    ))


def version_path(instance, filename):
    # No user-provided paths or names enter storage keys.
    return f'{instance.entry.source_id}/{instance.entry_id}/{instance.id}/content'
