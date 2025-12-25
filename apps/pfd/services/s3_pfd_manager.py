"""
AWS S3 PFD Manager
Manages PFD and P&ID files in the existing S3 bucket structure

Bucket Structure:
rejlers-edrs-project/
  PFD_to_PID/
    PFD/         - Original PFD files
    PID/         - Converted P&ID files
"""

import boto3
import os
import json
from datetime import datetime
from io import BytesIO
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class S3PFDManager:
    """
    Manages PFD and P&ID files in AWS S3
    Bucket: rejlers-edrs-project
    Region: ap-south-1
    """
    
    def __init__(self):
        """Initialize S3 client for the PFD bucket"""
        self.bucket_name = os.environ.get('PFD_S3_BUCKET', 'rejlers-edrs-project')
        self.region = os.environ.get('PFD_S3_REGION', 'ap-south-1')
        self.base_path = 'PFD_to_PID'
        self.pfd_folder = f'{self.base_path}/PFD'
        self.pid_folder = f'{self.base_path}/PID'
        
        # Initialize S3 client
        self.s3_client = boto3.client(
            's3',
            region_name=self.region,
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
        )
        
        logger.info(f"S3PFDManager initialized: bucket={self.bucket_name}, region={self.region}")
    
    def list_pfd_files(self, prefix: str = '', limit: int = 100) -> List[Dict]:
        """
        List all PFD files from the PFD folder
        
        Args:
            prefix: Optional prefix to filter files
            limit: Maximum number of files to return
            
        Returns:
            List of file metadata dicts
        """
        try:
            search_prefix = f"{self.pfd_folder}/"
            if prefix:
                search_prefix = f"{self.pfd_folder}/{prefix}"
            
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=search_prefix,
                MaxKeys=limit
            )
            
            files = []
            for obj in response.get('Contents', []):
                key = obj['Key']
                
                # Skip if it's the folder itself
                if key.endswith('/'):
                    continue
                
                # Extract filename
                filename = key.replace(f"{self.pfd_folder}/", '')
                
                # Check if corresponding P&ID exists
                pid_key = self._get_pid_key_for_pfd(key)
                has_pid_conversion = self._check_file_exists(pid_key)
                
                file_info = {
                    's3_key': key,
                    'filename': filename,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'has_pid_conversion': has_pid_conversion,
                    'pid_key': pid_key if has_pid_conversion else None,
                    'extension': os.path.splitext(filename)[1].lower()
                }
                
                files.append(file_info)
            
            logger.info(f"Found {len(files)} PFD files")
            return files
            
        except Exception as e:
            logger.error(f"Error listing PFD files: {e}")
            return []
    
    def list_pid_files(self, prefix: str = '', limit: int = 100) -> List[Dict]:
        """
        List all P&ID files from the PID folder
        
        Args:
            prefix: Optional prefix to filter files
            limit: Maximum number of files to return
            
        Returns:
            List of file metadata dicts
        """
        try:
            search_prefix = f"{self.pid_folder}/"
            if prefix:
                search_prefix = f"{self.pid_folder}/{prefix}"
            
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=search_prefix,
                MaxKeys=limit
            )
            
            files = []
            for obj in response.get('Contents', []):
                key = obj['Key']
                
                # Skip if it's the folder itself
                if key.endswith('/'):
                    continue
                
                # Extract filename
                filename = key.replace(f"{self.pid_folder}/", '')
                
                # Try to find corresponding PFD
                pfd_key = self._get_pfd_key_for_pid(key)
                has_pfd_source = self._check_file_exists(pfd_key)
                
                file_info = {
                    's3_key': key,
                    'filename': filename,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'has_pfd_source': has_pfd_source,
                    'pfd_key': pfd_key if has_pfd_source else None,
                    'extension': os.path.splitext(filename)[1].lower()
                }
                
                files.append(file_info)
            
            logger.info(f"Found {len(files)} P&ID files")
            return files
            
        except Exception as e:
            logger.error(f"Error listing P&ID files: {e}")
            return []
    
    def get_pfd_file(self, s3_key: str) -> Optional[BytesIO]:
        """
        Download a PFD file from S3
        
        Args:
            s3_key: S3 key of the file
            
        Returns:
            BytesIO buffer with file content
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            buffer = BytesIO(response['Body'].read())
            buffer.seek(0)
            
            logger.info(f"Retrieved PFD file: {s3_key}")
            return buffer
            
        except Exception as e:
            logger.error(f"Error retrieving PFD file {s3_key}: {e}")
            return None
    
    def get_pid_file(self, s3_key: str) -> Optional[BytesIO]:
        """
        Download a P&ID file from S3
        
        Args:
            s3_key: S3 key of the file
            
        Returns:
            BytesIO buffer with file content
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            buffer = BytesIO(response['Body'].read())
            buffer.seek(0)
            
            logger.info(f"Retrieved P&ID file: {s3_key}")
            return buffer
            
        except Exception as e:
            logger.error(f"Error retrieving P&ID file {s3_key}: {e}")
            return None
    
    def upload_pfd_file(self, file_content: bytes, filename: str, metadata: Dict = None) -> Dict:
        """
        Upload a new PFD file to the PFD folder
        
        Args:
            file_content: File content as bytes
            filename: Filename to use
            metadata: Optional metadata dict
            
        Returns:
            Upload result dict
        """
        try:
            s3_key = f"{self.pfd_folder}/{filename}"
            
            # Prepare metadata
            s3_metadata = {
                'uploaded_at': datetime.now().isoformat(),
                'original_filename': filename
            }
            if metadata:
                s3_metadata.update({k: str(v) for k, v in metadata.items()})
            
            # Determine content type
            content_type = self._get_content_type(filename)
            
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=file_content,
                ContentType=content_type,
                Metadata=s3_metadata
            )
            
            logger.info(f"Uploaded PFD file: {s3_key}")
            
            return {
                'success': True,
                's3_key': s3_key,
                'filename': filename,
                'size': len(file_content),
                'url': self.get_presigned_url(s3_key)
            }
            
        except Exception as e:
            logger.error(f"Error uploading PFD file: {e}")
            return {'success': False, 'error': str(e)}
    
    def save_pid_conversion(
        self,
        pfd_key: str,
        pid_content: bytes,
        output_format: str = 'pdf',
        metadata: Dict = None
    ) -> Dict:
        """
        Save a converted P&ID to the PID folder
        
        Args:
            pfd_key: Original PFD S3 key
            pid_content: Converted P&ID content
            output_format: Output format (pdf, dxf, png, etc.)
            metadata: Optional metadata dict
            
        Returns:
            Save result dict
        """
        try:
            # Generate P&ID filename based on PFD filename
            pfd_filename = pfd_key.replace(f"{self.pfd_folder}/", '')
            base_name = os.path.splitext(pfd_filename)[0]
            
            # Add timestamp to avoid conflicts
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pid_filename = f"{base_name}_PID_{timestamp}.{output_format}"
            
            pid_key = f"{self.pid_folder}/{pid_filename}"
            
            # Prepare metadata
            s3_metadata = {
                'converted_at': datetime.now().isoformat(),
                'source_pfd_key': pfd_key,
                'output_format': output_format
            }
            if metadata:
                s3_metadata.update({k: str(v) for k, v in metadata.items()})
            
            # Determine content type
            content_type = self._get_content_type(pid_filename)
            
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=pid_key,
                Body=pid_content,
                ContentType=content_type,
                Metadata=s3_metadata
            )
            
            logger.info(f"Saved P&ID conversion: {pid_key}")
            
            return {
                'success': True,
                's3_key': pid_key,
                'filename': pid_filename,
                'size': len(pid_content),
                'pfd_key': pfd_key,
                'url': self.get_presigned_url(pid_key)
            }
            
        except Exception as e:
            logger.error(f"Error saving P&ID conversion: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_conversion_pair(self, pfd_key: str) -> Dict:
        """
        Get both PFD and its converted P&ID files
        
        Args:
            pfd_key: PFD S3 key
            
        Returns:
            Dict with PFD and P&ID information
        """
        try:
            # Get PFD info
            pfd_buffer = self.get_pfd_file(pfd_key)
            
            # Find corresponding P&ID
            pid_key = self._get_pid_key_for_pfd(pfd_key)
            pid_buffer = None
            
            if self._check_file_exists(pid_key):
                pid_buffer = self.get_pid_file(pid_key)
            
            return {
                'pfd': {
                    's3_key': pfd_key,
                    'content': pfd_buffer,
                    'exists': pfd_buffer is not None
                },
                'pid': {
                    's3_key': pid_key,
                    'content': pid_buffer,
                    'exists': pid_buffer is not None
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting conversion pair: {e}")
            return {'pfd': {'exists': False}, 'pid': {'exists': False}}
    
    def get_presigned_url(self, s3_key: str, expiration: int = 3600) -> str:
        """
        Generate a presigned URL for downloading a file
        
        Args:
            s3_key: S3 key of the file
            expiration: URL expiration in seconds (default 1 hour)
            
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
        except Exception as e:
            logger.error(f"Error generating presigned URL: {e}")
            return ''
    
    def _check_file_exists(self, s3_key: str) -> bool:
        """Check if a file exists in S3"""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except:
            return False
    
    def _get_pid_key_for_pfd(self, pfd_key: str) -> str:
        """
        Get the expected P&ID key for a PFD file
        Assumes naming pattern: PFD_filename.ext -> PID/PFD_filename_PID.ext
        """
        pfd_filename = pfd_key.replace(f"{self.pfd_folder}/", '')
        base_name = os.path.splitext(pfd_filename)[0]
        
        # Look for any P&ID with matching base name
        # This will return the most recent version
        return f"{self.pid_folder}/{base_name}_PID.pdf"
    
    def _get_pfd_key_for_pid(self, pid_key: str) -> str:
        """
        Get the source PFD key for a P&ID file
        Reverses the naming pattern
        """
        pid_filename = pid_key.replace(f"{self.pid_folder}/", '')
        
        # Remove _PID_timestamp suffix and extension
        base_name = pid_filename.split('_PID_')[0] if '_PID_' in pid_filename else pid_filename.split('_PID.')[0]
        
        # Try common PFD extensions
        for ext in ['.pdf', '.dwg', '.dxf', '.png', '.jpg']:
            pfd_key = f"{self.pfd_folder}/{base_name}{ext}"
            if self._check_file_exists(pfd_key):
                return pfd_key
        
        # Return default if not found
        return f"{self.pfd_folder}/{base_name}.pdf"
    
    def _get_content_type(self, filename: str) -> str:
        """Determine content type from filename"""
        ext = os.path.splitext(filename)[1].lower()
        
        content_types = {
            '.pdf': 'application/pdf',
            '.dwg': 'application/acad',
            '.dxf': 'application/dxf',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.svg': 'image/svg+xml'
        }
        
        return content_types.get(ext, 'application/octet-stream')
    
    def get_bucket_structure_info(self) -> Dict:
        """Get information about the bucket structure"""
        return {
            'bucket_name': self.bucket_name,
            'region': self.region,
            'base_path': self.base_path,
            'pfd_folder': self.pfd_folder,
            'pid_folder': self.pid_folder,
            'pfd_count': len(self.list_pfd_files()),
            'pid_count': len(self.list_pid_files())
        }


def get_s3_pfd_manager() -> S3PFDManager:
    """Factory function to get S3PFDManager instance"""
    return S3PFDManager()
