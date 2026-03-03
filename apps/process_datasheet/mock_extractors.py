"""
Mock SDV Data Extractors for Testing
Simulates P&ID and HMB extraction (replace with real extraction later)
"""
import logging
from typing import Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)


class MockPIDExtractor:
    """
    Mock P&ID extractor for testing
    TODO: Replace with real OCR/AI extraction from designiq.pid_ocr_extractor_v2
    """
    
    def extract_from_pdf(self, pdf_file) -> Dict:
        """
        Extract valve and line data from P&ID
        
        Args:
            pdf_file: Uploaded PDF file
        
        Returns:
            Dict with structured P&ID data
        """
        logger.info("[MockPIDExtractor] Extracting from P&ID...")
        
        # Simulated extraction (replace with real extraction)
        pid_data = {
            'valves': [
                {
                    'tag': 'SDV-100-001',
                    'type': 'SDV',
                    'location': 'Main Gas Line Inlet',
                    'line_no': '6"-GA-100-1501-A2B',
                    'fail_position': 'FC',
                    'closure_time': '5 seconds'
                },
                {
                    'tag': 'SDV-100-002',
                    'type': 'SDV',
                    'location': 'Gas Line Branch A',
                    'line_no': '4"-GA-100-1502-A2B',
                    'fail_position': 'FC',
                    'closure_time': '3 seconds'
                },
                {
                    'tag': 'XV-100-003',
                    'type': 'XV',
                    'location': 'Isolation Valve',
                    'line_no': '6"-GA-100-1501-A2B',
                    'fail_position': 'FO',
                    'closure_time': '10 seconds'
                }
            ],
            'lines': [
                {
                    'line_no': '6"-GA-100-1501-A2B',
                    'piping_class': 'ASME B16.5 150#',
                    'service': 'Natural Gas Main Line',
                    'sour_service': 'No',
                    'special_service': 'None'
                },
                {
                    'line_no': '4"-GA-100-1502-A2B',
                    'piping_class': 'ASME B16.5 150#',
                    'service': 'Natural Gas Branch',
                    'sour_service': 'No',
                    'special_service': 'None'
                }
            ],
            'drawing_info': {
                'pid_no': 'P-100-001-Rev-A',
                'date': datetime.now().strftime('%d-%b-%Y'),
                'project': 'Gas Processing Unit',
                'area': 'Gas Compression'
            }
        }
        
        logger.info(f"[MockPIDExtractor] ✅ Extracted {len(pid_data['valves'])} valves, {len(pid_data['lines'])} lines")
        return pid_data


class MockHMBExtractor:
    """
    Mock HMB (Heat & Material Balance) extractor for testing
    TODO: Replace with real extraction logic
    """
    
    def extract_from_pdf(self, pdf_file) -> Dict:
        """
        Extract process streams and conditions from HMB
        
        Args:
            pdf_file: Uploaded PDF file
        
        Returns:
            Dict with structured HMB data
        """
        logger.info("[MockHMBExtractor] Extracting from HMB...")
        
        # Simulated extraction (replace with real extraction)
        hmb_data = {
            'streams': [
                {
                    'stream_id': 'S-100',
                    'stream_name': 'Gas Feed',
                    'line_no': '6"-GA-100',
                    'fluid': 'Natural Gas',
                    'composition': 'Methane 95.5%, Ethane 3.2%, Propane 1.1%, N2 0.2%',
                    'phase': 'Gas',
                    'state': 'Supercritical',
                    'temperature': {
                        'min': -10,
                        'max': 65,
                        'normal': 45,
                        'unit': '°C'
                    },
                    'pressure': {
                        'normal': 75,
                        'design': 90,
                        'unit': 'barg'
                    },
                    'flow_rate': 50000,
                    'flow_unit': 'kg/hr',
                    'density': 0.72,
                    'density_unit': 'kg/m3',
                    'molecular_weight': 16.8,
                    'viscosity': 0.011,
                    'viscosity_unit': 'cP'
                },
                {
                    'stream_id': 'S-101',
                    'stream_name': 'Gas Branch',
                    'line_no': '4"-GA-100',
                    'fluid': 'Natural Gas',
                    'composition': 'Methane 95.5%, Ethane 3.2%, Propane 1.1%, N2 0.2%',
                    'phase': 'Gas',
                    'state': 'Gas',
                    'temperature': {
                        'min': -5,
                        'max': 60,
                        'normal': 40,
                        'unit': '°C'
                    },
                    'pressure': {
                        'normal': 70,
                        'design': 85,
                        'unit': 'barg'
                    },
                    'flow_rate': 25000,
                    'flow_unit': 'kg/hr',
                    'density': 0.70,
                    'density_unit': 'kg/m3',
                    'molecular_weight': 16.8
                }
            ],
            'process_conditions': {
                'ambient_temp_min': 10,
                'ambient_temp_max': 50,
                'ambient_temp_unit': '°C',
                'design_code': 'ASME B31.3',
                'material_spec': 'ASTM A106 Gr. B'
            }
        }
        
        logger.info(f"[MockHMBExtractor] ✅ Extracted {len(hmb_data['streams'])} streams")
        return hmb_data


def match_lines_to_streams(pid_data: Dict, hmb_data: Dict) -> Dict:
    """
    Pre-match line numbers between P&ID and HMB
    Makes AI mapping more deterministic
    
    Args:
        pid_data: P&ID extracted data
        hmb_data: HMB extracted data
    
    Returns:
        Dict mapping valve tags to matched streams
    """
    logger.info("[LineStreamMatcher] Matching lines to streams...")
    
    line_context = {}
    
    # Build line -> stream mapping
    line_to_stream = {}
    for stream in hmb_data.get('streams', []):
        stream_line = stream.get('line_no', '')
        if stream_line:
            # Normalize line numbers (remove size for matching)
            normalized_line = stream_line.split('-', 1)[-1] if '-' in stream_line else stream_line
            line_to_stream[normalized_line] = stream
    
    # Match valves to streams via line numbers
    for valve in pid_data.get('valves', []):
        valve_tag = valve.get('tag')
        valve_line = valve.get('line_no', '')
        
        if valve_line:
            # Normalize
            normalized_valve_line = valve_line.split('-', 1)[-1] if '-' in valve_line else valve_line
            
            # Find matching stream
            for norm_line, stream in line_to_stream.items():
                if norm_line in normalized_valve_line or normalized_valve_line in norm_line:
                    line_context[valve_tag] = {
                        'line_no': valve_line,
                        'stream_id': stream.get('stream_id'),
                        'stream_name': stream.get('stream_name'),
                        'match_confidence': 'high'
                    }
                    logger.info(f"[LineStreamMatcher] Matched {valve_tag} -> {stream.get('stream_id')}")
                    break
    
    logger.info(f"[LineStreamMatcher] ✅ Matched {len(line_context)} valves to streams")
    return line_context


# Test function
def test_mock_extractors():
    """Test the mock extractors"""
    
    pid_extractor = MockPIDExtractor()
    hmb_extractor = MockHMBExtractor()
    
    pid_data = pid_extractor.extract_from_pdf(None)
    hmb_data = hmb_extractor.extract_from_pdf(None)
    
    print("=== P&ID Data ===")
    print(f"Valves: {len(pid_data['valves'])}")
    for valve in pid_data['valves']:
        print(f"  - {valve['tag']}: {valve['line_no']}")
    
    print("\n=== HMB Data ===")
    print(f"Streams: {len(hmb_data['streams'])}")
    for stream in hmb_data['streams']:
        print(f"  - {stream['stream_id']}: {stream['line_no']}")
    
    print("\n=== Line-Stream Matching ===")
    line_context = match_lines_to_streams(pid_data, hmb_data)
    for valve_tag, context in line_context.items():
        print(f"  - {valve_tag} -> {context['stream_id']} (via {context['line_no']})")


if __name__ == "__main__":
    test_mock_extractors()
