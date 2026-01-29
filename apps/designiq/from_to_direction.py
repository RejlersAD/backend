"""
FROM-TO Direction Module for Pipelines
Uses line geometry and arrow markers from CAD/vector parsing to determine flow direction.

This module:
1. Attaches arrow markers to line endpoints based on proximity
2. Computes FROM vs TO direction for each line using arrow orientation
3. Maps FROM/TO endpoints to OCR line numbers

Does NOT modify existing line detection, OCR, regex, or table logic.
"""

import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Literal
import logging

logger = logging.getLogger(__name__)

# Type aliases
Point = Tuple[float, float]
EndpointRole = Literal["FROM", "TO"]


@dataclass
class Line:
    """Represents a detected pipeline line with ordered points"""
    id: str
    points: List[Point]  # Normalized coordinates, ordered along the pipe


@dataclass
class OcrItem:
    """Represents an OCR-detected text item (line number)"""
    id: str
    text: str  # Line number already validated by regex
    bbox: Tuple[float, float, float, float]  # (x_min, y_min, x_max, y_max)


@dataclass
class Arrow:
    """Represents an arrow marker from CAD/vector parsing"""
    id: str
    centroid: Point  # (ax, ay) - center point of arrow
    tip: Point  # (tx, ty) - direction of the arrowhead


@dataclass
class Endpoint:
    """Represents an endpoint of a pipeline line"""
    x: float
    y: float
    arrow_id: Optional[str] = None


@dataclass
class EndpointRoles:
    """Role assignment for line endpoints"""
    start_role: EndpointRole
    end_role: EndpointRole


# ============================================================================
# 1. ATTACH ARROWS TO LINE ENDPOINTS
# ============================================================================

def point_to_segment_distance(
    point: Point,
    seg_start: Point,
    seg_end: Point
) -> Tuple[float, bool]:
    """
    Calculate perpendicular distance from point to line segment.
    
    Args:
        point: The point to measure from
        seg_start: Start of line segment
        seg_end: End of line segment
    
    Returns:
        (distance, within_segment): Distance and whether projection lies within segment
    """
    px, py = point
    x1, y1 = seg_start
    x2, y2 = seg_end
    
    # Vector from seg_start to seg_end
    dx = x2 - x1
    dy = y2 - y1
    
    # Handle degenerate segment (zero length)
    seg_length_sq = dx * dx + dy * dy
    if seg_length_sq < 1e-10:
        dist = np.sqrt((px - x1)**2 + (py - y1)**2)
        return dist, True
    
    # Parameter t for projection onto infinite line
    # t = dot(point - seg_start, seg_end - seg_start) / |seg_end - seg_start|^2
    t = ((px - x1) * dx + (py - y1) * dy) / seg_length_sq
    
    # Check if projection is within segment bounds [0, 1]
    within_segment = 0 <= t <= 1
    
    # Clamp t to [0, 1] to get closest point on segment
    t_clamped = max(0, min(1, t))
    
    # Closest point on segment
    closest_x = x1 + t_clamped * dx
    closest_y = y1 + t_clamped * dy
    
    # Distance from point to closest point
    distance = np.sqrt((px - closest_x)**2 + (py - closest_y)**2)
    
    return distance, within_segment


def associate_arrows_to_endpoints(
    lines: List[Line],
    arrows: List[Arrow],
    radius: float,
) -> Dict[str, Dict[str, Endpoint]]:
    """
    Associate arrow markers with line endpoints based on proximity to first/last segments.
    
    For each line:
    - Start endpoint = points[0], uses segment points[0] -> points[1]
    - End endpoint = points[-1], uses segment points[-2] -> points[-1]
    
    For each endpoint, find arrows within radius distance (projected onto the segment).
    If multiple arrows qualify, choose the nearest one.
    
    Args:
        lines: List of Line objects with ordered points
        arrows: List of Arrow objects from CAD/vector parsing
        radius: Maximum perpendicular distance for association (normalized units)
    
    Returns:
        Dict mapping line_id to {"start": Endpoint(...), "end": Endpoint(...)}
    """
    endpoints_map = {}
    
    logger.info(f"  🎯 Associating {len(arrows)} arrows with {len(lines)} line endpoints")
    logger.info(f"     Association radius: {radius:.3f} (normalized)")
    
    for line in lines:
        if not line.points or len(line.points) < 2:
            logger.debug(f"     ⚠️ Line {line.id} has insufficient points ({len(line.points)})")
            continue
        
        # Define endpoints
        start_point = line.points[0]
        end_point = line.points[-1]
        
        # Define segments for projection
        # Start segment: points[0] -> points[1]
        if len(line.points) >= 2:
            start_segment = (line.points[0], line.points[1])
        else:
            start_segment = (start_point, start_point)  # Degenerate
        
        # End segment: points[-2] -> points[-1]
        if len(line.points) >= 2:
            end_segment = (line.points[-2], line.points[-1])
        else:
            end_segment = (end_point, end_point)  # Degenerate
        
        # Create endpoint objects
        start_endpoint = Endpoint(x=start_point[0], y=start_point[1])
        end_endpoint = Endpoint(x=end_point[0], y=end_point[1])
        
        # Find arrows for START endpoint
        best_start_arrow = None
        best_start_dist = float('inf')
        
        for arrow in arrows:
            dist, within = point_to_segment_distance(
                arrow.centroid,
                start_segment[0],
                start_segment[1]
            )
            
            # Arrow must be within radius AND projection must lie within segment
            if within and dist <= radius and dist < best_start_dist:
                best_start_dist = dist
                best_start_arrow = arrow
        
        if best_start_arrow:
            start_endpoint.arrow_id = best_start_arrow.id
            logger.debug(f"     ✅ Line {line.id} start -> arrow {best_start_arrow.id} (dist={best_start_dist:.4f})")
        
        # Find arrows for END endpoint
        best_end_arrow = None
        best_end_dist = float('inf')
        
        for arrow in arrows:
            dist, within = point_to_segment_distance(
                arrow.centroid,
                end_segment[0],
                end_segment[1]
            )
            
            if within and dist <= radius and dist < best_end_dist:
                best_end_dist = dist
                best_end_arrow = arrow
        
        if best_end_arrow:
            end_endpoint.arrow_id = best_end_arrow.id
            logger.debug(f"     ✅ Line {line.id} end -> arrow {best_end_arrow.id} (dist={best_end_dist:.4f})")
        
        endpoints_map[line.id] = {
            "start": start_endpoint,
            "end": end_endpoint
        }
    
    # Log statistics
    total_endpoints = len(endpoints_map) * 2
    associated_start = sum(1 for ep in endpoints_map.values() if ep['start'].arrow_id)
    associated_end = sum(1 for ep in endpoints_map.values() if ep['end'].arrow_id)
    
    logger.info(f"  ✅ Associated arrows: {associated_start} starts, {associated_end} ends (out of {total_endpoints} total endpoints)")
    
    return endpoints_map


# ============================================================================
# 2. COMPUTE DIRECTION (FROM vs TO) FOR EACH LINE
# ============================================================================

def normalize_vector(v: Tuple[float, float]) -> Tuple[float, float]:
    """Normalize a 2D vector to unit length"""
    x, y = v
    magnitude = np.sqrt(x**2 + y**2)
    if magnitude < 1e-10:
        return (0.0, 0.0)
    return (x / magnitude, y / magnitude)


def dot_product(v1: Tuple[float, float], v2: Tuple[float, float]) -> float:
    """Compute dot product of two 2D vectors"""
    return v1[0] * v2[0] + v1[1] * v2[1]


def infer_from_to_for_line(
    line: Line,
    endpoints: Dict[str, Endpoint],
    arrows_by_id: Dict[str, Arrow],
) -> EndpointRoles:
    """
    Infer FROM/TO roles for a line's endpoints based on arrow orientations.
    
    Logic:
    1. Compute local direction vectors at start and end
    2. For endpoints with arrows, compute dot product between arrow direction and line direction
    3. If arrow points TOWARDS the line segment (dot > 0), that endpoint is TO
    4. If arrow points AWAY from line segment (dot < 0), that endpoint is FROM
    5. Fallback: start=FROM, end=TO
    
    Args:
        line: Line object with ordered points
        endpoints: Dict with "start" and "end" Endpoint objects
        arrows_by_id: Dict mapping arrow ID to Arrow object
    
    Returns:
        EndpointRoles with start_role and end_role
    """
    if len(line.points) < 2:
        # Fallback for degenerate lines
        return EndpointRoles(start_role="FROM", end_role="TO")
    
    # Compute local direction vectors
    # v_start: direction near start (from points[0] to points[1])
    p0, p1 = line.points[0], line.points[1]
    v_start = normalize_vector((p1[0] - p0[0], p1[1] - p0[1]))
    
    # v_end: direction near end (from points[-2] to points[-1])
    pn_1, pn = line.points[-2], line.points[-1]
    v_end = normalize_vector((pn[0] - pn_1[0], pn[1] - pn_1[1]))
    
    # Get associated arrows
    start_endpoint = endpoints["start"]
    end_endpoint = endpoints["end"]
    
    start_arrow = arrows_by_id.get(start_endpoint.arrow_id) if start_endpoint.arrow_id else None
    end_arrow = arrows_by_id.get(end_endpoint.arrow_id) if end_endpoint.arrow_id else None
    
    # Scoring for each endpoint
    start_score = None  # Positive = TO, Negative = FROM
    end_score = None
    
    # Analyze START endpoint
    if start_arrow:
        # Arrow direction vector (from centroid to tip)
        v_arrow = normalize_vector((
            start_arrow.tip[0] - start_arrow.centroid[0],
            start_arrow.tip[1] - start_arrow.centroid[1]
        ))
        
        # Dot product: positive means arrow points along line direction
        start_score = dot_product(v_arrow, v_start)
        logger.debug(f"       START arrow dot product: {start_score:.3f} (arrow points {'along' if start_score > 0 else 'against'} line)")
    
    # Analyze END endpoint
    if end_arrow:
        v_arrow = normalize_vector((
            end_arrow.tip[0] - end_arrow.centroid[0],
            end_arrow.tip[1] - end_arrow.centroid[1]
        ))
        
        end_score = dot_product(v_arrow, v_end)
        logger.debug(f"       END arrow dot product: {end_score:.3f} (arrow points {'along' if end_score > 0 else 'against'} line)")
    
    # Decision logic with threshold to avoid noise
    THRESHOLD = 0.3
    
    # Case 1: End arrow points along line direction (TO), start has no arrow or points back
    if end_score is not None and end_score > THRESHOLD:
        # End is TO (arrow points towards it along flow)
        return EndpointRoles(start_role="FROM", end_role="TO")
    
    # Case 2: Start arrow points back along line (TO at start, FROM at end - reverse flow)
    if start_score is not None and start_score < -THRESHOLD:
        # Start is TO (arrow points back, indicating reverse flow)
        return EndpointRoles(start_role="TO", end_role="FROM")
    
    # Case 3: Start arrow points forward, end arrow points back - use strongest signal
    if start_score is not None and end_score is not None:
        if abs(end_score) > abs(start_score):
            # End arrow is stronger
            if end_score > THRESHOLD:
                return EndpointRoles(start_role="FROM", end_role="TO")
            elif end_score < -THRESHOLD:
                return EndpointRoles(start_role="TO", end_role="FROM")
        else:
            # Start arrow is stronger
            if start_score > THRESHOLD:
                return EndpointRoles(start_role="FROM", end_role="TO")
            elif start_score < -THRESHOLD:
                return EndpointRoles(start_role="TO", end_role="FROM")
    
    # Case 4: Only start arrow with clear direction
    if start_score is not None and end_score is None:
        if start_score > THRESHOLD:
            return EndpointRoles(start_role="FROM", end_role="TO")
        elif start_score < -THRESHOLD:
            return EndpointRoles(start_role="TO", end_role="FROM")
    
    # Case 5: Only end arrow with clear direction
    if end_score is not None and start_score is None:
        if end_score > THRESHOLD:
            return EndpointRoles(start_role="FROM", end_role="TO")
        elif end_score < -THRESHOLD:
            return EndpointRoles(start_role="TO", end_role="FROM")
    
    # Default fallback: deterministic start=FROM, end=TO
    logger.debug(f"       Using fallback: start=FROM, end=TO")
    return EndpointRoles(start_role="FROM", end_role="TO")


# ============================================================================
# 3. MAP FROM/TO ENDPOINTS TO OCR LINE NUMBERS
# ============================================================================

def find_nearest_ocr(
    x: float,
    y: float,
    ocr_items: List[OcrItem],
    max_distance: float,
) -> Optional[OcrItem]:
    """
    Find the nearest OCR item to a given point.
    
    Uses the center of each OCR item's bounding box for distance calculation.
    Returns None if no item is within max_distance.
    
    Args:
        x: X coordinate of point (normalized)
        y: Y coordinate of point (normalized)
        ocr_items: List of OcrItem objects
        max_distance: Maximum Euclidean distance for match (normalized units)
    
    Returns:
        Nearest OcrItem within max_distance, or None
    """
    best_item = None
    best_distance = float('inf')
    
    for item in ocr_items:
        # Calculate center of bounding box
        x_min, y_min, x_max, y_max = item.bbox
        cx = (x_min + x_max) / 2.0
        cy = (y_min + y_max) / 2.0
        
        # Euclidean distance
        dist = np.sqrt((x - cx)**2 + (y - cy)**2)
        
        if dist < best_distance and dist <= max_distance:
            best_distance = dist
            best_item = item
    
    if best_item:
        logger.debug(f"       Found OCR '{best_item.text}' at distance {best_distance:.4f}")
    
    return best_item


def find_connected_lines(
    line_id: str,
    endpoint: Endpoint,
    all_lines: List[Line],
    endpoints_map: Dict[str, Dict[str, Endpoint]],
    connection_threshold: float = 0.02
) -> List[str]:
    """
    Find all lines that connect to this endpoint (adjacent lines at connection points).
    
    Args:
        line_id: ID of the current line
        endpoint: The endpoint to check for connections
        all_lines: List of all detected lines
        endpoints_map: Map of line IDs to their endpoints
        connection_threshold: Maximum distance to consider as connected (default 0.02 = 2% of normalized size)
    
    Returns:
        List of line IDs that are connected to this endpoint
    """
    connected = []
    
    for other_line in all_lines:
        # Skip the same line
        if other_line.id == line_id:
            continue
        
        # Check if other line has endpoints
        if other_line.id not in endpoints_map:
            continue
        
        other_endpoints = endpoints_map[other_line.id]
        
        # Check distance to both endpoints of the other line
        for endpoint_key, other_endpoint in other_endpoints.items():
            distance = np.sqrt(
                (endpoint.x - other_endpoint.x)**2 +
                (endpoint.y - other_endpoint.y)**2
            )
            
            if distance <= connection_threshold:
                connected.append(other_line.id)
                logger.debug(f"       Found connected line {other_line.id} at distance {distance:.4f}")
                break  # Only add once per line
    
    return connected


def find_adjacent_line_numbers(
    connection_point: Endpoint,
    connected_line_ids: List[str],
    endpoints_map: Dict[str, Dict[str, Endpoint]],
    ocr_items: List[OcrItem],
    max_distance: float,
    connection_threshold: float = 0.02
) -> List[str]:
    """
    Find all line numbers near the OTHER endpoints of connected lines.
    
    For each connected line, find which endpoint is at the connection point,
    then check the OTHER endpoint for nearby OCR line numbers.
    
    Args:
        connection_point: The connection point where lines meet
        connected_line_ids: IDs of lines connected at this point
        endpoints_map: Map of line IDs to their endpoints
        ocr_items: List of OCR-detected line numbers
        max_distance: Maximum distance for OCR association
        connection_threshold: Distance threshold for determining which endpoint is at connection
    
    Returns:
        List of unique line numbers found near the OTHER ends of connected lines
    """
    line_numbers = []
    
    for line_id in connected_line_ids:
        if line_id not in endpoints_map:
            continue
        
        endpoints = endpoints_map[line_id]
        
        # Find which endpoint is at the connection point, check the OTHER one
        start_endpoint = endpoints.get("start")
        end_endpoint = endpoints.get("end")
        
        if not start_endpoint or not end_endpoint:
            continue
        
        # Calculate distances to connection point
        start_dist = np.sqrt(
            (connection_point.x - start_endpoint.x)**2 +
            (connection_point.y - start_endpoint.y)**2
        )
        
        end_dist = np.sqrt(
            (connection_point.x - end_endpoint.x)**2 +
            (connection_point.y - end_endpoint.y)**2
        )
        
        # Check the endpoint that's FARTHER from the connection point
        if start_dist < connection_threshold:
            # Start is at connection, check end
            check_endpoint = end_endpoint
            other_end = "end"
        elif end_dist < connection_threshold:
            # End is at connection, check start
            check_endpoint = start_endpoint
            other_end = "start"
        else:
            # Neither endpoint is at connection (shouldn't happen)
            continue
        
        # Find OCR near the OTHER endpoint
        ocr_item = find_nearest_ocr(
            check_endpoint.x,
            check_endpoint.y,
            ocr_items,
            max_distance
        )
        
        if ocr_item and ocr_item.text not in line_numbers:
            line_numbers.append(ocr_item.text)
            logger.debug(f"         Found adjacent line number '{ocr_item.text}' at {other_end} of connected line {line_id}")
    
    return line_numbers


def build_from_to_map(
    lines: List[Line],
    endpoints_map: Dict[str, Dict[str, Endpoint]],
    roles_map: Dict[str, EndpointRoles],
    ocr_items: List[OcrItem],
    max_distance: float,
) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Build FROM-TO mapping for all lines by associating endpoints with OCR line numbers.
    
    Now enhanced to find ADJACENT connected line numbers at connection points.
    
    For each line:
    1. Get start and end endpoints from endpoints_map
    2. Get roles (FROM/TO) from roles_map
    3. Find nearest OCR item to each endpoint (DIRECT match)
    4. Find CONNECTED lines at each endpoint
    5. Find OCR items near connected lines' endpoints (ADJACENT matches)
    6. Combine direct + adjacent matches into comma-separated lists
    
    Args:
        lines: List of Line objects
        endpoints_map: Dict mapping line_id to {"start": Endpoint, "end": Endpoint}
        roles_map: Dict mapping line_id to EndpointRoles
        ocr_items: List of OcrItem objects (detected line numbers)
        max_distance: Maximum distance for OCR association (normalized units)
    
    Returns:
        Dict mapping line_id to {"from_line": Optional[str], "to_line": Optional[str]}
        (Now includes comma-separated lists of adjacent line numbers)
    """
    from_to_map = {}
    
    logger.info(f"  🔗 Building FROM-TO map for {len(lines)} lines using {len(ocr_items)} OCR items")
    logger.info(f"     Max OCR association distance: {max_distance:.3f} (normalized)")
    logger.info(f"     🌐 Now detecting ADJACENT connected line numbers at junctions")
    
    for line in lines:
        if line.id not in endpoints_map or line.id not in roles_map:
            logger.debug(f"     ⚠️ Line {line.id} missing endpoints or roles")
            from_to_map[line.id] = {"from_line": None, "to_line": None}
            continue
        
        endpoints = endpoints_map[line.id]
        roles = roles_map[line.id]
        
        start_endpoint = endpoints["start"]
        end_endpoint = endpoints["end"]
        
        # Find DIRECT OCR for each endpoint
        start_ocr = find_nearest_ocr(
            start_endpoint.x,
            start_endpoint.y,
            ocr_items,
            max_distance
        )
        
        end_ocr = find_nearest_ocr(
            end_endpoint.x,
            end_endpoint.y,
            ocr_items,
            max_distance
        )
        
        # Find CONNECTED lines at each endpoint
        connection_threshold = 0.02  # 2% threshold for connections
        
        start_connected = find_connected_lines(
            line.id,
            start_endpoint,
            lines,
            endpoints_map,
            connection_threshold=connection_threshold
        )
        
        end_connected = find_connected_lines(
            line.id,
            end_endpoint,
            lines,
            endpoints_map,
            connection_threshold=connection_threshold
        )
        
        # Find ADJACENT line numbers via connected lines (check OTHER end of each connected line)
        start_adjacent = find_adjacent_line_numbers(
            start_endpoint,
            start_connected,
            endpoints_map,
            ocr_items,
            max_distance,
            connection_threshold=connection_threshold
        ) if start_connected else []
        
        end_adjacent = find_adjacent_line_numbers(
            end_endpoint,
            end_connected,
            endpoints_map,
            ocr_items,
            max_distance,
            connection_threshold=connection_threshold
        ) if end_connected else []
        
        # Build FROM/TO lists (direct + adjacent)
        from_numbers = []
        to_numbers = []
        
        # Add direct matches based on roles
        if roles.start_role == "FROM" and start_ocr:
            from_numbers.append(start_ocr.text)
        elif roles.start_role == "TO" and start_ocr:
            to_numbers.append(start_ocr.text)
        
        if roles.end_role == "FROM" and end_ocr:
            from_numbers.append(end_ocr.text)
        elif roles.end_role == "TO" and end_ocr:
            to_numbers.append(end_ocr.text)
        
        # Add adjacent matches based on roles
        if roles.start_role == "FROM":
            from_numbers.extend([n for n in start_adjacent if n not in from_numbers])
        elif roles.start_role == "TO":
            to_numbers.extend([n for n in start_adjacent if n not in to_numbers])
        
        if roles.end_role == "FROM":
            from_numbers.extend([n for n in end_adjacent if n not in from_numbers])
        elif roles.end_role == "TO":
            to_numbers.extend([n for n in end_adjacent if n not in to_numbers])
        
        # Create comma-separated strings
        from_line = ", ".join(from_numbers) if from_numbers else None
        to_line = ", ".join(to_numbers) if to_numbers else None
        
        from_to_map[line.id] = {
            "from_line": from_line,
            "to_line": to_line
        }
        
        if start_adjacent or end_adjacent:
            logger.debug(f"     🌐 Line {line.id}: FROM={from_line}, TO={to_line} (includes {len(start_adjacent)+len(end_adjacent)} adjacent)")
        else:
            logger.debug(f"     ✅ Line {line.id}: FROM={from_line}, TO={to_line}")
    
    # Log statistics
    total_lines = len(from_to_map)
    lines_with_from = sum(1 for entry in from_to_map.values() if entry["from_line"])
    lines_with_to = sum(1 for entry in from_to_map.values() if entry["to_line"])
    lines_with_both = sum(1 for entry in from_to_map.values() if entry["from_line"] and entry["to_line"])
    
    logger.info(f"  ✅ FROM-TO mapping complete:")
    logger.info(f"     Lines with FROM: {lines_with_from}/{total_lines} ({100*lines_with_from/total_lines:.1f}%)")
    logger.info(f"     Lines with TO: {lines_with_to}/{total_lines} ({100*lines_with_to/total_lines:.1f}%)")
    logger.info(f"     Lines with BOTH: {lines_with_both}/{total_lines} ({100*lines_with_both/total_lines:.1f}%)")
    
    return from_to_map


# ============================================================================
# 4. MAIN PIPELINE FUNCTION
# ============================================================================

def compute_from_to_for_lines(
    lines: List[Line],
    arrows: List[Arrow],
    ocr_items: List[OcrItem],
    arrow_association_radius: float = 0.05,
    ocr_association_max_distance: float = 0.08,
) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Complete FROM-TO detection pipeline.
    
    Pipeline:
    1. Associate arrows with line endpoints (using perpendicular distance to first/last segments)
    2. Infer FROM/TO roles for each line based on arrow orientations
    3. Map FROM/TO endpoints to OCR line numbers
    
    Args:
        lines: List of Line objects with ordered points
        arrows: List of Arrow objects from CAD/vector parsing
        ocr_items: List of OcrItem objects (detected line numbers)
        arrow_association_radius: Max distance for arrow-endpoint association (default 0.05 = 5% of normalized size)
        ocr_association_max_distance: Max distance for OCR-endpoint association (default 0.08 = 8%)
    
    Returns:
        Dict mapping line_id to {"from_line": Optional[str], "to_line": Optional[str]}
    """
    logger.info("🚀 Starting FROM-TO direction detection pipeline")
    logger.info(f"   Input: {len(lines)} lines, {len(arrows)} arrows, {len(ocr_items)} OCR items")
    
    # Step 1: Associate arrows with endpoints
    endpoints_map = associate_arrows_to_endpoints(lines, arrows, arrow_association_radius)
    
    # Step 2: Infer FROM/TO roles
    logger.info(f"  🧭 Inferring FROM/TO roles for {len(endpoints_map)} lines")
    arrows_by_id = {arrow.id: arrow for arrow in arrows}
    roles_map = {}
    
    for line in lines:
        if line.id in endpoints_map:
            roles = infer_from_to_for_line(line, endpoints_map[line.id], arrows_by_id)
            roles_map[line.id] = roles
            logger.debug(f"     Line {line.id}: start={roles.start_role}, end={roles.end_role}")
    
    # Step 3: Build FROM-TO map
    from_to_map = build_from_to_map(
        lines,
        endpoints_map,
        roles_map,
        ocr_items,
        ocr_association_max_distance
    )
    
    logger.info("✅ FROM-TO direction detection pipeline complete")
    
    return from_to_map
