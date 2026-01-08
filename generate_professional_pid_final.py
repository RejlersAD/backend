"""
Generate PROFESSIONAL P&ID using reference P&ID and standard ISA/ANSI conventions
Based on analysis of professional engineering drawings and legend standards
"""
import os
import sys
import django
import fitz
import base64
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from openai import OpenAI
from django.conf import settings
import logging
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_pfd_analysis():
    """Load PFD analysis"""
    pfd_file = Path(__file__).parent / 'P16093_PFD_Analysis.json'
    with open(pfd_file, 'r') as f:
        return json.load(f)

def load_reference_pid():
    """Load reference P&ID as base64"""
    ref_file = Path(__file__).parent.parent / 'Documents' / 'PFD to P&ID' / '1601' / 'P16093-14-01-08-1602_P&ID.pdf'
    
    doc = fitz.open(ref_file)
    page = doc[0]
    mat = fitz.Matrix(300/72, 300/72)
    pix = page.get_pixmap(matrix=mat)
    
    from PIL import Image
    from io import BytesIO
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    buffered = BytesIO()
    img.save(buffered, format="PNG", optimize=True, quality=95)
    img_base64 = base64.b64encode(buffered.getvalue()).decode()
    doc.close()
    
    return img_base64

def generate_professional_pid():
    """Generate professional P&ID following ISA/ANSI standards"""
    
    logger.info("="*80)
    logger.info("PROFESSIONAL P&ID GENERATOR (ISA/ANSI STANDARDS)")
    logger.info("="*80)
    
    # Load data
    logger.info("📂 Loading PFD analysis and reference P&ID...")
    pfd_data = load_pfd_analysis()
    ref_pid_base64 = load_reference_pid()
    logger.info("✅ Data loaded")
    
    # Extract PFD information
    equipment = pfd_data.get('equipment', [])
    piping = pfd_data.get('piping', [])
    instruments = pfd_data.get('instruments', [])
    
    logger.info(f"   Equipment: {len(equipment)}")
    logger.info(f"   Piping: {len(piping)}")
    logger.info(f"   Instruments: {len(instruments)}")
    
    # Build ULTRA-CONDENSED professional prompt
    prompt = f"""Professional P&ID (ISA 5.1/ANSI): Drawing P16093-14-01-08-1602 - EXPORT GAS KO DRUM

CRITICAL: Black lines on WHITE background. Horizontal flow. CAD quality. Match reference style.

LAYOUT: A1 landscape, title block bottom-right, legend top-left, notes bottom-left.

V-3601 (SAHIL EXPORT GAS KOD): Vertical cylinder 7800x3300mm, 22.4 BARG, 55°C/-29°C, CS+SS316L. Tag "V-3601" bold 5mm above. 7 nozzles: IN-16" (left), OUT-16" (bottom-right), DRAIN-3/4" (bottom), VENT-3/4" (top), PSV-3" (top-side), LG-1/2", PG-1/2". Demister pad (hatched) inside top.

LINE 14-01-08-1602: 16" 300# CS, single-line 0.5mm, from V-3601 to EXPORT STATION →, flow arrows.

VALVES (ANSI symbols): HV-1602-01 gate+handwheel, SDV-3601-01 globe+actuator FC, PCV-3601-01 control FO (to PIC), CV-1602-01 check. All 16", tags 2.5mm below.

INSTRUMENTS (ISA 15mm circles, 2.5mm text): PT-3601-01 filled (outlet, to PIC), PIC-3601-01 empty (PAH/PAL), LT-3601-01 filled (side, to LIC), LIC-3601-01 empty (LAH/LAL/LAHH/LALL), TT-3601-01 thermowell, FT-3601-01 orifice. Dashed signals 0.25mm.

SAFETY: PSV-3601-01 spring valve 3" @20 BARG, V-3601 top to HP FLARE.

UTILITIES: IA 6 BARG (dashed) to actuators. Drains 3/4" TO CLOSED DRAIN. Vents 3/4" TO ATMOSPHERE.

LEGEND: ━ Process, - - Instrument, —·— Pneumatic, ● Field, ○ Panel, >< Gate, >◁ Globe, >| Check.

NOTES: 1.ASME B31.3 2.Pneumatic 6 BARG 3.PSV per API 520 4.ISA 5.1

TEXT: Equipment 5mm bold, lines 3mm italic, instruments 2.5mm. LINES: Process 0.5mm, equipment 0.7mm, signals 0.25mm.

Professional AutoCAD style - sharp, clean, grid-aligned, technical drawing."""

    # Generate with DALL-E 3
    logger.info("🎨 Generating professional P&ID with DALL-E 3...")
    logger.info(f"   Prompt length: {len(prompt)} characters")
    
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1792x1024",  # Landscape HD
            quality="hd",
            n=1,
            response_format="url"
        )
        
        image_url = response.data[0].url
        logger.info(f"✅ P&ID generated successfully")
        logger.info(f"   URL: {image_url}")
        
        # Download and save
        import requests
        img_data = requests.get(image_url).content
        output_file = Path(__file__).parent / 'Professional_PID_ISA_ANSI_Standard.png'
        with open(output_file, 'wb') as f:
            f.write(img_data)
        
        logger.info(f"💾 Saved to: {output_file}")
        logger.info("="*80)
        logger.info("✅ PROFESSIONAL P&ID GENERATION COMPLETE")
        logger.info("="*80)
        
        return output_file
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise

if __name__ == '__main__':
    generate_professional_pid()
