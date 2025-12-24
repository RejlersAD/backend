"""
CRS Template Manager - Smart Template Fetching
Handles CRS template access with AWS S3 integration
Uses soft-coding approach - no core logic modification
Enhanced with S3Service for proper bucket access
"""

import os
import requests
from io import BytesIO
from django.conf import settings
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# S3 Template Configuration
S3_TEMPLATE_KEY = "CRS template.xlsx"  # Template in bucket root
S3_TEMPLATE_FALLBACK_KEYS = [
    "CRS template.xlsx",
    "templates/CRS template.xlsx",
    "media/templates/CRS template.xlsx",
]

# AWS S3 Template URL (legacy fallback)
DEFAULT_TEMPLATE_URL = "https://user-management-rejlers.s3.us-east-1.amazonaws.com/CRS+template.xlsx"

# Local template paths (in priority order)
LOCAL_TEMPLATE_PATHS = [
    os.path.join(settings.BASE_DIR, 'CRS template.xlsx'),  # Root backend folder
    os.path.join(settings.BASE_DIR, 'apps', 'crs_documents', 'helpers', 'CRS template.xlsx'),
    os.path.join(settings.BASE_DIR, 'test', 'crs', 'CRS template.xlsx'),
    os.path.join(settings.BASE_DIR, 'media', 'templates', 'CRS template.xlsx'),
]


def get_s3_service():
    """Get S3Service instance if available"""
    try:
        from apps.core.s3_service import S3Service
        return S3Service()
    except ImportError:
        logger.warning("S3Service not available, falling back to direct URL access")
        return None


def download_template_from_s3_service() -> BytesIO:
    """
    Download CRS template using S3Service
    Tries multiple possible keys in the bucket
    
    Returns:
        BytesIO: Template buffer
        
    Raises:
        FileNotFoundError: If template not found in S3
    """
    s3_service = get_s3_service()
    if not s3_service:
        raise FileNotFoundError("S3Service not available")
    
    # Try each possible template key
    for template_key in S3_TEMPLATE_FALLBACK_KEYS:
        logger.info(f"⬇️ Trying to download template: {template_key}")
        try:
            result = s3_service.download_file(template_key)
            
            if result.get('success') and result.get('body'):
                # Read the streaming body
                content = result['body'].read()
                template_buffer = BytesIO(content)
                logger.info(f"✅ Downloaded template from S3: {template_key} ({len(content)} bytes)")
                return template_buffer
                
        except Exception as e:
            logger.debug(f"Template not found at {template_key}: {e}")
            continue
    
    raise FileNotFoundError(f"Template not found in S3. Tried keys: {S3_TEMPLATE_FALLBACK_KEYS}")


def get_crs_template() -> BytesIO:
    """
    Smart template fetcher with multiple fallbacks
    
    Priority:
    1. Local template files (multiple locations checked)
    2. Download from AWS S3 using S3Service (bucket: user-management-rejlers)
    3. Fallback to direct URL download
    4. Cache downloaded template for future use
    
    Returns:
        BytesIO: Template file buffer
    
    Raises:
        FileNotFoundError: If template cannot be found or downloaded
    """
    
    # Strategy 1: Check local paths first (fastest)
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
    
    # Strategy 2: Download from AWS S3 using S3Service
    logger.info(f"⬇️ No local template found, attempting download from AWS S3 bucket...")
    try:
        template_buffer = download_template_from_s3_service()
        
        # Cache the downloaded template for future use
        cache_template_locally(template_buffer)
        
        logger.info(f"✅ Downloaded and cached template from AWS S3")
        return template_buffer
        
    except Exception as e:
        logger.warning(f"⚠️ S3Service download failed: {e}, trying direct URL...")
    
    # Strategy 3: Fallback to direct URL download
    try:
        template_buffer = download_template_from_s3(DEFAULT_TEMPLATE_URL)
        
        # Cache the downloaded template for future use
        cache_template_locally(template_buffer)
        
        logger.info(f"✅ Downloaded and cached template from direct URL")
        return template_buffer
        
    except Exception as e:
        logger.error(f"❌ Failed to download template: {e}")
    
    # Strategy 4: Raise error if all strategies fail
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
        's3_bucket': 'user-management-rejlers',
        's3_key': S3_TEMPLATE_KEY,
        's3_url': DEFAULT_TEMPLATE_URL,
        's3_accessible': False,
        's3_service_available': False,
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
    
    # Check S3Service availability
    s3_service = get_s3_service()
    info['s3_service_available'] = s3_service is not None
    
    # Check S3 accessibility via S3Service
    if s3_service:
        try:
            result = s3_service.download_file(S3_TEMPLATE_KEY)
            info['s3_accessible'] = result.get('success', False)
        except:
            info['s3_accessible'] = False
    
    # Fallback: Check S3 accessibility via HEAD request
    if not info['s3_accessible']:
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
