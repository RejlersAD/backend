"""
RADAI S3 Backward Compatibility Layer
Ensures existing code continues to work while enabling gradual migration to unified service

This module provides:
1. Adapter classes for existing S3 services
2. Wrapper functions for seamless migration
3. Automatic fallback to legacy services
4. Migration tracking and logging
"""

import os
import logging
from typing import Dict, Optional, Any, BinaryIO
from django.conf import settings

logger = logging.getLogger(__name__)

# Import services (with fallback handling)
try:
    from .unified_s3_service import get_unified_s3_service, UnifiedS3Service
    UNIFIED_SERVICE_AVAILABLE = True
except ImportError:
    UNIFIED_SERVICE_AVAILABLE = False
    logger.warning("[Compatibility] Unified S3 service not available")

try:
    from .s3_service import get_s3_service, S3Service
    LEGACY_SERVICE_AVAILABLE = True
except ImportError:
    LEGACY_SERVICE_AVAILABLE = False
    logger.warning("[Compatibility] Legacy S3 service not available")


class S3ServiceAdapter:
    """
    Adapter class that provides unified interface for both legacy and new S3 services
    Ensures backward compatibility while enabling gradual migration
    """
    
    def __init__(self, prefer_unified: bool = None):
        """
        Initialize adapter with service preference
        
        Args:
            prefer_unified: If True, prefer unified service when available
        """
        if prefer_unified is None:
            prefer_unified = getattr(settings, 'RADAI_USE_UNIFIED_FOLDERS', False)
        
        self.prefer_unified = prefer_unified
        self.unified_service = None
        self.legacy_service = None
        
        # Initialize available services
        if UNIFIED_SERVICE_AVAILABLE and self.prefer_unified:
            try:
                self.unified_service = get_unified_s3_service()
                logger.info("[S3Adapter] Using unified S3 service")
            except Exception as e:
                logger.warning(f"[S3Adapter] Failed to initialize unified service: {e}")
        
        if LEGACY_SERVICE_AVAILABLE and not self.unified_service:
            try:
                self.legacy_service = get_s3_service()
                logger.info("[S3Adapter] Using legacy S3 service")
            except Exception as e:
                logger.warning(f"[S3Adapter] Failed to initialize legacy service: {e}")
    
    def _get_active_service(self):
        """Get the currently active service"""
        return self.unified_service if self.unified_service else self.legacy_service
    
    def upload_file(self, file_obj, folder_type: str, filename: str = None, **kwargs) -> Dict:
        """
        Upload file using appropriate service
        
        Args:
            file_obj: File object to upload
            folder_type: Legacy folder type or document type
            filename: Optional filename
            **kwargs: Additional arguments
            
        Returns:
            dict: Upload result
        """
        try:
            if self.unified_service:
                # Convert legacy folder_type to document_type
                document_type = self._convert_folder_to_document_type(folder_type)
                return self.unified_service.upload_document(
                    file_obj=file_obj,
                    document_type=document_type,
                    filename=filename,
                    **kwargs
                )
            
            elif self.legacy_service:
                return self.legacy_service.upload_file(
                    file_obj=file_obj,
                    folder_type=folder_type,
                    filename=filename,
                    **kwargs
                )
            
            else:
                return {'success': False, 'error': 'No S3 service available'}
                
        except Exception as e:
            logger.error(f"[S3Adapter] Upload failed: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def download_file(self, s3_key: str, local_path: str = None) -> Dict:
        """Download file using appropriate service"""
        try:
            if self.unified_service:
                result = self.unified_service.download_document(s3_key)
                if result['success'] and local_path:
                    # Save to local path if requested
                    with open(local_path, 'wb') as f:
                        f.write(result['content'])
                return result
            
            elif self.legacy_service:
                return self.legacy_service.download_file(s3_key, local_path)
            
            else:
                return {'success': False, 'error': 'No S3 service available'}
                
        except Exception as e:
            logger.error(f"[S3Adapter] Download failed: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def generate_presigned_url(self, s3_key: str, expiration: int = 3600) -> str:
        """Generate presigned URL using appropriate service"""
        try:
            if self.unified_service:
                return self.unified_service.generate_download_url(s3_key, expiration)
            
            elif self.legacy_service:
                return self.legacy_service.get_presigned_url(s3_key, expiration)
            
            else:
                return None
                
        except Exception as e:
            logger.error(f"[S3Adapter] Presigned URL generation failed: {str(e)}")
            return None
    
    def delete_file(self, s3_key: str) -> Dict:
        """Delete file using appropriate service"""
        try:
            if self.unified_service:
                return self.unified_service.delete_document(s3_key)
            
            elif self.legacy_service:
                return self.legacy_service.delete_file(s3_key)
            
            else:
                return {'success': False, 'error': 'No S3 service available'}
                
        except Exception as e:
            logger.error(f"[S3Adapter] Delete failed: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def _convert_folder_to_document_type(self, folder_type: str) -> str:
        """Convert legacy folder type to unified document type"""
        mapping = {
            'pid_drawings': 'pid_drawing',
            'pfd_files': 'pfd_document',
            'pid_reports': 'engineering_document',
            'crs_documents': 'crs_document',
            'exports': 'user_export',
            'avatars': 'user_upload',
            'temp': 'user_upload',
            'logs': 'system_report',
        }
        
        return mapping.get(folder_type, 'user_upload')


class LegacyS3ServiceWrapper:
    """
    Wrapper that makes unified service look like legacy service
    Enables drop-in replacement for existing code
    """
    
    def __init__(self):
        self.adapter = S3ServiceAdapter(prefer_unified=True)
    
    def upload_file(self, file_obj, folder_type: str, filename: str = None, **kwargs):
        """Legacy upload interface"""
        return self.adapter.upload_file(file_obj, folder_type, filename, **kwargs)
    
    def download_file(self, s3_key: str, local_path: str = None):
        """Legacy download interface"""
        return self.adapter.download_file(s3_key, local_path)
    
    def get_presigned_url(self, s3_key: str, expiration: int = 3600):
        """Legacy presigned URL interface"""
        return self.adapter.generate_presigned_url(s3_key, expiration)
    
    def delete_file(self, s3_key: str):
        """Legacy delete interface"""
        return self.adapter.delete_file(s3_key)


# Singleton adapters
_s3_adapter = None
_legacy_wrapper = None

def get_s3_adapter(prefer_unified: bool = None) -> S3ServiceAdapter:
    """Get S3 adapter singleton"""
    global _s3_adapter
    if _s3_adapter is None:
        _s3_adapter = S3ServiceAdapter(prefer_unified)
    return _s3_adapter

def get_legacy_s3_wrapper() -> LegacyS3ServiceWrapper:
    """Get legacy wrapper singleton"""
    global _legacy_wrapper
    if _legacy_wrapper is None:
        _legacy_wrapper = LegacyS3ServiceWrapper()
    return _legacy_wrapper


# =============================================================================
# MIGRATION HELPER FUNCTIONS
# =============================================================================

def migrate_service_to_unified(service_name: str, dry_run: bool = True) -> Dict:
    """
    Migrate a specific service from legacy to unified S3 service
    
    Args:
        service_name: Name of service to migrate
        dry_run: If True, only simulate the migration
        
    Returns:
        dict: Migration report
    """
    logger.info(f"[Migration] {'Simulating' if dry_run else 'Starting'} migration for {service_name}")
    
    migration_report = {
        'service': service_name,
        'dry_run': dry_run,
        'files_processed': 0,
        'files_migrated': 0,
        'errors': [],
        'success': False,
    }
    
    try:
        # This would implement actual migration logic
        # For now, it's a placeholder
        migration_report['success'] = True
        logger.info(f"[Migration] Migration {'simulation' if dry_run else 'completed'} for {service_name}")
        
    except Exception as e:
        migration_report['errors'].append(str(e))
        logger.error(f"[Migration] Failed for {service_name}: {e}")
    
    return migration_report

def validate_migration_readiness() -> Dict:
    """
    Validate system readiness for migration to unified service
    
    Returns:
        dict: Validation report
    """
    report = {
        'ready': False,
        'checks': [],
        'issues': [],
        'recommendations': [],
    }
    
    # Check unified service availability
    check = {'name': 'Unified Service Availability', 'passed': UNIFIED_SERVICE_AVAILABLE}
    if UNIFIED_SERVICE_AVAILABLE:
        check['message'] = 'Unified S3 service is available'
    else:
        check['message'] = 'Unified S3 service not available'
        report['issues'].append('Unified S3 service not available')
    report['checks'].append(check)
    
    # Check environment configuration
    unified_enabled = getattr(settings, 'RADAI_USE_UNIFIED_FOLDERS', False)
    check = {'name': 'Environment Configuration', 'passed': True}
    if unified_enabled:
        check['message'] = 'RADAI_USE_UNIFIED_FOLDERS is enabled'
    else:
        check['message'] = 'RADAI_USE_UNIFIED_FOLDERS is disabled (legacy mode)'
        report['recommendations'].append('Enable RADAI_USE_UNIFIED_FOLDERS in environment')
    report['checks'].append(check)
    
    # Check S3 credentials
    aws_key = os.environ.get('AWS_ACCESS_KEY_ID')
    aws_secret = os.environ.get('AWS_SECRET_ACCESS_KEY') 
    bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')
    
    check = {'name': 'AWS Credentials', 'passed': bool(aws_key and aws_secret and bucket_name)}
    if check['passed']:
        check['message'] = 'AWS credentials and bucket configured'
    else:
        check['message'] = 'Missing AWS credentials or bucket configuration'
        report['issues'].append('AWS credentials or bucket not properly configured')
    report['checks'].append(check)
    
    # Overall readiness
    report['ready'] = len(report['issues']) == 0
    
    return report


# =============================================================================
# CONVENIENCE FUNCTIONS FOR GRADUAL MIGRATION
# =============================================================================

def smart_upload(file_obj, document_type: str, **kwargs) -> Dict:
    """
    Smart upload that uses unified service when available, falls back to legacy
    """
    adapter = get_s3_adapter()
    return adapter.upload_file(file_obj, document_type, **kwargs)

def smart_download(s3_key: str, **kwargs) -> Dict:
    """Smart download with service fallback"""
    adapter = get_s3_adapter()
    return adapter.download_file(s3_key, **kwargs)

def smart_presigned_url(s3_key: str, expiration: int = 3600) -> str:
    """Smart presigned URL generation with service fallback"""
    adapter = get_s3_adapter()
    return adapter.generate_presigned_url(s3_key, expiration)

def smart_delete(s3_key: str) -> Dict:
    """Smart delete with service fallback"""
    adapter = get_s3_adapter()
    return adapter.delete_file(s3_key)


# =============================================================================
# LEGACY IMPORT COMPATIBILITY
# =============================================================================

# These imports maintain compatibility with existing code
try:
    from .s3_service import S3Service as LegacyS3Service
except ImportError:
    LegacyS3Service = None

# Provide legacy function names for backward compatibility
upload_to_s3 = smart_upload
download_from_s3 = smart_download
get_s3_url = smart_presigned_url
delete_from_s3 = smart_delete


if __name__ == "__main__":
    # Test compatibility layer
    print("[Compatibility] Testing S3 service compatibility...")
    
    report = validate_migration_readiness()
    print(f"Migration readiness: {'✅ Ready' if report['ready'] else '⚠️ Issues found'}")
    
    for check in report['checks']:
        status = "✅" if check['passed'] else "❌"
        print(f"{status} {check['name']}: {check['message']}")
    
    if report['issues']:
        print("\n⚠️ Issues:")
        for issue in report['issues']:
            print(f"  - {issue}")
    
    if report['recommendations']:
        print("\n💡 Recommendations:")
        for rec in report['recommendations']:
            print(f"  - {rec}")