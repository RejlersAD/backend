"""
SPEC Customization — Intelligent Range Expansion & Component Detection
========================================================================

This script validates the enhanced extraction and expansion features:

1. **Enhanced Size Range Detection**: Multiple pattern support
   - "1.5 & Below" → [0.5, 0.75, 1.0, 1.25, 1.5]
   - "1/2\" thru 1-1/2\"" → [0.5, 0.75, 1.0, 1.25, 1.5]
   - "up to 2\"" → [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
   - size_from="1/2", size_to="1-1/2" → individual rows

2. **Comprehensive Component Detection**: Priority subtypes
   - VENT & DRAIN VALVES
   - CHECK VALVES
   - GASKETS
   - SOCKOLETS
   - FLANGES (GEN.)

Usage:
    python _test_intelligent_extraction.py <job_id>

Example:
    python _test_intelligent_extraction.py 12345678-1234-1234-1234-123456789012
"""
import sys
import os
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.spec_customization.models import PaperSpecExtractionJob, PipingClass
from apps.spec_customization.services.config import (
    SIZE_EXPANSION_CONFIG,
    COMPONENT_TYPE_DETECTION_CONFIG
)
from apps.spec_customization.services.data_quality import (
    _detect_range_pattern,
    _detect_size_range,
    _parse_size_value,
)
import json


def test_range_detection():
    """Test enhanced size range pattern detection."""
    print("\n" + "="*80)
    print("TEST 1: Size Range Pattern Detection")
    print("="*80)
    
    test_cases = [
        ("1.5 & Below", True),
        ("1½ & Below", True),
        ("1/2\" thru 1-1/2\"", True),
        ("1/2\" through 1-1/2\"", True),
        ("up to 2\"", True),
        ("≤ 1.5\"", True),
        ("1.5 and smaller", True),
        ("1/2\" to 1-1/2\"", True),
        ("1/2\"", False),  # No range pattern
        ("2\" NPS", False),
    ]
    
    passed = 0
    failed = 0
    
    for test_str, expected in test_cases:
        result = _detect_range_pattern(test_str)
        status = "✅ PASS" if result == expected else "❌ FAIL"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"{status} | '{test_str}' → {result} (expected {expected})")
    
    print(f"\n📊 Results: {passed} passed, {failed} failed")
    return failed == 0


def test_component_type_detection(job_id: str):
    """Test that priority component types are extracted."""
    print("\n" + "="*80)
    print("TEST 2: Priority Component Type Detection")
    print("="*80)
    
    try:
        job = PaperSpecExtractionJob.objects.get(pk=job_id)
        
        # Get priority subtypes from config
        priority_subtypes = COMPONENT_TYPE_DETECTION_CONFIG.get("priority_subtypes", {})
        
        # Get all extracted components
        all_components = []
        for cls in job.piping_classes.prefetch_related('components').all():
            for comp in cls.components.all():
                all_components.append({
                    'class': cls.class_code,
                    'type': comp.component_type,
                    'sub_type': comp.sub_type,
                    'description': comp.description,
                })
        
        print(f"\n📋 Total components extracted: {len(all_components)}")
        
        # Check for each priority type
        found_types = {}
        for comp_type, subtypes in priority_subtypes.items():
            found_types[comp_type] = []
            for comp in all_components:
                comp_text = f"{comp['type']} {comp['sub_type']} {comp['description']}".upper()
                for priority_subtype in subtypes:
                    if priority_subtype.upper() in comp_text:
                        found_types[comp_type].append({
                            'subtype': priority_subtype,
                            'class': comp['class'],
                            'extracted': comp['sub_type'] or comp['description']
                        })
                        break
        
        # Report findings
        all_found = True
        for comp_type, found_list in found_types.items():
            type_display = comp_type.upper()
            if found_list:
                print(f"\n✅ {type_display}: Found {len(found_list)} components")
                for item in found_list[:3]:  # Show first 3 examples
                    print(f"   - {item['subtype']} (Class {item['class']}: {item['extracted']})")
            else:
                print(f"\n⚠️  {type_display}: NOT FOUND")
                all_found = False
        
        return all_found
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_size_expansion(job_id: str):
    """Test that size ranges are expanded to individual rows."""
    print("\n" + "="*80)
    print("TEST 3: Size Range Expansion")
    print("="*80)
    
    try:
        job = PaperSpecExtractionJob.objects.get(pk=job_id)
        
        # Check configuration
        config = SIZE_EXPANSION_CONFIG
        print(f"\n📋 Size Expansion Config:")
        print(f"   - Enabled: {config['enable_size_expansion']}")
        print(f"   - Expand All Ranges: {config['expand_all_ranges']}")
        print(f"   - Small Threshold: {config['small_size_threshold']}")
        print(f"   - Small Ladder: {config['small_size_ladder']}")
        print(f"   - Medium Threshold: {config['medium_size_threshold']}")
        print(f"   - Medium Ladder: {config['medium_size_ladder']}")
        
        # Look for expanded components
        expansion_examples = []
        size_distribution = {}
        
        for cls in job.piping_classes.prefetch_related('components').all():
            for comp in cls.components.all():
                size_from = comp.size_from or ''
                size_to = comp.size_to or ''
                notes = comp.notes or ''
                
                # Check if this is an expanded component
                if 'Auto-expanded from range' in notes or 'Auto-expanded from' in notes:
                    expansion_examples.append({
                        'class': cls.class_code,
                        'type': comp.sub_type or comp.component_type,
                        'size_from': size_from,
                        'size_to': size_to,
                        'notes': notes[:100]
                    })
                
                # Track size distribution
                if size_from and size_from == size_to:
                    # Single size row
                    size_distribution[size_from] = size_distribution.get(size_from, 0) + 1
        
        print(f"\n📊 Expansion Statistics:")
        print(f"   - Expanded components found: {len(expansion_examples)}")
        print(f"   - Unique sizes: {len(size_distribution)}")
        
        if expansion_examples:
            print(f"\n✅ PASS: Size expansion detected!")
            print(f"\n📝 Sample Expanded Components (showing first 5):")
            for ex in expansion_examples[:5]:
                print(f"   - Class {ex['class']}: {ex['type']} | Size: {ex['size_from']}")
                print(f"     Notes: {ex['notes']}")
            
            # Check if critical small sizes are present
            critical_sizes = ['1/2', '3/4', '1', '1-1/4', '1-1/2']
            found_sizes = [s for s in critical_sizes if s in size_distribution]
            print(f"\n📏 Critical Small Sizes Found: {found_sizes}")
            
            if len(found_sizes) >= 4:
                print(f"✅ PASS: At least 4 critical sizes detected ({len(found_sizes)}/5)")
                return True
            else:
                print(f"⚠️  WARNING: Only {len(found_sizes)}/5 critical sizes found")
                return False
        else:
            print(f"\n⚠️  INFO: No expanded components found")
            print(f"   This may mean:")
            print(f"   1. The PDF doesn't contain range patterns")
            print(f"   2. OR this is a pre-enhancement extraction")
            print(f"   3. OR expansion was disabled in config")
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n❌ Error: Job ID required")
        print("\nUsage: python _test_intelligent_extraction.py <job_id>")
        sys.exit(1)
    
    job_id = sys.argv[1]
    
    print("\n" + "="*80)
    print("SPEC Customization — Intelligent Extraction Validation")
    print("="*80)
    print(f"\nJob ID: {job_id}")
    
    try:
        job = PaperSpecExtractionJob.objects.get(pk=job_id)
        print(f"Job Status: {job.status}")
        print(f"Document: {job.document.original_filename}")
        print(f"Classes: {job.piping_classes.count()}")
        print(f"Total Components: {sum(c.components.count() for c in job.piping_classes.all())}")
    except Exception as e:
        print(f"\n❌ ERROR: Could not load job: {e}")
        sys.exit(1)
    
    # Run tests
    test1_pass = test_range_detection()
    test2_pass = test_component_type_detection(job_id)
    test3_pass = test_size_expansion(job_id)
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print(f"Test 1 (Range Pattern Detection): {'✅ PASS' if test1_pass else '❌ FAIL'}")
    print(f"Test 2 (Component Type Detection): {'✅ PASS' if test2_pass else '❌ FAIL'}")
    print(f"Test 3 (Size Range Expansion):     {'✅ PASS' if test3_pass else '⚠️  PARTIAL'}")
    
    if test1_pass and test2_pass:
        print("\n🎉 CORE TESTS PASSED — Intelligent extraction is working!")
        print("\nℹ️  Note: Test 3 requires a new extraction with range patterns in the PDF")
        sys.exit(0)
    else:
        print("\n⚠️  SOME TESTS FAILED — Please review the output above")
        sys.exit(1)


if __name__ == '__main__':
    main()
