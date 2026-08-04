# Database Synchronization Tool

## Overview
This tool synchronizes your remote PostgreSQL database (preprod) with your local pgAdmin4 database in real-time.

## Features
- ✓ **100% Database Synchronization**: Complete schema and data sync
- ✓ **Soft Coding**: All credentials and settings in configuration file
- ✓ **Real-time Sync**: Automatic periodic synchronization
- ✓ **Automatic Backups**: Local database backed up before each sync
- ✓ **Detailed Logging**: All operations logged for tracking
- ✓ **Safe Operations**: Connection testing and error handling

## Files Created
- `db_sync_config.json` - Configuration file (credentials, sync settings)
- `db_sync.py` - Main synchronization script
- `db_sync_realtime.py` - Real-time monitoring and scheduling
- `db_sync_requirements.txt` - Python dependencies
- `sync_once.bat` - Run one-time sync (Windows)
- `sync_realtime.bat` - Run continuous sync (Windows)

## Prerequisites

### 1. Install PostgreSQL Client Tools
You need `pg_dump` and `pg_restore` commands available in your PATH.

**Download PostgreSQL client tools:**
- https://www.postgresql.org/download/windows/
- Or install full PostgreSQL (includes pgAdmin4)

**Verify installation:**
```powershell
pg_dump --version
pg_restore --version
```

### 2. Install Python Dependencies
```powershell
pip install -r db_sync_requirements.txt
```

## Configuration

### Edit `db_sync_config.json`

**IMPORTANT**: Update your local database password!

```json
{
  "remote_db": {
    "host": "altaria.proxy.rlwy.net",
    "port": 13039,
    "database": "railway",
    "user": "postgres",
    "password": "JXintDwoohqZVyDfCuwlodAEEumeIicO"
  },
  "local_db": {
    "host": "localhost",
    "port": 5432,
    "database": "radai_preprod",
    "user": "postgres",
    "password": "YOUR_LOCAL_PASSWORD"  ← CHANGE THIS!
  },
  "sync_settings": {
    "sync_interval_seconds": 300,        ← Sync every 5 minutes
    "backup_before_sync": true,
    "backup_directory": "./db_backups",
    "exclude_tables": [],
    "log_file": "./db_sync.log"
  }
}
```

### Configuration Options

- **sync_interval_seconds**: How often to sync (300 = 5 minutes, 600 = 10 minutes, etc.)
- **backup_before_sync**: Create backup before each sync (recommended: true)
- **backup_directory**: Where to store backups
- **log_file**: Log file location

## Usage

### Option 1: One-time Sync (Recommended for First Run)

**Using Batch File (Easy):**
```powershell
.\sync_once.bat
```

**Using Python Directly:**
```powershell
python db_sync.py
```

**What it does:**
1. Tests connections to remote and local databases
2. Creates local database if it doesn't exist
3. Backs up current local database
4. Dumps remote database
5. Restores remote data to local database
6. Shows statistics (tables, rows)

### Option 2: Real-time Continuous Sync

**Using Batch File (Easy):**
```powershell
.\sync_realtime.bat
```

**Using Python Directly:**
```powershell
python db_sync_realtime.py
```

**What it does:**
1. Runs initial synchronization
2. Waits for specified interval (e.g., 5 minutes)
3. Automatically syncs again
4. Repeats continuously until you press Ctrl+C
5. Shows countdown and statistics

**Press Ctrl+C to stop**

### Option 3: One-time Sync via Real-time Script

```powershell
python db_sync_realtime.py --once
```

## pgAdmin4 Configuration

### Creating Server in pgAdmin4

1. Open pgAdmin4
2. Right-click "Servers" → "Register" → "Server"

**General Tab:**
- Name: `RADAI_preprod`

**Connection Tab:**
- Host: `localhost`
- Port: `5432`
- Maintenance database: `radai_preprod`
- Username: `postgres`
- Password: `[your local password]`

**Save password**: Check this box

3. Click "Save"

### After First Sync

1. In pgAdmin4, refresh the server (right-click → "Refresh")
2. Expand: RADAI_preprod → Databases → radai_preprod → Schemas → public → Tables
3. You should see all tables from remote database

## Monitoring

### Check Logs
```powershell
Get-Content db_sync.log -Tail 50 -Wait
```

### Check Backups
Backups are stored in `db_backups/` directory:
- Format: `local_backup_YYYYMMDD_HHMMSS.sql`
- Custom PostgreSQL format (compressed)

### Restore from Backup
If you need to restore a backup:
```powershell
pg_restore -h localhost -p 5432 -U postgres -d radai_preprod -c db_backups/local_backup_20260804_120000.sql
```

## Troubleshooting

### "pg_dump not found"
- Install PostgreSQL client tools
- Add PostgreSQL bin directory to PATH
- Typical location: `C:\Program Files\PostgreSQL\16\bin`

### "Could not connect to database"
- Check if PostgreSQL service is running
- Verify local database credentials in config
- Test connection in pgAdmin4

### "Permission denied"
- Make sure local PostgreSQL user has CREATE DATABASE permission
- Try connecting as superuser (postgres)

### "Database already exists with different schema"
- The tool will drop existing schema before sync
- Backup is created automatically
- You can disable backups in config (not recommended)

## Security Notes

⚠️ **IMPORTANT**: The config file contains passwords!

- **DO NOT** commit `db_sync_config.json` to git
- Add to `.gitignore`:
  ```
  db_sync_config.json
  db_backups/
  *.log
  remote_dump_*.sql
  ```

## Advanced Usage

### Custom Sync Interval
Edit `db_sync_config.json`:
```json
"sync_interval_seconds": 600  // 10 minutes
"sync_interval_seconds": 1800 // 30 minutes
"sync_interval_seconds": 3600 // 1 hour
```

### Disable Backups (Not Recommended)
```json
"backup_before_sync": false
```

### Custom Backup Location
```json
"backup_directory": "D:/DatabaseBackups"
```

## Examples

### First-time Setup
```powershell
# 1. Edit config with your local password
notepad db_sync_config.json

# 2. Install dependencies
pip install -r db_sync_requirements.txt

# 3. Run first sync
.\sync_once.bat

# 4. Verify in pgAdmin4
# Refresh server and check tables
```

### Daily Development Workflow
```powershell
# Start real-time sync in the morning
.\sync_realtime.bat

# Work normally - database syncs automatically
# Press Ctrl+C when done for the day
```

### Manual Sync On-Demand
```powershell
# Quick sync whenever needed
.\sync_once.bat
```

## Support

For issues or questions:
1. Check `db_sync.log` for error details
2. Verify configuration in `db_sync_config.json`
3. Test connections manually in pgAdmin4

## License
Internal tool for RADAI project
