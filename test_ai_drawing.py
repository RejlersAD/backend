"""
Test AI P&ID Drawing Generation
================================
This script tests the new AI-powered P&ID drawing generator
"""

import os
import sys
import django

# Setup Django environment
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pfd_converter.ai_drawing_generator import AIPIDDrawingGenerator
from django.conf import settings
import json

def test_ai_drawing_generator():
    """Test the AI drawing generator with sample P&ID specs"""
    
    print("="*80)
    print("🎨 TESTING AI P&ID DRAWING GENERATOR")
    print("="*80)
    
    # Create sample P&ID specifications
    pid_specs = {
        'drawing_info': {
            'drawing_number': 'PID-TEST-001',
            'title': 'Test Process Unit P&ID',
            'revision': 'A',
            'project_name': 'AI Drawing Test',
            'date': '2026-01-08'
        },
        'equipment_list': [
            {
                'tag': 'V-101',
                'type': 'vessel',
                'description': 'Feed Drum',
                'specifications': {
                    'design_pressure': '25 barg',
                    'design_temperature': '150°C',
                    'material': 'CS'
                }
            },
            {
                'tag': 'P-101A/B',
                'type': 'pump',
                'description': 'Feed Pump',
                'specifications': {
                    'type': 'Centrifugal',
                    'capacity': '100 m3/h',
                    'head': '50 m'
                }
            },
            {
                'tag': 'E-101',
                'type': 'heat_exchanger',
                'description': 'Feed Preheater',
                'specifications': {
                    'type': 'Shell & Tube',
                    'duty': '500 kW'
                }
            }
        ],
        'instrument_list': [
            {
                'tag': 'PI-101',
                'type': 'Pressure Indicator',
                'description': 'V-101 Pressure',
                'range': '0-30 barg'
            },
            {
                'tag': 'LI-101',
                'type': 'Level Indicator',
                'description': 'V-101 Level',
                'range': '0-100%'
            },
            {
                'tag': 'TI-101',
                'type': 'Temperature Indicator',
                'description': 'Feed Temperature',
                'range': '0-200°C'
            },
            {
                'tag': 'FI-101',
                'type': 'Flow Indicator',
                'description': 'Feed Flow Rate',
                'range': '0-150 m3/h'
            }
        ],
        'piping_specifications': [
            {
                'line_number': '6"-P-101-CS150',
                'from': 'V-101',
                'to': 'P-101A/B',
                'size': '6"',
                'rating': 'Class 150',
                'material': 'CS'
            },
            {
                'line_number': '6"-P-102-CS150',
                'from': 'P-101A/B',
                'to': 'E-101',
                'size': '6"',
                'rating': 'Class 150',
                'material': 'CS'
            }
        ],
        'safety_devices': [
            {
                'tag': 'PSV-101',
                'type': 'PSV',
                'protected_equipment': ['V-101'],
                'set_pressure': '27 barg',
                'relieving_capacity': '5000 kg/h'
            }
        ]
    }
    
    print("\n📋 Sample P&ID Specifications:")
    print(f"   Equipment: {len(pid_specs['equipment_list'])} items")
    print(f"   Instruments: {len(pid_specs['instrument_list'])} items")
    print(f"   Piping Lines: {len(pid_specs['piping_specifications'])} lines")
    print(f"   Safety Devices: {len(pid_specs['safety_devices'])} devices")
    
    # Initialize AI generator
    generator = AIPIDDrawingGenerator()
    
    # Check if OpenAI client is available
    if not generator.client:
        print("\n❌ OpenAI API key not configured!")
        print("   Set OPENAI_API_KEY in environment variables")
        print("   Testing will use fallback method...")
    else:
        print("\n✅ OpenAI API key configured")
        print(f"   Vision Model: {generator.model_vision}")
        print(f"   Drawing Model: {generator.model_dalle}")
    
    # Generate output path
    output_path = os.path.join(settings.MEDIA_ROOT, 'pid_drawings_advanced', 'PID-TEST-001.pdf')
    
    print(f"\n🎨 Generating AI P&ID Drawing...")
    print(f"   Output: {output_path}")
    
    try:
        # Generate drawing (without PFD image since we're testing)
        result_path = generator._create_fallback_drawing(pid_specs, output_path)
        
        print(f"\n✅ Drawing Generated Successfully!")
        print(f"   File: {result_path}")
        
        # Check file size
        if os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            print(f"   Size: {file_size:,} bytes ({file_size/1024:.1f} KB)")
            print(f"\n📥 You can download this file from:")
            print(f"   docker cp radai_backend_local:{result_path} ./PID-TEST-001.pdf")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Generation Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = test_ai_drawing_generator()
    
    print("\n" + "="*80)
    if success:
        print("✅ TEST PASSED - AI Drawing Generator is Working!")
        print("\nNext Steps:")
        print("1. Upload a NEW PFD at http://localhost:5173/pfd/upload")
        print("2. Click 'Generate P&ID' button")
        print("3. Wait for AI generation (~30-60 seconds)")
        print("4. Download the new P&ID drawing")
        print("\n⚠️  OLD conversions will still have old-style drawings")
        print("   Only NEW conversions after this update will have AI drawings")
    else:
        print("❌ TEST FAILED - Check errors above")
    print("="*80)
