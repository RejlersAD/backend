"""
AWS S3 Service
Comprehensive S3 operations for media and static files management
"""
import boto3
import os
import uuid
import mimetypes
from datetime import datetime
from django.conf import settings
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger(__name__)


class S3Service:
    """
    Centralized S3 service for all file operations
    Handles uploads, downloads, deletions, and presigned URLs
    """
    
    # Folder structure mapping
    FOLDERS = {
        'pid_drawings': 'media/pid_drawings/',
        'pid_reports': 'media/pid_reports/',
        'crs_documents': 'media/crs_documents/',
        'pfd_files': 'media/pfd_files/',
        'avatars': 'media/avatars/',
        'exports': 'media/exports/',
        'temp': 'media/temp/',
        'static_css': 'static/css/',
        'static_js': 'static/js/',
        'static_images': 'static/images/',
        'static_fonts': 'static/fonts/',
        'backups_db': 'backups/database/',
        'backups_reports': 'backups/reports/',
        'logs': 'logs/',
    }
    
    def __init__(self):
        """Initialize S3 client with credentials from environment"""
        self.bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'user-management-rejlers')
        self.region = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
        
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
        
        logger.info(f"[S3Service] Initialized with bucket: {self.bucket_name}, region: {self.region}")
    
    def _get_folder(self, folder_type: str) -> str:
        """Get the S3 folder path for a given type"""
        return self.FOLDERS.get(folder_type, 'media/')
    
    def _generate_unique_filename(self, original_filename: str) -> str:
        """Generate a unique filename with timestamp and UUID"""
        name, ext = os.path.splitext(original_filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        return f"{name}_{timestamp}_{unique_id}{ext}"
    
    def upload_file(self, file_obj, folder_type: str, filename: str = None, 
                    content_type: str = None, metadata: dict = None) -> dict:
        """
        Upload a file to S3
        
        Args:
            file_obj: File-like object or path to file
            folder_type: Type of folder (pid_drawings, crs_documents, etc.)
            filename: Optional custom filename
            content_type: Optional MIME type
            metadata: Optional metadata dictionary
            
        Returns:
            dict with upload details (key, url, size, etc.)
        """
        try:
            folder = self._get_folder(folder_type)
            
            # Generate unique filename if not provided
            if not filename:
                if hasattr(file_obj, 'name'):
                    filename = self._generate_unique_filename(file_obj.name)
                else:
                    filename = self._generate_unique_filename('file.bin')
            
            s3_key = f"{folder}{filename}"
            
            # Detect content type
            if not content_type:
                content_type, _ = mimetypes.guess_type(filename)
                content_type = content_type or 'application/octet-stream'
            
            # Prepare extra args
            extra_args = {
                'ContentType': content_type,
            }
            
            if metadata:
                extra_args['Metadata'] = metadata
            
            # Upload file
            if hasattr(file_obj, 'read'):
                # File-like object
                file_obj.seek(0)
                self.s3_client.upload_fileobj(
                    file_obj, 
                    self.bucket_name, 
                    s3_key,
                    ExtraArgs=extra_args
                )
                file_obj.seek(0)
                file_size = len(file_obj.read())
                file_obj.seek(0)
            else:
                # File path
                self.s3_client.upload_file(
                    file_obj, 
                    self.bucket_name, 
                    s3_key,
                    ExtraArgs=extra_args
                )
                file_size = os.path.getsize(file_obj)
            
            logger.info(f"[S3Service] Uploaded: {s3_key} ({file_size} bytes)")
            
            return {
                'success': True,
                'key': s3_key,
                'bucket': self.bucket_name,
                'region': self.region,
                'size': file_size,
                'content_type': content_type,
                'url': self.get_presigned_url(s3_key),
                's3_url': f"s3://{self.bucket_name}/{s3_key}",
                'https_url': f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{s3_key}"
            }
            
        except ClientError as e:
            logger.error(f"[S3Service] Upload failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def download_file(self, s3_key: str, local_path: str = None) -> dict:
        """
        Download a file from S3
        
        Args:
            s3_key: S3 object key
            local_path: Optional local path to save file
            
        Returns:
            dict with download details
        """
        try:
            if local_path:
                self.s3_client.download_file(self.bucket_name, s3_key, local_path)
                logger.info(f"[S3Service] Downloaded: {s3_key} to {local_path}")
                return {
                    'success': True,
                    'key': s3_key,
                    'local_path': local_path
                }
            else:
                # Return file object
                response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
                return {
                    'success': True,
                    'key': s3_key,
                    'body': response['Body'],
                    'content_type': response.get('ContentType'),
                    'size': response.get('ContentLength')
                }
                
        except ClientError as e:
            logger.error(f"[S3Service] Download failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def delete_file(self, s3_key: str) -> dict:
        """Delete a file from S3"""
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            logger.info(f"[S3Service] Deleted: {s3_key}")
            return {
                'success': True,
                'key': s3_key
            }
        except ClientError as e:
            logger.error(f"[S3Service] Delete failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def delete_folder(self, folder_type: str) -> dict:
        """Delete all files in a folder"""
        try:
            folder = self._get_folder(folder_type)
            bucket = self.s3_resource.Bucket(self.bucket_name)
            
            deleted_count = 0
            for obj in bucket.objects.filter(Prefix=folder):
                obj.delete()
                deleted_count += 1
            
            logger.info(f"[S3Service] Deleted {deleted_count} files from {folder}")
            return {
                'success': True,
                'folder': folder,
                'deleted_count': deleted_count
            }
        except ClientError as e:
            logger.error(f"[S3Service] Folder delete failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_presigned_url(self, s3_key: str, expiration: int = 3600) -> str:
        """
        Generate a presigned URL for secure file access
        
        Args:
            s3_key: S3 object key
            expiration: URL expiration time in seconds (default 1 hour)
            
        Returns:
            Presigned URL string
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
        except ClientError as e:
            logger.error(f"[S3Service] Presigned URL failed: {str(e)}")
            return None
    
    def get_presigned_upload_url(self, s3_key: str, content_type: str = None,
                                  expiration: int = 3600) -> dict:
        """
        Generate a presigned URL for direct browser uploads
        
        Args:
            s3_key: Target S3 object key
            content_type: Expected content type
            expiration: URL expiration time in seconds
            
        Returns:
            dict with presigned URL and fields
        """
        try:
            conditions = []
            fields = {}
            
            if content_type:
                conditions.append(['eq', '$Content-Type', content_type])
                fields['Content-Type'] = content_type
            
            response = self.s3_client.generate_presigned_post(
                Bucket=self.bucket_name,
                Key=s3_key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=expiration
            )
            
            return {
                'success': True,
                'url': response['url'],
                'fields': response['fields']
            }
        except ClientError as e:
            logger.error(f"[S3Service] Presigned upload URL failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def list_files(self, folder_type: str = None, prefix: str = None, 
                   max_keys: int = 1000) -> dict:
        """
        List files in S3 bucket
        
        Args:
            folder_type: Type of folder to list
            prefix: Custom prefix to filter
            max_keys: Maximum number of keys to return
            
        Returns:
            dict with file listing
        """
        try:
            if folder_type:
                prefix = self._get_folder(folder_type)
            elif not prefix:
                prefix = ''
            
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            
            files = []
            for obj in response.get('Contents', []):
                files.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'url': self.get_presigned_url(obj['Key'])
                })
            
            return {
                'success': True,
                'prefix': prefix,
                'count': len(files),
                'files': files
            }
        except ClientError as e:
            logger.error(f"[S3Service] List files failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def file_exists(self, s3_key: str) -> bool:
        """Check if a file exists in S3"""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError:
            return False
    
    def get_file_info(self, s3_key: str) -> dict:
        """Get file metadata from S3"""
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return {
                'success': True,
                'key': s3_key,
                'size': response['ContentLength'],
                'content_type': response.get('ContentType'),
                'last_modified': response['LastModified'].isoformat(),
                'metadata': response.get('Metadata', {}),
                'etag': response.get('ETag')
            }
        except ClientError as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def copy_file(self, source_key: str, dest_key: str) -> dict:
        """Copy a file within S3"""
        try:
            copy_source = {'Bucket': self.bucket_name, 'Key': source_key}
            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=self.bucket_name,
                Key=dest_key
            )
            logger.info(f"[S3Service] Copied: {source_key} to {dest_key}")
            return {
                'success': True,
                'source': source_key,
                'destination': dest_key
            }
        except ClientError as e:
            logger.error(f"[S3Service] Copy failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def move_file(self, source_key: str, dest_key: str) -> dict:
        """Move a file within S3 (copy then delete)"""
        copy_result = self.copy_file(source_key, dest_key)
        if copy_result['success']:
            delete_result = self.delete_file(source_key)
            if delete_result['success']:
                return {
                    'success': True,
                    'source': source_key,
                    'destination': dest_key
                }
        return copy_result
    
    def get_bucket_size(self) -> dict:
        """Get total size of all objects in bucket"""
        try:
            total_size = 0
            total_count = 0
            
            paginator = self.s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket_name):
                for obj in page.get('Contents', []):
                    total_size += obj['Size']
                    total_count += 1
            
            return {
                'success': True,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'total_count': total_count
            }
        except ClientError as e:
            return {
                'success': False,
                'error': str(e)
            }


# Singleton instance
_s3_service = None

def get_s3_service() -> S3Service:
    """Get or create S3 service singleton"""
    global _s3_service
    if _s3_service is None:
        _s3_service = S3Service()
    return _s3_service


# Convenience functions
def upload_to_s3(file_obj, folder_type: str, filename: str = None, **kwargs) -> dict:
    """Convenience function for file upload"""
    return get_s3_service().upload_file(file_obj, folder_type, filename, **kwargs)

def download_from_s3(s3_key: str, local_path: str = None) -> dict:
    """Convenience function for file download"""
    return get_s3_service().download_file(s3_key, local_path)

def get_s3_url(s3_key: str, expiration: int = 3600) -> str:
    """Convenience function for presigned URL"""
    return get_s3_service().get_presigned_url(s3_key, expiration)

def delete_from_s3(s3_key: str) -> dict:
    """Convenience function for file deletion"""
    return get_s3_service().delete_file(s3_key)

def list_s3_files(folder_type: str) -> dict:
    """Convenience function for listing files"""
    return get_s3_service().list_files(folder_type)
