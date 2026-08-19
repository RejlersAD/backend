"""
Consistency helpers — file hash + cache deduplication
"""
import hashlib

from apps.pfd_quality.models import PFDQDocument


def compute_file_hash(file_obj) -> str:
    """Return SHA-256 hex digest without loading entire file into memory at once."""
    sha256 = hashlib.sha256()
    file_obj.seek(0)
    for chunk in iter(lambda: file_obj.read(65536), b''):
        sha256.update(chunk)
    file_obj.seek(0)
    return sha256.hexdigest()


def check_cache(file_hash: str):
    """
    Return the first completed PFDQDocument with matching hash, or None.
    Only reuses documents that completed successfully.
    """
    return (
        PFDQDocument.objects.filter(
            file_hash=file_hash,
            status=PFDQDocument.Status.COMPLETED,
        )
        .order_by('created_at')
        .first()
    )
