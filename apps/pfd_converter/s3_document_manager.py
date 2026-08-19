"""
Smart AWS S3 Document Manager for PFD & Philosophy Documents
Provides intelligent document organization and retrieval
"""
import os
import logging
from datetime import datetime
from typing import Optional, Dict, List
import boto3
from botocore.exceptions import ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


class S3DocumentManager:
    """
    Smart S3 Document Manager for PFD and Philosophy documents
    
    Features:
    - Organized folder structure by project and date
    - Automatic metadata tagging
    - Presigned URL generation for secure access
    - Document versioning support
    - Batch operations
    """
    
    def __init__(self):
        self.enabled = getattr(settings, 'USE_S3', False)
        
        if self.enabled:
            try:
                self.bucket_name = settings.AWS_STORAGE_BUCKET_NAME
                self.region = getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
                
                # Initialize S3 client
                self.s3_client = boto3.client(
                    's3',
                    region_name=self.region
                )
                
                # Test bucket access
                try:
                    self.s3_client.head_bucket(Bucket=self.bucket_name)
                    logger.info(f"[S3DocumentManager] ✅ Bucket accessible: {self.bucket_name}")
                except ClientError as e:
                    error_code = e.response.get('Error', {}).get('Code')
                    logger.error(f"[S3DocumentManager] ❌ Bucket access failed: {error_code}")
                    logger.warning(f"[S3DocumentManager] Disabling S3 due to access issues")
                    self.enabled = False
                    
            except Exception as e:
                logger.error(f"[S3DocumentManager] Initialization failed: {str(e)}")
                self.enabled = False
        else:
            logger.info("[S3DocumentManager] S3 is disabled - using local storage")
    
    def _generate_document_path(self, 
                                doc_type: str, 
                                project_code: str, 
                                filename: str,
                                user_id: str = None) -> str:
        """
        Generate smart S3 path with organized structure
        
        Structure:
        documents/
            └── {doc_type}/          # pfd or philosophy
                └── {year}/
                    └── {month}/
                        └── {project_code}/
                            └── {user_id}/
                                └── {filename}
        """
        now = datetime.now()
        year = now.strftime('%Y')
        month = now.strftime('%m')
        
        parts = [
            'documents',
            doc_type.lower(),
            year,
            month
        ]
        
        if project_code:
            parts.append(project_code)
        
        if user_id:
            parts.append(str(user_id))
        
        parts.append(filename)
        
        return '/'.join(parts)
    
    def upload_pfd(self, 
                   file_obj, 
                   filename: str,
                   project_code: str,
                   user_id: str,
                   metadata: Optional[Dict] = None) -> Dict:
        """
        Upload PFD document to S3 with smart organization
        
        Args:
            file_obj: File object to upload
            filename: Original filename
            project_code: Project code for organization
            user_id: User ID who uploaded
            metadata: Additional metadata (document_number, revision, etc.)
        
        Returns:
            Dict with s3_key, url, and metadata
        """
        if not self.enabled:
            logger.warning("[S3] S3 disabled - file not uploaded")
            return {'error': 'S3 not enabled'}
        
        try:
            # Generate organized S3 path
            s3_key = self._generate_document_path('pfd', project_code, filename, user_id)
            
            # Prepare metadata tags
            s3_metadata = {
                'document-type': 'pfd',
                'project-code': project_code or 'unknown',
                'uploaded-by': str(user_id),
                'upload-date': datetime.now().isoformat()
            }
            
            # Add custom metadata
            if metadata:
                for key, value in metadata.items():
                    if value:
                        s3_metadata[key.replace('_', '-')] = str(value)
            
            # Upload to S3
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                s3_key,
                ExtraArgs={
                    'Metadata': s3_metadata,
                    'ContentType': self._get_content_type(filename),
                    'ServerSideEncryption': 'AES256'  # Encryption at rest
                }
            )
            
            # Generate presigned URL (valid for 24 hours)
            url = self.generate_presigned_url(s3_key, expiration=86400)
            
            logger.info(f"[S3] Uploaded PFD: {s3_key}")
            
            return {
                's3_key': s3_key,
                'url': url,
                'bucket': self.bucket_name,
                'region': self.region,
                'metadata': s3_metadata
            }
            
        except ClientError as e:
            logger.error(f"[S3] Upload failed: {str(e)}")
            return {'error': str(e)}
    
    def upload_philosophy(self, 
                         file_obj, 
                         filename: str,
                         project_code: str,
                         user_id: str,
                         metadata: Optional[Dict] = None) -> Dict:
        """
        Upload Philosophy document to S3 with smart organization
        """
        if not self.enabled:
            logger.warning("[S3] S3 disabled - file not uploaded")
            return {'error': 'S3 not enabled'}
        
        try:
            # Generate organized S3 path
            s3_key = self._generate_document_path('philosophy', project_code, filename, user_id)
            
            # Prepare metadata tags
            s3_metadata = {
                'document-type': 'philosophy',
                'project-code': project_code or 'unknown',
                'uploaded-by': str(user_id),
                'upload-date': datetime.now().isoformat()
            }
            
            # Add custom metadata
            if metadata:
                for key, value in metadata.items():
                    if value:
                        s3_metadata[key.replace('_', '-')] = str(value)
            
            # Upload to S3
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                s3_key,
                ExtraArgs={
                    'Metadata': s3_metadata,
                    'ContentType': self._get_content_type(filename),
                    'ServerSideEncryption': 'AES256'
                }
            )
            
            # Generate presigned URL
            url = self.generate_presigned_url(s3_key, expiration=86400)
            
            logger.info(f"[S3] Uploaded Philosophy: {s3_key}")
            
            return {
                's3_key': s3_key,
                'url': url,
                'bucket': self.bucket_name,
                'region': self.region,
                'metadata': s3_metadata
            }
            
        except ClientError as e:
            logger.error(f"[S3] Upload failed: {str(e)}")
            return {'error': str(e)}
    
    def generate_presigned_url(self, s3_key: str, expiration: int = 3600) -> str:
        """
        Generate presigned URL for secure document access
        
        Args:
            s3_key: S3 object key
            expiration: URL expiration time in seconds (default 1 hour)
        """
        if not self.enabled:
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
            logger.error(f"[S3] Failed to generate presigned URL: {str(e)}")
            return None
    
    def get_document_metadata(self, s3_key: str) -> Optional[Dict]:
        """Get document metadata from S3"""
        if not self.enabled:
            return None
        
        try:
            response = self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            return response.get('Metadata', {})
        except ClientError as e:
            logger.error(f"[S3] Failed to get metadata: {str(e)}")
            return None
    
    def list_project_documents(self, project_code: str, doc_type: str = None) -> List[Dict]:
        """
        List all documents for a project
        
        Args:
            project_code: Project code to filter by
            doc_type: Optional document type filter (pfd or philosophy)
        """
        if not self.enabled:
            return []
        
        try:
            prefix = f'documents/'
            if doc_type:
                prefix += f'{doc_type.lower()}/'
            
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix
            )
            
            documents = []
            for obj in response.get('Contents', []):
                key = obj['Key']
                if project_code in key:
                    documents.append({
                        'key': key,
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'].isoformat(),
                        'url': self.generate_presigned_url(key)
                    })
            
            return documents
            
        except ClientError as e:
            logger.error(f"[S3] Failed to list documents: {str(e)}")
            return []
    
    def delete_document(self, s3_key: str) -> bool:
        """Delete document from S3"""
        if not self.enabled:
            return False
        
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            logger.info(f"[S3] Deleted: {s3_key}")
            return True
        except ClientError as e:
            logger.error(f"[S3] Failed to delete: {str(e)}")
            return False
    
    def _get_content_type(self, filename: str) -> str:
        """Determine content type from filename"""
        ext = os.path.splitext(filename)[1].lower()
        content_types = {
            '.pdf': 'application/pdf',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.dwg': 'application/acad',
            '.dxf': 'application/dxf'
        }
        return content_types.get(ext, 'application/octet-stream')
    
    def get_storage_stats(self, project_code: str = None) -> Dict:
        """
        Get storage statistics
        
        Args:
            project_code: Optional project filter
        """
        if not self.enabled:
            return {'error': 'S3 not enabled'}
        
        try:
            stats = {
                'pfd_count': 0,
                'philosophy_count': 0,
                'total_size': 0,
                'pfd_size': 0,
                'philosophy_size': 0
            }
            
            for doc_type in ['pfd', 'philosophy']:
                prefix = f'documents/{doc_type}/'
                
                response = self.s3_client.list_objects_v2(
                    Bucket=self.bucket_name,
                    Prefix=prefix
                )
                
                for obj in response.get('Contents', []):
                    key = obj['Key']
                    size = obj['Size']
                    
                    if not project_code or project_code in key:
                        if doc_type == 'pfd':
                            stats['pfd_count'] += 1
                            stats['pfd_size'] += size
                        else:
                            stats['philosophy_count'] += 1
                            stats['philosophy_size'] += size
                        
                        stats['total_size'] += size
            
            # Convert to MB
            stats['total_size_mb'] = round(stats['total_size'] / (1024 * 1024), 2)
            stats['pfd_size_mb'] = round(stats['pfd_size'] / (1024 * 1024), 2)
            stats['philosophy_size_mb'] = round(stats['philosophy_size'] / (1024 * 1024), 2)
            
            return stats
            
        except ClientError as e:
            logger.error(f"[S3] Failed to get stats: {str(e)}")
            return {'error': str(e)}


# Singleton instance
_s3_manager = None

def get_s3_manager() -> S3DocumentManager:
    """Get singleton S3 document manager instance"""
    global _s3_manager
    if _s3_manager is None:
        _s3_manager = S3DocumentManager()
    return _s3_manager
