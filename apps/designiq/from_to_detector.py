"""
FROM-TO Determination Integration Module
Coordinates symbol detection, endpoint association, and OCR mapping.
"""

import re
import numpy as np
from typing import List, Dict, Optional
import logging

from .direction_symbols import detect_direction_symbols, DirectionSymbol
from .endpoint_association import (
    associate_symbols_to_endpoints,
    infer_from_to_for_line,
    Line,
    Endpoint,
    EndpointRoles
)
from .ocr_mapping import build_from_to_map, OCRItem

logger = logging.getLogger(__name__)


class FromToDetector:
    """
    Main class for FROM-TO determination using computer vision.
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize detector with configuration.
        
        Args:
            config: Configuration dict with parameters for:
                - Symbol detection (min_symbol_area, max_symbol_area, etc.)
                - Endpoint association (endpoint_radius)
                - OCR mapping (max_ocr_distance, line_number_pattern)
        """
        self.config = config or {}
        
        # Default configuration
        self.config.setdefault('min_symbol_area', 50)
        self.config.setdefault('max_symbol_area', 5000)
        self.config.setdefault('epsilon_factor', 0.02)
        self.config.setdefault('min_vertices', 3)
        self.config.setdefault('max_vertices', 7)
        self.config.setdefault('canny_low', 50)
        self.config.setdefault('canny_high', 150)
        self.config.setdefault('endpoint_radius', 0.05)  # 5% of image
        self.config.setdefault('max_ocr_distance', 0.1)  # 10% of image
        self.config.setdefault('line_number_pattern', r'\b\d{1,2}[\"\']?-[A-Z]{2,3}-\d{4}\b')
        
        # Compile regex pattern
        self.line_number_regex = re.compile(
            self.config['line_number_pattern'],
            re.IGNORECASE
        )
        
        logger.info("✅ FROM-TO Detector initialized")
        logger.info(f"   Symbol area range: {self.config['min_symbol_area']}-{self.config['max_symbol_area']}")
        logger.info(f"   Endpoint radius: {self.config['endpoint_radius']}")
        logger.info(f"   OCR max distance: {self.config['max_ocr_distance']}")
    
    def detect_from_to(
        self,
        image: np.ndarray,
        lines: List[Dict],
        ocr_items: List[Dict]
    ) -> Dict[str, Dict[str, Optional[str]]]:
        """
        Main method to detect FROM-TO relationships.
        
        Args:
            image: Input P&ID image (grayscale or BGR)
            lines: List of line dicts with:
                - id: str
                - points: List[Tuple[float, float]] (normalized coordinates)
            ocr_items: List of OCR result dicts with:
                - id: str
                - text: str
                - bbox: Tuple[float, float, float, float] (normalized)
        
        Returns:
            Dict mapping line_id to {"from_line": str, "to_line": str}
        """
        logger.info("🚀 Starting FROM-TO detection pipeline")
        
        # Convert input dicts to dataclass objects
        line_objects = [
            Line(id=l['id'], points=l['points'])
            for l in lines
        ]
        
        ocr_objects = [
            OCRItem(id=o['id'], text=o['text'], bbox=o['bbox'])
            for o in ocr_items
        ]
        
        logger.info(f"   Input: {len(line_objects)} lines, {len(ocr_objects)} OCR items")
        
        # Step 1: Detect direction symbols
        logger.info("📍 Step 1: Detecting direction symbols...")
        symbols = detect_direction_symbols(image, self.config)
        
        if not symbols:
            logger.warning("   ⚠️ No symbols detected, returning empty FROM-TO map")
            return {line.id: {"from_line": None, "to_line": None} for line in line_objects}
        
        # Step 2: Associate symbols with endpoints
        logger.info("📍 Step 2: Associating symbols with line endpoints...")
        endpoints_map = associate_symbols_to_endpoints(
            line_objects,
            symbols,
            self.config['endpoint_radius']
        )
        
        # Step 3: Infer FROM/TO roles
        logger.info("📍 Step 3: Inferring FROM/TO roles...")
        symbols_by_id = {s.id: s for s in symbols}
        roles_map = {}
        
        for line in line_objects:
            endpoints = endpoints_map.get(line.id)
            if not endpoints:
                continue
            
            roles = infer_from_to_for_line(line, endpoints, symbols_by_id)
            roles_map[line.id] = roles
        
        logger.info(f"   ✅ Inferred roles for {len(roles_map)} lines")
        
        # Step 4: Map to OCR line numbers
        logger.info("📍 Step 4: Mapping endpoints to OCR items...")
        from_to_map = build_from_to_map(
            line_objects,
            endpoints_map,
            roles_map,
            ocr_objects,
            self.config['max_ocr_distance'],
            self.line_number_regex
        )
        
        logger.info("✅ FROM-TO detection complete")
        
        return from_to_map
    
    def update_config(self, new_config: Dict):
        """Update configuration parameters"""
        self.config.update(new_config)
        
        # Recompile regex if pattern changed
        if 'line_number_pattern' in new_config:
            self.line_number_regex = re.compile(
                self.config['line_number_pattern'],
                re.IGNORECASE
            )
        
        logger.info(f"✅ Configuration updated: {list(new_config.keys())}")
