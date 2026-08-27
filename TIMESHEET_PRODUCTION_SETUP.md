# Timesheet Mirror Setup for Production (Railway)

## Problem
The HR Employees page (`https://www.radai.ae/hr/employees`) shows:
> "Waiting for the first sync from the office. The biometric mirror is set up but no events have been received yet."

This happens because production uses `TIMESHEET_DATA_SOURCE=mirror` mode, which reads from the `TimesheetEvent` table in your PostgreSQL database. This table is currently empty.

## Architecture

```
┌─────────────────────┐         ┌──────────────────────┐
│ Local Development   │         │  Production (Railway)│
│ (Office Network)    │         │                      │
├─────────────────────┤         ├──────────────────────┤
│ Direct SQL Server   │         │  Postgres Mirror     │
│ TIMESHEET_DATA_     │         │  TIMESHEET_DATA_     │
│ SOURCE=sqlserver    │         │  SOURCE=mirror       │
│                     │         │                      │
│ ✅ Works fine       │         │  ❌ Empty table      │
└─────────────────────┘         └──────────────────────┘
```

## Solution Options

### Option 1: Quick Fix - Seed Sample Data (Recommended for Testing)

Run this command on Railway to populate the database with sample attendance data:

```bash
railway run python manage.py seed_timesheet_mirror --days 14 --employees 30
```

**Parameters:**
- `--days N`: Number of days of history (default: 7)
- `--employees N`: Number of sample employees (default: 20)
- `--clear`: Clear existing events first

**Example outputs:**
```bash
# Seed last 30 days for 50 employees
railway run python manage.py seed_timesheet_mirror --days 30 --employees 50

# Clear and reseed
railway run python manage.py seed_timesheet_mirror --clear --days 14
```

After running this, visit `https://www.radai.ae/hr/employees` and the timesheet data should appear.

---

### Option 2: Set Up Real Sync Agent (For Production Use)

The `timesheet_mirror_sync.py` script should run on your office network to continuously sync real biometric data to Railway.

#### Architecture:
```
[Office SQL Server] → [timesheet_mirror_sync.py] → [Railway API]
    192.168.99.52         (runs on office PC)      (production)
```

#### Steps:

**1. Configure Railway Environment Variables:**

In your Railway dashboard, set these env vars:

```bash
TIMESHEET_DATA_SOURCE=mirror
TIMESHEET_MIRROR_API_KEY=<generate-strong-random-key>
TIMESHEET_LIVE_LOOKBACK_HOURS=20
```

Generate API key:
```bash
openssl rand -hex 32
# Example: a7f9c3d8e2b1a6f4d9c8e7b3a2f1d6c9e8b7a6f5d4c3e2b1a9f8d7c6e5b4a3
```

**2. Configure the supplied Sync Agent:**

The maintained agent and current step-by-step instructions are available at:

- `scripts/timesheet_mirror_sync.py`
- `scripts/TIMESHEET_MIRROR_SETUP.md`

Do not copy the obsolete sample implementation below for a new installation.
Use the supplied agent because it supports the confirmed Matrix schema,
idempotent batching, retries, user-master synchronization and preflight checks.

<details>
<summary>Legacy example (reference only)</summary>

```python
#!/usr/bin/env python3
"""
Timesheet Mirror Sync Agent
Runs on office network, syncs biometric data to Railway production.
"""
import pyodbc
import requests
import hashlib
from datetime import datetime, timedelta

# Configuration
SQL_SERVER = "192.168.99.52"
SQL_DATABASE = "YourBiometricDB"
SQL_USER = "readonly_user"
SQL_PASSWORD = "your_password"

RAILWAY_API = "https://radai-backend-production.up.railway.app/api/v1/timesheet/mirror/ingest/"
API_KEY = "your-mirror-api-key-here"  # Must match Railway env var

def generate_event_id(emp_code, event_time, event_type):
    """Generate deterministic ID for idempotent upserts"""
    key = f"{emp_code}|{event_time.isoformat()}|{event_type}"
    return hashlib.sha256(key.encode()).hexdigest()[:64]

def sync_events(hours=24):
    """Sync last N hours of events to Railway"""
    
    # Connect to SQL Server
    conn = pyodbc.connect(
        f'DRIVER={{SQL Server}};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};'
        f'UID={SQL_USER};PWD={SQL_PASSWORD}'
    )
    
    # Query events
    cutoff = datetime.now() - timedelta(hours=hours)
    query = f"""
        SELECT EmpCode, EmpName, PunchTime, PunchType
        FROM Mx_Transaction  -- Adjust table name
        WHERE PunchTime >= ?
        ORDER BY PunchTime
    """
    
    cursor = conn.cursor()
    cursor.execute(query, cutoff)
    
    events = []
    for row in cursor.fetchall():
        events.append({
            "source_event_id": generate_event_id(row.EmpCode, row.PunchTime, row.PunchType),
            "employee_code": row.EmpCode,
            "employee_name": row.EmpName,
            "employee_email": "",  # Add if available
            "department": "",  # Add if available
            "event_time": row.PunchTime.isoformat(),
            "event_type": row.PunchType,
        })
    
    conn.close()
    
    # Send to Railway
    if events:
        response = requests.post(
            RAILWAY_API,
            json={"events": events},
            headers={"X-Timesheet-Mirror-Key": API_KEY},
            timeout=60
        )
        
        if response.status_code == 200:
            print(f"✅ Synced {len(events)} events")
        else:
            print(f"❌ Failed: {response.status_code} - {response.text}")
    else:
        print("No events to sync")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--hours', type=int, default=24, help='Hours of history to sync')
    args = parser.parse_args()
    
    sync_events(args.hours)
```

</details>

**3. Schedule the Sync Agent:**

Run it every hour on your office PC:

```bash
# Windows Task Scheduler
schtasks /create /tn "Timesheet Sync" /tr "python C:\path\to\timesheet_mirror_sync.py" /sc hourly

# Or Linux cron
0 * * * * python /path/to/timesheet_mirror_sync.py >> /var/log/timesheet_sync.log 2>&1
```

---

### Option 3: Switch Back to SQL Server Mode (Not Recommended for Production)

If Railway could access your office network (via VPN/tunnel), you could use:

```bash
TIMESHEET_DATA_SOURCE=sqlserver
TIMESHEET_HOST=192.168.99.52
TIMESHEET_USER=your_sql_user
TIMESHEET_PASSWORD=your_sql_password
```

**Why not recommended:**
- Railway can't access private office networks
- Security risk exposing SQL Server to internet
- Sync agent (Option 2) is the proper production solution

---

## Testing the Setup

### Check Railway Environment:

```bash
railway run python manage.py diagnose_mirror
```

This shows:
- Current `DATA_SOURCE` setting
- Number of events in the table
- Latest event timestamp
- Configuration issues

### Check via API:

```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  https://radai-backend-production.up.railway.app/api/v1/timesheet/health/
```

Should return:
```json
{
  "data_source": "mirror",
  "ping": {
    "ok": true,
    "mode": "mirror",
    "event_count": 1234,
    "latest_event": "2026-08-21T14:30:00Z"
  }
}
```

---

## Current Status

✅ **Local Development** (`localhost:5173`):
- Uses `TIMESHEET_DATA_SOURCE=sqlserver`
- Direct connection to office SQL Server
- Works perfectly ✓

❌ **Production** (`www.radai.ae`):
- Uses `TIMESHEET_DATA_SOURCE=mirror`
- Postgres `TimesheetEvent` table is **empty**
- Shows "Waiting for first sync" error

---

## Immediate Action (Choose One)

### For Testing (Quick):
```bash
railway run python manage.py seed_timesheet_mirror --days 14 --employees 30
```
This gives you sample data immediately so you can demo the HR page.

### For Production (Permanent):
1. Set up the sync agent on an office PC
2. Configure Railway API key
3. Schedule hourly syncs
4. Monitor with `diagnose_mirror` command

---

## Troubleshooting

### "Mirror ingest disabled (no key configured)"
**Fix:** Set `TIMESHEET_MIRROR_API_KEY` in Railway environment variables.

### "Invalid mirror key"
**Fix:** Ensure the API key in your sync script matches Railway's `TIMESHEET_MIRROR_API_KEY`.

### Events not appearing in Live view
**Fix:** Check `TIMESHEET_LIVE_LOOKBACK_HOURS` (default: 20). Live only shows last N hours.

### Need to reseed data
```bash
railway run python manage.py seed_timesheet_mirror --clear --days 30
```

---

## Documentation Files

- **This file**: Setup guide for production
- `apps/timesheet/models.py`: TimesheetEvent model definition
- `apps/timesheet/mirror_views.py`: Ingest API endpoint documentation
- `apps/timesheet/config.py`: All configuration options

---

## Questions?

Run diagnostics:
```bash
railway run python manage.py diagnose_mirror
```

Check logs:
```bash
railway logs --tail 100
```

Inspect specific employee:
```bash
railway run python manage.py inspect_biometric --email employee@rejlers.com
```
