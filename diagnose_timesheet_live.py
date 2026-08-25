#!/usr/bin/env python
"""Quick diagnostic for timesheet Live view issues"""
import os
import django

# Initialize Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.timesheet.sqlserver import connect
from apps.timesheet import services
import json

print("=" * 80)
print("TIMESHEET LIVE VIEW DIAGNOSTIC")
print("=" * 80)

# Test 1: Connection
print("\n1. Testing SQL Server connection...")
try:
    with connect() as cur:
        cur.execute('SELECT TOP 1 1 AS test')
        result = cur.fetchone()
        print("✅ Connection OK")
except Exception as e:
    print(f"❌ Connection failed: {e}")
    exit(1)

# Test 2: Check table exists and has data
print("\n2. Checking table data...")
try:
    with connect() as cur:
        cur.execute('SELECT COUNT(*) AS total FROM dbo.Mx_VEW_UserAttendanceEvents')
        row = cur.fetchone()
        total = row['total'] if isinstance(row, dict) else row[0]
        print(f"✅ Table has {total:,} total records")
except Exception as e:
    print(f"❌ Table query failed: {e}")
    exit(1)

# Test 3: Check recent data (last 20 hours)
print("\n3. Checking recent data (last 20 hours)...")
try:
    with connect() as cur:
        cur.execute('''
            SELECT COUNT(*) AS recent_count 
            FROM dbo.Mx_VEW_UserAttendanceEvents 
            WHERE AttendanceTime >= DATEADD(HOUR, -20, GETDATE())
        ''')
        row = cur.fetchone()
        recent = row['recent_count'] if isinstance(row, dict) else row[0]
        print(f"{'✅' if recent > 0 else '⚠️ '} Found {recent:,} events in last 20 hours")
        
        if recent == 0:
            # Check most recent event
            cur.execute('''
                SELECT TOP 1 AttendanceTime 
                FROM dbo.Mx_VEW_UserAttendanceEvents 
                ORDER BY AttendanceTime DESC
            ''')
            latest_row = cur.fetchone()
            if latest_row:
                latest_time = latest_row['AttendanceTime'] if isinstance(latest_row, dict) else latest_row[0]
                print(f"   Most recent event: {latest_time}")
            else:
                print("   No events found in table!")
except Exception as e:
    print(f"❌ Recent data query failed: {e}")

# Test 4: Call the actual live_status() service
print("\n4. Testing live_status() service...")
try:
    result = services.live_status()
    print(f"✅ Service returned: {len(result.get('rows', []))} rows")
    print(f"   Summary: {result.get('summary', {})}")
    print(f"   Lookback hours: {result.get('lookback_hours', 'N/A')}")
    print(f"   Window from: {result.get('window_from', 'N/A')}")
except Exception as e:
    print(f"❌ Service call failed: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)
