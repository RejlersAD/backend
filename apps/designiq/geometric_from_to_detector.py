"""
Geometric FROM-TO Detection for P&ID Diagrams
==============================================

This module detects line connectivity using geometric analysis:
1. Normalize OCR coordinates to [0,1] range
2. Detect line segments from PDF image
3. Assign unique IDs to each line segment
4. Associate line numbers with line segments (spatial proximity)
5. Build connectivity graph based on line intersections/endpoints
6. Infer FROM-TO relationships from connectivity

Author: RAD AI System
Date: 2026-01-29
"""

# Conditional import for cv2 (graceful fallback if not installed)
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    cv2 = None
    CV2_AVAILABLE = False

import numpy as np
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass, field
from collections import defaultdict
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class NormalizedPoint:
    """Normalized coordinate (0-1 range) relative to page dimensions"""
    x: float  # 0.0 = left edge, 1.0 = right edge
    y: float  # 0.0 = top edge, 1.0 = bottom edge
    
    def distance_to(self, other: 'NormalizedPoint') -> float:
        """Euclidean distance to another point"""
        return np.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)


@dataclass
class LineSegment:
    """Detected line segment with normalized coordinates"""
    id: int
    start: NormalizedPoint
    end: NormalizedPoint
    length: float
    angle: float  # radians, 0 = horizontal right
    color_id: int  # unique identifier
    
    def midpoint(self) -> NormalizedPoint:
        """Get center point of line segment"""
        return NormalizedPoint(
            x=(self.start.x + self.end.x) / 2,
            y=(self.start.y + self.end.y) / 2
        )
    
    def contains_point(self, point: NormalizedPoint, threshold: float = 0.01) -> bool:
        """Check if point is on or near this line segment"""
        # Distance from point to line segment
        dx = self.end.x - self.start.x
        dy = self.end.y - self.start.y
        
        if dx == 0 and dy == 0:
            return point.distance_to(self.start) < threshold
        
        # Parameter t along the line
        t = max(0, min(1, ((point.x - self.start.x) * dx + (point.y - self.start.y) * dy) / (dx*dx + dy*dy)))
        
        # Closest point on line segment
        closest = NormalizedPoint(
            x=self.start.x + t * dx,
            y=self.start.y + t * dy
        )
        
        return point.distance_to(closest) < threshold
    
    def is_connected_to(self, other: 'LineSegment', threshold: float = 0.015) -> bool:
        """Check if this line connects to another line at endpoints"""
        # Check all endpoint combinations
        connections = [
            (self.start, other.start),
            (self.start, other.end),
            (self.end, other.start),
            (self.end, other.end)
        ]
        
        for p1, p2 in connections:
            if p1.distance_to(p2) < threshold:
                return True
        
        return False


@dataclass
class LineNumber:
    """Line number detected via OCR with normalized position"""
    tag: str
    position: NormalizedPoint
    bbox: Tuple[float, float, float, float]  # normalized (x, y, width, height)
    confidence: float
    associated_line_id: Optional[int] = None


@dataclass
class ConnectivityGraph:
    """Graph representing line segment connectivity"""
    nodes: Dict[int, LineSegment] = field(default_factory=dict)  # line_id -> LineSegment
    edges: Dict[int, Set[int]] = field(default_factory=lambda: defaultdict(set))  # line_id -> set of connected line_ids
    line_number_map: Dict[str, int] = field(default_factory=dict)  # line_tag -> line_id
    
    def add_node(self, line: LineSegment):
        """Add a line segment node"""
        self.nodes[line.id] = line
        if line.id not in self.edges:
            self.edges[line.id] = set()
    
    def add_edge(self, line_id1: int, line_id2: int):
        """Add bidirectional connection between lines"""
        self.edges[line_id1].add(line_id2)
        self.edges[line_id2].add(line_id1)
    
    def associate_line_number(self, tag: str, line_id: int):
        """Associate a line number with a line segment"""
        self.line_number_map[tag] = line_id
    
    def get_connected_line_tags(self, tag: str) -> List[str]:
        """Get all line tags connected to this line tag"""
        if tag not in self.line_number_map:
            return []
        
        line_id = self.line_number_map[tag]
        connected_ids = self.edges.get(line_id, set())
        
        # Find line tags for connected line IDs
        connected_tags = []
        for other_tag, other_id in self.line_number_map.items():
            if other_id in connected_ids and other_tag != tag:
                connected_tags.append(other_tag)
        
        return connected_tags


class GeometricFromToDetector:
    """
    Detects FROM-TO relationships using geometric line detection and connectivity analysis
    """
    
    def __init__(self, 
                 line_detection_threshold: int = 50,
                 min_line_length: int = 30,
                 max_line_gap: int = 10,
                 association_threshold: float = 0.03):
        """
        Initialize geometric detector
        
        Args:
            line_detection_threshold: Hough transform threshold
            min_line_length: Minimum line length to detect (pixels)
            max_line_gap: Maximum gap between line segments (pixels)
            association_threshold: Max distance to associate line number with line (normalized)
        """
        if not CV2_AVAILABLE:
            raise ImportError("OpenCV (cv2) is required for GeometricFromToDetector but not installed. "
                            "Set ENABLE_ML_FEATURES=true to enable ML/OCR features.")
        
        self.line_detection_threshold = line_detection_threshold
        self.min_line_length = min_line_length
        self.max_line_gap = max_line_gap
        self.association_threshold = association_threshold
        
        logger.info(f"🔧 Geometric FROM-TO Detector initialized")
        logger.info(f"   Line detection threshold: {line_detection_threshold}")
        logger.info(f"   Min line length: {min_line_length}px")
        logger.info(f"   Association threshold: {association_threshold}")
    
    def normalize_coordinates(self, x: float, y: float, page_width: int, page_height: int) -> NormalizedPoint:
        """Convert absolute coordinates to normalized [0,1] range"""
        return NormalizedPoint(
            x=x / page_width if page_width > 0 else 0,
            y=y / page_height if page_height > 0 else 0
        )
    
    def detect_line_segments(self, image: np.ndarray) -> List[LineSegment]:
        """
        Detect line segments in image using OpenCV
        
        Args:
            image: Input image (grayscale or BGR)
        
        Returns:
            List of detected line segments with normalized coordinates
        """
        logger.info(f"🔍 Detecting line segments in image {image.shape}")
        
        # Convert to grayscale if needed
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()
        
        height, width = gray.shape
        
        # Preprocessing: enhance lines
        # 1. Bilateral filter to reduce noise while keeping edges
        gray = cv2.bilateralFilter(gray, 9, 75, 75)
        
        # 2. Adaptive thresholding
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
            cv2.THRESH_BINARY_INV, 11, 2
        )
        
        # 3. Morphological operations to connect line segments
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
        
        # 4. Edge detection
        edges = cv2.Canny(binary, 50, 150, apertureSize=3)
        
        # Detect lines using Probabilistic Hough Transform
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi/180,
            threshold=self.line_detection_threshold,
            minLineLength=self.min_line_length,
            maxLineGap=self.max_line_gap
        )
        
        if lines is None or len(lines) == 0:
            logger.warning("⚠️ No line segments detected")
            return []
        
        # Convert to LineSegment objects with normalized coordinates
        line_segments = []
        for i, line in enumerate(lines):
            x1, y1, x2, y2 = line[0]
            
            # Normalize coordinates
            start = self.normalize_coordinates(x1, y1, width, height)
            end = self.normalize_coordinates(x2, y2, width, height)
            
            # Calculate length and angle
            length = start.distance_to(end)
            angle = np.arctan2(end.y - start.y, end.x - start.x)
            
            # Assign unique color ID (hash-based for consistency)
            color_id = hash((i, x1, y1, x2, y2)) % (10**6)
            
            line_segments.append(LineSegment(
                id=i,
                start=start,
                end=end,
                length=length,
                angle=angle,
                color_id=color_id
            ))
        
        logger.info(f"✅ Detected {len(line_segments)} line segments")
        return line_segments
    
    def associate_line_numbers_with_segments(self,
                                            line_numbers: List[Dict],
                                            line_segments: List[LineSegment],
                                            page_width: int,
                                            page_height: int) -> List[LineNumber]:
        """
        Associate each line number (OCR result) with nearest line segment
        
        Args:
            line_numbers: List of OCR results with positions
            line_segments: Detected line segments
            page_width: Page width for normalization
            page_height: Page height for normalization
        
        Returns:
            List of LineNumber objects with associated line IDs
        """
        logger.info(f"🔗 Associating {len(line_numbers)} line numbers with {len(line_segments)} segments")
        
        line_number_objects = []
        
        for ln_data in line_numbers:
            # Extract position from OCR data
            tag = ln_data.get('line_number', '')
            if not tag:
                continue
            
            # Get bounding box or position
            bbox = ln_data.get('bbox', [0, 0, 0, 0])
            if len(bbox) >= 4:
                # Center of bounding box
                center_x = (bbox[0] + bbox[2]) / 2
                center_y = (bbox[1] + bbox[3]) / 2
            else:
                # Fallback to position if available
                center_x = ln_data.get('x', 0)
                center_y = ln_data.get('y', 0)
            
            # Normalize position
            position = self.normalize_coordinates(center_x, center_y, page_width, page_height)
            
            # Normalize bbox
            norm_bbox = (
                bbox[0] / page_width if page_width > 0 else 0,
                bbox[1] / page_height if page_height > 0 else 0,
                (bbox[2] - bbox[0]) / page_width if page_width > 0 else 0,
                (bbox[3] - bbox[1]) / page_height if page_height > 0 else 0
            ) if len(bbox) >= 4 else (0, 0, 0, 0)
            
            # Find nearest line segment
            min_distance = float('inf')
            nearest_line_id = None
            
            for segment in line_segments:
                # Check if point is ON the line
                if segment.contains_point(position, threshold=self.association_threshold):
                    nearest_line_id = segment.id
                    min_distance = 0
                    break
                
                # Otherwise find minimum distance to line
                # Distance to start point
                dist_start = position.distance_to(segment.start)
                # Distance to end point
                dist_end = position.distance_to(segment.end)
                # Distance to midpoint
                dist_mid = position.distance_to(segment.midpoint())
                
                min_seg_distance = min(dist_start, dist_end, dist_mid)
                
                if min_seg_distance < min_distance:
                    min_distance = min_seg_distance
                    if min_distance < self.association_threshold:
                        nearest_line_id = segment.id
            
            line_number_objects.append(LineNumber(
                tag=tag,
                position=position,
                bbox=norm_bbox,
                confidence=ln_data.get('confidence', 1.0),
                associated_line_id=nearest_line_id
            ))
            
            if nearest_line_id is not None:
                logger.debug(f"   {tag} → Line #{nearest_line_id} (distance: {min_distance:.4f})")
        
        associated_count = sum(1 for ln in line_number_objects if ln.associated_line_id is not None)
        logger.info(f"✅ Associated {associated_count}/{len(line_number_objects)} line numbers with segments")
        
        return line_number_objects
    
    def build_connectivity_graph(self,
                                 line_segments: List[LineSegment],
                                 line_numbers: List[LineNumber]) -> ConnectivityGraph:
        """
        Build connectivity graph from line segments and line numbers
        
        Args:
            line_segments: Detected line segments
            line_numbers: Line numbers with associations
        
        Returns:
            ConnectivityGraph representing line connections
        """
        logger.info(f"📊 Building connectivity graph")
        
        graph = ConnectivityGraph()
        
        # Add all line segments as nodes
        for segment in line_segments:
            graph.add_node(segment)
        
        # Detect connections between line segments (endpoint proximity)
        connection_count = 0
        for i, seg1 in enumerate(line_segments):
            for seg2 in line_segments[i+1:]:
                if seg1.is_connected_to(seg2):
                    graph.add_edge(seg1.id, seg2.id)
                    connection_count += 1
        
        logger.info(f"   Found {connection_count} line-to-line connections")
        
        # Associate line numbers with line IDs
        for ln in line_numbers:
            if ln.associated_line_id is not None:
                graph.associate_line_number(ln.tag, ln.associated_line_id)
        
        logger.info(f"✅ Graph built: {len(graph.nodes)} nodes, {connection_count} edges, {len(graph.line_number_map)} tagged lines")
        
        return graph
    
    def infer_from_to_relationships(self, graph: ConnectivityGraph) -> Dict[str, Dict[str, any]]:
        """
        Infer FROM-TO relationships from connectivity graph
        
        Args:
            graph: Connectivity graph
        
        Returns:
            Dict mapping line_tag -> {from_line, to_line, method, confidence}
        """
        logger.info(f"🎯 Inferring FROM-TO relationships")
        
        relationships = {}
        
        for tag in graph.line_number_map.keys():
            connected_tags = graph.get_connected_line_tags(tag)
            
            if len(connected_tags) == 0:
                # No connections found
                relationships[tag] = {
                    'from_line': '',
                    'to_line': '',
                    'method': 'geometric_isolated',
                    'confidence': 'low'
                }
            elif len(connected_tags) == 1:
                # One connection - could be FROM or TO
                relationships[tag] = {
                    'from_line': connected_tags[0],
                    'to_line': '',
                    'method': 'geometric_single',
                    'confidence': 'medium'
                }
            elif len(connected_tags) == 2:
                # Two connections - ideal case (FROM and TO)
                relationships[tag] = {
                    'from_line': connected_tags[0],
                    'to_line': connected_tags[1],
                    'method': 'geometric_dual',
                    'confidence': 'high'
                }
            else:
                # Multiple connections - branching point
                # Use first two as FROM-TO, store rest in metadata
                relationships[tag] = {
                    'from_line': connected_tags[0],
                    'to_line': connected_tags[1],
                    'method': 'geometric_branch',
                    'confidence': 'medium',
                    'additional_connections': ', '.join(connected_tags[2:])
                }
        
        mapped_count = sum(1 for v in relationships.values() if v['from_line'] or v['to_line'])
        logger.info(f"✅ Inferred relationships for {mapped_count}/{len(relationships)} lines")
        
        return relationships
    
    def process_pdf_page(self,
                         pdf_path: str,
                         page_num: int,
                         line_numbers: List[Dict]) -> Dict[str, Dict[str, any]]:
        """
        Complete pipeline: detect lines, build graph, infer FROM-TO
        
        Args:
            pdf_path: Path to PDF file
            page_num: Page number (0-indexed)
            line_numbers: OCR-detected line numbers with positions
        
        Returns:
            FROM-TO relationship mapping
        """
        logger.info(f"🚀 Processing PDF page for geometric FROM-TO detection")
        logger.info(f"   PDF: {Path(pdf_path).name}")
        logger.info(f"   Page: {page_num}")
        logger.info(f"   Line numbers: {len(line_numbers)}")
        
        try:
            # Convert PDF page to image
            import pdf2image
            
            images = pdf2image.convert_from_path(
                pdf_path,
                first_page=page_num + 1,
                last_page=page_num + 1,
                dpi=200  # Good balance of quality and speed
            )
            
            if not images:
                logger.error("❌ Failed to convert PDF page to image")
                return {}
            
            # Convert PIL Image to numpy array
            image = np.array(images[0])
            height, width = image.shape[:2]
            
            logger.info(f"   Image dimensions: {width}x{height}")
            
            # Step 1: Detect line segments
            line_segments = self.detect_line_segments(image)
            
            if not line_segments:
                logger.warning("⚠️ No line segments detected, cannot infer FROM-TO")
                return {}
            
            # Step 2: Associate line numbers with segments
            line_number_objects = self.associate_line_numbers_with_segments(
                line_numbers, line_segments, width, height
            )
            
            # Step 3: Build connectivity graph
            graph = self.build_connectivity_graph(line_segments, line_number_objects)
            
            # Step 4: Infer FROM-TO relationships
            relationships = self.infer_from_to_relationships(graph)
            
            logger.info(f"✅ Geometric FROM-TO detection complete")
            
            return relationships
            
        except ImportError:
            logger.error("❌ pdf2image not installed. Run: pip install pdf2image")
            return {}
        except Exception as e:
            logger.error(f"❌ Error in geometric FROM-TO detection: {e}")
            logger.exception(e)
            return {}
