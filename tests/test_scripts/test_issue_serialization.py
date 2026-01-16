#!/usr/bin/env python
"""
Comprehensive debugging script to test the new comprehensive issue serialization logic.
This script will test report ID 45 with various debugging methods.
"""
import os
import sys
import django

# Setup Django environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pid_analysis.models import PIDDrawing, PIDAnalysisReport, PIDIssue
from apps.pid_analysis.serializers import PIDAnalysisReportSerializer
from apps.pid_analysis.export_service import PIDReportExportService
import json
from datetime import datetime

def debug_report(report_id):
    """Debug a specific report comprehensively"""
    print(f"\n{'='*50}")
    print(f"DEBUGGING REPORT ID: {report_id}")
    print(f"{'='*50}")
    
    try:
        drawing = PIDDrawing.objects.get(id=report_id)
        report = drawing.analysis_report
        
        print(f"\nDRAWING INFO:")
        print(f"- ID: {drawing.id}")
        print(f"- Drawing Number: {drawing.drawing_number}")
        print(f"- Title: {drawing.drawing_title}")
        print(f"- Status: {drawing.status}")
        
        print(f"\nREPORT INFO:")
        print(f"- ID: {report.id}")
        print(f"- Total Issues: {report.total_issues}")
        print(f"- Pending: {report.pending_count}")
        print(f"- Approved: {report.approved_count}")
        print(f"- Ignored: {report.ignored_count}")
        
        print(f"\nDATABASE ISSUES CHECK:")
        db_issues = list(report.issues.all())
        print(f"- Database issues count: {len(db_issues)}")
        
        if db_issues:
            print("- First 3 database issues:")
            for issue in db_issues[:3]:
                print(f"  * Issue {issue.serial_number}: {issue.pid_reference} - {issue.issue_observed[:50]}...")
        else:
            print("- No database issues found")
        
        print(f"\nREPORT_DATA JSON CHECK:")
        if hasattr(report, 'report_data') and report.report_data:
            print(f"- report_data type: {type(report.report_data)}")
            if isinstance(report.report_data, dict):
                print(f"- report_data keys: {list(report.report_data.keys())}")
                
                # Check for issues in various locations
                possible_keys = ['issues', 'identified_issues', 'analysis_issues', 'pid_issues']
                for key in possible_keys:
                    if key in report.report_data:
                        issues = report.report_data[key]
                        print(f"- Found '{key}': {type(issues)} with {len(issues) if isinstance(issues, list) else 'N/A'} items")
                        if isinstance(issues, list) and issues:
                            print(f"  * First issue: {issues[0] if issues else 'Empty'}")
                
                # Check nested structure
                if 'analysis_result' in report.report_data:
                    analysis_result = report.report_data['analysis_result']
                    print(f"- analysis_result type: {type(analysis_result)}")
                    if isinstance(analysis_result, dict) and 'issues' in analysis_result:
                        nested_issues = analysis_result['issues']
                        print(f"- Nested issues: {type(nested_issues)} with {len(nested_issues) if isinstance(nested_issues, list) else 'N/A'} items")
            else:
                print(f"- report_data is not a dict: {str(report.report_data)[:200]}")
        else:
            print("- No report_data found")
        
        print(f"\nSERIALIZER TEST:")
        serializer = PIDAnalysisReportSerializer(report)
        serialized_data = serializer.data
        
        print(f"- Serializer issues count: {len(serialized_data.get('issues', []))}")
        print(f"- Debug info: {serialized_data.get('debug_info', 'No debug info')}")
        
        if serialized_data.get('issues'):
            print("- First serialized issue:")
            first_issue = serialized_data['issues'][0]
            print(f"  * Serial: {first_issue.get('serial_number')}")
            print(f"  * Reference: {first_issue.get('pid_reference')}")
            print(f"  * Issue: {first_issue.get('issue_observed')[:50]}...")
        
        print(f"\nEXPORT SERVICE TEST:")
        export_service = PIDReportExportService()
        
        # Test if export methods would work
        print("- Export service initialized successfully")
        print(f"- Company name: {export_service.company_name}")
        
        return True, drawing, report
        
    except PIDDrawing.DoesNotExist:
        print(f"❌ Drawing with ID {report_id} does not exist")
        return False, None, None
    except Exception as e:
        print(f"❌ Error debugging report: {str(e)}")
        import traceback
        traceback.print_exc()
        return False, None, None

def list_all_reports():
    """List all available reports for debugging"""
    print(f"\n{'='*50}")
    print("ALL AVAILABLE REPORTS")
    print(f"{'='*50}")
    
    drawings = PIDDrawing.objects.filter(status__in=['completed', 'processing']).order_by('-id')[:10]
    
    print(f"Found {drawings.count()} recent drawings:")
    for drawing in drawings:
        try:
            report = drawing.analysis_report
            print(f"- ID {drawing.id}: {drawing.drawing_number or 'No Number'} "
                  f"({drawing.status}) - {report.total_issues} issues")
        except:
            print(f"- ID {drawing.id}: {drawing.drawing_number or 'No Number'} "
                  f"({drawing.status}) - No report")

def main():
    """Main debugging function"""
    print("PID ANALYSIS ISSUE SERIALIZATION DEBUG")
    print("=" * 50)
    
    # List available reports
    list_all_reports()
    
    # Debug specific reports
    test_report_ids = [45, 44, 43]  # Production report IDs
    
    for report_id in test_report_ids:
        success, drawing, report = debug_report(report_id)
        
        if success and report.total_issues > 0:
            # Test if our new logic would generate placeholder data
            print(f"\nPLACEHOLDER TEST for Report {report_id}:")
            if not list(report.issues.all()) and not (hasattr(report, 'report_data') and 
                isinstance(report.report_data, dict) and 
                any(key in report.report_data for key in ['issues', 'identified_issues', 'analysis_issues', 'pid_issues'])):
                print("✅ Would generate placeholder data")
            else:
                print("⚠️ Would use existing data")
    
    print(f"\n{'='*50}")
    print("DEBUGGING COMPLETE")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()