"""
Google Drive and Sheets Integration Service
Uploads invoices to Google Drive and logs to Google Sheets
"""
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from django.conf import settings
import logging
import os

logger = logging.getLogger(__name__)


class GoogleIntegrationService:
    """Handle Google Drive and Sheets operations"""
    
    def __init__(self):
        """Initialize Google API clients"""
        self.credentials_file = getattr(settings, 'GOOGLE_CREDENTIALS_FILE', None)
        self.drive_folder_id = getattr(settings, 'GOOGLE_DRIVE_FOLDER_ID', None)
        self.sheets_id = getattr(settings, 'GOOGLE_SHEETS_ID', None)
        
        self.drive_service = None
        self.sheets_service = None
        
        if self.credentials_file and os.path.exists(self.credentials_file):
            try:
                creds = service_account.Credentials.from_service_account_file(
                    self.credentials_file,
                    scopes=[
                        'https://www.googleapis.com/auth/drive.file',
                        'https://www.googleapis.com/auth/spreadsheets'
                    ]
                )
                self.drive_service = build('drive', 'v3', credentials=creds)
                self.sheets_service = build('sheets', 'v4', credentials=creds)
                logger.info("Google integration initialized successfully")
            except Exception as e:
                logger.warning(f"Google integration not available: {e}")
    
    def upload_to_drive(self, file_path: str, filename: str, invoice_type: str) -> dict:
        """
        Upload invoice to Google Drive
        Returns: {'file_id': 'xxx', 'file_url': 'xxx'}
        """
        if not self.drive_service or not self.drive_folder_id:
            logger.warning("Google Drive not configured")
            return None
        
        try:
            # Create subfolder for invoice type if needed
            folder_name = f"{invoice_type.upper()} Invoices"
            subfolder_id = self._get_or_create_folder(folder_name, self.drive_folder_id)
            
            # Upload file
            file_metadata = {
                'name': filename,
                'parents': [subfolder_id]
            }
            
            media = MediaFileUpload(
                file_path,
                mimetype='application/pdf',
                resumable=True
            )
            
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()
            
            logger.info(f"File uploaded to Drive: {file.get('id')}")
            
            return {
                'file_id': file.get('id'),
                'file_url': file.get('webViewLink')
            }
            
        except Exception as e:
            logger.error(f"Drive upload failed: {e}")
            return None
    
    def _get_or_create_folder(self, folder_name: str, parent_id: str) -> str:
        """Get existing folder or create new one"""
        try:
            # Check if folder exists
            query = f"name='{folder_name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            results = self.drive_service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)'
            ).execute()
            
            files = results.get('files', [])
            if files:
                return files[0]['id']
            
            # Create folder if it doesn't exist
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id]
            }
            
            folder = self.drive_service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()
            
            return folder.get('id')
            
        except Exception as e:
            logger.error(f"Folder operation failed: {e}")
            return parent_id  # Fallback to parent
    
    def log_to_sheets(self, invoice_data: dict) -> bool:
        """Log invoice data to Google Sheets"""
        if not self.sheets_service or not self.sheets_id:
            logger.warning("Google Sheets not configured")
            return False
        
        try:
            # Prepare row data
            row = [
                invoice_data.get('invoice_number', ''),
                invoice_data.get('vendor_name', ''),
                invoice_data.get('invoice_date', ''),
                invoice_data.get('total_amount', 0),
                invoice_data.get('currency', 'AED'),
                invoice_data.get('invoice_type', ''),
                invoice_data.get('status', ''),
                invoice_data.get('created_at', ''),
                invoice_data.get('drive_url', '')
            ]
            
            # Append to sheet
            body = {
                'values': [row]
            }
            
            result = self.sheets_service.spreadsheets().values().append(
                spreadsheetId=self.sheets_id,
                range='Invoice Register!A:I',
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
            
            logger.info(f"Invoice logged to Google Sheets: {invoice_data.get('invoice_number')}")
            return True
            
        except Exception as e:
            logger.error(f"Sheets logging failed: {e}")
            return False
