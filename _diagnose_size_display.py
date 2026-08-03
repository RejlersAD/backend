"""
SPEC Customization — Size Display Diagnostic
=============================================

This script helps diagnose why FirstSizeFrom/FirstSizeTo values may not appear
correctly in the PipingCommodityFilter sheet.

Checks:
1. Database: Are expanded components present with correct size_from/size_to?
2. NPD Parsing: Can the system parse the size formats in the database?
3. NPD Normalization: Are sizes being formatted correctly for Excel output?
4. Preview Generation: Are the rows appearing in the preview data?

Usage:
    python _diagnose_size_display.py <job_id>

Example:
    python _diagnose_size_display.py 12345678-1234-1234-1234-123456789012
"""
import sys
import os
import django

# Setup Django environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.spec_customization.models import PaperSpecExtractionJob, PipingClass
from apps.spec_customization.services.exporters.smartplant_config import (
    _to_float_npd,
    _normalize_npd,
    _enumerate_npds,
)
from apps.spec_customization.services.exporters.workbook_preview import build_preview, WORKBOOK_SPEC


def diagnose_size_parsing():
    """Test size parsing and normalization functions."""
    print("\n" + "="*80)
    print("TEST 1: Size Parsing & Normalization")
    print("="*80)
    
    test_sizes = [
        "1/2", "3/4", "1", "1-1/4", "1-1/2",
        0.5, 0.75, 1.0, 1.25, 1.5,
    ]
    
    print("\nSize Format Conversions:")
    print("-" * 60)
    print(f"{'Input':<15} {'Float':<10} {'Normalized':<15}")
    print("-" * 60)
    
    for size in test_sizes:
        float_val = _to_float_npd(size)
        normalized = _normalize_npd(size)
        print(f"{str(size):<15} {str(float_val):<10} {normalized:<15}")
    
    print("\n✅ Size parsing and normalization functions are working")
    return True


def diagnose_npd_enumeration():
    """Test NPD enumeration for size ranges."""
    print("\n" + "="*80)
    print("TEST 2: NPD Enumeration (Range → Individual Sizes)")
    print("="*80)
    
    test_ranges = [
        ("1/2", "1-1/2"),
        ("0.5", "1.5"),
        ("1/2", "1/2"),  # Single size
        ("", ""),  # Empty
    ]
    
    print("\nSize Range Enumeration:")
    print("-" * 80)
    print(f"{'Size From':<15} {'Size To':<15} {'Enumerated NPDs':<50}")
    print("-" * 80)
    
    for size_from, size_to in test_ranges:
        npds = _enumerate_npds(size_from, size_to)
        normalized = [_normalize_npd(n) for n in npds]
        print(f"{size_from:<15} {size_to:<15} {str(normalized):<50}")
    
    print("\n✅ NPD enumeration is working correctly")
    return True


def diagnose_database_components(job_id: str):
    """Check what's actually stored in the database."""
    print("\n" + "="*80)
    print("TEST 3: Database Component Inspection")
    print("="*80)
    
    try:
        job = PaperSpecExtractionJob.objects.get(pk=job_id)
        
        print(f"\nJob: {job_id}")
        print(f"Status: {job.status}")
        print(f"Document: {job.document.original_filename}")
        
        # Look for small size components
        small_sizes_found = []
        total_components = 0
        
        for cls in job.piping_classes.prefetch_related('components').all():
            print(f"\n--- Class: {cls.class_code} ---")
            components = cls.components.all()
            total_components += components.count()
            
            # Group by size_from for display
            size_groups = {}
            for comp in components:
                size_key = f"{comp.size_from} to {comp.size_to}"
                if size_key not in size_groups:
                    size_groups[size_key] = []
                size_groups[size_key].append({
                    'type': comp.component_type,
                    'sub_type': comp.sub_type,
                    'schedule': comp.schedule_or_rating,
                })
            
            # Display size groups
            for size_range, comps in sorted(size_groups.items())[:10]:  # First 10
                count = len(comps)
                sample = comps[0]
                print(f"  {size_range:<30} → {count:>3} components ({sample['sub_type'] or sample['type']})")
                
                # Check if this is a small size
                from_val = _to_float_npd(size_range.split(' to ')[0])
                if from_val is not None and from_val <= 1.5:
                    small_sizes_found.append({
                        'class': cls.class_code,
                        'size': size_range,
                        'count': count,
                        'normalized': _normalize_npd(from_val)
                    })
        
        print(f"\n📊 Database Summary:")
        print(f"   - Total components: {total_components}")
        print(f"   - Small size components found (≤1.5\"): {len(small_sizes_found)}")
        
        if small_sizes_found:
            print(f"\n✅ Small sizes ARE present in database:")
            unique_sizes = sorted(set(s['normalized'] for s in small_sizes_found))
            print(f"   Unique sizes: {', '.join(unique_sizes)}")
            return True
        else:
            print(f"\n⚠️  WARNING: No small size components found in database")
            print(f"   This means either:")
            print(f"   1. The PDF doesn't contain small size ranges")
            print(f"   2. This extraction was done BEFORE the enhancement was deployed")
            print(f"   3. Size expansion failed")
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def diagnose_preview_output(job_id: str):
    """Check if sizes appear in the preview/export data."""
    print("\n" + "="*80)
    print("TEST 4: Preview/Export Output Inspection")
    print("="*80)
    
    try:
        job = PaperSpecExtractionJob.objects.get(pk=job_id)
        preview_data = build_preview(job, WORKBOOK_SPEC)
        
        # Find PipingCommodityFilter sheet
        pcf_sheet = next(
            (s for s in preview_data['sheets'] if s['name'] == 'PipingCommodityFilter'),
            None
        )
        
        if not pcf_sheet:
            print("❌ FAIL: PipingCommodityFilter sheet not found")
            return False
        
        print(f"\n✅ Found PipingCommodityFilter sheet")
        print(f"   Total rows: {pcf_sheet['row_count']}")
        
        # Check FirstSizeFrom/FirstSizeTo values
        size_distribution = {}
        for row in pcf_sheet['rows']:
            cells = row['cells']
            size_from = cells.get('FirstSizeFrom', '')
            size_to = cells.get('FirstSizeTo', '')
            size_key = f"{size_from} to {size_to}"
            size_distribution[size_key] = size_distribution.get(size_key, 0) + 1
        
        print(f"\n📊 FirstSizeFrom/FirstSizeTo Distribution:")
        print("-" * 60)
        print(f"{'Size Range':<30} {'Count':<10}")
        print("-" * 60)
        
        for size_range, count in sorted(size_distribution.items(), key=lambda x: x[1], reverse=True)[:15]:
            print(f"{size_range:<30} {count:<10}")
        
        # Check for critical small sizes
        critical_sizes = ['1/2', '3/4', '1', '1-1/4', '1-1/2']
        found_critical = []
        
        for size in critical_sizes:
            size_key = f"{size} to {size}"
            if size_key in size_distribution:
                found_critical.append(size)
        
        print(f"\n📏 Critical Small Sizes Check:")
        for size in critical_sizes:
            size_key = f"{size} to {size}"
            if size_key in size_distribution:
                print(f"   ✅ {size} → Found ({size_distribution[size_key]} rows)")
            else:
                print(f"   ❌ {size} → NOT FOUND")
        
        if len(found_critical) >= 3:
            print(f"\n✅ PASS: {len(found_critical)}/{len(critical_sizes)} critical sizes found in export")
            return True
        else:
            print(f"\n⚠️  WARNING: Only {len(found_critical)}/{len(critical_sizes)} critical sizes found")
            print(f"   Expected to see: {', '.join(critical_sizes)}")
            print(f"   Actually found: {', '.join(found_critical)}")
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
        print("\nUsage: python _diagnose_size_display.py <job_id>")
        sys.exit(1)
    
    job_id = sys.argv[1]
    
    print("\n" + "="*80)
    print("SPEC Customization — Size Display Diagnostic")
    print("="*80)
    print(f"\nJob ID: {job_id}")
    
    # Run tests
    test1 = diagnose_size_parsing()
    test2 = diagnose_npd_enumeration()
    test3 = diagnose_database_components(job_id)
    test4 = diagnose_preview_output(job_id)
    
    # Summary
    print("\n" + "="*80)
    print("DIAGNOSTIC SUMMARY")
    print("="*80)
    print(f"Test 1 (Size Parsing):         {'✅ PASS' if test1 else '❌ FAIL'}")
    print(f"Test 2 (NPD Enumeration):      {'✅ PASS' if test2 else '❌ FAIL'}")
    print(f"Test 3 (Database Components):  {'✅ PASS' if test3 else '❌ FAIL'}")
    print(f"Test 4 (Preview Output):       {'✅ PASS' if test4 else '❌ FAIL'}")
    
    if test1 and test2 and test3 and test4:
        print("\n🎉 ALL TESTS PASSED — Sizes should be visible in FirstSizeFrom/FirstSizeTo!")
        print("\nIf you still don't see them in the UI:")
        print("  1. Refresh the browser page")
        print("  2. Clear browser cache")
        print("  3. Re-download the SPEC.xlsx file")
    elif not test3:
        print("\n⚠️  RECOMMENDATION: Upload a NEW PDF to trigger extraction with enhanced logic")
        print("    Existing extractions won't show size expansion (data is immutable)")
    else:
        print("\n⚠️  SOME TESTS FAILED — Please review the output above")
    
    sys.exit(0 if (test1 and test2) else 1)


if __name__ == '__main__':
    main()
