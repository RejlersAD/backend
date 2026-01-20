#!/usr/bin/env python
"""Quick test of AutoCAD filtering with user's exact examples"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.core.helpers.unified_comment_extractor import is_autocad_comment

print("=" * 60)
print("Testing AutoCAD Filter with Your Exact Examples")
print("=" * 60)

# Test 1: AutoCAD SHX Text + CAD Reference
test1_reviewer = "AutoCAD SHX Text"
test1_content = "AD204-604-D-11154"
result1 = is_autocad_comment(test1_reviewer, test1_content)
print(f"\n✓ Test 1:")
print(f"  Reviewer: '{test1_reviewer}'")
print(f"  Content: '{test1_content}'")
print(f"  BLOCKED: {result1}")

# Test 2: AutoCAD SHX Text + All-caps text
test2_reviewer = "AutoCAD SHX Text"
test2_content = "PRODUCED WATER"
result2 = is_autocad_comment(test2_reviewer, test2_content)
print(f"\n✓ Test 2:")
print(f"  Reviewer: '{test2_reviewer}'")
print(f"  Content: '{test2_content}'")
print(f"  BLOCKED: {result2}")

# Test 3: AutoCAD SHX Text + All-caps text
test3_reviewer = "AutoCAD SHX Text"
test3_content = "LP RELIEF GAS"
result3 = is_autocad_comment(test3_reviewer, test3_content)
print(f"\n✓ Test 3:")
print(f"  Reviewer: '{test3_reviewer}'")
print(f"  Content: '{test3_content}'")
print(f"  BLOCKED: {result3}")

# Summary
print("\n" + "=" * 60)
if all([result1, result2, result3]):
    print("✅ ALL TESTS PASSED - All AutoCAD comments will be blocked!")
else:
    print("❌ SOME TESTS FAILED")
print("=" * 60)
