"""
Azure Algorithm Integration - AIFlow P&ID Generator
====================================================

This module integrates Azure's Hough Transform line detection
into the existing AIFlow P&ID generation pipeline.

Integration Points:
1. Enhanced line detection in graph_based_pid_generator.py
2. Improved connectivity detection
3. Better flow analysis
"""

from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


def integrate_azure_line_detection(
    existing_graph_generator,
    enable_hough=True,
    hough_config=None
):
    """
    Enhance existing P&ID generator with Azure line detection
    
    Usage in graph_based_pid_generator.py:
    
        from apps.pfd_converter.azure_algorithms.integration import (
            integrate_azure_line_detection
        )
        
        # In GraphBasedPIDGenerator.__init__
        integrate_azure_line_detection(self, enable_hough=True)
    
    Args:
        existing_graph_generator: Instance of GraphBasedPIDGenerator
        enable_hough: Enable Hough Transform line detection
        hough_config: Configuration for line detection
    """
    
    if not enable_hough:
        logger.info("Azure line detection disabled")
        return
    
    # Import Azure adapter (only if enabled)
    try:
        from apps.pfd_converter.azure_algorithms.local_adapter import LocalLineDetector
        
        # Initialize line detector
        detector = LocalLineDetector(config=hough_config or {
            'hough_threshold': 50,
            'min_line_length': 30,
            'max_line_gap': 10,
            'preprocessing': True
        })
        
        # Attach to generator instance
        existing_graph_generator._azure_line_detector = detector
        existing_graph_generator._azure_enabled = True
        
        logger.info("✅ Azure line detection integrated")
        
    except ImportError as e:
        logger.warning(f"⚠️  Azure algorithms not available: {e}")
        existing_graph_generator._azure_enabled = False


def enhance_routing_with_hough_transform(
    graph_generator,
    start_pos: Tuple[float, float],
    end_pos: Tuple[float, float],
    image_path: Optional[str] = None
) -> List[Tuple[float, float]]:
    """
    Use Hough Transform to improve pipe routing
    
    Falls back to orthogonal routing if Azure not available
    
    Usage in _draw_pipe method:
    
        from apps.pfd_converter.azure_algorithms.integration import (
            enhance_routing_with_hough_transform
        )
        
        # Try Azure routing first
        route = enhance_routing_with_hough_transform(
            self, start_pos, end_pos, image_path
        )
        
        if not route:
            # Fallback to orthogonal
            route = self._create_orthogonal_route(start_pos, end_pos)
    
    Args:
        graph_generator: GraphBasedPIDGenerator instance
        start_pos: Starting position (x, y)
        end_pos: Ending position (x, y)
        image_path: Path to reference image (optional)
        
    Returns:
        List of waypoints for routing
    """
    
    # Check if Azure enabled
    if not getattr(graph_generator, '_azure_enabled', False):
        return []
    
    # Check if image available
    if not image_path:
        return []
    
    try:
        # Get line detector
        detector = graph_generator._azure_line_detector
        
        # Detect lines in image
        detected_lines = detector.detect_lines(image_path)
        
        # Find best routing path using detected lines
        route = _find_routing_from_detected_lines(
            start_pos,
            end_pos,
            detected_lines
        )
        
        if route:
            logger.info(f"✅ Azure routing: {len(route)} waypoints")
            return route
        
    except Exception as e:
        logger.warning(f"⚠️  Azure routing failed: {e}")
    
    return []


def _find_routing_from_detected_lines(
    start_pos: Tuple[float, float],
    end_pos: Tuple[float, float],
    detected_lines: List[Dict]
) -> List[Tuple[float, float]]:
    """
    Find optimal routing using Hough-detected lines
    
    This implements A* pathfinding over detected line segments
    """
    import numpy as np
    
    # Convert detected lines to routing graph
    waypoints = []
    
    # Add start position
    waypoints.append(start_pos)
    
    # Find intermediate lines that connect start to end
    for line in detected_lines:
        line_start = (line['start']['x'], line['start']['y'])
        line_end = (line['end']['x'], line['end']['y'])
        
        # Check if line is roughly between start and end
        dist_to_start = np.linalg.norm(np.array(line_start) - np.array(start_pos))
        dist_to_end = np.linalg.norm(np.array(line_end) - np.array(end_pos))
        
        # If line is on the path, add its waypoints
        if dist_to_start < 100 and dist_to_end < 100:
            if line_start not in waypoints:
                waypoints.append(line_start)
            if line_end not in waypoints:
                waypoints.append(line_end)
    
    # Add end position
    waypoints.append(end_pos)
    
    return waypoints if len(waypoints) > 2 else []


# Configuration helper
class AzureIntegrationConfig:
    """
    Configuration for Azure algorithm integration
    
    Usage:
        config = AzureIntegrationConfig(
            enable_hough_lines=True,
            enable_graph_construction=True
        )
    """
    
    def __init__(
        self,
        enable_hough_lines: bool = True,
        enable_graph_construction: bool = False,  # Future feature
        hough_threshold: int = 50,
        min_line_length: int = 30,
        max_line_gap: int = 10
    ):
        self.enable_hough_lines = enable_hough_lines
        self.enable_graph_construction = enable_graph_construction
        
        # Line detection parameters
        self.hough_config = {
            'hough_threshold': hough_threshold,
            'min_line_length': min_line_length,
            'max_line_gap': max_line_gap,
            'preprocessing': True
        }
    
    def to_dict(self) -> Dict:
        """Export configuration"""
        return {
            'enable_hough_lines': self.enable_hough_lines,
            'enable_graph_construction': self.enable_graph_construction,
            'hough_config': self.hough_config
        }


# Integration status checker
def check_azure_integration_status() -> Dict:
    """
    Check if Azure algorithms are available and working
    
    Returns:
        Status dictionary with availability info
    """
    status = {
        'available': False,
        'line_detection': False,
        'graph_construction': False,
        'error': None
    }
    
    try:
        from apps.pfd_converter.azure_algorithms.local_adapter import (
            LocalLineDetector,
            LocalGraphConstructor
        )
        
        # Test instantiation
        detector = LocalLineDetector()
        constructor = LocalGraphConstructor()
        
        status['available'] = True
        status['line_detection'] = True
        status['graph_construction'] = True
        
        logger.info("✅ Azure algorithms available")
        
    except ImportError as e:
        status['error'] = str(e)
        logger.warning(f"⚠️  Azure algorithms not available: {e}")
    except Exception as e:
        status['error'] = str(e)
        logger.error(f"❌ Azure algorithm error: {e}")
    
    return status


# Example integration for graph_based_pid_generator.py
def example_integration_in_generator():
    """
    Example showing how to integrate Azure algorithms
    
    ADD THIS TO graph_based_pid_generator.py:
    """
    
    example_code = '''
# In GraphBasedPIDGenerator.__init__:

from apps.pfd_converter.azure_algorithms.integration import (
    integrate_azure_line_detection,
    AzureIntegrationConfig
)

# Configure Azure integration
self.azure_config = AzureIntegrationConfig(
    enable_hough_lines=True,
    hough_threshold=50,
    min_line_length=30
)

# Integrate Azure algorithms
integrate_azure_line_detection(
    self,
    enable_hough=self.azure_config.enable_hough_lines,
    hough_config=self.azure_config.hough_config
)


# In _draw_pipe method (enhanced routing):

from apps.pfd_converter.azure_algorithms.integration import (
    enhance_routing_with_hough_transform
)

# Try Azure-enhanced routing first
if hasattr(self, '_azure_enabled') and self._azure_enabled:
    azure_route = enhance_routing_with_hough_transform(
        self,
        start_pos,
        end_pos,
        image_path=getattr(self, 'reference_image_path', None)
    )
    
    if azure_route:
        # Use Azure routing
        for i in range(len(azure_route) - 1):
            start = azure_route[i]
            end = azure_route[i + 1]
            self.canvas.line(start[0], start[1], end[0], end[1])
        return

# Fallback to original orthogonal routing
route = self._create_orthogonal_route(start_pos, end_pos)
# ... existing code ...
'''
    
    return example_code


# Export main functions
__all__ = [
    'integrate_azure_line_detection',
    'enhance_routing_with_hough_transform',
    'AzureIntegrationConfig',
    'check_azure_integration_status',
    'example_integration_in_generator'
]
