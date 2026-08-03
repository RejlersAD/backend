"""
Reference Document Intelligence Processor
Extracts and structures data from reference documents for P&ID cross-verification
SOFT-CODED: AI-powered extraction from equipment datasheets, legends, standards
"""

import base64
import io
from typing import Dict, List, Any
import fitz  # PyMuPDF
from openai import OpenAI
import os


class ReferenceDocumentProcessor:
    """AI-powered processor for reference documents"""
    
    def __init__(self):
        """Initialize OpenAI client"""
        api_key = os.getenv('OPENAI_API_KEY')
        if api_key:
            self.client = OpenAI(api_key=api_key, timeout=120.0)
        else:
            self.client = None
            print("[WARNING] OpenAI API key not found - reference processing will be limited")
    
    def process_reference_documents(self, documents: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process uploaded reference documents and extract structured data
        
        Args:
            documents: Dict of {doc_type: Django FieldFile object or file path}
        
        Returns:
            Structured data extracted from all reference documents
        """
        extracted_data = {}
        
        for doc_type, doc_file in documents.items():
            try:
                print(f"[INFO] Processing reference document: {doc_type}")
                
                # NEW: Equipment List - Structured equipment data
                if doc_type in ['equipment_list']:
                    extracted_data['equipment_list'] = self._extract_equipment_list(doc_file)
                
                # NEW: Critical Stress Line List - for stress-critical piping verification
                elif doc_type in ['critical_stress_list']:
                    extracted_data['critical_stress_list'] = self._extract_critical_stress_list(doc_file)

                # NEW: Line List - Structured piping data
                elif doc_type in ['line_list']:
                    extracted_data['line_list'] = self._extract_line_list(doc_file)
                
                # NEW: Alarm & Trip Schedule - Setpoints reference
                elif doc_type in ['alarm_trip_schedule']:
                    extracted_data['alarm_trip_schedule'] = self._extract_alarm_trip_schedule(doc_file)
                
                # NEW: Legend / Symbol Sheet - Symbol and spec interpretation
                elif doc_type in ['legend_symbols', 'legends_symbols']:
                    extracted_data['legend_symbols'] = self._extract_legend_symbols(doc_file)
                
            except Exception as e:
                print(f"[ERROR] Failed to process {doc_type}: {e}")
                continue
        
        return extracted_data
    
    def _extract_equipment_specs(self, doc_file) -> Dict[str, Any]:
        """Extract equipment specifications using AI
        
        Args:
            doc_file: Django FieldFile object or file path string
        """
        if not self.client:
            return {}
        
        try:
            # Convert PDF to images
            images = self._pdf_to_images(doc_file)
            if not images:
                return {}
            
            # AI extraction prompt
            prompt = """Extract ALL equipment specifications from this datasheet:

**REQUIRED INFORMATION:**
- Equipment Tag Numbers
- Equipment Type (Vessel, Tank, Heat Exchanger, Pump, Compressor, etc.)
- Design Pressure (barg / psig)
- Design Temperature (°C / °F)
- Operating Pressure
- Operating Temperature
- Material of Construction (MOC)
- Nozzle Sizes and Ratings
- Pipe Class at connections
- Trim Class requirements
- PSV set pressures (if applicable)
- Special requirements (insulation, heating, coating, etc.)

**OUTPUT FORMAT - JSON:**
{
    "equipment": [
        {
            "tag": "V-101",
            "type": "Pressure Vessel",
            "design_pressure": "50 barg",
            "design_temp": "200°C",
            "operating_pressure": "45 barg",
            "operating_temp": "180°C",
            "moc": "CS + SS316L clad",
            "nozzles": [
                {"size": "6 inch", "rating": "Class 300", "service": "Inlet"},
                {"size": "4 inch", "rating": "Class 300", "service": "Outlet"}
            ],
            "pipe_class": "300#",
            "trim_class": "IV",
            "psv_setpoint": "52 barg",
            "special_requirements": ["Steam traced", "Insulated"]
        }
    ]
}"""

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert mechanical engineer analyzing equipment datasheets."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{images[0]}"}}
                    ]}
                ],
                temperature=0.1,
                max_tokens=3000
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            return result
        
        except Exception as e:
            print(f"[ERROR] Equipment spec extraction failed: {e}")
            return {}
    
    def _extract_instrument_specs(self, doc_file) -> Dict[str, Any]:
        """Extract instrument specifications
        
        Args:
            doc_file: Django FieldFile object or file path string
        """
        if not self.client:
            return {}
        
        try:
            images = self._pdf_to_images(doc_file)
            if not images:
                return {}
            
            prompt = """Extract ALL instrument specifications:

**REQUIRED:**
- Instrument Tag (e.g., TT-101, PT-202, FIC-303)
- Instrument Type (Transmitter, Controller, Indicator, Switch, Valve)
- Range and Units
- Fail-Safe Position (FC=Fail Closed, FO=Fail Open, FL=Fail Last)
- Process Connection Size
- Signal Type (4-20mA, HART, Fieldbus, Pneumatic)
- Calibrated Range
- Alarm Setpoints (High/Low)
- Trip Setpoints
- Accuracy requirements
- Special requirements

OUTPUT JSON:
{
    "instruments": [
        {
            "tag": "PT-101",
            "type": "Pressure Transmitter",
            "range": "0-100 barg",
            "fail_safe": "N/A (monitoring only)",
            "connection": "1/2 inch NPT",
            "signal": "4-20mA HART",
            "alarm_high": "90 barg",
            "trip_high": "95 barg"
        }
    ]
}"""

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an instrumentation engineer."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{images[0]}"}}
                    ]}
                ],
                temperature=0.1,
                max_tokens=3000
            )
            
            import json
            return json.loads(response.choices[0].message.content)
        
        except Exception as e:
            print(f"[ERROR] Instrument spec extraction failed: {e}")
            return {}
    
    def _extract_legends(self, doc_file) -> Dict[str, Any]:
        """Extract legend symbols and definitions
        
        Args:
            doc_file: Django FieldFile object or file path string
        """
        # Basic text extraction
        try:
            # Handle both Django FieldFile and string paths
            pdf_path = doc_file.path if hasattr(doc_file, 'path') else str(doc_file)
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            
            return {
                "symbols_defined": text,
                "abbreviations": self._parse_abbreviations(text)
            }
        except Exception as e:
            print(f"[ERROR] Legend extraction failed: {e}")
            return {}
    
    def _extract_standards(self, doc_file) -> Dict[str, Any]:
        """Extract P&ID standards and requirements
        
        Args:
            doc_file: Django FieldFile object or file path string
        """
        try:
            pdf_path = doc_file.path if hasattr(doc_file, 'path') else str(doc_file)
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            
            return {
                "standards_text": text,
                "key_requirements": self._parse_standards(text)
            }
        except Exception as e:
            print(f"[ERROR] Standards extraction failed: {e}")
            return {}
    
    def _extract_safety_requirements(self, doc_file) -> Dict[str, Any]:
        """Extract safety requirements (SIL, HAZOP)
        
        Args:
            doc_file: Django FieldFile object or file path string
        """
        try:
            pdf_path = doc_file.path if hasattr(doc_file, 'path') else str(doc_file)
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            
            return {
                "safety_text": text,
                "sil_requirements": self._parse_sil_requirements(text),
                "hazop_actions": self._parse_hazop_actions(text)
            }
        except Exception as e:
            print(f"[ERROR] Safety requirements extraction failed: {e}")
            return {}
    
    def _extract_process_conditions(self, doc_file) -> Dict[str, Any]:
        """Extract process description and operating conditions
        
        Args:
            doc_file: Django FieldFile object or file path string
        """
        try:
            pdf_path = doc_file.path if hasattr(doc_file, 'path') else str(doc_file)
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            
            return {
                "process_description": text[:2000],  # First 2000 chars
                "operating_conditions": self._parse_operating_conditions(text)
            }
        except Exception as e:
            print(f"[ERROR] Process description extraction failed: {e}")
            return {}
    
    def _extract_iso_standards(self, doc_file) -> Dict[str, Any]:
        """Extract ISO standards requirements
        
        Args:
            doc_file: Django FieldFile object or file path string
        """
        try:
            pdf_path = doc_file.path if hasattr(doc_file, 'path') else str(doc_file)
            doc = fitz.open(pdf_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            
            return {
                "iso_text": text,
                "compliance_requirements": text[:1500]
            }
        except Exception as e:
            print(f"[ERROR] ISO standards extraction failed: {e}")
            return {}
    
    def _pdf_to_images(self, pdf_file) -> List[str]:
        """Convert PDF first page to base64 image
        
        Args:
            pdf_file: Django FieldFile object or file path string
        """
        try:
            # Handle both Django FieldFile and string paths
            if isinstance(pdf_file, str):
                pdf_path = pdf_file
            elif hasattr(pdf_file, 'path'):
                # Django FieldFile
                pdf_path = pdf_file.path
            else:
                # Try to get path attribute
                pdf_path = str(pdf_file)
            
            doc = fitz.open(pdf_path)
            page = doc[0]  # First page only
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom
            img_data = pix.tobytes("png")
            base64_img = base64.b64encode(img_data).decode('utf-8')
            doc.close()
            return [base64_img]
        except Exception as e:
            print(f"[ERROR] PDF to image conversion failed: {e}")
            return []
    
    def _parse_abbreviations(self, text: str) -> Dict[str, str]:
        """Parse abbreviations from legend text"""
        # Simple pattern matching for abbreviations
        abbreviations = {}
        lines = text.split('\n')
        for line in lines:
            if '-' in line or ':' in line:
                parts = line.replace(':', '-').split('-', 1)
                if len(parts) == 2:
                    abbr = parts[0].strip()
                    meaning = parts[1].strip()
                    if len(abbr) <= 10 and len(meaning) > 2:
                        abbreviations[abbr] = meaning
        return abbreviations
    
    def _parse_standards(self, text: str) -> List[str]:
        """Extract key requirements from standards"""
        requirements = []
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if any(keyword in line.lower() for keyword in ['shall', 'must', 'required', 'mandatory']):
                if len(line) > 20 and len(line) < 200:
                    requirements.append(line)
        return requirements[:20]  # Top 20 requirements
    
    def _parse_sil_requirements(self, text: str) -> List[Dict[str, Any]]:
        """Parse SIL requirements"""
        # Look for SIL ratings and associated equipment
        import re
        sil_items = []
        lines = text.split('\n')
        for line in lines:
            if 'SIL' in line.upper():
                sil_items.append({'text': line.strip()})
        return sil_items
    
    def _parse_hazop_actions(self, text: str) -> List[str]:
        """Parse HAZOP actions"""
        actions = []
        lines = text.split('\n')
        for line in lines:
            if any(word in line.lower() for word in ['action', 'recommendation', 'shall', 'install', 'provide']):
                if len(line) > 20:
                    actions.append(line.strip())
        return actions[:15]
    
    def _parse_operating_conditions(self, text: str) -> Dict[str, Any]:
        """Parse operating conditions"""
        import re
        conditions = {}
        
        # Look for pressure mentions
        pressure_pattern = r'(\d+\.?\d*)\s*(barg|psig|bar|psi)'
        pressure_matches = re.findall(pressure_pattern, text, re.IGNORECASE)
        if pressure_matches:
            conditions['pressures'] = [f"{p[0]} {p[1]}" for p in pressure_matches[:5]]
        
        # Look for temperature mentions
        temp_pattern = r'(\d+\.?\d*)\s*(°C|°F|C|deg)'
        temp_matches = re.findall(temp_pattern, text, re.IGNORECASE)
        if temp_matches:
            conditions['temperatures'] = [f"{t[0]} {t[1]}" for t in temp_matches[:5]]
        
        return conditions    
    
    def _extract_equipment_list(self, doc_file) -> Dict[str, Any]:
        """
        Extract Equipment List data for P&ID cross-verification
        Equipment List contains structured equipment data: Tag, Type, Design P/T, Size, etc.
        """
        if not self.client:
            return {}
        
        try:
            images = self._pdf_to_images(doc_file)
            if not images:
                return {}
            
            prompt = """Extract ALL equipment from this Equipment List document.

**REQUIRED INFORMATION:**
- Equipment Tag Numbers (exact as shown)
- Equipment Type/Description
- Design Pressure and Temperature  
- Operating Pressure and Temperature
- Equipment Size/Capacity
- Material of Construction (MOC)
- Any special requirements or notes

**OUTPUT FORMAT - JSON:**
{
    "equipment": [
        {
            "tag": "V-101",
            "description": "Separation Vessel",
            "type": "Vertical Pressure Vessel",
            "design_pressure": "50 barg",
            "design_temp": "200°C",
            "operating_pressure": "40 barg",
            "operating_temp": "180°C",
            "size": "2.5m ID x 6m TT",
            "moc": "CS with SS316L clad",
            "notes": "Steam traced, insulated"
        }
    ]
}

Extract ALL equipment accurately."""

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a mechanical engineer extracting equipment data from Equipment Lists."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{images[0]}"}}
                    ]}
                ],
                temperature=0.1,
                max_tokens=4000
            )
            
            import json
            import re
            result = response.choices[0].message.content
            json_match = re.search(r'```json\s*(.*?)\s*```', result, re.DOTALL)
            if json_match:
                result = json_match.group(1)
            return json.loads(result)
        
        except Exception as e:
            print(f"[ERROR] Equipment List extraction failed: {e}")
            return {}
    
    def _extract_line_list(self, doc_file) -> Dict[str, Any]:
        """
        Extract Line List data for P&ID cross-verification
        Line List contains piping data: Line numbers, sizes, specs, from/to, etc.
        """
        if not self.client:
            return {}
        
        try:
            images = self._pdf_to_images(doc_file)
            if not images:
                return {}
            
            prompt = """Extract ALL piping lines from this Line List document.

**REQUIRED INFORMATION:**
- Line Number (complete as shown, e.g., 2"-D-6155-033842-X-N)
- Line Size (pipe diameter)
- Pipe Spec/Class (e.g., CS150, 316SS, 300#)
- Fluid Service (what flows in the pipe)
- From (source equipment/location)
- To (destination equipment/location)
- Design Pressure and Temperature
- Operating Pressure and Temperature
- Insulation/Tracing requirements
- Any special notes

**OUTPUT FORMAT - JSON:**
{
    "lines": [
        {
            "line_number": "2\\"-D-6155-033842-X-N",
            "size": "2 inch",
            "spec": "CS150",
            "fluid": "Diesel",
            "from": "V-101",
            "to": "P-201",
            "design_pressure": "16 barg",
            "design_temp": "120°C",
            "operating_pressure": "10 barg",
            "operating_temp": "80°C",
            "insulation": "Yes",
            "tracing": "Heat traced",
            "notes": "Sloped 1:100 for drainage"
        }
    ]
}

Extract ALL line numbers accurately. Line numbers are CRITICAL for verification."""

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a piping engineer extracting line data from Line Lists."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{images[0]}"}}
                    ]}
                ],
                temperature=0.1,
                max_tokens=4000
            )
            
            import json
            import re
            result = response.choices[0].message.content
            json_match = re.search(r'```json\s*(.*?)\s*```', result, re.DOTALL)
            if json_match:
                result = json_match.group(1)
            return json.loads(result)
        
        except Exception as e:
            print(f"[ERROR] Line List extraction failed: {e}")
            return {}

    def _extract_critical_stress_list(self, doc_file) -> Dict[str, Any]:
        """
        Extract Critical Stress Line List for stress-analysis and piping flexibility verification.
        These lines need anchor points, guides, and expansion provisions on the P&ID.
        """
        if not self.client:
            return {}

        try:
            images = self._pdf_to_images(doc_file)
            if not images:
                return {}

            prompt = """Extract ALL critical stress (CSS) piping lines from this document.

**REQUIRED INFORMATION:**
- Line Number (complete, e.g., 4"-HP-6155-033900-A-H)
- Line Size (pipe diameter, e.g., 4 inch)
- Stress Class / CSS Class (e.g., CSS-1, CSS-2, CRITICAL)
- Fluid Service (e.g., High Pressure Steam, Hot Oil, Condensate)
- Design Pressure (barg)
- Design Temperature (°C) — high temp is the primary stress driver
- Material (e.g., P91, P22, SS316, CS)
- Rating (pressure class, e.g., Class 600, ANSI 900#)
- Any special notes (e.g., PWHT required, expansion loop required, cold spring)

**OUTPUT FORMAT - JSON:**
{
    "lines": [
        {
            "line_number": "4\\"-HP-6155-033900-A-H",
            "size": "4 inch",
            "stress_class": "CSS-1",
            "service": "High Pressure Steam",
            "design_pressure": "60 barg",
            "design_temp": "480°C",
            "material": "P91",
            "rating": "Class 900",
            "notes": "Expansion loop required — PWHT mandatory"
        }
    ]
}

Extract ALL stress-critical line numbers carefully. These are safety-critical for flexibility analysis."""

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a piping stress engineer extracting critical stress line data from engineering documents."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{images[0]}"}}
                    ]}
                ],
                temperature=0.1,
                max_tokens=4000
            )

            import json
            import re
            result = response.choices[0].message.content
            json_match = re.search(r'```json\s*(.*?)\s*```', result, re.DOTALL)
            if json_match:
                result = json_match.group(1)
            return json.loads(result)

        except Exception as e:
            print(f"[ERROR] Critical Stress List extraction failed: {e}")
            return {}

    def _extract_alarm_trip_schedule(self, doc_file) -> Dict[str, Any]:
        """
        Extract Alarm & Trip Schedule for instrument setpoint verification
        Contains alarm/trip setpoints for all instruments
        """
        if not self.client:
            return {}
        
        try:
            images = self._pdf_to_images(doc_file)
            if not images:
                return {}
            
            prompt = """Extract ALL alarm and trip setpoints from this Alarm & Trip Schedule.

**REQUIRED INFORMATION:**
- Instrument Tag (PT-101, TT-202, LT-303, etc.)
- Process Variable (Pressure, Temperature, Level, Flow, etc.)
- Units (barg, °C, %, m³/h, etc.)
- Alarm Setpoints:
  * LL (Low Low Alarm)
  * L (Low Alarm)
  * H (High Alarm)
  * HH (High High Alarm)
- Trip Setpoints:
  * LL (Low Low Trip)
  * HH (High High Trip)
- Trip Action (Shutdown, Isolation, etc.)

**OUTPUT FORMAT - JSON:**
{
    "alarms_trips": [
        {
            "tag": "PT-101",
            "parameter": "Pressure",
            "units": "barg",
            "alarm_ll": "5",
            "alarm_l": "10",
            "alarm_h": "45",
            "alarm_hh": "48",
            "trip_ll": "3",
            "trip_hh": "50",
            "trip_action": "Shutdown feed pump"
        },
        {
            "tag": "TT-202",
            "parameter": "Temperature",
            "units": "°C",
            "alarm_l": "150",
            "alarm_h": "200",
            "trip_hh": "210",
            "trip_action": "Emergency cooling activation"
        }
    ]
}

Extract ALL setpoints accurately. These will be verified against P&ID."""

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an instrumentation engineer extracting alarm/trip data."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{images[0]}"}}
                    ]}
                ],
                temperature=0.1,
                max_tokens=4000
            )
            
            import json
            import re
            result = response.choices[0].message.content
            json_match = re.search(r'```json\s*(.*?)\s*```', result, re.DOTALL)
            if json_match:
                result = json_match.group(1)
            return json.loads(result)
        
        except Exception as e:
            print(f"[ERROR] Alarm/Trip Schedule extraction failed: {e}")
            return {}
    
    def _extract_legend_symbols(self, doc_file) -> Dict[str, Any]:
        """
        Extract Legend / Symbol Sheet for symbol and specification interpretation
        Contains symbol definitions, abbreviations, pipe specs, line numbering system
        """
        if not self.client:
            return {}
        
        try:
            images = self._pdf_to_images(doc_file)
            if not images:
                return {}
            
            prompt = """Extract ALL symbol definitions, abbreviations, and specifications from this Legend/Symbol Sheet.

**REQUIRED INFORMATION:**

1️⃣ **SYMBOLS:**
   - Equipment symbols (vessels, pumps, valves, etc.)
   - Instrument symbols (transmitters, controllers, etc.)
   - Valve symbols and their meanings
   - Line symbols (process, utility, signal, etc.)

2️⃣ **ABBREVIATIONS:**
   - Fluid codes (D=Diesel, VG=Vapor Gas, HC=Hydrocarbon, etc.)
   - Equipment prefixes (V=Vessel, P=Pump, E=Exchanger, etc.)
   - Specification codes (CS=Carbon Steel, SS=Stainless Steel, etc.)

3️⃣ **PIPE SPECIFICATIONS:**
   - Pipe class definitions (150#, 300#, 600#)
   - Material specifications
   - Rating tables

4️⃣ **LINE NUMBERING SYSTEM:**
   - Line number format explanation
   - Serial number ranges
   - Specification codes

5️⃣ **NOTES AND STANDARDS:**
   - Drawing standards references (AGES-GL-08-005, etc.)
   - General notes
   - Special requirements

**OUTPUT FORMAT - JSON:**
{
    "symbols": [
        {"symbol": "Check Valve", "description": "Flow direction indicated by symbol orientation"},
        {"symbol": "Control Valve", "description": "Fail position shown (FC/FO/FL)"}
    ],
    "abbreviations": {
        "D": "Diesel",
        "VG": "Vapor Gas",
        "HC": "Hydrocarbon",
        "V": "Vessel",
        "P": "Pump",
        "CS": "Carbon Steel",
        "SS": "Stainless Steel"
    },
    "pipe_specs": [
        {"class": "CS150", "description": "Carbon Steel 150# ANSI"},
        {"class": "316SS", "description": "Stainless Steel 316"}
    ],
    "line_numbering": {
        "format": "[SIZE]-[FLUID]-[SEQUENCE]-[SPEC]",
        "example": "2\\"-D-6155-033842-X-N",
        "serial_range": "Up to 9600"
    },
    "standards_references": [
        "AGES-GL-08-005, Rev B4: Control valve manifold sizing",
        "AGES-PH-04-001, Rev-1, Table 14.1: Thermowell sizes"
    ],
    "notes": [
        "All dimensions in millimeters unless noted",
        "All pressures in barg unless noted"
    ]
}

Extract ALL information. This is the key reference for P&ID interpretation."""

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a senior P&ID engineer extracting legend and symbol data."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        *[{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}} 
                          for img in images[:2]]  # First 2 pages for legend
                    ]}
                ],
                temperature=0.1,
                max_tokens=4000
            )
            
            import json
            import re
            result = response.choices[0].message.content
            json_match = re.search(r'```json\s*(.*?)\s*```', result, re.DOTALL)
            if json_match:
                result = json_match.group(1)
            return json.loads(result)
        
        except Exception as e:
            print(f"[ERROR] Legend/Symbol Sheet extraction failed: {e}")
            return {}
    
    def _extract_pfd_data(self, doc_file) -> Dict[str, Any]:
        """
        Extract PFD (Process Flow Diagram) data for P&ID cross-verification
        SOFT-CODED: P&IDs are generated from PFDs - extract process flow for quality check
        
        Args:
            doc_file: Django FieldFile object or file path string
        """
        if not self.client:
            return {}
        
        try:
            # Convert PDF to images
            images = self._pdf_to_images(doc_file)
            if not images:
                return {}
            
            # AI extraction prompt for PFD analysis
            prompt = """Analyze this PFD (Process Flow Diagram) and extract ALL process flow information.

**CRITICAL - P&ID QUALITY CHECK:**
P&IDs are generated from PFDs. Extract the following to verify P&ID accuracy:

**REQUIRED INFORMATION:**

1️⃣ **MAJOR EQUIPMENT (Vessels, Pumps, Compressors, Exchangers)**
   - Equipment Tag Numbers (V-101, P-201, C-301, E-401, etc.)
   - Equipment Type and Function
   - Design Pressure and Temperature
   - Operating Conditions
   - Material of Construction
   - Equipment Capacity/Size
   - CRITICAL: ALL equipment shown on PFD MUST appear on P&ID

2️⃣ **PROCESS STREAMS / LINE NUMBERS**
   - Stream Numbers or Line Tags
   - Stream Composition (what fluid/gas)
   - Operating Pressure (barg/psig)
   - Operating Temperature (°C/°F)
   - Flow Rate (kg/h, m³/h, etc.)
   - Stream Source (from equipment or feed)
   - Stream Destination (to equipment or product)
   - CRITICAL: Stream connectivity must match between PFD and P&ID

3️⃣ **PROCESS CONDITIONS**
   - Normal Operating Pressure (NOP)
   - Normal Operating Temperature (NOT)
   - Maximum Design Pressure
   - Maximum Design Temperature
   - Flow rates and compositions
   - CRITICAL: Conditions on P&ID must match PFD

4️⃣ **MATERIAL BALANCE**
   - Input stream quantities
   - Output stream quantities
   - Material balance closure
   - CRITICAL: Check if P&ID supports material balance

5️⃣ **CONTROL PHILOSOPHY (if shown)**
   - Control loops indicated on PFD
   - Set points and ranges
   - Critical parameters controlled
   - CRITICAL: P&ID must implement all control loops shown on PFD

6️⃣ **SAFETY INSTRUMENTATION**
   - Pressure Relief Valves (PSV) shown
   - Safety interlocks indicated
   - Emergency shutdown systems
   - CRITICAL: All safety devices on PFD must be on P&ID

7️⃣ **UTILITY SYSTEMS**
   - Cooling water supplies
   - Steam supplies
   - Instrument air
   - Nitrogen purge
   - CRITICAL: Utility connections must match

**OUTPUT FORMAT - JSON:**
{
    "equipment": [
        {
            "tag": "V-101",
            "type": "Separation Vessel",
            "design_pressure": "50 barg",
            "design_temp": "200°C",
            "operating_pressure": "40 barg",
            "operating_temp": "180°C",
            "moc": "Carbon Steel",
            "function": "3-phase separator"
        }
    ],
    "streams": [
        {
            "stream_id": "101",
            "description": "Feed to separator",
            "source": "Feed pump P-101",
            "destination": "Vessel V-101",
            "pressure": "42 barg",
            "temperature": "180°C",
            "flow_rate": "50 m³/h",
            "composition": "Oil/Water/Gas"
        }
    ],
    "process_conditions": {
        "operating_pressure_range": "35-45 barg",
        "operating_temp_range": "170-190°C",
        "design_margins": "Safety factor applied"
    },
    "control_loops": [
        {
            "parameter": "Pressure Control",
            "location": "V-101 outlet",
            "type": "PIC",
            "setpoint": "40 barg"
        }
    ],
    "safety_systems": [
        {
            "device": "PSV-101",
            "location": "V-101 vapor space",
            "set_pressure": "48 barg",
            "purpose": "Overpressure protection"
        }
    ],
    "utilities": [
        {
            "utility": "Cooling Water",
            "connected_equipment": "E-201",
            "supply_pressure": "6 barg"
        }
    ],
    "notes": [
        "Any special requirements or notes from PFD"
    ]
}

Extract ALL visible information accurately. This will be used for P&ID quality verification."""

            # Make AI call with first few pages of PFD
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a Senior Process Engineer analyzing PFDs for P&ID cross-verification. Extract complete and accurate process flow data."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            *[{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}} 
                              for img in images[:3]]  # First 3 pages
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            result = response.choices[0].message.content
            
            # Try to parse JSON response
            import json
            import re
            
            # Extract JSON if wrapped in markdown
            json_match = re.search(r'```json\s*(.*?)\s*```', result, re.DOTALL)
            if json_match:
                result = json_match.group(1)
            
            try:
                pfd_data = json.loads(result)
                print(f"[INFO] Extracted PFD data: {len(pfd_data.get('equipment', []))} equipment, "
                      f"{len(pfd_data.get('streams', []))} streams")
                return pfd_data
            except json.JSONDecodeError:
                print(f"[WARNING] Could not parse PFD JSON response, using text extraction")
                return {"raw_text": result}
        
        except Exception as e:
            print(f"[ERROR] PFD extraction failed: {e}")
            return {}