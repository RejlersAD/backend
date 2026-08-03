"""
User Storage Manager - AWS S3 User-Based Folder System
Manages user-specific folders and file storage in S3

Structure:
users/
  {user_id}/
    profile.json           # User metadata
    uploads/               # Original uploaded files
      {timestamp}_{filename}
    exports/               # Generated exports (xlsx, csv, pdf, docx, json)
      {timestamp}_{format}_{filename}
    history/               # Activity logs
      {year}/{month}/
        activity_{date}.json

Does NOT modify existing code or APIs
"""

import json
import os
from datetime import datetime
from io import BytesIO
from typing import Optional, Dict, List, Any
import logging

logger = logging.getLogger(__name__)

# Try to import S3 service from multiple possible locations
S3_AVAILABLE = False
S3Service = None

try:
    from apps.core.s3_service import S3Service
    S3_AVAILABLE = True
except ImportError:
    try:
        from apps.rbac.s3_service import S3Service
        S3_AVAILABLE = True
    except ImportError:
        logger.warning("S3Service not available - user storage will use local fallback")


class UserStorageManager:
    """
    Manages user-specific storage in AWS S3
    Implements folder-per-user pattern for isolation and easy retrieval
    """
    
    def __init__(self, user=None, user_id: Optional[int] = None, username: Optional[str] = None):
        """
        Initialize with user object or user details
        
        Args:
            user: Django User object (preferred)
            user_id: User ID as fallback
            username: Username as fallback
        """
        if user:
            self.user_id = user.id
            self.username = user.username
            self.email = getattr(user, 'email', '')
            self.full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        else:
            self.user_id = user_id or 0
            self.username = username or 'anonymous'
            self.email = ''
            self.full_name = ''
        
        # S3 configuration
        self.s3_service = S3Service() if S3_AVAILABLE else None
        self.bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME', 'user-management-rejlers')
        
        # Base paths
        self.user_base_path = f"users/{self.user_id}"
        self.uploads_path = f"{self.user_base_path}/uploads"
        self.exports_path = f"{self.user_base_path}/exports"
        self.history_path = f"{self.user_base_path}/history"
        
    def _get_timestamp(self) -> str:
        """Get formatted timestamp for filenames"""
        return datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def _get_date_path(self) -> str:
        """Get year/month path for history organization"""
        now = datetime.now()
        return f"{now.year}/{now.month:02d}"
    
    def ensure_user_folders(self) -> Dict[str, bool]:
        """
        Ensure all user folders exist in S3
        Creates folder structure if not exists
        
        Returns:
            Dict with folder creation status
        """
        if not self.s3_service:
            logger.warning("S3 not available, skipping folder creation")
            return {'success': False, 'reason': 'S3 not available'}
        
        folders = [
            f"{self.user_base_path}/",
            f"{self.uploads_path}/",
            f"{self.exports_path}/",
            f"{self.history_path}/",
        ]
        
        results = {}
        for folder in folders:
            try:
                # Create empty object to represent folder
                self.s3_service.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=folder,
                    Body=''
                )
                results[folder] = True
                logger.debug(f"Created/verified folder: {folder}")
            except Exception as e:
                results[folder] = False
                logger.error(f"Failed to create folder {folder}: {e}")
        
        # Create/update user profile
        self._update_user_profile()
        
        return {'success': True, 'folders': results}
    
    def _update_user_profile(self):
        """Create or update user profile JSON in S3"""
        if not self.s3_service:
            return
        
        profile_key = f"{self.user_base_path}/profile.json"
        
        # Try to get existing profile
        existing_profile = {}
        try:
            response = self.s3_service.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=profile_key
            )
            existing_profile = json.loads(response['Body'].read().decode('utf-8'))
        except Exception:
            pass  # Profile doesn't exist yet
        
        # Update profile
        profile = {
            'user_id': self.user_id,
            'username': self.username,
            'email': self.email,
            'full_name': self.full_name,
            'created_at': existing_profile.get('created_at', datetime.now().isoformat()),
            'last_activity': datetime.now().isoformat(),
            'total_uploads': existing_profile.get('total_uploads', 0),
            'total_exports': existing_profile.get('total_exports', 0),
        }
        
        try:
            self.s3_service.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=profile_key,
                Body=json.dumps(profile, indent=2),
                ContentType='application/json'
            )
        except Exception as e:
            logger.error(f"Failed to update user profile: {e}")
    
    def save_upload(
        self,
        file_content: bytes,
        original_filename: str,
        metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Save uploaded file to user's uploads folder
        
        Args:
            file_content: File content as bytes
            original_filename: Original filename
            metadata: Optional metadata dict
            
        Returns:
            Dict with upload result and S3 key
        """
        if not self.s3_service:
            return {'success': False, 'error': 'S3 not available'}
        
        self.ensure_user_folders()
        
        timestamp = self._get_timestamp()
        safe_filename = "".join(c for c in original_filename if c.isalnum() or c in ".-_").strip()
        s3_key = f"{self.uploads_path}/{timestamp}_{safe_filename}"
        
        # Prepare metadata
        upload_metadata = {
            'user_id': str(self.user_id),
            'username': self.username,
            'original_filename': original_filename,
            'upload_time': datetime.now().isoformat(),
        }
        if metadata:
            upload_metadata.update({k: str(v) for k, v in metadata.items()})
        
        try:
            self.s3_service.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=file_content,
                Metadata=upload_metadata
            )
            
            # Log activity
            self.log_activity('upload', {
                's3_key': s3_key,
                'filename': original_filename,
                'size': len(file_content),
                'metadata': metadata
            })
            
            # Update profile stats
            self._increment_profile_stat('total_uploads')
            
            logger.info(f"Saved upload to S3: {s3_key}")
            
            return {
                'success': True,
                's3_key': s3_key,
                'filename': original_filename,
                'size': len(file_content),
                'timestamp': timestamp
            }
            
        except Exception as e:
            logger.error(f"Failed to save upload: {e}")
            return {'success': False, 'error': str(e)}
    
    def save_export(
        self,
        file_content: bytes,
        export_format: str,
        base_filename: str,
        metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Save exported file to user's exports folder
        
        Args:
            file_content: File content as bytes
            export_format: Format type (xlsx, csv, json, pdf, docx)
            base_filename: Base filename without extension
            metadata: Optional metadata dict
            
        Returns:
            Dict with export result and S3 key
        """
        if not self.s3_service:
            return {'success': False, 'error': 'S3 not available'}
        
        self.ensure_user_folders()
        
        timestamp = self._get_timestamp()
        extension = export_format.lower()
        safe_filename = "".join(c for c in base_filename if c.isalnum() or c in "-_").strip() or "export"
        s3_key = f"{self.exports_path}/{timestamp}_{safe_filename}.{extension}"
        
        # Content type mapping
        content_types = {
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'csv': 'text/csv',
            'json': 'application/json',
            'pdf': 'application/pdf',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }
        
        try:
            self.s3_service.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=file_content,
                ContentType=content_types.get(extension, 'application/octet-stream'),
                Metadata={
                    'user_id': str(self.user_id),
                    'username': self.username,
                    'export_format': export_format,
                    'export_time': datetime.now().isoformat(),
                }
            )
            
            # Log activity
            self.log_activity('export', {
                's3_key': s3_key,
                'format': export_format,
                'filename': f"{safe_filename}.{extension}",
                'size': len(file_content),
                'metadata': metadata
            })
            
            # Update profile stats
            self._increment_profile_stat('total_exports')
            
            logger.info(f"Saved export to S3: {s3_key}")
            
            return {
                'success': True,
                's3_key': s3_key,
                'filename': f"{safe_filename}.{extension}",
                'format': export_format,
                'size': len(file_content),
                'timestamp': timestamp
            }
            
        except Exception as e:
            logger.error(f"Failed to save export: {e}")
            return {'success': False, 'error': str(e)}
    
    def log_activity(self, action: str, details: Dict[str, Any]):
        """
        Log user activity to S3 history folder
        
        Args:
            action: Action type (upload, export, download, view, etc.)
            details: Activity details
        """
        if not self.s3_service:
            return
        
        date_path = self._get_date_path()
        today = datetime.now().strftime("%Y-%m-%d")
        history_key = f"{self.history_path}/{date_path}/activity_{today}.json"
        
        # Get existing activities for today
        activities = []
        try:
            response = self.s3_service.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=history_key
            )
            activities = json.loads(response['Body'].read().decode('utf-8'))
        except Exception:
            pass  # File doesn't exist yet
        
        # Add new activity
        activity_entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'user_id': self.user_id,
            'username': self.username,
            'details': details
        }
        activities.append(activity_entry)
        
        try:
            self.s3_service.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=history_key,
                Body=json.dumps(activities, indent=2, default=str),
                ContentType='application/json'
            )
        except Exception as e:
            logger.error(f"Failed to log activity: {e}")
    
    def _increment_profile_stat(self, stat_name: str):
        """Increment a stat in user profile"""
        if not self.s3_service:
            return
        
        profile_key = f"{self.user_base_path}/profile.json"
        
        try:
            response = self.s3_service.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=profile_key
            )
            profile = json.loads(response['Body'].read().decode('utf-8'))
            profile[stat_name] = profile.get(stat_name, 0) + 1
            profile['last_activity'] = datetime.now().isoformat()
            
            self.s3_service.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=profile_key,
                Body=json.dumps(profile, indent=2),
                ContentType='application/json'
            )
        except Exception as e:
            logger.error(f"Failed to increment profile stat: {e}")
    
    def get_user_uploads(self, limit: int = 50) -> List[Dict]:
        """
        Get list of user's uploaded files
        
        Args:
            limit: Maximum number of files to return
            
        Returns:
            List of file info dicts
        """
        if not self.s3_service:
            return []
        
        try:
            response = self.s3_service.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=f"{self.uploads_path}/",
                MaxKeys=limit
            )
            
            files = []
            for obj in response.get('Contents', []):
                key = obj['Key']
                if key.endswith('/'):
                    continue  # Skip folders
                
                files.append({
                    's3_key': key,
                    'filename': key.split('/')[-1],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                })
            
            return sorted(files, key=lambda x: x['last_modified'], reverse=True)
            
        except Exception as e:
            logger.error(f"Failed to list uploads: {e}")
            return []
    
    def get_user_exports(self, limit: int = 50) -> List[Dict]:
        """
        Get list of user's exported files
        
        Args:
            limit: Maximum number of files to return
            
        Returns:
            List of file info dicts
        """
        if not self.s3_service:
            return []
        
        try:
            response = self.s3_service.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=f"{self.exports_path}/",
                MaxKeys=limit
            )
            
            files = []
            for obj in response.get('Contents', []):
                key = obj['Key']
                if key.endswith('/'):
                    continue
                
                filename = key.split('/')[-1]
                format_ext = filename.split('.')[-1] if '.' in filename else 'unknown'
                
                files.append({
                    's3_key': key,
                    'filename': filename,
                    'format': format_ext,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                })
            
            return sorted(files, key=lambda x: x['last_modified'], reverse=True)
            
        except Exception as e:
            logger.error(f"Failed to list exports: {e}")
            return []
    
    def get_activity_history(self, days: int = 30) -> List[Dict]:
        """
        Get user's activity history
        
        Args:
            days: Number of days of history to retrieve
            
        Returns:
            List of activity entries
        """
        if not self.s3_service:
            return []
        
        all_activities = []
        
        try:
            # List all activity files in history folder
            response = self.s3_service.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=f"{self.history_path}/",
            )
            
            for obj in response.get('Contents', []):
                key = obj['Key']
                if not key.endswith('.json'):
                    continue
                
                try:
                    file_response = self.s3_service.s3_client.get_object(
                        Bucket=self.bucket_name,
                        Key=key
                    )
                    activities = json.loads(file_response['Body'].read().decode('utf-8'))
                    all_activities.extend(activities)
                except Exception:
                    continue
            
            # Sort by timestamp descending
            all_activities.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            
            return all_activities[:days * 100]  # Approximate limit
            
        except Exception as e:
            logger.error(f"Failed to get activity history: {e}")
            return []
    
    def get_user_profile(self) -> Optional[Dict]:
        """Get user's profile from S3"""
        if not self.s3_service:
            return None
        
        profile_key = f"{self.user_base_path}/profile.json"
        
        try:
            response = self.s3_service.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=profile_key
            )
            return json.loads(response['Body'].read().decode('utf-8'))
        except Exception:
            return None
    
    def download_file(self, s3_key: str) -> Optional[BytesIO]:
        """
        Download a file from user's S3 storage
        
        Args:
            s3_key: S3 key of the file
            
        Returns:
            BytesIO buffer with file content, or None
        """
        if not self.s3_service:
            return None
        
        # Security: Ensure the key belongs to this user
        if not s3_key.startswith(self.user_base_path):
            logger.warning(f"Attempted to access file outside user folder: {s3_key}")
            return None
        
        try:
            response = self.s3_service.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            buffer = BytesIO(response['Body'].read())
            buffer.seek(0)
            
            # Log download activity
            self.log_activity('download', {
                's3_key': s3_key,
                'filename': s3_key.split('/')[-1]
            })
            
            return buffer
            
        except Exception as e:
            logger.error(f"Failed to download file: {e}")
            return None
    
    def delete_file(self, s3_key: str) -> bool:
        """
        Delete a file from user's S3 storage
        
        Args:
            s3_key: S3 key of the file to delete
            
        Returns:
            True if deleted successfully, False otherwise
        """
        if not self.s3_service:
            return False
        
        # Security: Ensure the key belongs to this user
        if not s3_key.startswith(self.user_base_path):
            logger.warning(f"Attempted to delete file outside user folder: {s3_key}")
            return False
        
        try:
            self.s3_service.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            # Log delete activity
            self.log_activity('delete', {
                's3_key': s3_key,
                'filename': s3_key.split('/')[-1]
            })
            
            logger.info(f"Successfully deleted file: {s3_key}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete file: {e}")
            return False
    
    def generate_presigned_url(self, s3_key: str, expiration: int = 3600) -> Optional[str]:
        """
        Generate a presigned URL for temporary file access
        
        Args:
            s3_key: S3 key of the file
            expiration: URL validity in seconds (default: 1 hour)
            
        Returns:
            Presigned URL string, or None
        """
        if not self.s3_service:
            return None
        
        # Security: Ensure the key belongs to this user
        if not s3_key.startswith(self.user_base_path):
            logger.warning(f"Attempted to share file outside user folder: {s3_key}")
            return None
        
        try:
            url = self.s3_service.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': s3_key
                },
                ExpiresIn=expiration
            )
            
            return url
            
        except Exception as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            return None
    
    def get_file_metadata(self, s3_key: str) -> Optional[Dict]:
        """
        Get detailed metadata for a file
        
        Args:
            s3_key: S3 key of the file
            
        Returns:
            Dict with file metadata, or None
        """
        if not self.s3_service:
            return None
        
        # Security: Ensure the key belongs to this user
        if not s3_key.startswith(self.user_base_path):
            logger.warning(f"Attempted to access metadata outside user folder: {s3_key}")
            return None
        
        try:
            response = self.s3_service.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            metadata = {
                's3_key': s3_key,
                'filename': s3_key.split('/')[-1],
                'size': response.get('ContentLength', 0),
                'content_type': response.get('ContentType', 'unknown'),
                'last_modified': response.get('LastModified', '').isoformat() if response.get('LastModified') else None,
                'etag': response.get('ETag', '').strip('"'),
                'storage_class': response.get('StorageClass', 'STANDARD'),
                'metadata': response.get('Metadata', {}),
            }
            
            return metadata
            
        except Exception as e:
            logger.error(f"Failed to get file metadata: {e}")
            return None


def get_user_storage(user) -> UserStorageManager:
    """
    Factory function to get UserStorageManager for a user
    
    Args:
        user: Django User object
        
    Returns:
        UserStorageManager instance
    """
    return UserStorageManager(user=user)
