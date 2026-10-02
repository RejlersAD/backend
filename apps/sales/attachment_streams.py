"""Disk-backed upload preparation. Original bytes define document identity."""
from contextlib import contextmanager
import gzip
import hashlib
from tempfile import TemporaryFile

from django.conf import settings
from rest_framework.exceptions import ValidationError

from .workspace_graph import valid_name


CHUNK_BYTES = 64 * 1024


def configured_limit(name):
    value = int(getattr(settings, name, 0) or 0)
    return value if value > 0 else None


def stream_digest(source):
    digest, length = hashlib.sha256(), 0
    source.seek(0)
    while True:
        chunk = source.read(CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
        length += len(chunk)
    source.seek(0)
    return length, digest.hexdigest()


@contextmanager
def prepare_upload(uploaded, *, compress=False):
    limit = configured_limit('SALES_WORKSPACE_MAX_UPLOAD_BYTES')
    if (not uploaded or not valid_name(uploaded.name) or type(uploaded.size) is not int
            or uploaded.size <= 0 or (limit is not None and uploaded.size > limit)):
        suffix = f', up to {limit} bytes' if limit is not None else ''
        raise ValidationError({'file': f'Choose a nonempty file with a valid filename{suffix}.'})
    with TemporaryFile(mode='w+b') as original, TemporaryFile(mode='w+b') as encoded:
        digest, length = hashlib.sha256(), 0
        # gzip has no source filename or wall-clock timestamp, so retries produce
        # the same representation. Neither branch loads the whole file into RAM.
        compressor = gzip.GzipFile(filename='', mode='wb', fileobj=encoded, mtime=0, compresslevel=6) if compress else None
        try:
            while True:
                chunk = uploaded.read(CHUNK_BYTES)
                if not chunk:
                    break
                length += len(chunk)
                if length > uploaded.size or (limit is not None and length > limit):
                    raise ValidationError({'file': 'The file size is invalid.'})
                digest.update(chunk)
                original.write(chunk)
                if compressor:
                    compressor.write(chunk)
        finally:
            if compressor:
                compressor.close()
        if length != uploaded.size:
            raise ValidationError({'file': 'The file size is invalid.'})
        original.seek(0)
        encoding = 'gzip' if compress and encoded.tell() < length else 'identity'
        stored = encoded if encoding == 'gzip' else original
        stored_size, stored_hash = stream_digest(stored)
        yield {'original': original, 'stored': stored, 'size': length, 'sha256': digest.hexdigest(),
               'storage_encoding': encoding, 'stored_size': stored_size, 'stored_sha256': stored_hash}
