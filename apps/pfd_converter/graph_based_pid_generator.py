"""
Graph-Based P&ID Generator
Professional CAD-quality P&ID generation using Network Graph Theory

This uses graph algorithms for:
- Intelligent equipment positioning (topological sort, force-directed layout)
- Smart line routing (A* pathfinding, orthogonal routing)
- Automatic layout optimization
- Minimal line crossings
- Professional process flow visualization

Based on 25+ years process engineering best practices

ENHANCED WITH AZURE ALGORITHMS:
- Hough Transform line detection (from Microsoft Azure P&ID repo)
- Graph-based connectivity analysis
- NO Azure account required - 100% local execution!
"""

import networkx as nx
import re
from reportlab.lib.pagesizes import A1, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from typing import Dict, List, Tuple, Optional, Set
import logging
import math
import os
from datetime import datetime

# Azure P&ID Algorithm Integration (adapted for local use)
try:
    from apps.pfd_converter.azure_algorithms.integration import (
        integrate_azure_line_detection,
        AzureIntegrationConfig,
        check_azure_integration_status
    )
    AZURE_ALGORITHMS_AVAILABLE = True
except ImportError:
    AZURE_ALGORITHMS_AVAILABLE = False
    logger.info("Azure algorithms not available (optional enhancement)")

logger = logging.getLogger(__name__)


def normalize_equipment_data(equipment_list: List[Dict]) -> List[Dict]:
    """
    Normalize equipment data to standardized format
    Maps: description → name, ensures all required fields
    """
    normalized = []
    for eq in equipment_list:
        normalized.append({
            'tag': eq.get('tag', ''),
            'name': eq.get('description') or eq.get('name', ''),  # Use description as name
            'type': eq.get('type', 'vessel').lower(),
            'position_hint': eq.get('position', {}),
            'size': eq.get('size'),
            'duty': eq.get('duty'),
            'material': eq.get('material'),
            'description': eq.get('description', '')
        })
    return normalized


def normalize_stream_data(streams_list: List[Dict]) -> List[Dict]:
    """
    Normalize stream data to standardized format
    Maps: source → from, destination → to
    """
    normalized = []
    for st in streams_list:
        normalized.append({
            'stream_id': st.get('stream_id') or st.get('line_number') or st.get('id', ''),
            'from': st.get('source') or st.get('from', ''),  # Map source → from
            'to': st.get('destination') or st.get('to', ''),  # Map destination → to
            'phase': st.get('phase', 'liquid'),
            'line_size': st.get('line_size') or st.get('size', ''),
            'material': st.get('material', ''),
            'pressure': st.get('pressure'),
            'temperature': st.get('temperature'),
            'flow_rate': st.get('flow_rate')
        })
    return normalized


def validate_connectivity(equipment: List[Dict], streams: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """
    Validate that all streams connect to valid equipment
    Returns: (validated_streams, warnings)
    """
    equipment_tags = {eq['tag'] for eq in equipment if eq.get('tag')}
    validated_streams = []
    warnings = []
    
    for st in streams:
        source = st.get('from', '')
        dest = st.get('to', '')
        
        if not source or not dest:
            warnings.append(f"Stream {st.get('stream_id', '?')}: missing source or destination")
            continue
            
        if source not in equipment_tags:
            warnings.append(f"Stream {st.get('stream_id', '?')}: source '{source}' not found in equipment")
            continue
            
        if dest not in equipment_tags:
            warnings.append(f"Stream {st.get('stream_id', '?')}: destination '{dest}' not found in equipment")
            continue
            
        validated_streams.append(st)
    
    return validated_streams, warnings


class ProcessFlowGraph:
    """
    Build and analyze process flow as a directed graph
    """
    
    def __init__(self, equipment: List[Dict], streams: List[Dict]):
        """
        Initialize process flow graph from equipment and stream data
        
        Args:
            equipment: List of equipment dicts with 'tag', 'type', 'description'
            streams: List of stream dicts with 'source', 'destination', flow data
        """
        self.graph = nx.DiGraph()
        self.equipment = {eq.get('tag', f"EQ-{i}"): eq for i, eq in enumerate(equipment)}
        self.streams = streams
        
        # Build graph
        self._build_graph()
        
    def _build_graph(self):
        """Build networkx graph from equipment and streams"""
        # Add equipment as nodes
        for tag, eq_data in self.equipment.items():
            self.graph.add_node(
                tag,
                type=eq_data.get('type', 'equipment'),
                description=eq_data.get('description', ''),
                data=eq_data
            )
        
        # Add streams as edges (support both 'from'/'to' and 'source'/'destination')
        for stream in self.streams:
            source = stream.get('from') or stream.get('source', '')
            dest = stream.get('to') or stream.get('destination', '')
            
            if source and dest and source in self.equipment and dest in self.equipment:
                self.graph.add_edge(
                    source,
                    dest,
                    stream_data=stream
                )
    
    def get_hierarchical_layout(self, width: float, height: float) -> Dict[str, Tuple[float, float]]:
        """
        Calculate hierarchical layout (upstream → downstream flow)
        
        Returns:
            Dict mapping equipment tag to (x, y) position
        """
        positions = {}
        
        if len(self.graph.nodes) == 0:
            return positions
        
        try:
            # Remove cyclic edges (e.g., bypass lines) for layout purposes
            layout_graph = self.graph.copy()
            
            # Find and remove back edges that create cycles
            try:
                cycles = list(nx.simple_cycles(layout_graph))
                if cycles:
                    logger.info(f"Found {len(cycles)} cycles in graph, removing back edges for layout")
                    for cycle in cycles:
                        # Remove the last edge in the cycle (typically the bypass/recycle)
                        if len(cycle) >= 2:
                            layout_graph.remove_edge(cycle[-1], cycle[0])
            except:
                pass
            
            # Try topological sort for SCHEMATIC PROCESS FLOW (left-to-right)
            layers = list(nx.topological_generations(layout_graph))
            
            # Calculate positions layer by layer (STRICT LEFT-TO-RIGHT FLOW)
            # This creates SCHEMATIC/ELEVATION view, not plan/top view
            layer_width = width / (len(layers) + 1)
            
            # Equipment baseline (horizontal centerline for process flow)
            baseline_y = height / 2  # Center baseline for schematic view
            
            for layer_idx, layer_nodes in enumerate(layers):
                x = layer_width * (layer_idx + 1)
                
                # Minimal vertical spacing (keep equipment near horizontal baseline)
                # This creates SCHEMATIC FLOW VIEW with equipment in line
                node_count = len(layer_nodes)
                vertical_variance = min(height * 0.3, 150)  # Max 30% height variance
                vertical_spacing = vertical_variance / (node_count + 1) if node_count > 1 else 0
                
                for node_idx, node in enumerate(sorted(layer_nodes)):
                    # Center equipment around baseline (schematic view)
                    y_offset = vertical_spacing * (node_idx - node_count/2 + 0.5)
                    y = baseline_y + y_offset
                    positions[node] = (x, y)
                    
        except nx.NetworkXError:
            # Graph has cycles, use force-directed layout
            logger.warning("Graph has cycles, using force-directed layout")
            positions = self._force_directed_layout(width, height)
        
        return positions
    
    def _force_directed_layout(self, width: float, height: float) -> Dict[str, Tuple[float, float]]:
        """
        Force-directed layout for cyclic graphs
        Uses spring layout algorithm
        """
        if len(self.graph.nodes) == 0:
            return {}
        
        # Use networkx spring layout
        spring_pos = nx.spring_layout(self.graph, iterations=100, seed=42)
        
        # Scale to drawing area
        positions = {}
        for node, (x, y) in spring_pos.items():
            # Spring layout returns values in [-1, 1], scale to drawing area
            scaled_x = (x + 1) * width / 2
            scaled_y = (y + 1) * height / 2
            positions[node] = (scaled_x, scaled_y)
        
        return positions
    
    def get_flow_direction(self, source: str, dest: str) -> str:
        """
        Determine primary flow direction for routing
        """
        if not nx.has_path(self.graph, source, dest):
            return 'unknown'
        
        # Simple heuristic based on graph topology
        return 'forward'  # Can be enhanced


class OrthogonalRouter:
    """
    A* pathfinding for orthogonal (Manhattan) line routing
    Ensures professional CAD-style right-angle connections
    """
    
    def __init__(self, grid_size: float = 10*mm):
        """
        Initialize router with grid
        
        Args:
            grid_size: Grid spacing for routing (default 10mm)
        """
        self.grid_size = grid_size
        self.obstacles: Set[Tuple[int, int]] = set()
    
    def add_obstacle(self, x: float, y: float, width: float, height: float):
        """Add equipment as obstacle for routing"""
        # Convert to grid coordinates
        grid_x1 = int(x / self.grid_size)
        grid_y1 = int(y / self.grid_size)
        grid_x2 = int((x + width) / self.grid_size)
        grid_y2 = int((y + height) / self.grid_size)
        
        # Mark all grid cells as obstacles
        for gx in range(grid_x1 - 1, grid_x2 + 2):  # Add margin
            for gy in range(grid_y1 - 1, grid_y2 + 2):
                self.obstacles.add((gx, gy))
    
    def route(self, start: Tuple[float, float], end: Tuple[float, float]) -> List[Tuple[float, float]]:
        """
        Find orthogonal path from start to end avoiding obstacles
        
        Returns:
            List of waypoints forming the route
        """
        # Convert to grid coordinates
        start_grid = (int(start[0] / self.grid_size), int(start[1] / self.grid_size))
        end_grid = (int(end[0] / self.grid_size), int(end[1] / self.grid_size))
        
        # Simple A* implementation
        path = self._astar(start_grid, end_grid)
        
        if not path:
            # Fallback: direct line
            return [start, end]
        
        # Convert grid path back to coordinates
        route = [(x * self.grid_size, y * self.grid_size) for x, y in path]
        
        # Simplify path (remove unnecessary waypoints)
        route = self._simplify_path(route)
        
        return route
    
    def _astar(self, start: Tuple[int, int], goal: Tuple[int, int]) -> List[Tuple[int, int]]:
        """
        A* pathfinding on grid
        """
        from heapq import heappush, heappop
        
        def heuristic(a, b):
            # Manhattan distance
            return abs(a[0] - b[0]) + abs(a[1] - b[1])
        
        open_set = []
        heappush(open_set, (0, start))
        came_from = {}
        g_score = {start: 0}
        
        max_iterations = 1000  # Prevent infinite loops
        iterations = 0
        
        while open_set and iterations < max_iterations:
            iterations += 1
            current = heappop(open_set)[1]
            
            if current == goal:
                # Reconstruct path
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                return list(reversed(path))
            
            # Check neighbors (4-connected grid: up, down, left, right)
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                neighbor = (current[0] + dx, current[1] + dy)
                
                # Skip obstacles
                if neighbor in self.obstacles:
                    continue
                
                tentative_g = g_score[current] + 1
                
                if neighbor not in g_score or tentative_g < g_score[neighbor]:
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    f_score = tentative_g + heuristic(neighbor, goal)
                    heappush(open_set, (f_score, neighbor))
        
        # No path found
        return None
    
    def _simplify_path(self, path: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """
        Remove unnecessary waypoints (collinear points)
        """
        if len(path) < 3:
            return path
        
        simplified = [path[0]]
        
        for i in range(1, len(path) - 1):
            prev = simplified[-1]
            curr = path[i]
            next_pt = path[i + 1]
            
            # Check if points are collinear (same x or same y)
            if not ((prev[0] == curr[0] == next_pt[0]) or (prev[1] == curr[1] == next_pt[1])):
                simplified.append(curr)
        
        simplified.append(path[-1])
        return simplified


class GraphBasedPIDGenerator:
    """
    Professional P&ID Generator using Graph Theory
    
    Features:
    - Intelligent equipment positioning via graph algorithms
    - Smart orthogonal line routing
    - ISA 5.1 compliant symbols
    - Professional CAD-style layout
    """
    
    def __init__(self, drawing_specs: Dict):
        """
        Initialize generator
        
        Args:
            drawing_specs: {
                'drawing_number': str,
                'drawing_title': str,
                'project_name': str,
                'equipment': List[Dict],
                'piping': List[Dict],  # or 'process_streams'
                'instrumentation': List[Dict],  # or 'instruments'
                'valves': List[Dict]
            }
        """
        self.specs = drawing_specs
        
        # A1 landscape (841mm x 594mm)
        self.page_width, self.page_height = landscape(A1)
        
        # STRICT ALIGNMENT SYSTEM - Professional Grid-Based Layout
        self.margin = 20 * mm
        
        # BOTTOM ZONE (0-200mm): Title block + Tables - STRICT GRID
        self.bottom_zone_height = 200 * mm
        self.title_block_width = 250 * mm
        self.title_block_height = 150 * mm
        
        # DRAWING ZONE (200mm - top): Main P&ID diagram
        self.drawing_area_x = self.margin
        self.drawing_area_y = self.margin + self.bottom_zone_height
        self.drawing_width = self.page_width - 2*self.margin
        self.drawing_height = self.page_height - 2*self.margin - self.bottom_zone_height
        
        # SMART DYNAMIC TABLE LAYOUT SYSTEM (Soft-Coded & Flexible)
        # Define table heights first (can be easily adjusted for future growth)
        table_heights = {
            'general_notes': 40*mm,      # Can increase if more notes needed
            'legend': 60*mm,              # Can increase if more symbols needed
            'equipment_schedule': 65*mm,  # Can increase for more equipment
            'instrument_index': 50*mm,    # Can increase for more instruments
            'valve_schedule': 65*mm,      # Can increase for more valves
            'line_list': 50*mm            # Can increase for more lines
        }
        
        # Vertical spacing between stacked tables (minimum gap)
        vertical_spacing = 8*mm
        
        # Calculate row positions dynamically from bottom up
        # This ensures tables never overlap, even if heights change
        row_1_y = self.margin  # Base row (bottom)
        row_2_y = row_1_y + table_heights['general_notes'] + vertical_spacing  # Stack on top
        row_3_y = row_2_y + table_heights['legend'] + vertical_spacing  # Next level up
        
        # Calculate available space before title block
        title_block_start_x = self.page_width - self.margin - self.title_block_width
        
        # Table widths (optimized for no overlap)
        table_width_small = 195*mm   # Col 1 & 2 tables
        table_width_medium = 135*mm  # Col 3 tables (prevents title block overlap)
        table_spacing = 10*mm        # Horizontal spacing between columns
        
        # Calculate column positions with proper spacing
        col_1_x = self.margin
        col_2_x = col_1_x + table_width_small + table_spacing
        col_3_x = col_2_x + table_width_small + table_spacing
        
        # Dynamic width adjustment to prevent overlap with title block
        max_col_3_width = title_block_start_x - col_3_x - table_spacing
        if max_col_3_width < table_width_medium:
            table_width_medium = max_col_3_width
        
        # Divide bottom zone into precise columns and rows
        self.table_grid = {
            'col_1_x': col_1_x,
            'col_2_x': col_2_x,
            'col_3_x': col_3_x,
            'col_4_x': title_block_start_x,
            
            'row_1_y': row_1_y,  # Bottom row - dynamically calculated
            'row_2_y': row_2_y,  # Middle row - stacks on row_1 with spacing
            'row_3_y': row_3_y,  # Upper row - stacks on row_2 with spacing
            'row_4_y': self.margin + 150*mm,  # Title block row (fixed)
            
            'standard_width_small': table_width_small,
            'standard_width_medium': table_width_medium,
            'standard_height_small': 50*mm,
            'standard_height_medium': 65*mm,
            'standard_height_large': 150*mm,
            
            # Store table heights for dynamic access
            'table_heights': table_heights,
            'vertical_spacing': vertical_spacing
        }
        
        # Line weights (ISO standard)
        self.line_weights = {
            'border': 1.0,
            'equipment': 0.7,
            'process': 0.5,
            'instrument': 0.25,
            'grid': 0.1
        }
        
        # Text sizes
        self.text_sizes = {
            'title': 6*mm,
            'equipment_tag': 5*mm,
            'equipment_name': 3*mm,
            'line_number': 3*mm,
            'instrument': 2.5*mm,
            'notes': 2.5*mm
        }
        
        # Symbol sizes
        self.equipment_sizes = {
            'vessel': (60*mm, 150*mm),
            'tank': (80*mm, 120*mm),
            'pump': (40*mm, 40*mm),
            'exchanger': (60*mm, 30*mm),
            'default': (50*mm, 50*mm)
        }
        
        self.instrument_diameter = 15*mm
        self.valve_size = 8*mm
        
        # Build process flow graph
        equipment = self.specs.get('equipment', [])
        streams = self.specs.get('piping', self.specs.get('process_streams', []))
        
        self.flow_graph = ProcessFlowGraph(equipment, streams)
        self.router = OrthogonalRouter(grid_size=5*mm)
        
        # Equipment positions (calculated during generation)
        self.equipment_positions = {}
        
        # ===== AZURE ALGORITHM INTEGRATION =====
        # Enable Microsoft Azure's Hough Transform line detection (100% local!)
        if AZURE_ALGORITHMS_AVAILABLE:
            try:
                # Configure Azure algorithms
                self.azure_config = AzureIntegrationConfig(
                    enable_hough_lines=True,
                    hough_threshold=50,
                    min_line_length=30,
                    max_line_gap=10
                )
                
                # Integrate Azure line detection
                integrate_azure_line_detection(
                    self,
                    enable_hough=self.azure_config.enable_hough_lines,
                    hough_config=self.azure_config.hough_config
                )
                
                logger.info("✅ Azure P&ID algorithms integrated (Hough Transform enabled)")
            except Exception as e:
                logger.warning(f"⚠️  Azure algorithm integration failed: {e}")
        
    def generate(self, output_path: str) -> str:
        """
        Generate P&ID using graph-based layout
        
        Returns:
            Path to generated PDF
        """
        logger.info(f"🎨 Generating graph-based P&ID: {output_path}")
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        c = canvas.Canvas(output_path, pagesize=landscape(A1))
        
        try:
            # 1. Draw border and title block
            logger.info("  → Drawing border and title block")
            self._draw_border_and_title_block(c)
            
            # Draw zone boundaries for debugging (ENABLED to show layout)
            self._draw_zone_boundaries(c)
            
            # 2. Draw grid reference system
            logger.info("  → Drawing grid reference system (A-Z, 1-20)")
            self._draw_grid_reference_system(c)
            
            # 3. Calculate equipment positions using graph layout
            logger.info("  → Calculating intelligent equipment layout")
            self.equipment_positions = self._calculate_equipment_positions()
            
            # 4. Add equipment as obstacles for routing
            logger.info("  → Building routing grid")
            self._register_obstacles()
            
            # 5. Draw equipment
            logger.info("  → Drawing equipment with ISA symbols")
            self._draw_all_equipment(c)
            
            # 6. Route and draw process lines
            logger.info("  → Routing process lines (orthogonal)")
            self._draw_process_lines(c)
            
            # 7. Draw instrumentation
            logger.info("  → Drawing instruments")
            self._draw_instrumentation(c)
            
            # 8. Draw valves
            logger.info("  → Drawing valves")
            self._draw_valves(c)
            
            # 9. Draw legend
            logger.info("  → Drawing comprehensive legend")
            self._draw_legend(c)
            
            # 10. Draw equipment schedule
            logger.info("  → Drawing equipment schedule")
            self._draw_equipment_schedule(c)
            
            # 11. Draw valve schedule
            logger.info("  → Drawing valve schedule")
            self._draw_valve_schedule(c)
            
            # 12. Draw general notes
            logger.info("  → Drawing general notes")
            self._draw_general_notes(c)
            
            # 13. Draw instrument index
            logger.info("  → Drawing instrument index")
            self._draw_instrument_index(c)
            
            # 14. Draw line list
            logger.info("  → Drawing line list")
            self._draw_line_list(c)
            
            # 15. Draw north arrow
            logger.info("  → Drawing north arrow")
            self._draw_north_arrow(c)
            
            c.save()
            
            logger.info(f"✅ Professional P&ID generated successfully")
            return output_path
            
        except Exception as e:
            logger.error(f"❌ Failed to generate P&ID: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
    
    def _calculate_equipment_positions(self) -> Dict[str, Tuple[float, float, float, float]]:
        """
        Calculate equipment positions for SCHEMATIC/PROCESS FLOW VIEW (elevation view)
        
        Strategy:
        1. Use HORIZONTAL LEFT-TO-RIGHT layout (process flow schematic)
        2. Equipment arranged in ELEVATION VIEW (side view, not top view)
        3. Vessels shown VERTICALLY (standing), pumps horizontal
        4. Apply strict left-to-right process flow direction
        
        Returns:
            Dict mapping tag to (x, y, width, height)
        """
        positions = {}
        min_spacing = 200*mm  # Minimum distance between equipment centers
        
        # Try to use PFD position hints first
        has_hints = any(eq.get('position_hint') for eq in self.flow_graph.equipment.values())
        
        if has_hints:
            logger.info("    Using PFD position hints for layout")
            # Use normalized positions from PFD (0.0 to 1.0 scale)
            for tag, eq_data in self.flow_graph.equipment.items():
                pos_hint = eq_data.get('position_hint', {})
                eq_type = eq_data.get('type', 'default').lower()
                
                # Get equipment size
                width, height = self.equipment_sizes.get(eq_type, self.equipment_sizes['default'])
                
                if pos_hint and 'x' in pos_hint and 'y' in pos_hint:
                    # STRICT GRID ALIGNMENT in drawing zone
                    # Quantize positions to 50mm grid for perfect alignment
                    grid_size = 50 * mm
                    usable_width = self.drawing_width - min_spacing * 2
                    usable_height = self.drawing_height - min_spacing * 2
                    
                    x_norm = float(pos_hint['x'])
                    y_norm = float(pos_hint['y'])
                    
                    # Map to drawing coordinates with grid snapping
                    raw_x = self.drawing_area_x + min_spacing + (x_norm * usable_width) - width/2
                    raw_y = self.drawing_area_y + min_spacing + (y_norm * usable_height) - height/2
                    
                    # Snap to grid for perfect alignment
                    actual_x = round(raw_x / grid_size) * grid_size
                    actual_y = round(raw_y / grid_size) * grid_size
                    
                    positions[tag] = (actual_x, actual_y, width, height)
                    logger.debug(f"      {tag}: Grid-aligned ({actual_x:.0f}, {actual_y:.0f})")
        
        # Fill in any missing positions using graph layout
        if len(positions) < len(self.flow_graph.equipment):
            logger.info("    Using graph layout for remaining equipment")
            layout = self.flow_graph.get_hierarchical_layout(
                self.drawing_width,
                self.drawing_height
            )
            
            for tag, (x, y) in layout.items():
                if tag not in positions:
                    eq_data = self.flow_graph.equipment.get(tag, {})
                    eq_type = eq_data.get('type', 'default').lower()
                    width, height = self.equipment_sizes.get(eq_type, self.equipment_sizes['default'])
                    
                    # STRICT GRID ALIGNMENT - snap to 50mm grid
                    grid_size = 50 * mm
                    raw_x = self.drawing_area_x + x - width/2
                    raw_y = self.drawing_area_y + y - height/2
                    
                    actual_x = round(raw_x / grid_size) * grid_size
                    actual_y = round(raw_y / grid_size) * grid_size
                    
                    # Ensure minimum clearance from bottom (add 30mm extra padding)
                    min_y_clearance = self.drawing_area_y + 30 * mm
                    if actual_y < min_y_clearance:
                        actual_y = round(min_y_clearance / grid_size) * grid_size
                    
                    positions[tag] = (actual_x, actual_y, width, height)
        
        # Apply minimum spacing constraints (push equipment apart if too close)
        positions = self._apply_spacing_constraints(positions, min_spacing)
        
        return positions
    
    def _apply_spacing_constraints(self, positions: Dict, min_spacing: float) -> Dict:
        """
        Adjust equipment positions to maintain minimum spacing
        """
        # Simple iterative pushing algorithm
        max_iterations = 50
        for iteration in range(max_iterations):
            moved = False
            tags = list(positions.keys())
            
            for i, tag1 in enumerate(tags):
                for tag2 in tags[i+1:]:
                    x1, y1, w1, h1 = positions[tag1]
                    x2, y2, w2, h2 = positions[tag2]
                    
                    # Calculate center-to-center distance
                    cx1, cy1 = x1 + w1/2, y1 + h1/2
                    cx2, cy2 = x2 + w2/2, y2 + h2/2
                    
                    dx = cx2 - cx1
                    dy = cy2 - cy1
                    distance = math.sqrt(dx**2 + dy**2)
                    
                    # If too close, push apart
                    if distance < min_spacing and distance > 0:
                        # Push proportionally
                        push_factor = (min_spacing - distance) / distance / 2
                        push_x = dx * push_factor
                        push_y = dy * push_factor
                        
                        positions[tag1] = (x1 - push_x, y1 - push_y, w1, h1)
                        positions[tag2] = (x2 + push_x, y2 + push_y, w2, h2)
                        moved = True
            
            if not moved:
                break
        
        return positions
    
    def _draw_zone_boundaries(self, c: canvas.Canvas):
        """Draw visual guide lines showing layout zones (for debugging)"""
        c.setStrokeColor(colors.red)
        c.setLineWidth(0.5)
        c.setDash([3, 3])  # Dashed line
        
        # Bottom zone boundary line
        zone_line_y = self.margin + self.bottom_zone_height
        c.line(self.margin, zone_line_y, self.page_width - self.margin, zone_line_y)
        
        # Add label
        c.setFont("Helvetica", 2*mm)
        c.setFillColor(colors.red)
        c.drawString(self.page_width/2, zone_line_y + 2*mm, "DRAWING ZONE (Equipment above this line)")
        c.setFillColor(colors.black)
        
        # Reset line style
        c.setDash([])
        c.setStrokeColor(colors.black)
    
    def _register_obstacles(self):
        """Register equipment as obstacles for line routing"""
        for tag, (x, y, width, height) in self.equipment_positions.items():
            self.router.add_obstacle(x, y, width, height)
    
    def _draw_grid_reference_system(self, c: canvas.Canvas):
        """Draw professional grid reference system (A-Z horizontal, 1-20 vertical)"""
        c.setStrokeColor(colors.gray)
        c.setLineWidth(0.1*mm)
        c.setFont("Helvetica", 2.5*mm)
        
        # Drawing area boundaries
        grid_x_start = self.drawing_area_x
        grid_x_end = self.page_width - self.margin
        grid_y_start = self.drawing_area_y
        grid_y_end = self.page_height - self.margin
        
        # Horizontal grid (A-Z)
        num_h_divisions = 12  # A-L (12 divisions for ~70mm spacing)
        h_spacing = (grid_x_end - grid_x_start) / num_h_divisions
        
        for i in range(num_h_divisions + 1):
            x = grid_x_start + i * h_spacing
            # Draw vertical grid line
            c.setDash([2, 2])
            c.line(x, grid_y_start, x, grid_y_end)
            c.setDash([])
            
            # Draw grid label at top and bottom
            if i < num_h_divisions:
                label = chr(65 + i)  # A, B, C, ...
                c.setFillColor(colors.black)
                c.drawCentredString(x + h_spacing/2, grid_y_end + 5*mm, label)
                c.drawCentredString(x + h_spacing/2, grid_y_start - 8*mm, label)
        
        # Vertical grid (1-20)
        num_v_divisions = 8  # 1-8 (8 divisions for ~50mm spacing)
        v_spacing = (grid_y_end - grid_y_start) / num_v_divisions
        
        for i in range(num_v_divisions + 1):
            y = grid_y_start + i * v_spacing
            # Draw horizontal grid line
            c.setDash([2, 2])
            c.line(grid_x_start, y, grid_x_end, y)
            c.setDash([])
            
            # Draw grid label at left and right
            if i < num_v_divisions:
                label = str(i + 1)
                c.setFillColor(colors.black)
                c.drawString(grid_x_start - 12*mm, y + v_spacing/2 - 1*mm, label)
                c.drawString(grid_x_end + 5*mm, y + v_spacing/2 - 1*mm, label)
        
        c.setStrokeColor(colors.black)
        c.setFillColor(colors.black)
    
    def _draw_border_and_title_block(self, c: canvas.Canvas):
        """Draw main border and comprehensive professional title block"""
        c.setStrokeColor(colors.black)
        c.setLineWidth(self.line_weights['border'])
        
        # Main border
        c.rect(self.margin, self.margin, 
               self.page_width - 2*self.margin, 
               self.page_height - 2*self.margin)
        
        # STRICT ALIGNMENT: Title block uses Column 4, Row 4 (right side, full height)
        tb_x = self.table_grid['col_4_x']
        tb_y = self.margin
        tb_width = self.title_block_width
        tb_height = self.table_grid['standard_height_large']
        
        # Title block outer border
        c.setLineWidth(0.7*mm)
        c.rect(tb_x, tb_y, tb_width, tb_height)
        
        # === TITLE SECTION (Top) ===
        title_y = tb_y + tb_height - 40*mm
        c.setLineWidth(0.35*mm)
        c.line(tb_x, title_y, tb_x + tb_width, title_y)
        
        c.setFont("Helvetica-Bold", 8*mm)
        title = self.specs.get('drawing_title', 'P&ID Drawing')
        c.drawCentredString(tb_x + tb_width/2, tb_y + tb_height - 20*mm, title)
        
        c.setFont("Helvetica", 4*mm)
        project = self.specs.get('project_name', 'Project Name')
        c.drawCentredString(tb_x + tb_width/2, tb_y + tb_height - 30*mm, project)
        
        # === PROJECT INFORMATION SECTION ===
        info_y = title_y - 5*mm
        c.line(tb_x, info_y, tb_x + tb_width, info_y)
        
        # CLIENT (dynamic from specs)
        c.setFont("Helvetica-Bold", 3*mm)
        c.drawString(tb_x + 5*mm, info_y - 7*mm, "CLIENT:")
        c.setFont("Helvetica", 3*mm)
        client = self.specs.get('client', 'ADNOC - Abu Dhabi National Oil Company')
        c.drawString(tb_x + 30*mm, info_y - 7*mm, client)
        
        # PROJECT (dynamic from specs)
        c.setFont("Helvetica-Bold", 3*mm)
        c.drawString(tb_x + 5*mm, info_y - 13*mm, "PROJECT:")
        c.setFont("Helvetica", 3*mm)
        project_code = self.specs.get('project_code', 'PROJECT-CODE')
        c.drawString(tb_x + 30*mm, info_y - 13*mm, f"{project_code} - {project}")
        
        # CONTRACTOR (dynamic from specs)
        c.setFont("Helvetica-Bold", 3*mm)
        c.drawString(tb_x + 5*mm, info_y - 19*mm, "CONTRACTOR:")
        c.setFont("Helvetica", 3*mm)
        contractor = self.specs.get('contractor', 'Rejlers AB - Engineering Solutions')
        c.drawString(tb_x + 30*mm, info_y - 19*mm, contractor)
        
        # === DRAWING IDENTIFICATION ===
        ident_y = info_y - 25*mm
        c.setLineWidth(0.35*mm)
        c.line(tb_x, ident_y, tb_x + tb_width, ident_y)
        
        # Drawing number (prominent)
        c.setFont("Helvetica-Bold", 6*mm)
        drawing_num = self.specs.get('drawing_number', 'PID-001')
        c.drawString(tb_x + 5*mm, ident_y - 10*mm, f"DWG NO: {drawing_num}")
        
        # Revision
        c.setFont("Helvetica-Bold", 5*mm)
        revision = self.specs.get('revision', 'A')
        c.drawString(tb_x + tb_width - 30*mm, ident_y - 10*mm, f"REV: {revision}")
        
        # Sheet number
        c.setFont("Helvetica", 3*mm)
        c.drawString(tb_x + 5*mm, ident_y - 17*mm, "SHEET: 1 of 1")
        
        # Scale
        c.drawString(tb_x + 50*mm, ident_y - 17*mm, "SCALE: NTS")
        
        # Drawing status
        c.setFont("Helvetica-Bold", 3*mm)
        c.drawString(tb_x + 100*mm, ident_y - 17*mm, "STATUS: IFA")
        
        # View type - explicitly indicate SCHEMATIC/ELEVATION VIEW
        c.setFont("Helvetica-Bold", 3.5*mm)
        c.setFillColor(colors.HexColor('#0066CC'))  # Blue color for emphasis
        c.drawString(tb_x + 5*mm, ident_y - 23*mm, "VIEW: SCHEMATIC/ELEVATION")
        c.setFillColor(colors.black)  # Reset to black
        
        # === REVISION HISTORY TABLE ===
        rev_y = ident_y - 28*mm  # Adjusted down to accommodate view type
        c.setLineWidth(0.25*mm)
        c.line(tb_x, rev_y, tb_x + tb_width, rev_y)
        
        # Table header
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawString(tb_x + 2*mm, rev_y - 5*mm, "REV")
        c.drawString(tb_x + 15*mm, rev_y - 5*mm, "DATE")
        c.drawString(tb_x + 40*mm, rev_y - 5*mm, "DESCRIPTION")
        c.drawString(tb_x + 140*mm, rev_y - 5*mm, "BY")
        c.drawString(tb_x + 160*mm, rev_y - 5*mm, "CHK")
        c.drawString(tb_x + 180*mm, rev_y - 5*mm, "APP")
        
        # Vertical lines for table
        c.line(tb_x + 12*mm, rev_y, tb_x + 12*mm, rev_y - 30*mm)
        c.line(tb_x + 37*mm, rev_y, tb_x + 37*mm, rev_y - 30*mm)
        c.line(tb_x + 135*mm, rev_y, tb_x + 135*mm, rev_y - 30*mm)
        c.line(tb_x + 155*mm, rev_y, tb_x + 155*mm, rev_y - 30*mm)
        c.line(tb_x + 175*mm, rev_y, tb_x + 175*mm, rev_y - 30*mm)
        
        # Revision entries
        c.setFont("Helvetica", 2.5*mm)
        row_y = rev_y - 11*mm
        
        # Current revision
        c.drawString(tb_x + 4*mm, row_y, revision)
        c.drawString(tb_x + 15*mm, row_y, datetime.now().strftime("%d-%b-%Y"))
        c.drawString(tb_x + 40*mm, row_y, "AI-Generated P&ID from PFD")
        c.drawString(tb_x + 140*mm, row_y, "AI")
        c.drawString(tb_x + 160*mm, row_y, "ENG")
        c.drawString(tb_x + 180*mm, row_y, "PM")
        
        c.line(tb_x, row_y - 6*mm, tb_x + tb_width, row_y - 6*mm)
        
        # === APPROVAL SECTION ===
        app_y = rev_y - 32*mm
        c.setLineWidth(0.35*mm)
        c.line(tb_x, app_y, tb_x + tb_width, app_y)
        
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawString(tb_x + 5*mm, app_y - 6*mm, "PREPARED BY:")
        c.drawString(tb_x + 90*mm, app_y - 6*mm, "CHECKED BY:")
        c.drawString(tb_x + 175*mm, app_y - 6*mm, "APPROVED BY:")
        
        c.setFont("Helvetica", 2*mm)
        c.drawString(tb_x + 5*mm, app_y - 11*mm, "AI System")
        c.drawString(tb_x + 90*mm, app_y - 11*mm, "Engineering")
        c.drawString(tb_x + 175*mm, app_y - 11*mm, "Project Manager")
        
        c.drawString(tb_x + 5*mm, app_y - 15*mm, datetime.now().strftime("%d-%b-%Y"))
        c.drawString(tb_x + 90*mm, app_y - 15*mm, "______________")
        c.drawString(tb_x + 175*mm, app_y - 15*mm, "______________")
        
        # === STANDARDS & REFERENCES ===
        std_y = app_y - 18*mm
        c.setLineWidth(0.25*mm)
        c.line(tb_x, std_y, tb_x + tb_width, std_y)
        
        c.setFont("Helvetica", 2*mm)
        c.drawString(tb_x + 3*mm, std_y - 4*mm, "STANDARDS: ISA 5.1, ISO 10628, ASME B31.3")
        c.drawString(tb_x + 3*mm, std_y - 8*mm, "UNITS: Metric (mm, kg, kPa) unless noted")
        
        # Bottom border
        c.setLineWidth(0.7*mm)
        c.line(tb_x, tb_y, tb_x + tb_width, tb_y)
        
        revision = self.specs.get('revision', 'A')
        c.drawString(tb_x + 5*mm, tb_y + 8*mm, f"Rev: {revision}")
        
        date_str = datetime.now().strftime('%Y-%m-%d')
        c.drawString(tb_x + 100*mm, tb_y + 8*mm, f"Date: {date_str}")
        
        # Generation method indicator
        c.setFont("Helvetica", 2*mm)
        c.drawString(tb_x + 5*mm, tb_y + 2*mm, "Generated: Graph-Based AI Layout")
    
    def _draw_all_equipment(self, c: canvas.Canvas):
        """Draw all equipment using ISA symbols"""
        c.setStrokeColor(colors.black)
        c.setLineWidth(self.line_weights['equipment'])
        
        for tag, (x, y, width, height) in self.equipment_positions.items():
            eq_data = self.flow_graph.equipment.get(tag, {})
            eq_type = eq_data.get('type', 'equipment').lower()
            # Use 'name' field which has description
            name = eq_data.get('name') or eq_data.get('description', '')
            
            # Draw appropriate symbol
            if 'vessel' in eq_type or 'column' in eq_type:
                self._draw_vessel_symbol(c, x, y, width, height, tag, name)
            elif 'tank' in eq_type:
                self._draw_tank_symbol(c, x, y, width, height, tag, name)
            elif 'pump' in eq_type:
                self._draw_pump_symbol(c, x, y, width, height, tag, name)
            elif 'exchanger' in eq_type or 'heater' in eq_type:
                self._draw_exchanger_symbol(c, x, y, width, height, tag, name)
            else:
                # Generic equipment box
                self._draw_generic_equipment(c, x, y, width, height, tag, name)
            
            # Add professional annotations for each equipment
            self._draw_equipment_tag_leader(c, tag, x, y, width, height)
            self._draw_insulation_indicator(c, eq_data, x, y, width, height)
            self._draw_vent_drain_symbols(c, eq_type, x, y, width, height)
            self._draw_equipment_process_conditions(c, eq_data, x, y, width, height)
    
    def _draw_equipment_tag_leader(self, c: canvas.Canvas, tag: str, x: float, y: float, w: float, h: float):
        """Draw professional tag with arrow leader pointing to equipment"""
        # Tag position above equipment
        tag_x = x + w/2
        tag_y = y + h + 20*mm
        
        # White background box for tag
        tag_width = len(tag) * 3*mm + 8*mm
        tag_height = 8*mm
        
        c.setFillColor(colors.white)
        c.setLineWidth(0.5*mm)
        c.setStrokeColor(colors.black)
        c.rect(tag_x - tag_width/2, tag_y, tag_width, tag_height, fill=1, stroke=1)
        
        # Tag text
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 4*mm)
        c.drawCentredString(tag_x, tag_y + 2*mm, tag)
        
        # Leader arrow from tag to equipment
        leader_start_x = tag_x
        leader_start_y = tag_y - 1*mm
        leader_end_x = x + w/2
        leader_end_y = y + h
        
        # Draw leader line
        c.setLineWidth(0.35*mm)
        c.line(leader_start_x, leader_start_y, leader_end_x, leader_end_y)
        
        # Arrowhead pointing to equipment
        arrow_size = 2*mm
        c.setFillColor(colors.black)
        path = c.beginPath()
        path.moveTo(leader_end_x, leader_end_y)
        path.lineTo(leader_end_x - arrow_size, leader_end_y + arrow_size*2)
        path.lineTo(leader_end_x + arrow_size, leader_end_y + arrow_size*2)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
    
    def _draw_insulation_indicator(self, c: canvas.Canvas, eq_data: Dict, x: float, y: float, w: float, h: float):
        """Draw insulation indicator (triple line) if equipment is insulated"""
        insulation = eq_data.get('insulation', '').lower()
        if not insulation or insulation == 'none':
            return
        
        # Triple line around equipment outline (3mm offset)
        offset = 3*mm
        c.setLineWidth(0.25*mm)
        c.setStrokeColor(colors.black)
        
        # Insulation outline (for vessel/tank - rectangular approximation)
        eq_type = eq_data.get('type', '').lower()
        
        if 'vessel' in eq_type or 'column' in eq_type:
            # Vessel: draw triple outline around cylinder
            for i in range(3):
                line_offset = offset + i * 1*mm
                c.rect(x - line_offset, y - line_offset, 
                       w + 2*line_offset, h + 2*line_offset, 
                       fill=0, stroke=1)
        
        elif 'tank' in eq_type:
            # Tank: triple outline
            for i in range(3):
                line_offset = offset + i * 1*mm
                c.rect(x - line_offset, y - line_offset, 
                       w + 2*line_offset, h + 2*line_offset, 
                       fill=0, stroke=1)
        
        # Insulation type label (small text)
        c.setFont("Helvetica", 1.5*mm)
        insul_label = ""
        if 'cold' in insulation:
            insul_label = "COLD INSUL"
        elif 'hot' in insulation or 'heat' in insulation:
            insul_label = "HOT INSUL"
        else:
            insul_label = "INSULATED"
        
        if insul_label:
            c.setFillColor(colors.white)
            label_width = len(insul_label) * 1.2*mm
            c.rect(x + w + 5*mm, y + h/2, label_width, 3*mm, fill=1, stroke=1)
            c.setFillColor(colors.black)
            c.drawString(x + w + 6*mm, y + h/2 + 0.5*mm, insul_label)
    
    def _draw_vent_drain_symbols(self, c: canvas.Canvas, eq_type: str, x: float, y: float, w: float, h: float):
        """Draw vent (V) and drain (D) symbols on equipment"""
        c.setLineWidth(0.35*mm)
        c.setFont("Helvetica-Bold", 2*mm)
        
        # Vent on top (for vessels and tanks)
        if 'vessel' in eq_type or 'column' in eq_type or 'tank' in eq_type:
            vent_x = x + w * 0.2
            vent_y = y + h
            
            # Vent circle
            c.setStrokeColor(colors.black)
            c.setFillColor(colors.white)
            c.circle(vent_x, vent_y, 3*mm, fill=1, stroke=1)
            
            # "V" text
            c.setFillColor(colors.black)
            c.drawCentredString(vent_x, vent_y - 1*mm, "V")
            
            # Drain on bottom
            drain_x = x + w * 0.8
            drain_y = y
            
            # Drain circle
            c.setStrokeColor(colors.black)
            c.setFillColor(colors.white)
            c.circle(drain_x, drain_y, 3*mm, fill=1, stroke=1)
            
            # "D" text
            c.setFillColor(colors.black)
            c.drawCentredString(drain_x, drain_y - 1*mm, "D")
    
    def _draw_equipment_process_conditions(self, c: canvas.Canvas, eq_data: Dict, x: float, y: float, w: float, h: float):
        """Draw operating conditions directly on drawing near equipment"""
        # Operating pressure
        op_pressure = eq_data.get('operating_pressure') or eq_data.get('pressure', '')
        design_pressure = eq_data.get('design_pressure', '')
        
        # Operating temperature
        op_temp = eq_data.get('operating_temperature') or eq_data.get('temperature', '')
        design_temp = eq_data.get('design_temperature', '')
        
        # Material of construction
        material = eq_data.get('material', '')
        
        # Position annotations to the right of equipment
        annotation_x = x + w + 10*mm
        annotation_y = y + h * 0.7
        
        c.setFont("Helvetica", 2*mm)
        c.setFillColor(colors.black)
        
        # Operating conditions
        if op_pressure:
            c.drawString(annotation_x, annotation_y, f"Oper: {op_pressure}")
            annotation_y -= 4*mm
        
        if design_pressure:
            c.setFont("Helvetica", 1.8*mm)
            c.drawString(annotation_x, annotation_y, f"Design: {design_pressure}")
            annotation_y -= 3.5*mm
            c.setFont("Helvetica", 2*mm)
        
        if op_temp:
            c.drawString(annotation_x, annotation_y, f"Temp: {op_temp}")
            annotation_y -= 4*mm
        
        if material and material.upper() not in ['CS', 'CARBON STEEL']:
            # Only show if special material
            c.setFont("Helvetica-Bold", 2*mm)
            c.drawString(annotation_x, annotation_y, f"MOC: {material}")

    
    def _draw_vessel_symbol(self, c, x, y, w, h, tag, desc):
        """Draw professional vertical vessel in ELEVATION VIEW (schematic/process flow view)
        
        This draws vessels as STANDING VERTICALLY (elevation/side view)
        NOT as seen from above (plan/top view)
        """
        c.setLineWidth(0.7*mm)
        
        # Vessel shell (main body)
        c.rect(x, y, w, h)
        
        # Top head (elliptical)
        c.arc(x, y + h - w/2, x + w, y + h + w/4, startAng=0, extent=180)
        
        # Bottom head (elliptical)
        c.arc(x, y - w/4, x + w, y + w/2, startAng=180, extent=180)
        
        # Demister pad at top (mesh pattern)
        demister_y = y + h * 0.85
        demister_h = h * 0.08
        c.rect(x + w*0.1, demister_y, w*0.8, demister_h)
        # Mesh pattern
        c.setLineWidth(0.25*mm)
        for i in range(3):
            mesh_y = demister_y + i * demister_h/3
            c.line(x + w*0.1, mesh_y, x + w*0.9, mesh_y)
        c.setLineWidth(0.7*mm)
        
        # Internal trays (3 trays)
        c.setLineWidth(0.35*mm)
        for i in range(1, 4):
            tray_y = y + h * (0.2 + i * 0.15)
            c.line(x + w*0.15, tray_y, x + w*0.85, tray_y)
            # Downcomer
            c.rect(x + w*0.75, tray_y - 8*mm, w*0.08, 8*mm)
        c.setLineWidth(0.7*mm)
        
        # Nozzles with proper flanges
        nozzle_len = 12*mm
        flange_w = 3*mm
        
        # N1 - Top nozzle (vapor outlet)
        n1_x = x + w/2
        n1_y = y + h
        c.line(n1_x, n1_y, n1_x, n1_y + nozzle_len)
        c.rect(n1_x - flange_w, n1_y + nozzle_len - 2*mm, flange_w*2, 2*mm)  # Flange
        c.setFont("Helvetica", 2*mm)
        c.drawString(n1_x + 3*mm, n1_y + nozzle_len - 3*mm, "N1")
        
        # N2 - Bottom nozzle (liquid outlet)
        n2_x = x + w/2
        n2_y = y
        c.line(n2_x, n2_y, n2_x, n2_y - nozzle_len)
        c.rect(n2_x - flange_w, n2_y - nozzle_len, flange_w*2, 2*mm)
        c.drawString(n2_x + 3*mm, n2_y - nozzle_len + 1*mm, "N2")
        
        # N3 - Feed nozzle (side, upper)
        n3_x = x
        n3_y = y + h*0.7
        c.line(n3_x, n3_y, n3_x - nozzle_len, n3_y)
        c.rect(n3_x - nozzle_len, n3_y - flange_w, 2*mm, flange_w*2)
        c.drawString(n3_x - nozzle_len - 6*mm, n3_y - 2*mm, "N3")
        
        # N4 - Level nozzle (side, lower)
        n4_x = x + w
        n4_y = y + h*0.3
        c.line(n4_x, n4_y, n4_x + nozzle_len, n4_y)
        c.rect(n4_x + nozzle_len - 2*mm, n4_y - flange_w, 2*mm, flange_w*2)
        c.drawString(n4_x + nozzle_len + 1*mm, n4_y - 2*mm, "N4")
        
        # Platform and ladder (left side)
        c.setLineWidth(0.35*mm)
        platform_y = y + h*0.5
        # Platform
        c.line(x - 15*mm, platform_y, x, platform_y)
        c.line(x - 15*mm, platform_y - 1*mm, x - 15*mm, platform_y + 1*mm)  # Support
        # Ladder
        ladder_x = x - 10*mm
        c.line(ladder_x, y, ladder_x, platform_y)
        c.line(ladder_x - 3*mm, y, ladder_x - 3*mm, platform_y)
        for rung_i in range(5):
            rung_y = y + rung_i * (platform_y - y) / 5
            c.line(ladder_x - 3*mm, rung_y, ladder_x, rung_y)
        c.setLineWidth(0.7*mm)
        
        # Support skirt
        skirt_h = 8*mm
        c.line(x, y, x - 5*mm, y - skirt_h)
        c.line(x + w, y, x + w + 5*mm, y - skirt_h)
        c.line(x - 5*mm, y - skirt_h, x + w + 5*mm, y - skirt_h)
        
        # Tag is now drawn by _draw_equipment_tag_leader method
    
    def _draw_tank_symbol(self, c, x, y, w, h, tag, desc):
        """Draw professional storage tank in ELEVATION VIEW (schematic view)
        
        This draws tanks as STANDING VERTICALLY (elevation/side view)
        NOT as seen from above (plan/top view)
        """
        c.setLineWidth(0.7*mm)
        
        # Tank shell
        c.rect(x, y, w, h)
        
        # Cone roof (typical for atmospheric tank)
        roof_height = w * 0.25
        c.line(x, y + h, x + w/2, y + h + roof_height)
        c.line(x + w, y + h, x + w/2, y + h + roof_height)
        # Roof vent
        c.circle(x + w/2, y + h + roof_height, 2*mm)
        
        # Shell courses (horizontal lines showing shell sections)
        c.setLineWidth(0.25*mm)
        for i in range(1, 4):
            course_y = y + i * h / 4
            c.line(x, course_y, x + w, course_y)
        c.setLineWidth(0.7*mm)
        
        # Nozzles with flanges
        c.setLineWidth(0.5*mm)
        nozzle_len = 12*mm
        flange_w = 3*mm
        
        # N1 - Inlet nozzle (top, side)
        n1_x = x + w
        n1_y = y + h * 0.8
        c.line(n1_x, n1_y, n1_x + nozzle_len, n1_y)
        c.rect(n1_x + nozzle_len - 2*mm, n1_y - flange_w, 2*mm, flange_w*2)
        c.setFont("Helvetica", 2*mm)
        c.drawString(n1_x + nozzle_len + 1*mm, n1_y - 2*mm, "N1")
        
        # N2 - Outlet nozzle (bottom)
        n2_x = x + w * 0.3
        n2_y = y
        c.line(n2_x, n2_y, n2_x, n2_y - nozzle_len)
        c.rect(n2_x - flange_w, n2_y - nozzle_len, flange_w*2, 2*mm)
        c.drawString(n2_x + 3*mm, n2_y - nozzle_len + 1*mm, "N2")
        
        # N3 - Drain nozzle (bottom)
        n3_x = x + w * 0.7
        n3_y = y
        c.line(n3_x, n3_y, n3_x, n3_y - nozzle_len)
        c.rect(n3_x - flange_w, n3_y - nozzle_len, flange_w*2, 2*mm)
        c.drawString(n3_x + 3*mm, n3_y - nozzle_len + 1*mm, "N3")
        
        # N4 - Level gauge (side)
        n4_x = x
        n4_y = y + h * 0.5
        c.line(n4_x, n4_y, n4_x - nozzle_len, n4_y)
        c.rect(n4_x - nozzle_len, n4_y - flange_w, 2*mm, flange_w*2)
        c.drawString(n4_x - nozzle_len - 8*mm, n4_y - 2*mm, "LG")
        
        # Level gauge glass (visual indicator)
        c.setLineWidth(0.35*mm)
        c.rect(x - nozzle_len - 2*mm, y + h*0.2, 2*mm, h*0.6)
        # Liquid level indication (wavy line at 60%)
        level_y = y + h * 0.6
        for wave_i in range(5):
            wave_x = x - nozzle_len - 1*mm
            wave_y_start = level_y + wave_i * 1*mm
            c.line(wave_x - 0.5*mm, wave_y_start, wave_x + 0.5*mm, wave_y_start + 0.5*mm)
        c.setLineWidth(0.7*mm)
        
        # Foundation
        c.setLineWidth(0.5*mm)
        found_y = y - 3*mm
        c.line(x - 5*mm, found_y, x + w + 5*mm, found_y)
        # Foundation supports
        c.line(x, y, x, found_y)
        c.line(x + w, y, x + w, found_y)
        c.setLineWidth(0.7*mm)
        
        # Tag is now drawn by _draw_equipment_tag_leader method
    
    def _draw_pump_symbol(self, c, x, y, w, h, tag, desc):
        """Draw professional centrifugal pump in SCHEMATIC/ELEVATION VIEW (side view)
        
        This shows pump and motor in side view (elevation), not plan view
        """
        center_x = x + w/2
        center_y = y + h/2
        
        c.setLineWidth(0.7*mm)
        
        # Pump casing (volute shape - spiral)
        pump_radius = min(w, h) / 3
        c.circle(center_x - 5*mm, center_y, pump_radius)
        
        # Volute discharge (tangential)
        volute_start_x = center_x - 5*mm + pump_radius * 0.7
        volute_start_y = center_y + pump_radius * 0.7
        c.line(volute_start_x, volute_start_y, volute_start_x + 5*mm, volute_start_y + 5*mm)
        
        # Motor (behind pump)
        motor_x = center_x + 10*mm
        motor_radius = pump_radius * 0.8
        c.circle(motor_x, center_y, motor_radius)
        # Motor lines (indicating it's a motor)
        c.line(motor_x - motor_radius*0.5, center_y - motor_radius*0.5, 
               motor_x + motor_radius*0.5, center_y + motor_radius*0.5)
        c.line(motor_x - motor_radius*0.5, center_y + motor_radius*0.5,
               motor_x + motor_radius*0.5, center_y - motor_radius*0.5)
        
        # Coupling
        c.setLineWidth(0.5*mm)
        coupling_x = center_x + 2.5*mm
        c.rect(coupling_x - 2*mm, center_y - 3*mm, 4*mm, 6*mm)
        c.setLineWidth(0.7*mm)
        
        # Suction nozzle (larger, from left)
        c.setLineWidth(0.5*mm)
        suction_y = center_y
        suction_len = 15*mm
        c.line(x - suction_len, suction_y, center_x - 5*mm - pump_radius, suction_y)
        # Suction flange
        c.rect(x - suction_len - 2*mm, suction_y - 4*mm, 2*mm, 8*mm)
        c.setFont("Helvetica", 2*mm)
        c.drawString(x - suction_len - 8*mm, suction_y - 2*mm, "SUCT")
        
        # Discharge nozzle (smaller, from top)
        discharge_x = center_x - 5*mm
        discharge_y = center_y + pump_radius
        discharge_len = 12*mm
        c.line(discharge_x, discharge_y, discharge_x, discharge_y + discharge_len)
        # Discharge flange
        c.rect(discharge_x - 3*mm, discharge_y + discharge_len - 2*mm, 6*mm, 2*mm)
        c.drawString(discharge_x + 4*mm, discharge_y + discharge_len - 3*mm, "DISCH")
        c.setLineWidth(0.7*mm)
        
        # Baseplate
        c.setLineWidth(0.35*mm)
        baseplate_y = y
        baseplate_width = w + 10*mm
        c.line(x - 5*mm, baseplate_y, x + baseplate_width - 5*mm, baseplate_y)
        c.line(x - 5*mm, baseplate_y - 2*mm, x - 5*mm, baseplate_y)
        c.line(x + baseplate_width - 5*mm, baseplate_y - 2*mm, x + baseplate_width - 5*mm, baseplate_y)
        c.setLineWidth(0.7*mm)
        
        # Tag is now drawn by _draw_equipment_tag_leader method
    
    def _draw_exchanger_symbol(self, c, x, y, w, h, tag, desc):
        """Draw shell & tube heat exchanger in SCHEMATIC/ELEVATION VIEW
        
        Shows exchanger in side/elevation view for process flow schematic
        """
        # Shell (ellipse approximation with circles)
        c.ellipse(x, y, x + w, y + h)
        
        # Tube bundle (inner circle)
        c.ellipse(x + w*0.2, y + h*0.2, x + w*0.8, y + h*0.8)
        
        # Nozzles
        c.line(x, y + h/2, x - 10*mm, y + h/2)
        c.line(x + w, y + h/2, x + w + 10*mm, y + h/2)
        
        c.setFont("Helvetica-Bold", self.text_sizes['equipment_tag'])
        c.drawCentredString(x + w/2, y - 10*mm, tag)
        
        c.setFont("Helvetica", self.text_sizes['equipment_name'])
        c.drawCentredString(x + w/2, y - 16*mm, desc[:20])
    
    def _draw_generic_equipment(self, c, x, y, w, h, tag, desc):
        """Draw generic equipment box"""
        c.rect(x, y, w, h)
        
        c.setFont("Helvetica-Bold", self.text_sizes['equipment_tag'])
        c.drawCentredString(x + w/2, y + h/2, tag)
        
        c.setFont("Helvetica", self.text_sizes['equipment_name'])
        c.drawCentredString(x + w/2, y - 8*mm, desc[:20])
    
    def _draw_process_lines(self, c: canvas.Canvas):
        """Draw process lines using orthogonal routing with annotations"""
        c.setStrokeColor(colors.black)
        c.setLineWidth(self.line_weights['process'])
        
        streams = self.specs.get('piping', self.specs.get('process_streams', []))
        
        for stream in streams:
            # Support both field naming conventions
            source = stream.get('from') or stream.get('source', '')
            dest = stream.get('to') or stream.get('destination', '')
            
            if source not in self.equipment_positions or dest not in self.equipment_positions:
                logger.warning(f"Skipping stream {stream.get('stream_id', '?')}: source={source}, dest={dest} not in positions")
                continue
            
            # Get equipment positions and sizes
            src_x, src_y, src_w, src_h = self.equipment_positions[source]
            dst_x, dst_y, dst_w, dst_h = self.equipment_positions[dest]
            
            # Get nozzle connection points (not equipment centers)
            src_eq = self.flow_graph.equipment.get(source, {})
            dst_eq = self.flow_graph.equipment.get(dest, {})
            
            start = self._get_outlet_nozzle(src_eq.get('type'), src_x, src_y, src_w, src_h, dst_x)
            end = self._get_inlet_nozzle(dst_eq.get('type'), dst_x, dst_y, dst_w, dst_h, src_x)
            
            # Route the line with Azure-enhanced detection (if available)
            route = None
            
            # Try Azure Hough Transform routing first (better line detection)
            if hasattr(self, '_azure_enabled') and self._azure_enabled:
                try:
                    from apps.pfd_converter.azure_algorithms.integration import enhance_routing_with_hough_transform
                    
                    # Try to get reference image path (if available from analysis)
                    reference_image = self.specs.get('reference_image_path')
                    
                    if reference_image:
                        azure_route = enhance_routing_with_hough_transform(
                            self, start, end, image_path=reference_image
                        )
                        if azure_route and len(azure_route) >= 2:
                            route = azure_route
                            logger.debug(f"✅ Using Azure Hough Transform routing for {source}→{dest}")
                except Exception as e:
                    logger.debug(f"Azure routing fallback: {e}")
            
            # Fallback to standard orthogonal routing
            if route is None:
                route = self.router.route(start, end)
            
            # Draw the routed line
            if len(route) >= 2:
                c.setStrokeColor(colors.black)
                c.setLineWidth(self.line_weights['process'])
                
                for i in range(len(route) - 1):
                    c.line(route[i][0], route[i][1], route[i+1][0], route[i+1][1])
                
                # Add flow direction arrow (1/3 along the path)
                arrow_idx = max(1, len(route) // 3)
                if arrow_idx < len(route):
                    self._draw_flow_arrow(c, route[arrow_idx-1], route[arrow_idx])
            
                # Add line size and stream ID annotation
                self._add_line_annotation(c, route, stream)
    
    def _get_outlet_nozzle(self, eq_type: str, x: float, y: float, w: float, h: float, target_x: float) -> Tuple[float, float]:
        """
        Get outlet nozzle position for equipment type
        
        Args:
            eq_type: Equipment type (vessel, pump, tank)
            x, y, w, h: Equipment bounding box
            target_x: X-coordinate of target equipment (for direction hint)
        """
        eq_type = (eq_type or 'default').lower()
        
        if 'vessel' in eq_type or 'column' in eq_type:
            # Vessel: bottom outlet preferred, or side if target is to the side
            if target_x > x + w:  # Target to right
                return (x + w, y + h * 0.3)  # Side nozzle
            else:
                return (x + w/2, y)  # Bottom nozzle
        
        elif 'pump' in eq_type:
            # Pump: discharge on right side
            return (x + w, y + h/2)
        
        elif 'tank' in eq_type:
            # Tank: bottom outlet
            return (x + w/2, y)
        
        else:
            # Default: right side, middle height
            return (x + w, y + h/2)
    
    def _get_inlet_nozzle(self, eq_type: str, x: float, y: float, w: float, h: float, source_x: float) -> Tuple[float, float]:
        """
        Get inlet nozzle position for equipment type
        
        Args:
            eq_type: Equipment type (vessel, pump, tank)
            x, y, w, h: Equipment bounding box
            source_x: X-coordinate of source equipment (for direction hint)
        """
        eq_type = (eq_type or 'default').lower()
        
        if 'vessel' in eq_type or 'column' in eq_type:
            # Vessel: top inlet or side
            if source_x < x:  # Source to left
                return (x, y + h * 0.7)  # Side nozzle
            else:
                return (x + w/2, y + h)  # Top nozzle
        
        elif 'pump' in eq_type:
            # Pump: suction on left/bottom
            return (x, y + h/2)
        
        elif 'tank' in eq_type:
            # Tank: top inlet
            return (x + w/2, y + h)
        
        else:
            # Default: left side, middle height
            return (x, y + h/2)
    
    def _draw_flow_arrow(self, c: canvas.Canvas, from_pt: Tuple[float, float], to_pt: Tuple[float, float]):
        """Draw professional flow direction arrow on process line"""
        arrow_len = 4*mm
        arrow_width = 2*mm
        
        # Calculate angle of line segment
        dx = to_pt[0] - from_pt[0]
        dy = to_pt[1] - from_pt[1]
        
        if dx == 0 and dy == 0:
            return
            
        angle = math.atan2(dy, dx)
        
        # Arrow tip position (slightly before to_pt)
        tip_x = from_pt[0] + dx * 0.7
        tip_y = from_pt[1] + dy * 0.7
        
        # Arrow wings
        left_angle = angle + 2.6
        right_angle = angle - 2.6
        
        left_x = tip_x - arrow_len * math.cos(left_angle)
        left_y = tip_y - arrow_len * math.sin(left_angle)
        
        right_x = tip_x - arrow_len * math.cos(right_angle)
        right_y = tip_y - arrow_len * math.sin(right_angle)
        
        # Draw filled arrow
        c.setFillColor(colors.black)
        path = c.beginPath()
        path.moveTo(tip_x, tip_y)
        path.lineTo(left_x, left_y)
        path.lineTo(right_x, right_y)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
    
    def _add_line_annotation(self, c: canvas.Canvas, route: List[Tuple[float, float]], stream: Dict):
        """
        Add professional line specification callout with leader arrow
        Format: "L-101" over "6\"-CS-150#-A1" (Line Number over Size-Material-Rating-Class)
        """
        if len(route) < 2:
            return
        
        # Find midpoint of route for callout position
        mid_idx = len(route) // 2
        mid_x, mid_y = route[mid_idx]
        
        # Build professional line specification
        spec_parts = []
        
        # Line size (mandatory)
        line_size = stream.get('line_size') or stream.get('size', '')
        if line_size:
            size_clean = self._format_line_size(line_size)
            spec_parts.append(size_clean)
        else:
            spec_parts.append('6"')  # Default size
        
        # Material code (default to CS for Carbon Steel)
        material = stream.get('material', 'CS')
        if material:
            spec_parts.append(material)
        
        # Pressure rating (default to 150#)
        rating = stream.get('rating', '150#')
        spec_parts.append(rating)
        
        # Pipe class (default to A1)
        pipe_class = stream.get('pipe_class', 'A1')
        spec_parts.append(pipe_class)
        
        # Create full line spec
        line_spec = "-".join(spec_parts)
        
        # Line number
        stream_id = stream.get('stream_id') or stream.get('line_number', '')
        line_number = f"L-{stream_id}" if stream_id else ""
        
        # Position callout box offset from line (above and to the side)
        callout_x = mid_x + 15*mm
        callout_y = mid_y + 10*mm
        
        # Draw leader arrow from line to callout
        c.setLineWidth(0.25*mm)
        c.setStrokeColor(colors.black)
        c.line(mid_x, mid_y, callout_x - 8*mm, callout_y)
        
        # Arrowhead at line
        arrow_size = 1.5*mm
        c.setFillColor(colors.black)
        path = c.beginPath()
        path.moveTo(mid_x, mid_y)
        path.lineTo(mid_x + arrow_size, mid_y + arrow_size)
        path.lineTo(mid_x + arrow_size, mid_y - arrow_size)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
        
        # Draw callout box with white background
        box_width = max(len(line_number), len(line_spec)) * 2.2*mm + 6*mm
        box_height = 9*mm
        
        c.setFillColor(colors.white)
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.35*mm)
        c.rect(callout_x - box_width/2, callout_y - box_height/2, box_width, box_height, fill=1, stroke=1)
        
        # Draw line number (top line, bold)
        if line_number:
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 2.5*mm)
            c.drawCentredString(callout_x, callout_y + 1*mm, line_number)
        
        # Draw line spec (bottom line, regular)
        c.setFont("Helvetica", 2*mm)
        c.drawCentredString(callout_x, callout_y - 3*mm, line_spec)
    
    def _format_line_size(self, size_str: str) -> str:
        """
        Format line size to standard notation
        Examples: "6 inch" → "6\"", "150mm" → "6\"", "4" → "4\""
        """
        if not size_str:
            return ""
        
        # Extract numeric value
        match = re.search(r'(\d+\.?\d*)', str(size_str))
        if match:
            size_num = match.group(1)
            
            # Check if metric (mm)
            if 'mm' in str(size_str).lower():
                # Convert mm to inches approximately
                mm_val = float(size_num)
                inch_val = mm_val / 25.4
                return f"{inch_val:.1f}\""
            else:
                # Assume inches
                return f"{size_num}\""
        
        return str(size_str)
    
    def _draw_arrow(self, c, from_pt, to_pt):
        """Legacy arrow method - kept for compatibility"""
        self._draw_flow_arrow(c, from_pt, to_pt)
        c.line(to_pt[0], to_pt[1],
               to_pt[0] - arrow_len * math.cos(left_angle),
               to_pt[1] - arrow_len * math.sin(left_angle))
        c.line(to_pt[0], to_pt[1],
               to_pt[0] - arrow_len * math.cos(right_angle),
               to_pt[1] - arrow_len * math.sin(right_angle))
    
    def _draw_instrumentation(self, c: canvas.Canvas):
        """Draw professional ISA 5.1 compliant instrument symbols"""
        instruments = self.specs.get('instrumentation', self.specs.get('instruments', []))
        
        c.setLineWidth(self.line_weights['instrument'])
        
        # Draw each instrument with signal line to its location
        for idx, inst in enumerate(instruments):
            tag = inst.get('tag', f'I-{idx+1}')
            inst_type = inst.get('type', '').lower()
            connected_to = inst.get('connected_to') or inst.get('location', '')
            service = inst.get('service', inst.get('description', ''))
            
            # Determine instrument function letters (ISA 5.1)
            function_letters = self._get_isa_function_letters(tag, inst_type, service)
            
            # Determine if local or control room (affects circle style)
            is_local = inst.get('location_type', 'field').lower() in ['field', 'local', 'mounted']
            
            # Try to find connected equipment
            if connected_to in self.equipment_positions:
                eq_x, eq_y, eq_w, eq_h = self.equipment_positions[connected_to]
                
                # Position instrument based on measured variable
                first_letter = function_letters[0] if function_letters else 'X'
                
                if first_letter == 'P':  # Pressure
                    # Pressure transmitter: side or top of vessel
                    inst_x = eq_x + eq_w/2
                    inst_y = eq_y + eq_h + 40*mm
                    tap_x, tap_y = eq_x + eq_w/2, eq_y + eq_h * 0.9
                    
                elif first_letter == 'L':  # Level
                    # Level transmitter: side of vessel at measurement level
                    inst_x = eq_x - 50*mm
                    inst_y = eq_y + eq_h * 0.6
                    tap_x, tap_y = eq_x, eq_y + eq_h * 0.6
                    
                elif first_letter == 'T':  # Temperature
                    # Temperature: thermowell on equipment side
                    inst_x = eq_x + eq_w + 50*mm
                    inst_y = eq_y + eq_h * 0.5
                    tap_x, tap_y = eq_x + eq_w, eq_y + eq_h * 0.5
                    
                elif first_letter == 'F':  # Flow
                    # Flow transmitter: on pipe (shown above equipment)
                    inst_x = eq_x + eq_w/2 + 30*mm
                    inst_y = eq_y + eq_h + 40*mm
                    tap_x, tap_y = eq_x + eq_w/2, eq_y + eq_h
                    
                else:
                    # Generic: above equipment
                    inst_x = eq_x + eq_w/2
                    inst_y = eq_y + eq_h + 40*mm
                    tap_x, tap_y = eq_x + eq_w/2, eq_y + eq_h
                
                # Draw impulse/signal line (dashed for pneumatic/electrical)
                c.setDash([2*mm, 1*mm], 0)
                c.setStrokeColor(colors.black)
                c.setLineWidth(0.25*mm)
                c.line(tap_x, tap_y, inst_x, inst_y - self.instrument_diameter/2)
                c.setDash([], 0)
                
                # Draw ISA 5.1 instrument circle
                c.setLineWidth(0.5*mm)
                c.setFillColor(colors.white)
                
                if is_local:
                    # Local instrument: thin circle
                    c.circle(inst_x, inst_y, self.instrument_diameter/2, fill=1, stroke=1)
                else:
                    # Control room instrument: double circle
                    c.circle(inst_x, inst_y, self.instrument_diameter/2, fill=1, stroke=1)
                    c.circle(inst_x, inst_y, self.instrument_diameter/2 - 1*mm, fill=0, stroke=1)
                
                # Function letters inside circle (ISA 5.1)
                c.setFillColor(colors.black)
                c.setFont("Helvetica-Bold", 2.5*mm)
                
                if len(function_letters) <= 3:
                    # Single line
                    c.drawCentredString(inst_x, inst_y - 1.5*mm, function_letters)
                else:
                    # Two lines for long tags
                    c.setFont("Helvetica-Bold", 2*mm)
                    c.drawCentredString(inst_x, inst_y + 1*mm, function_letters[:3])
                    c.drawCentredString(inst_x, inst_y - 2.5*mm, function_letters[3:])
                
                # Loop number below circle in small font
                c.setFont("Helvetica", 2*mm)
                loop_num = tag.split('-')[-1] if '-' in tag else tag
                c.drawCentredString(inst_x, inst_y - self.instrument_diameter/2 - 4*mm, loop_num)
                
                # Alarm/trip indicators if applicable
                has_alarm = inst.get('has_alarm', False) or 'alarm' in service.lower()
                has_trip = inst.get('has_trip', False) or 'trip' in service.lower()
                
                if has_alarm or has_trip:
                    c.setLineWidth(0.35*mm)
                    # Alarm indicator (A in small circle)
                    if has_alarm:
                        alarm_x = inst_x + self.instrument_diameter/2 + 3*mm
                        c.circle(alarm_x, inst_y, 3*mm)
                        c.setFont("Helvetica-Bold", 2*mm)
                        c.drawCentredString(alarm_x, inst_y - 1*mm, "A")
                    # Trip indicator (interlock symbol)
                    if has_trip:
                        trip_x = inst_x + self.instrument_diameter/2 + 3*mm
                        trip_y = inst_y - 5*mm if has_alarm else inst_y
                        c.circle(trip_x, trip_y, 3*mm)
                        c.setFont("Helvetica-Bold", 2*mm)
                        c.drawCentredString(trip_x, trip_y - 1*mm, "I")
                
            else:
                # Place in sequence at top if no connection
                inst_x = self.margin + 100*mm + idx*60*mm
                inst_y = self.page_height - self.margin - 50*mm
                
                # Draw ISA circle
                c.setLineWidth(0.5*mm)
                c.setFillColor(colors.white)
                c.circle(inst_x, inst_y, self.instrument_diameter/2, fill=1, stroke=1)
                
                # Function letters
                c.setFillColor(colors.black)
                c.setFont("Helvetica-Bold", 2.5*mm)
                c.drawCentredString(inst_x, inst_y - 1.5*mm, function_letters[:4])
    
    def _get_isa_function_letters(self, tag: str, inst_type: str, service: str) -> str:
        """
        Extract ISA 5.1 function letters from instrument tag or type
        
        First letter = Measured/initiating variable (P, T, F, L, etc.)
        Subsequent letters = Functions (I=Indicator, T=Transmitter, C=Controller, etc.)
        
        Examples:
            PT-101  → PT (Pressure Transmitter)
            FIC-202 → FIC (Flow Indicator Controller)
            LAH-303 → LAH (Level Alarm High)
        """
        # Try to extract from tag first (most reliable)
        if '-' in tag:
            prefix = tag.split('-')[0].upper()
            if len(prefix) >= 2 and prefix[0].isalpha():
                return prefix
        
        # Otherwise derive from type/service
        letters = ''
        
        # First letter (measured variable)
        type_lower = inst_type.lower()
        service_lower = service.lower()
        
        if 'pressure' in type_lower or 'pressure' in service_lower:
            letters += 'P'
        elif 'temperature' in type_lower or 'temp' in service_lower:
            letters += 'T'
        elif 'flow' in type_lower or 'flow' in service_lower:
            letters += 'F'
        elif 'level' in type_lower or 'level' in service_lower:
            letters += 'L'
        elif 'analysis' in type_lower or 'analyzer' in type_lower:
            letters += 'A'
        else:
            letters += 'X'  # Unknown
        
        # Subsequent letters (function)
        if 'indicator' in type_lower and 'controller' in type_lower:
            letters += 'IC'
        elif 'controller' in type_lower:
            letters += 'C'
        elif 'transmitter' in type_lower:
            letters += 'T'
        elif 'indicator' in type_lower:
            letters += 'I'
        elif 'switch' in type_lower:
            letters += 'S'
        elif 'alarm' in type_lower:
            letters += 'A'
        else:
            letters += 'T'  # Default to transmitter
        
        return letters
    
    def _draw_valves(self, c: canvas.Canvas):
        """Draw valve symbols ON process lines (not as separate equipment)"""
        valves = self.specs.get('valves', [])
        streams = self.specs.get('piping', self.specs.get('process_streams', []))
        
        if not valves or not streams:
            return
        
        # Place valves on their associated streams
        for valve in valves:
            valve_tag = valve.get('tag', '')
            valve_type = valve.get('type', '').lower()
            
            # Match valve to stream (simplified: use first stream for now)
            # In production: match by tag number, size, or explicit connection
            if streams:
                stream = streams[0]  # Simplified
                source = stream.get('from') or stream.get('source', '')
                dest = stream.get('to') or stream.get('destination', '')
                
                if source in self.equipment_positions and dest in self.equipment_positions:
                    src_x, src_y, src_w, src_h = self.equipment_positions[source]
                    dst_x, dst_y, dst_w, dst_h = self.equipment_positions[dest]
                    
                    # Place valve 1/3 along the line
                    valve_x = src_x + src_w + (dst_x - src_x - src_w) * 0.35
                    valve_y = src_y + src_h/2 + (dst_y + dst_h/2 - src_y - src_h/2) * 0.35
                    
                    # Draw valve symbol
                    self._draw_valve_symbol(c, valve, valve_x, valve_y)
    
    def _draw_valve_symbol(self, c: canvas.Canvas, valve: Dict, x: float, y: float):
        """Draw professional valve symbol based on type"""
        valve_type = valve.get('type', '').lower()
        valve_tag = valve.get('tag', '')
        actuator = valve.get('actuator', '').lower()
        fail_pos = valve.get('fail_position', '').lower()
        size = 8*mm
        
        c.setLineWidth(0.7*mm)
        c.setStrokeColor(colors.black)
        
        # Different symbols for different valve types
        if 'gate' in valve_type:
            # Gate valve: Rectangle with wedge
            c.rect(x - size*0.6, y - size*0.6, size*1.2, size*1.2)
            # Wedge
            c.line(x - size*0.4, y + size*0.6, x, y - size*0.6)
            c.line(x + size*0.4, y + size*0.6, x, y - size*0.6)
            
        elif 'globe' in valve_type:
            # Globe valve: Circle with baffle
            c.circle(x, y, size*0.7)
            # Internal baffle
            c.line(x - size*0.5, y, x - size*0.2, y - size*0.3)
            c.line(x + size*0.5, y, x + size*0.2, y + size*0.3)
            
        elif 'ball' in valve_type:
            # Ball valve: Circle with filled center
            c.circle(x, y, size*0.7)
            c.setFillColor(colors.black)
            c.circle(x, y, size*0.3, fill=1)
            c.setFillColor(colors.white)
            
        elif 'check' in valve_type:
            # Check valve: Triangle with flapper
            path = c.beginPath()
            path.moveTo(x - size*0.8, y - size*0.6)
            path.lineTo(x - size*0.8, y + size*0.6)
            path.lineTo(x + size*0.6, y)
            path.close()
            c.drawPath(path, fill=0, stroke=1)
            # Flapper (hinged disk)
            c.line(x - size*0.3, y - size*0.5, x - size*0.3, y + size*0.5)
            
        elif 'control' in valve_type or 'fcv' in valve_type or 'lcv' in valve_type or 'pcv' in valve_type:
            # Control valve: Circle body
            c.circle(x, y, size*0.7)
            # Flow direction arrow inside
            c.setLineWidth(0.35*mm)
            c.line(x, y - size*0.5, x, y + size*0.5)
            # Arrow head
            c.line(x, y + size*0.5, x - size*0.2, y + size*0.3)
            c.line(x, y + size*0.5, x + size*0.2, y + size*0.3)
            c.setLineWidth(0.7*mm)
            
            # Actuator on top
            if 'pneumatic' in actuator or not actuator:
                # Pneumatic diaphragm actuator
                act_y = y + size*0.7 + size*0.8
                c.setLineWidth(0.5*mm)
                # Diaphragm case
                c.circle(x, act_y, size*0.6)
                # Diaphragm lines
                c.line(x - size*0.4, act_y, x + size*0.4, act_y)
                c.line(x - size*0.3, act_y + size*0.2, x + size*0.3, act_y + size*0.2)
                # Actuator stem
                c.line(x, y + size*0.7, x, act_y - size*0.6)
                
                # Air supply line (3mm instrument line)
                c.setDash([1, 1])
                c.setLineWidth(0.25*mm)
                c.line(x + size*0.6, act_y, x + size*1.2, act_y)
                c.setDash([])
                c.setLineWidth(0.5*mm)
                
                # Fail position indicator
                if 'close' in fail_pos or 'fc' in fail_pos:
                    # Arrow pointing down (spring pushes down to close)
                    c.setFont("Helvetica-Bold", 2*mm)
                    c.drawString(x + size*1.3, act_y - 2*mm, "FC")
                    c.line(x + size*1.2, act_y - 4*mm, x + size*1.2, act_y - 8*mm)
                    # Arrow head down
                    c.line(x + size*1.2, act_y - 8*mm, x + size*1.0, act_y - 6*mm)
                    c.line(x + size*1.2, act_y - 8*mm, x + size*1.4, act_y - 6*mm)
                elif 'open' in fail_pos or 'fo' in fail_pos:
                    # Arrow pointing up (spring pushes up to open)
                    c.setFont("Helvetica-Bold", 2*mm)
                    c.drawString(x + size*1.3, act_y - 2*mm, "FO")
                    c.line(x + size*1.2, act_y + 4*mm, x + size*1.2, act_y + 8*mm)
                    # Arrow head up
                    c.line(x + size*1.2, act_y + 8*mm, x + size*1.0, act_y + 6*mm)
                    c.line(x + size*1.2, act_y + 8*mm, x + size*1.4, act_y + 6*mm)
                    
            elif 'motor' in actuator or 'electric' in actuator:
                # Electric motor actuator
                act_y = y + size*0.7 + size*0.6
                c.setLineWidth(0.5*mm)
                # Motor symbol (circle with M)
                c.circle(x, act_y, size*0.5)
                c.setFont("Helvetica-Bold", 3*mm)
                c.drawCentredString(x, act_y - 1.5*mm, "M")
                # Actuator stem
                c.line(x, y + size*0.7, x, act_y - size*0.5)
                c.setLineWidth(0.7*mm)
                
        elif 'safety' in valve_type or 'relief' in valve_type or 'psv' in valve_type:
            # Safety/Relief valve: Triangle with spring
            path = c.beginPath()
            path.moveTo(x - size*0.8, y - size*0.6)
            path.lineTo(x - size*0.8, y + size*0.6)
            path.lineTo(x + size*0.6, y)
            path.close()
            c.drawPath(path, fill=0, stroke=1)
            
            # Spring symbol on top
            c.setLineWidth(0.35*mm)
            spring_y = y + size*0.6
            c.line(x, y, x, spring_y)
            # Spring coils
            for i in range(4):
                coil_y = spring_y + i * size*0.15
                c.line(x - size*0.2, coil_y, x + size*0.2, coil_y + size*0.075)
                c.line(x + size*0.2, coil_y + size*0.075, x - size*0.2, coil_y + size*0.15)
            c.setLineWidth(0.7*mm)
            
            # Set pressure
            set_pressure = valve.get('set_pressure', '10 barg')
            c.setFont("Helvetica", 2*mm)
            c.drawString(x + size*0.8, y, f"Set: {set_pressure}")
            
        else:
            # Manual isolation valve: X symbol (default)
            c.line(x - size*0.6, y - size*0.6, x + size*0.6, y + size*0.6)
            c.line(x - size*0.6, y + size*0.6, x + size*0.6, y - size*0.6)
            # Handwheel
            c.setLineWidth(0.35*mm)
            c.circle(x, y + size*0.9, size*0.3)
            c.setLineWidth(0.7*mm)
        
        # Valve tag in white box
        c.setFillColor(colors.white)
        tag_box_width = len(valve_tag) * 2*mm + 4*mm
        c.rect(x - tag_box_width/2, y - size*1.2 - 6*mm, tag_box_width, 6*mm, fill=1, stroke=1)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawCentredString(x, y - size*1.2 - 4*mm, valve_tag)
    
    def _draw_legend(self, c: canvas.Canvas):
        """Draw comprehensive professional symbol legend"""
        # Dynamic positioning using soft-coded values
        legend_x = self.table_grid['col_1_x']
        legend_y_base = self.table_grid['row_2_y']  # Base position
        legend_width = self.table_grid['standard_width_small']
        legend_height = self.table_grid['table_heights']['legend']
        
        # Legend box - proper bottom-up positioning
        c.setLineWidth(0.35*mm)
        c.rect(legend_x, legend_y_base, legend_width, legend_height)
        
        # Title - positioned from top of box
        c.setFont("Helvetica-Bold", 4*mm)
        c.drawString(legend_x + 5*mm, legend_y_base + legend_height - 6*mm, "LEGEND - SYMBOLS AND ABBREVIATIONS")
        
        # Header line below title
        c.setLineWidth(0.25*mm)
        header_y = legend_y_base + legend_height - 10*mm
        c.line(legend_x, header_y, legend_x + legend_width, header_y)
        
        # Column 1: Line types
        col1_x = legend_x + 5*mm
        y = header_y - 6*mm
        
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawString(col1_x, y, "LINE TYPES:")
        y -= 5*mm
        
        c.setFont("Helvetica", 2*mm)
        c.setLineWidth(0.5*mm)
        c.line(col1_x, y, col1_x + 15*mm, y)
        c.drawString(col1_x + 17*mm, y - 1*mm, "Process Line")
        y -= 5*mm
        
        c.setLineWidth(0.25*mm)
        c.setDash([2*mm, 1*mm], 0)
        c.line(col1_x, y, col1_x + 15*mm, y)
        c.setDash([], 0)
        c.drawString(col1_x + 17*mm, y - 1*mm, "Instrument Signal")
        y -= 5*mm
        
        c.setLineWidth(0.35*mm)
        c.line(col1_x, y, col1_x + 7*mm, y)
        c.line(col1_x + 8*mm, y, col1_x + 15*mm, y)
        c.drawString(col1_x + 17*mm, y - 1*mm, "Future/Spare")
        
        # Column 2: Equipment symbols
        col2_x = legend_x + 65*mm
        y = header_y - 6*mm
        
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawString(col2_x, y, "EQUIPMENT:")
        y -= 5*mm
        
        c.setFont("Helvetica", 2*mm)
        c.setLineWidth(0.35*mm)
        c.rect(col2_x, y - 3*mm, 8*mm, 12*mm)
        c.drawString(col2_x + 10*mm, y, "Vessel/Column")
        y -= 6*mm
        
        c.circle(col2_x + 4*mm, y + 1*mm, 3*mm)
        c.drawString(col2_x + 10*mm, y, "Pump")
        y -= 6*mm
        
        c.rect(col2_x, y - 3*mm, 10*mm, 8*mm)
        c.line(col2_x, y + 5*mm, col2_x + 5*mm, y + 9*mm)
        c.line(col2_x + 10*mm, y + 5*mm, col2_x + 5*mm, y + 9*mm)
        c.drawString(col2_x + 12*mm, y, "Tank")
        
        # Column 3: Abbreviations
        col3_x = legend_x + 125*mm
        y = header_y - 6*mm
        
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawString(col3_x, y, "ABBREVIATIONS:")
        y -= 5*mm
        
        c.setFont("Helvetica", 2*mm)
        abbrev = [
            ("CS", "Carbon Steel"),
            ("SS", "Stainless Steel"),
            ("FCV", "Flow Control Valve"),
            ("PT", "Pressure Transmitter"),
            ("LT", "Level Transmitter"),
            ("NTS", "Not To Scale")
        ]
        
        # Draw abbreviations with boundary check
        for short, full in abbrev:
            if y > legend_y_base + 3*mm:  # Ensure content stays inside box
                c.drawString(col3_x, y, f"{short} - {full}")
                y -= 4*mm
    
    def _draw_equipment_schedule(self, c: canvas.Canvas):
        """Draw comprehensive equipment schedule table"""
        # STRICT ALIGNMENT: Column 2, Row 3 (middle-left, upper position)
        table_x = self.table_grid['col_2_x']
        table_y = self.table_grid['row_3_y']
        table_width = self.table_grid['standard_width_small']
        table_height = self.table_grid['standard_height_medium']
        
        # Table border
        c.setLineWidth(0.35*mm)
        c.rect(table_x, table_y - 60*mm, table_width, table_height)
        
        # Title
        c.setFont("Helvetica-Bold", 4*mm)
        c.drawString(table_x + 5*mm, table_y, "EQUIPMENT SCHEDULE")
        
        # Table header
        c.setLineWidth(0.25*mm)
        header_y = table_y - 8*mm
        c.line(table_x, header_y, table_x + table_width, header_y)
        
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawString(table_x + 3*mm, header_y - 5*mm, "TAG")
        c.drawString(table_x + 35*mm, header_y - 5*mm, "EQUIPMENT NAME")
        c.drawString(table_x + 120*mm, header_y - 5*mm, "TYPE")
        c.drawString(table_x + 160*mm, header_y - 5*mm, "SIZE/DUTY")
        
        # Vertical lines
        c.line(table_x + 32*mm, header_y, table_x + 32*mm, table_y - 60*mm)
        c.line(table_x + 115*mm, header_y, table_x + 115*mm, table_y - 60*mm)
        c.line(table_x + 155*mm, header_y, table_x + 155*mm, table_y - 60*mm)
        
        # Equipment rows
        equipment = self.specs.get('equipment', [])
        row_y = header_y - 10*mm
        
        c.setFont("Helvetica", 2*mm)
        for idx, eq in enumerate(equipment[:7]):  # Max 7 items
            tag = eq.get('tag', 'N/A')
            name = eq.get('name') or eq.get('description', 'N/A')
            eq_type = eq.get('type', 'N/A').title()
            size = eq.get('size', '-')
            
            c.drawString(table_x + 3*mm, row_y, tag)
            c.drawString(table_x + 35*mm, row_y, name[:40])
            c.drawString(table_x + 120*mm, row_y, eq_type)
            c.drawString(table_x + 160*mm, row_y, str(size))
            
            row_y -= 6*mm
            c.line(table_x, row_y + 2*mm, table_x + table_width, row_y + 2*mm)
    
    def _draw_valve_schedule(self, c: canvas.Canvas):
        """Draw valve schedule table"""
        # STRICT ALIGNMENT: Column 3, Row 3 (middle-right, aligned with equipment schedule)
        table_x = self.table_grid['col_3_x']
        table_y = self.table_grid['row_3_y']
        table_width = self.table_grid['standard_width_medium']
        table_height = self.table_grid['standard_height_medium']
        
        valves = self.specs.get('valves', [])
        if not valves:
            return
        
        # Table border (aligned to standard height)
        c.setLineWidth(0.35*mm)
        c.rect(table_x, table_y - 60*mm, table_width, table_height)
        
        # Title
        c.setFont("Helvetica-Bold", 4*mm)
        c.drawString(table_x + 5*mm, table_y, "VALVE SCHEDULE")
        
        # Header
        header_y = table_y - 8*mm
        c.setLineWidth(0.25*mm)
        c.line(table_x, header_y, table_x + table_width, header_y)
        
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawString(table_x + 3*mm, header_y - 5*mm, "TAG")
        c.drawString(table_x + 35*mm, header_y - 5*mm, "TYPE")
        c.drawString(table_x + 85*mm, header_y - 5*mm, "SIZE")
        c.drawString(table_x + 110*mm, header_y - 5*mm, "ACTUATOR")
        c.drawString(table_x + 145*mm, header_y - 5*mm, "FAIL ACTION")
        
        # Vertical lines
        c.line(table_x + 32*mm, header_y, table_x + 32*mm, table_y - 40*mm)
        c.line(table_x + 80*mm, header_y, table_x + 80*mm, table_y - 40*mm)
        c.line(table_x + 105*mm, header_y, table_x + 105*mm, table_y - 40*mm)
        c.line(table_x + 140*mm, header_y, table_x + 140*mm, table_y - 40*mm)
        
        # Valve rows
        row_y = header_y - 10*mm
        c.setFont("Helvetica", 2*mm)
        
        for valve in valves[:4]:  # Max 4 valves
            tag = valve.get('tag', 'N/A')
            v_type = valve.get('type', 'N/A').replace('_', ' ').title()
            size = valve.get('size', '-')
            actuator = valve.get('actuator', 'Manual').title()
            fail = valve.get('fail_position', '-').replace('_', ' ').upper()
            
            c.drawString(table_x + 3*mm, row_y, tag)
            c.drawString(table_x + 35*mm, row_y, v_type[:20])
            c.drawString(table_x + 85*mm, row_y, str(size))
            c.drawString(table_x + 110*mm, row_y, actuator[:15])
            c.drawString(table_x + 145*mm, row_y, fail)
            
            row_y -= 6*mm
            c.line(table_x, row_y + 2*mm, table_x + table_width, row_y + 2*mm)
    
    def _draw_general_notes(self, c: canvas.Canvas):
        """Draw general notes section"""
        # STRICT ALIGNMENT: Column 1, Row 1 (bottom-left, below legend)
        notes_x = self.table_grid['col_1_x']
        notes_y_base = self.table_grid['row_1_y']
        notes_width = self.table_grid['standard_width_small']
        notes_height = 40*mm  # Increased height for proper fit
        
        # Notes box - draw from base position
        c.setLineWidth(0.35*mm)
        c.rect(notes_x, notes_y_base, notes_width, notes_height)
        
        # Title - positioned from top of box
        c.setFont("Helvetica-Bold", 4*mm)
        c.drawString(notes_x + 5*mm, notes_y_base + notes_height - 6*mm, "GENERAL NOTES")
        
        # Header line below title
        c.setLineWidth(0.25*mm)
        header_line_y = notes_y_base + notes_height - 10*mm
        c.line(notes_x, header_line_y, notes_x + notes_width, header_line_y)
        
        # Notes content - with proper spacing inside box
        c.setFont("Helvetica", 1.8*mm)  # Slightly smaller font for better fit
        notes = [
            "• DRAWING TYPE: Schematic/Elevation View (Process Flow)",
            "1. All dimensions in millimeters unless otherwise noted.",
            "2. All elevations relative to plant datum.",
            "3. Pipe specifications per project piping class.",
            "4. All instruments per ISA 5.1 standards.",
            "5. Valve actuation: FC=Fail Close, FO=Fail Open.",
            "6. This drawing is AI-generated and requires review.",
        ]
        
        # Start notes content below header line with proper spacing
        y = header_line_y - 5*mm
        for note in notes:
            if y > notes_y_base + 3*mm:  # Ensure content stays inside box
                c.drawString(notes_x + 3*mm, y, note)
                y -= 4.5*mm
    
    def _draw_instrument_index(self, c: canvas.Canvas):
        """Draw comprehensive instrument index table"""
        # Dynamic positioning using soft-coded values
        table_x = self.table_grid['col_2_x']
        table_y_base = self.table_grid['row_1_y']  # Base position
        table_width = self.table_grid['standard_width_small']
        table_height = self.table_grid['table_heights']['instrument_index']
        
        instruments = self.specs.get('instruments', [])
        if not instruments:
            return
        
        # Table border - proper bottom-up positioning
        c.setLineWidth(0.35*mm)
        c.rect(table_x, table_y_base, table_width, table_height)
        
        # Title - positioned from top of box
        c.setFont("Helvetica-Bold", 4*mm)
        c.drawString(table_x + 5*mm, table_y_base + table_height - 6*mm, "INSTRUMENT INDEX")
        
        # Header line below title
        header_y = table_y_base + table_height - 10*mm
        c.setLineWidth(0.25*mm)
        c.line(table_x, header_y, table_x + table_width, header_y)
        
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawString(table_x + 3*mm, header_y - 5*mm, "TAG")
        c.drawString(table_x + 35*mm, header_y - 5*mm, "SERVICE")
        c.drawString(table_x + 110*mm, header_y - 5*mm, "RANGE")
        c.drawString(table_x + 160*mm, header_y - 5*mm, "TYPE")
        
        # Vertical lines
        c.line(table_x + 32*mm, header_y, table_x + 32*mm, table_y_base)
        c.line(table_x + 105*mm, header_y, table_x + 105*mm, table_y_base)
        c.line(table_x + 155*mm, header_y, table_x + 155*mm, table_y_base)
        
        # Instrument rows - work downward from header
        row_y = header_y - 8*mm
        c.setFont("Helvetica", 2*mm)
        
        for inst in instruments[:5]:  # Max 5 instruments
            if row_y > table_y_base + 3*mm:  # Ensure content stays inside box
                tag = inst.get('tag', 'N/A')
                service = inst.get('service', inst.get('description', 'Process'))[:35]
                inst_range = inst.get('range', '-')
                inst_type = inst.get('type', 'Transmitter').replace('_', ' ').title()[:20]
                
                c.drawString(table_x + 3*mm, row_y, tag)
                c.drawString(table_x + 35*mm, row_y, service)
                c.drawString(table_x + 110*mm, row_y, str(inst_range))
                c.drawString(table_x + 160*mm, row_y, inst_type)
                
                row_y -= 6*mm
                if row_y > table_y_base + 2*mm:
                    c.line(table_x, row_y + 2*mm, table_x + table_width, row_y + 2*mm)
    
    def _draw_line_list(self, c: canvas.Canvas):
        """Draw line list table with specifications"""
        # Dynamic positioning using soft-coded values
        table_x = self.table_grid['col_3_x']
        table_y_base = self.table_grid['row_1_y']  # Base position
        table_width = self.table_grid['standard_width_medium']
        table_height = self.table_grid['table_heights']['line_list']
        
        streams = self.specs.get('process_streams', self.specs.get('piping', []))
        if not streams:
            return
        
        # Table border - proper bottom-up positioning
        c.setLineWidth(0.35*mm)
        c.rect(table_x, table_y_base, table_width, table_height)
        
        # Title - positioned from top of box
        c.setFont("Helvetica-Bold", 4*mm)
        c.drawString(table_x + 5*mm, table_y_base + table_height - 6*mm, "LINE LIST")
        
        # Header line below title
        header_y = table_y_base + table_height - 10*mm
        c.setLineWidth(0.25*mm)
        c.line(table_x, header_y, table_x + table_width, header_y)
        
        c.setFont("Helvetica-Bold", 2.5*mm)
        c.drawString(table_x + 3*mm, header_y - 5*mm, "LINE NO")
        c.drawString(table_x + 35*mm, header_y - 5*mm, "SIZE")
        c.drawString(table_x + 60*mm, header_y - 5*mm, "SPEC")
        c.drawString(table_x + 100*mm, header_y - 5*mm, "FROM/TO")
        
        # Vertical lines
        c.line(table_x + 32*mm, header_y, table_x + 32*mm, table_y_base)
        c.line(table_x + 55*mm, header_y, table_x + 55*mm, table_y_base)
        c.line(table_x + 95*mm, header_y, table_x + 95*mm, table_y_base)
        
        # Line rows - work downward from header
        row_y = header_y - 8*mm
        c.setFont("Helvetica", 2*mm)
        
        for i, stream in enumerate(streams[:5]):  # Max 5 lines
            if row_y > table_y_base + 3*mm:  # Ensure content stays inside box
                line_no = f"L-{i+1}"
                size = stream.get('line_size', '6 inch')
                spec = f"{size[:1]}\"-CS-150#"
                from_to = f"{stream.get('from', '')[:8]}/{stream.get('to', '')[:8]}"
                
                c.drawString(table_x + 3*mm, row_y, line_no)
                c.drawString(table_x + 35*mm, row_y, size[:8])
                c.drawString(table_x + 60*mm, row_y, spec)
                c.drawString(table_x + 100*mm, row_y, from_to)
                
                row_y -= 6*mm
                if row_y > table_y_base + 2*mm:
                    c.line(table_x, row_y + 2*mm, table_x + table_width, row_y + 2*mm)
    
    def _draw_north_arrow(self, c: canvas.Canvas):
        """Draw north arrow orientation indicator"""
        # Position in upper right corner
        arrow_x = self.page_width - self.margin - 40*mm
        arrow_y = self.page_height - self.margin - 40*mm
        
        # Circle
        c.setLineWidth(0.35*mm)
        c.circle(arrow_x, arrow_y, 15*mm, stroke=1, fill=0)
        
        # Arrow pointing up (North)
        c.setFillColor(colors.black)
        c.setLineWidth(0.5*mm)
        
        # Arrow shaft
        c.line(arrow_x, arrow_y - 10*mm, arrow_x, arrow_y + 10*mm)
        
        # Arrow head
        c.setLineWidth(0.7*mm)
        c.line(arrow_x, arrow_y + 10*mm, arrow_x - 3*mm, arrow_y + 5*mm)
        c.line(arrow_x, arrow_y + 10*mm, arrow_x + 3*mm, arrow_y + 5*mm)
        
        # Label
        c.setFont("Helvetica-Bold", 4*mm)
        c.drawCentredString(arrow_x, arrow_y - 17*mm, "N")


def generate_graph_based_pid(drawing_specs: Dict, output_path: str) -> str:
    """
    Convenience function for graph-based P&ID generation with professional data enrichment
    
    Args:
        drawing_specs: Drawing specifications dictionary with:
            - equipment: List of equipment dicts
            - process_streams or piping: List of stream dicts
            - instruments: List of instrument dicts
            - valves: List of valve dicts
            - drawing_number, drawing_title, etc.
        output_path: Output PDF path
        
    Returns:
        Path to generated PDF
    """
    logger.info("🔧 Normalizing input data for graph-based generation...")
    
    # Normalize equipment data (description → name)
    equipment = drawing_specs.get('equipment', [])
    equipment_normalized = normalize_equipment_data(equipment)
    
    # Normalize stream data (source → from, destination → to)
    streams = drawing_specs.get('process_streams') or drawing_specs.get('piping', [])
    streams_normalized = normalize_stream_data(streams)
    
    # Validate connectivity
    streams_validated, warnings = validate_connectivity(equipment_normalized, streams_normalized)
    
    if warnings:
        logger.warning(f"⚠️  Connectivity warnings ({len(warnings)}):")
        for warning in warnings[:5]:  # Show first 5
            logger.warning(f"  - {warning}")
    
    logger.info(f"✅ Validated: {len(equipment_normalized)} equipment, {len(streams_validated)} streams")
    
    # Update specs with normalized data
    normalized_specs = dict(drawing_specs)
    normalized_specs['equipment'] = equipment_normalized
    normalized_specs['process_streams'] = streams_validated
    normalized_specs['piping'] = streams_validated  # Both keys for compatibility
    
    # ===== DATA ENRICHMENT FOR PROFESSIONAL P&ID =====
    logger.info("🎨 Enriching data with professional engineering defaults...")
    try:
        from .data_enrichment import enrich_all_data
        enriched_specs = enrich_all_data(normalized_specs)
        logger.info("✅ Data enriched with operating conditions, elevations, and defaults")
    except Exception as e:
        logger.warning(f"⚠️  Data enrichment failed: {e}, continuing with normalized data")
        enriched_specs = normalized_specs
    
    # Generate P&ID with enriched data
    generator = GraphBasedPIDGenerator(enriched_specs)
    return generator.generate(output_path)
