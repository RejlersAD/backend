"""
AWS S3 Storage Backends
Secure S3 storage configuration using django-storages
Enhanced with better error handling and logging
"""
import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# Check if S3 is enabled before importing storages
USE_S3 = os.environ.get('USE_S3', 'False').lower() == 'true'

if USE_S3:
    from storages.backends.s3boto3 import S3Boto3Storage
    
    class MediaStorage(S3Boto3Storage):
        """
        S3 storage backend for media files (P&ID drawings, reports, avatars)
        
        Security features:
        - Uses environment variables for configuration
        - No hardcoded credentials
        - Supports IAM roles (EC2/ECS/Lambda)
        - Custom expiration for presigned URLs
        """
        
        location = 'media'
        default_acl = None  # Disable ACLs (use bucket policy instead)
        file_overwrite = False   # Don't overwrite files with same name
        custom_domain = False    # Use presigned URLs instead of public URLs
        
        # Security: Use temporary presigned URLs (expires in 1 hour)
        querystring_expire = 3600  # 1 hour
        
        # Performance optimizations
        object_parameters = {
            'CacheControl': 'max-age=86400',  # 24 hours
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[MediaStorage] Initialized S3 media storage: {self.bucket_name}/{self.location}")


    class StaticStorage(S3Boto3Storage):
        """
        S3 storage backend for static files (CSS, JS, images)
        
        Static files can be public since they don't contain sensitive data
        """
        
        location = 'static'
        default_acl = None  # Disable ACLs (use bucket policy for public access)
        file_overwrite = True         # Overwrite on deployment
        
        # Cache static files for 1 year (immutable)
        object_parameters = {
            'CacheControl': 'max-age=31536000, immutable',
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[StaticStorage] Initialized S3 static storage: {self.bucket_name}/{self.location}")


    class PIDDrawingStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for P&ID drawings
        
        Features:
        - Isolated folder structure
        - Private access only
        - Long-term storage (no automatic deletion)
        - Presigned URLs for secure downloads
        """
        
        location = 'media/pid_drawings'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 7200  # 2 hours (longer for analysis processing)
        
        # Metadata for tracking
        object_parameters = {
            'CacheControl': 'max-age=86400',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'pid_drawing'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[PIDDrawingStorage] Initialized: {self.bucket_name}/{self.location}")


    class PIDReportStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for generated P&ID reports (PDF, Excel)
        
        Features:
        - Isolated folder structure
        - Private access only
        - Presigned URLs for downloads
        """
        
        location = 'media/pid_reports'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 3600  # 1 hour
        
        object_parameters = {
            'CacheControl': 'max-age=86400',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'pid_report'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[PIDReportStorage] Initialized: {self.bucket_name}/{self.location}")


    class CRSDocumentStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for CRS documents
        """
        
        location = 'media/crs_documents'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 3600
        
        object_parameters = {
            'CacheControl': 'max-age=86400',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'crs_document'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[CRSDocumentStorage] Initialized: {self.bucket_name}/{self.location}")


    class PFDFileStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for PFD files
        """
        
        location = 'media/pfd_files'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 7200
        
        object_parameters = {
            'CacheControl': 'max-age=86400',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'pfd_file'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[PFDFileStorage] Initialized: {self.bucket_name}/{self.location}")


    class AvatarStorage(S3Boto3Storage):
        """
        Dedicated S3 storage for user avatars
        """
        
        location = 'media/avatars'
        default_acl = 'private'
        file_overwrite = True  # Allow overwriting avatars
        custom_domain = False
        querystring_expire = 86400  # 24 hours
        
        object_parameters = {
            'CacheControl': 'max-age=3600',  # 1 hour (avatars can change)
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'avatar'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[AvatarStorage] Initialized: {self.bucket_name}/{self.location}")


    class ExportStorage(S3Boto3Storage):
        """
        Temporary storage for exports (Excel, CSV, etc.)
        """
        
        location = 'media/exports'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False
        querystring_expire = 1800  # 30 minutes for exports
        
        object_parameters = {
            'CacheControl': 'max-age=1800',
            'Metadata': {
                'app': 'aiflow',
                'content_type': 'export'
            }
        }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            logger.info(f"[ExportStorage] Initialized: {self.bucket_name}/{self.location}")

else:
    # Fallback to local file storage when S3 is not enabled
    from django.core.files.storage import FileSystemStorage
    
    logger.info("[Storage] S3 not enabled, using local file storage")
    
    class MediaStorage(FileSystemStorage):
        """Local file storage for media files"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = getattr(settings, 'MEDIA_ROOT', 'media')
            super().__init__(*args, **kwargs)
    
    class StaticStorage(FileSystemStorage):
        """Local file storage for static files"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = getattr(settings, 'STATIC_ROOT', 'staticfiles')
            super().__init__(*args, **kwargs)
    
    class PIDDrawingStorage(FileSystemStorage):
        """Local storage for P&ID drawings"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/pid_drawings'
            super().__init__(*args, **kwargs)
    
    class PIDReportStorage(FileSystemStorage):
        """Local storage for P&ID reports"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/pid_reports'
            super().__init__(*args, **kwargs)
    
    class CRSDocumentStorage(FileSystemStorage):
        """Local storage for CRS documents"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/crs_documents'
            super().__init__(*args, **kwargs)
    
    class PFDFileStorage(FileSystemStorage):
        """Local storage for PFD files"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/pfd_files'
            super().__init__(*args, **kwargs)
    
    class AvatarStorage(FileSystemStorage):
        """Local storage for avatars"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/avatars'
            super().__init__(*args, **kwargs)
    
    class ExportStorage(FileSystemStorage):
        """Local storage for exports"""
        def __init__(self, *args, **kwargs):
            kwargs['location'] = 'media/exports'
            super().__init__(*args, **kwargs)

