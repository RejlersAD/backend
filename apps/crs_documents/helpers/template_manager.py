"""
CRS Template Manager - Smart Template Fetching
Handles CRS template access with AWS S3 fallback
Uses soft-coding approach - no core logic modification
"""

import os
import requests
from io import BytesIO
from django.conf import settings
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# AWS S3 Template URL
DEFAULT_TEMPLATE_URL = "https://user-management-rejlers.s3.ap-south-1.amazonaws.com/CRS+template.xlsx"

# Local template paths (in priority order)
LOCAL_TEMPLATE_PATHS = [
    os.path.join(settings.BASE_DIR, 'CRS template.xlsx'),  # Root backend folder
    os.path.join(settings.BASE_DIR, 'apps', 'crs_documents', 'helpers', 'CRS template.xlsx'),
    os.path.join(settings.BASE_DIR, 'test', 'crs', 'CRS template.xlsx'),
    os.path.join(settings.BASE_DIR, 'media', 'templates', 'CRS template.xlsx'),
]


def get_crs_template() -> BytesIO:
    """
    Smart template fetcher with multiple fallbacks
    
    Priority:
    1. Local template files (multiple locations checked)
    2. Download from AWS S3 (if accessible)
    3. Cache downloaded template for future use
    
    Returns:
        BytesIO: Template file buffer
    
    Raises:
        FileNotFoundError: If template cannot be found or downloaded
    """
    
    # Strategy 1: Check local paths
    for template_path in LOCAL_TEMPLATE_PATHS:
        if os.path.exists(template_path):
            logger.info(f"✅ Found local CRS template: {template_path}")
            try:
                with open(template_path, 'rb') as f:
                    template_buffer = BytesIO(f.read())
                    logger.info(f"✅ Loaded template successfully ({len(template_buffer.getvalue())} bytes)")
                    return template_buffer
            except Exception as e:
                logger.warning(f"⚠️ Failed to read local template {template_path}: {e}")
                continue
    
    # Strategy 2: Download from AWS S3
    logger.info(f"⬇️ No local template found, attempting download from AWS S3...")
    try:
        template_buffer = download_template_from_s3(DEFAULT_TEMPLATE_URL)
        
        # Cache the downloaded template for future use
        cache_template_locally(template_buffer)
        
        logger.info(f"✅ Downloaded and cached template from AWS S3")
        return template_buffer
        
    except Exception as e:
        logger.error(f"❌ Failed to download template from S3: {e}")
    
    # Strategy 3: Raise error if all strategies fail
    raise FileNotFoundError(
        "CRS template not found. Please ensure:\n"
        "1. Local template exists in one of these paths:\n"
        f"   {', '.join(LOCAL_TEMPLATE_PATHS)}\n"
        "2. Or AWS S3 template is accessible at:\n"
        f"   {DEFAULT_TEMPLATE_URL}"
    )


def download_template_from_s3(url: str, timeout: int = 30) -> BytesIO:
    """
    Download CRS template from AWS S3
    
    Args:
        url: S3 URL of the template
        timeout: Request timeout in seconds
    
    Returns:
        BytesIO: Downloaded template buffer
    
    Raises:
        requests.RequestException: If download fails
    """
    logger.info(f"Downloading template from: {url}")
    
    response = requests.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    
    # Verify it's an Excel file
    content_type = response.headers.get('Content-Type', '')
    if 'spreadsheet' not in content_type and 'excel' not in content_type:
        logger.warning(f"⚠️ Unexpected content type: {content_type}")
    
    template_buffer = BytesIO(response.content)
    logger.info(f"Downloaded {len(response.content)} bytes")
    
    return template_buffer


def cache_template_locally(template_buffer: BytesIO) -> bool:
    """
    Cache downloaded template locally for future use
    
    Args:
        template_buffer: Template file buffer
    
    Returns:
        bool: True if cached successfully, False otherwise
    """
    try:
        # Use first writable location as cache
        cache_path = LOCAL_TEMPLATE_PATHS[0]
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        
        # Write template to cache
        template_buffer.seek(0)
        with open(cache_path, 'wb') as f:
            f.write(template_buffer.read())
        
        logger.info(f"✅ Cached template to: {cache_path}")
        return True
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to cache template: {e}")
        return False


def get_template_info() -> dict:
    """
    Get information about available templates
    
    Returns:
        dict: Template availability information
    """
    info = {
        'local_templates': [],
        's3_url': DEFAULT_TEMPLATE_URL,
        's3_accessible': False,
        'recommended_action': None
    }
    
    # Check local templates
    for path in LOCAL_TEMPLATE_PATHS:
        if os.path.exists(path):
            size = os.path.getsize(path)
            info['local_templates'].append({
                'path': path,
                'size': size,
                'exists': True
            })
    
    # Check S3 accessibility (quick HEAD request)
    try:
        response = requests.head(DEFAULT_TEMPLATE_URL, timeout=5)
        info['s3_accessible'] = response.status_code == 200
    except:
        info['s3_accessible'] = False
    
    # Provide recommendation
    if info['local_templates']:
        info['recommended_action'] = 'Using local template'
    elif info['s3_accessible']:
        info['recommended_action'] = 'Will download from S3 on first use'
    else:
        info['recommended_action'] = 'Please upload template manually'
    
    return info


# Expose main function
__all__ = ['get_crs_template', 'get_template_info']
