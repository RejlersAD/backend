"""Shared S3 inventory for the AWS dashboard card and Admin Console."""
import logging
from collections import Counter
from time import perf_counter

from django.core.cache import cache
from django.utils import timezone

from .s3_service import get_s3_service

logger = logging.getLogger(__name__)
INVENTORY_TTL_SECONDS = 300


def get_admin_s3_snapshot():
    """Read the configured production bucket once, caching only aggregate data."""
    key = None
    try:
        service = get_s3_service()
        key = f'admin-s3-inventory:v1:{service.region}:{service.bucket_name}'
        try:
            cached = cache.get(key)
        except Exception:
            cached = None
        if cached is not None:
            return cached

        start = perf_counter()
        total_bytes = total_files = 0
        extensions = Counter()
        paginator = service.s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=service.bucket_name):
            for obj in page.get('Contents', []):
                total_files += 1
                total_bytes += obj['Size']
                filename = obj['Key'].rsplit('/', 1)[-1]
                if '.' in filename:
                    extensions[filename.rsplit('.', 1)[-1].lower()] += 1
        total_extensions = sum(extensions.values()) or 1
        result = {
            'status': 'connected',
            'total_files': total_files,
            'total_size_gb': round(total_bytes / 1024 ** 3, 2),
            'file_breakdown': [
                {'type': ext.upper(), 'count': count, 'percentage': round(count / total_extensions * 100, 1)}
                for ext, count in extensions.most_common(5)
            ],
            'bucket': service.bucket_name,
            'region': service.region,
            'checked_at': timezone.now().isoformat(),
            'inventory_duration_ms': round((perf_counter() - start) * 1000, 2),
        }
    except Exception:
        logger.warning('S3 inventory unavailable', exc_info=True)
        result = {'status': 'offline', 'message': 'Storage service unavailable',
                  'checked_at': timezone.now().isoformat()}
    if key:
        try:
            cache.set(key, result, INVENTORY_TTL_SECONDS if result['status'] == 'connected' else 30)
        except Exception:
            logger.debug('S3 inventory cache unavailable')
    return result
