"""
CRS Template Manager - AWS S3 Integration
Handles ADNOC Onshore/Offshore template storage and retrieval
"""

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import logging
from typing import List, Dict, Optional
from io import BytesIO
import os

logger = logging.getLogger(__name__)

# Template S3 Configuration - Loaded from environment variables for security
TEMPLATE_S3_CONFIG = {
    'aws_access_key_id': os.getenv('CRS_TEMPLATE_AWS_ACCESS_KEY_ID', ''),
    'aws_secret_access_key': os.getenv('CRS_TEMPLATE_AWS_SECRET_ACCESS_KEY', ''),
    'region_name': os.getenv('CRS_TEMPLATE_AWS_REGION', 'us-east-1'),
    'bucket_name': os.getenv('CRS_TEMPLATE_BUCKET_NAME', 'radai-templates'),
}

# Project type configuration - Soft-coded for easy expansion
PROJECT_TYPES = {
    'adnoc_onshore': {
        'label': 'ADNOC Onshore',
        'code': 'adnoc_onshore',
        's3_prefix': 'templates/crs/adnoc_onshore/',
        'icon': '🏜️',
    },
    'adnoc_offshore': {
        'label': 'ADNOC Offshore',
        'code': 'adnoc_offshore',
        's3_prefix': 'templates/crs/adnoc_offshore/',
        'icon': '🌊',
    },
}


class CRSTemplateManager:
    """Manages CRS templates stored in AWS S3"""
    
    def __init__(self):
        """Initialize S3 client with template credentials"""
        try:
            self.s3_client = boto3.client(
                's3',
                aws_access_key_id=TEMPLATE_S3_CONFIG['aws_access_key_id'],
                aws_secret_access_key=TEMPLATE_S3_CONFIG['aws_secret_access_key'],
                region_name=TEMPLATE_S3_CONFIG['region_name']
            )
            self.bucket_name = TEMPLATE_S3_CONFIG['bucket_name']
            logger.info(f"✅ CRS Template Manager initialized with bucket: {self.bucket_name}")
        except Exception as e:
            logger.error(f"❌ Failed to initialize S3 client: {e}")
            self.s3_client = None
            self.bucket_name = None
    
    def get_project_types(self) -> List[Dict]:
        """
        Get list of available project types
        Returns: List of project type configurations
        """
        return [
            {
                'code': code,
                'label': config['label'],
                'icon': config['icon'],
            }
            for code, config in PROJECT_TYPES.items()
        ]
    
    def list_templates(self, project_type: str) -> List[Dict]:
        """
        List available templates for a specific project type
        
        Args:
            project_type: Project type code (e.g., 'adnoc_onshore')
        
        Returns:
            List of template metadata dicts
        """
        if not self.s3_client:
            logger.error("❌ S3 client not initialized")
            return []
        
        if project_type not in PROJECT_TYPES:
            logger.error(f"❌ Invalid project type: {project_type}")
            return []
        
        s3_prefix = PROJECT_TYPES[project_type]['s3_prefix']
        templates = []
        
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=s3_prefix
            )
            
            if 'Contents' not in response:
                logger.warning(f"⚠️ No templates found for {project_type} in {s3_prefix}")
                return []
            
            for obj in response['Contents']:
                # Skip directory markers
                if obj['Key'].endswith('/'):
                    continue
                
                # Extract filename from S3 key
                filename = obj['Key'].split('/')[-1]
                
                # Only include Excel templates
                if filename.lower().endswith(('.xls', '.xlsx')):
                    templates.append({
                        'key': obj['Key'],
                        'filename': filename,
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'].isoformat(),
                        'project_type': project_type,
                    })
            
            logger.info(f"✅ Found {len(templates)} templates for {project_type}")
            return templates
            
        except ClientError as e:
            logger.error(f"❌ S3 ClientError listing templates: {e}")
            return []
        except NoCredentialsError:
            logger.error("❌ AWS credentials not available")
            return []
        except Exception as e:
            logger.error(f"❌ Error listing templates: {e}")
            return []
    
    def get_template(self, s3_key: str) -> Optional[BytesIO]:
        """
        Download template file from S3
        
        Args:
            s3_key: S3 object key (full path)
        
        Returns:
            BytesIO buffer with template content, or None if error
        """
        if not self.s3_client:
            logger.error("❌ S3 client not initialized")
            return None
        
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            template_buffer = BytesIO(response['Body'].read())
            logger.info(f"✅ Downloaded template: {s3_key}")
            return template_buffer
            
        except ClientError as e:
            logger.error(f"❌ S3 ClientError downloading template: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Error downloading template: {e}")
            return None
    
    def get_presigned_url(self, s3_key: str, expiration: int = 3600) -> Optional[str]:
        """
        Generate presigned URL for direct download
        
        Args:
            s3_key: S3 object key
            expiration: URL expiration time in seconds (default 1 hour)
        
        Returns:
            Presigned URL string or None if error
        """
        if not self.s3_client:
            logger.error("❌ S3 client not initialized")
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
            logger.info(f"✅ Generated presigned URL for: {s3_key}")
            return url
            
        except ClientError as e:
            logger.error(f"❌ Error generating presigned URL: {e}")
            return None
    
    def upload_template(self, file_content: bytes, project_type: str, filename: str) -> bool:
        """
        Upload a new template to S3
        
        Args:
            file_content: Template file bytes
            project_type: Project type code
            filename: Template filename
        
        Returns:
            True if successful, False otherwise
        """
        if not self.s3_client:
            logger.error("❌ S3 client not initialized")
            return False
        
        if project_type not in PROJECT_TYPES:
            logger.error(f"❌ Invalid project type: {project_type}")
            return False
        
        s3_prefix = PROJECT_TYPES[project_type]['s3_prefix']
        s3_key = f"{s3_prefix}{filename}"
        
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=file_content,
                ContentType='application/vnd.ms-excel'
            )
            logger.info(f"✅ Uploaded template: {s3_key}")
            return True
            
        except ClientError as e:
            logger.error(f"❌ Error uploading template: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error uploading template: {e}")
            return False


# Global instance
template_manager = CRSTemplateManager()
