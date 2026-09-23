import hashlib
import mimetypes
import os
import re
import uuid

from django.conf import settings
from django.core.files import File
from django.core.files.storage import FileSystemStorage

from apps.core.s3_service import S3Service


class HMBStorageError(RuntimeError):
    pass


def _safe_filename(filename):
    basename = os.path.basename(filename or 'upload.bin')
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', basename).strip('._')
    return cleaned[:180] or 'upload.bin'


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def store_hmb_source(path, *, upload_kind, original_filename, project_id=None, user_id=None):
    """Store an accepted HMB source privately when S3 is enabled."""
    checksum = _sha256(path)
    if upload_kind == 'output_template' and not getattr(settings, 'USE_S3', False):
        if not settings.DEBUG:
            raise HMBStorageError('Private S3 storage must be enabled for production templates.')
        storage = FileSystemStorage(location=os.path.join(settings.BASE_DIR, 'private_hmb'))
        with open(path, 'rb') as source:
            key = storage.save(f'{project_id}/{uuid.uuid4().hex}.xlsx', File(source))
        return {'stored': True, 'backend': 'local', 'key': key, 'sha256': checksum,
                'size': os.path.getsize(path), 'content_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
    if not getattr(settings, 'USE_S3', False):
        if not settings.DEBUG:
            raise HMBStorageError('Private storage is unavailable. Enable S3 before accepting production uploads.')
        return {
            'stored': False,
            'key': '',
            'sha256': checksum,
            'size': os.path.getsize(path),
            'content_type': mimetypes.guess_type(original_filename or '')[0] or 'application/octet-stream',
        }

    folder_type = {
        'master_template': 'hmb_master_templates', 'case_file': 'hmb_case_files',
        'output_template': 'hmb_output_templates',
    }[upload_kind]
    scope = str(project_id or 'unbound')
    filename = f'{scope}/{uuid.uuid4().hex}_{_safe_filename(original_filename)}'
    content_type = mimetypes.guess_type(original_filename or '')[0] or 'application/octet-stream'
    metadata = {
        'upload-kind': str(upload_kind),
        'project-id': str(project_id or ''),
        'user-id': str(user_id or ''),
        'sha256': checksum,
    }
    try:
        result = S3Service().upload_file(
            path,
            folder_type,
            filename=filename,
            content_type=content_type,
            metadata=metadata,
        )
    except Exception as exc:
        raise HMBStorageError(f'Could not retain the uploaded file in private storage: {exc}') from exc
    if not result.get('success') or not result.get('key'):
        raise HMBStorageError(
            f"Could not retain the uploaded file in private storage: {result.get('error', 'unknown S3 error')}"
        )
    return {
        'stored': True,
        'backend': 's3',
        'key': result['key'],
        'sha256': checksum,
        'size': result.get('size', os.path.getsize(path)),
        'content_type': content_type,
    }


def read_hmb_source(source_upload):
    if source_upload.metadata.get('storage_backend') == 'local':
        storage = FileSystemStorage(location=os.path.join(settings.BASE_DIR, 'private_hmb'))
        try:
            with storage.open(source_upload.storage_key, 'rb') as source:
                content = source.read()
        except OSError as exc:
            raise HMBStorageError('Could not retrieve the saved final template.') from exc
    else:
        try:
            result = S3Service().download_file(source_upload.storage_key)
            if not result.get('success'):
                raise HMBStorageError('Could not retrieve the saved final template.')
            try:
                content = result['body'].read()
            finally:
                result['body'].close()
        except Exception as exc:
            raise HMBStorageError('Could not retrieve the saved final template.') from exc
    if hashlib.sha256(content).hexdigest() != source_upload.file_sha256:
        raise HMBStorageError('Saved final template checksum does not match its audit record.')
    return content


def delete_hmb_source(storage_key, backend='s3'):
    if backend == 'local':
        FileSystemStorage(location=os.path.join(settings.BASE_DIR, 'private_hmb')).delete(storage_key)
        return
    if not storage_key or not getattr(settings, 'USE_S3', False):
        return
    try:
        S3Service().delete_file(storage_key)
    except Exception:
        pass