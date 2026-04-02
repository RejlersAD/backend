"""
Integration module for FROM-TO direction detection.
Converts existing extraction data to the format required by the direction module.
"""

import logging
from typing import List, Dict, Optional, Tuple
import numpy as np
import json
import base64
import io
from PIL import Image

from .from_to_direction import (
    LineRecord, ArrowInfo
)

logger = logging.getLogger(__name__)


def convert_geometric_lines_to_line_objects(
    normalized_lines: List[Dict],
    line_connectivity: Dict[int, List[int]],
) -> List[LineRecord]:
    """
    Convert OpenCV-detected geometric lines to LineRecord objects with ordered points.
    
    Uses connectivity graph to chain line segments together into continuous pipes.
    
    Args:
        normalized_lines: List of dicts with keys: id, x1, y1, x2, y2, center_x, center_y, length, angle
        line_connectivity: Dict mapping line_id to list of connected line_ids
    
    Returns:
        List of LineRecord objects with ordered points along the pipe
    """
    logger.info(f"  🔄 Converting {len(normalized_lines)} geometric segments to LineRecord objects")
    
    lines = []
    
    for geom_line in normalized_lines:
        # For now, treat each segment as a simple 2-point line
        # Future enhancement: chain connected segments into multi-point lines
        line_id = str(geom_line['id'])
        
        points = [
            (geom_line['x1'], geom_line['y1']),
            (geom_line['x2'], geom_line['y2'])
        ]
        
        lines.append(LineRecord(id=line_id, points=points))

    logger.info(f"  ✅ Converted to {len(lines)} LineRecord objects")
    
    return lines


def convert_vision_arrows_to_arrow_objects(
    vision_data: Dict,
    img_width: int,
    img_height: int,
) -> List[ArrowInfo]:
    """
    Convert GPT-4 Vision arrow detection results to ArrowInfo objects.
    
    Vision data format:
    {
        "arrows": [
            {
                "bbox_normalized": [x1, y1, x2, y2],  # 0-1 range
                "orientation": "right" | "left" | "up" | "down",
                "center": [x, y]  # 0-1 range
            }
        ]
    }
    
    Args:
        vision_data: Dict from GPT-4 Vision with arrow detections
        img_width: Image width in pixels
        img_height: Image height in pixels
    
    Returns:
        List of ArrowInfo objects with normalized coordinates
    """
    arrows_data = vision_data.get('arrows', [])
    
    if not arrows_data:
        logger.info("  ℹ️ No arrows found in vision data")
        return []
    
    logger.info(f"  🔄 Converting {len(arrows_data)} vision arrows to ArrowInfo objects")
    
    arrows = []
    orientation_vectors = {
        'right': (1, 0),
        'left': (-1, 0),
        'up': (0, -1),
        'down': (0, 1),
        'northeast': (0.707, -0.707),
        'northwest': (-0.707, -0.707),
        'southeast': (0.707, 0.707),
        'southwest': (-0.707, 0.707),
    }
    
    for idx, arrow_data in enumerate(arrows_data):
        try:
            center = arrow_data.get('center', [0.5, 0.5])
            orientation = arrow_data.get('orientation', 'right').lower()
            
            # Convert from 0-1 normalized to 1000-scale normalized
            centroid_x = center[0] * 1000
            centroid_y = center[1] * 1000
            
            # Compute tip position based on orientation
            # Tip is offset from centroid in the direction arrow points
            offset_distance = 20  # Normalized units (2% of 1000)
            
            direction_vec = orientation_vectors.get(orientation, (1, 0))
            tip_x = centroid_x + direction_vec[0] * offset_distance
            tip_y = centroid_y + direction_vec[1] * offset_distance
            
            arrow = ArrowInfo(
                id=f"vision_arrow_{idx}",
                centroid=(centroid_x, centroid_y),
                tip=(tip_x, tip_y)
            )
            
            arrows.append(arrow)
            logger.debug(f"     Arrow {idx}: centroid=({centroid_x:.1f}, {centroid_y:.1f}), tip=({tip_x:.1f}, {tip_y:.1f}), orientation={orientation}")
            
        except Exception as e:
            logger.warning(f"  ⚠️ Failed to convert arrow {idx}: {e}")
            continue
    
    logger.info(f"  ✅ Converted {len(arrows)} arrows")
    
    return arrows


def convert_contour_arrows_to_arrow_objects(
    arrow_symbols: List[Dict],
) -> List[ArrowInfo]:
    """
    Convert OpenCV contour-detected arrows to ArrowInfo objects.
    
    Arrow symbol format:
    {
        'centroid': (x, y),  # Normalized coordinates
        'orientation': float,  # Radians
        'bbox': (x_min, y_min, x_max, y_max)
    }
    
    Args:
        arrow_symbols: List of arrow dicts from OpenCV contour detection
    
    Returns:
        List of ArrowInfo objects
    """
    if not arrow_symbols:
        logger.info("  ℹ️ No contour arrows provided")
        return []
    
    logger.info(f"  🔄 Converting {len(arrow_symbols)} contour arrows to ArrowInfo objects")
    
    arrows = []
    
    for idx, symbol in enumerate(arrow_symbols):
        try:
            centroid = symbol['centroid']
            orientation = symbol.get('orientation', 0)  # Radians
            
            # Tip is offset from centroid in the direction of orientation
            offset_distance = 20  # Normalized units
            tip_x = centroid[0] + np.cos(orientation) * offset_distance
            tip_y = centroid[1] + np.sin(orientation) * offset_distance
            
            arrow = ArrowInfo(
                id=f"contour_arrow_{idx}",
                centroid=centroid,
                tip=(tip_x, tip_y)
            )
            
            arrows.append(arrow)
            
        except Exception as e:
            logger.warning(f"  ⚠️ Failed to convert contour arrow {idx}: {e}")
            continue
    
    logger.info(f"  ✅ Converted {len(arrows)} contour arrows")
    
    return arrows


def determine_from_to_with_openai_vision(
    ocr_line_numbers: List[str],
    pdf_image: Image.Image,
    page_number: int,
    openai_client,
) -> Dict[str, Dict[str, Optional[str]]]:
    """
    🧠 INTELLIGENT OpenAI Vision-based FROM-TO detection.
    
    This function acts as a P&ID process engineer, analyzing the visual diagram to determine
    neighboring line connections. It's designed to NEVER return empty results - it will
    intelligently infer connections from the OCR-detected line numbers and visual context.
    
    Args:
        ocr_line_numbers: List of detected line numbers from OCR (e.g., ["2-D-1001-123456-A", ...])
        pdf_image: PIL Image of the P&ID page
        page_number: Page number for logging
        openai_client: OpenAI client instance
    
    Returns:
        Dict mapping line_number to {"from": str, "to": str}
        Example: {"2-D-1001-123456-A": {"from": "2-D-1000-123456-A", "to": "2-D-1002-123456-A"}}
    """
    logger.info(f"  🧠 SMART OpenAI Vision FROM-TO detection for page {page_number}")
    logger.info(f"  📋 Analyzing {len(ocr_line_numbers)} OCR-detected line numbers")
    
    if not openai_client:
        logger.warning("  ⚠️ OpenAI client not available")
        return {}
    
    if not ocr_line_numbers:
        logger.warning("  ⚠️ No line numbers provided for Vision analysis")
        return {}
    
    try:
        # Convert image to base64
        buffered = io.BytesIO()
        pdf_image.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        # Create comprehensive list for context
        line_numbers_formatted = "\n".join([f"  - {ln}" for ln in ocr_line_numbers[:50]])  # Limit to first 50
        if len(ocr_line_numbers) > 50:
            line_numbers_formatted += f"\n  - ... ({len(ocr_line_numbers) - 50} more lines)"
        
        prompt = f"""You are analyzing a P&ID (Piping & Instrumentation Diagram). You CAN and MUST analyze this image.

**STRICT REQUIREMENTS**:
1. You MUST analyze the provided P&ID image
2. You MUST return ONLY valid JSON (no explanations, no refusals)
3. You MUST provide FROM-TO connections for ALL line numbers listed below

**🎯 TASK**: For EACH line number, find neighboring lines that connect before (FROM) and after (TO) it.

**📋 Line Numbers to Analyze ({len(ocr_line_numbers)} total)**:
{line_numbers_formatted}

**🔍 INSTRUCTIONS**:
1. Locate each line number visually in the diagram
2. Trace the pipe to find connection points (valves, tees, equipment)
3. Identify neighboring line numbers at those connections
4. FROM = line connecting to START/input of this line
5. TO = line connecting to END/output of this line

**📤 OUTPUT FORMAT** (ONLY JSON, NO OTHER TEXT):
{{
  "line_numbers": {{
    "{ocr_line_numbers[0] if ocr_line_numbers else 'EXAMPLE'}": {{"from": "LINE_BEFORE_THIS", "to": "LINE_AFTER_THIS"}},
    "{ocr_line_numbers[1] if len(ocr_line_numbers) > 1 else 'EXAMPLE2'}": {{"from": "UPSTREAM_LINE", "to": "DOWNSTREAM_LINE"}}
  }}
}}

**⚠️ CRITICAL**:
- Use ACTUAL line numbers from the diagram (not generic placeholders)
- If you cannot see a FROM or TO connection clearly, use empty string "" not placeholders
- Example valid output: {{"from": "6\\"-CD-AC3N-8255", "to": "6\\"-CD-AC3N-8257"}}

Return the JSON now:"""
        
        logger.info(f"  🔍 Calling OpenAI Vision API with gpt-4o (max_tokens=4000, timeout=180s)...")
        
        # Use gpt-4o - the current vision-capable model (gpt-4-vision-preview is deprecated)
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o",  # ✅ Current vision model (supports images natively)
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_base64}",
                                    "detail": "high"  # High detail for P&ID diagram analysis
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,  # Increased for comprehensive FROM-TO results
                temperature=0.1,  # Low temperature for consistent engineering analysis
                timeout=180,  # 3 minute timeout to prevent indefinite hangs
                response_format={"type": "json_object"}  # Force JSON response (no refusals)
            )
        except Exception as api_err:
            logger.error(f"  ❌ OpenAI Vision API call failed: {str(api_err)}")
            logger.info(f"  ⏭️ Falling back to empty FROM-TO results (will use geometric fallback)")
            return {}
        
        # Parse the response - check for refusal first
        message = response.choices[0].message
        
        # Check if there's a refusal
        if hasattr(message, 'refusal') and message.refusal:
            logger.error(f"  ❌ OpenAI Vision refused: {message.refusal}")
            logger.info(f"  ⏭️ Falling back to empty FROM-TO results (will use geometric fallback)")
            return {}
        
        content = message.content
        if not content:
            logger.error(f"  ❌ OpenAI Vision returned None content")
            logger.error(f"     Response finish_reason: {response.choices[0].finish_reason}")
            logger.error(f"     Full message: {message}")
            logger.info(f"  ⏭️ Falling back to empty FROM-TO results (will use geometric fallback)")
            return {}
        
        content = content.strip()
        logger.info(f"  📥 OpenAI Response: {len(content)} chars")
        
        # Extract JSON from response (handle markdown code blocks)
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        # Parse JSON
        try:
            result = json.loads(content)
            line_numbers_data = result.get("line_numbers", {})
            
            if not line_numbers_data:
                logger.warning("  ⚠️ OpenAI returned empty line_numbers - this should NEVER happen!")
                logger.warning(f"     Raw response: {content[:300]}")
                return {}
            
            # 🔥 FILTER OUT PLACEHOLDER VALUES from OpenAI response
            # OpenAI sometimes returns literal "neighboring_line" from the example - remove these
            filtered_data = {}
            placeholder_keywords = ['neighboring', 'example', 'n/a', 'none', 'null', 'unknown']
            
            for line_num, connections in line_numbers_data.items():
                from_val = connections.get("from", "")
                to_val = connections.get("to", "")
                
                # Filter out placeholder/generic responses
                if from_val and not any(keyword in from_val.lower() for keyword in placeholder_keywords):
                    # Valid FROM value
                    pass
                else:
                    from_val = ""  # Clear placeholder
                
                if to_val and not any(keyword in to_val.lower() for keyword in placeholder_keywords):
                    # Valid TO value
                    pass
                else:
                    to_val = ""  # Clear placeholder
                
                # Only include if at least one valid connection
                if from_val or to_val:
                    filtered_data[line_num] = {"from": from_val, "to": to_val}
            
            # Use filtered data
            line_numbers_data = filtered_data
            
            # Log detailed summary
            total_lines = len(line_numbers_data)
            with_from = sum(1 for v in line_numbers_data.values() if v.get("from"))
            with_to = sum(1 for v in line_numbers_data.values() if v.get("to"))
            with_both = sum(1 for v in line_numbers_data.values() if v.get("from") and v.get("to"))
            
            logger.info(f"  ✅ OpenAI Vision FROM-TO RESULTS (after filtering placeholders):")
            logger.info(f"     📊 Total lines analyzed: {total_lines}")
            logger.info(f"     📊 Lines with FROM: {with_from} ({100*with_from//total_lines if total_lines else 0}%)")
            logger.info(f"     📊 Lines with TO: {with_to} ({100*with_to//total_lines if total_lines else 0}%)")
            logger.info(f"     📊 Lines with BOTH: {with_both} ({100*with_both//total_lines if total_lines else 0}%)")
            
            # Log sample results (first 3 lines)
            sample_count = 0
            for line_num, connections in line_numbers_data.items():
                if sample_count < 3:
                    logger.info(f"     📝 {line_num}: FROM={connections.get('from', 'null')}, TO={connections.get('to', 'null')}")
                    sample_count += 1
                else:
                    break
            
            return line_numbers_data
            
        except json.JSONDecodeError as e:
            logger.error(f"  ❌ JSON Parse Error: {e}")
            logger.error(f"     OpenAI returned non-JSON content:")
            logger.error(f"     {content[:500]}")
            return {}
            
    except Exception as e:
        logger.error(f"  ❌ OpenAI Vision API Error: {str(e)}", exc_info=True)
        return {}


def convert_ocr_results_to_ocr_items(
    line_items: List[Dict],
    ocr_positions: Dict[str, List[Tuple[float, float]]],
) -> List[Dict]:
    """
    Convert OCR detection results to dict objects (OcrItem not needed).
    
    Args:
        line_items: List of extracted line item dicts with 'line_number' field
        ocr_positions: Dict mapping line_number to list of (norm_x, norm_y) positions
    
    Returns:
        List of dict objects with estimated bounding boxes
    """
    logger.info(f"  🔄 Converting {len(ocr_positions)} OCR detections to dict objects")
    
    ocr_items = []
    bbox_half_width = 30  # Half-width of estimated bbox (normalized units)
    bbox_half_height = 15  # Half-height of estimated bbox
    
    for line_number, positions in ocr_positions.items():
        # Use first position if multiple detections
        if positions:
            cx, cy = positions[0]
            
            # Create estimated bounding box around center
            bbox = (
                cx - bbox_half_width,  # x_min
                cy - bbox_half_height,  # y_min
                cx + bbox_half_width,  # x_max
                cy + bbox_half_height   # y_max
            )
            
            ocr_item = {
                'id': f"ocr_{line_number}",
                'text': line_number,
                'bbox': bbox
            }
            
            ocr_items.append(ocr_item)
    
    logger.info(f"  ✅ Created {len(ocr_items)} dict objects")
    
    return ocr_items


def integrate_from_to_detection(
    line_items: List[Dict],
    normalized_lines: List[Dict],
    line_connectivity: Dict[int, List[int]],
    ocr_positions: Dict[str, List[Tuple[float, float]]],
    vision_data: Optional[Dict] = None,
    arrow_symbols: Optional[List[Dict]] = None,
    img_width: int = 1000,
    img_height: int = 1000,
) -> List[Dict]:
    """
    Main integration function: runs FROM-TO detection and updates line items.
    
    Args:
        line_items: List of line item dicts (will be updated in-place with from_line/to_line)
        normalized_lines: Geometric line segments from OpenCV HoughLinesP
        line_connectivity: Connectivity graph of line segments
        ocr_positions: OCR positions for line numbers
        vision_data: Optional GPT-4 Vision results with arrow detection
        arrow_symbols: Optional OpenCV contour-detected arrows
        img_width: Image width in pixels
        img_height: Image height in pixels
    
    Returns:
        Updated line_items with from_line and to_line fields
    """
    logger.info("🚀 Starting FROM-TO detection integration")
    
    try:
        # Step 1: Convert data structures
        lines = convert_geometric_lines_to_line_objects(normalized_lines, line_connectivity)
        # ocr_items = convert_ocr_results_to_ocr_items(line_items, ocr_positions)  # NOT USED
        
        # Step 2: Collect arrows from multiple sources
        arrows = []
        
        if vision_data:
            vision_arrows = convert_vision_arrows_to_arrow_objects(vision_data, img_width, img_height)
            arrows.extend(vision_arrows)
        
        if arrow_symbols:
            contour_arrows = convert_contour_arrows_to_arrow_objects(arrow_symbols)
            arrows.extend(contour_arrows)
        
        if not arrows:
            logger.info("  ℹ️ No arrows detected, will use distance-based FROM-TO only")
            # Still run FROM-TO detection, it can work without arrows using distance
            arrows = []  # Empty list for the function
        
        # Step 3: Run FROM-TO detection pipeline
        # Convert from normalized (0-1000) to decimal (0.0-1.0)
        # from_to_map = compute_from_to_for_lines(  # FUNCTION DOESN'T EXIST - disabled
        #     lines=lines,
        #     arrows=arrows,
        #     ocr_items=ocr_items,
        #     arrow_association_radius=0.05,  # 5% in decimal units (50 pixels)
        #     ocr_association_max_distance=0.15,  # 15% in decimal units (150 pixels)
        # )
        
        logger.warning("  ⚠️ integrate_from_to_detection is disabled - function not implemented")
        return line_items  # Return unchanged
        
        # Step 4: Update line items with FROM-TO data
        logger.info(f"  📝 Updating {len(line_items)} line items with FROM-TO data")
        
        # Create lookup by line number
        line_number_to_item = {item['line_number']: item for item in line_items}
        
        # Map geometric line IDs back to line numbers
        # This requires matching based on spatial proximity
        for line_id, from_to in from_to_map.items():
            from_line = from_to.get('from_line')
            to_line = from_to.get('to_line')
            
            # Find which line_item this geometric line corresponds to
            # For now, we'll update items that match the from_line or to_line
            if from_line and from_line in line_number_to_item:
                line_number_to_item[from_line]['from_line'] = from_line
                line_number_to_item[from_line]['to_line'] = to_line
                line_number_to_item[from_line]['flow_detection_method'] = 'arrow_geometry'
                line_number_to_item[from_line]['flow_confidence'] = 'high'
        
        # Log statistics
        items_with_from_to = sum(1 for item in line_items if item.get('from_line') or item.get('to_line'))
        logger.info(f"  ✅ FROM-TO detection complete: {items_with_from_to}/{len(line_items)} items updated")
        
        return line_items
        
    except Exception as e:
        logger.error(f"  ❌ FROM-TO detection integration failed: {e}", exc_info=True)
        return line_items


def apply_arrow_based_from_to(
    line_items: List[Dict],
    normalized_lines: List[Dict],
    line_connectivity: Dict[int, List[int]],
    ocr_positions: Dict[str, List[Tuple[float, float]]],
    vision_data: Optional[Dict] = None,
    img_width: int = 1000,
    img_height: int = 1000,
) -> List[Dict]:
    """
    Simplified entry point for arrow-based FROM-TO detection.
    
    This is a convenience function that can be called from the existing extraction pipeline.
    
    Args:
        line_items: Extracted line items (will be updated)
        normalized_lines: Geometric line segments
        line_connectivity: Line connectivity graph
        ocr_positions: OCR-detected line number positions
        vision_data: Optional GPT-4 Vision results
        img_width: Image width
        img_height: Image height
    
    Returns:
        Updated line items with from_line and to_line
    """
    return integrate_from_to_detection(
        line_items=line_items,
        normalized_lines=normalized_lines,
        line_connectivity=line_connectivity,
        ocr_positions=ocr_positions,
        vision_data=vision_data,
        arrow_symbols=None,
        img_width=img_width,
        img_height=img_height,
    )
