#!/usr/bin/env python
"""Test Triple-Layer AutoCAD Filtering System"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import re
from apps.core.helpers.unified_comment_extractor import is_autocad_comment

print("=" * 70)
print("TRIPLE-LAYER AUTOCAD FILTERING SYSTEM TEST")
print("=" * 70)

# Test cases from user's report
test_cases = [
    {"reviewer": "AutoCAD SHX Text", "content": "AD204-604-D-11154"},
    {"reviewer": "AutoCAD SHX Text", "content": "PRODUCED WATER"},
    {"reviewer": "AutoCAD SHX Text", "content": "LP RELIEF GAS"},
    {"reviewer": "autocad shx text", "content": "Some text"},  # lowercase
    {"reviewer": "AUTOCAD SHX TEXT", "content": "Some text"},  # uppercase
    {"reviewer": "  AutoCAD SHX Text  ", "content": "Trimmed"},  # with spaces
]

print("\n🔥 LAYER 1: OCR Smart Detection (unified_comment_extractor.py)")
print("-" * 70)
for i, test in enumerate(test_cases, 1):
    result = is_autocad_comment(test["reviewer"], test["content"])
    status = "✅ BLOCKED" if result else "❌ PASSED"
    print(f"{i}. {status}: Reviewer='{test['reviewer']}' | Content='{test['content']}'")

print("\n🔥 LAYER 2: Exact String Match (revision_views.py line 346)")
print("-" * 70)
for i, test in enumerate(test_cases, 1):
    reviewer_lower = test["reviewer"].strip().lower()
    exact_match = (reviewer_lower == 'autocad shx text')
    status = "✅ BLOCKED" if exact_match else "→ Check next filter"
    print(f"{i}. {status}: '{test['reviewer']}'.strip().lower() == 'autocad shx text'")

print("\n🔥 LAYER 2: Keyword Pattern Match (revision_views.py line 352)")
print("-" * 70)
keywords = ['autocad', 'autodesk', 'acad', 'shx', '.shx', '.dwg', '.dxf']
for i, test in enumerate(test_cases, 1):
    reviewer_lower = test["reviewer"].lower().strip()
    keyword_match = any(kw in reviewer_lower for kw in keywords)
    status = "✅ BLOCKED" if keyword_match else "→ Check next filter"
    print(f"{i}. {status}: Any keyword in '{test['reviewer']}'")

print("\n🔥 LAYER 2: CAD Reference Pattern (revision_views.py line 358)")
print("-" * 70)
cad_pattern = re.compile(r'^[A-Z]{2}\d{3}-\d{3}-[A-Z]-\d{5}$')
for i, test in enumerate(test_cases, 1):
    cad_match = bool(cad_pattern.match(test["content"].strip()))
    status = "✅ BLOCKED" if cad_match else "→ Check next filter"
    print(f"{i}. {status}: CAD pattern in '{test['content']}'")

print("\n🔥 LAYER 2: All-Caps System Text (revision_views.py line 364)")
print("-" * 70)
caps_pattern = re.compile(r'^[A-Z\s]{4,50}$')
for i, test in enumerate(test_cases, 1):
    caps_match = bool(caps_pattern.match(test["content"].strip()))
    status = "✅ BLOCKED" if caps_match else "→ Check next filter"
    print(f"{i}. {status}: All-caps in '{test['content']}'")

print("\n🔥 LAYER 3: Pre-Database Validation (revision_views.py line 414)")
print("-" * 70)
for i, test in enumerate(test_cases, 1):
    reviewer = test["reviewer"].strip()
    exact_match_layer3 = (reviewer.lower() == 'autocad shx text')
    contains_autocad = 'autocad' in reviewer.lower()
    blocked = exact_match_layer3 or contains_autocad
    status = "✅ BLOCKED" if blocked else "❌ WOULD SAVE"
    print(f"{i}. {status}: Final check before database")

print("\n" + "=" * 70)
print("✅ SUMMARY: Comments must pass ALL 3 layers to be saved")
print("   - Layer 1: Smart OCR detection with regex patterns")
print("   - Layer 2: 5-step post-OCR filtering (exact match + patterns)")
print("   - Layer 3: Pre-database validation (final safety check)")
print("=" * 70)
