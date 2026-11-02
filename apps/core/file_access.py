"""
Storage-agnostic local access to FileField contents.

`FileField.path` raises NotImplementedError under remote storage (S3), so
callers that need a real filesystem path (PyMuPDF, pdfplumber, pandas …)
must go through here instead:

    from apps.core.file_access import local_file_path, read_file_bytes

    path  = local_file_path(doc.original_file)   # local fast path, else
                                                 # streams to a temp file
    data  = read_file_bytes(doc.original_file)   # bytes, no path at all

Temp files returned by local_file_path are the caller's responsibility to
delete (they persist by design — callers in tasks.py reuse them across a
run and clean up in a finally block).
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)


def read_file_bytes(field_file) -> bytes:
    """Read the whole file through the storage backend — works for S3 and
    local storage alike, never touches FileField.path."""
    with field_file.open('rb') as fh:
        return fh.read()


def local_file_path(field_file) -> str:
    """Return a local filesystem path for a FileField.

    1. Local storage  →  the field's own .path (no copy).
    2. Remote storage →  stream through the field's storage backend to a
       temp file (the storage instance resolves any `location` prefix).
    Raises ValueError when the field has no usable source.
    """
    if not field_file:
        raise ValueError('No file attached')
    try:
        path = field_file.path
        if path and os.path.exists(path):
            return path
    except (NotImplementedError, AttributeError):
        pass  # remote storage (e.g. S3Boto3Storage) has no local path

    name = getattr(field_file, 'name', None)
    if not name:
        raise ValueError('File field has neither a local path nor a stored name')
    ext = name.rsplit('.', 1)[-1] if '.' in name else 'bin'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
    try:
        with field_file.storage.open(name, 'rb') as src:
            shutil.copyfileobj(src, tmp)
    finally:
        tmp.flush()
        tmp.close()
    logger.info('[FileAccess] Downloaded %s to temp file %s', name, tmp.name)
    return tmp.name
