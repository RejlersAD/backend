"""
FROM-TO Direction Module for P&ID Pipelines

Post-processing module inspired by "Automated counting of piping and instrumentation diagram
using artificial intelligence" (J. Integr. Sci. Technol., 2025, 136, 1147).

This module fills FROM and TO columns in the pipeline table by:
1. Detecting arrow/triangle symbols on the P&ID image
2. Correlating arrows with line numbers using spatial geometry
3. Determining flow direction based on arrow orientation

Designed to be modular and easily replaceable with Claude/vision API later.
Pure post-processing - does NOT change OCR, regex, or table generation logic.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional, Set
import math
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# 1. DATA STRUCTURES (paper-aligned, reuse-friendly)
# ============================================================================

@dataclass
class LineRecord:
    """
    Represents a single pipeline line extracted from P&ID.
    Matches the columns in your Excel table.
    """
    original_detection: str   # Full line number: "36-41-SWS-00538-A2AU16-V"
    fluid_code: str
    size: str
    sequence_no: str
    pipr_class: str
    insulation: str
    from_line: Optional[str] = None  # Will be filled by this module
    to_line: Optional[str] = None    # Will be filled by this module
    
    # Optional metadata for spatial correlation
    bbox: Optional[Tuple[float, float, float, float]] = None  # OCR bbox (x1, y1, x2, y2)


@dataclass
class ArrowInfo:
    """
    Arrow/triangle symbol detected on a pipeline.
    Can be provided by image processing or Claude/vision API.
    """
    line_number: str                    # Matches LineRecord.original_detection
    tip: Tuple[float, float]            # Arrowhead tip position (x, y)
    base: Tuple[float, float]           # Arrow base position (x, y)
    confidence: float = 1.0             # Detection confidence [0-1]
    
    @property
    def direction(self) -> Tuple[float, float]:
        """Unit vector pointing from base to tip"""
        dx = self.tip[0] - self.base[0]
        dy = self.tip[1] - self.base[1]
        mag = math.sqrt(dx * dx + dy * dy)
        if mag < 1e-6:
            return (0.0, 0.0)
        return (dx / mag, dy / mag)


@dataclass
class PIDImage:
    """Represents the rendered P&ID image with metadata"""
    width: int
    height: int
    dpi: int = 300
    page_number: int = 1


@dataclass
class DirectionConfig:
    """Configuration for direction inference (soft-coded parameters)"""
    max_angle_deg: float = 45.0         # Max angle deviation for "in direction of"
    max_search_distance: float = 500.0  # Max pixel distance to search for target
    min_confidence: float = 0.3         # Minimum arrow confidence to use
    prefer_sequential: bool = True      # Prefer sequential line numbers when ambiguous
    fallback_to_nearest: bool = True    # If no arrow, use nearest neighbor
    
    # Spatial weights for intelligent matching
    angle_weight: float = 0.6
    distance_weight: float = 0.3
    sequence_weight: float = 0.1


# ============================================================================
# 2. GEOMETRY HELPERS (reusable primitives)
# ============================================================================

def bbox_center(bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Compute center point of a bounding box"""
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """Euclidean distance between two points"""
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def normalize_vector(v: Tuple[float, float]) -> Tuple[float, float]:
    """Normalize a vector to unit length"""
    mag = math.sqrt(v[0] ** 2 + v[1] ** 2)
    if mag < 1e-8:
        return (0.0, 0.0)
    return (v[0] / mag, v[1] / mag)


def dot_product(v1: Tuple[float, float], v2: Tuple[float, float]) -> float:
    """Dot product of two vectors"""
    return v1[0] * v2[0] + v1[1] * v2[1]


def angle_between_vectors_deg(v1: Tuple[float, float], v2: Tuple[float, float]) -> float:
    """Angle between two vectors in degrees [0-180]"""
    v1_norm = normalize_vector(v1)
    v2_norm = normalize_vector(v2)
    
    dot = dot_product(v1_norm, v2_norm)
    # Clamp to avoid numerical errors
    dot = max(-1.0, min(1.0, dot))
    
    angle_rad = math.acos(dot)
    return math.degrees(angle_rad)


def point_in_direction_of(
    point: Tuple[float, float],
    direction: Tuple[float, float],
    reference: Tuple[float, float]
) -> bool:
    """Check if 'point' is in the direction of 'direction' vector from 'reference'"""
    to_point = (point[0] - reference[0], point[1] - reference[1])
    to_point_norm = normalize_vector(to_point)
    
    if to_point_norm == (0.0, 0.0):
        return False
    
    dot = dot_product(direction, to_point_norm)
    return dot > 0.5  # More than 60 degrees = not in direction


def extract_sequence_number(line_number: str) -> Optional[int]:
    """
    Extract numeric sequence from line number for intelligent matching.
    Example: "36-41-SWS-00538-A2AU16-V" → 538
    """
    import re
    # Look for sequence number pattern (typically 4-6 digits)
    match = re.search(r'-(\d{4,6})-', line_number)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


# ============================================================================
# 3. SPATIAL MATCHING (arrow → line number correlation)
# ============================================================================

def find_nearest_line_number_in_direction(
    arrow_tip: Tuple[float, float],
    arrow_dir: Tuple[float, float],
    line_records: List[LineRecord],
    ocr_positions: Dict[str, Tuple[float, float]],
    config: DirectionConfig
) -> Optional[str]:
    """
    Find the nearest line number text that lies in the direction of the arrow.
    
    This is the core spatial correlation function, mimicking the paper's approach
    of combining symbol detection with text recognition to build relationships.
    
    Args:
        arrow_tip: Arrow tip position (x, y)
        arrow_dir: Arrow direction unit vector
        line_records: All pipeline lines with their metadata
        ocr_positions: Map of line_number → (cx, cy) text center
        config: Direction inference configuration
    
    Returns:
        original_detection of the best matching line, or None
    """
    candidates = []
    
    for record in line_records:
        line_num = record.original_detection
        if line_num not in ocr_positions:
            continue
        
        text_pos = ocr_positions[line_num]
        
        # Vector from arrow tip to text center
        to_text = (text_pos[0] - arrow_tip[0], text_pos[1] - arrow_tip[1])
        dist = math.sqrt(to_text[0]**2 + to_text[1]**2)
        
        # Skip if too far
        if dist > config.max_search_distance:
            continue
        
        # Check if text is in arrow direction
        to_text_norm = normalize_vector(to_text)
        angle = angle_between_vectors_deg(arrow_dir, to_text_norm)
        
        # Skip if angle too large
        if angle > config.max_angle_deg:
            continue
        
        # Compute weighted score (lower is better)
        angle_score = angle / config.max_angle_deg
        distance_score = dist / config.max_search_distance
        
        # Sequential bonus: prefer consecutive sequence numbers
        sequence_score = 0.5  # Default
        if config.prefer_sequential:
            seq_num = extract_sequence_number(line_num)
            if seq_num is not None:
                # Bonus for numbers that look like they could be connected
                # (this is heuristic, can be improved with domain knowledge)
                sequence_score = 0.3
        
        total_score = (
            angle_score * config.angle_weight +
            distance_score * config.distance_weight +
            sequence_score * config.sequence_weight
        )
        
        candidates.append((line_num, total_score, dist, angle))
    
    if not candidates:
        return None
    
    # Return line with lowest score (best match)
    candidates.sort(key=lambda x: x[1])
    best_line, score, dist, angle = candidates[0]
    
    logger.debug(
        f"Arrow matched to {best_line}: "
        f"score={score:.3f}, dist={dist:.1f}px, angle={angle:.1f}°"
    )
    
    return best_line


def find_nearest_neighbor(
    line_number: str,
    line_records: List[LineRecord],
    ocr_positions: Dict[str, Tuple[float, float]],
    max_distance: float
) -> Optional[str]:
    """
    Fallback: find the spatially nearest line number (no direction constraint).
    Used when no arrow is available.
    """
    if line_number not in ocr_positions:
        return None
    
    my_pos = ocr_positions[line_number]
    min_dist = float('inf')
    nearest = None
    
    for record in line_records:
        other_line = record.original_detection
        if other_line == line_number:
            continue
        
        if other_line not in ocr_positions:
            continue
        
        other_pos = ocr_positions[other_line]
        dist = distance(my_pos, other_pos)
        
        if dist < min_dist and dist <= max_distance:
            min_dist = dist
            nearest = other_line
    
    return nearest


# ============================================================================
# 4. DIRECTION INFERENCE (core logic)
# ============================================================================

def infer_direction_from_arrow(
    arrow: ArrowInfo,
    line_records: List[LineRecord],
    ocr_positions: Dict[str, Tuple[float, float]],
    config: DirectionConfig
) -> Tuple[Optional[str], Optional[str]]:
    """
    Infer FROM and TO for a single arrow.
    
    Logic:
    - FROM = arrow.line_number (the line the arrow is on)
    - TO = nearest line number in the direction of the arrow tip
    
    Args:
        arrow: Arrow information
        line_records: All pipeline lines
        ocr_positions: OCR text positions
        config: Configuration
    
    Returns:
        (from_line, to_line) tuple
    """
    if arrow.confidence < config.min_confidence:
        logger.debug(f"Arrow on {arrow.line_number} below confidence threshold")
        return (None, None)
    
    # FROM is the line the arrow is on
    from_line = arrow.line_number
    
    # TO is the target the arrow points to
    to_line = find_nearest_line_number_in_direction(
        arrow_tip=arrow.tip,
        arrow_dir=arrow.direction,
        line_records=line_records,
        ocr_positions=ocr_positions,
        config=config
    )
    
    if to_line is None:
        logger.debug(f"Arrow on {from_line} points to no clear target")
        to_line = "UNKNOWN"
    
    return (from_line, to_line)


def infer_from_to_for_pid(
    pdf_bytes: bytes,
    line_records: List[LineRecord],
    arrows: Optional[List[ArrowInfo]] = None,
    config: Optional[DirectionConfig] = None
) -> List[LineRecord]:
    """
    Main entry point: Infer FROM-TO for all lines in a P&ID.
    
    This function is designed to be called AFTER OCR/regex extraction
    and BEFORE writing the final Excel/CSV table.
    
    Args:
        pdf_bytes: Original P&ID PDF (single sheet)
        line_records: List of lines extracted by OCR/regex
        arrows: Optional list of detected arrows (if None, will attempt detection)
        config: Direction inference configuration
    
    Returns:
        Same line_records list with from_line and to_line populated
    """
    if config is None:
        config = DirectionConfig()
    
    logger.info(f"🎯 Starting FROM-TO inference for {len(line_records)} lines")
    
    # Step 1: Build OCR positions map
    ocr_positions = {}
    for record in line_records:
        if record.bbox:
            ocr_positions[record.original_detection] = bbox_center(record.bbox)
    
    logger.info(f"   📍 Built OCR position map for {len(ocr_positions)} lines")
    
    # Step 2: If no arrows provided, attempt detection or use fallback
    if arrows is None or len(arrows) == 0:
        logger.warning("⚠️ No arrows provided - attempting image-based detection")
        arrows = detect_arrows_from_pdf(pdf_bytes, line_records, ocr_positions)
        
        if not arrows:
            logger.warning("   No arrows detected - using fallback (nearest neighbor)")
            return apply_fallback_strategy(line_records, ocr_positions, config)
    
    logger.info(f"   🎯 Processing {len(arrows)} arrows")
    
    # Step 3: Build arrow map (line_number → list of arrows)
    arrow_map: Dict[str, List[ArrowInfo]] = {}
    for arrow in arrows:
        if arrow.line_number not in arrow_map:
            arrow_map[arrow.line_number] = []
        arrow_map[arrow.line_number].append(arrow)
    
    # Step 4: Process each line
    processed_count = 0
    
    for record in line_records:
        line_num = record.original_detection
        
        if line_num in arrow_map:
            # Has arrow(s) - use direction inference
            line_arrows = arrow_map[line_num]
            
            # If multiple arrows, use the one with highest confidence
            best_arrow = max(line_arrows, key=lambda a: a.confidence)
            
            from_line, to_line = infer_direction_from_arrow(
                best_arrow, line_records, ocr_positions, config
            )
            
            record.from_line = from_line
            record.to_line = to_line
            processed_count += 1
            
        elif config.fallback_to_nearest:
            # No arrow - use nearest neighbor fallback
            nearest = find_nearest_neighbor(
                line_num, line_records, ocr_positions, config.max_search_distance
            )
            
            if nearest:
                record.from_line = line_num
                record.to_line = nearest
                logger.debug(f"Fallback: {line_num} → {nearest}")
    
    logger.info(f"✅ Processed {processed_count} lines with arrows")
    logger.info(f"   📊 FROM-TO inference complete")
    
    return line_records


def apply_fallback_strategy(
    line_records: List[LineRecord],
    ocr_positions: Dict[str, Tuple[float, float]],
    config: DirectionConfig
) -> List[LineRecord]:
    """
    Fallback strategy when no arrows detected.
    Uses spatial proximity and sequential numbering.
    """
    logger.info("   Applying fallback strategy (spatial + sequential)")
    
    for record in line_records:
        line_num = record.original_detection
        
        # Try to find nearest neighbor
        nearest = find_nearest_neighbor(
            line_num, line_records, ocr_positions, config.max_search_distance
        )
        
        if nearest:
            # Simple heuristic: FROM = current, TO = nearest
            record.from_line = line_num
            record.to_line = nearest
        else:
            # No neighbors found
            record.from_line = line_num
            record.to_line = "UNKNOWN"
    
    return line_records


# ============================================================================
# 5. ARROW DETECTION (placeholder for future enhancement)
# ============================================================================

def detect_arrows_from_pdf(
    pdf_bytes: bytes,
    line_records: List[LineRecord],
    ocr_positions: Dict[str, Tuple[float, float]]
) -> List[ArrowInfo]:
    """
    Detect arrows from P&ID image.
    
    THIS IS A PLACEHOLDER for future implementation.
    Can be replaced with:
    1. OpenCV-based triangle detection
    2. Claude/vision API call (mimicking the paper's approach)
    3. YOLOv8 arrow detector
    
    For now, returns empty list to trigger fallback strategy.
    
    Future Claude/vision integration example:
    ```python
    import anthropic
    
    # Render PDF to image
    image = render_pdf_page(pdf_bytes, dpi=300)
    image_b64 = encode_image_base64(image)
    
    # Prepare prompt
    prompt = f'''
    Analyze this P&ID drawing and detect all arrow/triangle symbols on pipeline lines.
    
    For each arrow, return:
    - line_number: Which pipeline line it's on (choose from: {[r.original_detection for r in line_records]})
    - tip: (x, y) coordinates of arrow tip
    - base: (x, y) coordinates of arrow base
    - confidence: 0-1 confidence score
    
    Return as JSON array of arrows.
    '''
    
    response = client.messages.create(
        model="claude-3-sonnet-20240229",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "data": image_b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    )
    
    arrows_json = parse_json_response(response.content[0].text)
    return [ArrowInfo(**a) for a in arrows_json]
    ```
    """
    logger.info("   🔍 Arrow detection not yet implemented - using fallback")
    return []


def render_pdf_page(pdf_bytes: bytes, dpi: int = 300) -> 'PILImage':
    """
    Render PDF first page to PIL Image.
    
    Args:
        pdf_bytes: PDF file bytes
        dpi: Resolution for rendering
    
    Returns:
        PIL Image object
    """
    try:
        from pdf2image import convert_from_bytes
        
        images = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=1, last_page=1)
        if images:
            return images[0]
        else:
            raise ValueError("PDF rendering returned no images")
            
    except ImportError:
        logger.error("pdf2image not installed. Install with: pip install pdf2image")
        raise
    except Exception as e:
        logger.error(f"PDF rendering failed: {str(e)}")
        raise


# ============================================================================
# 6. INTEGRATION HELPERS (for existing pipeline)
# ============================================================================

def convert_table_data_to_line_records(
    table_data: List[Dict],
    ocr_bboxes: Optional[Dict[str, Tuple[float, float, float, float]]] = None
) -> List[LineRecord]:
    """
    Convert existing table_data format to LineRecord objects.
    
    Args:
        table_data: List of dicts from format_as_table_data()
        ocr_bboxes: Optional map of line_number → bbox
    
    Returns:
        List of LineRecord objects
    """
    records = []
    
    for item in table_data:
        line_num = item.get('line_number', '')
        if not line_num:
            continue
        
        bbox = None
        if ocr_bboxes and line_num in ocr_bboxes:
            bbox = ocr_bboxes[line_num]
        
        record = LineRecord(
            original_detection=line_num,
            fluid_code=item.get('fluid_code', ''),
            size=item.get('size', ''),
            sequence_no=item.get('sequence_no', ''),
            pipr_class=item.get('pipr_class', ''),
            insulation=item.get('insulation', ''),
            from_line=item.get('from_line'),
            to_line=item.get('to_line'),
            bbox=bbox
        )
        records.append(record)
    
    return records


def apply_line_records_to_table_data(
    line_records: List[LineRecord],
    table_data: List[Dict]
) -> List[Dict]:
    """
    Apply FROM-TO results from LineRecords back to table_data.
    
    Args:
        line_records: LineRecords with filled from_line/to_line
        table_data: Original table_data to update
    
    Returns:
        Updated table_data with FROM-TO populated
    """
    # Build lookup map
    record_map = {r.original_detection: r for r in line_records}
    
    # Update table_data in place
    for item in table_data:
        line_num = item.get('line_number', '')
        if line_num in record_map:
            record = record_map[line_num]
            item['from_line'] = record.from_line
            item['to_line'] = record.to_line
            item['flow_detection_method'] = 'geometric_direction'
            item['flow_confidence'] = 'medium'
    
    return table_data


# ============================================================================
# 7. EXAMPLE USAGE
# ============================================================================

def example_integration_with_existing_pipeline():
    """
    Example showing how to integrate this module with existing upload_pid view.
    
    This demonstrates the paper's approach: separate stages that can be
    independently developed and tested.
    """
    
    # Existing code (UNCHANGED):
    # -----------------------------
    # extractor = PIDLineExtractorV2()
    # line_items = extractor.extract_from_pdf(tmp_path)
    # table_data = extractor.format_as_table_data(line_items)
    
    # NEW: Add FROM-TO detection
    # -----------------------------
    try:
        from .from_to_direction import (
            convert_table_data_to_line_records,
            infer_from_to_for_pid,
            apply_line_records_to_table_data,
            DirectionConfig
        )
        
        # Read PDF bytes
        with open('path/to/pid.pdf', 'rb') as f:
            pdf_bytes = f.read()
        
        # Convert existing table_data to LineRecords
        # (assumes table_data already has line_number, fluid_code, etc.)
        line_records = convert_table_data_to_line_records(
            table_data=[],  # Your existing table_data
            ocr_bboxes={}   # Optional: bbox map from OCR engine
        )
        
        # Configure direction inference
        config = DirectionConfig(
            max_angle_deg=45.0,
            max_search_distance=500.0,
            prefer_sequential=True,
            fallback_to_nearest=True
        )
        
        # Infer FROM-TO
        line_records = infer_from_to_for_pid(
            pdf_bytes=pdf_bytes,
            line_records=line_records,
            arrows=None,  # Will trigger detection or fallback
            config=config
        )
        
        # Apply results back to table_data
        table_data = apply_line_records_to_table_data(line_records, [])
        
        logger.info(f"✅ FROM-TO populated for {len(table_data)} lines")
        
    except Exception as e:
        logger.warning(f"⚠️ FROM-TO detection failed: {str(e)}", exc_info=True)
        # Continue without FROM-TO data - existing columns unaffected
    
    # Rest of existing code (UNCHANGED):
    # -----------------------------
    # Save to database, return response, etc.


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)
    
    test_records = [
        LineRecord("36-41-SWS-00538-A2AU16-V", "SWS", "36", "00538", "A2AU16", "V",
                  bbox=(100, 100, 200, 120)),
        LineRecord("12-41-SWS-00539-A2AU16-V", "SWS", "12", "00539", "A2AU16", "V",
                  bbox=(300, 100, 400, 120)),
        LineRecord("8-41-SWS-00540-A2AU16-V", "SWS", "8", "00540", "A2AU16", "V",
                  bbox=(500, 100, 600, 120)),
    ]
    
    # Simulate arrow detection
    test_arrows = [
        ArrowInfo("36-41-SWS-00538-A2AU16-V", tip=(250, 110), base=(180, 110), confidence=0.9),
        ArrowInfo("12-41-SWS-00539-A2AU16-V", tip=(450, 110), base=(380, 110), confidence=0.85),
    ]
    
    config = DirectionConfig()
    
    # Test without PDF (will use fallback)
    result = infer_from_to_for_pid(
        pdf_bytes=b"",  # Empty for testing
        line_records=test_records,
        arrows=test_arrows,
        config=config
    )
    
    print("\n=== FROM-TO Detection Results ===")
    for r in result:
        print(f"{r.original_detection}:")
        print(f"  FROM: {r.from_line}")
        print(f"  TO: {r.to_line}")
        print()
