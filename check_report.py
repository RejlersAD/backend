#!/usr/bin/env python
"""
Quick script to check report data in Railway database
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pid_analysis.models import PIDAnalysisReport
from apps.pid_analysis.serializers import PIDAnalysisReportSerializer
import json

# Get report #29
try:
    report = PIDAnalysisReport.objects.get(pid_drawing_id=29)
    print(f"✓ Found report for drawing #{29}")
    print(f"  Total issues in DB: {report.total_issues}")
    print(f"  Report data type: {type(report.report_data)}")
    
    if isinstance(report.report_data, dict):
        issues_in_json = report.report_data.get('issues', [])
        print(f"  Issues in report_data JSON: {len(issues_in_json)}")
        
        if issues_in_json:
            print(f"\n  First issue sample:")
            first_issue = issues_in_json[0]
            print(f"    - Title: {first_issue.get('title', 'N/A')}")
            print(f"    - Severity: {first_issue.get('severity', 'N/A')}")
            print(f"    - Has location_on_drawing: {'location_on_drawing' in first_issue}")
    else:
        print(f"  WARNING: report_data is not a dict!")
        print(f"  Value: {report.report_data}")
    
    # Check related PIDIssue objects
    related_issues_count = report.issues.count()
    print(f"\n  Related PIDIssue objects: {related_issues_count}")
    
    # Test serializer
    print(f"\n--- Testing Serializer ---")
    serializer = PIDAnalysisReportSerializer(report)
    serialized_data = serializer.data
    print(f"  Serialized issues count: {len(serialized_data.get('issues', []))}")
    
    if serialized_data.get('issues'):
        print(f"\n  First serialized issue:")
        first = serialized_data['issues'][0]
        print(f"    - Title: {first.get('title', 'N/A')}")
        print(f"    - Severity: {first.get('severity', 'N/A')}")
        print(f"    - Has location_on_drawing: {'location_on_drawing' in first}")
        if 'location_on_drawing' in first:
            print(f"    - Location data: {first['location_on_drawing']}")
    else:
        print(f"  ERROR: No issues in serialized data!")
        print(f"  Full serialized data keys: {list(serialized_data.keys())}")
        
except PIDAnalysisReport.DoesNotExist:
    print(f"✗ No report found for drawing #{29}")
    print("\nAvailable reports:")
    for r in PIDAnalysisReport.objects.all().order_by('-id')[:10]:
        print(f"  - Report ID {r.id} (Drawing #{r.pid_drawing_id}): {r.total_issues} issues")
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
