"""
S3 PFD Reference Service
========================

Download and use reference PFDs from AWS S3 bucket for:
- Testing extraction algorithms
- Providing sample data
- Learning from existing project PFDs
"""

import boto3
from botocore.exceptions import ClientError
from decouple import config
import os
import json
import logging
from django.conf import settings
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class S3PFDService:
    """Service to access reference PFDs from S3 bucket"""
    
    def __init__(self):
        # Use the configured bucket name directly
        self.bucket_name = config('AWS_STORAGE_BUCKET_NAME', default='user-management-rejlers')
        self.region = config('AWS_S3_REGION_NAME', default='us-east-1')
        self.access_key = config('AWS_ACCESS_KEY_ID', default='')
        self.secret_key = config('AWS_SECRET_ACCESS_KEY', default='')
        
        logger.info(f"🔧 S3 Service Config:")
        logger.info(f"   Bucket: {self.bucket_name}")
        logger.info(f"   Region: {self.region}")
        logger.info(f"   Access Key: {self.access_key[:8]}... (masked)")
        
        # Check if S3 is configured
        self.s3_enabled = bool(self.access_key and self.secret_key)
        
        if self.s3_enabled:
            try:
                self.s3_client = boto3.client(
                    's3',
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key,
                    region_name=self.region
                )
                logger.info(f"✅ S3 PFD Service initialized - Bucket: {self.bucket_name}")
            except Exception as e:
                logger.error(f"❌ S3 initialization failed: {str(e)}")
                self.s3_enabled = False
        else:
            logger.warning("⚠️  S3 PFD Service disabled - AWS credentials not configured")
    
    def get_reference_pfds(self, project_code: str = None, limit: int = 10) -> List[Dict]:
        """
        Get list of reference PFDs from S3
        
        Args:
            project_code: Filter by specific project (e.g., 'P16093')
            limit: Maximum number of PFDs to return
            
        Returns:
            List of PFD metadata dictionaries
        """
        if not self.s3_enabled:
            return []
        
        try:
            # Load inventory file if exists
            inventory_path = os.path.join(settings.BASE_DIR, 's3_pdf_inventory.json')
            if os.path.exists(inventory_path):
                with open(inventory_path, 'r') as f:
                    inventory = json.load(f)
                
                # Filter for PFDs
                pfds = [
                    file for file in inventory.get('files', [])
                    if 'PFD' in file['key'] 
                    and 'Legend' not in file['key']
                    and 'legend' not in file['key']
                ]
                
                # Filter by project if specified
                if project_code:
                    pfds = [pfd for pfd in pfds if project_code in pfd['key']]
                
                # Sort by size (larger files likely have more content)
                pfds.sort(key=lambda x: x['size_mb'], reverse=True)
                
                return pfds[:limit]
            
            return []
            
        except Exception as e:
            logger.error(f"❌ Failed to get reference PFDs: {str(e)}")
            return []
    
    def download_pfd(self, s3_key: str, local_path: str = None) -> Optional[str]:
        """
        Download a PFD from S3 to local storage
        
        Args:
            s3_key: S3 object key
            local_path: Local path to save file (optional)
            
        Returns:
            Path to downloaded file or None if failed
        """
        if not self.s3_enabled:
            logger.warning("⚠️  S3 not enabled - cannot download PFD")
            return None
        
        try:
            # Generate local path if not provided
            if not local_path:
                filename = os.path.basename(s3_key)
                local_path = os.path.join(settings.MEDIA_ROOT, 'pfd_temp', 's3_downloads', filename)
            
            # Create directory
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # Download file
            logger.info(f"⬇️  Downloading PFD from S3: {s3_key}")
            self.s3_client.download_file(
                self.bucket_name,
                s3_key,
                local_path
            )
            
            logger.info(f"✅ Downloaded to: {local_path}")
            return local_path
            
        except ClientError as e:
            logger.error(f"❌ S3 download failed: {str(e)}")
            return None
    
    def get_sample_pfd(self, project: str = 'P16093') -> Optional[str]:
        """
        Download a sample PFD for testing
        
        Args:
            project: Project code (default: P16093 - SAHIL)
            
        Returns:
            Path to downloaded sample PFD
        """
        # Get available PFDs for project
        pfds = self.get_reference_pfds(project_code=project, limit=1)
        
        if not pfds:
            logger.warning(f"⚠️  No PFDs found for project {project}")
            return None
        
        # Download first PFD
        return self.download_pfd(pfds[0]['key'])
    
    def list_available_projects(self) -> List[str]:
        """Get list of projects with PFDs in S3"""
        if not self.s3_enabled:
            return []
        
        try:
            inventory_path = os.path.join(settings.BASE_DIR, 's3_pdf_inventory.json')
            if os.path.exists(inventory_path):
                with open(inventory_path, 'r') as f:
                    inventory = json.load(f)
                
                # Extract unique project codes
                projects = set()
                for file in inventory.get('files', []):
                    if 'PFD' in file['key']:
                        # Extract project code patterns (e.g., P16093, 5900702)
                        import re
                        matches = re.findall(r'(P\d{5}|\d{7})', file['key'])
                        projects.update(matches)
                
                return sorted(list(projects))
            
            return []
            
        except Exception as e:
            logger.error(f"❌ Failed to list projects: {str(e)}")
            return []
