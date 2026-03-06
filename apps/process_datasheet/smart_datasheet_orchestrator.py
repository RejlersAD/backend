"""
Smart Datasheet Orchestrator
Intelligently detects document types and routes to appropriate datasheet generator
Supports: PFD/Pump, Pressure Instrument, MOV Equipment, SDV Streams
"""

import logging
import uuid
from django.core.cache import cache
from django.conf import settings
import threading
import tempfile
import os
import base64
from io import BytesIO

logger = logging.getLogger(__name__)


class SmartDatasheetOrchestrator:
    """
    Orchestrates datasheet generation based on intelligent document detection
    """
    
    def __init__(self):
        self.job_id = str(uuid.uuid4())
        
    def detect_document_types(self, uploaded_files):
        """
        Intelligently detect what types of documents were uploaded
        Returns: dict with document classifications
        """
        detected = {
            'has_pid': False,
            'has_hmb': False,
            'has_pump_data': False,
            'document_count': len(uploaded_files),
            'file_names': [f.name.lower() for f in uploaded_files]
        }
        
        for file in uploaded_files:
            filename_lower = file.name.lower()
            
            # Detect P&ID
            if any(keyword in filename_lower for keyword in ['pid', 'p&id', 'piping', 'instrument', 'diagram']):
                detected['has_pid'] = True
                
            # Detect HMB
            if any(keyword in filename_lower for keyword in ['hmb', 'heat', 'material', 'balance', 'stream']):
                detected['has_hmb'] = True
                
            # Detect Pump data
            if any(keyword in filename_lower for keyword in ['pump', 'hydraulic', 'pfd', 'flow']):
                detected['has_pump_data'] = True
        
        return detected
    
    def determine_datasheet_types(self, detected_docs, user_selection=None):
        """
        Determine which datasheet types to generate based on detected documents
        Returns: list of datasheet types to generate
        """
        datasheet_types = []
        
        # If user explicitly selected types, use those
        if user_selection and isinstance(user_selection, list):
            return user_selection
        
        # Smart auto-detection
        if detected_docs['has_pid'] and detected_docs['has_hmb']:
            # P&ID + HMB = Can generate MOV and SDV datasheets
            datasheet_types.extend(['mov_equipment', 'sdv_streams'])
            
        if detected_docs['has_pid']:
            # P&ID alone = Can generate Pressure Instrument datasheets
            datasheet_types.append('pressure_instrument')
            
        if detected_docs['has_pump_data']:
            # Pump data = Can generate Pump/PFD datasheets
            datasheet_types.append('pump_hydraulic')
        
        # Remove duplicates
        datasheet_types = list(set(datasheet_types))
        
        return datasheet_types
    
    def process_smart_datasheet(self, files_dict, user_preferences=None):
        """
        Main orchestration method - processes files based on user selection
        Args:
            files_dict: Dictionary with keys 'pid', 'hmb', 'other' containing file objects
            user_preferences: Dictionary with 'selected_type' key
        """
        try:
            selected_type = user_preferences.get('selected_type') if user_preferences else None
            
            if not selected_type:
                raise Exception("No datasheet type selected")
            
            logger.info(f"[Smart Orchestrator {self.job_id}] Processing {selected_type} datasheet")
            
            # Step 1: Validate files
            cache.set(f'smart_job_{self.job_id}', {
                'status': 'processing',
                'progress': 10,
                'stage': f'Preparing {selected_type} generation...'
            }, timeout=3600)
            
            datasheet_types = [selected_type]
            
            logger.info(f"[Smart Orchestrator {self.job_id}] Will generate: {datasheet_types}")
            
            # Step 3: Route to appropriate processors
            results = {}
            progress_per_type = 60 / len(datasheet_types)
            current_progress = 20
            
            for idx, datasheet_type in enumerate(datasheet_types):
                current_progress += progress_per_type
                cache.set(f'smart_job_{self.job_id}', {
                    'status': 'processing',
                    'progress': int(current_progress),
                    'stage': f'Generating {datasheet_type} datasheets ({idx+1}/{len(datasheet_types)})...'
                }, timeout=3600)
                
                try:
                    if datasheet_type == 'mov_equipment':
                        result = self._process_mov(files_dict)
                        results['mov_equipment'] = result
                        
                    elif datasheet_type == 'sdv_streams':
                        result = self._process_sdv(files_dict)
                        results['sdv_streams'] = result
                        
                    elif datasheet_type == 'pressure_instrument':
                        result = self._process_pressure_instrument(files_dict)
                        results['pressure_instrument'] = result
                        
                    elif datasheet_type == 'pump_hydraulic':
                        result = self._process_pump(files_dict)
                        results['pump_hydraulic'] = result
                        
                except Exception as e:
                    logger.error(f"[Smart Orchestrator {self.job_id}] Error processing {datasheet_type}: {str(e)}")
                    results[datasheet_type] = {
                        'success': False,
                        'error': str(e)
                    }
            
            # Step 4: Compile final results
            cache.set(f'smart_job_{self.job_id}', {
                'status': 'processing',
                'progress': 90,
                'stage': 'Compiling results...'
            }, timeout=3600)
            
            successful_types = [k for k, v in results.items() if v.get('success')]
            failed_types = [k for k, v in results.items() if not v.get('success')]
            
            final_result = {
                'success': len(successful_types) > 0,
                'job_id': self.job_id,
                'selected_type': selected_type,
                'generated_types': successful_types,
                'failed_types': failed_types,
                'results': results,
                'summary': {
                    'total_attempted': len(datasheet_types),
                    'successful': len(successful_types),
                    'failed': len(failed_types)
                }
            }
            
            cache.set(f'smart_job_{self.job_id}', {
                'status': 'completed',
                'progress': 100,
                'stage': 'Complete!',
                'result': final_result
            }, timeout=3600)
            
            return final_result
            
        except Exception as e:
            logger.error(f"[Smart Orchestrator {self.job_id}] Error: {str(e)}")
            cache.set(f'smart_job_{self.job_id}', {
                'status': 'failed',
                'progress': 0,
                'stage': 'Failed',
                'error': str(e)
            }, timeout=3600)
            raise
    
    def _process_mov(self, files_dict):
        """Route to MOV processor"""
        from .mov_threading_processor import process_mov_in_thread
        
        # Get P&ID and HMB file info from dict
        pid_info = files_dict.get('pid')
        hmb_info = files_dict.get('hmb')
        
        if not pid_info or not hmb_info:
            return {'success': False, 'error': 'MOV requires both P&ID and HMB files'}
        
        # Files are already saved as temp files, just use the paths
        pid_path = pid_info['path']
        hmb_path = hmb_info['path']
        pid_filename = pid_info['name']
        
        # Process with proper parameters: (pid_path, hmb_path, pid_filename, user_email, job_id)
        result = process_mov_in_thread(
            pid_path,
            hmb_path,
            pid_filename,
            'smart_user@system',  # user_email  
            str(uuid.uuid4())  # job_id
        )
        return result
    
    def _process_sdv(self, files_dict):
        """Route to SDV processor"""
        from .sdv_threading_processor import process_sdv_in_thread
        
        # Get P&ID and HMB file info from dict
        pid_info = files_dict.get('pid')
        hmb_info = files_dict.get('hmb')
        
        if not pid_info or not hmb_info:
            return {'success': False, 'error': 'SDV requires both P&ID and HMB files'}
        
        # Files are already saved as temp files, just use the paths
        pid_path = pid_info['path']
        hmb_path = hmb_info['path']
        pid_filename = pid_info['name']
        
        # Process with proper parameters: (pid_path, hmb_path, pid_filename, user_email, job_id)
        result = process_sdv_in_thread(
            pid_path, 
            hmb_path, 
            pid_filename,
            'smart_user@system',  # user_email
            str(uuid.uuid4())  # job_id
        )
        return result
    
    def _process_pressure_instrument(self, files_dict):
        """Route to Pressure Instrument processor"""
        from .pressure_threading_processor import process_pressure_threading
        
        # Get P&ID file info from dict
        pid_info = files_dict.get('pid')
        
        if not pid_info:
            return {'success': False, 'error': 'Pressure Instrument requires P&ID file'}
        
        # Files are already saved as temp files, use path and original filename
        pid_path = pid_info['path']
        pid_filename = pid_info.get('name', 'Unknown')
        
        # Process synchronously (orchestrator already runs in a thread)
        result = process_pressure_threading(pid_path, str(uuid.uuid4()), pid_filename)
        return result
    
    def _process_pump(self, files_dict):
        """Route to Pump/PFD processor"""
        # Get pump data file from dict
        other_file = files_dict.get('other')
        
        if not other_file:
            return {'success': False, 'error': 'Pump Hydraulic requires pump data file'}
        
        # This would integrate with existing pump hydraulic logic
        return {
            'success': True,
            'message': 'Pump Hydraulic processing (integration pending)',
            'excel_file': None,
            'filename': 'pump_hydraulic.xlsx'
        }


def process_smart_datasheet_async(files_dict, user_preferences, job_id):
    """
    Async wrapper for threading
    Args:
        files_dict: Dictionary with 'pid', 'hmb', 'other' keys
        user_preferences: Dictionary with 'selected_type'
        job_id: UUID string
    """
    orchestrator = SmartDatasheetOrchestrator()
    orchestrator.job_id = job_id
    
    try:
        result = orchestrator.process_smart_datasheet(files_dict, user_preferences)
        return result
    except Exception as e:
        logger.error(f"[Smart Orchestrator {job_id}] Processing failed: {str(e)}")
        cache.set(f'smart_job_{job_id}', {
            'status': 'failed',
            'progress': 0,
            'error': str(e)
        }, timeout=3600)
        raise
