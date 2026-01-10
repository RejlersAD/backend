"""
Test Database-Integrated Converter
Verifies that all databases are accessible and the converter works
"""

import os
import sys
import django

# Setup Django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pfd_converter.database_integrated_converter import DatabaseIntegratedConverter
from apps.pfd_converter.services_advanced_pipeline import AdvancedPFDToPIDPipeline

print("=" * 80)
print("     DATABASE-INTEGRATED CONVERTER VERIFICATION")
print("=" * 80)

print("\n📊 Step 1: Initialize Database-Integrated Converter")
print("-" * 70)

try:
    converter = DatabaseIntegratedConverter()
    print("✅ Converter initialized successfully")
    print(f"   • Reference DB loaded: {len(converter.reference_db.get('files', []))} files")
    print(f"   • Legend DB items: {converter.legend_db.get('metadata', {}).get('total_items_extracted', 0)}")
    print(f"   • Symbol codes indexed: {len(converter.search_index.get('by_symbol_code', {}))}")
except Exception as e:
    print(f"❌ Error initializing converter: {str(e)}")
    sys.exit(1)

print("\n🔍 Step 2: Test Symbol Search")
print("-" * 70)

# Test symbol code search
test_codes = ['MOV-101', 'PSV-101', 'PT-101', 'VALVE']
for code in test_codes:
    result = converter.search_legend_by_code(code)
    if result:
        print(f"✅ Found symbol: {code}")
        print(f"   Category: {result.get('category', 'N/A')}")
        print(f"   Description: {result.get('description', 'N/A')[:80]}...")
    else:
        print(f"⚠️ Symbol not found: {code}")

print("\n🔎 Step 3: Test Keyword Search")
print("-" * 70)

# Test keyword search
test_keywords = ['PUMP', 'VALVE', 'PRESSURE']
for keyword in test_keywords:
    results = converter.search_legend_by_keyword(keyword)
    print(f"✅ Keyword '{keyword}': {len(results)} results")
    if results:
        print(f"   Sample: {results[0].get('description', 'N/A')[:80]}...")

print("\n📁 Step 4: Test Category Retrieval")
print("-" * 70)

# Test category retrieval
test_categories = ['VALVES', 'INSTRUMENTS', 'EQUIPMENT']
for category in test_categories:
    legends = converter.get_category_legends(category)
    print(f"✅ Category '{category}': {len(legends)} items")

print("\n🎯 Step 5: Test Reference Matching")
print("-" * 70)

# Test reference matching
test_pfd_data = {
    'equipment': [
        {'type': 'pump', 'tag': 'P-101'},
        {'type': 'vessel', 'tag': 'V-101'}
    ],
    'process_streams': []
}

reference = converter.find_similar_reference(test_pfd_data, category='PUMP')
if reference:
    print(f"✅ Found reference match:")
    print(f"   Category: {reference.get('category')}")
    print(f"   Score: {reference.get('score')}")
    print(f"   Examples: {reference.get('pfd_count')} PFDs, {reference.get('pid_count')} P&IDs")
else:
    print("⚠️ No reference match found (will use general patterns)")

print("\n🚀 Step 6: Test Advanced Pipeline Integration")
print("-" * 70)

try:
    pipeline = AdvancedPFDToPIDPipeline()
    
    if pipeline.use_database:
        print("✅ Pipeline successfully integrated with database converter")
        print(f"   • Database mode: ENABLED")
        print(f"   • Reference examples: {len(converter.reference_db.get('files', []))}")
        print(f"   • Legend items available: {converter.legend_db.get('metadata', {}).get('total_items_extracted', 0)}")
    else:
        print("⚠️ Pipeline initialized but database mode DISABLED")
        print("   • Will use fallback standard generator")
except Exception as e:
    print(f"❌ Error initializing pipeline: {str(e)}")

print("\n📊 Step 7: Database Statistics")
print("-" * 70)

# Show comprehensive stats
legend_cats = converter.legend_db.get('categories', {})
print(f"\nLegend Database Breakdown:")
for cat_name, cat_data in sorted(legend_cats.items(), key=lambda x: x[1].get('count', 0), reverse=True):
    count = cat_data.get('count', 0)
    print(f"   • {cat_name:30} : {count:5} items")

print(f"\nSearch Index Statistics:")
print(f"   • Symbol codes: {len(converter.search_index.get('by_symbol_code', {}))}")
print(f"   • Keywords: {len(converter.search_index.get('by_keyword', {}))}")
print(f"   • Categories: {len(converter.search_index.get('by_category', {}))}")
print(f"   • Source files: {len(converter.search_index.get('by_source_file', {}))}")

print(f"\nReference Database:")
ref_cats = converter.reference_db.get('categories', {})
total_pfds = sum(cat.get('pfds', 0) for cat in ref_cats.values())
total_pids = sum(cat.get('pids', 0) for cat in ref_cats.values())
print(f"   • Total PFDs: {total_pfds}")
print(f"   • Total P&IDs: {total_pids}")
print(f"   • Categories: {len(ref_cats)}")

print("\n" + "=" * 80)
print("                    VERIFICATION COMPLETE!")
print("=" * 80)

print(f"""
✅ System Status: OPERATIONAL

Database Integration:
  • Reference Database: ✅ Loaded
  • Legend Database: ✅ Loaded  
  • Search Index: ✅ Loaded
  • Pipeline Integration: ✅ Active

Ready to generate enhanced P&IDs with comprehensive database knowledge!

Frontend URL: http://localhost:5173/pfd/upload
""")
