"""
AWS S3 integration service for secure file storage and access.
Provides pre-signed URLs for secure uploads/downloads with user tracking.
"""
import boto3
import hashlib
import mimetypes
from datetime import datetime, timedelta
from django.conf import settings
from botocore.exceptions import ClientError
from apps.rbac.models import UserStorage
from apps.rbac.utils import create_audit_log


class S3Service:
    """
    AWS S3 service for secure file operations.
    Implements pre-signed URLs for direct client uploads/downloads.
    """
    
    def __init__(self, organization=None):
        """
        Initialize S3 client with organization-specific bucket.
        
        Args:
            organization: Organization model instance (optional, uses default if not provided)
        """
        self.organization = organization
        self.bucket = getattr(organization, 's3_bucket_name', None) or settings.AWS_STORAGE_BUCKET_NAME
        self.region = getattr(organization, 's3_region', None) or settings.AWS_S3_REGION_NAME
        
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=self.region
        )
    
    def generate_upload_url(self, user, file_name, file_size, content_type=None, 
                          expires_in=3600, tags=None, category='general'):
        """
        Generate pre-signed URL for direct upload to S3.
        
        Args:
            user: User uploading the file
            file_name: Name of the file
            file_size: Size in bytes
            content_type: MIME type (auto-detected if not provided)
            expires_in: URL expiration in seconds (default 1 hour)
            tags: Dict of custom tags
            category: File category (general, pid_analysis, reports, etc.)
        
        Returns:
            dict: {
                'upload_url': str,
                'file_key': str,
                'storage_id': str,
                'expires_at': datetime
            }
        """
        # Generate unique S3 key
        timestamp = datetime.utcnow().strftime('%Y/%m/%d')
        unique_id = hashlib.md5(f"{user.id}{file_name}{datetime.utcnow()}".encode()).hexdigest()[:12]
        file_key = f"users/{user.id}/{category}/{timestamp}/{unique_id}_{file_name}"
        
        # Auto-detect content type
        if not content_type:
            content_type, _ = mimetypes.guess_type(file_name)
            content_type = content_type or 'application/octet-stream'
        
        # Create UserStorage record
        storage = UserStorage.objects.create(
            user_profile=user.userprofile,
            organization=user.userprofile.organization,
            file_name=file_name,
            file_key=file_key,
            file_size=file_size,
            mime_type=content_type,
            category=category,
            tags=tags or {}
        )
        
        # Generate pre-signed POST URL
        try:
            conditions = [
                {'bucket': self.bucket},
                ['content-length-range', file_size, file_size],
            ]
            
            fields = {
                'Content-Type': content_type,
                'x-amz-meta-user-id': str(user.id),
                'x-amz-meta-storage-id': str(storage.id),
                'x-amz-meta-category': category,
            }
            
            presigned_post = self.s3_client.generate_presigned_post(
                Bucket=self.bucket,
                Key=file_key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=expires_in
            )
            
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            # Audit log
            create_audit_log(
                user=user,
                action='generate_upload_url',
                resource_type='storage',
                resource_id=str(storage.id),
                details={'file_name': file_name, 'file_size': file_size, 'category': category}
            )
            
            return {
                'upload_url': presigned_post['url'],
                'fields': presigned_post['fields'],
                'file_key': file_key,
                'storage_id': str(storage.id),
                'expires_at': expires_at.isoformat()
            }
            
        except ClientError as e:
            storage.delete()
            raise Exception(f"Failed to generate upload URL: {str(e)}")
    
    def generate_download_url(self, storage_id, user, expires_in=3600):
        """
        Generate pre-signed URL for downloading a file.
        
        Args:
            storage_id: UUID of UserStorage record
            user: User requesting download
            expires_in: URL expiration in seconds (default 1 hour)
        
        Returns:
            dict: {
                'download_url': str,
                'file_name': str,
                'file_size': int,
                'expires_at': datetime
            }
        """
        try:
            storage = UserStorage.objects.get(id=storage_id)
            
            # Permission check: user must own file or be same organization
            if storage.user_profile.user != user and storage.organization != user.userprofile.organization:
                raise PermissionError("You don't have permission to access this file")
            
            # Generate pre-signed URL
            download_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket,
                    'Key': storage.file_key,
                    'ResponseContentDisposition': f'attachment; filename="{storage.file_name}"'
                },
                ExpiresIn=expires_in
            )
            
            # Update download tracking
            storage.download_count += 1
            storage.last_downloaded_at = datetime.utcnow()
            storage.save(update_fields=['download_count', 'last_downloaded_at', 'updated_at'])
            
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            # Audit log
            create_audit_log(
                user=user,
                action='generate_download_url',
                resource_type='storage',
                resource_id=str(storage.id),
                details={'file_name': storage.file_name, 'file_key': storage.file_key}
            )
            
            return {
                'download_url': download_url,
                'file_name': storage.file_name,
                'file_size': storage.file_size,
                'mime_type': storage.mime_type,
                'expires_at': expires_at.isoformat()
            }
            
        except UserStorage.DoesNotExist:
            raise Exception("File not found")
        except ClientError as e:
            raise Exception(f"Failed to generate download URL: {str(e)}")
    
    def delete_file(self, storage_id, user):
        """
        Delete a file from S3 and mark storage record as deleted.
        
        Args:
            storage_id: UUID of UserStorage record
            user: User requesting deletion
        
        Returns:
            bool: True if successful
        """
        try:
            storage = UserStorage.objects.get(id=storage_id)
            
            # Permission check
            if storage.user_profile.user != user and not user.userprofile.has_permission('file_delete'):
                raise PermissionError("You don't have permission to delete this file")
            
            # Delete from S3
            self.s3_client.delete_object(
                Bucket=self.bucket,
                Key=storage.file_key
            )
            
            # Soft delete storage record
            storage.is_deleted = True
            storage.save()
            
            # Audit log
            create_audit_log(
                user=user,
                action='delete_file',
                resource_type='storage',
                resource_id=str(storage.id),
                details={'file_name': storage.file_name, 'file_key': storage.file_key}
            )
            
            return True
            
        except UserStorage.DoesNotExist:
            raise Exception("File not found")
        except ClientError as e:
            raise Exception(f"Failed to delete file: {str(e)}")
    
    def verify_upload(self, storage_id, user, checksum=None):
        """
        Verify that file was successfully uploaded to S3.
        
        Args:
            storage_id: UUID of UserStorage record
            user: User who uploaded
            checksum: Optional MD5 checksum to verify
        
        Returns:
            bool: True if file exists and matches checksum
        """
        try:
            storage = UserStorage.objects.get(id=storage_id)
            
            # Check if file exists in S3
            response = self.s3_client.head_object(
                Bucket=self.bucket,
                Key=storage.file_key
            )
            
            # Verify checksum if provided
            if checksum:
                s3_etag = response['ETag'].strip('"')
                if s3_etag != checksum:
                    raise Exception("Checksum mismatch - file corrupted")
                storage.checksum = checksum
            
            # Update storage record
            storage.uploaded_at = datetime.utcnow()
            storage.save()
            
            # Audit log
            create_audit_log(
                user=user,
                action='verify_upload',
                resource_type='storage',
                resource_id=str(storage.id),
                details={'file_name': storage.file_name, 'verified': True}
            )
            
            return True
            
        except UserStorage.DoesNotExist:
            raise Exception("Storage record not found")
        except ClientError as e:
            raise Exception(f"File verification failed: {str(e)}")
    
    def get_storage_stats(self, user):
        """
        Get storage statistics for a user.
        
        Returns:
            dict: Storage usage statistics
        """
        from django.db.models import Sum, Count
        
        user_storage = UserStorage.objects.filter(
            user_profile__user=user,
            is_deleted=False
        )
        
        stats = user_storage.aggregate(
            total_files=Count('id'),
            total_size=Sum('file_size'),
            total_downloads=Sum('download_count')
        )
        
        # Default storage limits (500GB)
        max_storage_gb = 500
        max_storage_bytes = max_storage_gb * 1024 * 1024 * 1024
        
        return {
            'total_files': stats['total_files'] or 0,
            'total_size_bytes': stats['total_size'] or 0,
            'total_size_mb': round((stats['total_size'] or 0) / (1024 * 1024), 2),
            'total_downloads': stats['total_downloads'] or 0,
            'max_storage_bytes': max_storage_bytes,
            'max_storage_gb': max_storage_gb,
            'usage_percent': round(((stats['total_size'] or 0) / max_storage_bytes) * 100, 2) if max_storage_bytes > 0 else 0
        }
