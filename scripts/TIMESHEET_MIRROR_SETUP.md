# Production Time Attendance Bridge

Production cannot connect directly to `192.168.99.52`. That is a private LAN
address and is not routable from Railway. Do not expose SQL Server port 1433 to
the public internet. Run the supplied sync agent on one always-on Windows PC or
server inside the office instead:

```text
Matrix SQL Server (LAN) -> office sync agent -> HTTPS -> RADAI production
192.168.99.52:1433                                  PostgreSQL mirror
```

Only outbound HTTPS access is required. Events are idempotent, so overlapping
sync windows and retries do not create duplicate attendance records.

## 1. Prepare the office machine

Install Python 3.11 or later, clone/copy the backend repository, then run from
the backend directory:

```powershell
python -m pip install -r requirements-sync-agent.txt
Copy-Item scripts\timesheet_mirror.env.example scripts\timesheet_mirror.env
```

Edit `scripts\timesheet_mirror.env` and replace:

- `TIMESHEET_USER` and `TIMESHEET_PASSWORD` with the SQL credentials.
- `TIMESHEET_MIRROR_API_KEY` with the exact existing value from Railway's
  backend variables. It must match on both sides.

The example already contains the confirmed Matrix database, table and columns:

```text
matrix.dbo.Mx_VEW_UserAttendanceEvents
UserID, FullName, DptName, EventDateTime, EntryExitType (0=IN, 1=OUT)
```

Keep `timesheet_mirror.env` private and do not commit or email it.

## 2. Run the preflight

```powershell
python scripts\timesheet_mirror_sync.py `
  --env-file scripts\timesheet_mirror.env `
  --check --hours 24
```

Success prints both:

```text
SQL Server check passed
Production mirror authentication passed
```

Common failures:

- `invalid mirror key`: copy the Railway key again without spaces or quotes.
- SQL connection error: run on the office LAN/VPN and confirm port 1433.
- SQL identifier/column error: confirm the configured Matrix view and columns.

## 3. Prime live synchronization

After any controlled history import, prime the local checkpoint so the watcher
does not re-upload the rolling window on its first cycle:

```powershell
python scripts\timesheet_mirror_sync.py `
  --env-file scripts\timesheet_mirror.env `
  --hours 2 --prime-state
```

Large history replays are intentionally blocked by default. Only run one during
an approved maintenance window, after the bulk-ingest backend is deployed:

```powershell
python scripts\timesheet_mirror_sync.py `
  --env-file scripts\timesheet_mirror.env `
  --hours 720 --batch-size 100 --allow-large-replay
```

## 4. Schedule continuous synchronization

### Automated Windows installation (recommended)

On the office PC, open PowerShell as Administrator and run:

```powershell
Set-Location C:\path\to\backend
powershell -ExecutionPolicy Bypass -File .\scripts\install_timesheet_mirror_task.ps1 `
  -Replay30Days -SyncUsers
```

The installer validates dependencies, Matrix SQL connectivity and Production
authentication before it creates `RADAI Attendance Sync`. The task runs as
`SYSTEM` at Windows startup, watches a checkpointed 48-hour recovery window,
and sends only unseen events every five minutes. The two optional switches are
intended for the first installation; omit them when repairing/re-registering
an existing agent.

### Manual installation

Open Windows Task Scheduler and create a task with:

- Trigger: at system startup.
- Program: the full path to `python.exe` (find it with `Get-Command python`).
- Start in: the backend repository directory.
- Arguments:

```text
scripts\timesheet_mirror_sync.py --env-file scripts\timesheet_mirror.env --hours 2 --batch-size 100 --watch --interval 300
```

Enable `Run whether user is logged on or not` and `Restart the task if it
fails`. Use a Windows service account that can read the repository and reach
SQL Server. The persistent checkpoint keeps the two-hour recovery overlap
without re-uploading already synchronized events.

For an interactive always-running process instead, use:

```powershell
python scripts\timesheet_mirror_sync.py `
  --env-file scripts\timesheet_mirror.env `
  --hours 2 --batch-size 100 --watch --interval 300
```

On Windows, `scripts\run_timesheet_mirror_agent.cmd` provides a checkpointed
48-hour/five-minute watcher with a rotating local log. It is suitable as the
Task Scheduler action and can also be double-clicked for a manual start.

Run `--users` separately when employee-master details need refreshing; do not
include it in the continuous watcher command.

## Production settings

The Railway backend must retain:

```text
TIMESHEET_FEATURE_ENABLED=true
TIMESHEET_DATA_SOURCE=mirror
TIMESHEET_INPUT_MODE=hybrid
TIMESHEET_MIRROR_API_KEY=<same secret as office agent>
TIMESHEET_INGEST_TZ_OFFSET=4
```

The production ingest URL is:

```text
https://backend-production-3883.up.railway.app/api/v1/timesheet/mirror/ingest/
```

The API-key-protected endpoint is intentionally public over HTTPS so the office
agent can reach it. SQL Server itself remains private.
