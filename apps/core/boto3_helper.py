"""
Boto3 Connection Helper
Provides reusable boto3 clients with proper error handling
"""
import boto3
import os
import logging
from typing import Optional, Dict, Any
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError
from botocore.config import Config

logger = logging.getLogger(__name__)


class Boto3Helper:
    """
    Centralized boto3 connection helper with retry logic and error handling
    """
    
    # Connection pool for reusing clients
    _clients = {}
    _resources = {}
    
    @classmethod
    def get_s3_client(
        cls,
        region: Optional[str] = None,
        bucket_specific: bool = False,
        **kwargs
    ) -> boto3.client:
        """
        Get or create S3 client with proper configuration
        
        Args:
            region: AWS region (defaults to AWS_S3_REGION_NAME or us-east-1)
            bucket_specific: If True, uses PFD bucket credentials
            **kwargs: Additional boto3 client parameters
            
        Returns:
            Configured boto3 S3 client
        """
        # Determine region
        if not region:
            if bucket_specific:
                region = os.environ.get('PFD_S3_REGION', 'ap-south-1')
            else:
                region = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
        
        # Check if client already exists
        cache_key = f"s3_{region}_{bucket_specific}"
        if cache_key in cls._clients:
            return cls._clients[cache_key]
        
        # Get credentials
        access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        
        if not access_key or not secret_key:
            raise NoCredentialsError()
        
        # Configure client with retry logic
        config = Config(
            region_name=region,
            retries={
                'max_attempts': 3,
                'mode': 'adaptive'
            },
            connect_timeout=10,
            read_timeout=30,
            **kwargs.pop('config', {})
        )
        
        try:
            client = boto3.client(
                's3',
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=config,
                **kwargs
            )
            
            # Cache the client
            cls._clients[cache_key] = client
            
            logger.info(f"S3 client created successfully for region: {region}")
            return client
            
        except Exception as e:
            logger.error(f"Failed to create S3 client: {e}")
            raise
    
    @classmethod
    def get_s3_resource(
        cls,
        region: Optional[str] = None,
        **kwargs
    ) -> boto3.resource:
        """
        Get or create S3 resource with proper configuration
        
        Args:
            region: AWS region
            **kwargs: Additional boto3 resource parameters
            
        Returns:
            Configured boto3 S3 resource
        """
        if not region:
            region = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
        
        cache_key = f"s3_resource_{region}"
        if cache_key in cls._resources:
            return cls._resources[cache_key]
        
        access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        
        if not access_key or not secret_key:
            raise NoCredentialsError()
        
        try:
            resource = boto3.resource(
                's3',
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                **kwargs
            )
            
            cls._resources[cache_key] = resource
            
            logger.info(f"S3 resource created successfully for region: {region}")
            return resource
            
        except Exception as e:
            logger.error(f"Failed to create S3 resource: {e}")
            raise
    
    @classmethod
    def test_bucket_access(cls, bucket_name: str, region: Optional[str] = None) -> Dict[str, Any]:
        """
        Test access to a specific bucket
        
        Args:
            bucket_name: Name of the bucket to test
            region: AWS region
            
        Returns:
            Dict with test results
        """
        result = {
            'accessible': False,
            'exists': False,
            'has_read_permission': False,
            'has_write_permission': False,
            'error': None
        }
        
        try:
            client = cls.get_s3_client(region=region)
            
            # Test 1: Check if bucket exists
            try:
                client.head_bucket(Bucket=bucket_name)
                result['exists'] = True
                result['accessible'] = True
            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code == '404':
                    result['error'] = f"Bucket '{bucket_name}' not found"
                    return result
                elif error_code == '403':
                    result['exists'] = True
                    result['error'] = f"Access denied to bucket '{bucket_name}'"
                    return result
                else:
                    result['error'] = str(e)
                    return result
            
            # Test 2: Check read permission
            try:
                client.list_objects_v2(Bucket=bucket_name, MaxKeys=1)
                result['has_read_permission'] = True
            except ClientError as e:
                logger.warning(f"Read test failed: {e}")
            
            # Test 3: Check write permission (metadata only, no actual upload)
            try:
                test_key = f"test/.boto3_access_test"
                client.put_object(
                    Bucket=bucket_name,
                    Key=test_key,
                    Body=b'test',
                    ServerSideEncryption='AES256'
                )
                result['has_write_permission'] = True
                
                # Cleanup
                try:
                    client.delete_object(Bucket=bucket_name, Key=test_key)
                except:
                    pass
                    
            except ClientError as e:
                logger.warning(f"Write test failed: {e}")
            
        except NoCredentialsError:
            result['error'] = "AWS credentials not configured"
        except Exception as e:
            result['error'] = str(e)
        
        return result
    
    @classmethod
    def validate_connection(cls) -> Dict[str, Any]:
        """
        Validate boto3 connection and return status
        
        Returns:
            Dict with validation results
        """
        results = {
            'boto3_installed': False,
            'credentials_configured': False,
            'main_bucket_accessible': False,
            'pfd_bucket_accessible': False,
            'details': {}
        }
        
        # Check boto3 installation
        try:
            import boto3
            results['boto3_installed'] = True
            results['details']['boto3_version'] = boto3.__version__
        except ImportError:
            results['details']['error'] = "boto3 not installed"
            return results
        
        # Check credentials
        access_key = os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
        
        if access_key and secret_key:
            results['credentials_configured'] = True
            results['details']['access_key'] = f"{access_key[:8]}...{access_key[-4:]}"
        else:
            results['details']['credentials_error'] = "AWS credentials not set"
            return results
        
        # Test main bucket
        main_bucket = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'user-management-rejlers')
        main_region = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
        
        main_test = cls.test_bucket_access(main_bucket, main_region)
        results['main_bucket_accessible'] = main_test['accessible']
        results['details']['main_bucket'] = {
            'name': main_bucket,
            'region': main_region,
            'status': main_test
        }
        
        # Test PFD bucket
        pfd_bucket = os.environ.get('PFD_S3_BUCKET', 'rejlers-edrs-project')
        pfd_region = os.environ.get('PFD_S3_REGION', 'ap-south-1')
        
        pfd_test = cls.test_bucket_access(pfd_bucket, pfd_region)
        results['pfd_bucket_accessible'] = pfd_test['accessible']
        results['details']['pfd_bucket'] = {
            'name': pfd_bucket,
            'region': pfd_region,
            'status': pfd_test
        }
        
        return results
    
    @classmethod
    def clear_cache(cls):
        """Clear cached clients and resources"""
        cls._clients.clear()
        cls._resources.clear()
        logger.info("Boto3 connection cache cleared")
    
    @classmethod
    def get_connection_info(cls) -> Dict[str, Any]:
        """
        Get current boto3 connection information
        
        Returns:
            Dict with connection details
        """
        return {
            'main_bucket': {
                'name': os.environ.get('AWS_STORAGE_BUCKET_NAME', 'user-management-rejlers'),
                'region': os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
            },
            'pfd_bucket': {
                'name': os.environ.get('PFD_S3_BUCKET', 'rejlers-edrs-project'),
                'region': os.environ.get('PFD_S3_REGION', 'ap-south-1')
            },
            'credentials_configured': bool(
                os.environ.get('AWS_ACCESS_KEY_ID') and 
                os.environ.get('AWS_SECRET_ACCESS_KEY')
            ),
            'cached_clients': len(cls._clients),
            'cached_resources': len(cls._resources)
        }


# Convenience functions
def get_s3_client(**kwargs):
    """Get S3 client using Boto3Helper"""
    return Boto3Helper.get_s3_client(**kwargs)


def get_s3_resource(**kwargs):
    """Get S3 resource using Boto3Helper"""
    return Boto3Helper.get_s3_resource(**kwargs)


def validate_boto3_connection():
    """Validate boto3 connection"""
    return Boto3Helper.validate_connection()


def test_s3_bucket(bucket_name: str, region: Optional[str] = None):
    """Test access to a specific S3 bucket"""
    return Boto3Helper.test_bucket_access(bucket_name, region)
