"""
Google Sheets Integration Service for CRS
Handles OAuth2 authentication and data export to Google Sheets
"""

import os
import json
from typing import List, Dict, Optional, Tuple
from datetime import datetime

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False


class GoogleSheetsService:
    """
    Professional Google Sheets integration service
    Handles authentication, data export, and sheet management
    """
    
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
    
    def __init__(self, credentials_json: Optional[str] = None, token_json: Optional[str] = None):
        """
        Initialize Google Sheets service
        
        Args:
            credentials_json: JSON string of Google OAuth credentials
            token_json: JSON string of saved OAuth token
        """
        if not GOOGLE_AVAILABLE:
            raise ImportError("Google API libraries not installed. Install with: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        
        self.credentials_json = credentials_json
        self.token_json = token_json
        self.service = None
    
    def find_credentials_file(self) -> Optional[str]:
        """Find credentials.json or client_secret_*.json file"""
        if os.path.exists('credentials.json'):
            return 'credentials.json'
        
        current_dir = os.path.dirname(os.path.abspath(__file__))
        client_secret_files = []
        
        for file in os.listdir(current_dir):
            if file.startswith('client_secret') and file.endswith('.json'):
                client_secret_files.append(file)
        
        return client_secret_files[0] if client_secret_files else None
    
    def authenticate(self) -> bool:
        """
        Authenticate with Google Sheets API
        Returns True if successful, False otherwise
        """
        creds = None
        
        # Try to load from token_json string
        if self.token_json:
            try:
                token_data = json.loads(self.token_json)
                creds = Credentials.from_authorized_user_info(token_data, self.SCOPES)
            except Exception as e:
                print(f"Warning: Could not load token from string: {e}")
                creds = None
        # Try to load from token.json file
        elif os.path.exists('token.json'):
            try:
                creds = Credentials.from_authorized_user_file('token.json', self.SCOPES)
            except Exception as e:
                print(f"Warning: Could not load existing token: {e}")
                creds = None
        
        # Refresh or create new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"Error refreshing token: {e}")
                    creds = None
            
            # Need new authentication
            if not creds or not creds.valid:
                # Try credentials from string
                if self.credentials_json:
                    try:
                        creds_data = json.loads(self.credentials_json)
                        
                        # Convert web credentials to installed format if needed
                        if 'web' in creds_data and 'installed' not in creds_data:
                            print("Converting web credentials to installed format...")
                            installed_creds = {
                                "installed": {
                                    "client_id": creds_data['web']['client_id'],
                                    "project_id": creds_data['web'].get('project_id', ''),
                                    "auth_uri": creds_data['web']['auth_uri'],
                                    "token_uri": creds_data['web']['token_uri'],
                                    "auth_provider_x509_cert_url": creds_data['web'].get('auth_provider_x509_cert_url', ''),
                                    "client_secret": creds_data['web']['client_secret'],
                                    "redirect_uris": ["http://localhost"]
                                }
                            }
                            temp_creds_file = 'temp_credentials.json'
                            with open(temp_creds_file, 'w') as f:
                                json.dump(installed_creds, f)
                            creds_file = temp_creds_file
                        else:
                            # Save to temp file
                            temp_creds_file = 'temp_credentials.json'
                            with open(temp_creds_file, 'w') as f:
                                json.dump(creds_data, f)
                            creds_file = temp_creds_file
                        
                        flow = InstalledAppFlow.from_client_secrets_file(creds_file, self.SCOPES)
                        creds = flow.run_local_server(port=0, open_browser=True)
                        
                        # Cleanup temp file
                        if os.path.exists('temp_credentials.json'):
                            os.remove('temp_credentials.json')
                    except Exception as e:
                        print(f"Error with credentials string: {e}")
                        return False
                else:
                    # Try credentials file
                    creds_file = self.find_credentials_file()
                    
                    if not creds_file:
                        print("ERROR: Google credentials file not found!")
                        return False
                    
                    print(f"Using credentials file: {creds_file}")
                    
                    try:
                        with open(creds_file, 'r') as f:
                            creds_data = json.load(f)
                        
                        # Convert web to installed if needed
                        if 'web' in creds_data and 'installed' not in creds_data:
                            print("Converting web credentials to installed format...")
                            installed_creds = {
                                "installed": {
                                    "client_id": creds_data['web']['client_id'],
                                    "project_id": creds_data['web'].get('project_id', ''),
                                    "auth_uri": creds_data['web']['auth_uri'],
                                    "token_uri": creds_data['web']['token_uri'],
                                    "auth_provider_x509_cert_url": creds_data['web'].get('auth_provider_x509_cert_url', ''),
                                    "client_secret": creds_data['web']['client_secret'],
                                    "redirect_uris": ["http://localhost"]
                                }
                            }
                            temp_creds_file = 'temp_credentials.json'
                            with open(temp_creds_file, 'w') as f:
                                json.dump(installed_creds, f)
                            creds_file = temp_creds_file
                        
                        flow = InstalledAppFlow.from_client_secrets_file(creds_file, self.SCOPES)
                        creds = flow.run_local_server(port=0, open_browser=True)
                        
                        # Cleanup temp file
                        if os.path.exists('temp_credentials.json'):
                            os.remove('temp_credentials.json')
                    except Exception as e:
                        print(f"Error during authentication: {e}")
                        return False
            
            # Save token
            if creds:
                try:
                    # Save to string for database
                    self.token_json = creds.to_json()
                    # Also save to file
                    with open('token.json', 'w') as token:
                        token.write(creds.to_json())
                except Exception as e:
                    print(f"Warning: Could not save token: {e}")
        
        # Build service
        if creds:
            self.service = build('sheets', 'v4', credentials=creds)
            return True
        
        return False
    
    def export_to_sheet(
        self,
        spreadsheet_id: str,
        data: List[Dict],
        start_row: int = 9
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Export CRS data to Google Sheets
        
        Args:
            spreadsheet_id: Google Sheet ID
            data: List of comment dictionaries
            start_row: Starting row number (default: 9)
        
        Returns:
            Tuple of (success: bool, result: dict)
        """
        if not self.service:
            if not self.authenticate():
                return False, {"error": "Authentication failed"}
        
        try:
            sheet = self.service.spreadsheets()
            
            # Prepare values for Sheet
            # Columns: S NO. | PAGE NUMBER | CLAUSE NO | COMPANY COMMENTS | (empty) | (empty) | (empty) | CONTRACTOR RESPONSE | (empty) | (empty) | (empty) | (empty) | COMPANY Response
            values = []
            for idx, item in enumerate(data, start=1):
                row = [
                    idx,  # A: S NO.
                    item.get('page', ''),  # B: PAGE NUMBER
                    item.get('clause', ''),  # C: CLAUSE NO
                    item.get('text', ''),  # D: COMPANY COMMENTS
                    '',  # E: Empty
                    '',  # F: Empty
                    '',  # G: Empty
                    item.get('contractor_response', ''),  # H: CONTRACTOR RESPONSE
                    '',  # I: Empty
                    '',  # J: Empty
                    '',  # K: Empty
                    '',  # L: Empty
                    item.get('company_response', ''),  # M: COMPANY Response
                ]
                values.append(row)
            
            range_name = f'A{start_row}:M{start_row + len(values) - 1}'
            
            body = {
                'values': values
            }
            
            result = sheet.values().update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption='RAW',
                body=body
            ).execute()
            
            print(f"✅ Successfully exported {len(values)} rows to Google Sheets")
            print(f"   Updated {result.get('updatedCells')} cells")
            
            return True, {
                "updated_rows": len(values),
                "updated_cells": result.get('updatedCells'),
                "range": range_name,
                "spreadsheet_id": spreadsheet_id
            }
        
        except HttpError as error:
            print(f"❌ Google Sheets API error: {error}")
            return False, {"error": str(error)}
        except Exception as e:
            print(f"❌ Export error: {e}")
            return False, {"error": str(e)}
    
    def get_sheet_info(self, spreadsheet_id: str) -> Tuple[bool, Optional[Dict]]:
        """
        Get information about a Google Sheet
        
        Args:
            spreadsheet_id: Google Sheet ID
        
        Returns:
            Tuple of (success: bool, info: dict)
        """
        if not self.service:
            if not self.authenticate():
                return False, {"error": "Authentication failed"}
        
        try:
            sheet = self.service.spreadsheets()
            result = sheet.get(spreadsheetId=spreadsheet_id).execute()
            
            return True, {
                "title": result.get('properties', {}).get('title'),
                "sheets": [s.get('properties', {}).get('title') for s in result.get('sheets', [])],
                "spreadsheet_id": spreadsheet_id,
                "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
            }
        
        except HttpError as error:
            print(f"❌ Google Sheets API error: {error}")
            return False, {"error": str(error)}
        except Exception as e:
            print(f"❌ Error getting sheet info: {e}")
            return False, {"error": str(e)}
    
    def create_new_sheet(self, title: str) -> Tuple[bool, Optional[Dict]]:
        """
        Create a new Google Sheet
        
        Args:
            title: Sheet title
        
        Returns:
            Tuple of (success: bool, sheet_info: dict)
        """
        if not self.service:
            if not self.authenticate():
                return False, {"error": "Authentication failed"}
        
        try:
            sheet = self.service.spreadsheets()
            spreadsheet = {
                'properties': {
                    'title': title
                }
            }
            
            result = sheet.create(body=spreadsheet).execute()
            
            spreadsheet_id = result.get('spreadsheetId')
            
            return True, {
                "spreadsheet_id": spreadsheet_id,
                "title": title,
                "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
            }
        
        except HttpError as error:
            print(f"❌ Google Sheets API error: {error}")
            return False, {"error": str(error)}
        except Exception as e:
            print(f"❌ Error creating sheet: {e}")
            return False, {"error": str(e)}
    
    def get_token_json(self) -> Optional[str]:
        """Get current token as JSON string for storage"""
        return self.token_json
