"""
Programmatic P&ID Drawing Generator
Professional CAD-style P&ID generation using ReportLab
Based on ROBOFLOW legend standards and ISA 5.1 specifications

This module generates real technical drawings (not AI images) with:
- Precise line weights (0.7mm equipment, 0.5mm process, 0.25mm instruments)
- ISA 5.1 compliant instrument symbols
- Professional text sizing and layout
- A1 landscape format with proper title blocks
- Soft-coded configuration for easy customization
"""

from reportlab.lib.pagesizes import A1, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors
import logging
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import os

# Import soft-coded configuration
from .config.pid_output_config import get_config, merge_config

logger = logging.getLogger(__name__)


class ProgrammaticPIDGenerator:
    """
    Professional P&ID Drawing Generator using ReportLab
    Creates CAD-quality technical drawings programmatically
    Uses soft-coded configuration for easy customization
    """
    
    def __init__(self, drawing_specs: Dict, config_name: str = 'default', config_overrides: Dict = None):
        """
        Initialize generator with drawing specifications and configuration
        
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
            config_name: Name of configuration to use ('default', 'rejlers', 'a0')
            config_overrides: Optional dictionary to override specific config values
        """
        self.specs = drawing_specs
        
        # Load configuration (soft-coded)
        base_config = get_config(config_name)
        if config_overrides:
            self.config = merge_config(base_config, config_overrides)
        else:
            self.config = base_config
        
        # Page settings from config
        self.page_width, self.page_height = self.config['page_size']
        
        # Margins from config
        margins = self.config['margins']
        self.margin = margins['left']  # Assuming uniform margins, or use individual
        self.drawing_width = self.page_width - margins['left'] - margins['right']
        self.drawing_height = self.page_height - margins['top'] - margins['bottom']
        
        # Line weights from config
        self.line_weights = self.config['line_weights']
        
        # Text sizes from config
        self.text_sizes = self.config['text_sizes']
        
        # Symbol sizes from config
        self.symbol_sizes = self.config['symbol_sizes']
        
        # Colors from config
        self.color_black = self.config['colors']['primary']
        
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
        """Draw main border and title block using soft-coded configuration"""
        c.setStrokeColor(self.color_black)
        c.setLineWidth(self.line_weights['border'])
        
        # Main border
        c.rect(self.margin, self.margin, self.drawing_width, self.drawing_height)
        
        # Title block configuration
        title_block_config = self.config['title_block']
        
        if not title_block_config['enabled']:
            return
        
        # Calculate title block position
        tb_width = title_block_config['width']
        tb_height = title_block_config['height']
        
        if title_block_config['position'] == 'bottom-right':
            title_block_x = self.page_width - self.margin - tb_width
            title_block_y = self.margin
        elif title_block_config['position'] == 'bottom-left':
            title_block_x = self.margin
            title_block_y = self.margin
        elif title_block_config['position'] == 'top-right':
            title_block_x = self.page_width - self.margin - tb_width
            title_block_y = self.page_height - self.margin - tb_height
        else:  # top-left
            title_block_x = self.margin
            title_block_y = self.page_height - self.margin - tb_height
        
        # Draw title block border
        c.setLineWidth(title_block_config['border_width'])
        c.rect(title_block_x, title_block_y, tb_width, tb_height)
        
        # Draw divider lines
        for divider in title_block_config.get('dividers', []):
            y_pos = title_block_y + divider['y_position']
            c.line(title_block_x, y_pos, title_block_x + tb_width, y_pos)
        
        # Draw title block fields
        for field in title_block_config['fields']:
            field_name = field['name']
            font = field['font']
            font_size = field['font_size']
            y_position = title_block_y + field['y_position']
            x_position = title_block_x + field['x_offset']
            formatter = field.get('formatter', lambda x: str(x) if x else '')
            
            # Get value from specs
            value = self.specs.get(field_name, None)
            
            # Format the value
            formatted_value = formatter(value)
            
            # Draw text
            c.setFont(font, font_size)
            c.drawString(x_position, y_position, formatted_value)
    
    def _draw_legend(self, c: canvas.Canvas):
        """Draw symbol legend using soft-coded configuration"""
        legend_config = self.config['legend']
        
        if not legend_config['enabled']:
            return
        
        # Calculate legend position
        if legend_config['position'] == 'top-left':
            legend_x = self.margin + legend_config['x_offset']
            legend_y = self.page_height - self.margin - legend_config['y_offset']
        elif legend_config['position'] == 'top-right':
            legend_x = self.page_width - self.margin - 100 * mm
            legend_y = self.page_height - self.margin - legend_config['y_offset']
        elif legend_config['position'] == 'bottom-left':
            legend_x = self.margin + legend_config['x_offset']
            legend_y = self.margin + legend_config['y_offset']
        else:  # bottom-right
            legend_x = self.page_width - self.margin - 100 * mm
            legend_y = self.margin + legend_config['y_offset']
        
        # Draw legend title
        c.setFont(legend_config['title_font'], legend_config['title_size'])
        c.drawString(legend_x, legend_y, legend_config['title'])
        
        # Draw legend items
        c.setFont(legend_config['item_font'], legend_config['item_size'])
        y_offset = legend_y - legend_config['line_spacing']
        
        for item in legend_config['items']:
            symbol = item.get('symbol', '')
            description = item.get('description', '')
            text = f"{symbol}  {description}"
            c.drawString(legend_x, y_offset, text)
            y_offset -= legend_config['line_spacing']
    
    def _draw_notes(self, c: canvas.Canvas):
        """Draw general notes using soft-coded configuration"""
        notes_config = self.config['notes']
        
        if not notes_config['enabled']:
            return
        
        # Calculate notes position
        if notes_config['position'] == 'top-left':
            notes_x = self.margin + notes_config['x_offset']
            notes_y = self.page_height - self.margin - notes_config['y_offset']
        elif notes_config['position'] == 'top-right':
            notes_x = self.page_width - self.margin - 200 * mm
            notes_y = self.page_height - self.margin - notes_config['y_offset']
        elif notes_config['position'] == 'bottom-left':
            notes_x = self.margin + notes_config['x_offset']
            notes_y = self.margin + notes_config['y_offset']
        else:  # bottom-right
            notes_x = self.page_width - self.margin - 200 * mm
            notes_y = self.margin + notes_config['y_offset']
        
        # Draw notes title
        c.setFont(notes_config['title_font'], notes_config['title_size'])
        c.drawString(notes_x, notes_y, notes_config['title'])
        
        # Draw note items
        c.setFont(notes_config['item_font'], notes_config['item_size'])
        y_offset = notes_y - notes_config['line_spacing']
        
        for note in notes_config['items']:
            c.drawString(notes_x, y_offset, note)
            y_offset -= notes_config['line_spacing']
    
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


def generate_pid_from_specs(drawing_specs: Dict, output_path: str, config_name: str = 'default', config_overrides: Dict = None) -> str:
    """
    Convenience function to generate P&ID from specifications with soft-coded configuration
    
    Args:
        drawing_specs: Dictionary with drawing specifications
        output_path: Where to save the PDF
        config_name: Name of configuration to use ('default', 'rejlers', 'a0')
        config_overrides: Optional dictionary to override specific config values
        
    Returns:
        str: Path to generated PDF
        
    Example:
        # Use default configuration
        generate_pid_from_specs(specs, "output.pdf")
        
        # Use Rejlers configuration
        generate_pid_from_specs(specs, "output.pdf", config_name='rejlers')
        
        # Use default with custom overrides
        overrides = {'title_block': {'width': 250 * mm}}
        generate_pid_from_specs(specs, "output.pdf", config_overrides=overrides)
    """
    generator = ProgrammaticPIDGenerator(drawing_specs, config_name, config_overrides)
    return generator.generate(output_path)
