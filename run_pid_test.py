"""
Quick standalone test script - runs PID analysis on /tmp/test_pid.pdf and prints all findings.
Run inside container: python run_pid_test.py
"""
import os
import sys
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, '/app')

import django
django.setup()

from apps.pid_analysis.services import PIDAnalysisService

print('[INFO] Starting analysis with updated rules (Rules 11-18)...')
print('[INFO] PDF: /tmp/test_pid.pdf')
print('')

svc = PIDAnalysisService()

# Patch _parse_analysis_response to also save the raw response for debugging
_orig_parse = svc._parse_analysis_response
def _debug_parse(response_text, tokens_used):
    print('[DEBUG RAW RESPONSE] tokens=%d | len=%d' % (tokens_used, len(response_text)))
    return _orig_parse(response_text, tokens_used)
svc._parse_analysis_response = _debug_parse

result = svc.analyze_pid_drawing('/tmp/test_pid.pdf', drawing_number='TEST-DEBASIS')

print('')
print('=' * 70)
print('ANALYSIS RESULTS SUMMARY')
print('=' * 70)
print('Total issues   :', result['total_issues'])
print('Critical       :', result['critical_count'])
print('Major          :', result['major_count'])
print('Minor          :', result['minor_count'])
print('Confidence     :', result.get('confidence'))
meta = result.get('analysis_metadata', {})
print('Instruments OCR:', meta.get('instrument_tags_found'))
print('Equipment OCR  :', meta.get('equipment_tags_found'))
print('Lines OCR      :', meta.get('line_numbers_found'))
print('=' * 70)
print('')
print('--- DETAILED FINDINGS ---')
print('')

for i in result['issues']:
    print(f"[{i['serial_number']:02d}] SEVERITY={i['severity'].upper()} | CATEGORY={i['category']}")
    print(f"     REF    : {i['pid_reference']}")
    print(f"     ISSUE  : {i['issue_observed'][:200]}")
    print(f"     ACTION : {i['action_required'][:150]}")
    loc = i.get('location_on_drawing', {})
    if loc:
        print(f"     ZONE   : {loc.get('zone')} | {loc.get('proximity_description', '')[:80]}")
    print()

print('=' * 70)
print('FULL JSON (for debugging):')
print(json.dumps(result['issues'], indent=2, ensure_ascii=False)[:3000])
