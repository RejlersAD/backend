"""
Programmatic P&ID Drawing Generator
Professional CAD-style P&ID generation using ReportLab
Based on ROBOFLOW legend standards and ISA 5.1 specifications

This module generates real technical drawings (not AI images) with:
- Precise line weights (0.7mm equipment, 0.5mm process, 0.25mm instruments)
- ISA 5.1 compliant instrument symbols
- Professional text sizing and layout
- A1 landscape format with proper title blocks
"""

from reportlab.lib.pagesizes import A1, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors
import logging
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import os

logger = logging.getLogger(__name__)


class ProgrammaticPIDGenerator:
    """
    Professional P&ID Drawing Generator using ReportLab
    Creates CAD-quality technical drawings programmatically
    """
    
    def __init__(self, drawing_specs: Dict):
        """
        Initialize generator with drawing specifications
        
        Args:
            drawing_specs: Dictionary containing:
                - drawing_number: P&ID drawing number
                - drawing_title: Title of the drawing
                - project_name: Project name
                - project_code: Project code
                - revision: Drawing revision
                - equipment: List of equipment items
                - piping: List of piping connections
                - instrumentation: List of instruments
                - valves: List of valves
        """
        self.specs = drawing_specs
        
        # A1 landscape dimensions (841mm x 594mm)
        self.page_width, self.page_height = landscape(A1)
        
        # Drawing margins
        self.margin = 20 * mm
        self.drawing_width = self.page_width - 2 * self.margin
        self.drawing_height = self.page_height - 2 * self.margin
        
        # Line weights (ISO/ISA standards)
        self.line_weights = {
            'border': 1.0,      # Border and title block
            'equipment': 0.7,   # Equipment outlines
            'process': 0.5,     # Process lines
            'instrument': 0.25, # Instrument signals
            'grid': 0.1         # Grid lines (optional)
        }
        
        # Text sizes (in mm)
        self.text_sizes = {
            'title': 6,           # Drawing title
            'equipment_tag': 5,   # Equipment tags (V-3601)
            'equipment_name': 3,  # Equipment names
            'line_number': 3,     # Line numbers
            'instrument': 2.5,    # Instrument tags
            'notes': 2.5          # General notes
        }
        
        # Symbol sizes
        self.symbol_sizes = {
            'instrument_circle': 15 * mm,  # ISA instrument circle diameter
            'valve_width': 8 * mm,          # Valve symbol width
            'valve_height': 8 * mm          # Valve symbol height
        }
        
        # Colors (all black for technical drawings)
        self.color_black = colors.black
        
    def generate(self, output_path: str) -> str:
        """
        Generate the P&ID drawing and save to PDF
        
        Args:
            output_path: Full path where PDF should be saved
            
        Returns:
            str: Path to generated PDF file
        """
        logger.info(f"🎨 Generating programmatic P&ID: {output_path}")
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Create canvas
        c = canvas.Canvas(output_path, pagesize=landscape(A1))
        
        try:
            # 1. Draw border and title block
            logger.info("  → Drawing border and title block")
            self._draw_border_and_title_block(c)
            
            # 2. Draw legend and notes
            logger.info("  → Drawing legend and notes")
            self._draw_legend(c)
            self._draw_notes(c)
            
            # 3. Draw equipment
            logger.info("  → Drawing equipment")
            equipment_positions = self._draw_equipment(c)
            
            # 4. Draw piping connections
            logger.info("  → Drawing piping")
            self._draw_piping(c, equipment_positions)
            
            # 5. Draw valves
            logger.info("  → Drawing valves")
            valve_positions = self._draw_valves(c)
            
            # 6. Draw instrumentation
            logger.info("  → Drawing instrumentation")
            self._draw_instrumentation(c, equipment_positions, valve_positions)
            
            # 7. Save PDF
            logger.info("  → Saving PDF")
            c.save()
            
            logger.info(f"✅ P&ID generated successfully: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"❌ Failed to generate P&ID: {str(e)}")
            raise
    
    def _draw_border_and_title_block(self, c: canvas.Canvas):
        """Draw main border and title block"""
        c.setStrokeColor(self.color_black)
        c.setLineWidth(self.line_weights['border'])
        
        # Main border
        c.rect(self.margin, self.margin, self.drawing_width, self.drawing_height)
        
        # Title block (200mm x 100mm in bottom-right corner)
        title_block_width = 200 * mm
        title_block_height = 100 * mm
        title_block_x = self.page_width - self.margin - title_block_width
        title_block_y = self.margin
        
        c.rect(title_block_x, title_block_y, title_block_width, title_block_height)
        
        # Dividers in title block
        c.line(title_block_x, title_block_y + 60*mm, 
               title_block_x + title_block_width, title_block_y + 60*mm)
        c.line(title_block_x, title_block_y + 40*mm, 
               title_block_x + title_block_width, title_block_y + 40*mm)
        c.line(title_block_x, title_block_y + 20*mm, 
               title_block_x + title_block_width, title_block_y + 20*mm)
        
        # Title block text
        c.setFont("Helvetica-Bold", self.text_sizes['title'] * mm)
        
        # Drawing title
        title = self.specs.get('drawing_title', 'P&ID Drawing')
        c.drawString(title_block_x + 5*mm, title_block_y + 75*mm, title)
        
        # Project info
        c.setFont("Helvetica", self.text_sizes['equipment_name'] * mm)
        project = self.specs.get('project_name', 'Project')
        c.drawString(title_block_x + 5*mm, title_block_y + 50*mm, f"Project: {project}")
        
        # Drawing number
        c.setFont("Helvetica-Bold", self.text_sizes['equipment_tag'] * mm)
        drawing_num = self.specs.get('drawing_number', 'PID-001')
        c.drawString(title_block_x + 5*mm, title_block_y + 28*mm, f"Drawing No: {drawing_num}")
        
        # Revision
        revision = self.specs.get('revision', 'A')
        c.drawString(title_block_x + 5*mm, title_block_y + 8*mm, f"Rev: {revision}")
        
        # Date
        date_str = datetime.now().strftime('%Y-%m-%d')
        c.drawString(title_block_x + 100*mm, title_block_y + 8*mm, f"Date: {date_str}")
    
    def _draw_legend(self, c: canvas.Canvas):
        """Draw symbol legend in top-left corner"""
        legend_x = self.margin + 10*mm
        legend_y = self.page_height - self.margin - 30*mm
        
        c.setFont("Helvetica-Bold", self.text_sizes['equipment_name'] * mm)
        c.drawString(legend_x, legend_y, "LEGEND")
        
        c.setFont("Helvetica", self.text_sizes['notes'] * mm)
        y_offset = legend_y - 8*mm
        
        legend_items = [
            "━━━  Process Line",
            "- - -  Instrument Signal",
            "⬡   Gate Valve",
            "◇   Control Valve",
            "○   Instrument (Field)",
            "◯   Instrument (Panel)"
        ]
        
        for item in legend_items:
            c.drawString(legend_x, y_offset, item)
            y_offset -= 6*mm
    
    def _draw_notes(self, c: canvas.Canvas):
        """Draw general notes in bottom-left corner"""
        notes_x = self.margin + 10*mm
        notes_y = self.margin + 60*mm
        
        c.setFont("Helvetica-Bold", self.text_sizes['equipment_name'] * mm)
        c.drawString(notes_x, notes_y, "GENERAL NOTES")
        
        c.setFont("Helvetica", self.text_sizes['notes'] * mm)
        y_offset = notes_y - 8*mm
        
        notes = [
            "1. All dimensions in millimeters unless noted",
            "2. All instruments per ISA 5.1 standard",
            "3. Line numbers indicate: Size-Fluid-Spec-Line Number",
            "4. Equipment tags per project standards"
        ]
        
        for note in notes:
            c.drawString(notes_x, y_offset, note)
            y_offset -= 6*mm
    
    def _draw_equipment(self, c: canvas.Canvas) -> Dict[str, Tuple[float, float]]:
        """
        Draw equipment items and return their positions
        
        Returns:
            Dict mapping equipment tag to (x, y) center position
        """
        positions = {}
        equipment_list = self.specs.get('equipment', [])
        
        if not equipment_list:
            return positions
        
        c.setStrokeColor(self.color_black)
        c.setLineWidth(self.line_weights['equipment'])
        
        # Calculate starting position (center of drawing area)
        start_x = self.margin + self.drawing_width * 0.3
        start_y = self.margin + self.drawing_height * 0.5
        
        for idx, equipment in enumerate(equipment_list):
            # Position vessels vertically offset
            x = start_x
            y = start_y - (idx * 100*mm)
            
            equipment_type = equipment.get('type', 'vessel')
            tag = equipment.get('tag', f'V-{idx+1}')
            name = equipment.get('name', 'Vessel')
            
            if 'vessel' in equipment_type.lower() or 'column' in equipment_type.lower():
                # Draw vertical vessel
                width = 100 * mm
                height = 230 * mm
                self._draw_vessel(c, x, y, width, height, tag, name)
                positions[tag] = (x + width/2, y + height/2)
            
            elif 'pump' in equipment_type.lower():
                # Draw pump symbol
                size = 40 * mm
                self._draw_pump(c, x, y, size, tag, name)
                positions[tag] = (x + size/2, y + size/2)
            
            elif 'exchanger' in equipment_type.lower():
                # Draw heat exchanger
                width = 80 * mm
                height = 40 * mm
                self._draw_exchanger(c, x, y, width, height, tag, name)
                positions[tag] = (x + width/2, y + height/2)
        
        return positions
    
    def _draw_vessel(self, c: canvas.Canvas, x: float, y: float, 
                     width: float, height: float, tag: str, name: str):
        """Draw vertical vessel with nozzles"""
        # Vessel body (vertical cylinder)
        c.rect(x, y, width, height)
        
        # Demister pad (hatched rectangle)
        demister_y = y + height * 0.7
        demister_height = height * 0.1
        c.rect(x, demister_y, width, demister_height)
        
        # Hatching for demister
        c.setLineWidth(0.2)
        for i in range(10):
            hatch_y = demister_y + (i * demister_height / 10)
            c.line(x, hatch_y, x + width, hatch_y + demister_height/10)
        c.setLineWidth(self.line_weights['equipment'])
        
        # Nozzles
        nozzle_length = 15 * mm
        # Feed nozzle (top)
        c.line(x + width/2, y + height, x + width/2, y + height + nozzle_length)
        # Bottom nozzle
        c.line(x + width/2, y, x + width/2, y - nozzle_length)
        # Side nozzles
        c.line(x, y + height*0.8, x - nozzle_length, y + height*0.8)
        c.line(x + width, y + height*0.5, x + width + nozzle_length, y + height*0.5)
        
        # Equipment tag
        c.setFont("Helvetica-Bold", self.text_sizes['equipment_tag'] * mm)
        c.drawCentredString(x + width/2, y - 10*mm, tag)
        
        # Equipment name
        c.setFont("Helvetica", self.text_sizes['equipment_name'] * mm)
        c.drawCentredString(x + width/2, y - 16*mm, name)
    
    def _draw_pump(self, c: canvas.Canvas, x: float, y: float, 
                   size: float, tag: str, name: str):
        """Draw centrifugal pump symbol"""
        # Pump circle
        c.circle(x + size/2, y + size/2, size/2)
        
        # Suction nozzle (left)
        c.line(x - 10*mm, y + size/2, x, y + size/2)
        
        # Discharge nozzle (right)
        c.line(x + size, y + size/2, x + size + 10*mm, y + size/2)
        
        # Equipment tag
        c.setFont("Helvetica-Bold", self.text_sizes['equipment_tag'] * mm)
        c.drawCentredString(x + size/2, y - 10*mm, tag)
        
        # Equipment name
        c.setFont("Helvetica", self.text_sizes['equipment_name'] * mm)
        c.drawCentredString(x + size/2, y - 16*mm, name)
    
    def _draw_exchanger(self, c: canvas.Canvas, x: float, y: float,
                        width: float, height: float, tag: str, name: str):
        """Draw shell and tube heat exchanger"""
        # Shell (rectangle)
        c.rect(x, y, width, height)
        
        # Tube bundle (inner circle)
        c.circle(x + width/2, y + height/2, height/3)
        
        # Nozzles
        nozzle_length = 10 * mm
        # Shell side
        c.line(x, y + height*0.7, x - nozzle_length, y + height*0.7)
        c.line(x + width, y + height*0.3, x + width + nozzle_length, y + height*0.3)
        # Tube side
        c.line(x + width*0.2, y + height, x + width*0.2, y + height + nozzle_length)
        c.line(x + width*0.8, y, x + width*0.8, y - nozzle_length)
        
        # Equipment tag
        c.setFont("Helvetica-Bold", self.text_sizes['equipment_tag'] * mm)
        c.drawCentredString(x + width/2, y - 10*mm, tag)
        
        # Equipment name
        c.setFont("Helvetica", self.text_sizes['equipment_name'] * mm)
        c.drawCentredString(x + width/2, y - 16*mm, name)
    
    def _draw_piping(self, c: canvas.Canvas, equipment_positions: Dict[str, Tuple[float, float]]):
        """Draw process piping between equipment"""
        piping_list = self.specs.get('piping', [])
        
        if not piping_list:
            return
        
        c.setStrokeColor(self.color_black)
        c.setLineWidth(self.line_weights['process'])
        
        for pipe in piping_list:
            from_tag = pipe.get('from_equipment')
            to_tag = pipe.get('to_equipment')
            line_number = pipe.get('line_number', '')
            
            if from_tag in equipment_positions and to_tag in equipment_positions:
                x1, y1 = equipment_positions[from_tag]
                x2, y2 = equipment_positions[to_tag]
                
                # Draw horizontal routing (orthogonal)
                mid_x = (x1 + x2) / 2
                c.line(x1, y1, mid_x, y1)  # Horizontal from source
                c.line(mid_x, y1, mid_x, y2)  # Vertical
                c.line(mid_x, y2, x2, y2)  # Horizontal to target
                
                # Line number label
                if line_number:
                    c.setFont("Helvetica", self.text_sizes['line_number'] * mm)
                    c.drawString(mid_x + 2*mm, y1 + 2*mm, line_number)
    
    def _draw_valves(self, c: canvas.Canvas) -> Dict[str, Tuple[float, float]]:
        """
        Draw valves and return their positions
        
        Returns:
            Dict mapping valve tag to (x, y) position
        """
        positions = {}
        valve_list = self.specs.get('valves', [])
        
        if not valve_list:
            return positions
        
        c.setStrokeColor(self.color_black)
        c.setLineWidth(self.line_weights['process'])
        
        # Position valves along process lines
        start_x = self.margin + self.drawing_width * 0.5
        start_y = self.margin + self.drawing_height * 0.5
        
        for idx, valve in enumerate(valve_list):
            x = start_x + (idx * 40*mm)
            y = start_y
            
            valve_type = valve.get('type', 'gate')
            tag = valve.get('tag', f'V-{idx+1}')
            
            if 'gate' in valve_type.lower():
                self._draw_gate_valve(c, x, y, tag)
            elif 'control' in valve_type.lower():
                self._draw_control_valve(c, x, y, tag)
            elif 'check' in valve_type.lower():
                self._draw_check_valve(c, x, y, tag)
            elif 'safety' in valve_type.lower():
                self._draw_safety_valve(c, x, y, tag)
            
            positions[tag] = (x, y)
        
        return positions
    
    def _draw_gate_valve(self, c: canvas.Canvas, x: float, y: float, tag: str):
        """Draw gate valve symbol (two triangles)"""
        size = self.symbol_sizes['valve_width']
        
        # Two triangles forming diamond
        path = c.beginPath()
        path.moveTo(x - size/2, y)
        path.lineTo(x, y + size/2)
        path.lineTo(x + size/2, y)
        path.lineTo(x, y - size/2)
        path.close()
        c.drawPath(path)
        
        # Valve tag below
        c.setFont("Helvetica", self.text_sizes['instrument'] * mm)
        c.drawCentredString(x, y - 8*mm, tag)
    
    def _draw_control_valve(self, c: canvas.Canvas, x: float, y: float, tag: str):
        """Draw control valve symbol"""
        size = self.symbol_sizes['valve_width']
        
        # Diamond (globe valve body)
        path = c.beginPath()
        path.moveTo(x - size/2, y)
        path.lineTo(x, y + size/2)
        path.lineTo(x + size/2, y)
        path.lineTo(x, y - size/2)
        path.close()
        c.drawPath(path)
        
        # Actuator on top
        c.rect(x - size/4, y + size/2, size/2, size/2)
        
        # Valve tag below
        c.setFont("Helvetica", self.text_sizes['instrument'] * mm)
        c.drawCentredString(x, y - 8*mm, tag)
    
    def _draw_check_valve(self, c: canvas.Canvas, x: float, y: float, tag: str):
        """Draw check valve symbol"""
        size = self.symbol_sizes['valve_width']
        
        # Circle with arrow
        c.circle(x, y, size/2)
        
        # Arrow inside
        path = c.beginPath()
        path.moveTo(x - size/3, y)
        path.lineTo(x + size/3, y)
        path.lineTo(x + size/6, y + size/6)
        path.moveTo(x + size/3, y)
        path.lineTo(x + size/6, y - size/6)
        c.drawPath(path)
        
        # Valve tag below
        c.setFont("Helvetica", self.text_sizes['instrument'] * mm)
        c.drawCentredString(x, y - 8*mm, tag)
    
    def _draw_safety_valve(self, c: canvas.Canvas, x: float, y: float, tag: str):
        """Draw safety/relief valve symbol"""
        size = self.symbol_sizes['valve_width']
        
        # Triangle (valve body)
        path = c.beginPath()
        path.moveTo(x - size/2, y - size/2)
        path.lineTo(x + size/2, y - size/2)
        path.lineTo(x, y + size/2)
        path.close()
        c.drawPath(path)
        
        # Spring on top
        spring_height = size/2
        c.line(x, y + size/2, x, y + size/2 + spring_height)
        
        # Valve tag
        c.setFont("Helvetica", self.text_sizes['instrument'] * mm)
        c.drawCentredString(x, y - 10*mm, tag)
    
    def _draw_instrumentation(self, c: canvas.Canvas, 
                             equipment_positions: Dict[str, Tuple[float, float]],
                             valve_positions: Dict[str, Tuple[float, float]]):
        """Draw instruments with ISA 5.1 symbols"""
        instrument_list = self.specs.get('instrumentation', [])
        
        if not instrument_list:
            return
        
        c.setStrokeColor(self.color_black)
        c.setLineWidth(self.line_weights['instrument'])
        
        for instrument in instrument_list:
            tag = instrument.get('tag', 'PT-001')
            location = instrument.get('location', 'field')
            connected_to = instrument.get('connected_to', '')
            measurement_type = instrument.get('type', 'pressure')
            
            # Determine position based on connection
            if connected_to in equipment_positions:
                x_base, y_base = equipment_positions[connected_to]
                # Offset instruments to the side
                x = x_base + 80*mm
                y = y_base
            elif connected_to in valve_positions:
                x_base, y_base = valve_positions[connected_to]
                x = x_base
                y = y_base + 60*mm
            else:
                # Default position
                x = self.margin + self.drawing_width * 0.7
                y = self.margin + self.drawing_height * 0.5
            
            # Draw instrument circle
            circle_diameter = self.symbol_sizes['instrument_circle']
            
            if location.lower() == 'field':
                # Solid circle for field instruments
                c.circle(x, y, circle_diameter/2, fill=0)
            else:
                # Empty circle for panel/DCS instruments
                c.circle(x, y, circle_diameter/2, fill=0)
            
            # Instrument tag inside circle
            c.setFont("Helvetica-Bold", self.text_sizes['instrument'] * mm)
            c.drawCentredString(x, y - 2*mm, tag)
            
            # Signal line (dashed) from instrument to equipment
            if connected_to in equipment_positions:
                x_equip, y_equip = equipment_positions[connected_to]
                self._draw_dashed_line(c, x, y - circle_diameter/2, x_equip, y_equip)
            elif connected_to in valve_positions:
                x_valve, y_valve = valve_positions[connected_to]
                self._draw_dashed_line(c, x, y - circle_diameter/2, x_valve, y_valve)
    
    def _draw_dashed_line(self, c: canvas.Canvas, x1: float, y1: float, 
                          x2: float, y2: float, dash_length: float = 3*mm):
        """Draw dashed line for instrument signals"""
        c.setDash([dash_length, dash_length])
        c.line(x1, y1, x2, y2)
        c.setDash([])  # Reset to solid


def generate_pid_from_specs(drawing_specs: Dict, output_path: str) -> str:
    """
    Convenience function to generate P&ID from specifications
    
    Args:
        drawing_specs: Dictionary with drawing specifications
        output_path: Where to save the PDF
        
    Returns:
        str: Path to generated PDF
    """
    generator = ProgrammaticPIDGenerator(drawing_specs)
    return generator.generate(output_path)
