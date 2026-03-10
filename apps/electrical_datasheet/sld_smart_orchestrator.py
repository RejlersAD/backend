"""
Smart SLD Orchestrator
Intelligently processes Single Line Diagrams and routes to appropriate datasheet generators
Supports: Transformer, Diesel Generator, 11KV Switchgear, and other electrical equipment

COST OPTIMIZATION:
- Uses HybridSLDExtractor (PaddleOCR + GPT-3.5-turbo) for 99% cost savings
- Old cost: ~$75 per 1000 pages (GPT-4o Vision)
- New cost: ~$0.50 per 1000 pages (Hybrid approach)
"""

import logging
import uuid
from django.core.cache import cache
from django.conf import settings
import threading
import tempfile
import os
from typing import Dict, List

from .hybrid_sld_extractor import HybridSLDExtractor
from .ai_provider_config import AIProviderConfig

logger = logging.getLogger(__name__)


class SmartSLDOrchestrator:
    """
    Orchestrates electrical datasheet generation from SLD analysis
    
    Features:
    - Intelligent SLD document detection
    - AI-powered equipment extraction
    - Automatic routing to datasheet generators
    - Support for multiple datasheet types simultaneously
    """
    
    # Soft-coded configuration
    ORCHESTRATOR_CONFIG = {
        'max_concurrent_extractions': 3,
        'cache_ttl_seconds': 3600,  # 1 hour
        'async_processing': True,
        'enable_ai_extraction': True,
        'fallback_to_manual': True,
    }
    
    # Datasheet type mappings (soft-coded)
    DATASHEET_TYPE_MAP = {
        'transformer': {
            'name': 'Transformer Datasheet',
            'equipment_types': ['transformer'],
            'generator_class': 'TransformerDatasheetGenerator',
            'priority': 1
        },
        'diesel_generator': {
            'name': 'Diesel Generator Datasheet',
            'equipment_types': ['diesel_generator', 'generator'],
            'generator_class': 'DieselGeneratorDatasheetGenerator',
            'priority': 2
        },
        'switchgear_11kv': {
            'name': '11KV Switchgear Datasheet',
            'equipment_types': ['switchgear_11kv', 'switchgear'],
            'generator_class': 'SwitchgearDatasheetGenerator',
            'priority': 3
        }
    }
    
    def __init__(self):
        self.job_id = str(uuid.uuid4())
        self.extractor = HybridSLDExtractor()  # Cost-optimized hybrid extractor
        self.config = AIProviderConfig
        
        # Log cost estimate
        strategy = self.config.get_active_strategy()
        logger.info(f"[SmartSLDOrchestrator {self.job_id}] Using strategy: {strategy['name']}")
        logger.info(f"[SmartSLDOrchestrator {self.job_id}] Estimated cost: ${strategy['estimated_cost_per_page']:.4f} per page")
        
    def detect_sld_files(self, uploaded_files) -> Dict:
        """
        Detect and classify uploaded SLD files
        
        Returns:
            Dict with file classifications
        """
        detected = {
            'sld_files': [],
            'other_files': [],
            'total_count': len(uploaded_files),
            'has_sld': False
        }
        
        for file in uploaded_files:
            filename_lower = file.name.lower()
            
            # Detect SLD files
            sld_keywords = ['sld', 'single line', 'single-line', 'oneline', 'one-line', 
                          'electrical', 'power distribution', '11kv', 'switchgear']
            
            is_sld = any(keyword in filename_lower for keyword in sld_keywords)
            
            if is_sld:
                detected['sld_files'].append(file)
                detected['has_sld'] = True
            else:
                detected['other_files'].append(file)
        
        logger.info(f"[SmartSLDOrchestrator {self.job_id}] Detected {len(detected['sld_files'])} SLD files")
        return detected
    
    def determine_datasheet_types(self, detected_files: Dict, user_selection: Dict = None) -> List[str]:
        """
        Determine which datasheet types to generate
        
        Args:
            detected_files: Result from detect_sld_files()
            user_selection: User's explicit datasheet type selections
                {
                    'transformer': True/False,
                    'diesel_generator': True/False,
                    'switchgear_11kv': True/False
                }
        
        Returns:
            List of datasheet types to generate
        """
        datasheet_types = []
        
        # If user explicitly selected types, use those
        if user_selection:
            for ds_type, is_selected in user_selection.items():
                if is_selected and ds_type in self.DATASHEET_TYPE_MAP:
                    datasheet_types.append(ds_type)
            
            logger.info(f"[SmartSLDOrchestrator {self.job_id}] User selected: {datasheet_types}")
            return datasheet_types
        
        # Auto-detection fallback (if no user selection)
        if detected_files['has_sld']:
            # Default to all types if SLD detected
            datasheet_types = list(self.DATASHEET_TYPE_MAP.keys())
            logger.info(f"[SmartSLDOrchestrator {self.job_id}] Auto-detected all types")
        
        return datasheet_types
    
    def process_smart_sld(self, uploaded_files, project_info: Dict, 
                         datasheet_selection: Dict, analysis_options: Dict = None) -> Dict:
        """
        Main orchestration method - processes SLD files and generates datasheets
        
        Args:
            uploaded_files: List of uploaded file objects
            project_info: Project information dict
                {
                    'drawing_number': str,
                    'drawing_title': str,
                    'revision': str,
                    'voltage_level': str,
                    'project_name': str,
                    'area': str
                }
            datasheet_selection: Selected datasheet types
                {
                    'transformer': True/False,
                    'diesel_generator': True/False,
                    'switchgear_11kv': True/False
                }
            analysis_options: Analysis options (optional)
                {
                    'extract_tags': bool,
                    'detect_equipment_types': bool,
                    'extract_specifications': bool,
                    'generate_datasheets': bool,
                    'identify_connections': bool
                }
        
        Returns:
            Dict with processing results
        """
        try:
            logger.info(f"[SmartSLDOrchestrator {self.job_id}] Starting SLD processing...")
            
            # Step 1: Detect SLD files
            detected_files = self.detect_sld_files(uploaded_files)
            
            if not detected_files['has_sld']:
                return {
                    'success': False,
                    'error': 'No SLD files detected in uploaded files',
                    'job_id': self.job_id
                }
            
            # Step 2: Determine datasheet types to generate
            datasheet_types = self.determine_datasheet_types(detected_files, datasheet_selection)
            
            if not datasheet_types:
                return {
                    'success': False,
                    'error': 'No datasheet types selected',
                    'job_id': self.job_id
                }
            
            # Step 3: Extract equipment from SLD files using Hybrid AI (Cost-Optimized)
            extraction_results = []
            total_cost = 0.0
            
            for sld_file in detected_files['sld_files']:
                logger.info(f"[SmartSLDOrchestrator {self.job_id}] Extracting from {sld_file.name}...")
                
                # Save file temporarily
                temp_path = self._save_temp_file(sld_file)
                
                try:
                    # Extract equipment using Hybrid Extractor (PaddleOCR + GPT-3.5-turbo)
                    extraction = self.extractor.extract_from_file(temp_path, datasheet_types)
                    extraction['source_file'] = sld_file.name
                    extraction_results.append(extraction)
                    
                    # Track cost
                    if 'actual_cost' in extraction:
                        file_cost = extraction['actual_cost'].get('total_cost', 0.0)
                        total_cost += file_cost
                        logger.info(f"[SmartSLDOrchestrator {self.job_id}] File cost: ${file_cost:.4f}")
                    
                finally:
                    # Cleanup temp file
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
            
            # Step 4: Aggregate equipment by type
            aggregated_equipment = self._aggregate_equipment(extraction_results, datasheet_types)
            
            # Step 5: Generate datasheets (placeholder - implement generators later)
            generated_datasheets = self._generate_datasheets(
                aggregated_equipment, 
                project_info, 
                datasheet_types,
                analysis_options
            )
            
            # Step 6: Cache results
            cache_key = f'sld_orchestrator_{self.job_id}'
            cache.set(cache_key, {
                'extraction_results': extraction_results,
                'aggregated_equipment': aggregated_equipment,
                'generated_datasheets': generated_datasheets,
                'project_info': project_info
            }, self.ORCHESTRATOR_CONFIG['cache_ttl_seconds'])
            
            result = {
                'total_cost': total_cost,
                'cost_breakdown': f"${total_cost:.4f} for {len(detected_files['sld_files'])} files",
                'detailed_results': extraction_results
            }
            
            logger.info(f"[SmartSLDOrchestrator {self.job_id}] ✅ Complete: {aggregated_equipment['total_count']} equipment, cost: ${total_cost:.4f}
                'equipment_by_type': aggregated_equipment['by_type_count'],
                'datasheets_generated': generated_datasheets,
                'extraction_method': extraction_results[0].get('extraction_method') if extraction_results else 'none',
                'confidence': extraction_results[0].get('confidence') if extraction_results else 'none',
                'detailed_results': extraction_results
            }
            
            logger.info(f"[SmartSLDOrchestrator {self.job_id}] ✅ Processing complete: {aggregated_equipment['total_count']} equipment extracted")
            return result
            
        except Exception as e:
            logger.error(f"[SmartSLDOrchestrator {self.job_id}] ❌ Error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'job_id': self.job_id
            }
    
    def _save_temp_file(self, uploaded_file) -> str:
        """Save uploaded file to temporary location"""
        _, ext = os.path.splitext(uploaded_file.name)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        
        for chunk in uploaded_file.chunks():
            temp_file.write(chunk)
        
        temp_file.close()
        return temp_file.name
    
    def _aggregate_equipment(self, extraction_results: List[Dict], 
                            datasheet_types: List[str]) -> Dict:
        """
        Aggregate equipment from multiple extraction results
        """
        aggregated = {
            'all_equipment': [],
            'by_type': {},
            'by_type_count': {},
            'total_count': 0
        }
        
        for result in extraction_results:
            equipment_list = result.get('equipment', [])
            aggregated['all_equipment'].extend(equipment_list)
            
            # Organize by type
            for eq in equipment_list:
                eq_type = eq.get('type', 'unknown')
                if eq_type not in aggregated['by_type']:
                    aggregated['by_type'][eq_type] = []
                aggregated['by_type'][eq_type].append(eq)
        
        # Count by type
        for eq_type, eq_list in aggregated['by_type'].items():
            aggregated['by_type_count'][eq_type] = len(eq_list)
        
        aggregated['total_count'] = len(aggregated['all_equipment'])
        
        return aggregated
    
    def _generate_datasheets(self, aggregated_equipment: Dict, project_info: Dict, 
                           datasheet_types: List[str], analysis_options: Dict = None) -> List[Dict]:
        """
        Generate datasheets from extracted equipment
        
        Returns:
            List of generated datasheet objects
        """
        generated = []
        
        for ds_type in datasheet_types:
            if ds_type not in self.DATASHEET_TYPE_MAP:
                continue
            
            config = self.DATASHEET_TYPE_MAP[ds_type]
            equipment_for_type = []
            
            # Collect equipment for this datasheet type
            for eq_type in config['equipment_types']:
                equipment_for_type.extend(aggregated_equipment['by_type'].get(eq_type, []))
            
            if equipment_for_type:
                # Create datasheet record (simplified for now)
                datasheet = {
                    'type': ds_type,
                    'name': config['name'],
                    'equipment_count': len(equipment_for_type),
                    'equipment_details': equipment_for_type,
                    'project_info': project_info,
                    'status': 'draft',
                    'generated_by': 'smart_sld_orchestrator'
                }
                
                generated.append(datasheet)
                logger.info(f"[SmartSLDOrchestrator {self.job_id}] Generated {ds_type} datasheet with {len(equipment_for_type)} equipment")
        
        return generated
    
    def get_job_status(self, job_id: str) -> Dict:
        """
        Get status of a processing job
        """
        cache_key = f'sld_orchestrator_{job_id}'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            return {
                'status': 'completed',
                'job_id': job_id,
                'data': cached_data
            }
        else:
            return {
                'status': 'not_found',
                'job_id': job_id
            }
