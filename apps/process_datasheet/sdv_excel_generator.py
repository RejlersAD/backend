"""
SDV Datasheet Excel Generator
Fills the bundled template with AI-mapped data
"""
import logging
from pathlib import Path
from typing import Dict, List
from datetime import datetime
import openpyxl
from io import BytesIO
from django.core.files.base import ContentFile
from apps.process_datasheet.template_manager import SDVTemplateManager

logger = logging.getLogger(__name__)


class SDVExcelGenerator:
    """
    Generate filled SDV datasheets from mapped data
    Uses the bundled Excel template and openpyxl
    """
    
    # Excel cell mappings (based on actual template structure from image)
    CELL_MAPPINGS = {
        # Document Info (Header)
        'document_no': 'C2',
        'rev_no': 'N2', 
        'date': 'N3',
        
        # Row 1: Tag No.
        'tag_no': 'D5',
        
        # Row 2: Service
        'service': 'D6',
        
        # Row 3: P&ID No.
        'pid_no': 'D7',
        
        # Row 4: Line No. | Piping class
        'line_no': 'D8',
        'piping_class': 'I8',
        
        # Row 5: Sour Service | Special Service
        'sour_service': 'D9',
        'special_service': 'I9',
        
        # Row 6: Ambient Temp | Min | Max. | Unit
        'ambient_temp_min': 'E10',
        'ambient_temp_max': 'G10',
        'ambient_temp_unit': 'I10',
        
        # Row 7: Fluid | Phase | State
        'fluid': 'D11',
        'phase': 'F11',
        'state': 'H11',
        
        # Row 8: Press. | Normal | Design | Unit
        'operating_pressure_normal': 'E12',
        'operating_pressure_design': 'G12',
        'pressure_unit': 'I12',
        
        # Row 9: Temperature | Min | Max. | Unit
        'operating_temp_min': 'E13',
        'operating_temp_max': 'G13',
        'operating_temp_unit': 'I13',
        
        # Row 10: Design Temp. | Min | Max. | Unit
        'design_temp_min': 'E14',
        'design_temp_max': 'G14',
        'design_temp_unit': 'I14',
        
        # Row 11: Shut Off Pressure
        'shut_off_pressure': 'D15',
        
        # Row 12: Bore Detail
        'bore_detail': 'D16',
        
        # Row 13: Mech. Handwheel
        'mech_handwheel': 'D17',
        
        # Row 14: Air Fail position
        'fail_position': 'D18',
        
        # Row 15: Valve Close Time | Valve Open Time
        'valve_close_time': 'D19',
        'valve_open_time': 'H19',
        
        # Row 16: Design Pressure
        'design_pressure': 'D20',
        
        # Row 17: Seat Leakage Class
        'seat_leakage_class': 'D21',
        
        # Row 18: NACE Requirement
        'nace_requirement': 'D22',
    }
    
    def __init__(self):
        """Initialize generator"""
        logger.info("[SDVExcelGenerator] Initialized")
    
    def generate_datasheet(
        self,
        mapped_data: Dict,
        output_filename: str = None
    ) -> BytesIO:
        """
        Generate filled SDV datasheet Excel file
        
        Args:
            mapped_data: AI-mapped data from SDVDatasheetAIMapper
            output_filename: Optional filename for output
        
        Returns:
            BytesIO: Excel file in memory
        """
        logger.info("[SDVExcelGenerator] Starting datasheet generation...")
        
        try:
            # Get template
            template_path = SDVTemplateManager.get_template_path()
            logger.info(f"[SDVExcelGenerator] Loading template: {template_path.name}")
            
            # Load workbook
            wb = openpyxl.load_workbook(template_path)
            ws = wb.active
            
            # Get first valve (or iterate if multiple)
            valves = mapped_data.get('valves', [])
            
            if not valves:
                logger.warning("[SDVExcelGenerator] No valves to fill")
                raise ValueError("No valve data provided")
            
            # For now, fill first valve (can be extended for multiple sheets)
            valve_data = valves[0]
            logger.info(f"[SDVExcelGenerator] Filling data for: {valve_data.get('tag_no', 'Unknown')}")
            
            # Fill all mapped fields
            filled_count = self._fill_template(ws, valve_data)
            
            logger.info(f"[SDVExcelGenerator] ✅ Filled {filled_count} fields")
            
            # Save to memory
            output = BytesIO()
            wb.save(output)
            output.seek(0)
            wb.close()
            
            logger.info(f"[SDVExcelGenerator] ✅ Generated Excel file ({len(output.getvalue())/1024:.2f} KB)")
            
            return output
            
        except Exception as e:
            logger.error(f"[SDVExcelGenerator] ❌ Error: {e}")
            raise
    
    def _fill_template(self, ws, valve_data: Dict) -> int:
        """
        Fill Excel template with valve data
        
        Args:
            ws: openpyxl worksheet
            valve_data: Single valve data dict
        
        Returns:
            int: Number of fields filled
        """
        filled_count = 0
        
        # Add default document info
        ws['C2'] = valve_data.get('document_no', 'RJ-AB-SDV-DS-001')
        ws['N2'] = valve_data.get('rev_no', 'A')
        ws['N3'] = valve_data.get('date', datetime.now().strftime('%d-%b-%Y'))
        filled_count += 3
        
        # Map all fields using CELL_MAPPINGS
        for field, cell in self.CELL_MAPPINGS.items():
            # Skip document header fields already handled
            if field in ['document_no', 'rev_no', 'date']:
                continue
                
            value = valve_data.get(field)
            if value is not None and value != '':
                ws[cell] = str(value)
                filled_count += 1
        
        return filled_count
    
    def generate_multiple_datasheets(
        self,
        mapped_data: Dict
    ) -> Dict[str, BytesIO]:
        """
        Generate multiple datasheets (one per valve)
        
        Args:
            mapped_data: AI-mapped data with multiple valves
        
        Returns:
            Dict mapping valve tags to Excel files
        """
        valves = mapped_data.get('valves', [])
        datasheets = {}
        
        for valve in valves:
            tag = valve.get('tag_no', 'Unknown')
            logger.info(f"[SDVExcelGenerator] Generating datasheet for {tag}")
            
            # Create single-valve data
            single_valve_data = {
                'valves': [valve],
                'confidence': valve.get('confidence', 'medium')
            }
            
            # Generate datasheet
            excel_file = self.generate_datasheet(single_valve_data)
            datasheets[tag] = excel_file
        
        logger.info(f"[SDVExcelGenerator] ✅ Generated {len(datasheets)} datasheets")
        return datasheets


# Quick test function
def test_excel_generator():
    """Test Excel generator with sample data"""
    from datetime import datetime
    
    sample_mapped_data = {
        'valves': [
            {
                'tag_no': 'SDV-100-001',
                'service': 'Main Gas Line Shutdown',
                'pid_no': 'P-100-001',
                'line_no': '6"-GA-100-1501-A2B',
                'piping_class': 'ASME B16.5 150#',
                'fluid': 'Natural Gas',
                'phase': 'Gas',
                'state': 'Supercritical',
                'operating_pressure_normal': '75',
                'operating_pressure_design': '90',
                'pressure_unit': 'barg',
                'operating_temp_min': '-10',
                'operating_temp_max': '65',
                'operating_temp_unit': '°C',
                'design_temp_min': '-20',
                'design_temp_max': '85',
                'design_temp_unit': '°C',
                'fail_position': 'FC (Fail Close)',
                'valve_close_time': '5 seconds',
                'valve_open_time': '10 seconds',
                'document_no': 'RJ-AB-001-DS-001',
                'rev_no': 'A',
                'date': datetime.now().strftime('%d-%b-%Y'),
                'confidence': 'high'
            }
        ],
        'overall_confidence': 'high'
    }
    
    generator = SDVExcelGenerator()
    excel_file = generator.generate_datasheet(sample_mapped_data)
    
    # Save to file
    output_path = Path(r'C:\Users\Abdullah.Khan\RAD_AI\SDV_AI_Generated_TEST.xlsx')
    with open(output_path, 'wb') as f:
        f.write(excel_file.getvalue())
    
    print(f"✅ Test file generated: {output_path}")
    print(f"   Size: {output_path.stat().st_size / 1024:.2f} KB")


if __name__ == "__main__":
    test_excel_generator()
