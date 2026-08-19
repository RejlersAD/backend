"""
PID Verification V2 - Multi-Layer Extraction Orchestrator
=========================================================
Coordinates all extraction layers for multi-file, multi-page processing:
  - Layer 1: Free OCR (Tesseract, PyMuPDF, pdfplumber)
  - Layer 2: ML OCR fallback (EasyOCR, PaddleOCR)
  - Layer 3: Vision AI (OpenAI/Claude/Gemini with BYOK)

Handles:
  - Parallel multi-file processing
  - Per-page progress tracking
  - Result aggregation and storage
  - Cost tracking
  - Error handling and retry
"""

import os
import logging
import time
from typing import Dict, List, Optional
from decimal import Decimal
from pathlib import Path
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.utils import timezone

# Import extraction layers
from .extraction_layer1_ocr import Layer1OCRExtractor
from .extraction_layer2_ml_ocr import Layer2MLOCRExtractor
from .extraction_layer3_vision import Layer3VisionAIExtractor

# Import models
from ..models import (
    PIDVExtractionResult,
    PIDVExtractionPage,
    PIDVProject,
    PIDVDocument,
    PIDVLegendSheet,
)

# Import configuration
from ..extraction_config import (
    MULTI_FILE_CONFIG,
    STORAGE_CONFIG,
    PROGRESS_CONFIG,
)

logger = logging.getLogger(__name__)


class MultiLayerExtractionOrchestrator:
    """
    Orchestrates multi-layer extraction across multiple files and pages.
    
    Workflow:
      1. Initialize extraction for project
      2. For each file:
         a. Run Layer 1 (free OCR)
         b. Check if Layer 2 needed (conditional)
         c. Check if Layer 3 needed (based on mode)
         d. Merge all layer results
         e. Save to database + export JSON
      3. Aggregate results across all files
      4. Run cross-file comparison (Layer 4)
    """
    
    def __init__(
        self,
        project_id: str,
        extraction_mode: str = 'balanced',
        user_api_key: Optional[str] = None,
        vision_provider: str = 'openai',
        user_id: Optional[int] = None,
    ):
        """
        Initialize extraction orchestrator.
        
        Args:
            project_id: UUID of PIDVProject
            extraction_mode: 'fast' | 'balanced' | 'deep' | 'vision_only'
            user_api_key: User's BYOK API key (optional)
            vision_provider: 'openai' | 'claude' | 'gemini'
            user_id: ID of user initiating extraction
        """
        self.project_id = project_id
        self.extraction_mode = extraction_mode
        self.user_api_key = user_api_key
        self.vision_provider = vision_provider
        self.user_id = user_id
        
        self.config = MULTI_FILE_CONFIG
        self.storage_config = STORAGE_CONFIG
        
        # Track overall progress
        self.total_files = 0
        self.processed_files = 0
        self.total_pages = 0
        self.processed_pages = 0
        self.total_cost = Decimal('0.0000')
        
        # Store extraction results
        self.extraction_results = []
    
    def extract_all_project_files(self) -> Dict:
        """
        Extract all files in project (P&IDs, legends, reference data).
        
        Returns:
            {
                'project_id': str,
                'extraction_mode': str,
                'total_files': int,
                'total_pages': int,
                'extraction_results': [...],
                'total_cost_usd': float,
                'total_processing_time': float,
                'summary': {...},
            }
        """
        start_time = time.time()
        logger.info(f"[Orchestrator] Starting extraction for project {self.project_id} (mode: {self.extraction_mode})")
        
        # Get project
        try:
            project = PIDVProject.objects.get(project_id=self.project_id)
        except PIDVProject.DoesNotExist:
            logger.error(f"[Orchestrator] Project {self.project_id} not found")
            return {'error': 'Project not found'}
        
        # Get all files in project
        files_to_process = self._get_project_files(project)
        self.total_files = len(files_to_process)
        
        logger.info(f"[Orchestrator] Found {self.total_files} files to process")
        
        # Process files in parallel (if enabled)
        if self.config.get('parallel_processing', {}).get('enabled', True):
            max_workers = self.config['parallel_processing'].get('max_workers', 4)
            results = self._process_files_parallel(files_to_process, max_workers)
        else:
            results = self._process_files_sequential(files_to_process)
        
        # Aggregate results
        summary = self._generate_summary(results)
        
        end_time = time.time()
        total_time = round(end_time - start_time, 2)
        
        logger.info(
            f"[Orchestrator] Extraction complete: "
            f"{self.total_files} files, {self.total_pages} pages "
            f"in {total_time}s (cost: ${float(self.total_cost):.4f})"
        )
        
        return {
            'project_id': str(self.project_id),
            'extraction_mode': self.extraction_mode,
            'total_files': self.total_files,
            'total_pages': self.total_pages,
            'extraction_results': results,
            'total_cost_usd': float(self.total_cost),
            'total_processing_time': total_time,
            'summary': summary,
        }
    
    def extract_single_file(
        self,
        file_path: str,
        file_type: str,
        file_id: Optional[str] = None,
        model_instance: Optional[object] = None,
    ) -> Dict:
        """
        Extract single file with all applicable layers.
        
        Args:
            file_path: Path to file (PDF)
            file_type: 'pid_drawing' | 'legend_sheet' | 'equipment_list' | 'line_list' | 'pms'
            file_id: UUID of PIDVDocument/PIDVLegendSheet/PIDVReferenceData
            model_instance: Django model instance (Document/LegendSheet/ReferenceData)
        
        Returns:
            {
                'file_path': str,
                'file_type': str,
                'extraction_id': str,
                'layer1_result': {...},
                'layer2_result': {...},
                'layer3_result': {...},
                'merged_result': {...},
                'cost_usd': float,
                'processing_time': float,
            }
        """
        start_time = time.time()
        logger.info(f"[Orchestrator] Extracting file: {file_path} (type: {file_type})")
        
        # Determine extraction profile based on file type
        profile = self._get_extraction_profile(file_type)
        
        # LAYER 1: Free OCR (always runs)
        logger.info(f"[Orchestrator] ===== LAYER 1: Free OCR =====")
        layer1_extractor = Layer1OCRExtractor(extraction_profile=profile)
        layer1_result = layer1_extractor.extract_from_pdf(file_path, file_type)
        
        # Update page count
        self.total_pages += layer1_result.get('total_pages', 0)
        
        # LAYER 2: ML OCR Fallback (conditional)
        layer2_result = None
        layer2_extractor = Layer2MLOCRExtractor()
        
        if layer2_extractor.should_trigger(layer1_result):
            logger.info(f"[Orchestrator] ===== LAYER 2: ML OCR Fallback =====")
            layer2_result = self._run_layer2_for_file(file_path, layer1_result)
        else:
            logger.info(f"[Orchestrator] Skipping Layer 2 (OCR confidence sufficient)")
        
        # LAYER 3: Vision AI (conditional based on mode)
        layer3_result = None
        layer3_extractor = Layer3VisionAIExtractor(
            mode=self.extraction_mode,
            user_api_key=self.user_api_key,
            provider=self.vision_provider,
        )
        
        if layer3_extractor.should_trigger(layer1_result, layer2_result):
            logger.info(f"[Orchestrator] ===== LAYER 3: Vision AI =====")
            layer3_result = self._run_layer3_for_file(file_path, layer1_result, layer2_result)
            self.total_cost += layer3_extractor.get_total_cost()
        else:
            logger.info(f"[Orchestrator] Skipping Layer 3 (mode: {self.extraction_mode})")
        
        # Merge all layer results
        merged_result = self._merge_all_layers(layer1_result, layer2_result, layer3_result)
        
        # Save to database
        extraction_db = self._save_extraction_to_db(
            file_path,
            file_type,
            merged_result,
            model_instance,
        )
        
        # Export to JSON (if enabled)
        if self.storage_config.get('json_export', {}).get('enabled', True):
            json_path = self._export_to_json(merged_result, extraction_db)
            logger.info(f"[Orchestrator] Exported to JSON: {json_path}")
        
        end_time = time.time()
        processing_time = round(end_time - start_time, 2)
        
        self.processed_files += 1
        
        result = {
            'file_path': file_path,
            'file_type': file_type,
            'extraction_id': str(extraction_db.extraction_id) if extraction_db else None,
            'layer1_result': layer1_result,
            'layer2_result': layer2_result,
            'layer3_result': layer3_result,
            'merged_result': merged_result,
            'cost_usd': float(merged_result.get('cost_breakdown', {}).get('total_cost_usd', 0)),
            'processing_time': processing_time,
        }
        
        logger.info(
            f"[Orchestrator] File complete: {os.path.basename(file_path)} "
            f"({processing_time}s, ${result['cost_usd']:.4f})"
        )
        
        return result
    
    def _get_project_files(self, project: PIDVProject) -> List[Dict]:
        """Get all files in project for extraction."""
        files = []
        
        # P&ID Documents
        for doc in project.documents.all():
            if doc.original_file:
                files.append({
                    'path': doc.original_file.path,
                    'type': 'pid_drawing',
                    'model': doc,
                    'priority': self.config['file_types']['pid_drawing']['priority'],
                })
        
        # Legend Sheets
        for legend in project.legend_sheets.all():
            if legend.original_file:
                files.append({
                    'path': legend.original_file.path,
                    'type': 'legend_sheet',
                    'model': legend,
                    'priority': self.config['file_types']['legend_sheet']['priority'],
                })
        
        # Reference Data (Equipment List, Line List, PMS)
        for ref_data in project.reference_data.all():
            if ref_data.original_file:
                files.append({
                    'path': ref_data.original_file.path,
                    'type': ref_data.data_type,  # 'equipment_list', 'line_list', 'pms'
                    'model': ref_data,
                    'priority': self.config['file_types'].get(ref_data.data_type, {}).get('priority', 5),
                })
        
        # Sort by priority (lower number = higher priority)
        files.sort(key=lambda x: x['priority'])
        
        return files
    
    def _process_files_parallel(self, files: List[Dict], max_workers: int) -> List[Dict]:
        """Process files in parallel using ThreadPoolExecutor."""
        results = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {
                executor.submit(
                    self.extract_single_file,
                    file_info['path'],
                    file_info['type'],
                    None,
                    file_info['model']
                ): file_info
                for file_info in files
            }
            
            for future in as_completed(future_to_file):
                file_info = future_to_file[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info(f"[Orchestrator] Completed {file_info['path']}")
                except Exception as e:
                    logger.error(f"[Orchestrator] Failed {file_info['path']}: {str(e)}")
                    results.append({
                        'file_path': file_info['path'],
                        'error': str(e),
                    })
        
        return results
    
    def _process_files_sequential(self, files: List[Dict]) -> List[Dict]:
        """Process files sequentially."""
        results = []
        
        for file_info in files:
            try:
                result = self.extract_single_file(
                    file_info['path'],
                    file_info['type'],
                    None,
                    file_info['model']
                )
                results.append(result)
            except Exception as e:
                logger.error(f"[Orchestrator] Failed {file_info['path']}: {str(e)}")
                results.append({
                    'file_path': file_info['path'],
                    'error': str(e),
                })
        
        return results
    
    def _run_layer2_for_file(self, file_path: str, layer1_result: Dict) -> Dict:
        """Run Layer 2 ML OCR for all pages in file."""
        # TODO: Implement per-page Layer 2 processing
        # This is a placeholder
        return {
            'note': 'Layer 2 processing not yet fully implemented',
            'per_page_results': [],
        }
    
    def _run_layer3_for_file(
        self,
        file_path: str,
        layer1_result: Dict,
        layer2_result: Optional[Dict]
    ) -> Dict:
        """Run Layer 3 Vision AI for all pages in file."""
        # TODO: Implement per-page Layer 3 processing
        # This is a placeholder
        return {
            'note': 'Layer 3 processing not yet fully implemented',
            'per_page_results': [],
        }
    
    def _merge_all_layers(
        self,
        layer1: Dict,
        layer2: Optional[Dict],
        layer3: Optional[Dict]
    ) -> Dict:
        """Merge results from all layers into final structure."""
        merged = {
            'file_info': layer1.get('file_info', {}),
            'extraction_config': {
                'mode': self.extraction_mode,
                'layer1_engines': layer1.get('engines_used', []),
                'layer2_engines': [],
                'layer3_provider': self.vision_provider if layer3 else None,
                'user_api_key_used': bool(self.user_api_key),
            },
            'aggregated_data': layer1.get('aggregated_data', {}),
            'per_page_results': layer1.get('per_page_results', []),
            'cost_breakdown': {
                'layer1_cost_usd': 0.0,
                'layer2_cost_usd': 0.0,
                'layer3_cost_usd': 0.0 if not layer3 else sum(
                    p.get('cost_usd', 0) for p in layer3.get('per_page_results', [])
                ),
                'total_cost_usd': 0.0 if not layer3 else sum(
                    p.get('cost_usd', 0) for p in layer3.get('per_page_results', [])
                ),
            },
            'processing_time': {
                'layer1_seconds': layer1.get('total_processing_time', 0),
                'layer2_seconds': 0.0 if not layer2 else layer2.get('total_processing_time', 0),
                'layer3_seconds': 0.0 if not layer3 else sum(
                    p.get('processing_time', 0) for p in layer3.get('per_page_results', [])
                ),
                'total_seconds': layer1.get('total_processing_time', 0),
            },
        }
        
        return merged
    
    def _save_extraction_to_db(
        self,
        file_path: str,
        file_type: str,
        merged_result: Dict,
        model_instance: Optional[object]
    ) -> Optional[PIDVExtractionResult]:
        """Save extraction results to database."""
        try:
            project = PIDVProject.objects.get(project_id=self.project_id)
            
            extraction = PIDVExtractionResult.objects.create(
                project=project,
                document=model_instance if isinstance(model_instance, PIDVDocument) else None,
                legend_sheet=model_instance if isinstance(model_instance, PIDVLegendSheet) else None,
                file_type=file_type,
                file_name=os.path.basename(file_path),
                page_count=merged_result.get('file_info', {}).get('page_count', 1),
                extraction_mode=self.extraction_mode,
                user_api_key_used=bool(self.user_api_key),
                vision_provider=self.vision_provider if merged_result['extraction_config']['layer3_provider'] else '',
                extraction_data=merged_result,
                status='completed',
                total_cost_usd=Decimal(str(merged_result['cost_breakdown']['total_cost_usd'])),
                processing_time_seconds=Decimal(str(merged_result['processing_time']['total_seconds'])),
                extracted_by_id=self.user_id,
                completed_at=timezone.now(),
            )
            
            # Save per-page results
            for page_result in merged_result.get('per_page_results', []):
                PIDVExtractionPage.objects.create(
                    extraction_result=extraction,
                    page_number=page_result['page_num'],
                    layer1_data=page_result.get('tesseract_result', {}),
                    extracted_items=page_result.get('merged_items', {}),
                    avg_confidence=Decimal(str(page_result.get('confidence_score', 0))),
                    items_found=len(page_result.get('merged_items', {}).get('equipment_tags', [])),
                )
            
            logger.info(f"[Orchestrator] Saved extraction to DB: {extraction.extraction_id}")
            return extraction
        
        except Exception as e:
            logger.error(f"[Orchestrator] Failed to save to DB: {str(e)}")
            return None
    
    def _export_to_json(self, merged_result: Dict, extraction_db: Optional[PIDVExtractionResult]) -> str:
        """Export extraction results to JSON file."""
        # TODO: Implement JSON export with S3 upload
        return "export_not_yet_implemented.json"
    
    def _generate_summary(self, results: List[Dict]) -> Dict:
        """Generate summary of all extraction results."""
        summary = {
            'files_processed': len(results),
            'files_succeeded': sum(1 for r in results if 'error' not in r),
            'files_failed': sum(1 for r in results if 'error' in r),
            'total_pages': self.total_pages,
            'total_cost_usd': float(self.total_cost),
            'total_equipment_tags': 0,
            'total_line_numbers': 0,
            'total_instrument_tags': 0,
        }
        
        # Aggregate counts
        for result in results:
            if 'merged_result' in result:
                aggregated = result['merged_result'].get('aggregated_data', {})
                summary['total_equipment_tags'] += len(aggregated.get('equipment_tags', []))
                summary['total_line_numbers'] += len(aggregated.get('line_numbers', []))
                summary['total_instrument_tags'] += len(aggregated.get('instrument_tags', []))
        
        return summary
    
    def _get_extraction_profile(self, file_type: str) -> str:
        """Get extraction profile based on file type."""
        profile_map = {
            'pid_drawing': 'detailed',
            'legend_sheet': 'legend',
            'equipment_list': 'tabular',
            'line_list': 'tabular',
            'pms': 'tabular',
        }
        return profile_map.get(file_type, 'detailed')
