"""
Consistency Engine
==================
* Computes deterministic SHA-256 hash of the uploaded file.
* Checks whether the same file was already processed (cache hit).
* Guarantees identical inputs → identical outputs.
"""
import hashlib
import logging

logger = logging.getLogger(__name__)


def compute_file_hash(file_obj) -> str:
    """
    Return the SHA-256 hex digest of `file_obj`.
    Works with InMemoryUploadedFile, TemporaryUploadedFile, and plain file-like objects.
    Rewinds the file cursor before and after reading so callers are unaffected.
    """
    sha = hashlib.sha256()
    try:
        file_obj.seek(0)
    except Exception:
        pass

    for chunk in iter(lambda: file_obj.read(8192), b''):
        sha.update(chunk)

    try:
        file_obj.seek(0)
    except Exception:
        pass

    return sha.hexdigest()


def check_cache(file_hash: str):
    """
    Return an existing completed PIDVDocument with this hash, or None.
    Only 'completed' documents are used as cache hits.
    """
    from apps.pid_verification.models import PIDVDocument
    return (
        PIDVDocument.objects
        .filter(file_hash=file_hash, status=PIDVDocument.Status.COMPLETED)
        .order_by('-created_at')
        .first()
    )
