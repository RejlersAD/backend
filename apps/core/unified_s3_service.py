"""
RADAI Unified S3 Service
Consolidated S3 operations for the entire RADAI platform

This service provides:
1. Unified document storage across all RADAI services  
2. Consistent folder organization
3. Backward compatibility with existing services
4. Smart migration support
5. Environment-based configuration
"""

import os
import uuid
import hashlib
import mimetypes
from datetime import datetime, timedelta
from typing import Dict, Optional, List, BinaryIO
from io import BytesIO

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone

# Import configurations
from .unified_folder_config import UnifiedFolderConfig, get_folder, get_document_folder
from .s3_service import get_s3_service  # For backward compatibility

import logging
logger = logging.getLogger(__name__)


class UnifiedS3Service:
    """
    Unified S3 service for the entire RADAI platform
    Replaces multiple S3 service implementations with a single, consistent interface
    """
    
    def __init__(self):
        """Initialize unified S3 service with smart configuration"""
        # Use standardized bucket and region
        self.bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', 'user-management-rejlers')
        self.region = getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
        
        # RADAI-specific configuration
        self.use_unified_folders = getattr(settings, 'RADAI_USE_UNIFIED_FOLDERS', False)
        self.folder_prefix = getattr(settings, 'RADAI_FOLDER_PREFIX', '')
        self.migration_mode = getattr(settings, 'RADAI_MIGRATION_MODE', False)
        self.debug_mode = getattr(settings, 'RADAI_S3_DEBUG', False)
        
        # Initialize S3 clients
        self._init_s3_clients()
        
        # Log configuration
        if self.debug_mode:
            self._log_configuration()
    
    def _init_s3_clients(self):
        """Initialize S3 clients with proper configuration"""
        try:
            self.s3_client = boto3.client(
                's3',
                region_name=self.region,
                aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
            )
            
            self.s3_resource = boto3.resource(
                's3',
                region_name=self.region,
                aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')  
            )
            
            logger.info(f"[UnifiedS3] Initialized with bucket: {self.bucket_name}")
            
        except Exception as e:
            logger.error(f"[UnifiedS3] Failed to initialize S3 clients: {e}")
            self.s3_client = None
            self.s3_resource = None
    
    def _log_configuration(self):
        """Log current configuration (debug mode)"""
        logger.info("[UnifiedS3] Configuration:")
        logger.info(f"  Bucket: {self.bucket_name}")
        logger.info(f"  Region: {self.region}")
        logger.info(f"  Unified Folders: {self.use_unified_folders}")
        logger.info(f"  Folder Prefix: {self.folder_prefix or 'None'}")
        logger.info(f"  Migration Mode: {self.migration_mode}")
    
    def _get_full_path(self, folder_path: str, filename: str) -> str:
        """
        Get full S3 key with optional environment prefix
        
        Args:
            folder_path: Base folder path
            filename: File name
            
        Returns:
            str: Full S3 key
        """
        # Add environment prefix if configured
        if self.folder_prefix:
            folder_path = f"{self.folder_prefix.rstrip('/')}/{folder_path.lstrip('/')}"
        
        return f"{folder_path.rstrip('/')}/{filename}"
    
    def _generate_unique_filename(self, original_filename: str) -> str:
        """Generate unique filename with timestamp and UUID"""
        name, ext = os.path.splitext(original_filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        return f"{name}_{timestamp}_{unique_id}{ext}"
    
    def _calculate_checksum(self, file_obj: BinaryIO) -> str:
        """Calculate SHA256 checksum of file"""
        sha256_hash = hashlib.sha256()
        file_obj.seek(0)
        
        for chunk in iter(lambda: file_obj.read(4096), b""):
            sha256_hash.update(chunk)
        
        file_obj.seek(0)  # Reset file pointer
        return sha256_hash.hexdigest()
    
    def upload_document(self, 
                       file_obj: BinaryIO,
                       document_type: str,
                       filename: str = None,
                       user_id: Optional[int] = None,
                       metadata: Optional[Dict] = None) -> Dict:
        """
        Upload document with appropriate categorization
        
        Args:
            file_obj: File object to upload
            document_type: Type of document (pid_drawing, user_upload, etc.)
            filename: Optional custom filename 
            user_id: User ID for user-specific documents
            metadata: Additional metadata
            
        Returns:
            dict: Upload result with S3 key, URL, and metadata
        """
        try:
            if not self.s3_client:
                raise Exception("S3 client not initialized")
            
            # Generate filename if not provided
            if not filename:
                if hasattr(file_obj, 'name'):
                    filename = self._generate_unique_filename(os.path.basename(file_obj.name))
                else:
                    filename = f"document_{int(datetime.now().timestamp())}.bin"
            
            # Get appropriate folder
            if self.use_unified_folders:
                folder_path = get_document_folder(document_type, user_id)
            else:
                # Use legacy folder mapping
                legacy_service = get_s3_service()
                folder_path = legacy_service.get_document_folder(document_type, user_id)
            
            # Generate full S3 key
            s3_key = self._get_full_path(folder_path, filename)
            
            # Calculate file metadata
            file_size = file_obj.seek(0, 2)  # Get file size
            file_obj.seek(0)  # Reset position
            
            checksum = self._calculate_checksum(file_obj)
            content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            
            # Prepare S3 metadata
            s3_metadata = {
                'document-type': document_type,
                'original-filename': filename,
                'upload-timestamp': timezone.now().isoformat(),
                'checksum': checksum,
                'file-size': str(file_size),
            }
            
            if user_id:
                s3_metadata['user-id'] = str(user_id)
            
            if metadata:
                s3_metadata.update({f'custom-{k}': str(v) for k, v in metadata.items()})
            
            # Upload to S3
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                s3_key,
                ExtraArgs={
                    'ContentType': content_type,
                    'Metadata': s3_metadata,
                    'ServerSideEncryption': 'AES256',
                }
            )
            
            # Generate presigned URL
            presigned_url = self.generate_download_url(s3_key, expiration=3600)
            
            logger.info(f"[UnifiedS3] Uploaded: {s3_key}")
            
            return {
                'success': True,
                's3_key': s3_key,
                'bucket': self.bucket_name,
                'region': self.region,
                'url': presigned_url,
                'filename': filename,
                'file_size': file_size,
                'content_type': content_type,
                'checksum': checksum,
                'metadata': s3_metadata,
            }
            
        except Exception as e:
            logger.error(f"[UnifiedS3] Upload failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                's3_key': None,
                'url': None,
            }
    
    def download_document(self, s3_key: str) -> Dict:
        """
        Download document from S3
        
        Args:
            s3_key: S3 object key
            
        Returns:
            dict: Download result with file content and metadata
        """
        try:
            if not self.s3_client:
                raise Exception("S3 client not initialized")
            
            # Get object
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            file_content = response['Body'].read()
            metadata = response.get('Metadata', {})
            
            return {
                'success': True,
                'content': file_content,
                'content_type': response.get('ContentType'),
                'file_size': response.get('ContentLength'),
                'metadata': metadata,
                'last_modified': response.get('LastModified'),
            }
            
        except Exception as e:
            logger.error(f"[UnifiedS3] Download failed for {s3_key}: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'content': None,
            }
    
    def generate_download_url(self, s3_key: str, expiration: int = 3600) -> str:
        """Generate presigned download URL"""
        try:
            if not self.s3_client:
                return None
            
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': s3_key},
                ExpiresIn=expiration
            )
            
            return url
            
        except Exception as e:
            logger.error(f"[UnifiedS3] Failed to generate URL for {s3_key}: {e}")
            return None
    
    def delete_document(self, s3_key: str) -> Dict:
        """
        Delete document from S3
        
        Args:
            s3_key: S3 object key
            
        Returns:
            dict: Deletion result
        """
        try:
            if not self.s3_client:
                raise Exception("S3 client not initialized")
            
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            logger.info(f"[UnifiedS3] Deleted: {s3_key}")
            
            return {
                'success': True,
                'deleted_key': s3_key,
            }
            
        except Exception as e:
            logger.error(f"[UnifiedS3] Delete failed for {s3_key}: {str(e)}")
            return {
                'success': False,
                'error': str(e),
            }
    
    def list_documents(self, 
                      folder_path: str = '',
                      document_type: str = None,
                      limit: int = 100) -> Dict:
        """
        List documents in S3
        
        Args:
            folder_path: Folder to search in
            document_type: Filter by document type
            limit: Maximum results
            
        Returns:
            dict: List of documents
        """
        try:
            if not self.s3_client:
                raise Exception("S3 client not initialized")
            
            prefix = self._get_full_path(folder_path, '') if folder_path else ''
            
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=limit
            )
            
            documents = []
            for obj in response.get('Contents', []):
                doc_info = {
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'],
                    'filename': obj['Key'].split('/')[-1],
                }
                
                # Get metadata if available
                try:
                    head_response = self.s3_client.head_object(
                        Bucket=self.bucket_name,
                        Key=obj['Key']
                    )
                    doc_info['metadata'] = head_response.get('Metadata', {})
                    doc_info['content_type'] = head_response.get('ContentType')
                except:
                    pass  # Skip if metadata unavailable
                
                documents.append(doc_info)
            
            return {
                'success': True,
                'documents': documents,
                'count': len(documents),
                'prefix': prefix,
            }
            
        except Exception as e:
            logger.error(f"[UnifiedS3] List failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'documents': [],
            }
    
    def get_storage_stats(self) -> Dict:
        """Get storage statistics for the bucket"""
        try:
            if not self.s3_client:
                raise Exception("S3 client not initialized")
            
            # This is a simplified version - for production, consider using CloudWatch metrics
            response = self.s3_client.list_objects_v2(Bucket=self.bucket_name)
            
            total_objects = response.get('KeyCount', 0)
            total_size = sum(obj['Size'] for obj in response.get('Contents', []))
            
            return {
                'success': True,
                'bucket': self.bucket_name,
                'region': self.region,
                'total_objects': total_objects,
                'total_size': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'unified_folders': self.use_unified_folders,
            }
            
        except Exception as e:
            logger.error(f"[UnifiedS3] Stats failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
            }


# Singleton instance
_unified_s3_service = None

def get_unified_s3_service() -> UnifiedS3Service:
    """Get or create unified S3 service singleton"""
    global _unified_s3_service
    if _unified_s3_service is None:
        _unified_s3_service = UnifiedS3Service()
    return _unified_s3_service


# Convenience functions for easy migration from existing services
def upload_unified_document(file_obj, document_type: str, **kwargs) -> Dict:
    """Convenience function for document upload"""
    return get_unified_s3_service().upload_document(file_obj, document_type, **kwargs)

def download_unified_document(s3_key: str) -> Dict:
    """Convenience function for document download"""
    return get_unified_s3_service().download_document(s3_key)

def get_unified_download_url(s3_key: str, expiration: int = 3600) -> str:
    """Convenience function for download URL"""
    return get_unified_s3_service().generate_download_url(s3_key, expiration)