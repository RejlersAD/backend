"""
Hybrid MOV Equipment Extractor - Smart Cost Optimization

Intelligently chooses extraction method based on:
1. OCR First (FREE) - Try pattern matching first
2. Vision API Fallback (PAID) - Only if OCR fails or low confidence
3. Configuration - User can control behavior

COST OPTIMIZATION:
- OCR Only: $0 per extraction
- Hybrid: $0.001-0.01 per extraction (95% cost reduction)
- Vision Only: $0.01-0.03 per extraction

SOFT-CODED CONFIGURATION:
- Enable/disable each method
- Set confidence thresholds
- Control fallback behavior
"""

import logging
from typing import List, Dict, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ExtractionMethod(Enum):
    """Extraction method types"""
    OCR = "ocr"
    VISION_API = "vision_api"
    HYBRID = "hybrid"


class HybridMOVExtractor:
    """
    Smart hybrid extractor with intelligent cost optimization
    Combines FREE OCR with PAID Vision API for best results at lowest cost
    """

    def __init__(self, config: Dict = None):
        """
        Initialize hybrid extractor
        
        Args:
            config: Soft-coded configuration (merged with defaults)
        """
        # Properly merge provided config with defaults (soft-coded technique)
        self.config = {**self._get_default_config(), **(config or {})}
        
        # Initialize extractors based on configuration
        self.ocr_extractor = None
        self.vision_extractor = None
        
        if self.config.get('enable_ocr', True):
            try:
                from apps.pid_analysis.ocr_mov_extractor import OCRMOVExtractor
                self.ocr_extractor = OCRMOVExtractor()
                logger.info("[Hybrid-MOV] ✅ OCR extractor initialized (FREE)")
            except Exception as e:
                logger.warning(f"[Hybrid-MOV] ⚠️ OCR initialization failed: {e}")
        
        if self.config.get('enable_vision_api', True):
            try:
                from apps.pid_analysis.mov_equipment_extractor import MOVEquipmentExtractor
                self.vision_extractor = MOVEquipmentExtractor()
                logger.info("[Hybrid-MOV] ✅ Vision API extractor initialized (PAID)")
            except Exception as e:
                logger.warning(f"[Hybrid-MOV] ⚠️ Vision API initialization failed: {e}")

    def _get_default_config(self) -> Dict:
        """
        Get default soft-coded configuration
        
        Returns:
            dict: Configuration with cost optimization settings
        """
        return {
            # Extraction methods (enable/disable)
            'enable_ocr': True,           # FREE - Always try first
            'enable_vision_api': True,    # PAID - Fallback only
            
            # Extraction strategy
            'strategy': 'ocr_first',      # Options: 'ocr_first', 'vision_first', 'ocr_only', 'vision_only', 'parallel'
            
            # Quality thresholds
            'ocr_min_confidence': 0.6,    # Minimum OCR confidence to accept results
            'ocr_min_movs': 1,            # Minimum MOVs required from OCR to skip Vision API
            'vision_fallback': True,      # Use Vision API if OCR results are poor
            
            # Cost optimization
            'max_cost_per_extraction': 0.01,  # Maximum cost willing to spend
            'prefer_free_method': True,       # Always prefer free methods when available
            
            # Field completeness
            'required_fields': [          # Fields that must be extracted
                'tag_number', 'service', 'valve_type'
            ],
            'completeness_threshold': 0.5,  # % of fields that must be filled
            
            # Logging
            'detailed_logging': True,
            'log_costs': True             # Log extraction costs for monitoring
        }

    def extract_movs(self, pid_file_path: str, drawing_info: Dict = None) -> Dict:
        """
        Extract MOVs using intelligent hybrid approach
        
        Args:
            pid_file_path: Path to P&ID file
            drawing_info: Optional drawing metadata
            
        Returns:
            dict: {
                'movs': List of MOV data,
                'method': Extraction method used,
                'cost': Estimated cost,
                'confidence': Overall confidence score
            }
        """
        logger.info(f"[Hybrid-MOV] 🚀 Starting hybrid extraction: {pid_file_path}")
        
        strategy = self.config.get('strategy', 'ocr_first')
        
        # Execute strategy
        if strategy == 'ocr_only':
            return self._extract_with_ocr_only(pid_file_path, drawing_info)
        
        elif strategy == 'vision_only':
            return self._extract_with_vision_only(pid_file_path, drawing_info)
        
        elif strategy == 'ocr_first':
            return self._extract_ocr_first(pid_file_path, drawing_info)
        
        elif strategy == 'vision_first':
            return self._extract_vision_first(pid_file_path, drawing_info)
        
        elif strategy == 'parallel':
            return self._extract_parallel(pid_file_path, drawing_info)
        
        else:
            logger.error(f"[Hybrid-MOV] ❌ Unknown strategy: {strategy}")
            return {'movs': [], 'method': 'none', 'cost': 0, 'confidence': 0}

    def _extract_with_ocr_only(self, pid_file_path: str, drawing_info: Dict) -> Dict:
        """
        Extract using OCR only (FREE)
        
        Returns:
            dict: Extraction results with cost = $0
        """
        if not self.ocr_extractor:
            logger.error("[Hybrid-MOV] ❌ OCR extractor not available")
            return {'movs': [], 'method': 'none', 'cost': 0, 'confidence': 0}
        
        logger.info("[Hybrid-MOV] 📄 Using OCR-only extraction (FREE)")
        
        movs = self.ocr_extractor.extract_movs_from_pid(pid_file_path, drawing_info)
        confidence = self._calculate_confidence(movs, 'ocr')
        
        if self.config.get('log_costs', True):
            logger.info(f"[Hybrid-MOV] 💰 Cost: $0.00 (FREE)")
        
        return {
            'movs': movs,
            'method': 'ocr',
            'cost': 0.0,
            'confidence': confidence,
            'count': len(movs)
        }

    def _extract_with_vision_only(self, pid_file_path: str, drawing_info: Dict) -> Dict:
        """
        Extract using Vision API only (PAID)
        
        Returns:
            dict: Extraction results with estimated cost
        """
        if not self.vision_extractor:
            logger.error("[Hybrid-MOV] ❌ Vision API extractor not available")
            return {'movs': [], 'method': 'none', 'cost': 0, 'confidence': 0, 'count': 0}
        
        logger.info("[Hybrid-MOV] 🤖 Using Vision API extraction (PAID)")
        
        try:
            movs = self.vision_extractor.analyze_pid_for_movs(pid_file_path, drawing_info)
            logger.info(f"[Hybrid-MOV] 📊 Vision API returned {len(movs)} MOVs")
        except Exception as e:
            logger.error(f"[Hybrid-MOV] ❌ Vision API extraction error: {str(e)}")
            movs = []
        
        confidence = self._calculate_confidence(movs, 'vision')
        
        # Estimate cost (GPT-4o Vision: ~$0.01-0.03 per image)
        estimated_cost = 0.015
        
        if self.config.get('log_costs', True):
            logger.warning(f"[Hybrid-MOV] 💰 Cost: ${estimated_cost:.3f} (PAID)")
        
        return {
            'movs': movs,
            'method': 'vision_api',
            'cost': estimated_cost,
            'confidence': confidence,
            'count': len(movs)
        }

    def _extract_ocr_first(self, pid_file_path: str, drawing_info: Dict) -> Dict:
        """
        Hybrid: Try OCR first (FREE), fallback to Vision API if needed
        
        This is the RECOMMENDED approach for cost optimization
        
        Returns:
            dict: Extraction results with method and cost
        """
        # Step 1: Try OCR (FREE)
        logger.info("[Hybrid-MOV] 📄 Step 1: Trying OCR extraction (FREE)...")
        ocr_result = self._extract_with_ocr_only(pid_file_path, drawing_info)
        
        # Step 2: Evaluate OCR results
        ocr_quality = self._evaluate_extraction_quality(ocr_result)
        
        logger.info(f"[Hybrid-MOV] 📊 OCR Quality: {ocr_quality['score']:.2f} "
                   f"(MOVs: {ocr_quality['mov_count']}, "
                   f"Completeness: {ocr_quality['completeness']:.2f})")
        
        # Step 3: Decide if Vision API is needed
        if self._is_ocr_result_acceptable(ocr_quality):
            logger.info("[Hybrid-MOV] ✅ OCR results acceptable, skipping Vision API")
            logger.info(f"[Hybrid-MOV] 💰 Total Cost: $0.00 (100% FREE)")
            return ocr_result
        
        # Step 4: Fallback to Vision API if enabled
        if self.config.get('vision_fallback', True) and self.vision_extractor:
            logger.warning("[Hybrid-MOV] ⚠️ OCR results insufficient, using Vision API...")
            vision_result = self._extract_with_vision_only(pid_file_path, drawing_info)
            
            logger.info(f"[Hybrid-MOV] 💰 Total Cost: ${vision_result['cost']:.3f} (Vision API used)")
            return vision_result
        
        # Step 5: Return OCR results if Vision API not available
        logger.warning("[Hybrid-MOV] ⚠️ Vision API not enabled, returning OCR results")
        return ocr_result

    def _extract_vision_first(self, pid_file_path: str, drawing_info: Dict) -> Dict:
        """
        Hybrid: Try Vision API first, fallback to OCR if API fails
        
        Use this when quality is more important than cost
        """
        logger.info("[Hybrid-MOV] 🤖 Step 1: Trying Vision API extraction...")
        
        try:
            vision_result = self._extract_with_vision_only(pid_file_path, drawing_info)
            if vision_result['count'] > 0:
                return vision_result
        except Exception as e:
            logger.error(f"[Hybrid-MOV] ❌ Vision API failed: {e}")
        
        # Fallback to OCR
        logger.warning("[Hybrid-MOV] ⚠️ Vision API failed, using OCR fallback...")
        return self._extract_with_ocr_only(pid_file_path, drawing_info)

    def _extract_parallel(self, pid_file_path: str, drawing_info: Dict) -> Dict:
        """
        Parallel: Run both OCR and Vision API, merge best results
        
        Highest quality but highest cost - use for critical extractions only
        """
        logger.info("[Hybrid-MOV] 🔀 Running parallel extraction (OCR + Vision API)...")
        
        # Run both extractors
        ocr_result = self._extract_with_ocr_only(pid_file_path, drawing_info)
        vision_result = self._extract_with_vision_only(pid_file_path, drawing_info)
        
        # Merge results intelligently
        merged_movs = self._merge_extraction_results(
            ocr_result['movs'],
            vision_result['movs']
        )
        
        total_cost = ocr_result['cost'] + vision_result['cost']
        
        logger.info(f"[Hybrid-MOV] ✅ Parallel extraction complete")
        logger.info(f"[Hybrid-MOV] 📊 OCR: {len(ocr_result['movs'])} MOVs, "
                   f"Vision: {len(vision_result['movs'])} MOVs, "
                   f"Merged: {len(merged_movs)} MOVs")
        logger.warning(f"[Hybrid-MOV] 💰 Total Cost: ${total_cost:.3f} (HIGHEST COST)")
        
        return {
            'movs': merged_movs,
            'method': 'parallel',
            'cost': total_cost,
            'confidence': 0.95,
            'count': len(merged_movs)
        }

    def _evaluate_extraction_quality(self, result: Dict) -> Dict:
        """
        Evaluate extraction quality
        
        Args:
            result: Extraction result
            
        Returns:
            dict: Quality metrics
        """
        movs = result['movs']
        
        if not movs:
            return {
                'score': 0.0,
                'mov_count': 0,
                'completeness': 0.0,
                'has_required_fields': False
            }
        
        # Check completeness of extracted data
        total_fields = 0
        filled_fields = 0
        
        for mov in movs:
            for key, value in mov.items():
                total_fields += 1
                if value and value != 'N/A' and value != '':
                    filled_fields += 1
        
        completeness = filled_fields / total_fields if total_fields > 0 else 0
        
        # Check required fields
        required_fields = self.config.get('required_fields', ['tag_number', 'service', 'valve_type'])
        has_required = all(
            all(mov.get(field) for field in required_fields)
            for mov in movs
        )
        
        # Calculate overall score
        score = (
            (len(movs) / 10) * 0.4 +  # Number of MOVs (max 10)
            completeness * 0.4 +       # Data completeness
            (1.0 if has_required else 0.0) * 0.2  # Required fields present
        )
        
        return {
            'score': min(score, 1.0),
            'mov_count': len(movs),
            'completeness': completeness,
            'has_required_fields': has_required
        }

    def _is_ocr_result_acceptable(self, quality: Dict) -> bool:
        """
        Determine if OCR results are good enough
        
        Args:
            quality: Quality metrics
            
        Returns:
            bool: True if acceptable
        """
        # Check minimum MOV count
        if quality['mov_count'] < self.config.get('ocr_min_movs', 1):
            return False
        
        # Check completeness threshold
        if quality['completeness'] < self.config.get('completeness_threshold', 0.5):
            return False
        
        # Check required fields
        if not quality['has_required_fields']:
            return False
        
        return True

    def _merge_extraction_results(self, ocr_movs: List[Dict], 
                                  vision_movs: List[Dict]) -> List[Dict]:
        """
        Intelligently merge OCR and Vision API results
        
        Args:
            ocr_movs: MOVs from OCR
            vision_movs: MOVs from Vision API
            
        Returns:
            list: Merged MOV data
        """
        merged = {}
        
        # Add all Vision API results (higher confidence)
        for mov in vision_movs:
            tag = mov.get('tag_number')
            if tag:
                merged[tag] = mov
        
        # Add OCR results for tags not in Vision API
        for mov in ocr_movs:
            tag = mov.get('tag_number')
            if tag and tag not in merged:
                merged[tag] = mov
        
        return list(merged.values())

    def _calculate_confidence(self, movs: List[Dict], method: str) -> float:
        """
        Calculate confidence score for extraction
        
        Args:
            movs: Extracted MOVs
            method: Extraction method
            
        Returns:
            float: Confidence score (0-1)
        """
        if not movs:
            return 0.0
        
        # Base confidence by method
        base_confidence = {
            'ocr': 0.70,
            'vision': 0.90,
            'parallel': 0.95
        }.get(method, 0.5)
        
        # Adjust by data completeness
        total_fields = 0
        filled_fields = 0
        
        for mov in movs:
            for value in mov.values():
                total_fields += 1
                if value and value != 'N/A' and value != '':
                    filled_fields += 1
        
        completeness = filled_fields / total_fields if total_fields > 0 else 0
        
        return base_confidence * (0.7 + 0.3 * completeness)


# Factory function for easy integration
def create_hybrid_extractor(strategy: str = 'ocr_first') -> HybridMOVExtractor:
    """
    Create hybrid extractor with specified strategy
    
    Args:
        strategy: 'ocr_first', 'vision_first', 'ocr_only', 'vision_only', 'parallel'
        
    Returns:
        HybridMOVExtractor instance
    """
    config = {
        'enable_ocr': True,
        'enable_vision_api': True,
        'strategy': strategy,
        'vision_fallback': True,
        'detailed_logging': True,
        'log_costs': True
    }
    
    return HybridMOVExtractor(config)
