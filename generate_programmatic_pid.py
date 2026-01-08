"""
Programmatic Professional P&ID Generator
Creates real technical drawings with proper CAD-style symbols and line weights
"""
import os
import sys
from pathlib import Path
from reportlab.lib.pagesizes import A1, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

class ProfessionalPIDGenerator:
    """Generate professional P&ID drawings programmatically"""
    
    def __init__(self):
        # A1 landscape dimensions
        self.page_width, self.page_height = landscape(A1)
        
        # Line weights (convert mm to points: 1mm = 2.834645 points)
        self.line_weights = {
            'equipment': 0.7 * mm,      # 0.7mm for equipment
            'process': 0.5 * mm,         # 0.5mm for process lines
            'instrument': 0.25 * mm,     # 0.25mm for instrument signals
            'border': 1.0 * mm,          # 1mm for borders
        }
        
        # Text sizes
        self.text_sizes = {
            'equipment_tag': 5 * mm,     # 5mm for equipment tags
            'equipment_name': 3 * mm,    # 3mm for equipment names
            'line_number': 3 * mm,       # 3mm for line numbers
            'instrument': 2.5 * mm,      # 2.5mm for instruments
            'notes': 2.5 * mm,           # 2.5mm for notes
            'title': 4 * mm,             # 4mm for title block
        }
        
        # Margins
        self.margin = 20 * mm
        
        # Drawing area
        self.draw_width = self.page_width - (2 * self.margin)
        self.draw_height = self.page_height - (2 * self.margin)
        
        # Key positions
        self.origin_x = self.margin
        self.origin_y = self.margin
        
    def draw_border_and_title_block(self, c):
        """Draw border and title block"""
        c.setLineWidth(self.line_weights['border'])
        c.setStrokeColor(colors.black)
        
        # Main border
        c.rect(self.margin, self.margin, self.draw_width, self.draw_height)
        
        # Title block (bottom right, 200mm x 100mm)
        tb_width = 200 * mm
        tb_height = 100 * mm
        tb_x = self.page_width - self.margin - tb_width
        tb_y = self.margin
        
        c.setLineWidth(0.5 * mm)
        c.rect(tb_x, tb_y, tb_width, tb_height)
        
        # Title block content
        c.setFont("Helvetica-Bold", 4 * mm)
        c.drawString(tb_x + 5*mm, tb_y + tb_height - 10*mm, "PIPING & INSTRUMENTATION DIAGRAM")
        
        c.setFont("Helvetica", 3 * mm)
        c.drawString(tb_x + 5*mm, tb_y + tb_height - 20*mm, "EXPORT GAS KO DRUM")
        c.drawString(tb_x + 5*mm, tb_y + tb_height - 28*mm, "Drawing No: P16093-14-01-08-1602")
        c.drawString(tb_x + 5*mm, tb_y + tb_height - 36*mm, "Project: P16093 - SAHIL PLATFORM")
        c.drawString(tb_x + 5*mm, tb_y + tb_height - 44*mm, "Client: ADNOC")
        c.drawString(tb_x + 5*mm, tb_y + tb_height - 52*mm, "Contractor: REJLERS")
        c.drawString(tb_x + 5*mm, tb_y + tb_height - 60*mm, "Rev: A")
        c.drawString(tb_x + 5*mm, tb_y + tb_height - 68*mm, "Date: January 2026")
        
    def draw_vessel(self, c, x, y, width, height, tag, name):
        """Draw vertical pressure vessel"""
        c.setLineWidth(self.line_weights['equipment'])
        c.setStrokeColor(colors.black)
        c.setFillColor(colors.white)
        
        # Vessel body (rectangle with rounded ends)
        c.roundRect(x - width/2, y, width, height, 10*mm, fill=0)
        
        # Demister pad (hatched area at top)
        demister_y = y + height - 50*mm
        demister_height = 30*mm
        c.setLineWidth(0.3 * mm)
        for i in range(int(demister_height / (2*mm))):
            c.line(x - width/2 + 5*mm, demister_y + i*2*mm, 
                   x + width/2 - 5*mm, demister_y + i*2*mm)
        
        # Equipment tag (above vessel)
        c.setFont("Helvetica-Bold", self.text_sizes['equipment_tag'])
        tag_width = c.stringWidth(tag, "Helvetica-Bold", self.text_sizes['equipment_tag'])
        c.drawString(x - tag_width/2, y + height + 10*mm, tag)
        
        # Equipment name (below tag)
        c.setFont("Helvetica", self.text_sizes['equipment_name'])
        name_width = c.stringWidth(name, "Helvetica", self.text_sizes['equipment_name'])
        c.drawString(x - name_width/2, y + height + 5*mm, name)
        
        return {
            'x': x,
            'y': y,
            'width': width,
            'height': height,
            'nozzles': {
                'inlet': (x - width/2, y + height/2),        # Left side
                'outlet': (x + width/2, y + 20*mm),          # Bottom right
                'drain': (x, y),                              # Bottom center
                'vent': (x, y + height),                      # Top center
                'psv': (x + width/2 - 20*mm, y + height),   # Top side
                'level': (x + width/2, y + height/2),        # Side
            }
        }
    
    def draw_process_line(self, c, x1, y1, x2, y2, line_number, size_class, material):
        """Draw process line with orthogonal routing"""
        c.setLineWidth(self.line_weights['process'])
        c.setStrokeColor(colors.black)
        
        # Horizontal line
        if abs(y2 - y1) < 10*mm:
            c.line(x1, y1, x2, y2)
            
            # Line number in break (middle of line)
            mid_x = (x1 + x2) / 2
            c.setFont("Helvetica-Oblique", self.text_sizes['line_number'])
            
            # Draw line with break for text
            break_width = 40 * mm
            c.line(x1, y1, mid_x - break_width/2, y1)
            c.line(mid_x + break_width/2, y1, x2, y2)
            
            # Line number
            text = line_number
            text_width = c.stringWidth(text, "Helvetica-Oblique", self.text_sizes['line_number'])
            c.drawString(mid_x - text_width/2, y1 + 2*mm, text)
            
            # Size and material below
            c.setFont("Helvetica", 2.5 * mm)
            size_text = f'{size_class}  {material}'
            size_width = c.stringWidth(size_text, "Helvetica", 2.5 * mm)
            c.drawString(mid_x - size_width/2, y1 - 5*mm, size_text)
            
            # Flow arrows every 100mm
            arrow_spacing = 100 * mm
            num_arrows = int((x2 - x1) / arrow_spacing)
            arrow_size = 3 * mm
            for i in range(1, num_arrows + 1):
                arrow_x = x1 + i * arrow_spacing
                # Draw filled triangle
                c.setFillColor(colors.black)
                path = c.beginPath()
                path.moveTo(arrow_x, y1)
                path.lineTo(arrow_x - arrow_size, y1 - arrow_size/2)
                path.lineTo(arrow_x - arrow_size, y1 + arrow_size/2)
                path.close()
                c.drawPath(path, fill=1, stroke=0)
                c.setFillColor(colors.white)
        
        return (x2, y2)
    
    def draw_gate_valve(self, c, x, y, tag, size, orientation='horizontal'):
        """Draw gate valve symbol"""
        c.setLineWidth(self.line_weights['process'])
        c.setStrokeColor(colors.black)
        
        valve_size = 8 * mm
        
        # Two triangles pointing together (><)
        path = c.beginPath()
        path.moveTo(x - valve_size/2, y)
        path.lineTo(x, y + valve_size/2)
        path.lineTo(x, y - valve_size/2)
        path.close()
        c.drawPath(path, fill=0, stroke=1)
        
        path = c.beginPath()
        path.moveTo(x + valve_size/2, y)
        path.lineTo(x, y + valve_size/2)
        path.lineTo(x, y - valve_size/2)
        path.close()
        c.drawPath(path, fill=0, stroke=1)
        
        # Handwheel on top
        c.circle(x, y + valve_size, 3*mm, stroke=1, fill=0)
        c.line(x, y + valve_size/2, x, y + valve_size - 3*mm)
        
        # Tag below
        c.setFont("Helvetica", self.text_sizes['instrument'])
        tag_text = f"{tag}  {size}\""
        tag_width = c.stringWidth(tag_text, "Helvetica", self.text_sizes['instrument'])
        c.drawString(x - tag_width/2, y - 12*mm, tag_text)
        
        return (x, y)
    
    def draw_control_valve(self, c, x, y, tag, size, fail_mode):
        """Draw control valve with actuator"""
        c.setLineWidth(self.line_weights['process'])
        c.setStrokeColor(colors.black)
        
        valve_size = 8 * mm
        
        # Globe valve body
        c.circle(x, y, valve_size/2, stroke=1, fill=0)
        c.line(x - valve_size/2, y, x + valve_size/2, y)
        c.line(x, y - valve_size/2, x, y + valve_size/2)
        
        # Pneumatic actuator on top
        c.rect(x - 6*mm, y + valve_size, 12*mm, 10*mm, stroke=1, fill=0)
        c.line(x, y + valve_size/2, x, y + valve_size)
        
        # Fail mode
        c.setFont("Helvetica", 2*mm)
        fm_width = c.stringWidth(fail_mode, "Helvetica", 2*mm)
        c.drawString(x - fm_width/2, y + valve_size + 12*mm, fail_mode)
        
        # Tag below
        c.setFont("Helvetica", self.text_sizes['instrument'])
        tag_text = f"{tag}  {size}\""
        tag_width = c.stringWidth(tag_text, "Helvetica", self.text_sizes['instrument'])
        c.drawString(x - tag_width/2, y - 12*mm, tag_text)
        
        return (x, y)
    
    def draw_check_valve(self, c, x, y, tag, size):
        """Draw check valve symbol"""
        c.setLineWidth(self.line_weights['process'])
        c.setStrokeColor(colors.black)
        
        valve_size = 8 * mm
        
        # Ball with arrow (>|)
        c.circle(x - valve_size/4, y, valve_size/3, stroke=1, fill=1)
        c.line(x + valve_size/4, y - valve_size/2, x + valve_size/4, y + valve_size/2)
        
        # Arrow
        c.setFillColor(colors.black)
        path = c.beginPath()
        path.moveTo(x - valve_size/2, y)
        path.lineTo(x - valve_size/4, y + valve_size/4)
        path.lineTo(x - valve_size/4, y - valve_size/4)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
        c.setFillColor(colors.white)
        
        # Tag below
        c.setFont("Helvetica", self.text_sizes['instrument'])
        tag_text = f"{tag}  {size}\""
        tag_width = c.stringWidth(tag_text, "Helvetica", self.text_sizes['instrument'])
        c.drawString(x - tag_width/2, y - 12*mm, tag_text)
        
        return (x, y)
    
    def draw_instrument(self, c, x, y, tag, function, field_mounted=True, alarms=None):
        """Draw instrument symbol (ISA 5.1)"""
        circle_dia = 15 * mm
        
        # Circle
        c.setLineWidth(0.35 * mm)
        c.setStrokeColor(colors.black)
        
        if field_mounted:
            c.setFillColor(colors.black)
            c.circle(x, y, circle_dia/2, stroke=1, fill=1)
            c.setFillColor(colors.white)
            # Inner white circle for text
            c.circle(x, y, circle_dia/2 - 0.7*mm, stroke=0, fill=1)
        else:
            c.setFillColor(colors.white)
            c.circle(x, y, circle_dia/2, stroke=1, fill=1)
        
        # Tag inside circle
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 2.5 * mm)
        
        # Function code on top
        func_width = c.stringWidth(function, "Helvetica", 2.5 * mm)
        c.drawString(x - func_width/2, y + 2*mm, function)
        
        # Tag number on bottom
        tag_parts = tag.split('-')
        if len(tag_parts) >= 2:
            tag_num = '-'.join(tag_parts[1:])
            tag_width = c.stringWidth(tag_num, "Helvetica", 2.5 * mm)
            c.drawString(x - tag_width/2, y - 5*mm, tag_num)
        
        # Alarms beside circle
        if alarms:
            c.setFont("Helvetica", 2 * mm)
            y_offset = y + circle_dia/2 + 3*mm
            for alarm in alarms:
                alarm_width = c.stringWidth(alarm, "Helvetica", 2 * mm)
                c.drawString(x + circle_dia/2 + 3*mm, y_offset, alarm)
                y_offset -= 4*mm
        
        return (x, y)
    
    def draw_dashed_line(self, c, x1, y1, x2, y2):
        """Draw dashed line for instrument signals"""
        c.setLineWidth(self.line_weights['instrument'])
        c.setStrokeColor(colors.black)
        c.setDash([3, 3])  # 3mm dash, 3mm gap
        c.line(x1, y1, x2, y2)
        c.setDash()  # Reset to solid
    
    def draw_safety_valve(self, c, x, y, tag, set_pressure, size):
        """Draw pressure safety valve"""
        c.setLineWidth(self.line_weights['process'])
        c.setStrokeColor(colors.black)
        
        valve_size = 10 * mm
        
        # Valve body (triangle with spring on top)
        path = c.beginPath()
        path.moveTo(x, y)
        path.lineTo(x - valve_size/2, y + valve_size)
        path.lineTo(x + valve_size/2, y + valve_size)
        path.close()
        c.drawPath(path, fill=0, stroke=1)
        
        # Spring on top
        spring_height = 12 * mm
        c.line(x, y + valve_size, x, y + valve_size + spring_height)
        # Zigzag for spring
        for i in range(4):
            y_pos = y + valve_size + i * 3*mm
            c.line(x, y_pos, x + 2*mm, y_pos + 1.5*mm)
            c.line(x + 2*mm, y_pos + 1.5*mm, x, y_pos + 3*mm)
        
        # Tag and specs
        c.setFont("Helvetica", 2.5 * mm)
        c.drawString(x + 8*mm, y + valve_size, tag)
        c.drawString(x + 8*mm, y + valve_size - 4*mm, f"{set_pressure} BARG")
        c.drawString(x + 8*mm, y + valve_size - 8*mm, f"{size}\"")
        
        return (x, y + valve_size + spring_height)
    
    def draw_legend(self, c):
        """Draw legend"""
        legend_x = self.margin + 10*mm
        legend_y = self.page_height - self.margin - 20*mm
        
        c.setFont("Helvetica-Bold", 3.5 * mm)
        c.drawString(legend_x, legend_y, "LEGEND / SYMBOLS")
        
        c.setFont("Helvetica", 2.5 * mm)
        y = legend_y - 8*mm
        
        # Process line
        c.setLineWidth(self.line_weights['process'])
        c.line(legend_x, y, legend_x + 15*mm, y)
        c.drawString(legend_x + 20*mm, y - 1*mm, "Process Line")
        y -= 6*mm
        
        # Instrument signal
        c.setLineWidth(self.line_weights['instrument'])
        c.setDash([3, 3])
        c.line(legend_x, y, legend_x + 15*mm, y)
        c.setDash()
        c.drawString(legend_x + 20*mm, y - 1*mm, "Instrument Signal")
        y -= 6*mm
        
        # Field instrument
        c.circle(legend_x + 7.5*mm, y, 5*mm, stroke=1, fill=1)
        c.drawString(legend_x + 20*mm, y - 1*mm, "Field Mounted")
        y -= 6*mm
        
        # Panel instrument
        c.setFillColor(colors.white)
        c.circle(legend_x + 7.5*mm, y, 5*mm, stroke=1, fill=1)
        c.setFillColor(colors.black)
        c.drawString(legend_x + 20*mm, y - 1*mm, "Panel/DCS Mounted")
        
    def draw_notes(self, c):
        """Draw general notes"""
        notes_x = self.margin + 10*mm
        notes_y = self.margin + 80*mm
        
        c.setFont("Helvetica-Bold", 3.5 * mm)
        c.drawString(notes_x, notes_y, "GENERAL NOTES")
        
        c.setFont("Helvetica", 2.5 * mm)
        y = notes_y - 8*mm
        
        notes = [
            "1. All piping designed per ASME B31.3",
            "2. Instruments per ISA 5.1 standard",
            "3. Pneumatic supply: 6 BARG",
            "4. PSV sized per API 520/521",
            "5. All ratings at design temperature"
        ]
        
        for note in notes:
            c.drawString(notes_x, y, note)
            y -= 5*mm
    
    def generate(self, output_path):
        """Generate complete P&ID"""
        print("="*80)
        print("PROGRAMMATIC P&ID GENERATOR")
        print("="*80)
        print()
        
        c = canvas.Canvas(str(output_path), pagesize=landscape(A1))
        
        # Draw border and title block
        print("📐 Drawing border and title block...")
        self.draw_border_and_title_block(c)
        
        # Draw legend and notes
        print("📝 Drawing legend and notes...")
        self.draw_legend(c)
        self.draw_notes(c)
        
        # Main drawing area coordinates
        vessel_x = 250 * mm
        vessel_y = 150 * mm
        
        # Draw V-3601 vessel
        print("🏭 Drawing vessel V-3601...")
        vessel = self.draw_vessel(
            c, vessel_x, vessel_y, 
            width=100*mm, height=230*mm,
            tag="V-3601",
            name="SAHIL EXPORT GAS KOD"
        )
        
        # Equipment specifications box
        c.setFont("Helvetica", 2 * mm)
        spec_x = vessel_x - 80*mm
        spec_y = vessel_y + 50*mm
        specs = [
            "Design Pressure: 22.4 BARG",
            "Design Temp: 55°C / -29°C",
            "Material: CS + SS316L CLAD",
            "Height (T/T): 7800 MM",
            "Diameter: 3300 MM"
        ]
        for i, spec in enumerate(specs):
            c.drawString(spec_x, spec_y - i*5*mm, spec)
        
        # Draw process line from vessel outlet
        print("🔧 Drawing process line 14-01-08-1602...")
        line_start_x = vessel['nozzles']['outlet'][0] + 5*mm
        line_y = vessel['nozzles']['outlet'][1]
        line_end_x = self.page_width - self.margin - 220*mm
        
        self.draw_process_line(
            c, line_start_x, line_y, line_end_x, line_y,
            line_number="14-01-08-1602",
            size_class='16" 300#',
            material="CS"
        )
        
        # Draw valves on line
        print("⚙️  Drawing valves...")
        valve_positions = [
            line_start_x + 80*mm,
            line_start_x + 180*mm,
            line_start_x + 280*mm,
            line_start_x + 380*mm
        ]
        
        self.draw_gate_valve(c, valve_positions[0], line_y, "HV-1602-01", "16")
        self.draw_gate_valve(c, valve_positions[1], line_y, "SDV-3601-01", "16")
        self.draw_control_valve(c, valve_positions[2], line_y, "PCV-3601-01", "16", "FO")
        self.draw_check_valve(c, valve_positions[3], line_y, "CV-1602-01", "16")
        
        # Draw instruments
        print("🔬 Drawing instruments...")
        
        # PT-3601-01
        pt_x = line_start_x + 40*mm
        pt_y = line_y - 40*mm
        self.draw_instrument(c, pt_x, pt_y, "PT-3601-01", "PT", field_mounted=True)
        self.draw_dashed_line(c, line_start_x + 30*mm, line_y, pt_x, pt_y + 7.5*mm)
        
        # PIC-3601-01
        pic_x = pt_x
        pic_y = pt_y - 60*mm
        self.draw_instrument(c, pic_x, pic_y, "PIC-3601-01", "PIC", field_mounted=False, 
                           alarms=["PAH 21 BARG", "PAL 18 BARG"])
        self.draw_dashed_line(c, pt_x, pt_y - 7.5*mm, pic_x, pic_y + 7.5*mm)
        self.draw_dashed_line(c, pic_x, pic_y - 7.5*mm, valve_positions[2], line_y - 8*mm)
        
        # LT-3601-01
        lt_x = vessel_x + 70*mm
        lt_y = vessel_y + 115*mm
        self.draw_instrument(c, lt_x, lt_y, "LT-3601-01", "LT", field_mounted=True)
        self.draw_dashed_line(c, vessel_x + 50*mm, vessel_y + 115*mm, lt_x - 7.5*mm, lt_y)
        
        # LIC-3601-01
        lic_x = lt_x + 60*mm
        lic_y = lt_y
        self.draw_instrument(c, lic_x, lic_y, "LIC-3601-01", "LIC", field_mounted=False,
                           alarms=["LAHH 90%", "LAH 80%", "LAL 20%", "LALL 10%"])
        self.draw_dashed_line(c, lt_x + 7.5*mm, lt_y, lic_x - 7.5*mm, lic_y)
        
        # PSV-3601-01
        print("🛡️  Drawing safety valve...")
        psv_x = vessel['nozzles']['psv'][0]
        psv_y = vessel['nozzles']['psv'][1]
        psv_top = self.draw_safety_valve(c, psv_x, psv_y, "PSV-3601-01", "20", "3")
        
        # PSV discharge to flare
        c.setLineWidth(self.line_weights['process'])
        c.setDash([4, 4])
        c.line(psv_x, psv_top[1], psv_x, psv_top[1] + 30*mm)
        c.line(psv_x, psv_top[1] + 30*mm, psv_x + 100*mm, psv_top[1] + 30*mm)
        c.setDash()
        
        c.setFont("Helvetica", 2.5 * mm)
        c.drawString(psv_x + 105*mm, psv_top[1] + 28*mm, "TO HP FLARE")
        
        # Arrow at end
        c.setFillColor(colors.black)
        path = c.beginPath()
        path.moveTo(psv_x + 100*mm, psv_top[1] + 30*mm)
        path.lineTo(psv_x + 95*mm, psv_top[1] + 27*mm)
        path.lineTo(psv_x + 95*mm, psv_top[1] + 33*mm)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
        
        # "TO EXPORT STATION" label
        c.setFont("Helvetica", 3 * mm)
        c.drawString(line_end_x + 5*mm, line_y - 2*mm, "→ TO EXPORT STATION")
        
        print("💾 Saving PDF...")
        c.save()
        
        print("="*80)
        print("✅ PROFESSIONAL P&ID GENERATED SUCCESSFULLY")
        print(f"📁 Output: {output_path}")
        print("="*80)


if __name__ == '__main__':
    generator = ProfessionalPIDGenerator()
    output_path = Path(__file__).parent / 'Professional_PID_Programmatic.pdf'
    generator.generate(output_path)
    
    print("\n📋 P&ID Features:")
    print("   ✓ Real technical drawing (NOT AI-generated sketch)")
    print("   ✓ Proper CAD-style line weights")
    print("   ✓ Professional ISA 5.1 symbols")
    print("   ✓ Horizontal left-to-right flow")
    print("   ✓ Clean orthogonal routing")
    print("   ✓ Title block, legend, notes")
