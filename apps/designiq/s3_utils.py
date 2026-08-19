"""
S3 Utilities for P&ID Document Storage
Handles uploading and retrieving P&ID PDFs from AWS S3
"""
import os
import logging
import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from decouple import config

logger = logging.getLogger(__name__)


class S3DocumentStorage:
    """Manages P&ID document storage in AWS S3"""
    
    def __init__(self):
        """Initialize S3 client with credentials from environment"""
        # Standardized bucket with backward compatibility
        self.bucket_name = config('AWS_STORAGE_BUCKET_NAME', default='user-management-rejlers')
        self.region = config('AWS_S3_REGION_NAME', default='us-east-1')
        
        # Initialize S3 client
        try:
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=config('AWS_ACCESS_KEY_ID', default=''),
                aws_secret_access_key=config('AWS_SECRET_ACCESS_KEY', default=''),
                region_name=self.region
            )
            logger.info(f"✅ S3 client initialized for bucket: {self.bucket_name}")
        except Exception as e:
            logger.error(f"❌ Failed to initialize S3 client: {e}")
            self.s3_client = None
    
    def is_enabled(self):
        """Check if S3 storage is properly configured"""
        return (
            self.s3_client is not None and
            config('AWS_ACCESS_KEY_ID', default='') != '' and
            config('AWS_SECRET_ACCESS_KEY', default='')
        )
    
    def upload_document(self, file_obj, document_id, original_filename):
        """
        Upload P&ID document to S3
        
        Args:
            file_obj: Django UploadedFile object
            document_id: Unique document ID (e.g., "0001-drawing.pdf")
            original_filename: Original filename
        
        Returns:
            dict: {'s3_key': str, 's3_url': str, 'success': bool, 'error': str|None}
        """
        if not self.is_enabled():
            logger.warning("⚠️ S3 storage not configured - skipping upload")
            return {
                'success': False,
                'error': 'S3 storage not configured',
                's3_key': None,
                's3_url': None
            }
        
        try:
            # Generate S3 key: pid_documents/YYYY/MM/DD/document_id
            from django.utils import timezone
            now = timezone.now()
            s3_key = f"pid_documents/{now.strftime('%Y/%m/%d')}/{document_id}"
            
            # Reset file pointer to beginning
            file_obj.seek(0)
            
            # Upload to S3 with metadata
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                s3_key,
                ExtraArgs={
                    'ContentType': 'application/pdf',
                    'ContentDisposition': f'inline; filename="{original_filename}"',
                    'Metadata': {
                        'original-filename': original_filename,
                        'document-id': document_id,
                        'upload-date': now.isoformat()
                    }
                }
            )
            
            # Generate S3 URL
            s3_url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{s3_key}"
            
            logger.info(f"✅ Uploaded {document_id} to S3: {s3_key}")
            
            return {
                'success': True,
                'error': None,
                's3_key': s3_key,
                's3_url': s3_url
            }
            
        except ClientError as e:
            error_msg = f"S3 upload failed: {e.response['Error']['Message']}"
            logger.error(f"❌ {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                's3_key': None,
                's3_url': None
            }
        except Exception as e:
            error_msg = f"S3 upload failed: {str(e)}"
            logger.error(f"❌ {error_msg}", exc_info=True)
            return {
                'success': False,
                'error': error_msg,
                's3_key': None,
                's3_url': None
            }
    
    def generate_presigned_url(self, s3_key, expiration=3600):
        """
        Generate a presigned URL for temporary access to S3 object
        
        Args:
            s3_key: S3 object key
            expiration: URL expiration time in seconds (default 1 hour)
        
        Returns:
            str: Presigned URL or None if failed
        """
        if not self.is_enabled():
            return None
        
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
            logger.error(f"❌ Failed to generate presigned URL: {e}")
            return None
    
    def get_document(self, s3_key):
        """
        Download document from S3
        
        Args:
            s3_key: S3 object key
        
        Returns:
            bytes: File content or None if failed
        """
        if not self.is_enabled():
            return None
        
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            return response['Body'].read()
        except ClientError as e:
            logger.error(f"❌ Failed to download from S3: {e}")
            return None
    
    def delete_document(self, s3_key):
        """
        Delete document from S3
        
        Args:
            s3_key: S3 object key
        
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.is_enabled():
            return False
        
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            logger.info(f"✅ Deleted from S3: {s3_key}")
            return True
        except ClientError as e:
            logger.error(f"❌ Failed to delete from S3: {e}")
            return False


# Global instance
s3_storage = S3DocumentStorage()
