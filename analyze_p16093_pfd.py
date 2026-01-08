"""
P16093 PFD Comprehensive Analysis
==================================

Analyzes the P16093_PFD.pdf document to extract:
- All equipment with tags, types, specifications
- All line sizes and piping specifications
- Instrumentation and control systems
- Process flow streams with conditions
- Design parameters and notes
"""

import openai
from openai import OpenAI
from decouple import config
import json
import base64
import fitz  # PyMuPDF
from PIL import Image
import io
import os

# Initialize OpenAI
OPENAI_API_KEY = config('OPENAI_API_KEY', default='')
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def analyze_pfd_comprehensive(pdf_path: str):
    """Comprehensive PFD analysis using GPT-4 Vision"""
    
    print(f"📄 Analyzing PFD: {pdf_path}")
    print("=" * 80)
    
    # Convert PDF to images using PyMuPDF
    print("\n1️⃣ Converting PDF to images...")
    try:
        pdf_document = fitz.open(pdf_path)
        print(f"   ✅ PDF loaded: {pdf_document.page_count} page(s)")
        
        images = []
        for page_num in range(pdf_document.page_count):
            page = pdf_document[page_num]
            # Render page to pixmap (image) at 200 DPI
            pix = page.get_pixmap(matrix=fitz.Matrix(200/72, 200/72))
            # Convert to PIL Image
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
        
        pdf_document.close()
        print(f"   ✅ Converted {len(images)} page(s) to images")
        
    except Exception as e:
        print(f"   ❌ PDF conversion failed: {e}")
        return None
    
    # Analyze each page
    all_analysis = []
    
    for page_num, image in enumerate(images, 1):
        print(f"\n2️⃣ Analyzing Page {page_num}...")
        
        # Save temporary image
        temp_image = f"temp_page_{page_num}.png"
        image.save(temp_image, 'PNG')
        
        # Encode image
        with open(temp_image, 'rb') as img_file:
            image_data = base64.b64encode(img_file.read()).decode('utf-8')
        
        # Analyze with GPT-4 Vision
        analysis = analyze_page_with_vision(image_data, page_num)
        all_analysis.append(analysis)
        
        # Cleanup
        os.remove(temp_image)
    
    # Combine and structure results
    print("\n3️⃣ Structuring comprehensive analysis...")
    comprehensive_report = structure_analysis(all_analysis)
    
    return comprehensive_report


def analyze_page_with_vision(image_data: str, page_num: int):
    """Use GPT-4 Vision to extract detailed information from PFD page"""
    
    prompt = f"""Analyze this Process Flow Diagram (PFD) - Page {page_num} in EXTREME DETAIL.

Extract EVERY piece of information visible on this drawing:

**1. EQUIPMENT ITEMS**
For EACH equipment (vessels, tanks, pumps, heat exchangers, reactors, filters, etc.):
- Equipment Tag (e.g., V-3601-01, P-101A/B, E-201)
- Equipment Type (vessel, pump, heat exchanger, tank, reactor, column, drum, filter, compressor, etc.)
- Equipment Name/Description
- Design Specifications:
  * Design Temperature (°C)
  * Design Pressure (barg, psig)
  * Dimensions (diameter, height, length)
  * Material of Construction
  * Capacity/Duty ratings
  * Any other specifications visible

**2. PIPING & LINE SIZES**
For EACH pipe/line:
- Line Number (e.g., 14-P-101-CS, 16-01-08-1686)
- Line Size (e.g., 2", 4", 6", 8", 12", 16", 20", 24")
- Piping Class/Rating (e.g., 150#, 300#, 600#)
- Piping Material/Specification (e.g., CS - Carbon Steel, SS - Stainless Steel)
- Connected Equipment (from/to tags)
- Flow Direction (if arrows present)

**3. INSTRUMENTATION**
For EACH instrument:
- Instrument Tag (e.g., FT-3601-08A, PT-101, TI-202, LIC-301)
- Instrument Type:
  * F = Flow, P = Pressure, T = Temperature, L = Level, A = Analyzer
  * I = Indicator, T = Transmitter, C = Controller, V = Valve, S = Switch
- Function (measurement, control, alarm)
- Location (on which equipment or line)
- Set points (if visible)
- Control loops and interlocks

**4. PROCESS STREAMS**
For EACH stream/connection:
- Stream Name/Description (e.g., "From Gas Export", "To HP Flare")
- Stream Number (if visible)
- Source equipment/location
- Destination equipment/location
- Process Conditions:
  * Flow rate (if shown)
  * Temperature (°C)
  * Pressure (barg)
  * Composition (if mentioned)

**5. VALVES**
For EACH valve:
- Valve Tag/Number
- Valve Type (gate, ball, globe, check, control, safety, etc.)
- Size
- Actuation (manual, pneumatic, electric, hydraulic)
- Special notes (e.g., MOV, SDV, PSV, relief valve)

**6. SAFETY DEVICES**
- Pressure Safety Valves (PSV) with set pressures
- Rupture discs
- Emergency shutdown valves (SDV, ESV)
- Flame arrestors
- Any safety notes

**7. GENERAL NOTES & SPECIFICATIONS**
- Drawing title and number
- Project information
- Design standards referenced (ASME, API, ISA, etc.)
- Important notes numbered (1, 2, 3, etc.)
- Special requirements or holds
- Material specifications
- Operating conditions

**8. DRAWING METADATA**
- Drawing Number
- Revision
- Date
- Sheet number (e.g., "Sheet 1 of 3")
- Project name
- Client/Company

**OUTPUT FORMAT** (JSON):
```json
{{
  "page": {page_num},
  "drawing_info": {{
    "drawing_number": "",
    "title": "",
    "revision": "",
    "date": "",
    "sheet": "",
    "project": "",
    "client": ""
  }},
  "equipment": [
    {{
      "tag": "V-3601-01",
      "type": "vessel",
      "name": "Sahil Export Gas KOD",
      "specifications": {{
        "design_temp": "55°C/-29°C",
        "design_pressure": "22.4 barg/FV",
        "height": "7800 mm (T/T)",
        "diameter": "3300 mm",
        "material": "CS + SS 316L CLAD"
      }}
    }}
  ],
  "piping_lines": [
    {{
      "line_number": "14-01-08-1602",
      "size": "16 inch",
      "class": "300#",
      "material": "CS",
      "from": "V-3601-01",
      "to": "Gas Export Block Valve Station",
      "description": ""
    }}
  ],
  "instruments": [
    {{
      "tag": "PT-3601-01",
      "type": "pressure_transmitter",
      "location": "V-3601-01 inlet",
      "range": "0-25 barg",
      "function": "pressure measurement"
    }}
  ],
  "streams": [
    {{
      "stream_name": "From Sahil Gas Export Pig Receiver",
      "stream_number": "108",
      "from": "14.01.08.1602",
      "to": "V-3601-01",
      "conditions": {{
        "temperature": "",
        "pressure": "",
        "flow_rate": ""
      }}
    }}
  ],
  "valves": [
    {{
      "tag": "SDV-3601-01",
      "type": "shutdown_valve",
      "size": "16 inch",
      "actuation": "pneumatic",
      "notes": "Emergency shutdown valve"
    }}
  ],
  "safety_devices": [
    {{
      "tag": "PSV-3601-01",
      "type": "pressure_safety_valve",
      "set_pressure": "20 barg",
      "size": "3 inch",
      "discharge_to": "HP Flare"
    }}
  ],
  "notes": [
    {{
      "number": 1,
      "text": "All equipment prefixed by area code (14) and plant area code (01)"
    }}
  ]
}}
```

Be EXTREMELY thorough. Extract EVERY visible detail, number, specification, and note."""

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
        
        # Parse JSON
        if '```json' in content:
            json_start = content.find('```json') + 7
            json_end = content.find('```', json_start)
            json_str = content[json_start:json_end].strip()
        elif '```' in content:
            json_start = content.find('```') + 3
            json_end = content.find('```', json_start)
            json_str = content[json_start:json_end].strip()
        else:
            json_str = content.strip()
        
        analysis = json.loads(json_str)
        
        print(f"   ✅ Page {page_num} analyzed:")
        print(f"      - Equipment: {len(analysis.get('equipment', []))}")
        print(f"      - Piping Lines: {len(analysis.get('piping_lines', []))}")
        print(f"      - Instruments: {len(analysis.get('instruments', []))}")
        print(f"      - Streams: {len(analysis.get('streams', []))}")
        print(f"      - Valves: {len(analysis.get('valves', []))}")
        print(f"      - Safety Devices: {len(analysis.get('safety_devices', []))}")
        
        return analysis
        
    except Exception as e:
        print(f"   ❌ Analysis failed: {e}")
        return {}


def structure_analysis(all_pages):
    """Combine multi-page analysis into structured report"""
    
    report = {
        "document": "P16093_PFD.pdf",
        "total_pages": len(all_pages),
        "drawing_info": {},
        "equipment_summary": {},
        "piping_summary": {},
        "instrumentation_summary": {},
        "all_equipment": [],
        "all_piping": [],
        "all_instruments": [],
        "all_streams": [],
        "all_valves": [],
        "all_safety_devices": [],
        "all_notes": []
    }
    
    # Combine data from all pages
    for page_data in all_pages:
        if not page_data:
            continue
            
        # Drawing info (from first page)
        if not report["drawing_info"] and page_data.get("drawing_info"):
            report["drawing_info"] = page_data["drawing_info"]
        
        # Collect all items
        report["all_equipment"].extend(page_data.get("equipment", []))
        report["all_piping"].extend(page_data.get("piping_lines", []))
        report["all_instruments"].extend(page_data.get("instruments", []))
        report["all_streams"].extend(page_data.get("streams", []))
        report["all_valves"].extend(page_data.get("valves", []))
        report["all_safety_devices"].extend(page_data.get("safety_devices", []))
        report["all_notes"].extend(page_data.get("notes", []))
    
    # Create summaries
    report["equipment_summary"] = {
        "total_count": len(report["all_equipment"]),
        "by_type": {}
    }
    
    for eq in report["all_equipment"]:
        eq_type = eq.get("type", "unknown")
        report["equipment_summary"]["by_type"][eq_type] = \
            report["equipment_summary"]["by_type"].get(eq_type, 0) + 1
    
    report["piping_summary"] = {
        "total_lines": len(report["all_piping"]),
        "line_sizes": list(set([p.get("size", "") for p in report["all_piping"] if p.get("size")])),
        "piping_classes": list(set([p.get("class", "") for p in report["all_piping"] if p.get("class")]))
    }
    
    report["instrumentation_summary"] = {
        "total_instruments": len(report["all_instruments"]),
        "by_type": {}
    }
    
    for inst in report["all_instruments"]:
        inst_type = inst.get("type", "unknown")
        report["instrumentation_summary"]["by_type"][inst_type] = \
            report["instrumentation_summary"]["by_type"].get(inst_type, 0) + 1
    
    return report


def print_comprehensive_report(report):
    """Print human-readable comprehensive report"""
    
    print("\n" + "=" * 80)
    print("📊 COMPREHENSIVE PFD ANALYSIS REPORT")
    print("=" * 80)
    
    # Drawing Info
    print("\n📋 DRAWING INFORMATION:")
    print("-" * 80)
    for key, value in report.get("drawing_info", {}).items():
        if value:
            print(f"  {key.replace('_', ' ').title()}: {value}")
    
    # Equipment Summary
    print("\n⚙️  EQUIPMENT SUMMARY:")
    print("-" * 80)
    print(f"  Total Equipment Items: {report['equipment_summary']['total_count']}")
    print(f"\n  By Type:")
    for eq_type, count in report['equipment_summary']['by_type'].items():
        print(f"    - {eq_type.replace('_', ' ').title()}: {count}")
    
    # Equipment Details
    print("\n⚙️  EQUIPMENT DETAILS:")
    print("-" * 80)
    for eq in report["all_equipment"]:
        print(f"\n  🔹 {eq.get('tag', 'N/A')} - {eq.get('type', 'N/A').replace('_', ' ').title()}")
        if eq.get('name'):
            print(f"     Name: {eq['name']}")
        if eq.get('specifications'):
            print(f"     Specifications:")
            for spec_key, spec_val in eq['specifications'].items():
                if spec_val:
                    print(f"       • {spec_key.replace('_', ' ').title()}: {spec_val}")
    
    # Piping Summary
    print("\n🔧 PIPING SUMMARY:")
    print("-" * 80)
    print(f"  Total Piping Lines: {report['piping_summary']['total_lines']}")
    print(f"  Line Sizes: {', '.join(sorted(report['piping_summary']['line_sizes']))}")
    print(f"  Piping Classes: {', '.join(sorted(report['piping_summary']['piping_classes']))}")
    
    # Piping Details
    print("\n🔧 PIPING DETAILS:")
    print("-" * 80)
    for pipe in report["all_piping"]:
        print(f"\n  🔹 Line: {pipe.get('line_number', 'N/A')}")
        print(f"     Size: {pipe.get('size', 'N/A')}")
        print(f"     Class: {pipe.get('class', 'N/A')}")
        print(f"     Material: {pipe.get('material', 'N/A')}")
        print(f"     From: {pipe.get('from', 'N/A')} → To: {pipe.get('to', 'N/A')}")
        if pipe.get('description'):
            print(f"     Description: {pipe['description']}")
    
    # Instrumentation Summary
    print("\n🎛️  INSTRUMENTATION SUMMARY:")
    print("-" * 80)
    print(f"  Total Instruments: {report['instrumentation_summary']['total_instruments']}")
    print(f"\n  By Type:")
    for inst_type, count in report['instrumentation_summary']['by_type'].items():
        print(f"    - {inst_type.replace('_', ' ').title()}: {count}")
    
    # Instrumentation Details
    print("\n🎛️  INSTRUMENTATION DETAILS:")
    print("-" * 80)
    for inst in report["all_instruments"]:
        print(f"\n  🔹 {inst.get('tag', 'N/A')} - {inst.get('type', 'N/A').replace('_', ' ').title()}")
        print(f"     Location: {inst.get('location', 'N/A')}")
        print(f"     Function: {inst.get('function', 'N/A')}")
        if inst.get('range'):
            print(f"     Range: {inst['range']}")
    
    # Process Streams
    print("\n🌊 PROCESS STREAMS:")
    print("-" * 80)
    for stream in report["all_streams"]:
        print(f"\n  🔹 {stream.get('stream_name', 'N/A')}")
        if stream.get('stream_number'):
            print(f"     Stream Number: {stream['stream_number']}")
        print(f"     From: {stream.get('from', 'N/A')} → To: {stream.get('to', 'N/A')}")
        if stream.get('conditions'):
            print(f"     Conditions:")
            for cond_key, cond_val in stream['conditions'].items():
                if cond_val:
                    print(f"       • {cond_key.replace('_', ' ').title()}: {cond_val}")
    
    # Valves
    print("\n🚰 VALVES:")
    print("-" * 80)
    for valve in report["all_valves"]:
        print(f"\n  🔹 {valve.get('tag', 'N/A')} - {valve.get('type', 'N/A').replace('_', ' ').title()}")
        print(f"     Size: {valve.get('size', 'N/A')}")
        print(f"     Actuation: {valve.get('actuation', 'N/A')}")
        if valve.get('notes'):
            print(f"     Notes: {valve['notes']}")
    
    # Safety Devices
    print("\n⚠️  SAFETY DEVICES:")
    print("-" * 80)
    for safety in report["all_safety_devices"]:
        print(f"\n  🔹 {safety.get('tag', 'N/A')} - {safety.get('type', 'N/A').replace('_', ' ').title()}")
        if safety.get('set_pressure'):
            print(f"     Set Pressure: {safety['set_pressure']}")
        if safety.get('size'):
            print(f"     Size: {safety['size']}")
        if safety.get('discharge_to'):
            print(f"     Discharge To: {safety['discharge_to']}")
    
    # Notes
    print("\n📝 DRAWING NOTES:")
    print("-" * 80)
    for note in report["all_notes"]:
        print(f"\n  {note.get('number', '•')}. {note.get('text', 'N/A')}")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    pdf_path = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601\P16093_PFD.pdf"
    
    if not client:
        print("❌ OpenAI API key not configured!")
        print("   Please set OPENAI_API_KEY in your .env file")
        exit(1)
    
    # Run comprehensive analysis
    report = analyze_pfd_comprehensive(pdf_path)
    
    if report:
        # Print report to console
        print_comprehensive_report(report)
        
        # Save to JSON file
        output_file = "P16093_PFD_Analysis.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"\n💾 Full analysis saved to: {output_file}")
        print("\n✅ Analysis complete!")
    else:
        print("\n❌ Analysis failed!")
