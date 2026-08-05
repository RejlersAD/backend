#!/usr/bin/env python3
"""
RAD AI Attendance Mirror Sync Agent
====================================
Syncs biometric attendance data from the on-prem Matrix SQL Server to Railway.

This script runs on the office server (Windows/Linux) and continuously:
1. Queries the Matrix SQL Server for new punch events
2. Generates deterministic event IDs (idempotent upserts)
3. POSTs batches to Railway's /api/v1/timesheet/mirror/ingest/ endpoint
4. Optionally syncs user master data from Mx_VEW_UserDetails

Usage:
    # Sync last 48 hours (recommended for initial setup)
    python timesheet_mirror_sync.py --hours 48

    # Continuous sync mode (runs forever, sync every 5 minutes)
    python timesheet_mirror_sync.py --daemon --interval 300

    # Full historical sync (30 days)
    python timesheet_mirror_sync.py --hours 720

    # Sync with user master data refresh
    python timesheet_mirror_sync.py --hours 48 --sync-users

Environment Variables (required):
    TIMESHEET_HOST                  Matrix SQL Server host (e.g., 192.168.99.52)
    TIMESHEET_PORT                  Matrix SQL Server port (default: 1433)
    TIMESHEET_USER                  Database username
    TIMESHEET_PASSWORD              Database password
    TIMESHEET_DATABASE              Database name
    TIMESHEET_TABLE                 Attendance table (e.g., dbo.AttendanceLog)
    TIMESHEET_MIRROR_API_KEY        Shared secret for Railway API
    RAILWAY_BACKEND_URL             Railway backend URL (e.g., https://aiflowbackend-production.up.railway.app)

Optional Environment Variables:
    TIMESHEET_COL_EMPCODE           Employee code column (default: EmpCode)
    TIMESHEET_COL_NAME              Employee name column (default: EmpName)
    TIMESHEET_COL_EMAIL             Employee email column (default: empty)
    TIMESHEET_COL_DEPT              Department column (default: empty)
    TIMESHEET_COL_PUNCH_TIME        Punch time column (default: PunchTime)
    TIMESHEET_COL_PUNCH_TYPE        Punch type column (default: PunchType)
    TIMESHEET_IN_VALUE              IN punch value (default: IN)
    TIMESHEET_OUT_VALUE             OUT punch value (default: OUT)
    TIMESHEET_BATCH_SIZE            Events per POST batch (default: 1000)
    TIMESHEET_USER_DETAILS_TABLE    User master table (default: dbo.Mx_VEW_UserDetails)
"""

import os
import sys
import time
import logging
import argparse
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('timesheet_sync.log', mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Try to import SQL Server drivers
try:
    import pymssql
    HAS_PYMSSQL = True
    logger.info("Using pymssql driver")
except ImportError:
    HAS_PYMSSQL = False
    logger.warning("pymssql not available")

try:
    import pyodbc
    HAS_PYODBC = True
    if not HAS_PYMSSQL:
        logger.info("Using pyodbc driver")
except ImportError:
    HAS_PYODBC = False
    if not HAS_PYMSSQL:
        logger.warning("pyodbc not available")

if not HAS_PYMSSQL and not HAS_PYODBC:
    logger.error("No SQL Server driver available. Install pymssql or pyodbc.")
    sys.exit(1)

try:
    import requests
except ImportError:
    logger.error("requests library not found. Install: pip install requests")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration from environment
# ─────────────────────────────────────────────────────────────────────────────
def get_config() -> Dict[str, Any]:
    """Load configuration from environment variables."""
    config = {
        # SQL Server connection
        'host': os.getenv('TIMESHEET_HOST', '192.168.99.52'),
        'port': int(os.getenv('TIMESHEET_PORT', '1433')),
        'user': os.getenv('TIMESHEET_USER', ''),
        'password': os.getenv('TIMESHEET_PASSWORD', ''),
        'database': os.getenv('TIMESHEET_DATABASE', ''),
        'timeout': int(os.getenv('TIMESHEET_TIMEOUT', '10')),
        
        # Schema mapping
        'table': os.getenv('TIMESHEET_TABLE', ''),
        'col_empcode': os.getenv('TIMESHEET_COL_EMPCODE', 'EmpCode'),
        'col_name': os.getenv('TIMESHEET_COL_NAME', 'EmpName'),
        'col_email': os.getenv('TIMESHEET_COL_EMAIL', ''),
        'col_dept': os.getenv('TIMESHEET_COL_DEPT', ''),
        'col_punch_time': os.getenv('TIMESHEET_COL_PUNCH_TIME', 'PunchTime'),
        'col_punch_type': os.getenv('TIMESHEET_COL_PUNCH_TYPE', 'PunchType'),
        'in_value': os.getenv('TIMESHEET_IN_VALUE', 'IN'),
        'out_value': os.getenv('TIMESHEET_OUT_VALUE', 'OUT'),
        
        # User details table
        'user_details_table': os.getenv('TIMESHEET_USER_DETAILS_TABLE', 'dbo.Mx_VEW_UserDetails'),
        
        # Railway API
        'api_key': os.getenv('TIMESHEET_MIRROR_API_KEY', ''),
        'backend_url': os.getenv('RAILWAY_BACKEND_URL', 'https://aiflowbackend-production.up.railway.app'),
        
        # Sync settings
        'batch_size': int(os.getenv('TIMESHEET_BATCH_SIZE', '1000')),
    }
    
    # Validate required fields
    required = ['user', 'password', 'database', 'table', 'api_key']
    missing = [k for k in required if not config.get(k)]
    if missing:
        logger.error(f"Missing required environment variables: {', '.join([k.upper() for k in missing])}")
        logger.error("Set these in your environment or .env file")
        sys.exit(1)
    
    return config


# ─────────────────────────────────────────────────────────────────────────────
# SQL Server connection
# ─────────────────────────────────────────────────────────────────────────────
def connect_sqlserver(config: Dict[str, Any]):
    """Connect to SQL Server using available driver."""
    if HAS_PYMSSQL:
        return pymssql.connect(
            server=config['host'],
            port=str(config['port']),
            user=config['user'],
            password=config['password'],
            database=config['database'],
            timeout=config['timeout'],
            as_dict=True
        )
    elif HAS_PYODBC:
        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={config['host']},{config['port']};"
            f"DATABASE={config['database']};"
            f"UID={config['user']};"
            f"PWD={config['password']};"
            f"Timeout={config['timeout']};"
        )
        return pyodbc.connect(conn_str)
    else:
        raise RuntimeError("No SQL Server driver available")


# ─────────────────────────────────────────────────────────────────────────────
# Event ID generation (deterministic hash for idempotent upserts)
# ─────────────────────────────────────────────────────────────────────────────
def generate_event_id(employee_code: str, event_time: datetime, event_type: str) -> str:
    """Generate deterministic event ID from (employee_code, event_time, event_type)."""
    # Normalize inputs
    emp_code = str(employee_code).strip().upper()
    evt_time = event_time.strftime('%Y-%m-%d %H:%M:%S') if isinstance(event_time, datetime) else str(event_time)
    evt_type = str(event_type).strip().upper()
    
    # Hash
    content = f"{emp_code}|{evt_time}|{evt_type}"
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:32]


# ─────────────────────────────────────────────────────────────────────────────
# Fetch events from SQL Server
# ─────────────────────────────────────────────────────────────────────────────
def fetch_events(config: Dict[str, Any], hours: int) -> List[Dict[str, Any]]:
    """Fetch punch events from the last N hours."""
    logger.info(f"Fetching events from last {hours} hours...")
    
    cutoff = datetime.now() - timedelta(hours=hours)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
    
    # Build dynamic query based on available columns
    columns = [
        f"[{config['col_empcode']}] AS employee_code",
        f"[{config['col_punch_time']}] AS event_time",
        f"[{config['col_punch_type']}] AS event_type",
    ]
    
    if config['col_name']:
        columns.append(f"[{config['col_name']}] AS employee_name")
    if config['col_email']:
        columns.append(f"[{config['col_email']}] AS employee_email")
    if config['col_dept']:
        columns.append(f"[{config['col_dept']}] AS department")
    
    query = f"""
        SELECT {', '.join(columns)}
        FROM [{config['table']}]
        WHERE [{config['col_punch_time']}] >= ?
        ORDER BY [{config['col_punch_time']}] ASC
    """
    
    try:
        conn = connect_sqlserver(config)
        cursor = conn.cursor()
        
        if HAS_PYMSSQL:
            cursor.execute(query, (cutoff_str,))
            rows = cursor.fetchall()
        else:  # pyodbc returns rows as tuples, need to map to dict
            cursor.execute(query, cutoff_str)
            columns_list = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns_list, row)) for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        logger.info(f"Fetched {len(rows)} events from SQL Server")
        return rows
    
    except Exception as e:
        logger.error(f"Failed to fetch events: {e}")
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Fetch user master data
# ─────────────────────────────────────────────────────────────────────────────
def fetch_user_master(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fetch user master data from Mx_VEW_UserDetails."""
    logger.info("Fetching user master data...")
    
    query = f"""
        SELECT 
            EmpCode AS employee_code,
            FullName AS full_name,
            Card1 AS card1,
            Card2 AS card2,
            OfficeEmail AS office_email,
            PersEmail AS personal_email,
            Designation AS designation,
            Department AS department
        FROM [{config['user_details_table']}]
        WHERE EmpCode IS NOT NULL AND EmpCode != ''
    """
    
    try:
        conn = connect_sqlserver(config)
        cursor = conn.cursor()
        
        if HAS_PYMSSQL:
            cursor.execute(query)
            rows = cursor.fetchall()
        else:  # pyodbc
            cursor.execute(query)
            columns_list = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns_list, row)) for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        logger.info(f"Fetched {len(rows)} user records")
        return rows
    
    except Exception as e:
        logger.warning(f"Failed to fetch user master data (non-critical): {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Transform and prepare events for Railway
# ─────────────────────────────────────────────────────────────────────────────
def prepare_events(rows: List[Dict[str, Any]], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Transform SQL Server rows into Railway API format."""
    events = []
    
    for row in rows:
        # Extract and normalize
        employee_code = str(row.get('employee_code', '')).strip()
        event_time = row.get('event_time')
        event_type = str(row.get('event_type', '')).strip().upper()
        
        if not employee_code or not event_time:
            continue
        
        # Normalize event type
        if event_type == config['in_value'].upper():
            event_type = 'IN'
        elif event_type == config['out_value'].upper():
            event_type = 'OUT'
        else:
            # Skip unknown event types
            continue
        
        # Convert datetime to ISO string
        if isinstance(event_time, datetime):
            event_time_str = event_time.strftime('%Y-%m-%dT%H:%M:%S')
        else:
            event_time_str = str(event_time)
        
        # Generate deterministic ID
        source_event_id = generate_event_id(employee_code, event_time, event_type)
        
        # Build event payload
        event = {
            'source_event_id': source_event_id,
            'employee_code': employee_code,
            'event_time': event_time_str,
            'event_type': event_type,
        }
        
        # Optional fields
        if row.get('employee_name'):
            event['employee_name'] = str(row['employee_name']).strip()
        if row.get('employee_email'):
            event['employee_email'] = str(row['employee_email']).strip()
        if row.get('department'):
            event['department'] = str(row['department']).strip()
        
        events.append(event)
    
    return events


# ─────────────────────────────────────────────────────────────────────────────
# POST to Railway API
# ─────────────────────────────────────────────────────────────────────────────
def post_events_to_railway(events: List[Dict[str, Any]], config: Dict[str, Any]) -> bool:
    """POST events to Railway in batches."""
    if not events:
        logger.info("No events to sync")
        return True
    
    url = f"{config['backend_url'].rstrip('/')}/api/v1/timesheet/mirror/ingest/"
    headers = {
        'Content-Type': 'application/json',
        'X-Timesheet-Mirror-Key': config['api_key'],
    }
    
    batch_size = config['batch_size']
    total_batches = (len(events) + batch_size - 1) // batch_size
    
    logger.info(f"Posting {len(events)} events in {total_batches} batch(es)...")
    
    success_count = 0
    error_count = 0
    
    for i in range(0, len(events), batch_size):
        batch = events[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        
        payload = {'events': batch}
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                inserted = result.get('inserted', 0)
                updated = result.get('updated', 0)
                logger.info(f"Batch {batch_num}/{total_batches}: ✓ {inserted} inserted, {updated} updated")
                success_count += len(batch)
            else:
                logger.error(f"Batch {batch_num}/{total_batches}: Failed with status {response.status_code}")
                logger.error(f"Response: {response.text[:200]}")
                error_count += len(batch)
        
        except Exception as e:
            logger.error(f"Batch {batch_num}/{total_batches}: Exception - {e}")
            error_count += len(batch)
        
        # Small delay between batches to avoid overwhelming the server
        if batch_num < total_batches:
            time.sleep(0.5)
    
    logger.info(f"Sync complete: {success_count} succeeded, {error_count} failed")
    return error_count == 0


def post_users_to_railway(users: List[Dict[str, Any]], config: Dict[str, Any]) -> bool:
    """POST user master data to Railway."""
    if not users:
        logger.info("No user master data to sync")
        return True
    
    url = f"{config['backend_url'].rstrip('/')}/api/v1/timesheet/mirror/ingest-users/"
    headers = {
        'Content-Type': 'application/json',
        'X-Timesheet-Mirror-Key': config['api_key'],
    }
    
    payload = {'users': users}
    
    try:
        logger.info(f"Posting {len(users)} user records...")
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            created = result.get('created', 0)
            updated = result.get('updated', 0)
            logger.info(f"User sync: ✓ {created} created, {updated} updated")
            return True
        else:
            logger.error(f"User sync failed with status {response.status_code}")
            logger.error(f"Response: {response.text[:200]}")
            return False
    
    except Exception as e:
        logger.error(f"User sync exception: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main sync function
# ─────────────────────────────────────────────────────────────────────────────
def sync_once(config: Dict[str, Any], hours: int, sync_users: bool = False) -> bool:
    """Perform one sync cycle."""
    logger.info("=" * 80)
    logger.info(f"Starting sync cycle - last {hours} hours")
    logger.info("=" * 80)
    
    try:
        # Fetch events
        rows = fetch_events(config, hours)
        events = prepare_events(rows, config)
        
        # Post events
        events_ok = post_events_to_railway(events, config)
        
        # Optionally sync users
        users_ok = True
        if sync_users:
            user_rows = fetch_user_master(config)
            users_ok = post_users_to_railway(user_rows, config)
        
        logger.info("=" * 80)
        logger.info(f"Sync cycle complete - Status: {'SUCCESS' if (events_ok and users_ok) else 'PARTIAL/FAILED'}")
        logger.info("=" * 80)
        
        return events_ok and users_ok
    
    except Exception as e:
        logger.error(f"Sync cycle failed: {e}", exc_info=True)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='RAD AI Attendance Mirror Sync Agent',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--hours',
        type=int,
        default=48,
        help='Number of hours of historical data to sync (default: 48)'
    )
    parser.add_argument(
        '--daemon',
        action='store_true',
        help='Run continuously in daemon mode'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=300,
        help='Interval in seconds between syncs in daemon mode (default: 300 = 5 minutes)'
    )
    parser.add_argument(
        '--sync-users',
        action='store_true',
        help='Also sync user master data from Mx_VEW_UserDetails'
    )
    
    args = parser.parse_args()
    
    # Load configuration
    logger.info("RAD AI Attendance Mirror Sync Agent")
    logger.info("=" * 80)
    config = get_config()
    logger.info(f"SQL Server: {config['host']}:{config['port']}/{config['database']}")
    logger.info(f"Table: {config['table']}")
    logger.info(f"Railway: {config['backend_url']}")
    logger.info(f"Mode: {'DAEMON' if args.daemon else 'ONE-SHOT'}")
    logger.info("=" * 80)
    
    if args.daemon:
        logger.info(f"Running in daemon mode (sync every {args.interval} seconds)")
        logger.info("Press Ctrl+C to stop")
        
        # First sync with specified hours
        sync_once(config, args.hours, args.sync_users)
        
        # Then sync last 1 hour repeatedly
        sync_hours = 1
        
        while True:
            try:
                time.sleep(args.interval)
                sync_once(config, sync_hours, sync_users=False)
            except KeyboardInterrupt:
                logger.info("Received stop signal, exiting...")
                break
            except Exception as e:
                logger.error(f"Daemon loop error: {e}")
                time.sleep(60)  # Wait before retry
    else:
        # One-shot mode
        success = sync_once(config, args.hours, args.sync_users)
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
