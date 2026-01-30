"""
OCR Mapping Module
Maps line endpoints to OCR-detected line numbers and equipment nodes.
"""

import re
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Pattern
import logging

from .endpoint_association import Endpoint, EndpointRoles, Line

logger = logging.getLogger(__name__)


@dataclass
class OCRItem:
    """Represents an OCR detection"""
    id: str
    text: str
    bbox: Tuple[float, float, float, float]  # (x_min, y_min, x_max, y_max) normalized


def map_endpoint_to_ocr(
    endpoint: Endpoint,
    ocr_items: List[OCRItem],
    max_distance: float,
    line_direction: Tuple[float, float],
    line_number_regex: Pattern
) -> Optional[OCRItem]:
    """
    Map an endpoint to the nearest relevant OCR item.
    
    Args:
        endpoint: Endpoint to map
        ocr_items: List of OCR detections
        max_distance: Maximum distance for matching (normalized)
        line_direction: Direction vector of the line at endpoint
        line_number_regex: Compiled regex pattern for line numbers
    
    Returns:
        Best matching OCR item or None
    """
    candidates = []
    
    for ocr in ocr_items:
        # Compute center of OCR bbox
        ocr_center = (
            (ocr.bbox[0] + ocr.bbox[2]) / 2,
            (ocr.bbox[1] + ocr.bbox[3]) / 2
        )
        
        # Compute distance
        dist = np.sqrt(
            (endpoint.x - ocr_center[0])**2 +
            (endpoint.y - ocr_center[1])**2
        )
        
        if dist > max_distance:
            continue
        
        # Base score: inverse of distance
        score = 1.0 / (dist + 0.01)  # Avoid division by zero
        
        # Bonus: regex match (line number pattern)
        if line_number_regex and line_number_regex.search(ocr.text):
            score *= 2.0  # Double score for line numbers
        
        # Bonus: alignment with line direction
        if line_direction[0] != 0 or line_direction[1] != 0:
            # Vector from endpoint to OCR center
            to_ocr = (ocr_center[0] - endpoint.x, ocr_center[1] - endpoint.y)
            to_ocr_norm = normalize_vector(to_ocr)
            
            # Dot product with line direction
            dot = to_ocr_norm[0] * line_direction[0] + to_ocr_norm[1] * line_direction[1]
            
            # Bonus if aligned (dot > 0)
            if dot > 0:
                score *= (1.0 + dot * 0.5)
        
        candidates.append((score, ocr))
    
    if not candidates:
        return None
    
    # Return best scoring candidate
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def build_from_to_map(
    lines: List[Line],
    endpoints_map: Dict[str, Dict[str, Endpoint]],
    roles_map: Dict[str, EndpointRoles],
    ocr_items: List[OCRItem],
    max_distance: float,
    line_number_regex: Pattern
) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Build FROM/TO mapping for all lines.
    
    Args:
        lines: List of Line objects
        endpoints_map: Dict mapping line_id to endpoint dict
        roles_map: Dict mapping line_id to EndpointRoles
        ocr_items: List of OCR detections
        max_distance: Maximum distance for OCR matching (normalized)
        line_number_regex: Compiled regex for line numbers
    
    Returns:
        Dict mapping line_id to {"from_line": text, "to_line": text}
    """
    from_to_map = {}
    
    logger.info(f"  🗺️ Building FROM/TO map for {len(lines)} lines")
    logger.info(f"     Available OCR items: {len(ocr_items)}")
    logger.info(f"     Max OCR distance: {max_distance:.3f}")
    
    for line in lines:
        endpoints = endpoints_map.get(line.id)
        roles = roles_map.get(line.id)
        
        if not endpoints or not roles:
            continue
        
        # Get direction vectors at endpoints
        if len(line.points) >= 2:
            # Direction at start
            v_start = (
                line.points[1][0] - line.points[0][0],
                line.points[1][1] - line.points[0][1]
            )
            v_start_norm = normalize_vector(v_start)
            
            # Direction at end
            v_end = (
                line.points[-1][0] - line.points[-2][0],
                line.points[-1][1] - line.points[-2][1]
            )
            v_end_norm = normalize_vector(v_end)
        else:
            v_start_norm = (1.0, 0.0)
            v_end_norm = (1.0, 0.0)
        
        # Map endpoints to OCR
        start_ocr = map_endpoint_to_ocr(
            endpoints["start"],
            ocr_items,
            max_distance,
            v_start_norm,
            line_number_regex
        )
        
        end_ocr = map_endpoint_to_ocr(
            endpoints["end"],
            ocr_items,
            max_distance,
            v_end_norm,
            line_number_regex
        )
        
        # Assign based on roles
        from_line = None
        to_line = None
        
        if roles.start_role == "FROM":
            from_line = start_ocr.text if start_ocr else None
        else:  # start_role == "TO"
            to_line = start_ocr.text if start_ocr else None
        
        if roles.end_role == "FROM":
            from_line = end_ocr.text if end_ocr else None
        else:  # end_role == "TO"
            to_line = end_ocr.text if end_ocr else None
        
        from_to_map[line.id] = {
            "from_line": from_line,
            "to_line": to_line
        }
    
    # Log statistics
    with_from = sum(1 for v in from_to_map.values() if v.get('from_line'))
    with_to = sum(1 for v in from_to_map.values() if v.get('to_line'))
    with_both = sum(1 for v in from_to_map.values() if v.get('from_line') and v.get('to_line'))
    
    logger.info(f"  ✅ FROM/TO mapping complete:")
    logger.info(f"     Lines with FROM: {with_from}/{len(from_to_map)}")
    logger.info(f"     Lines with TO: {with_to}/{len(from_to_map)}")
    logger.info(f"     Lines with BOTH: {with_both}/{len(from_to_map)}")
    
    return from_to_map


def normalize_vector(v: Tuple[float, float]) -> Tuple[float, float]:
    """Normalize a 2D vector"""
    magnitude = np.sqrt(v[0]**2 + v[1]**2)
    if magnitude < 1e-6:
        return (0.0, 0.0)
    return (v[0] / magnitude, v[1] / magnitude)
