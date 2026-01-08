"""
Extract and analyze ROBOFLOW legend and abbreviation data for professional P&ID generation
"""
import os
import sys
import django
import pandas as pd
import fitz
import base64
import json
from pathlib import Path
from io import BytesIO
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from openai import OpenAI
from django.conf import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_abbreviations():
    """Extract abbreviations from Excel files"""
    roboflow_dir = Path(__file__).parent / 'roboflow_data'
    
    logger.info("="*80)
    logger.info("EXTRACTING ABBREVIATIONS AND CLASS NAMES")
    logger.info("="*80)
    
    abbreviations = {}
    
    # Try to read ABBREVIATIONS.xlsx
    abbr_files = list(roboflow_dir.rglob('ABBREVIATIONS.xlsx'))
    if abbr_files:
        logger.info(f"📄 Reading: {abbr_files[0].name}")
        try:
            df = pd.read_excel(abbr_files[0])
            logger.info(f"   Columns: {list(df.columns)}")
            logger.info(f"   Rows: {len(df)}")
            abbreviations['standard_abbreviations'] = df.to_dict('records')
            logger.info(f"   ✅ Extracted {len(df)} abbreviations")
        except Exception as e:
            logger.error(f"   ❌ Error: {e}")
    
    # Try to read CLASS NAME WITH ABBREVIATIONS.xlsx
    class_files = list(roboflow_dir.rglob('CLASS NAME WITH ABBREVIATIONS.xlsx'))
    if class_files:
        logger.info(f"📄 Reading: {class_files[0].name}")
        try:
            df = pd.read_excel(class_files[0])
            logger.info(f"   Columns: {list(df.columns)}")
            logger.info(f"   Rows: {len(df)}")
            abbreviations['class_abbreviations'] = df.to_dict('records')
            logger.info(f"   ✅ Extracted {len(df)} class abbreviations")
        except Exception as e:
            logger.error(f"   ❌ Error: {e}")
    
    return abbreviations

def analyze_roboflow_legend():
    """Analyze ROBOFLOW legend PDF with GPT-4 Vision"""
    roboflow_dir = Path(__file__).parent / 'roboflow_data'
    legend_file = roboflow_dir / 'LEGEND_SHEET' / 'legend.pdf'
    
    if not legend_file.exists():
        logger.error(f"❌ Legend file not found: {legend_file}")
        return None
    
    logger.info("="*80)
    logger.info("ANALYZING ROBOFLOW LEGEND WITH GPT-4 VISION")
    logger.info("="*80)
    logger.info(f"📄 File: {legend_file.name}")
    
    # Convert PDF to images
    doc = fitz.open(legend_file)
    logger.info(f"   Pages: {len(doc)}")
    
    all_analysis = {}
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(300/72, 300/72)
        pix = page.get_pixmap(matrix=mat)
        
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buffered = BytesIO()
        img.save(buffered, format="PNG", optimize=True, quality=95)
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        logger.info(f"\n🔍 Analyzing page {page_num + 1} ({pix.width}x{pix.height})...")
        
        # Detailed extraction prompt
        prompt = """Extract ALL P&ID symbol specifications from this legend sheet. Return detailed JSON with:

{
  "equipment_symbols": {
    "vessels": {"description": "", "line_weight": "", "typical_sizes": []},
    "pumps": {"description": "", "symbol_style": "", "orientation": ""},
    "heat_exchangers": {"description": "", "internal_details": ""},
    "compressors": {"description": "", "type_indicators": ""}
  },
  "valve_symbols": {
    "gate_valve": {"symbol": "", "operator": "", "dimensions": ""},
    "globe_valve": {"symbol": "", "actuator": "", "fail_position": ""},
    "ball_valve": {"symbol": "", "characteristics": ""},
    "check_valve": {"symbol": "", "direction_indicator": ""},
    "butterfly_valve": {"symbol": "", "disc_representation": ""},
    "control_valve": {"symbol": "", "positioner": "", "fail_modes": ""},
    "safety_valve": {"symbol": "", "spring_indication": "", "discharge": ""}
  },
  "instrument_symbols": {
    "circle_sizes": {"field": "", "panel": "", "dcs": ""},
    "line_weights": "",
    "fill_patterns": {"field": "", "panel": "", "shared": ""},
    "tag_format": {"structure": "", "examples": []},
    "function_codes": {"T": "Transmitter", "I": "Indicator", "C": "Controller", "E": "Element", "A": "Alarm"},
    "alarm_codes": {"H": "High", "L": "Low", "HH": "High-High", "LL": "Low-Low"}
  },
  "piping_symbols": {
    "process_line": {"weight": "", "style": "", "arrow_spacing": ""},
    "instrument_signal": {"weight": "", "dash_pattern": ""},
    "pneumatic": {"weight": "", "pattern": ""},
    "electric": {"weight": "", "pattern": ""}
  },
  "line_specifications": {
    "main_process": "0.5mm or 0.7mm",
    "equipment_outline": "0.7mm or 1.0mm",
    "signals": "0.25mm or 0.35mm"
  },
  "text_standards": {
    "equipment_tags": {"size": "", "weight": "", "case": ""},
    "line_numbers": {"size": "", "style": ""},
    "instrument_tags": {"size": "", "weight": ""},
    "notes": {"size": "", "justification": ""}
  },
  "connection_types": {
    "flanged": {"symbol": ""},
    "threaded": {"symbol": ""},
    "welded": {"symbol": ""},
    "union": {"symbol": ""}
  },
  "special_symbols": {
    "orifice_plate": {"symbol": "", "tap_locations": ""},
    "strainer": {"symbol": ""},
    "reducer": {"symbol": ""},
    "vent": {"symbol": "", "size": ""},
    "drain": {"symbol": "", "size": ""}
  },
  "drawing_standards": {
    "sheet_sizes": [],
    "border_thickness": "",
    "title_block_format": "",
    "legend_placement": "",
    "notes_placement": ""
  },
  "abbreviations": {
    "materials": {"CS": "Carbon Steel", "SS": "Stainless Steel"},
    "classes": {"150#": "", "300#": "", "600#": ""},
    "services": {}
  }
}

Be EXTREMELY specific with measurements, patterns, and conventions."""

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
                                    "url": f"data:image/png;base64,{img_base64}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4096,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            all_analysis[f"page_{page_num + 1}"] = content
            logger.info(f"   ✅ Extracted specifications")
            
        except Exception as e:
            logger.error(f"   ❌ Error: {e}")
    
    doc.close()
    return all_analysis

def analyze_sample_pids():
    """Analyze sample P&ID files to understand professional layout"""
    roboflow_dir = Path(__file__).parent / 'roboflow_data'
    pid_files = list((roboflow_dir / 'VESSEL & PUMP PID').glob('*.pdf'))[:3]  # Analyze first 3
    
    logger.info("="*80)
    logger.info("ANALYZING SAMPLE P&ID LAYOUTS")
    logger.info("="*80)
    
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    analyses = {}
    
    for pid_file in pid_files:
        logger.info(f"\n📄 Analyzing: {pid_file.name}")
        
        # Convert first page to image
        doc = fitz.open(pid_file)
        page = doc[0]
        mat = fitz.Matrix(200/72, 200/72)  # Lower DPI for layout analysis
        pix = page.get_pixmap(matrix=mat)
        
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buffered = BytesIO()
        img.save(buffered, format="PNG", optimize=True, quality=85)
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        prompt = """Analyze this professional P&ID layout. Extract:
1. Overall layout style (horizontal/vertical flow)
2. Equipment arrangement and spacing
3. Title block location and format
4. Legend placement
5. Line routing style (orthogonal/curved)
6. Text placement conventions
7. Symbol density and spacing
8. Professional quality indicators

Return concise JSON with these observations."""

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
                                    "url": f"data:image/png;base64,{img_base64}",
                                    "detail": "low"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1024,
                temperature=0.1
            )
            
            analyses[pid_file.stem] = response.choices[0].message.content
            logger.info(f"   ✅ Layout analyzed")
            
        except Exception as e:
            logger.error(f"   ❌ Error: {e}")
        
        doc.close()
    
    return analyses

if __name__ == '__main__':
    # Extract all data
    logger.info("\n" + "="*80)
    logger.info("ROBOFLOW DATA EXTRACTION AND ANALYSIS")
    logger.info("="*80 + "\n")
    
    # 1. Extract abbreviations
    abbreviations = extract_abbreviations()
    
    # 2. Analyze legend
    legend_specs = analyze_roboflow_legend()
    
    # 3. Analyze sample P&IDs
    pid_layouts = analyze_sample_pids()
    
    # Combine all data
    comprehensive_data = {
        'abbreviations': abbreviations,
        'legend_specifications': legend_specs,
        'pid_layout_analysis': pid_layouts,
        'extraction_date': '2026-01-08'
    }
    
    # Save comprehensive analysis
    output_file = Path(__file__).parent / 'ROBOFLOW_Comprehensive_PID_Specifications.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(comprehensive_data, f, indent=2, ensure_ascii=False)
    
    logger.info("\n" + "="*80)
    logger.info(f"✅ COMPREHENSIVE ANALYSIS COMPLETE")
    logger.info(f"📁 Saved to: {output_file}")
    logger.info("="*80)
    
    print("\n📋 Next Step:")
    print("   Use extracted specifications to regenerate professional P&ID with exact symbol standards")
