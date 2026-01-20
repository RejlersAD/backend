#!/usr/bin/env python
"""Test FINAL HTML and Response Filters"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

print("=" * 80)
print("🔥 FINAL LAYER: HTML & RESPONSE POST-PROCESSING FILTERS")
print("=" * 80)

# Simulate HTML with AutoCAD comments
test_html = """
<html>
<body>
<table>
    <tr><td>Page 1</td><td>Normal comment</td></tr>
    <tr><td>Page 4</td><td>AD204-604-D-11154</td></tr>
    <tr><td>Page 4</td><td>PRODUCED WATER</td></tr>
    <tr><td>Page 4</td><td>LP RELIEF GAS</td></tr>
    <tr><td>Page 5</td><td>Another normal comment</td></tr>
    <tr><td>Page 6</td><td>AutoCAD SHX Text comment</td></tr>
</table>
</body>
</html>
"""

print("\n📄 ORIGINAL HTML:")
print("-" * 80)
print(f"Total rows: {test_html.count('<tr>')}")
print(f"Contains 'AD204-604-D-11154': {('AD204-604-D-11154' in test_html)}")
print(f"Contains 'PRODUCED WATER': {('PRODUCED WATER' in test_html)}")
print(f"Contains 'LP RELIEF GAS': {('LP RELIEF GAS' in test_html)}")
print(f"Contains 'AutoCAD': {('AutoCAD' in test_html)}")

# Test HTML filter
from apps.crs.s3_excel_generator import CRSS3ExcelGenerator
generator = CRSS3ExcelGenerator()
filtered_html = generator._remove_autocad_from_html(test_html)

print("\n🔥 AFTER HTML FILTER:")
print("-" * 80)
print(f"Total rows: {filtered_html.count('<tr>')}")
print(f"Contains 'AD204-604-D-11154': {('AD204-604-D-11154' in filtered_html)}")
print(f"Contains 'PRODUCED WATER': {('PRODUCED WATER' in filtered_html)}")
print(f"Contains 'LP RELIEF GAS': {('LP RELIEF GAS' in filtered_html)}")
print(f"Contains 'AutoCAD': {('AutoCAD' in filtered_html)}")

# Test response filter
test_comments = [
    {"reviewer": "John Doe", "text": "Normal comment", "page": 1},
    {"reviewer": "AutoCAD SHX Text", "text": "AD204-604-D-11154", "page": 4},
    {"reviewer": "AutoCAD SHX Text", "text": "PRODUCED WATER", "page": 4},
    {"reviewer": "AutoCAD SHX Text", "text": "LP RELIEF GAS", "page": 4},
    {"reviewer": "Jane Smith", "text": "Another comment", "page": 5},
]

print("\n📋 ORIGINAL COMMENTS LIST:")
print("-" * 80)
print(f"Total comments: {len(test_comments)}")
print(f"AutoCAD comments: {sum(1 for c in test_comments if 'autocad' in c['reviewer'].lower())}")

# Import and test response filter
from apps.crs.revision_views import CRSRevisionChainViewSet
viewset = CRSRevisionChainViewSet()
filtered_comments = viewset._filter_autocad_from_response(test_comments)

print("\n🔥 AFTER RESPONSE FILTER:")
print("-" * 80)
print(f"Total comments: {len(filtered_comments)}")
print(f"AutoCAD comments: {sum(1 for c in filtered_comments if 'autocad' in c['reviewer'].lower())}")

# Summary
print("\n" + "=" * 80)
print("✅ FINAL FILTERS SUMMARY")
print("=" * 80)
print(f"HTML Filter: Removed {test_html.count('<tr>') - filtered_html.count('<tr>')} rows")
print(f"Response Filter: Removed {len(test_comments) - len(filtered_comments)} comments")
print("")
if filtered_html.count('<tr>') == 2 and len(filtered_comments) == 2:
    print("🎉 SUCCESS: All AutoCAD entries removed!")
    print("   - Only 2 normal comments remain (John Doe, Jane Smith)")
    print("   - All 3 AutoCAD entries blocked (AD204-604-D-11154, PRODUCED WATER, LP RELIEF GAS)")
else:
    print("❌ ISSUE: Some AutoCAD entries still present")
print("=" * 80)
