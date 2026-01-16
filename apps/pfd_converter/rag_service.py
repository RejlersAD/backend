"""
RAG Service for PFD Analysis
=============================

Retrieval Augmented Generation system that uses reference PFDs from S3
to enhance analysis of uploaded PFDs.

Process:
1. When user uploads PFD, extract basic info
2. Search S3 for similar reference PFDs (same project, similar equipment)
3. Retrieve and analyze reference PFDs
4. Use reference data as context for analyzing the uploaded PFD
5. Provide more accurate analysis based on learned patterns
"""

import logging
from typing import Dict, List, Optional
from .s3_pfd_service import S3PFDService
from .five_stage_analyzer import FiveStageAnalyzer
from openai import OpenAI
from decouple import config
import json

logger = logging.getLogger(__name__)

OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
openai_client = OpenAI(api_key=OPENAI_API_KEY)


class PFDRAGService:
    """
    RAG Service for intelligent PFD analysis using reference documents
    """
    
    def __init__(self):
        self.s3_service = S3PFDService()
        self.analyzer = FiveStageAnalyzer()
        self.model = config('OPENAI_MODEL', default='gpt-4o')
        
    def get_reference_context(self, document_info: Dict) -> Dict:
        """
        Retrieve relevant reference PFDs from S3 based on document info
        
        Args:
            document_info: Document metadata (project_code, document_number, etc.)
            
        Returns:
            Dictionary with reference PFD information
        """
        project_code = document_info.get('project_code', '')
        
        if not self.s3_service.s3_enabled:
            logger.warning("⚠️ S3 not enabled, RAG context unavailable")
            return {
                'references_available': False,
                'message': 'S3 not configured - analyzing without reference context'
            }
        
        logger.info(f"🔍 RAG: Searching for reference PFDs for project {project_code}")
        
        # Get reference PFDs from S3
        reference_pfds = self.s3_service.get_reference_pfds(
            project_code=project_code,
            limit=5
        )
        
        if not reference_pfds:
            logger.info(f"ℹ️ No reference PFDs found for project {project_code}")
            return {
                'references_available': False,
                'message': f'No reference PFDs found for project {project_code}'
            }
        
        logger.info(f"✅ Found {len(reference_pfds)} reference PFDs")
        
        return {
            'references_available': True,
            'reference_count': len(reference_pfds),
            'references': [
                {
                    'key': pfd['key'],
                    'size_mb': pfd['size_mb'],
                    'filename': pfd['key'].split('/')[-1]
                }
                for pfd in reference_pfds
            ]
        }
    
    def analyze_with_rag_context(self, pfd_path: str, document_info: Dict) -> Dict:
        """
        Analyze PFD using RAG - retrieve reference PFDs and use as context
        
        Args:
            pfd_path: Path to uploaded PFD file
            document_info: Document metadata
            
        Returns:
            Analysis results enhanced with reference context
        """
        logger.info("🚀 Starting RAG-enhanced PFD analysis")
        
        # Step 1: Get reference context
        reference_context = self.get_reference_context(document_info)
        
        # Step 2: If references available, download and analyze one
        reference_analysis = None
        if reference_context.get('references_available'):
            try:
                # Download first reference PFD
                first_ref = reference_context['references'][0]
                logger.info(f"⬇️ Downloading reference PFD: {first_ref['filename']}")
                
                ref_path = self.s3_service.download_pfd(first_ref['key'])
                
                if ref_path:
                    logger.info(f"📊 Analyzing reference PFD for pattern learning")
                    
                    # Quick analysis of reference (just stage 1 for patterns)
                    reference_analysis = self._quick_reference_analysis(ref_path)
                    
                    # Clean up
                    import os
                    if os.path.exists(ref_path):
                        os.remove(ref_path)
                        
            except Exception as e:
                logger.warning(f"⚠️ Reference analysis failed (non-critical): {str(e)}")
        
        # Step 3: Analyze uploaded PFD with reference context
        logger.info("🎯 Analyzing uploaded PFD with enhanced context")
        
        analysis_results = self._analyze_with_context(
            pfd_path,
            document_info,
            reference_analysis
        )
        
        # Add RAG metadata
        analysis_results['rag_context'] = {
            'references_used': reference_context.get('references_available', False),
            'reference_count': reference_context.get('reference_count', 0),
            'reference_files': reference_context.get('references', [])
        }
        
        return analysis_results
    
    def _quick_reference_analysis(self, ref_pfd_path: str) -> Optional[Dict]:
        """
        Quick analysis of reference PFD to extract patterns
        """
        try:
            from .five_stage_analyzer import analyze_pfd_five_stages
            
            ref_info = {
                'document_number': 'REFERENCE',
                'document_title': 'Reference PFD'
            }
            
            # Only analyze stage 1 for pattern learning
            results = analyze_pfd_five_stages(
                ref_pfd_path,
                ref_info,
                stages_to_run=[1]  # Only run stage 1
            )
            
            return results.get('stage1', {})
            
        except Exception as e:
            logger.error(f"Reference analysis error: {str(e)}")
            return None
    
    def _analyze_with_context(
        self, 
        pfd_path: str, 
        document_info: Dict,
        reference_analysis: Optional[Dict]
    ) -> Dict:
        """
        Analyze PFD with reference context using enhanced prompts
        """
        from .five_stage_analyzer import analyze_pfd_five_stages
        
        # If we have reference analysis, create enhanced context prompt
        if reference_analysis:
            # Extract patterns from reference
            ref_modules = reference_analysis.get('modules', [])
            ref_equipment_types = list(set([
                mod.get('module_type', 'unknown')
                for mod in ref_modules
            ]))
            
            logger.info(f"📚 Reference context: {len(ref_modules)} modules, types: {ref_equipment_types}")
            
            # Add reference context to document info
            document_info['reference_context'] = {
                'typical_modules': ref_equipment_types,
                'module_count': len(ref_modules),
                'equipment_patterns': ref_modules[:3]  # First 3 as examples
            }
        
        # Run full 5-stage analysis with context
        return analyze_pfd_five_stages(pfd_path, document_info)
    
    def get_rag_summary(self, document_info: Dict) -> str:
        """
        Generate a summary of RAG capabilities for this document
        """
        context = self.get_reference_context(document_info)
        
        if not context.get('references_available'):
            return "⚠️ No reference PFDs available - analyzing without historical context"
        
        ref_count = context.get('reference_count', 0)
        return f"✅ RAG Enabled: Using {ref_count} reference PFDs from S3 bucket for enhanced analysis"
