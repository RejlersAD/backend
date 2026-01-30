"""
Endpoint Association Module
Associates direction symbols with line endpoints and infers FROM/TO roles.
"""

import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Literal
import logging

from .direction_symbols import DirectionSymbol

logger = logging.getLogger(__name__)

EndpointRole = Literal["FROM", "TO"]


@dataclass
class Endpoint:
    """Represents an endpoint of a pipeline line"""
    x: float  # Normalized coordinate
    y: float  # Normalized coordinate
    symbol_id: Optional[str] = None


@dataclass
class EndpointRoles:
    """Role assignment for line endpoints"""
    start_role: EndpointRole
    end_role: EndpointRole


@dataclass
class Line:
    """Represents a detected pipeline line"""
    id: str
    points: List[Tuple[float, float]]  # Normalized coordinates


def associate_symbols_to_endpoints(
    lines: List[Line],
    symbols: List[DirectionSymbol],
    radius: float
) -> Dict[str, Dict[str, Endpoint]]:
    """
    Associate direction symbols with line endpoints based on proximity.
    
    Args:
        lines: List of Line objects with points in normalized coordinates
        symbols: List of detected DirectionSymbol objects
        radius: Maximum distance for association (normalized, e.g., 0.05 = 5% of image)
    
    Returns:
        Dict mapping line_id to {"start": Endpoint, "end": Endpoint}
    """
    endpoints_map = {}
    
    logger.info(f"  🔗 Associating {len(symbols)} symbols with {len(lines)} line endpoints")
    logger.info(f"     Association radius: {radius:.3f} (normalized)")
    
    for line in lines:
        if not line.points or len(line.points) < 2:
            continue
        
        # Define start and end points
        start_point = line.points[0]
        end_point = line.points[-1]
        
        # Create endpoint objects
        start_endpoint = Endpoint(x=start_point[0], y=start_point[1])
        end_endpoint = Endpoint(x=end_point[0], y=end_point[1])
        
        # Find nearest symbol for start endpoint
        min_dist_start = float('inf')
        nearest_symbol_start = None
        
        for symbol in symbols:
            dist = euclidean_distance(
                (start_endpoint.x, start_endpoint.y),
                symbol.centroid
            )
            if dist < min_dist_start and dist <= radius:
                min_dist_start = dist
                nearest_symbol_start = symbol
        
        if nearest_symbol_start:
            start_endpoint.symbol_id = nearest_symbol_start.id
        
        # Find nearest symbol for end endpoint
        min_dist_end = float('inf')
        nearest_symbol_end = None
        
        for symbol in symbols:
            dist = euclidean_distance(
                (end_endpoint.x, end_endpoint.y),
                symbol.centroid
            )
            if dist < min_dist_end and dist <= radius:
                min_dist_end = dist
                nearest_symbol_end = symbol
        
        if nearest_symbol_end:
            end_endpoint.symbol_id = nearest_symbol_end.id
        
        endpoints_map[line.id] = {
            "start": start_endpoint,
            "end": end_endpoint
        }
    
    # Log statistics
    total_endpoints = len(endpoints_map) * 2
    associated_endpoints = sum(
        (1 if ep['start'].symbol_id else 0) + (1 if ep['end'].symbol_id else 0)
        for ep in endpoints_map.values()
    )
    
    logger.info(f"  ✅ Associated {associated_endpoints}/{total_endpoints} endpoints with symbols")
    
    return endpoints_map


def infer_from_to_for_line(
    line: Line,
    endpoints: Dict[str, Endpoint],
    symbols_by_id: Dict[str, DirectionSymbol]
) -> EndpointRoles:
    """
    Infer FROM/TO roles for a line's endpoints based on symbol orientations.
    
    Args:
        line: Line object with ordered points
        endpoints: Dict with "start" and "end" Endpoint objects
        symbols_by_id: Dict mapping symbol ID to DirectionSymbol
    
    Returns:
        EndpointRoles with start_role and end_role
    """
    if len(line.points) < 2:
        # Fallback: start is FROM, end is TO
        return EndpointRoles(start_role="FROM", end_role="TO")
    
    # Compute local direction vectors
    # v_start: direction FROM start (first segment)
    v_start = normalize_vector(
        (line.points[1][0] - line.points[0][0],
         line.points[1][1] - line.points[0][1])
    )
    
    # v_end: direction TO end (last segment)
    v_end = normalize_vector(
        (line.points[-1][0] - line.points[-2][0],
         line.points[-1][1] - line.points[-2][1])
    )
    
    # Get associated symbols
    start_endpoint = endpoints["start"]
    end_endpoint = endpoints["end"]
    
    start_symbol = symbols_by_id.get(start_endpoint.symbol_id) if start_endpoint.symbol_id else None
    end_symbol = symbols_by_id.get(end_endpoint.symbol_id) if end_endpoint.symbol_id else None
    
    # Classification scores
    start_is_downstream = None  # True = TO, False = FROM
    end_is_downstream = None
    
    # Analyze start endpoint
    if start_symbol and start_symbol.orientation is not None:
        # Symbol direction vector
        v_sym = (np.cos(start_symbol.orientation), np.sin(start_symbol.orientation))
        
        # Dot product with line direction
        dot = v_sym[0] * v_start[0] + v_sym[1] * v_start[1]
        
        # If dot > 0: symbol points along line direction → downstream (TO)
        # If dot < 0: symbol points against line direction → upstream (FROM)
        if dot > 0.3:  # Threshold to avoid noise
            start_is_downstream = True
        elif dot < -0.3:
            start_is_downstream = False
    
    # Analyze end endpoint
    if end_symbol and end_symbol.orientation is not None:
        v_sym = (np.cos(end_symbol.orientation), np.sin(end_symbol.orientation))
        dot = v_sym[0] * v_end[0] + v_sym[1] * v_end[1]
        
        if dot > 0.3:
            end_is_downstream = True
        elif dot < -0.3:
            end_is_downstream = False
    
    # Decision logic
    if start_is_downstream is False and end_is_downstream is True:
        # Clear case: start is FROM, end is TO
        return EndpointRoles(start_role="FROM", end_role="TO")
    
    elif start_is_downstream is True and end_is_downstream is False:
        # Reverse: start is TO, end is FROM
        return EndpointRoles(start_role="TO", end_role="FROM")
    
    elif start_is_downstream is False and end_is_downstream is None:
        # Only start is FROM
        return EndpointRoles(start_role="FROM", end_role="TO")
    
    elif start_is_downstream is None and end_is_downstream is True:
        # Only end is TO
        return EndpointRoles(start_role="FROM", end_role="TO")
    
    elif start_is_downstream is True and end_is_downstream is None:
        # Only start is TO (reverse)
        return EndpointRoles(start_role="TO", end_role="FROM")
    
    elif start_is_downstream is None and end_is_downstream is False:
        # Only end is FROM (reverse)
        return EndpointRoles(start_role="TO", end_role="FROM")
    
    else:
        # Ambiguous or no symbols: fall back to default
        # Assumption: flow goes from start to end
        return EndpointRoles(start_role="FROM", end_role="TO")


def euclidean_distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """Compute Euclidean distance between two points"""
    return np.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)


def normalize_vector(v: Tuple[float, float]) -> Tuple[float, float]:
    """Normalize a 2D vector"""
    magnitude = np.sqrt(v[0]**2 + v[1]**2)
    if magnitude < 1e-6:
        return (0.0, 0.0)
    return (v[0] / magnitude, v[1] / magnitude)
