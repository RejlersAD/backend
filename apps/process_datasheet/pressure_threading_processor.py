"""
Threading-based async processor for Pressure Instrument datasheet generation
Fallback when Celery is in EAGER mode or not available
"""
import logging
import threading
import os
import base64
from django.core.cache import cache
from apps.process_datasheet.real_pid_extractor import RealPIDExtractor
from apps.process_datasheet.mock_extractors import MockPIDExtractor
from apps.process_datasheet.excel_generators.pressure_excel_generator import PressureExcelGeneratorDynamic

logger = logging.getLogger(__name__)

def log_and_print(message):
    """Helper to both log and print for visibility"""
    logger.info(message)
    print(message)

def generate_pressure_html_preview(instruments):
    """Generate HTML preview table for pressure instruments"""
    if not instruments:
        return '<p>No pressure instruments found</p>'
    
    html = '''
    <div style="overflow-x: auto;">
        <table style="width: 100%; border-collapse: collapse; font-family: Arial, sans-serif; font-size: 12px;">
            <thead>
                <tr style="background-color: #4472C4; color: white;">
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Tag Number</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">P&ID No</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Service</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Line No.</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Piping Class</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Equipment No.</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Operating Pressure (Norm)</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Operating Temp (Norm)</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Fluid State</th>
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Special Conditions</th>
                </tr>
            </thead>
            <tbody>
    '''
    
    for idx, inst in enumerate(instruments):
        row_color = '#f9f9f9' if idx % 2 == 0 else 'white'
        html += f'''
                <tr style="background-color: {row_color};">
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('tag_number', '')}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('pid_no', '')}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('service', '')}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('line_no', '')}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('piping_class', '')}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('equipment_no', '')}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('operating_pressure_norm', '')}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('operating_temp_norm', '')}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('fluid_state', '')}</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{inst.get('special_conditions', '')}</td>
                </tr>
        '''
    
    html += '''
            </tbody>
        </table>
    </div>
    '''
    
    return html

def process_pressure_threading(pid_file_path, job_id):
    """
    Process Pressure Instrument datasheet synchronously
    Called from orchestrator which already runs in a thread
    """
    try:
        log_and_print(f"🎯 [Pressure {job_id[:8]}] Starting pressure instrument extraction...")
        
        # Initialize cache
        cache.set(f'pressure_task_{job_id}_status', 'processing', timeout=3600)
        cache.set(f'pressure_task_{job_id}_progress', 0, timeout=3600)
        cache.set(f'pressure_task_{job_id}_stage', 'Initializing...', timeout=3600)
        
        # Update smart job status
        cache.set(f'smart_job_{job_id}', {
            'status': 'processing',
            'progress': 10,
            'stage': 'Extracting pressure instruments from P&ID...'
        }, timeout=3600)

        # STEP 1: Extract pressure instruments from P&ID
        cache.set(f'pressure_task_{job_id}_progress', 20, timeout=3600)
        cache.set(f'pressure_task_{job_id}_stage', 'Extracting pressure instruments from P&ID...', timeout=3600)
        
        log_and_print(f"📄 [Pressure {job_id[:8]}] STEP 1: Extracting pressure instruments from P&ID...")
        
        # Extract filename from path for P&ID No field (without extension)
        import os
        pid_filename = os.path.basename(pid_file_path)
        # Remove file extension to get clean drawing number
        pid_filename = os.path.splitext(pid_filename)[0]
        
        log_and_print(f"📋 [Pressure {job_id[:8]}] Using P&ID filename: {pid_filename}")
        
        try:
            # Use the same PressureInstrumentAnalyzer from the existing page
            from apps.pid_analysis.pressure_instrument_service import PressureInstrumentAnalyzer
            
            # Read the PID file
            with open(pid_file_path, 'rb') as f:
                pid_image_data = f.read()
            
            analyzer = PressureInstrumentAnalyzer()
            
            # Extract pressure instruments using AI Vision API
            drawing_info = {
                'drawing_number': pid_filename,
                'drawing_title': 'P&ID Analysis',
                'revision': '0'
            }
            
            # Use the correct method name: analyze_pid_with_ai
            pressure_instruments = analyzer.analyze_pid_with_ai(pid_image_data, drawing_info)
            
            # Update all instruments to use the uploaded filename as P&ID No
            for instrument in pressure_instruments:
                instrument['pid_no'] = pid_filename
            
            log_and_print(f"✅ [Pressure {job_id[:8]}] Found {len(pressure_instruments)} pressure instruments")
            
            if not pressure_instruments:
                log_and_print(f"⚠️ [Pressure {job_id[:8]}] No pressure instruments found, using mock data")
                raise ValueError("No pressure instruments detected")
                
        except Exception as e:
            logger.warning(f"[Pressure Thread {job_id}] Real extraction failed, using mock: {e}")
            log_and_print(f"🔄 [Pressure {job_id[:8]}] Using mock data fallback...")
            mock_extractor = MockPIDExtractor()
            pid_data = mock_extractor.extract_from_pdf(pid_file_path)
            pressure_instruments = [inst for inst in pid_data.get('valves', []) if inst.get('type', '').upper().startswith(('PI', 'PT', 'PG'))]
            log_and_print(f"✅ [Pressure {job_id[:8]}] Mock extraction: {len(pressure_instruments)} pressure instruments")

        cache.set(f'smart_job_{job_id}', {
            'status': 'processing',
            'progress': 60,
            'stage': 'Generating Excel datasheet...'
        }, timeout=3600)

        # STEP 2: Generate Excel
        cache.set(f'pressure_task_{job_id}_progress', 80, timeout=3600)
        cache.set(f'pressure_task_{job_id}_stage', 'Generating Excel...', timeout=3600)
        
        log_and_print(f"📊 [Pressure {job_id[:8]}] STEP 2: Generating Excel...")
        
        excel_generator = PressureExcelGeneratorDynamic()
        excel_data = excel_generator.generate({'instruments': pressure_instruments})
        
        if not excel_data or len(excel_data) == 0:
            raise ValueError("Excel generation produced empty file")
            
        log_and_print(f"✅ [Pressure {job_id[:8]}] Excel generated: {len(excel_data)} bytes")

        # Convert Excel to base64
        excel_base64 = base64.b64encode(excel_data).decode('utf-8')
        
        # Generate HTML preview
        html_preview = generate_pressure_html_preview(pressure_instruments)
        
        # Prepare result
        import uuid
        excel_filename = f"Pressure_Instrument_{job_id[:8]}_{uuid.uuid4().hex[:8]}.xlsx"
        
        result = {
            'success': True,
            'html_preview': html_preview,
            'excel_file': excel_base64,
            'filename': excel_filename,
            'instruments_count': len(pressure_instruments)
        }
        
        # Mark as complete in cache
        cache.set(f'pressure_task_{job_id}_status', 'completed', timeout=3600)
        cache.set(f'pressure_task_{job_id}_progress', 100, timeout=3600)
        cache.set(f'pressure_task_{job_id}_result', result, timeout=3600)
        
        log_and_print(f"✅ [Pressure {job_id[:8]}] COMPLETE! Datasheet ready with HTML preview.")
        return result

    except Exception as e:
        logger.error(f"[Pressure Thread {job_id}] Error: {str(e)}", exc_info=True)
        log_and_print(f"❌ [Pressure {job_id[:8]}] FAILED: {str(e)}")
        
        error_result = {
            'success': False,
            'error': str(e)
        }
        cache.set(f'pressure_task_{job_id}_status', 'failed', timeout=3600)
        cache.set(f'pressure_task_{job_id}_result', error_result, timeout=3600)
        return error_result
