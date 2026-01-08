"""
Reference P&ID Layout Analyzer
Analyzes actual P&ID to extract exact layout style, symbols, conventions, and formatting
"""
import os
import sys
import json
import base64
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from openai import OpenAI
import fitz  # PyMuPDF
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)

client = OpenAI(api_key=settings.OPENAI_API_KEY)


def convert_pdf_to_base64(pdf_path, dpi=300):
    """Convert PDF to high-res base64 image"""
    try:
        pdf_document = fitz.open(pdf_path)
        page = pdf_document[0]
        
        # Very high quality for detail capture
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        
        img_bytes = pix.tobytes("png")
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')
        
        pdf_document.close()
        return img_base64
    except Exception as e:
        logger.error(f"PDF conversion failed: {e}")
        raise


def analyze_pid_layout_and_style(pdf_path):
    """Deep analysis of P&ID layout, style, and conventions"""
    
    logger.info("🔍 Analyzing reference P&ID layout and style...")
    
    # Convert to high-resolution image
    image_data = convert_pdf_to_base64(pdf_path, dpi=300)
    
    prompt = """You are a professional P&ID drafting expert. Analyze this P&ID drawing in EXTREME DETAIL to extract the EXACT layout style, formatting, and conventions used.

**CRITICAL ANALYSIS REQUIREMENTS:**

1. **OVERALL LAYOUT STRUCTURE:**
   - Drawing orientation (landscape/portrait)
   - Drawing size (A0, A1, A3, etc.)
   - Overall arrangement philosophy (left-to-right flow, top-to-bottom, equipment-centric)
   - Spacing between elements (equipment spacing, line spacing)
   - Border style and thickness
   - Zone markings or grid system (if any)

2. **TITLE BLOCK ANALYSIS:**
   - Location (bottom right, bottom center, etc.)
   - Exact size and proportions
   - All fields included (drawing number, title, revision, date, project, client, contractor, etc.)
   - Text sizes for each field
   - Company logos location and size
   - Approval signatures location
   - Revision table format and location
   - Border style around title block

3. **EQUIPMENT REPRESENTATION:**
   - Exact symbol style (line thickness, proportions)
   - Equipment orientation (vertical, horizontal)
   - Tag placement (inside equipment, above, below, beside)
   - Tag format and font size
   - Equipment name/description placement
   - Specification callout style
   - Nozzle representation (circle, square, line)
   - Nozzle labeling format
   - Internal details representation (trays, demister, baffles)
   - Elevation markings style
   - Support/foundation indication

4. **PIPING REPRESENTATION:**
   - Line style (single line, double line)
   - Line thickness by type (process, utility, signal)
   - Line numbering placement (on line, break in line, offset)
   - Line numbering format and font size
   - Size/class/material callout format
   - Slope indication method
   - Insulation/tracing indication
   - Flow direction arrows (style, size, placement)
   - Continuation symbols
   - Branch connections representation

5. **VALVE SYMBOLS:**
   - Gate valve exact symbol and size
   - Globe valve symbol
   - Ball valve symbol
   - Butterfly valve symbol
   - Check valve symbol
   - Control valve symbol
   - Safety valve symbol
   - 3-way valve symbol
   - Actuator representation (pneumatic, electric, manual)
   - Valve tag placement and format
   - Valve size indication
   - Fail position indication (FO, FC)

6. **INSTRUMENT SYMBOLS:**
   - Circle size for instruments (mm or standard size)
   - Line thickness for instrument circles
   - Tag format inside circle (PT-3601-01 style)
   - Tag font and size
   - Connection line style (dashed, dotted, thickness)
   - Instrument location indication (field, panel, DCS)
   - Shared display indication
   - Signal line routing
   - Instrument function representation (transmitter, indicator, controller)
   - Alarm/trip indication
   - Multi-function representation

7. **CONTROL LOOPS:**
   - Loop representation style
   - Controller symbol
   - Control valve connection
   - Signal line style and routing
   - Setpoint indication
   - Alarm limit indication (PAH, PAL, PAHH, PALL)
   - Trip indication
   - Interlock representation

8. **LINE TYPES AND PATTERNS:**
   - Process line: style and thickness
   - Instrument signal: style (dashed, dotted, dash-dot)
   - Pneumatic signal: style
   - Electric signal: style
   - Hydraulic line: style
   - Steam line: style
   - Utility line: style
   - Drain/vent line: style

9. **TEXT AND LABELS:**
   - Font type (Arial, Times, technical font)
   - Equipment tag: font size and style (bold, regular)
   - Line number: font size and style
   - Instrument tag: font size and style
   - Notes: font size and style
   - General text: font size
   - Text orientation (horizontal only, or allows vertical)
   - Leader line style

10. **LEGEND/SYMBOL TABLE:**
    - Location on drawing
    - Format and layout
    - What symbols are explained
    - Size and styling
    - Border style

11. **NOTES SECTION:**
    - Location (top left, bottom left, etc.)
    - General notes format
    - Numbering system (1, 2, 3 or Note:)
    - Font size
    - Line spacing
    - Reference notes to specs/standards

12. **COLOR CODING (if any):**
    - Line colors by type
    - Equipment colors
    - Instrument colors
    - Text colors

13. **DRAWING STANDARDS INDICATED:**
    - ISA standard version (ISA 5.1, etc.)
    - Piping class referenced
    - Design codes mentioned
    - Instrument standards
    - Material specifications

14. **SPECIAL FEATURES:**
    - North arrow or orientation
    - Scale indication
    - Match lines to other drawings
    - Detail callouts
    - Typical details
    - Cross-references

15. **DIMENSIONAL INFORMATION:**
    - How dimensions are shown
    - Elevation data format
    - Equipment spacing standards
    - Nozzle orientation indication

16. **CONNECTIONS AND INTERFACES:**
    - How connections to other systems shown
    - Battery limit indication
    - Tie-in points representation
    - Off-plot connections

**OUTPUT FORMAT:**
Return comprehensive JSON with exact specifications:

```json
{
  "drawing_layout": {
    "orientation": "landscape",
    "size": "A1 or specific dimensions",
    "arrangement_philosophy": "description",
    "spacing_standards": {...},
    "border_style": "description"
  },
  "title_block": {
    "location": "bottom right",
    "dimensions": "width x height mm",
    "fields": [...],
    "text_sizes": {...},
    "format": "detailed description"
  },
  "equipment_style": {
    "vessels": {
      "symbol_type": "vertical cylinder",
      "line_thickness": "0.5mm",
      "tag_placement": "top center",
      "tag_format": "TAG-0000",
      "tag_font_size": "3.5mm",
      "nozzle_style": "circle with leader",
      "internal_details": "cross-hatch pattern"
    },
    "pumps": {...},
    "exchangers": {...}
  },
  "piping_style": {
    "line_thickness": "0.35mm for process",
    "numbering_format": "PP-AA-SS-LLLL",
    "numbering_placement": "break in line, centered",
    "numbering_font_size": "2.5mm",
    "size_class_format": "16\" 300#",
    "flow_arrow_style": "filled triangle, 5mm"
  },
  "valve_symbols": {
    "gate": "detailed symbol description and dimensions",
    "globe": "...",
    "control": "...",
    "safety": "...",
    "tag_format": "TYPE-0000-00"
  },
  "instrument_symbols": {
    "circle_diameter": "12mm",
    "line_thickness": "0.25mm",
    "tag_format": "TT-AAAA-SS",
    "tag_font_size": "2mm",
    "location_indicator": "filled/empty circle",
    "signal_line_style": "dashed 2mm-2mm"
  },
  "line_types": {
    "process": "solid 0.35mm",
    "instrument_signal": "dashed",
    "pneumatic": "dashed with dots",
    "electric": "dashed with slash marks"
  },
  "text_standards": {
    "font_family": "Arial or technical",
    "equipment_tag": "4mm bold",
    "line_number": "2.5mm italic",
    "instrument_tag": "2mm regular",
    "notes": "2.5mm regular"
  },
  "legend": {
    "location": "specific location",
    "format": "table/list",
    "content": "what's included"
  },
  "notes_section": {
    "location": "top left or bottom left",
    "format": "numbered list",
    "font_size": "2.5mm"
  },
  "standards_referenced": [
    "ISA 5.1",
    "ASME B31.3",
    "API 520"
  ],
  "professional_features": {
    "grid_system": "yes/no, format",
    "zone_marking": "A-Z, 1-20",
    "match_lines": "how shown",
    "continuation": "symbol style"
  }
}
```

Be EXTREMELY detailed - we need to replicate this EXACT professional style."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_data}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=4000,
            temperature=0.1
        )
        
        content = response.choices[0].message.content
        logger.info("✅ Layout analysis complete")
        
        # Parse JSON
        try:
            if '```json' in content:
                json_str = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                json_str = content.split('```')[1].split('```')[0].strip()
            else:
                json_str = content.strip()
            
            layout_spec = json.loads(json_str)
            return layout_spec
        except json.JSONDecodeError:
            logger.warning("⚠️ Response not JSON, returning as text")
            return {"raw_analysis": content}
            
    except Exception as e:
        logger.error(f"❌ Layout analysis failed: {e}")
        raise


def main():
    """Main execution"""
    
    pid_path = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601\P16093-14-01-08-1602_P&ID.pdf"
    output_dir = Path(r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend")
    output_path = output_dir / "Reference_PID_Layout_Style.json"
    
    logger.info("=" * 100)
    logger.info("REFERENCE P&ID LAYOUT & STYLE ANALYZER")
    logger.info("=" * 100)
    
    logger.info(f"\n📂 Analyzing: {pid_path}")
    
    # Analyze layout and style
    layout_spec = analyze_pid_layout_and_style(pid_path)
    
    # Save results
    with open(output_path, 'w') as f:
        json.dump(layout_spec, f, indent=2)
    
    logger.info(f"\n✅ Layout specification saved to: {output_path}")
    
    logger.info("\n" + "=" * 100)
    logger.info("ANALYSIS COMPLETE!")
    logger.info("=" * 100)
    
    # Print summary
    if isinstance(layout_spec, dict) and "raw_analysis" not in layout_spec:
        logger.info("\n📊 Extracted specifications:")
        for key in layout_spec.keys():
            logger.info(f"   - {key}")


if __name__ == "__main__":
    main()
