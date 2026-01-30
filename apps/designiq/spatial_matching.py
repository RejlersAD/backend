"""
Spatial Matching for FROM-TO Detection
Based on research paper: "Automated counting of piping and instrumentation diagram using artificial intelligence"

Key Technique: Spatially match extracted text descriptions with visual line representations
"""

import cv2
import numpy as np
from typing import List, Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class SpatialLineDetector:
    """
    Detects line geometries on P&ID diagrams using OpenCV
    Implements spatial matching from research paper
    """
    
    def __init__(self):
        self.min_line_length = 50
        self.max_line_gap = 10
        
    def detect_line_geometries(self, image: np.ndarray) -> List[Dict]:
        """
        Detect actual line positions and endpoints on P&ID
        
        Args:
            image: Input P&ID image (BGR format)
            
        Returns:
            List of line objects with start/end coordinates
        """
        try:
            # Convert to grayscale
            if len(image.shape) == 3:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            else:
                gray = image
            
            # Edge detection
            edges = cv2.Canny(gray, 50, 150, apertureSize=3)
            
            # Hough line detection
            lines = cv2.HoughLinesP(
                edges,
                rho=1,
                theta=np.pi/180,
                threshold=100,
                minLineLength=self.min_line_length,
                maxLineGap=self.max_line_gap
            )
            
            if lines is None:
                logger.warning("No lines detected in image")
                return []
            
            line_objects = []
            for idx, line in enumerate(lines):
                x1, y1, x2, y2 = line[0]
                
                # Calculate line properties
                length = self._calculate_distance((x1, y1), (x2, y2))
                angle = np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi
                
                # Determine if horizontal or vertical (typical for P&ID lines)
                is_horizontal = abs(angle) < 15 or abs(angle) > 165
                is_vertical = 75 < abs(angle) < 105
                
                line_objects.append({
                    'id': idx,
                    'start': (x1, y1),
                    'end': (x2, y2),
                    'center': ((x1 + x2) / 2, (y1 + y2) / 2),
                    'length': length,
                    'angle': angle,
                    'is_horizontal': is_horizontal,
                    'is_vertical': is_vertical,
                    'from_line': None,
                    'to_line': None
                })
            
            logger.info(f"Detected {len(line_objects)} lines in image")
            return line_objects
            
        except Exception as e:
            logger.error(f"Error detecting line geometries: {e}")
            return []
    
    def spatial_matching_from_to(
        self, 
        line_numbers: List[Dict], 
        line_geometries: List[Dict],
        image_width: int,
        image_height: int
    ) -> Dict[str, Dict]:
        """
        Match line number text positions to nearest line endpoints
        Implements spatial matching from research paper
        
        Args:
            line_numbers: List of detected line numbers with bboxes
                         Format: [{'text': '12-AB-001', 'bbox': [[x0,y0],[x1,y1],[x2,y2],[x3,y3]], ...}, ...]
                         or [{'text': '12-AB-001', 'bbox': (x, y, w, h), ...}, ...]
            line_geometries: List of detected line geometries
            image_width: Image width for normalization
            image_height: Image height for normalization
            
        Returns:
            Dict mapping line numbers to FROM-TO relationships
            Format: {'12-AB-001': {'from_line': '12-AB-000', 'to_line': '12-AB-002'}, ...}
        """
        try:
            from_to_map = {}
            
            # First pass: Assign line numbers to nearest line endpoints
            line_to_numbers = {}  # Maps line_id to {start_number, end_number}
            
            for line_num in line_numbers:
                line_tag = line_num.get('line_number') or line_num.get('text')
                if not line_tag:
                    continue
                
                # Get text bounding box center
                bbox = line_num.get('bbox')
                if not bbox:
                    continue
                
                # Convert bbox to center point (handle both polygon and rect formats)
                text_center = self._get_bbox_center(bbox)
                if not text_center:
                    continue
                
                # Find nearest line endpoint to this text
                min_distance = float('inf')
                nearest_line_id = None
                nearest_endpoint = None
                
                for line_geo in line_geometries:
                    line_id = line_geo['id']
                    
                    # Distance to start point
                    dist_start = self._calculate_distance(text_center, line_geo['start'])
                    if dist_start < min_distance:
                        min_distance = dist_start
                        nearest_line_id = line_id
                        nearest_endpoint = 'start'
                    
                    # Distance to end point
                    dist_end = self._calculate_distance(text_center, line_geo['end'])
                    if dist_end < min_distance:
                        min_distance = dist_end
                        nearest_line_id = line_id
                        nearest_endpoint = 'end'
                
                # Assign line number to nearest endpoint
                if nearest_line_id is not None:
                    if nearest_line_id not in line_to_numbers:
                        line_to_numbers[nearest_line_id] = {'start': None, 'end': None}
                    
                    line_to_numbers[nearest_line_id][nearest_endpoint] = line_tag
                    
                    logger.debug(f"Matched '{line_tag}' to line {nearest_line_id} {nearest_endpoint} (dist: {min_distance:.1f}px)")
            
            # Second pass: Determine FROM-TO based on line connections
            for line_num in line_numbers:
                line_tag = line_num.get('line_number') or line_num.get('text')
                if not line_tag:
                    continue
                
                # Find which line this number belongs to
                assigned_line_id = None
                assigned_endpoint = None
                
                for line_id, numbers in line_to_numbers.items():
                    if numbers['start'] == line_tag:
                        assigned_line_id = line_id
                        assigned_endpoint = 'start'
                        break
                    elif numbers['end'] == line_tag:
                        assigned_line_id = line_id
                        assigned_endpoint = 'end'
                        break
                
                if assigned_line_id is None:
                    continue
                
                # Get the line geometry
                line_geo = line_geometries[assigned_line_id]
                
                # Determine FROM-TO based on endpoint position and connected lines
                from_line = None
                to_line = None
                
                if assigned_endpoint == 'start':
                    # This number is at the START of the line
                    # FROM: Connected line at this end
                    from_line = self._find_connected_line_number(
                        line_geo['start'], 
                        line_geometries, 
                        line_to_numbers,
                        exclude_line_id=assigned_line_id
                    )
                    
                    # TO: The number at the END of this line
                    to_line = line_to_numbers[assigned_line_id]['end']
                    
                else:  # assigned_endpoint == 'end'
                    # This number is at the END of the line
                    # FROM: The number at the START of this line
                    from_line = line_to_numbers[assigned_line_id]['start']
                    
                    # TO: Connected line at this end
                    to_line = self._find_connected_line_number(
                        line_geo['end'],
                        line_geometries,
                        line_to_numbers,
                        exclude_line_id=assigned_line_id
                    )
                
                from_to_map[line_tag] = {
                    'from_line': from_line or '-',
                    'to_line': to_line or '-',
                    'method': 'spatial_matching',
                    'confidence': 'high' if (from_line and to_line) else 'medium'
                }
                
                logger.debug(f"Spatial matching: {line_tag} => FROM: {from_line}, TO: {to_line}")
            
            logger.info(f"Spatial matching complete: {len(from_to_map)} lines mapped")
            return from_to_map
            
        except Exception as e:
            logger.error(f"Error in spatial matching: {e}")
            return {}
    
    def _find_connected_line_number(
        self, 
        point: Tuple[float, float],
        line_geometries: List[Dict],
        line_to_numbers: Dict,
        exclude_line_id: int,
        threshold: float = 20.0
    ) -> Optional[str]:
        """
        Find line number at the OTHER end of a line connected to this point
        
        Args:
            point: (x, y) coordinates to check for connections
            line_geometries: All detected lines
            line_to_numbers: Mapping of line IDs to their endpoint numbers
            exclude_line_id: Don't check this line (it's the current line)
            threshold: Max distance to consider as "connected"
            
        Returns:
            Line number at the other end of connected line, or None
        """
        for line_geo in line_geometries:
            if line_geo['id'] == exclude_line_id:
                continue
            
            if line_geo['id'] not in line_to_numbers:
                continue
            
            numbers = line_to_numbers[line_geo['id']]
            
            # Check if this line's START connects to our point
            dist_start = self._calculate_distance(point, line_geo['start'])
            if dist_start < threshold:
                # Connected at start, return number at END
                if numbers['end']:
                    return numbers['end']
            
            # Check if this line's END connects to our point
            dist_end = self._calculate_distance(point, line_geo['end'])
            if dist_end < threshold:
                # Connected at end, return number at START
                if numbers['start']:
                    return numbers['start']
        
        return None
    
    def _calculate_distance(self, point1: Tuple[float, float], point2: Tuple[float, float]) -> float:
        """Calculate Euclidean distance between two points"""
        return np.sqrt((point1[0] - point2[0])**2 + (point1[1] - point2[1])**2)
    
    def _get_bbox_center(self, bbox) -> Optional[Tuple[float, float]]:
        """
        Convert bbox to center point, handling multiple formats
        
        Args:
            bbox: Either [[x0,y0],[x1,y1],[x2,y2],[x3,y3]] (polygon)
                  or (x, y, w, h) (rectangle)
                  or [x, y, w, h] (list rectangle)
                  
        Returns:
            (center_x, center_y) or None if invalid
        """
        try:
            if isinstance(bbox, (list, tuple)):
                # Check if it's a polygon format [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]
                if len(bbox) >= 4 and isinstance(bbox[0], (list, tuple)) and len(bbox[0]) == 2:
                    # Polygon format from OCR
                    x_coords = [point[0] for point in bbox]
                    y_coords = [point[1] for point in bbox]
                    center_x = sum(x_coords) / len(x_coords)
                    center_y = sum(y_coords) / len(y_coords)
                    return (center_x, center_y)
                
                # Check if it's (x, y, w, h) format
                elif len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
                    x, y, w, h = bbox
                    return (x + w/2, y + h/2)
            
            return None
        except Exception as e:
            logger.error(f"Error converting bbox to center: {e}")
            return None
    
    def visualize_spatial_matching(
        self,
        image: np.ndarray,
        line_geometries: List[Dict],
        line_to_numbers: Dict,
        output_path: str
    ):
        """
        Visualize spatial matching results for debugging
        
        Args:
            image: Original P&ID image
            line_geometries: Detected line geometries
            line_to_numbers: Mapping of line IDs to endpoint numbers
            output_path: Path to save visualization
        """
        try:
            vis_image = image.copy()
            
            # Draw detected lines
            for line_geo in line_geometries:
                color = (0, 255, 0)  # Green for lines
                cv2.line(
                    vis_image,
                    (int(line_geo['start'][0]), int(line_geo['start'][1])),
                    (int(line_geo['end'][0]), int(line_geo['end'][1])),
                    color,
                    2
                )
                
                # Draw endpoints
                cv2.circle(vis_image, (int(line_geo['start'][0]), int(line_geo['start'][1])), 5, (255, 0, 0), -1)
                cv2.circle(vis_image, (int(line_geo['end'][0]), int(line_geo['end'][1])), 5, (0, 0, 255), -1)
                
                # Draw line numbers at endpoints
                line_id = line_geo['id']
                if line_id in line_to_numbers:
                    if line_to_numbers[line_id]['start']:
                        cv2.putText(
                            vis_image,
                            line_to_numbers[line_id]['start'],
                            (int(line_geo['start'][0]) + 10, int(line_geo['start'][1])),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (255, 255, 0),
                            2
                        )
                    
                    if line_to_numbers[line_id]['end']:
                        cv2.putText(
                            vis_image,
                            line_to_numbers[line_id]['end'],
                            (int(line_geo['end'][0]) + 10, int(line_geo['end'][1])),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 255, 255),
                            2
                        )
            
            cv2.imwrite(output_path, vis_image)
            logger.info(f"Saved spatial matching visualization to {output_path}")
            
        except Exception as e:
            logger.error(f"Error creating visualization: {e}")
