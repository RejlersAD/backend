# Script to check and document the format type issues

print("""
🔍 FORMAT TYPE ISSUE ANALYSIS:

PROBLEM:
--------
User uploads work ONLY for 'ADNOC Onshore' button, but not for:
- ADNOC Offshore  
- General

ROOT CAUSE:
-----------
1. Frontend (DesignIQLists.jsx):
   - handlePIDUpload function has formatType parameter
   - But only 'onshore' upload actually works
   - Other buttons ('offshore', 'general') are not configured properly

2. Backend (views.py + pid_ocr_extractor_v2.py):
   - Expects format_type parameter: 'onshore', 'offshore', or 'general'
   - Currently only has regex patterns for 'onshore' and 'offshore'
   - Missing 'general' format implementation

FIX STRATEGY:
-------------
1. ✅ Check frontend sends correct format_type for all 3 buttons
2. ✅ Add 'general' format regex patterns in backend
3. ✅ Ensure all 3 formats work end-to-end

FORMAT SPECIFICATIONS:
----------------------
FORMAT 1: ADNOC ONSHORE (with area)
  Pattern: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
  Example: 4"-41-SWR-64313-A2AU16-V
  Usage: include_area=true, format_type='onshore'

FORMAT 2: ADNOC OFFSHORE
  Pattern: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
  Example: 604-HO-8-BC2GA0-1071-H
  Usage: include_area=false, format_type='offshore'

FORMAT 3: GENERAL (without area)
  Pattern: SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
  Example: 12-D-5777-033842-N
  Usage: include_area=false, format_type='general' OR format_type='onshore'

NOTE: General format is same as onshore without area, so we can reuse onshore logic!
""")
