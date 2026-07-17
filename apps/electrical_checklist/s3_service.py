"""
AWS S3 Service for Electrical Checklist
========================================
Handles all S3 operations for checklist PDFs and Excel exports

SOFT-CODED CONFIGURATION:
- Bucket name from environment variable
- Folder structure from config
- File naming patterns configurable
- Retention policies soft-coded

Pattern: Follows apps/core/s3_service.py and apps/pfd/services/s3_pfd_manager.py
"""
import boto3
import os
import io
import logging
from datetime import datetime, timedelta
from botocore.exceptions import ClientError
from botocore.client import Config
from django.conf import settings
from typing import Optional, List, Dict, BinaryIO

logger = logging.getLogger(__name__)


# ─── SOFT-CODED CONFIGURATION ─────────────────────────────────────────────────
# S3 bucket configuration
S3_BUCKET_ENV_KEY = 'AWS_STORAGE_BUCKET_NAME'
S3_BUCKET_FALLBACK = 'user-management-rejlers'
S3_REGION_ENV_KEY = 'AWS_S3_REGION_NAME'
S3_REGION_FALLBACK = 'us-east-1'

# Folder structure (soft-coded, uses project_code)
S3_FOLDER_PATTERNS = {
    'pdf': 'electrical_checklist/{project_code}/pdfs/',
    'excel': 'electrical_checklist/{project_code}/exports/',
    'signatures': 'electrical_checklist/{project_code}/signatures/',
    'temp': 'electrical_checklist/temp/'
}

# File naming patterns (soft-coded)
FILE_NAME_PATTERNS = {
    'pdf': '{timestamp}_{original_name}',
    'excel': '{project_code}_checklist_{job_id}_{timestamp}.xlsx',
    'signature': 'signature_{section_id}_{timestamp}.png'
}

# Presigned URL expiration (seconds)
PRESIGNED_URL_EXPIRATION = 3600  # 1 hour
DOWNLOAD_URL_EXPIRATION = 300    # 5 minutes

# File retention (days)
TEMP_FILE_RETENTION_HOURS = 24
ARCHIVE_AFTER_DAYS = 90
DELETE_ARCHIVED_AFTER_DAYS = 365


# ─── S3 SERVICE CLASS ─────────────────────────────────────────────────────────

class ChecklistS3Service:
    """
    Comprehensive S3 service for electrical checklist files
    """
    
    def __init__(self):
        """Initialize S3 client with credentials from environment"""
        self.bucket_name = os.environ.get(S3_BUCKET_ENV_KEY, S3_BUCKET_FALLBACK)
        self.region = os.environ.get(S3_REGION_ENV_KEY, S3_REGION_FALLBACK)

        # SOFT-CODED FIX: force the region-specific S3 endpoint + SigV4.
        # Opt-in regions (e.g. me-central-1 / UAE) reject requests signed
        # against/routed through the legacy global `s3.amazonaws.com`
        # endpoint with IllegalLocationConstraintException. Mirrors the same
        # fix already applied for Django storage in config/settings.py.
        self.s3_client = boto3.client(
            's3',
            region_name=self.region,
            endpoint_url=f'https://s3.{self.region}.amazonaws.com',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            config=Config(signature_version='s3v4', s3={'addressing_style': 'virtual'})
        )
        
        logger.info(f"[ChecklistS3] Initialized with bucket: {self.bucket_name}, region: {self.region}")
    
    # ─── FOLDER MANAGEMENT ────────────────────────────────────────────────────
    
    def get_folder_path(self, folder_type: str, project_code: str) -> str:
        """
        Get S3 folder path for a given type
        
        Args:
            folder_type: Type of folder ('pdf', 'excel', 'signatures', 'temp')
            project_code: Project code for path formatting
            
        Returns:
            str: Full S3 folder path
        """
        pattern = S3_FOLDER_PATTERNS.get(folder_type, S3_FOLDER_PATTERNS['temp'])
        return pattern.format(project_code=project_code)
    
    def get_file_key(self, file_type: str, project_code: str, job_id: Optional[int] = None,
                     original_name: Optional[str] = None, section_id: Optional[str] = None) -> str:
        """
        Generate S3 object key using soft-coded naming pattern
        
        Args:
            file_type: Type of file ('pdf', 'excel', 'signature')
            project_code: Project code
            job_id: Optional job ID
            original_name: Optional original filename
            section_id: Optional section ID for signatures
            
        Returns:
            str: Full S3 object key
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Get folder path
        folder = self.get_folder_path(file_type, project_code)
        
        # Get filename pattern
        pattern = FILE_NAME_PATTERNS.get(file_type, '{timestamp}_{original_name}')
        
        # Format filename
        filename = pattern.format(
            timestamp=timestamp,
            project_code=project_code,
            job_id=job_id or 'unknown',
            original_name=original_name or 'file',
            section_id=section_id or 'default'
        )
        
        return f"{folder}{filename}"
    
    # ─── UPLOAD OPERATIONS ────────────────────────────────────────────────────
    
    def upload_pdf(self, file_data: BinaryIO, original_filename: str, project_code: str) -> Dict:
        """
        Upload PDF file to S3
        
        Args:
            file_data: File binary data
            original_filename: Original filename
            project_code: Project code
            
        Returns:
            dict: {
                'success': bool,
                's3_key': str,
                'bucket': str,
                'size': int,
                'url': str (presigned)
            }
        """
        try:
            # Generate S3 key
            s3_key = self.get_file_key('pdf', project_code, original_name=original_filename)
            
            # Read file data
            if hasattr(file_data, 'read'):
                file_bytes = file_data.read()
                if hasattr(file_data, 'seek'):
                    file_data.seek(0)  # Reset file pointer
            else:
                file_bytes = file_data
            
            file_size = len(file_bytes)
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=file_bytes,
                ContentType='application/pdf',
                Metadata={
                    'original_filename': original_filename,
                    'project_code': project_code,
                    'upload_timestamp': datetime.now().isoformat()
                }
            )
            
            # Generate presigned URL
            url = self.generate_presigned_url(s3_key, expiration=PRESIGNED_URL_EXPIRATION)
            
            logger.info(f"[ChecklistS3] ✅ Uploaded PDF: {s3_key} ({file_size} bytes)")
            
            return {
                'success': True,
                's3_key': s3_key,
                'bucket': self.bucket_name,
                'size': file_size,
                'url': url
            }
            
        except Exception as e:
            logger.error(f"[ChecklistS3] ❌ PDF upload failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def upload_excel(self, file_data: BinaryIO, project_code: str, job_id: int) -> Dict:
        """
        Upload Excel file to S3
        
        Args:
            file_data: Excel file binary data
            project_code: Project code
            job_id: Job ID
            
        Returns:
            dict: {'success': bool, 's3_key': str, 'size': int, 'url': str}
        """
        try:
            # Generate S3 key
            s3_key = self.get_file_key('excel', project_code, job_id=job_id)
            
            # Read file data
            if hasattr(file_data, 'read'):
                file_bytes = file_data.read()
                if hasattr(file_data, 'seek'):
                    file_data.seek(0)
            else:
                file_bytes = file_data
            
            file_size = len(file_bytes)
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=file_bytes,
                ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                Metadata={
                    'project_code': project_code,
                    'job_id': str(job_id),
                    'upload_timestamp': datetime.now().isoformat()
                }
            )
            
            # Generate download URL
            url = self.generate_presigned_url(s3_key, expiration=DOWNLOAD_URL_EXPIRATION)
            
            logger.info(f"[ChecklistS3] ✅ Uploaded Excel: {s3_key} ({file_size} bytes)")
            
            return {
                'success': True,
                's3_key': s3_key,
                'bucket': self.bucket_name,
                'size': file_size,
                'url': url
            }
            
        except Exception as e:
            logger.error(f"[ChecklistS3] ❌ Excel upload failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def upload_signature(self, image_data: bytes, project_code: str, section_id: str) -> Dict:
        """
        Upload signature image to S3
        
        Args:
            image_data: Signature image bytes
            project_code: Project code
            section_id: Section identifier
            
        Returns:
            dict: {'success': bool, 's3_key': str, 'url': str}
        """
        try:
            s3_key = self.get_file_key('signature', project_code, section_id=section_id)
            
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=image_data,
                ContentType='image/png',
                Metadata={
                    'project_code': project_code,
                    'section_id': section_id,
                    'upload_timestamp': datetime.now().isoformat()
                }
            )
            
            url = self.generate_presigned_url(s3_key)
            
            logger.info(f"[ChecklistS3] ✅ Uploaded signature: {s3_key}")
            
            return {
                'success': True,
                's3_key': s3_key,
                'url': url
            }
            
        except Exception as e:
            logger.error(f"[ChecklistS3] ❌ Signature upload failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    # ─── DOWNLOAD OPERATIONS ──────────────────────────────────────────────────
    
    def download_file(self, s3_key: str) -> Optional[bytes]:
        """
        Download file from S3
        
        Args:
            s3_key: S3 object key
            
        Returns:
            bytes: File content or None if failed
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            file_data = response['Body'].read()
            logger.info(f"[ChecklistS3] ✅ Downloaded: {s3_key}")
            return file_data
            
        except Exception as e:
            logger.error(f"[ChecklistS3] ❌ Download failed for {s3_key}: {e}")
            return None
    
    def generate_presigned_url(self, s3_key: str, expiration: int = PRESIGNED_URL_EXPIRATION) -> str:
        """
        Generate presigned URL for file access
        
        Args:
            s3_key: S3 object key
            expiration: URL expiration time in seconds
            
        Returns:
            str: Presigned URL
        """
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': s3_key
                },
                ExpiresIn=expiration
            )
            return url
            
        except Exception as e:
            logger.error(f"[ChecklistS3] ❌ Presigned URL generation failed: {e}")
            return ""
    
    # ─── DELETE OPERATIONS ────────────────────────────────────────────────────
    
    def delete_file(self, s3_key: str) -> bool:
        """
        Delete file from S3
        
        Args:
            s3_key: S3 object key
            
        Returns:
            bool: True if successful
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            logger.info(f"[ChecklistS3] ✅ Deleted: {s3_key}")
            return True
            
        except Exception as e:
            logger.error(f"[ChecklistS3] ❌ Delete failed for {s3_key}: {e}")
            return False
    
    def delete_project_files(self, project_code: str) -> Dict:
        """
        Delete all files for a project
        
        Args:
            project_code: Project code
            
        Returns:
            dict: {'deleted_count': int, 'errors': list}
        """
        try:
            prefix = f"electrical_checklist/{project_code}/"
            
            # List all objects with prefix
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix
            )
            
            if 'Contents' not in response:
                return {'deleted_count': 0, 'errors': []}
            
            # Delete all objects
            objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]
            
            if objects_to_delete:
                delete_response = self.s3_client.delete_objects(
                    Bucket=self.bucket_name,
                    Delete={'Objects': objects_to_delete}
                )
                
                deleted_count = len(delete_response.get('Deleted', []))
                errors = delete_response.get('Errors', [])
                
                logger.info(f"[ChecklistS3] ✅ Deleted {deleted_count} files for project {project_code}")
                
                return {
                    'deleted_count': deleted_count,
                    'errors': errors
                }
            
            return {'deleted_count': 0, 'errors': []}
            
        except Exception as e:
            logger.error(f"[ChecklistS3] ❌ Project deletion failed: {e}")
            return {
                'deleted_count': 0,
                'errors': [str(e)]
            }
    
    # ─── UTILITY FUNCTIONS ────────────────────────────────────────────────────
    
    def list_project_files(self, project_code: str, file_type: Optional[str] = None) -> List[Dict]:
        """
        List all files for a project
        
        Args:
            project_code: Project code
            file_type: Optional filter by file type ('pdf', 'excel', 'signatures')
            
        Returns:
            list: List of file metadata dicts
        """
        try:
            if file_type:
                prefix = self.get_folder_path(file_type, project_code)
            else:
                prefix = f"electrical_checklist/{project_code}/"
            
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix
            )
            
            if 'Contents' not in response:
                return []
            
            files = []
            for obj in response['Contents']:
                files.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'url': self.generate_presigned_url(obj['Key'])
                })
            
            return files
            
        except Exception as e:
            logger.error(f"[ChecklistS3] ❌ List files failed: {e}")
            return []
    
    def get_file_metadata(self, s3_key: str) -> Optional[Dict]:
        """
        Get file metadata
        
        Args:
            s3_key: S3 object key
            
        Returns:
            dict: File metadata or None
        """
        try:
            response = self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            return {
                'size': response['ContentLength'],
                'content_type': response.get('ContentType'),
                'last_modified': response['LastModified'].isoformat(),
                'metadata': response.get('Metadata', {}),
                'url': self.generate_presigned_url(s3_key)
            }
            
        except Exception as e:
            logger.error(f"[ChecklistS3] ❌ Get metadata failed: {e}")
            return None


# ─── SINGLETON INSTANCE ───────────────────────────────────────────────────────
# Create a single instance to be reused across the application
_s3_service_instance = None

def get_s3_service() -> ChecklistS3Service:
    """Get or create S3 service singleton instance"""
    global _s3_service_instance
    if _s3_service_instance is None:
        _s3_service_instance = ChecklistS3Service()
    return _s3_service_instance
