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

## 3. Send the initial history

The first run sends user details and the last 30 days of events:

```powershell
python scripts\timesheet_mirror_sync.py `
  --env-file scripts\timesheet_mirror.env `
  --users --hours 720
```

Refresh Time Sheet Analytics after it completes. The warning should disappear
and the latest event/count should be visible.

## 4. Schedule continuous synchronization

Open Windows Task Scheduler and create a task with:

- Trigger: every 5 minutes, indefinitely.
- Program: the full path to `python.exe` (find it with `Get-Command python`).
- Start in: the backend repository directory.
- Arguments:

```text
scripts\timesheet_mirror_sync.py --env-file scripts\timesheet_mirror.env --users --hours 48
```

Enable `Run whether user is logged on or not` and `Restart the task if it
fails`. Use a Windows service account that can read the repository and reach
SQL Server. The 48-hour overlap makes temporary internet/production outages
self-healing.

For an interactive always-running process instead, use:

```powershell
python scripts\timesheet_mirror_sync.py `
  --env-file scripts\timesheet_mirror.env `
  --users --hours 48 --watch --interval 300
```

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
