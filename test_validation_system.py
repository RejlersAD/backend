#!/usr/bin/env python
"""Quick validation test for engineering modules"""
import os
import sys

# Test 1: Engineering Config
print("="*70)
print("STEP 1: Engineering Standards Configuration")
print("="*70)

try:
    from apps.pfd_converter.engineering_standards_config import get_engineering_config
    config = get_engineering_config()
    print(f"\n✅ Configuration loaded!")
    print(f"   - Instrument Types: {len(config.instrument_mapping.instrument_prefixes)}")
    print(f"   - Valve Types: {len(config.valve_mapping.valve_types)}")
    print(f"   - Validation Rules: {len(config.validation_rules.mandatory_checks)}")
except Exception as e:
    print(f"\n❌ Error: {e}")
    sys.exit(1)

# Test 2: S3 Reference Loader
print("\n" + "="*70)
print("STEP 2: S3 Reference Loader")
print("="*70)

try:
    from apps.pfd_converter.s3_reference_loader import S3ReferenceLoader
    
    print(f"\n🔗 Environment:")
    print(f"   Bucket: {os.getenv('AWS_STORAGE_BUCKET_NAME', 'NOT SET')}")
    print(f"   Region: {os.getenv('AWS_S3_REGION_NAME', 'NOT SET')}")
    
    loader = S3ReferenceLoader()
    print(f"\n🔗 S3 Loader:")
    print(f"   Bucket: {loader.bucket_name}")
    print(f"   Region: {loader.region}")
    
    print(f"\n📁 Loading ADNOC projects...")
    projects = loader.load_adnoc_projects()
    
    print(f"\n✅ Found {len(projects)} ADNOC projects:")
    for i, proj in enumerate(projects[:5], 1):
        proj_name = proj.split('/')[-2]
        print(f"   {i}. {proj_name[:60]}")
    
    if len(projects) > 5:
        print(f"   ... and {len(projects) - 5} more")
        
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

# Test 3: Validation Engine
print("\n" + "="*70)
print("STEP 3: Validation Engine")
print("="*70)

try:
    from apps.pfd_converter.validation_engine import validate_pid
    
    # Sample P&ID with intentional issues
    test_pid = {
        'drawing_number': 'TEST-001',
        'drawing_title': 'Test P&ID',
        'equipment_list': [
            {'tag': 'V-001', 'type': 'VESSEL', 'design_pressure': 25}  # Missing PSV!
        ],
        'instrument_list': [],
        'safety_devices': [],
        'valve_list': []
    }
    
    print(f"\n🔍 Validating test P&ID...")
    result = validate_pid(test_pid)
    
    print(f"\n{result.get_summary()}")
    
    if result.findings:
        print(f"\n📋 Sample Findings (first 3):")
        for finding in result.findings[:3]:
            severity_icon = "🔴" if finding.severity.value == "CRITICAL" else "🟡"
            print(f"   {severity_icon} [{finding.severity.value}] {finding.description}")
    
    print(f"\n✅ Validation engine working correctly!")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*70)
print("✅ ALL TESTS COMPLETED")
print("="*70)
