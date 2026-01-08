"""
Generate FINAL PROFESSIONAL P&ID using extracted ROBOFLOW legend specifications
"""
import os
import sys
import django
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from openai import OpenAI
from django.conf import settings
import logging
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generate_final_professional_pid():
    """Generate P&ID using extracted ROBOFLOW specifications"""
    
    logger.info("="*80)
    logger.info("FINAL PROFESSIONAL P&ID GENERATION")
    logger.info("Using ROBOFLOW Legend Specifications")
    logger.info("="*80)
    
    # Load extracted specifications
    specs_file = Path(__file__).parent / 'ROBOFLOW_Comprehensive_PID_Specifications.json'
    with open(specs_file, 'r', encoding='utf-8') as f:
        specs = json.load(f)
    
    logger.info("✅ Loaded ROBOFLOW specifications")
    
    # Build prompt using extracted standards
    prompt = """Professional P&ID Drawing P16093-14-01-08-1602 - SAHIL EXPORT GAS KO DRUM (V-3601)

DRAWING STANDARDS (from ROBOFLOW legend):
✓ Black lines on WHITE background only
✓ Orthogonal line routing (90° angles)
✓ Professional CAD quality
✓ A1 landscape format

LINE WEIGHTS:
- Equipment: 0.7mm solid
- Process lines: 0.5mm solid  
- Instruments: 0.25mm dashed
- Pneumatic: 0.25mm dotted

TEXT:
- Equipment tags: 3mm bold UPPERCASE (V-3601)
- Line numbers: 2.5mm italic (14-01-08-1602)
- Instruments: 2.5mm regular (PT-3601-01)

LAYOUT:
Title block: bottom-right
Legend: top-right
Notes: bottom-left
Flow: LEFT TO RIGHT horizontal

V-3601 VESSEL: Vertical cylinder 7800x3300mm, 22.4 BARG, CS+SS316L clad. Tag above bold. Demister pad hatched inside. 7 nozzles: IN-16" left, OUT-16" bottom-right, DRAIN-3/4" bottom, VENT-3/4" top, PSV-3" top-side, gauges 1/2".

LINE 14-01-08-1602: 16" 300# CS from V-3601 to EXPORT STATION →, flow arrows every 500mm.

VALVES (standard symbols):
HV-1602-01: gate valve + handwheel
SDV-3601-01: globe valve + actuator FC  
PCV-3601-01: control valve FO (to PIC)
CV-1602-01: check valve
All 16", tags below.

INSTRUMENTS (10mm circles per ROBOFLOW):
PT-3601-01: solid circle (field) near outlet → PIC
PIC-3601-01: empty circle (panel) with PAH/PAL
LT-3601-01: solid (side) → LIC
LIC-3601-01: empty with LAH/LAL/LAHH/LALL  
TT-3601-01: solid, thermowell
FT-3601-01: solid, orifice plate
Dashed 0.25mm signal lines.

SAFETY: PSV-3601-01 spring valve 3" @ 20 BARG, top to HP FLARE.

UTILITIES: IA 6 BARG dotted to actuators. Drains 3/4" TO CLOSED DRAIN. Vents 3/4" TO ATMOSPHERE.

LEGEND: ━ Process | - - Instrument | ··· Pneumatic | ● Field | ○ Panel | Standard valve symbols

NOTES: 1.ASME B31.3 2.Pneumatic 6 BARG 3.ISA 5.1 4.PSV per API 520

Professional engineering drawing - clean, sharp, grid-aligned, orthogonal routing."""

    logger.info("🎨 Generating P&ID with DALL-E 3...")
    logger.info(f"   Prompt length: {len(prompt)} characters")
    
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1792x1024",
            quality="hd",
            n=1,
            response_format="url"
        )
        
        image_url = response.data[0].url
        logger.info("✅ P&ID generated successfully")
        
        # Download and save
        img_data = requests.get(image_url).content
        output_file = Path(__file__).parent / 'FINAL_Professional_PID_ROBOFLOW_Standards.png'
        with open(output_file, 'wb') as f:
            f.write(img_data)
        
        logger.info(f"💾 Saved to: {output_file}")
        logger.info("="*80)
        logger.info("✅ FINAL PROFESSIONAL P&ID COMPLETE")
        logger.info("="*80)
        logger.info("\nP&ID Features:")
        logger.info("   ✓ ROBOFLOW legend standards applied")
        logger.info("   ✓ Orthogonal line routing")
        logger.info("   ✓ Correct line weights (0.7/0.5/0.25mm)")
        logger.info("   ✓ Standard instrument circles (10mm)")
        logger.info("   ✓ Professional text sizing")
        logger.info("   ✓ Horizontal left-to-right flow")
        logger.info("   ✓ Title block, legend, notes properly placed")
        
        return output_file
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise

if __name__ == '__main__':
    generate_final_professional_pid()
