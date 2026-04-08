"""
11KV Switchgear Datasheet Generator from SLD Documents
Extracts equipment data and generates comprehensive datasheets
"""
import logging
import re
import json
from typing import Dict, List, Optional
from django.conf import settings
from openai import OpenAI
import PyPDF2

logger = logging.getLogger(__name__)


class SwitchgearDatasheetGenerator:
    """Generate 11KV switchgear datasheets from SLD documents"""
    
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    def extract_text_from_pdf(self, pdf_file) -> str:
        """Extract text from uploaded PDF file"""
        try:
            # Reset file pointer to beginning
            pdf_file.seek(0)
            
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            text = ""
            
            logger.info(f"[SwitchgearDatasheet] PDF has {len(pdf_reader.pages)} pages")
            
            for page_num, page in enumerate(pdf_reader.pages, 1):
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                    logger.info(f"[SwitchgearDatasheet] Page {page_num}: Extracted {len(page_text)} chars")
                else:
                    logger.warning(f"[SwitchgearDatasheet] Page {page_num}: No text extracted (might be image-based)")
            
            logger.info(f"[SwitchgearDatasheet] Total extracted text: {len(text)} characters")
            return text
        except Exception as e:
            logger.error(f"[SwitchgearDatasheet] PDF extraction error: {e}", exc_info=True)
            return ""
    
    def generate_datasheet_from_sld(self, pdf_file, project_info: Dict = None) -> Dict:
        """
        Generate comprehensive 11KV switchgear datasheet from SLD PDF
        
        Args:
            pdf_file: Uploaded PDF file
            project_info: Optional project metadata
        
        Returns:
            {
                'success': bool,
                'datasheet_rows': List[Dict],
                'summary': Dict,
                'extraction_metadata': Dict
            }
        """
        try:
            # Extract text from PDF
            logger.info("[SwitchgearDatasheet] Extracting text from SLD PDF...")
            sld_text = self.extract_text_from_pdf(pdf_file)
            
            # More lenient text extraction check - if we have ANY text, try to process it
            if not sld_text or len(sld_text) < 20:
                logger.error(f"[SwitchgearDatasheet] Insufficient text: {len(sld_text) if sld_text else 0} chars")
                return {
                    'success': False,
                    'error': 'Could not extract text from PDF. The PDF might be image-based or empty. Please provide a text-based SLD document.'
                }
            
            logger.info(f"[SwitchgearDatasheet] Extracted {len(sld_text)} characters from PDF")
            
            # Use AI to extract structured datasheet information
            logger.info("[SwitchgearDatasheet] Analyzing SLD with AI...")
            datasheet_rows = self._extract_datasheet_with_ai(sld_text, project_info)
            
            if not datasheet_rows:
                logger.warning("[SwitchgearDatasheet] AI extraction returned no data, using template")
                # Fall back to template with extracted text hints
                datasheet_rows = self._get_default_datasheet_template()
            
            # Calculate summary statistics
            summary = {
                'total_rows': len(datasheet_rows),
                'equipment_count': sum(1 for row in datasheet_rows if row.get('description', '').strip()),
                'completed_fields': sum(1 for row in datasheet_rows if row.get('vendor_data', '').strip()),
                'missing_fields': sum(1 for row in datasheet_rows if not row.get('vendor_data', '').strip())
            }
            
            logger.info(f"[SwitchgearDatasheet] ✅ Generated {summary['total_rows']} datasheet rows")
            
            return {
                'success': True,
                'datasheet_rows': datasheet_rows,
                'summary': summary,
                'extraction_metadata': {
                    'document_length': len(sld_text),
                    'project_info': project_info or {}
                }
            }
            
        except Exception as e:
            logger.error(f"[SwitchgearDatasheet] Error: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def _extract_datasheet_with_ai(self, sld_text: str, project_info: Dict = None) -> List[Dict]:
        """Use AI to extract structured datasheet data from SLD text"""
        
        extraction_prompt = f"""You are an expert electrical engineer specializing in 11KV switchgear systems. 
Analyze the provided Single Line Diagram (SLD) document and extract comprehensive equipment datasheet information.

PROJECT INFORMATION:
{json.dumps(project_info or {}, indent=2)}

SLD DOCUMENT CONTENT:
{sld_text[:6000]}

TASK: Extract and structure ALL equipment data into a comprehensive datasheet format following this structure:

For each equipment item in the SLD, extract:
1. GENERAL - Basic identification and reference
2. EQUIPMENT DATA - Technical specifications
3. REFERENCE - Standards and specifications
4. REFERENCE SPECIFICATION - Document references
5. SITE DATA - Installation location details
6. GENERAL CHARACTERISTICS - Key features
7. MANUFACTURER DATA - Vendor information
8. MANUFACTURER'S OFFERING - Available options
9. RATINGS AND SHORT CIRCUIT DATA - Electrical ratings
10. CONSTRUCTION & DIMENSIONS - Physical specs
11. CONSTRUCTION - Building details
12. BUSBAR - Busbar configuration
13. CIRCUIT BREAKER DATA - Breaker specifications
14. CURRENT TRANSFORMER - CT details
15. EARTHING - Grounding system
16. NAMEPLATE - Equipment labeling
17. TYPE TESTS - Required testing
18. ROUTINE TESTS - Standard tests
19. SITE ACCEPTANCE TESTS - On-site testing
20. INSTRUMENTS - Measuring devices
21. WEIGHTS - Mass specifications
22. DIMENSIONS OF SHIPPING SECTION - Transport details
23. DIMENSIONS OF SWITCHGEAR - Overall dimensions
24. FOUNDATION - Base requirements
25. SPARE PARTS - Replacement components
26. SPECIAL TOOLS - Required tooling
27. TOOLS - General tools
28. DRAWINGS & SPECIFICATIONS - Document list
29. TESTING & METERING - Test equipment
30. MANUFACTURER - Company details
31. CONSTRUCTION - Assembly details

Return your response as a JSON array where each object has this structure:
{{
    "sr_no": "<sequential number>",
    "description": "<parameter description>",
    "required_data": "<specification or standard requirement>",
    "vendor_data": "<extracted value from SLD or empty string if not found>",
    "remarks": "<any additional notes>"
}}

IMPORTANT GUIDELINES:
- Extract ALL relevant parameters, even if values are not found (leave vendor_data empty)
- For missing data, set vendor_data to empty string ""
- Include section headers (e.g., "GENERAL", "EQUIPMENT DATA") as rows with description only
- Be comprehensive - include all standard 11KV switchgear parameters
- Extract actual values from the SLD when available
- Maintain the exact parameter sequence shown above
- For equipment ratings, extract: voltage, current, breaking capacity, frequency
- For construction, extract: IP rating, enclosure type, busbar material
- For dimensions, extract: height, width, depth, weight

Return ONLY the JSON array, no additional text."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": "You are an expert electrical engineer specializing in switchgear datasheets. Extract comprehensive equipment data and return only valid JSON."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0.2,
                max_tokens=4000
            )
            
            ai_response = response.choices[0].message.content
            
            # Parse JSON response
            # Remove markdown code blocks if present
            if '```json' in ai_response:
                ai_response = ai_response.split('```json')[1].split('```')[0]
            elif '```' in ai_response:
                ai_response = ai_response.split('```')[1].split('```')[0]
            
            datasheet_rows = json.loads(ai_response.strip())
            
            # Validate structure
            if isinstance(datasheet_rows, list) and len(datasheet_rows) > 0:
                # Ensure all rows have required fields
                for i, row in enumerate(datasheet_rows):
                    if 'sr_no' not in row:
                        row['sr_no'] = str(i + 1)
                    if 'description' not in row:
                        row['description'] = ''
                    if 'required_data' not in row:
                        row['required_data'] = ''
                    if 'vendor_data' not in row:
                        row['vendor_data'] = ''
                    if 'remarks' not in row:
                        row['remarks'] = ''
                
                return datasheet_rows
            else:
                logger.error("[SwitchgearDatasheet] Invalid AI response structure")
                return self._get_default_datasheet_template()
                
        except json.JSONDecodeError as e:
            logger.error(f"[SwitchgearDatasheet] JSON decode error: {e}")
            logger.error(f"AI Response: {ai_response[:500]}")
            return self._get_default_datasheet_template()
        except Exception as e:
            logger.error(f"[SwitchgearDatasheet] AI extraction error: {e}")
            return self._get_default_datasheet_template()
    
    def _get_default_datasheet_template(self) -> List[Dict]:
        """Return default 11KV switchgear datasheet template"""
        return [
            {"sr_no": "", "description": "GENERAL", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.0", "description": "EQUIPMENT TAG NO.", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.1", "description": "SERVICE", "required_data": "11 KV SWITCHGEAR", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "REFERENCE", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.2", "description": "APPLICABLE INTERNATIONAL STANDARDS", "required_data": "IEC 60298, IEC 60694, IEC 60255, IEC 60529", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.3", "description": "APPLICABLE SPEC./ADNOC-AGES", "required_data": "ADNOC-AGES-SP-1031", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "SITE DATA", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.4", "description": "SITE LOCATION", "required_data": "ABU DHABI", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.5", "description": "AREA CLASSIFICATION", "required_data": "SAFE AREA", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.6", "description": "CLIMATE CONDITIONS", "required_data": "TROPICAL", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.7", "description": "SITE ALTITUDE (M ABOVE SEA LEVEL)", "required_data": "< 100", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.8", "description": "MINIMUM AMBIENT TEMPERATURE", "required_data": "-5 ℃", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.9", "description": "MAXIMUM AMBIENT TEMPERATURE", "required_data": "50 ℃", "vendor_data": "", "remarks": ""},
            {"sr_no": "1.10", "description": "MAXIMUM RELATIVE HUMIDITY AT 40 ℃", "required_data": "100%", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "GENERAL CHARACTERISTICS", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "2", "description": "TYPE OF SWITCHGEAR", "required_data": "METAL ENCLOSED", "vendor_data": "", "remarks": ""},
            {"sr_no": "2.1", "description": "TYPE OF CIRCUIT BREAKER", "required_data": "VACUUM / SF6", "vendor_data": "", "remarks": ""},
            {"sr_no": "2.2", "description": "STANDARDS", "required_data": "IEC", "vendor_data": "", "remarks": ""},
            {"sr_no": "2.3", "description": "SYSTEM VOLTAGE (kV)", "required_data": "11", "vendor_data": "", "remarks": ""},
            {"sr_no": "2.4", "description": "SYSTEM FREQUENCY (Hz)", "required_data": "50", "vendor_data": "", "remarks": ""},
            {"sr_no": "2.5", "description": "NUMBER OF PHASES", "required_data": "3", "vendor_data": "", "remarks": ""},
            {"sr_no": "2.6", "description": "SYSTEM EARTHING", "required_data": "RESISTANCE EARTHED", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "RATINGS AND SHORT CIRCUIT DATA", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "3.1", "description": "RATED INSULATION VOLTAGE (kV)", "required_data": "12", "vendor_data": "", "remarks": ""},
            {"sr_no": "3.2", "description": "RATED VOLTAGE (kV)", "required_data": "11", "vendor_data": "", "remarks": ""},
            {"sr_no": "3.3", "description": "RATED NORMAL CURRENT (A)", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "3.4", "description": "RATED SHORT CIRCUIT BREAKING CURRENT (kA RMS)", "required_data": "25", "vendor_data": "", "remarks": ""},
            {"sr_no": "3.5", "description": "RATED PEAK WITHSTAND CURRENT (kA PEAK)", "required_data": "65 (2.5 × BREAKING CURRENT)", "vendor_data": "", "remarks": ""},
            {"sr_no": "3.6", "description": "RATED SHORT TIME WITHSTAND CURRENT (kA, 3 SEC)", "required_data": "25", "vendor_data": "", "remarks": ""},
            {"sr_no": "3.7", "description": "RATED POWER FREQUENCY WITHSTAND VOLTAGE", "required_data": "28 kV, 1 MINUTE (DRY)", "vendor_data": "", "remarks": ""},
            {"sr_no": "3.8", "description": "RATED IMPULSE WITHSTAND VOLTAGE (1.2/50 μs)", "required_data": "75 kV", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "CONSTRUCTION", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "4.1", "description": "TYPE", "required_data": "METAL CLAD / METAL ENCLOSED", "vendor_data": "", "remarks": ""},
            {"sr_no": "4.2", "description": "IP RATING", "required_data": "IP 54 MIN", "vendor_data": "", "remarks": ""},
            {"sr_no": "4.3", "description": "COLOUR", "required_data": "RAL 7035 (LIGHT GREY)", "vendor_data": "", "remarks": ""},
            {"sr_no": "4.4", "description": "ARC FAULT CLASSIFICATION", "required_data": "IAC AFL 25 kA 1 SEC", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "BUSBAR", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "5.1", "description": "MATERIAL", "required_data": "COPPER / ALUMINIUM", "vendor_data": "", "remarks": ""},
            {"sr_no": "5.2", "description": "SHAPE", "required_data": "RECTANGULAR", "vendor_data": "", "remarks": ""},
            {"sr_no": "5.3", "description": "BUSBAR RATING (A)", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "CIRCUIT BREAKER", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "6.1", "description": "TYPE", "required_data": "VACUUM / SF6", "vendor_data": "", "remarks": ""},
            {"sr_no": "6.2", "description": "OPERATING MECHANISM", "required_data": "SPRING CHARGED / STORED ENERGY", "vendor_data": "", "remarks": ""},
            {"sr_no": "6.3", "description": "AUXILIARY SUPPLY VOLTAGE", "required_data": "110 VDC / 220 VDC", "vendor_data": "", "remarks": ""},
            {"sr_no": "6.4", "description": "NUMBER OF OPERATING CYCLES", "required_data": "AS PER IEC 60056", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "CURRENT TRANSFORMER", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "7.1", "description": "TYPE", "required_data": "RESIN CAST", "vendor_data": "", "remarks": ""},
            {"sr_no": "7.2", "description": "NUMBER OF CT CORES", "required_data": "AS PER SCHEDULE", "vendor_data": "", "remarks": ""},
            {"sr_no": "7.3", "description": "CT RATIO", "required_data": "AS PER SCHEDULE", "vendor_data": "", "remarks": ""},
            {"sr_no": "7.4", "description": "CT CLASS", "required_data": "5P20, 0.5S", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "VOLTAGE TRANSFORMER", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "8.1", "description": "TYPE", "required_data": "RESIN CAST", "vendor_data": "", "remarks": ""},
            {"sr_no": "8.2", "description": "VT RATIO", "required_data": "11000/√3 : 110/√3", "vendor_data": "", "remarks": ""},
            {"sr_no": "8.3", "description": "VT CLASS", "required_data": "3P, 0.5", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "EARTHING", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "9.1", "description": "MAIN EARTHING BAR", "required_data": "COPPER", "vendor_data": "", "remarks": ""},
            {"sr_no": "9.2", "description": "EARTH FAULT RELAY", "required_data": "NUMERICAL / MULTIFUNCTION", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "PROTECTION & CONTROL", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "10.1", "description": "PROTECTION RELAY TYPE", "required_data": "NUMERICAL / MULTIFUNCTION", "vendor_data": "", "remarks": ""},
            {"sr_no": "10.2", "description": "PROTECTION FUNCTIONS", "required_data": "OVERCURRENT, EARTH FAULT, DIFFERENTIAL", "vendor_data": "", "remarks": ""},
            {"sr_no": "10.3", "description": "METERING", "required_data": "DIGITAL MULTIFUNCTION METER", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "AUXILIARY EQUIPMENT", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "11.1", "description": "ANTI-CONDENSATION HEATER", "required_data": "REQUIRED", "vendor_data": "", "remarks": ""},
            {"sr_no": "11.2", "description": "SPACE HEATER RATING", "required_data": "230 VAC", "vendor_data": "", "remarks": ""},
            {"sr_no": "11.3", "description": "LIGHTING", "required_data": "LED, 230 VAC", "vendor_data": "", "remarks": ""},
            {"sr_no": "", "description": "MANUFACTURER", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "12.1", "description": "NAME", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "12.2", "description": "MODEL/TYPE", "required_data": "", "vendor_data": "", "remarks": ""},
            {"sr_no": "12.3", "description": "COUNTRY OF ORIGIN", "required_data": "", "vendor_data": "", "remarks": ""},
        ]
    
    def export_to_excel(self, datasheet_rows: List[Dict], project_info: Dict = None):
        """Export datasheet to Excel with formatting"""
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        from io import BytesIO
        
        wb = Workbook()
        ws = wb.active
        ws.title = "11KV Switchgear Datasheet"
        
        # Define styles
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True, size=11)
        section_fill = PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid")
        section_font = Font(bold=True, size=10)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Add project header
        if project_info:
            ws.merge_cells('A1:E1')
            ws['A1'] = "11KV SWITCHGEAR DATASHEET"
            ws['A1'].font = Font(bold=True, size=14)
            ws['A1'].alignment = Alignment(horizontal='center')
            
            row_idx = 3
            for key, value in project_info.items():
                ws[f'A{row_idx}'] = key.replace('_', ' ').title()
                ws[f'B{row_idx}'] = value
                row_idx += 1
            
            row_idx += 1
        else:
            row_idx = 1
        
        # Add column headers
        headers = ['SR NO', 'DESCRIPTION', 'REQUIREMENTS AND CONDITIONS (REQUIRED DATA)', 'VENDOR DATA', 'Rem']
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        
        # Set column widths
        ws.column_dimensions['A'].width = 8
        ws.column_dimensions['B'].width = 50
        ws.column_dimensions['C'].width = 40
        ws.column_dimensions['D'].width = 30
        ws.column_dimensions['E'].width = 15
        
        row_idx += 1
        
        # Add data rows
        for row_data in datasheet_rows:
            sr_no = row_data.get('sr_no', '')
            description = row_data.get('description', '')
            required_data = row_data.get('required_data', '')
            vendor_data = row_data.get('vendor_data', '')
            remarks = row_data.get('remarks', '')
            
            # Check if this is a section header
            is_section = (sr_no == '' or sr_no is None) and description and not required_data and not vendor_data
            
            # Add cells
            ws.cell(row=row_idx, column=1, value=sr_no).border = border
            cell_desc = ws.cell(row=row_idx, column=2, value=description)
            cell_desc.border = border
            cell_desc.alignment = Alignment(wrap_text=True, vertical='top')
            
            cell_req = ws.cell(row=row_idx, column=3, value=required_data)
            cell_req.border = border
            cell_req.alignment = Alignment(wrap_text=True, vertical='top')
            
            cell_vendor = ws.cell(row=row_idx, column=4, value=vendor_data)
            cell_vendor.border = border
            cell_vendor.alignment = Alignment(wrap_text=True, vertical='top')
            
            ws.cell(row=row_idx, column=5, value=remarks).border = border
            
            # Apply section styling
            if is_section:
                for col in range(1, 6):
                    cell = ws.cell(row=row_idx, column=col)
                    cell.fill = section_fill
                    cell.font = section_font
            
            row_idx += 1
        
        # Save to BytesIO
        excel_buffer = BytesIO()
        wb.save(excel_buffer)
        excel_buffer.seek(0)
        
        return excel_buffer
