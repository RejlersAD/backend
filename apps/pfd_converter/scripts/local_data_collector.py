"""
Local File Collector for Training
==================================
Collects P&ID files from local media directory for training when S3 access is restricted
"""
import os
import logging
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)


def collect_local_pids(min_count: int = 50) -> List[str]:
    """
    Collect P&ID files from local media directory
    
    Args:
        min_count: Minimum number of files required
        
    Returns:
        List of file paths
    """
    logger.info("🔍 Collecting P&ID files from local filesystem...")
    
    # Search in media directories
    search_dirs = [
        '/app/media/pid_drawings',
        '/app/media/pfd_documents',
        '/app/pfd_documents',
        '/app/pid_drawings',
        '/app/pfd_training_samples',
        '/app/pid_training_samples',
    ]
    
    pids = []
    for search_dir in search_dirs:
        if not os.path.exists(search_dir):
            continue
            
        logger.info(f"   Scanning: {search_dir}")
        
        for root, dirs, files in os.walk(search_dir):
            for file in files:
                if any(file.lower().endswith(ext) for ext in ['.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff']):
                    full_path = os.path.join(root, file)
                    file_size = os.path.getsize(full_path)
                    
                    # Skip very small files (thumbnails)
                    if file_size > 50000:  # 50KB minimum
                        pids.append(full_path)
    
    logger.info(f"✅ Found {len(pids)} P&ID files in local filesystem")
    
    if len(pids) < min_count:
        logger.warning(f"⚠️  Only found {len(pids)} files (minimum: {min_count})")
        logger.info("💡 Tip: Run with fewer files for testing, or add more drawings")
    
    return sorted(set(pids))  # Remove duplicates


def collect_local_pfd_pid_pairs() -> List[Dict]:
    """
    Collect PFD→P&ID pairs from Django database
    
    Returns:
        List of dicts with 'pfd_path', 'pid_path', 'conversion_id'
    """
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    
    from apps.pfd_converter.models import PIDConversion
    
    logger.info("🔍 Collecting PFD→P&ID pairs from database...")
    
    conversions = PIDConversion.objects.filter(status='completed')
    
    pairs = []
    for conv in conversions:
        # Use correct field name: pid_file not generated_pid
        if conv.pfd_document and conv.pid_file:
            try:
                pfd_path = conv.pfd_document.path if hasattr(conv.pfd_document, 'path') else str(conv.pfd_document)
                pid_path = conv.pid_file.path if hasattr(conv.pid_file, 'path') else str(conv.pid_file)
                
                pairs.append({
                    'conversion_id': str(conv.id),
                    'pfd_path': pfd_path,
                    'pid_path': pid_path,
                    'created_at': conv.created_at,
                })
            except Exception as e:
                logger.warning(f"⚠️  Skipping conversion {conv.id}: {e}")
                continue
    
    logger.info(f"✅ Found {len(pairs)} completed PFD→P&ID conversions")
    
    return pairs
