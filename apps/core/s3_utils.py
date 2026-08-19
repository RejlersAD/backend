"""
AWS S3 Utilities
Secure S3 operations using boto3 with best practices
"""
import logging
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from django.conf import settings
from typing import Optional, BinaryIO
import mimetypes

logger = logging.getLogger(__name__)


class S3Client:
    """
    Secure S3 client wrapper with best practices
    
    Features:
    - Environment-based configuration
    - IAM role support
    - Comprehensive error handling
    - Logging without exposing secrets
    - Presigned URL generation
    """
    
    def __init__(self):
        """
        Initialize S3 client
        
        Credentials are loaded in this order:
        1. Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
        2. IAM role (EC2, ECS, Lambda)
        3. AWS credentials file (~/.aws/credentials)
        """
        try:
            self.s3_client = boto3.client(
                's3',
                region_name=settings.AWS_S3_REGION_NAME,
                # Credentials are auto-detected from environment/IAM role
                # NEVER hardcode credentials here
            )
            self.bucket_name = settings.AWS_STORAGE_BUCKET_NAME
            logger.info(f"S3 client initialized for region: {settings.AWS_S3_REGION_NAME}")
        except NoCredentialsError:
            logger.error("AWS credentials not found. Set environment variables or use IAM role.")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize S3 client: {type(e).__name__}: {str(e)}")
            raise
    
    def upload_file(
        self,
        file_obj: BinaryIO,
        s3_key: str,
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> bool:
        """
        Upload file to S3 with security best practices
        
        Args:
            file_obj: File object to upload
            s3_key: S3 object key (path in bucket)
            content_type: MIME type of file
            metadata: Additional metadata
        
        Returns:
            True if upload successful, False otherwise
        """
        try:
            # Auto-detect content type if not provided
            if not content_type:
                content_type, _ = mimetypes.guess_type(s3_key)
                content_type = content_type or 'application/octet-stream'
            
            # Prepare upload parameters
            extra_args = {
                'ContentType': content_type,
                'ServerSideEncryption': 'AES256',  # Enable server-side encryption
            }
            
            # Add custom metadata
            if metadata:
                extra_args['Metadata'] = {k: str(v) for k, v in metadata.items()}
            
            # Reset file pointer to beginning
            file_obj.seek(0)
            
            # Upload to S3
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                s3_key,
                ExtraArgs=extra_args
            )
            
            logger.info(f"Successfully uploaded file to S3: s3://{self.bucket_name}/{s3_key}")
            return True
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            logger.error(f"S3 upload failed ({error_code}): {s3_key}")
            # Don't log full exception (might contain sensitive info)
            return False
        except Exception as e:
            logger.error(f"Unexpected error during S3 upload: {type(e).__name__}")
            return False
    
    def download_file(self, s3_key: str, file_obj: BinaryIO) -> bool:
        """
        Download file from S3
        
        Args:
            s3_key: S3 object key
            file_obj: File object to write to
        
        Returns:
            True if download successful, False otherwise
        """
        try:
            self.s3_client.download_fileobj(
                self.bucket_name,
                s3_key,
                file_obj
            )
            logger.info(f"Successfully downloaded file from S3: {s3_key}")
            return True
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchKey':
                logger.warning(f"File not found in S3: {s3_key}")
            else:
                logger.error(f"S3 download failed ({error_code}): {s3_key}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during S3 download: {type(e).__name__}")
            return False
    
    def generate_presigned_url(
        self,
        s3_key: str,
        expiration: int = 3600,
        http_method: str = 'get_object'
    ) -> Optional[str]:
        """
        Generate presigned URL for temporary secure access
        
        Args:
            s3_key: S3 object key
            expiration: URL expiration time in seconds (default: 1 hour)
            http_method: 'get_object' for download, 'put_object' for upload
        
        Returns:
            Presigned URL or None if failed
        """
        try:
            url = self.s3_client.generate_presigned_url(
                http_method,
                Params={
                    'Bucket': self.bucket_name,
                    'Key': s3_key
                },
                ExpiresIn=expiration
            )
            logger.info(f"Generated presigned URL for {s3_key} (expires in {expiration}s)")
            return url
            
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL: {e.response['Error']['Code']}")
            return None
    
    def delete_file(self, s3_key: str) -> bool:
        """
        Delete file from S3
        
        Args:
            s3_key: S3 object key
        
        Returns:
            True if deletion successful, False otherwise
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            logger.info(f"Successfully deleted file from S3: {s3_key}")
            return True
            
        except ClientError as e:
            logger.error(f"S3 deletion failed: {e.response['Error']['Code']}")
            return False
    
    def file_exists(self, s3_key: str) -> bool:
        """
        Check if file exists in S3
        
        Args:
            s3_key: S3 object key
        
        Returns:
            True if file exists, False otherwise
        """
        try:
            self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            logger.error(f"Error checking file existence: {e.response['Error']['Code']}")
            return False
    
    def get_file_size(self, s3_key: str) -> Optional[int]:
        """
        Get file size in bytes
        
        Args:
            s3_key: S3 object key
        
        Returns:
            File size in bytes or None if failed
        """
        try:
            response = self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            return response['ContentLength']
        except ClientError:
            return None
    
    def list_files(self, prefix: str, max_keys: int = 1000) -> list:
        """
        List files in S3 with given prefix
        
        Args:
            prefix: S3 key prefix (folder path)
            max_keys: Maximum number of files to return
        
        Returns:
            List of file keys
        """
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            
            if 'Contents' not in response:
                return []
            
            return [obj['Key'] for obj in response['Contents']]
            
        except ClientError as e:
            logger.error(f"S3 list failed: {e.response['Error']['Code']}")
            return []


# Convenience function for quick S3 operations
def get_s3_client() -> S3Client:
    """Get configured S3 client instance"""
    return S3Client()
