"""
Legend Sheet S3 Extraction Cache
=================================
Hash-based cache for legend sheet extractions.

When a legend sheet is uploaded, its SHA-256 is used to look up a cached
extraction in S3 before invoking the full AI pipeline.  This avoids
re-running expensive multi-page OCR when the same file is re-uploaded
(e.g. a user uploads the same legend to a different project).

Cache objects are stored at:
  s3://<bucket>/pid_verification/legend_cache/v1/<file_hash>/extracted_data.json

Soft-coded constants (edit here to change behaviour, never inline):
  CACHE_KEY_VERSION   — bump to invalidate ALL existing cached extractions
  S3_CACHE_PREFIX     — top-level S3 key prefix for all cache entries
  CACHE_CONTENT_TYPE  — MIME type written when storing the JSON object
"""
import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded constants
# ---------------------------------------------------------------------------

S3_CACHE_PREFIX    = 'pid_verification/legend_cache'
CACHE_KEY_VERSION  = 'v2'          # bump this string to invalidate all caches
CACHE_CONTENT_TYPE = 'application/json'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s3_client():
    """Return (boto3 S3 client, bucket_name).  Bucket may be empty string."""
    import boto3
    region = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
    return boto3.client('s3', region_name=region), os.environ.get('AWS_STORAGE_BUCKET_NAME', '')


def get_cache_s3_key(file_hash: str) -> str:
    """Return the canonical S3 key for this file hash's cached extraction."""
    return f'{S3_CACHE_PREFIX}/{CACHE_KEY_VERSION}/{file_hash}/extracted_data.json'


def compute_file_hash(file_path: str) -> str:
    """Compute SHA-256 hex digest of a file on disk."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup_s3_cache(file_hash: str) -> dict | None:
    """
    Try to load a previously cached extraction for this file hash from S3.

    Returns:
        dict  — parsed extraction JSON on cache HIT
        None  — on cache MISS, S3 unavailable, or boto3 not installed
    """
    try:
        s3, bucket = _s3_client()
        if not bucket:
            logger.debug('[LegendCache] AWS_STORAGE_BUCKET_NAME not set — cache disabled')
            return None

        s3_key   = get_cache_s3_key(file_hash)
        response = s3.get_object(Bucket=bucket, Key=s3_key)
        data     = json.loads(response['Body'].read().decode('utf-8'))
        logger.info('[LegendCache] Cache HIT  hash=%.16s  key=%s', file_hash, s3_key)
        return data

    except Exception as exc:
        exc_name = type(exc).__name__
        # NoSuchKey is the expected "miss" case — log at INFO; anything else at DEBUG
        if 'NoSuchKey' in exc_name or 'NoSuchKey' in str(exc):
            logger.info('[LegendCache] Cache MISS  hash=%.16s', file_hash)
        else:
            logger.debug('[LegendCache] Cache lookup skipped: %s', exc)
        return None


def write_s3_cache(file_hash: str, extracted_data: dict) -> None:
    """
    Write extracted legend data to the S3 cache keyed by SHA-256 of the file.

    Non-fatal: exceptions are logged at DEBUG and swallowed so a cache write
    failure never aborts an extraction task.
    """
    try:
        s3, bucket = _s3_client()
        if not bucket:
            return

        s3_key = get_cache_s3_key(file_hash)
        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=json.dumps(extracted_data, indent=2, ensure_ascii=False).encode('utf-8'),
            ContentType=CACHE_CONTENT_TYPE,
        )
        logger.info('[LegendCache] Cache WRITE  hash=%.16s  key=%s', file_hash, s3_key)

    except Exception as exc:
        logger.debug('[LegendCache] Cache write failed (non-fatal): %s', exc)
