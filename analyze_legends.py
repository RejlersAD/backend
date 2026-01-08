"""
Analyze legend PDFs using GPT-4 Vision to extract professional P&ID symbol specifications
"""
import os
import sys
import django
import fitz  # PyMuPDF
import base64
import json
from pathlib import Path
from io import BytesIO
from PIL import Image

# Set up Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from openai import OpenAI
from django.conf import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def convert_pdf_pages_to_images(pdf_path, dpi=300):
    """Convert PDF pages to high-resolution images"""
    doc = fitz.open(pdf_path)
    images = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        # Render page to pixmap at high DPI
        mat = fitz.Matrix(dpi/72, dpi/72)
        pix = page.get_pixmap(matrix=mat)
        
        # Convert to PIL Image
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        # Convert to base64
        buffered = BytesIO()
        img.save(buffered, format="PNG", optimize=True, quality=95)
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        images.append({
            'page': page_num + 1,
            'width': pix.width,
            'height': pix.height,
            'base64': img_base64
        })
        
        logger.info(f"   Page {page_num + 1}: {pix.width}x{pix.height} pixels")
    
    doc.close()
    return images

def analyze_legend_with_gpt4_vision(legend_files):
    """Analyze legend PDFs using GPT-4 Vision"""
    
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    print("="*80)
    print("ANALYZING LEGEND FILES WITH GPT-4 VISION")
    print("="*80)
    print()
    
    all_symbols = {}
    
    for legend_path in legend_files:
        print(f"📄 Processing: {Path(legend_path).name}")
        print()
        
        # Convert PDF to images
        print("   🔄 Converting PDF to images...")
        images = convert_pdf_pages_to_images(legend_path, dpi=300)
        print(f"   ✅ Converted {len(images)} pages")
        print()
        
        # Analyze each page
        for img_data in images:
            page_num = img_data['page']
            print(f"   🔍 Analyzing page {page_num}...")
            
            try:
                # Create vision prompt
                prompt = """Analyze this P&ID legend/symbol sheet image in EXTREME DETAIL.

Extract ALL symbol specifications with precise measurements and descriptions:

1. **EQUIPMENT SYMBOLS:**
   - Vessels (vertical/horizontal): Shape, line weight, nozzle representation
   - Pumps: Symbol style, orientation, connection points
   - Heat exchangers: Shell/tube representation, baffle indication
   - Tanks: Shape, roof type, level indication
   - Compressors: Symbol type, stage indication

2. **PIPING SYMBOLS:**
   - Process lines: Line weight (mm), continuity (solid/dashed), arrow style
   - Instrument signals: Dash pattern (mm), line weight
   - Pneumatic lines: Dash-dot pattern specifications
   - Utility lines: Special markings or colors
   - Flow direction arrows: Size, style, spacing

3. **VALVE SYMBOLS (CRITICAL):**
   - Gate valve: Exact symbol shape, dimensions
   - Globe valve: Body shape, stem representation
   - Ball valve: Symbol characteristics
   - Check valve: Direction indicator, body shape
   - Butterfly valve: Disc representation
   - Control valve: Actuator representation (pneumatic/electric)
   - Safety valve: Spring indication, discharge direction
   - Manual vs automatic: Handwheel vs actuator symbols
   - Fail positions: FC (Fail Closed), FO (Fail Open) notation

4. **INSTRUMENT SYMBOLS (ISA 5.1):**
   - Circle sizes: Diameter in mm for field/panel/DCS mounted
   - Line weights: Circle outline thickness
   - Fill patterns: Solid (field), empty (panel), half-filled (shared)
   - Text inside circles: Font size, character limit, abbreviation rules
   - Tag format: Prefix codes (PT, LT, FT, TT, etc.)
   - Alarm indicators: High (H), Low (L), positions
   - Function codes: I (Indicator), C (Controller), T (Transmitter), etc.

5. **CONNECTION SYMBOLS:**
   - Flanged: Symbol type, bolt representation
   - Threaded: Symbol style
   - Welded: Indication method
   - Union: Symbol shape

6. **SPECIAL SYMBOLS:**
   - Reducers/Expanders: Representation style
   - Orifice plates: Symbol shape, tap locations
   - Strainers: Body representation
   - Traps: Type indication (steam, etc.)
   - Vents and drains: Symbol style, labeling

7. **TEXT STANDARDS:**
   - Equipment tags: Font, size (mm), weight (bold/regular), placement
   - Line numbers: Font, size, style (italic/regular)
   - Instrument tags: Font, size, case (upper/lower)
   - Notes: Font, size, justification
   - Dimension text: Size, precision

8. **LINE SPECIFICATIONS:**
   - Process lines: 0.5mm, 0.7mm, 1.0mm specifications
   - Instrument signals: 0.25mm, 0.35mm specifications
   - When to use each thickness

9. **DRAWING STANDARDS:**
   - Sheet size: A1, A3, etc.
   - Border thickness and margins
   - Title block: Components, text sizes, table format
   - Legend placement: Preferred corner/location
   - Notes section: Placement, numbering style
   - Revision table: Format, column headers

10. **MATERIAL/CLASS INDICATORS:**
    - Line class symbols (150#, 300#, 600#, etc.)
    - Material abbreviations (CS, SS, SS316L, etc.)
    - Insulation indicators
    - Tracing indicators (steam, electric)

Return a comprehensive JSON with ALL extracted specifications. Be EXTREMELY detailed - include exact measurements, patterns, and conventions. This will be used to generate professional P&IDs."""

                # Call GPT-4 Vision
                response = client.chat.completions.create(
                    model="gpt-4o",  # Latest vision model
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{img_data['base64']}",
                                        "detail": "high"  # High detail for precision
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=4096,
                    temperature=0.1  # Low temperature for factual extraction
                )
                
                content = response.choices[0].message.content
                print(f"   ✅ Analysis complete")
                print()
                
                # Store analysis
                filename = Path(legend_path).stem
                key = f"{filename}_page_{page_num}"
                all_symbols[key] = content
                
            except Exception as e:
                print(f"   ❌ Error analyzing page {page_num}: {e}")
                print()
    
    # Save comprehensive analysis
    output_file = Path(__file__).parent / 'Legend_Symbol_Specifications.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_symbols, f, indent=2, ensure_ascii=False)
    
    print("="*80)
    print(f"✅ LEGEND ANALYSIS COMPLETE")
    print(f"📁 Saved to: {output_file}")
    print("="*80)
    
    return output_file

if __name__ == '__main__':
    legend_dir = Path(__file__).parent / 'legend_files'
    legend_files = list(legend_dir.glob('*.pdf'))
    
    if not legend_files:
        print("❌ No legend PDF files found")
        print(f"   Expected location: {legend_dir}")
        sys.exit(1)
    
    print(f"Found {len(legend_files)} legend files:")
    for f in legend_files:
        print(f"   - {f.name}")
    print()
    
    output = analyze_legend_with_gpt4_vision(legend_files)
    
    print("\n📋 Next Step:")
    print("   Use the extracted specifications to regenerate professional P&ID")
