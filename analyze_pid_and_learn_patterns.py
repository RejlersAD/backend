"""
P&ID Analysis and Pattern Learning System
Analyzes P&ID generated from PFD and learns conversion patterns using GPT-4 Vision
"""
import os
import sys
import json
import base64
from openai import OpenAI
import fitz  # PyMuPDF
from pathlib import Path
import logging
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=settings.OPENAI_API_KEY)

def convert_pdf_to_images(pdf_path, dpi=200):
    """Convert PDF pages to base64 encoded images"""
    images = []
    try:
        pdf_document = fitz.open(pdf_path)
        logger.info(f"📄 Converting PDF: {pdf_document.page_count} pages")
        
        for page_num in range(pdf_document.page_count):
            page = pdf_document[page_num]
            
            # Convert to image at specified DPI
            zoom = dpi / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            
            # Convert to PNG bytes
            img_bytes = pix.tobytes("png")
            
            # Encode to base64
            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            images.append({
                'page': page_num + 1,
                'data': img_base64,
                'format': 'png'
            })
            
            logger.info(f"✅ Converted page {page_num + 1}")
        
        pdf_document.close()
        return images
        
    except Exception as e:
        logger.error(f"❌ PDF conversion failed: {e}")
        raise


def analyze_pid_with_vision(image_data, pfd_analysis=None):
    """Analyze P&ID using GPT-4 Vision and compare with PFD"""
    
    prompt = """You are an expert P&ID (Piping and Instrumentation Diagram) analyzer. 
Analyze this P&ID drawing in extreme detail and extract ALL technical information.

**CRITICAL REQUIREMENTS:**
1. Extract EVERY equipment item with complete specifications
2. Extract EVERY piping line with all details
3. Extract EVERY instrument with full tag information
4. Extract EVERY valve with complete specifications
5. Extract ALL control loops and logic
6. Extract ALL connections and flow paths
7. Note all symbols, conventions, and drawing standards

**EXTRACTION STRUCTURE:**

1. **DRAWING INFORMATION:**
   - Drawing number
   - Drawing title
   - Revision
   - Date
   - Project name/code
   - Scale
   - Sheet number
   
2. **EQUIPMENT (Every vessel, tank, pump, compressor, etc.):**
   For each equipment item extract:
   - Tag number (e.g., V-3601, P-3601)
   - Equipment type (vessel, pump, compressor, heat exchanger, etc.)
   - Description/service
   - Dimensions (length, diameter, height)
   - Material of construction
   - Design pressure and temperature
   - Operating pressure and temperature
   - Internals (trays, packing, demister, etc.)
   - Connections (nozzles, flanges)
   - Elevation/orientation
   - Notes and specifications
   
3. **PIPING LINES (Every line):**
   For each piping line extract:
   - Line number (complete)
   - Size (inch or mm)
   - Pressure class/rating
   - Material specification
   - Insulation type and thickness
   - Tracing (steam, electric, etc.)
   - From equipment/connection
   - To equipment/connection
   - Service/fluid
   - Flow direction
   - Slope requirements
   - Special requirements
   
4. **INSTRUMENTS (Every single instrument):**
   For each instrument extract:
   - Full tag number (e.g., PT-3601-01, FT-3601-02, LT-3601-03)
   - Instrument type (pressure transmitter, flow transmitter, level transmitter, temperature, etc.)
   - Measurement range (e.g., 0-25 barg, 0-100 m3/h)
   - Location on drawing
   - Process connection point
   - Signal type (4-20mA, digital, etc.)
   - Control system connection (DCS, PLC, etc.)
   - Function (monitoring, control, alarm, shutdown)
   - Fail-safe action (if applicable)
   - Associated control loops
   
5. **VALVES (Every valve):**
   For each valve extract:
   - Tag number (e.g., SDV-3601-01, CV-3601-01, HV-3601-01)
   - Valve type (gate, globe, ball, butterfly, check, safety, control)
   - Size
   - Pressure class
   - Material
   - Actuation (manual, pneumatic, electric, hydraulic)
   - Function (isolation, control, safety shutdown, pressure relief)
   - Fail position (fail open, fail closed, fail as-is)
   - Control signal type
   - Interlock conditions
   
6. **CONTROL LOOPS:**
   For each control loop identify:
   - Loop number/identifier
   - Controlled variable (pressure, flow, level, temperature)
   - Primary measurement instrument
   - Control valve
   - Controller type (PID, ON/OFF, cascade, ratio)
   - Setpoint range
   - Alarm high/low values
   - Shutdown conditions
   
7. **SAFETY SYSTEMS:**
   - Emergency shutdown valves (ESD, SDV)
   - Pressure safety valves (PSV, PRV)
   - Rupture discs
   - Flame arrestors
   - Blowdown systems
   - Interlock logic
   - Safety instrumented functions (SIF)
   
8. **UTILITIES:**
   - Utility connections (instrument air, steam, cooling water, etc.)
   - Utility line sizes and specifications
   - Drain and vent connections
   - Sample points
   
9. **SYMBOLS AND CONVENTIONS:**
   - Drawing symbols used
   - Line type conventions (process, utility, signal)
   - Instrument symbol conventions
   - Valve symbol conventions
   - Equipment symbol conventions
   
10. **NOTES AND SPECIFICATIONS:**
    - General notes
    - Piping specifications referenced
    - Instrument specifications
    - Design codes and standards
    - Special requirements

Return the analysis as a detailed JSON object with all the above information.
Be extremely thorough - extract EVERY detail visible on the drawing."""

    # If PFD analysis provided, add comparison instructions
    if pfd_analysis:
        prompt += f"""

**PFD COMPARISON:**
The PFD analysis for this same system is:
{json.dumps(pfd_analysis, indent=2)}

Additionally, compare the P&ID with the PFD and identify:
1. **Mapping**: How each PFD item maps to P&ID items
2. **Additions**: What new details appear in P&ID (instruments, valves, control loops)
3. **Expansions**: How PFD equipment is expanded with more detail in P&ID
4. **Control Philosophy**: Control strategy evident in P&ID
5. **Safety Layers**: Safety instrumentation and interlocks added
6. **Pattern Recognition**: Typical patterns for converting PFD to P&ID

Include a "pfd_to_pid_mapping" section in your response."""

    try:
        logger.info("🔍 Sending to GPT-4 Vision for P&ID analysis...")
        
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
        logger.info("✅ GPT-4 Vision analysis complete")
        
        # Try to parse as JSON
        try:
            # Find JSON in response
            if '```json' in content:
                json_str = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                json_str = content.split('```')[1].split('```')[0].strip()
            else:
                json_str = content.strip()
            
            analysis = json.loads(json_str)
            return analysis
        except json.JSONDecodeError:
            logger.warning("⚠️ Response not in JSON format, returning as text")
            return {"raw_analysis": content}
            
    except Exception as e:
        logger.error(f"❌ GPT-4 Vision analysis failed: {e}")
        raise


def compare_pfd_and_pid(pfd_analysis, pid_analysis):
    """Deep comparison using GPT-4 to learn conversion patterns"""
    
    prompt = f"""You are an expert in process engineering diagrams. Compare this PFD and P&ID analysis and learn the conversion patterns.

**PFD ANALYSIS:**
{json.dumps(pfd_analysis, indent=2)}

**P&ID ANALYSIS:**
{json.dumps(pid_analysis, indent=2)}

**LEARNING OBJECTIVES:**

1. **EQUIPMENT MAPPING PATTERNS:**
   - How each PFD equipment item is represented in P&ID
   - What additional details are added (nozzles, internals, elevations)
   - Symbol conventions and detail level differences
   
2. **PIPING EXPANSION PATTERNS:**
   - How PFD single lines become detailed P&ID piping
   - Addition of line numbers, sizes, classes, materials
   - Where isolation valves are placed
   - Where control valves are added
   - Typical valve spacing and placement rules
   
3. **INSTRUMENTATION ADDITION PATTERNS:**
   - What instruments are added for each equipment type
   - Typical instrument placement (inlet, outlet, top, bottom)
   - Instrument tag numbering patterns
   - Control loop configuration patterns
   - Alarm and trip point patterns
   
4. **CONTROL STRATEGY PATTERNS:**
   - How process control is implemented
   - Control loop types for different processes
   - Cascade control patterns
   - Ratio control patterns
   - Override control patterns
   
5. **SAFETY SYSTEM PATTERNS:**
   - Where safety valves are placed
   - Emergency shutdown valve placement
   - High/low pressure trip patterns
   - High/low level trip patterns
   - Interlock logic patterns
   
6. **UTILITY INTEGRATION PATTERNS:**
   - How utilities are connected
   - Instrument air distribution
   - Steam tracing patterns
   - Cooling water patterns
   - Drain and vent placement
   
7. **DESIGN STANDARDS AND RULES:**
   - Piping class selection rules
   - Instrument type selection rules
   - Valve type selection rules
   - Symbol conventions
   - Tag numbering schemes
   
8. **CONVERSION RULES:**
   - Step-by-step process to go from PFD to P&ID
   - Decision trees for adding instruments
   - Decision trees for adding valves
   - Control philosophy guidelines
   - Safety requirement guidelines

Return a comprehensive JSON object with:
- "equipment_mapping_rules": Patterns for equipment conversion
- "piping_rules": Patterns for piping detail addition
- "instrumentation_rules": Patterns for instrument addition
- "control_patterns": Control system patterns
- "safety_patterns": Safety system patterns
- "conversion_workflow": Step-by-step conversion process
- "soft_coding_config": Configuration structure for automation

Be extremely detailed - these patterns will be used to automatically generate P&IDs from PFDs."""

    try:
        logger.info("🧠 Learning patterns with GPT-4...")
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "user", "content": prompt}
            ],
            max_tokens=4000,
            temperature=0.1
        )
        
        content = response.choices[0].message.content
        logger.info("✅ Pattern learning complete")
        
        # Parse JSON
        try:
            if '```json' in content:
                json_str = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                json_str = content.split('```')[1].split('```')[0].strip()
            else:
                json_str = content.strip()
            
            patterns = json.loads(json_str)
            return patterns
        except json.JSONDecodeError:
            logger.warning("⚠️ Response not in JSON format, returning as text")
            return {"raw_patterns": content}
            
    except Exception as e:
        logger.error(f"❌ Pattern learning failed: {e}")
        raise


def main():
    """Main execution"""
    
    # File paths
    pid_path = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601\P16093-14-01-08-1602_P&ID.pdf"
    pfd_analysis_path = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend\P16093_PFD_Analysis.json"
    
    output_dir = Path(r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend")
    
    logger.info("=" * 80)
    logger.info("P&ID ANALYSIS AND PATTERN LEARNING SYSTEM")
    logger.info("=" * 80)
    
    # Load PFD analysis
    logger.info(f"\n📂 Loading PFD analysis from: {pfd_analysis_path}")
    with open(pfd_analysis_path, 'r') as f:
        pfd_analysis = json.load(f)
    
    logger.info(f"✅ PFD Analysis loaded:")
    logger.info(f"   - Equipment: {len(pfd_analysis.get('all_equipment', []))}")
    logger.info(f"   - Piping: {len(pfd_analysis.get('all_piping', []))}")
    logger.info(f"   - Instruments: {len(pfd_analysis.get('all_instruments', []))}")
    
    # Convert P&ID to images
    logger.info(f"\n📄 Converting P&ID: {pid_path}")
    images = convert_pdf_to_images(pid_path, dpi=200)
    
    # Analyze P&ID with comparison to PFD
    logger.info(f"\n🔍 Analyzing P&ID with GPT-4 Vision...")
    pid_analysis = analyze_pid_with_vision(images[0]['data'], pfd_analysis)
    
    # Save P&ID analysis
    pid_output = output_dir / "P16093_PID_Analysis.json"
    with open(pid_output, 'w') as f:
        json.dump(pid_analysis, f, indent=2)
    logger.info(f"✅ P&ID analysis saved to: {pid_output}")
    
    # Compare and learn patterns
    logger.info(f"\n🧠 Learning conversion patterns...")
    patterns = compare_pfd_and_pid(pfd_analysis, pid_analysis)
    
    # Save learned patterns
    patterns_output = output_dir / "PFD_to_PID_Conversion_Patterns.json"
    with open(patterns_output, 'w') as f:
        json.dump(patterns, f, indent=2)
    logger.info(f"✅ Conversion patterns saved to: {patterns_output}")
    
    # Print summary
    logger.info("\n" + "=" * 80)
    logger.info("ANALYSIS SUMMARY")
    logger.info("=" * 80)
    
    if isinstance(pid_analysis, dict) and 'raw_analysis' not in pid_analysis:
        logger.info("\n📊 P&ID CONTENT:")
        logger.info(f"   - Equipment: {len(pid_analysis.get('equipment', []))}")
        logger.info(f"   - Piping Lines: {len(pid_analysis.get('piping_lines', []))}")
        logger.info(f"   - Instruments: {len(pid_analysis.get('instruments', []))}")
        logger.info(f"   - Valves: {len(pid_analysis.get('valves', []))}")
        logger.info(f"   - Control Loops: {len(pid_analysis.get('control_loops', []))}")
    
    if isinstance(patterns, dict) and 'raw_patterns' not in patterns:
        logger.info("\n🎯 LEARNED PATTERNS:")
        for key in patterns.keys():
            logger.info(f"   - {key}")
    
    logger.info("\n" + "=" * 80)
    logger.info("✅ ANALYSIS AND PATTERN LEARNING COMPLETE!")
    logger.info("=" * 80)
    
    logger.info("\n📁 Output Files:")
    logger.info(f"   1. {pid_output}")
    logger.info(f"   2. {patterns_output}")


if __name__ == "__main__":
    main()
