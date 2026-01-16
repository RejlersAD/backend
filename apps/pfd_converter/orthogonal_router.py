"""
ORTHOGONAL LINE ROUTER
======================
Professional P&ID line routing with 90° angles only, no diagonals
"""

from typing import List, Tuple, Set
from reportlab.lib.units import mm


class OrthogonalRouter:
    """Routes process lines using only horizontal and vertical segments"""
    
    def __init__(self, spacing: float = 10*mm):
        self.spacing = spacing  # Minimum spacing between parallel lines
        self.occupied_segments = set()  # Track used line segments
        self.line_offsets = {}  # Track offsets for parallel lines
        
    def route(self, start: Tuple[float, float], end: Tuple[float, float], 
              line_id: str = None) -> List[Tuple[float, float]]:
        """
        Route from start to end using orthogonal path
        
        Args:
            start: (x, y) starting point
            end: (x, y) ending point
            line_id: Optional identifier for tracking parallel lines
            
        Returns:
            List of (x, y) waypoints forming orthogonal path
        """
        sx, sy = start
        ex, ey = end
        
        # Calculate offset for parallel lines
        offset = self._get_offset_for_parallel_line(start, end, line_id)
        
        waypoints = []
        waypoints.append((sx, sy))
        
        # Determine routing strategy based on relative positions
        dx = ex - sx
        dy = ey - sy
        
        if abs(dx) > abs(dy):
            # Horizontal-dominant routing
            # Go horizontal first, then vertical
            mid_x = sx + dx * 0.6  # 60% of horizontal distance
            
            # Add offset for parallel routing
            if abs(dy) > 1*mm:  # Only add mid-point if significant vertical change
                waypoints.append((mid_x, sy + offset))
                waypoints.append((mid_x, ey + offset))
            else:
                waypoints.append((mid_x, sy + offset))
        else:
            # Vertical-dominant routing
            # Go vertical first, then horizontal
            mid_y = sy + dy * 0.6  # 60% of vertical distance
            
            # Add offset for parallel routing
            if abs(dx) > 1*mm:  # Only add mid-point if significant horizontal change
                waypoints.append((sx + offset, mid_y))
                waypoints.append((ex + offset, mid_y))
            else:
                waypoints.append((sx + offset, mid_y))
        
        waypoints.append((ex, ey))
        
        # Record this route
        if line_id:
            self._record_route(waypoints, line_id)
        
        return waypoints
    
    def _get_offset_for_parallel_line(self, start: Tuple[float, float], 
                                      end: Tuple[float, float], line_id: str) -> float:
        """Calculate offset for lines running parallel"""
        if not line_id:
            return 0
        
        # Create key representing this general direction
        sx, sy = start
        ex, ey = end
        
        # Round to nearest 50mm to group parallel lines
        direction_key = (
            round(sx / (50*mm)),
            round(sy / (50*mm)),
            round(ex / (50*mm)),
            round(ey / (50*mm))
        )
        
        if direction_key not in self.line_offsets:
            self.line_offsets[direction_key] = []
        
        # Find next available offset slot
        existing_offsets = self.line_offsets[direction_key]
        if line_id not in existing_offsets:
            offset_index = len(existing_offsets)
            existing_offsets.append(line_id)
            # Alternate positive and negative offsets
            return (offset_index // 2 + 1) * self.spacing * (1 if offset_index % 2 == 0 else -1)
        
        return 0
    
    def _record_route(self, waypoints: List[Tuple[float, float]], line_id: str):
        """Record route segments as occupied"""
        for i in range(len(waypoints) - 1):
            segment = (waypoints[i], waypoints[i+1], line_id)
            self.occupied_segments.add(segment)
    
    def get_elbow_positions(self, waypoints: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """Get positions where elbow symbols should be drawn (at direction changes)"""
        elbows = []
        
        for i in range(1, len(waypoints) - 1):
            prev_x, prev_y = waypoints[i-1]
            curr_x, curr_y = waypoints[i]
            next_x, next_y = waypoints[i+1]
            
            # Check if direction changes (elbow needed)
            vec1_horizontal = abs(curr_y - prev_y) < 1*mm
            vec2_horizontal = abs(next_y - curr_y) < 1*mm
            
            # Elbow if one segment horizontal and next vertical (or vice versa)
            if vec1_horizontal != vec2_horizontal:
                elbows.append((curr_x, curr_y))
        
        return elbows


def test_orthogonal_router():
    """Test the orthogonal router"""
    router = OrthogonalRouter()
    
    # Test 1: Simple horizontal-then-vertical
    start = (100*mm, 300*mm)
    end = (400*mm, 200*mm)
    route = router.route(start, end, "line-1")
    print(f"Route 1: {len(route)} waypoints")
    for i, point in enumerate(route):
        print(f"  {i}: ({point[0]/mm:.1f}mm, {point[1]/mm:.1f}mm)")
    
    # Test 2: Parallel line
    start2 = (100*mm, 310*mm)  # 10mm above first line
    end2 = (400*mm, 210*mm)
    route2 = router.route(start2, end2, "line-2")
    print(f"\nRoute 2: {len(route2)} waypoints")
    for i, point in enumerate(route2):
        print(f"  {i}: ({point[0]/mm:.1f}mm, {point[1]/mm:.1f}mm)")
    
    # Test 3: Elbows
    elbows = router.get_elbow_positions(route)
    print(f"\nElbows for route 1: {len(elbows)}")
    for elbow in elbows:
        print(f"  Elbow at ({elbow[0]/mm:.1f}mm, {elbow[1]/mm:.1f}mm)")


if __name__ == "__main__":
    test_orthogonal_router()
