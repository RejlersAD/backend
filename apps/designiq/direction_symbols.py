"""
Direction Symbol Detection Module
Detects triangles and arrows on P&ID diagrams using computer vision.
"""

# Conditional import for cv2 (graceful fallback if not installed)
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    cv2 = None
    CV2_AVAILABLE = False

import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
import logging

logger = logging.getLogger(__name__)


@dataclass
class DirectionSymbol:
    """Represents a detected direction symbol (triangle/arrow) on a P&ID"""
    id: str
    centroid: Tuple[float, float]  # Normalized coordinates (0-1)
    bbox: Tuple[float, float, float, float]  # (x_min, y_min, x_max, y_max) normalized
    orientation: Optional[float]  # Radians, 0 along +x axis
    kind: str  # 'triangle', 'arrow', 'unknown'
    score: float  # Confidence score


def detect_direction_symbols(
    image: np.ndarray,
    config: Dict
) -> List[DirectionSymbol]:
    """
    Detect direction symbols (triangles/arrows) in a P&ID image.
    
    Args:
        image: Input image (grayscale or BGR)
        config: Configuration dict with:
            - min_symbol_area: Minimum contour area (default: 50)
            - max_symbol_area: Maximum contour area (default: 5000)
            - epsilon_factor: Approx epsilon as fraction of perimeter (default: 0.02)
            - min_vertices: Minimum vertices for polygon (default: 3)
            - max_vertices: Maximum vertices for polygon (default: 7)
            - canny_low: Canny low threshold (default: 50)
            - canny_high: Canny high threshold (default: 150)
    
    Returns:
        List of detected DirectionSymbol objects
    """
    if not CV2_AVAILABLE:
        logger.warning("⚠️ OpenCV not available, cannot detect direction symbols")
        return []
    
    # Get config parameters with defaults
    min_area = config.get('min_symbol_area', 50)
    max_area = config.get('max_symbol_area', 5000)
    epsilon_factor = config.get('epsilon_factor', 0.02)
    min_vertices = config.get('min_vertices', 3)
    max_vertices = config.get('max_vertices', 7)
    canny_low = config.get('canny_low', 50)
    canny_high = config.get('canny_high', 150)
    
    # Step 1: Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
    
    height, width = gray.shape
    
    # Step 2: Apply edge detection
    edges = cv2.Canny(gray, canny_low, canny_high)
    
    # Optional: Apply morphological operations to connect broken edges
    kernel = np.ones((3, 3), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=1)
    edges = cv2.erode(edges, kernel, iterations=1)
    
    # Step 3: Find contours
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    logger.info(f"  🔍 Found {len(contours)} contours in image")
    
    symbols = []
    symbol_id = 0
    
    for contour in contours:
        # Step 4: Filter by area
        area = cv2.contourArea(contour)
        if area < min_area or area > max_area:
            continue
        
        # Step 5: Approximate polygon
        perimeter = cv2.arcLength(contour, True)
        epsilon = epsilon_factor * perimeter
        approx = cv2.approxPolyDP(contour, epsilon, True)
        
        num_vertices = len(approx)
        
        # Step 6: Keep candidates with 3-7 vertices
        if num_vertices < min_vertices or num_vertices > max_vertices:
            continue
        
        # Step 7: Compute centroid from moments
        M = cv2.moments(contour)
        if M["m00"] == 0:
            continue
        
        cx = M["m10"] / M["m00"]
        cy = M["m01"] / M["m00"]
        
        # Normalize coordinates (0-1 range)
        cx_norm = cx / width
        cy_norm = cy / height
        
        # Step 8: Compute bounding box
        x, y, w, h = cv2.boundingRect(contour)
        bbox_norm = (
            x / width,
            y / height,
            (x + w) / width,
            (y + h) / height
        )
        
        # Step 9: Estimate orientation using PCA
        orientation = estimate_orientation_pca(contour)
        
        # Step 10: Classify kind
        kind, score = classify_symbol(approx, num_vertices, area, perimeter)
        
        symbol = DirectionSymbol(
            id=f"sym_{symbol_id}",
            centroid=(cx_norm, cy_norm),
            bbox=bbox_norm,
            orientation=orientation,
            kind=kind,
            score=score
        )
        
        symbols.append(symbol)
        symbol_id += 1
    
    logger.info(f"  ✅ Detected {len(symbols)} direction symbols")
    logger.info(f"     Triangles: {sum(1 for s in symbols if s.kind == 'triangle')}")
    logger.info(f"     Arrows: {sum(1 for s in symbols if s.kind == 'arrow')}")
    logger.info(f"     Unknown: {sum(1 for s in symbols if s.kind == 'unknown')}")
    
    return symbols


def estimate_orientation_pca(contour: np.ndarray) -> Optional[float]:
    """
    Estimate orientation of a contour using PCA.
    
    Args:
        contour: Contour points from OpenCV
    
    Returns:
        Orientation in radians (0 along +x axis), or None if cannot compute
    """
    try:
        # Reshape contour to 2D array
        points = contour.reshape(-1, 2).astype(np.float32)
        
        if len(points) < 3:
            return None
        
        # Compute mean
        mean = np.mean(points, axis=0)
        
        # Center the points
        centered = points - mean
        
        # Compute covariance matrix
        cov = np.cov(centered.T)
        
        # Eigenvalue decomposition
        eigenvalues, eigenvectors = np.linalg.eig(cov)
        
        # Principal component (largest eigenvalue)
        idx = np.argmax(eigenvalues)
        principal_vector = eigenvectors[:, idx]
        
        # Compute angle from +x axis
        orientation = np.arctan2(principal_vector[1], principal_vector[0])
        
        return float(orientation)
        
    except Exception as e:
        logger.warning(f"  ⚠️ PCA orientation failed: {e}")
        return None


def classify_symbol(
    approx: np.ndarray,
    num_vertices: int,
    area: float,
    perimeter: float
) -> Tuple[str, float]:
    """
    Classify a polygon as triangle, arrow, or unknown.
    
    Args:
        approx: Approximated polygon vertices
        num_vertices: Number of vertices
        area: Contour area
        perimeter: Contour perimeter
    
    Returns:
        Tuple of (kind: str, confidence: float)
    """
    # Calculate compactness (circle = 1.0, line = 0.0)
    if perimeter > 0:
        compactness = (4 * np.pi * area) / (perimeter ** 2)
    else:
        compactness = 0
    
    # Triangles: 3 vertices
    if num_vertices == 3:
        # Check if it's a reasonable triangle (not too elongated)
        if compactness > 0.2:  # More compact triangles
            return ('triangle', 0.9)
        else:
            return ('triangle', 0.6)
    
    # Arrows: 5-7 vertices with sharp tip
    elif 5 <= num_vertices <= 7:
        # Arrows typically have one sharp angle (tip)
        angles = compute_polygon_angles(approx)
        
        if angles:
            min_angle = np.min(angles)
            
            # Sharp tip (< 60 degrees)
            if min_angle < np.pi / 3:
                return ('arrow', 0.8)
            else:
                return ('arrow', 0.5)
        
        return ('arrow', 0.5)
    
    # 4 vertices might be diamond-shaped arrows
    elif num_vertices == 4:
        # Check aspect ratio
        x, y, w, h = cv2.boundingRect(approx)
        aspect_ratio = float(w) / float(h) if h > 0 else 0
        
        # Elongated diamonds could be arrows
        if 0.5 < aspect_ratio < 2.0:
            return ('arrow', 0.4)
        else:
            return ('unknown', 0.3)
    
    return ('unknown', 0.2)


def compute_polygon_angles(approx: np.ndarray) -> List[float]:
    """
    Compute internal angles of a polygon.
    
    Args:
        approx: Approximated polygon vertices
    
    Returns:
        List of internal angles in radians
    """
    angles = []
    n = len(approx)
    
    for i in range(n):
        # Get three consecutive points
        p1 = approx[i][0]
        p2 = approx[(i + 1) % n][0]
        p3 = approx[(i + 2) % n][0]
        
        # Vectors
        v1 = p1 - p2
        v2 = p3 - p2
        
        # Angle between vectors
        cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6)
        cos_angle = np.clip(cos_angle, -1.0, 1.0)
        angle = np.arccos(cos_angle)
        
        angles.append(angle)
    
    return angles
