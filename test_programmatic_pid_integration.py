"""
Test script for Programmatic P&ID Generator Integration
Verifies that the new programmatic generator works with the PFD converter
"""

import sys
import os
import django

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pfd_converter.programmatic_pid_generator import generate_pid_from_specs

def test_programmatic_generation():
    """Test basic programmatic P&ID generation"""
    print("=" * 60)
    print("Testing Programmatic P&ID Generator Integration")
    print("=" * 60)
    
    # Sample drawing specifications (minimal test data)
    test_specs = {
        'drawing_number': 'TEST-PID-001',
        'drawing_title': 'Test Process P&ID',
        'project_name': 'Test Integration Project',
        'project_code': 'TEST-001',
        'revision': 'A',
        'equipment': [
            {
                'tag': 'V-101',
                'name': 'Separator Vessel',
                'type': 'vessel',
                'connections': [
                    {'to_tag': 'P-101', 'line_number': '2-01-CS-001'}
                ]
            },
            {
                'tag': 'P-101',
                'name': 'Feed Pump',
                'type': 'pump',
                'connections': []
            }
        ],
        'piping': [
            {
                'from_equipment': 'V-101',
                'to_equipment': 'P-101',
                'line_number': '2"-01-CS-001'
            }
        ],
        'instrumentation': [
            {
                'tag': 'PT-101',
                'type': 'pressure transmitter',
                'location': 'field',
                'connected_to': 'V-101'
            },
            {
                'tag': 'LT-101',
                'type': 'level transmitter',
                'location': 'field',
                'connected_to': 'V-101'
            },
            {
                'tag': 'FT-101',
                'type': 'flow transmitter',
                'location': 'field',
                'connected_to': 'P-101'
            }
        ],
        'valves': [
            {
                'tag': 'HV-101',
                'type': 'gate'
            },
            {
                'tag': 'PCV-101',
                'type': 'control'
            }
        ]
    }
    
    # Output path
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'test_outputs')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'TEST_Programmatic_PID_Integration.pdf')
    
    print(f"\n📋 Test Specifications:")
    print(f"   Drawing Number: {test_specs['drawing_number']}")
    print(f"   Equipment: {len(test_specs['equipment'])} items")
    print(f"   Instrumentation: {len(test_specs['instrumentation'])} instruments")
    print(f"   Valves: {len(test_specs['valves'])} valves")
    print(f"   Piping: {len(test_specs['piping'])} connections")
    print(f"\n📁 Output Path: {output_path}")
    
    try:
        print("\n🎨 Generating P&ID...")
        result_path = generate_pid_from_specs(test_specs, output_path)
        
        print(f"\n✅ SUCCESS!")
        print(f"   Generated P&ID saved to: {result_path}")
        
        # Check file exists and size
        if os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            print(f"   File size: {file_size:,} bytes ({file_size/1024:.1f} KB)")
            print("\n✅ Integration test PASSED")
            return True
        else:
            print("\n❌ ERROR: File was not created")
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_converter_integration():
    """Test integration with PFDToPIDConverter"""
    print("\n" + "=" * 60)
    print("Testing PFDToPIDConverter Integration")
    print("=" * 60)
    
    from apps.pfd_converter.services import PFDToPIDConverter, DrawingConfig
    
    print(f"\n📋 Current Configuration:")
    print(f"   Generation Mode: {DrawingConfig.GENERATION_MODE}")
    print(f"   DALL-E 3 Enabled: {DrawingConfig.ENABLE_DALLE3}")
    print(f"   API Key Valid: {DrawingConfig.is_api_key_valid()}")
    
    # Sample PFD and P&ID specs
    pfd_data = {
        'equipment': [
            {'tag': 'V-201', 'type': 'Separator', 'description': 'Three-Phase Separator'}
        ]
    }
    
    pid_specs = {
        'pid_drawing_number': 'TEST-PID-002',
        'pid_title': 'Converter Integration Test',
        'pid_revision': 'A',
        'project_info': {
            'project_name': 'Integration Test',
            'project_code': 'TEST-002'
        },
        'equipment_list': [
            {
                'tag': 'V-201',
                'type': 'vessel',
                'name': 'Three-Phase Separator',
                'description': 'Separates oil, water, and gas',
                'connections': []
            }
        ],
        'instrument_list': [
            {
                'tag': 'PT-201',
                'type': 'pressure transmitter',
                'location': 'field',
                'connected_to': 'V-201'
            }
        ],
        'safety_devices': [
            {
                'tag': 'PSV-201',
                'type': 'safety valve'
            }
        ]
    }
    
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'test_outputs')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'TEST_Converter_Integration.pdf')
    
    print(f"\n📁 Output Path: {output_path}")
    
    try:
        converter = PFDToPIDConverter()
        print("\n🎨 Generating P&ID via converter...")
        
        result_path = converter.generate_pid_drawing(pfd_data, pid_specs, output_path)
        
        print(f"\n✅ SUCCESS!")
        print(f"   Generated P&ID saved to: {result_path}")
        
        if os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            print(f"   File size: {file_size:,} bytes ({file_size/1024:.1f} KB)")
            print("\n✅ Converter integration test PASSED")
            return True
        else:
            print("\n❌ ERROR: File was not created")
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("PROGRAMMATIC P&ID GENERATOR - INTEGRATION TEST SUITE")
    print("=" * 60)
    
    # Test 1: Direct programmatic generator
    test1_passed = test_programmatic_generation()
    
    # Test 2: Integration with converter
    test2_passed = test_converter_integration()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"   Direct Generator Test: {'✅ PASSED' if test1_passed else '❌ FAILED'}")
    print(f"   Converter Integration Test: {'✅ PASSED' if test2_passed else '❌ FAILED'}")
    
    if test1_passed and test2_passed:
        print("\n🎉 ALL TESTS PASSED! Integration successful!")
        sys.exit(0)
    else:
        print("\n⚠️ SOME TESTS FAILED - See details above")
        sys.exit(1)
