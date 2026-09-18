# File Server Replica: office connector and pilot setup

## Purpose and data flow

The office file server remains the document source. A read-only connector runs on
an office Windows machine that can access the share. It sends selected project
folders to RADAI over outbound HTTPS. Project Control uses the resulting project
links for browsing, searching, viewing, downloading, and information extraction.

```text
Office share -> office connector -> RADAI catalogue and private content storage
                                      -> Project Control project documents
```

The connector never writes, renames, deletes, or changes permissions on source
files. It does not open an inbound office-network port. Deploying the code does
not install a scheduled task, start a background service, or synchronize a live
share automatically.

## Implementation milestones — 15 September 2026

| Milestone | Plan | Verified result |
| --- | --- | --- |
| 1. Project connection | Reuse existing projects and define restricted access | Folders match project codes; publication requires an explicit project mapping and audience confirmation |
| 2. Replica pipeline | Add source-scoped connector authentication, inventories, and file versions | Connector retries are idempotent; incomplete scans retain existing entries; files use private storage |
| 3. Project workspace | Add 9.7 administration and server files inside Project Control | Folder navigation, search, preview/download, and extraction review implemented |
| 4. Information extraction | Preserve evidence and source versions | PDF/XLSX/DOCX/TXT/CSV supported; review never changes project baselines automatically |
| 5. Verification | Exercise permissions, synchronization, and the interface | 47 backend tests passed; connector suite has 23 passes and one Windows symlink privilege skip; six browser interaction tests and accessibility checks passed; production frontend build passed |
| 6. Local connection | Apply the migration and discover the actual server root | Local PostgreSQL migration applied; local RADAI catalogued 54 folders, with 10 matched project codes; no file contents copied and no project scopes published |

On 18 September 2026, the local development source first resumed folder-name
discovery at 09:07 Dubai time. After the user requested the contents of project
folders, recursive catalogue scanning was restored for all 54 immediate folders
then present on the share. The source now has explicit included paths; an empty
include list had both prevented recursive scanning and hidden cached nested
entries. Existing exclusions, project mappings and access settings are preserved.
The initial recursive inventory runs in the background, and each acknowledged
batch becomes browsable immediately. Subsequent scans start 300 seconds after
the previous scan finishes. Newly created top-level projects must be added to
the included paths. Catalogue scans index folders and file details without
copying document contents. The local administration interface is
`/admin/file-server-replica`.

The 08:35 scan was deliberately stopped during the Project Links redesign;
its failed record remains in scan history. The resumed scan records the current
successful connection separately.

### Active Project names and codes

Project Control's **Active Project** selector lists registered RADAI projects.
It shows the project code and full name, sorts by code, and searches combined
code/name/client terms. Selecting a project retains its canonical identity in
**Schedule > Planner**. A planning workspace is created explicitly when needed.

On 18 September 2026 the local server catalogue supplied 51 valid project codes:
49 projects were registered and mapped, and the two existing projects were reused.
Three general-purpose folders and one historical missing folder were skipped.
Existing project details and folder publication settings were preserved. Newly
registered identities start in Planning with operational status unconfirmed;
dates, client, budgets, and team assignments still require normal project setup.

For a later reviewed registration, use the intended backend/database environment:

```powershell
python manage.py register_server_projects --source <source-uuid> --actor <admin-user-id>
python manage.py register_server_projects --source <source-uuid> --actor <admin-user-id> --apply
```

The first command is a read-only preview. Applying is idempotent and requires
file-server administrator and Project Control create access. Only current,
included root folders with valid numeric codes are eligible. Duplicate codes,
deleted projects, and conflicting mappings are reported for review rather than
overwritten. Folder discovery alone does not register later projects automatically.

The initial Offline condition had two causes: discovery had run once without a
background connector, and the saved connector credential no longer matched the
source token. The local credential was repaired and verified against the API and
UNC share. A per-user Windows task named **RADAI File Server Replica** now runs
the connector continuously and starts again at Windows sign-in. Its configuration
and log are under `%LOCALAPPDATA%\RADAI\FileReplica`, restricted to the current
user, SYSTEM, and Windows administrators. The task requires that this Windows
user is signed in, the office share is accessible, and the local RADAI backend is
running. Production deployment and an always-on office service remain separate
rollout steps.

Validation commands (from `backend`, using the workspace virtual environment):

```powershell
..\.venv\Scripts\python.exe -X utf8 manage.py test apps.file_replica --settings=config.settings_file_replica_test --noinput
..\.venv\Scripts\python.exe -X utf8 -m unittest discover -s scripts/tests -p test_file_replica_sync.py
..\.venv\Scripts\python.exe -X utf8 manage.py makemigrations file_replica --check --dry-run --settings=config.settings_file_replica_migrations
```

The isolated test database uses SQLite. Apply migrations to PostgreSQL: an
existing `core` dependency migration contains PostgreSQL-specific SQL. The local
PostgreSQL migration was applied and its `django_migrations` record verified.

## Pilot scope

1. Add a source in **9.7 File Server Replica** with its absolute source root.
   Use the actual UNC share path supplied by the office share administrator,
   such as `\\uaeser2\RAD_FILE_SERVER\Projects` if that is the verified mapping.
   A user's mapped `R:` drive may be unavailable to another Windows account.
2. Select one project's directory by its exact path relative to that root.
   For example: `5900738 EPCM-Grid Power Integration Project`.
3. Add excluded subdirectories for material outside the pilot's document scope.
4. Link the source project folder to the existing Project Control project.
   Matching numeric project codes are suggested automatically; project access
   remains disabled until an administrator confirms the project audience:
   the project owner and active project members. Portfolio-wide commercial
   visibility alone does not grant access to replica files.
   Resolve ambiguous project-code matches before giving project users access.
5. Configure RADAI project access and the source's access scope before enabling
   replication. A connector account's ability to read a file is **not** permission
   for every RADAI user to see it. Windows share/NTFS ACLs are not imported
   automatically. Use separate scoped sources or excluded folders where necessary.
6. Issue a source token and save it only on the connector host. Start with a local
   dry run, then check connectivity, then perform one connected scan.

### Scope behavior

| Configuration | Result |
| --- | --- |
| Empty included paths | Discover immediate project directories only; no root files or recursive document reads |
| Explicit included paths | Recursively inventory selected directories, with navigation ancestors and no unselected sibling contents |
| Excluded paths | Skip that directory or file and all descendants |
| Catalogue mode | Send folders and metadata; no file-content uploads |
| Mirror mode | Hash in-scope files and upload content only when the backend requests it |

Included and excluded paths are relative to the source root. Absolute paths,
traversal components, alternate data streams, Windows device names, symlinks,
junctions, and other reparse points are rejected. Scope paths use Windows-style
case-insensitive comparisons. Include entries must select directories.

## Connector installation

### Recursive catalogue and long scans

Select every intended top-level project folder in `included_paths` to catalogue
all of its descendants. An empty include list intentionally performs only
top-level folder discovery. Catalogue mode records every regular file type,
including CAD/BIM files, archives, executables, unknown extensions and files
without an extension. The mirror upload-size limit does not exclude large files
from the catalogue. A type label comes from the filename; it does not establish
that RADAI can preview or extract that format. File bytes are not read or copied
in catalogue mode.

Traversal visits included projects breadth-first: all project roots and their
immediate contents are indexed before deeper folders. This prevents a large
earlier project's directory tree from leaving later projects blank. The queue
contains directory paths only; files stream one metadata record at a time.
Overlapping selections are deduplicated, existing root/path/junction checks
remain in force, and unsupported names do not block valid siblings. An unreadable
or unsupported path still makes the scan incomplete; partial scans never mark
unseen existing records missing. Empty folder browser views refresh every ten
seconds while the connector is syncing and stop after results or an error.
Inventory batches use bulk database writes, and completed scans mark missing
entries with a scope-filtered database update.

While a scan is running, the connector sends a heartbeat every 60 seconds on a
separate authenticated HTTP session. This updates both the source contact time
and the active scan time, including periods spent waiting for a slow share or
hashing a file. Inventory and upload requests also update these timestamps. A
heartbeat proves the connector process can contact RADAI; it does not prove an
individual share read has finished. After each acknowledged batch, progress is
logged at least every 1,000 entries or 30 seconds when batches are progressing.

The source API exposes `latest_scan` with its actual entry count and timestamps.
`scan_state` distinguishes discovery-only scope, unscanned current configuration,
running, incomplete and completed inventory. A completed scan applies only to
the configured includes/exclusions. An empty browser result means no catalogued
entries for that path; it does not by itself establish that the server folder is
empty. Folder/file type labels and scan-state fields are computed from existing
records and require no new database migration.

### Backend preparation

Deploy the backend and frontend changes, then run `python manage.py migrate
file_replica --noinput` against the intended PostgreSQL database. Replica files
use private S3 storage when `USE_S3` is enabled. Otherwise they use
`backend/private/file-replica/`, outside public media; mount that directory on
persistent storage. A custom Django storage alias can be supplied through the
`FILE_REPLICA_STORAGE_ALIAS` setting. Tokens are stored hashed in the database;
their plaintext value is shown only when issued. Rotating a token invalidates
the earlier one.

Project readers need the Project Control `read` action and project membership
or ownership. Downloads require `export`; extraction requires `create` and
project write access; review requires `update` and project write access.
Administrative configuration requires a superuser or active `super_admin`,
`admin`, or `ict_admin` role. Django `is_staff` status alone does not grant
replica administration.

Replica versions are retained indefinitely in this initial release. Missing
files are marked, with earlier bytes retained; there is no automatic purge.
Extraction supports files up to 20 MiB, 50 PDF pages, and 20 Excel worksheets.
Limit warnings describe any truncated rows or text. CAD/BIM native preview,
OCR, and automatic writes to project baselines are outside this version.

Use Python 3.10 or later on a machine that can read the office share and reach the
RADAI backend. Run these commands from the repository's `backend` directory:

```powershell
py -m venv .venv-file-replica
.\.venv-file-replica\Scripts\python.exe -m pip install -r requirements-file-replica-agent.txt
```

Give the Windows account running the connector read/list access to the selected
source directories and outbound HTTPS access to RADAI. Its local temporary
directory needs free space for one maximum-size upload plus small multipart
overhead. Content uploads are streamed from a temporary snapshot, so the entire
file is not loaded into memory.

Do not store connector credentials in the repository. Create a local file such as
`C:\ProgramData\RADAI\file-replica.env` and limit access to the account running the
connector and appropriate administrators:

```dotenv
RADAI_REPLICA_API_URL=https://<your-radai-backend>/api/v1/file-replica/agent/
RADAI_REPLICA_SOURCE_ID=<source-uuid-from-radai>
RADAI_REPLICA_TOKEN=<source-token-shown-when-issued>
```

Replace all placeholders. There is no default production URL. Credentials in a
URL are rejected, redirects are rejected, and server-supplied upload URLs are
never used. HTTP is accepted only for localhost/loopback development. Existing
environment variables take precedence over values in the optional env file.

## Validation and first synchronization

### 1. Local discovery (no connection or source writes)

```powershell
.\.venv-file-replica\Scripts\python.exe scripts\file_replica_sync.py --dry-run --root '\\uaeser2\RAD_FILE_SERVER\Projects'
```

The output is one JSON record per immediate directory. No API URL, token, or
network request to RADAI is needed for a dry run. Access to a UNC source still
uses the office file network.

### 2. Read and hash one pilot folder locally

```powershell
.\.venv-file-replica\Scripts\python.exe scripts\file_replica_sync.py --dry-run --root '\\uaeser2\RAD_FILE_SERVER\Projects' --include '5900738 EPCM-Grid Power Integration Project' --exclude '5900738 EPCM-Grid Power Integration Project\Private' --mode mirror --max-file-size-mb 100
```

Use the actual folder names. The dry run prints metadata and SHA-256 hashes;
it does not copy files into RADAI. A nonzero exit code means some scope or file
could not be processed completely. Remove the example exclusion if it does not
apply; add the actual restricted subdirectories instead.

### 3. Check configured connectivity and source root

```powershell
.\.venv-file-replica\Scripts\python.exe scripts\file_replica_sync.py --env-file 'C:\ProgramData\RADAI\file-replica.env' --check
```

This reads the source configuration from RADAI and checks local root access. It
does not create a scan or upload content. A successful check establishes root
access; the dry run and full scan establish access to individual subdirectories.
Connected scope comes from RADAI; `--root`, `--include`, `--exclude`, `--mode`, and
`--max-file-size-mb` are local dry-run options only.

### 4. Run one synchronization

```powershell
.\.venv-file-replica\Scripts\python.exe scripts\file_replica_sync.py --env-file 'C:\ProgramData\RADAI\file-replica.env' --once
```

One scan is also the default when no action flag is supplied. Validate the result
in RADAI: the folder hierarchy, project mapping, last scan status, one preview,
one download, restricted project access, and one extraction review. Change a
sample source document and scan again to confirm a new version appears.

### 5. Continuous foreground operation

```powershell
.\.venv-file-replica\Scripts\python.exe scripts\file_replica_sync.py --env-file 'C:\ProgramData\RADAI\file-replica.env' --watch
```

The connector reloads its RADAI configuration before each scan and uses the
configured interval (default 300 seconds, accepted range 60–86400). Ctrl+C stops
it. Run one connector instance per source to avoid overlapping scans.

### 6. Windows background operation

To install a task for the current Windows user, explicitly run the installer
from the `backend` directory with the Python interpreter and dedicated connector
environment file that passed `--check`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install_file_replica_task.ps1 -PythonPath '..\.venv\Scripts\python.exe' -EnvironmentFile 'private\file-replica-agent.env'
Start-ScheduledTask -TaskName 'RADAI File Server Replica'
Get-ScheduledTask -TaskName 'RADAI File Server Replica'
Get-Content "$env:LOCALAPPDATA\RADAI\FileReplica\connector.log" -Tail 15
```

The task runs hidden, uses the signed-in user's share access, restarts on process
failure, and ignores duplicate task starts. It copies credentials into protected
local application data; credentials do not appear in task arguments. Stop it with
`Stop-ScheduledTask -TaskName 'RADAI File Server Replica'`. If a token is rotated
in RADAI, replace the local connector credential before restarting the task.
Windows sign-out, shutdown, or loss of the office network interrupts updates;
cached folder listings remain browsable. Refresh the page/browser to load entries
as a large initial scan progresses. A successful scan reports completion only
after all selected, readable paths have been inventoried; errors remain visible.

## Synchronization and failure behavior

- Every scan resends the selected inventory; the backend decides which content
  requires transfer. The connector has no local cursor that could hide updates.
- Scan creation uses a UUID run ID. A retried request resends the same ID and body.
  Upload retries replay the same snapshot, scan ID, checksum, and modification
  timestamp. The backend handles these retries idempotently.
- File copies verify identity, size, and modification time before and after a
  read. A requested upload's hash and metadata must match its inventory entry.
  A changing file is retried on a later scan.
- The size limit defaults to 100 MiB and must be between 1 and 1024 MiB. A mirror
  file exceeding the configured limit remains an error entry, makes the scan
  incomplete, and is not uploaded. Exclude it or intentionally adjust the limit.
- Unavailable shares, permissions failures, unsafe paths, failed uploads, and
  unstable reads produce failed completion. They never declare a clean inventory.
  Only a fully successful scan may cause the backend to mark unseen in-scope
  entries missing. Previously copied versions remain subject to backend retention.
- Connection errors and HTTP 429/502/503/504 retry up to three attempts with
  bounded 1- and 2-second delays. Requests use 15-second connection and 120-second
  read timeouts. Redirects and authorization errors are not followed/retried.
- Configuration changes invalidate an active scan, so its completion cannot mark
  files missing under a different scope. After the first scan, a source root
  cannot be changed; create a new source for a different root to retain provenance.
- Exit status is `0` for a successful single scan/check/dry run, `1` for failure,
  and `130` for operator interruption. A disabled source starts no scan.
- Logs contain scan IDs and scoped relative paths, but not source tokens or API
  response bodies. Treat inventory output and logs as project information.

## Extraction and retention

File replication and extraction are separate operations. Files must have a
successfully copied version before RADAI can preview, download, or extract their
contents. Unsupported preview/extraction types remain downloadable when allowed.
The initial extraction types are text-based PDF, XLSX, DOCX, TXT, and CSV. There
is no OCR, macro execution, or external AI processing in this extraction path.
Scanned-image PDFs require OCR support that is outside this initial implementation.
CAD/BIM drawings, macros, and embedded executable content are not executed.

Extracted proposals retain source file/version and page or worksheet references.
Reviewers can accept or reject the evidence; this initial review workflow does
not edit project baselines automatically. A later source version makes the earlier
proposal potentially stale. Project authorization also applies
to search, content downloads, and extraction output. Define storage capacity,
version retention, and missing-file retention before expanding beyond the pilot;
the replica is not a replacement for the office server's independent backup.

## Agent API contract

All requests use `Authorization: Bearer <source-token>` and
`X-Replica-Source: <source-uuid>` against the configured `/api/v1/file-replica/agent/`
base URL. IDs returned by the API must be UUIDs. Responses are JSON objects.

| Method and relative endpoint | Body / response |
| --- | --- |
| `GET config/` | Returns `root_path`, `included_paths`, `excluded_paths`, `mode`, `max_file_size_mb`, `interval_seconds`, `enabled` |
| `POST scans/` | `{run_id}` -> `{id, status}` |
| `POST scans/{id}/entries/` | `{entries: [...]}` -> `{entries: [{id, relative_path, upload_required, status}]}` |
| `POST entries/{id}/content/` | Multipart `file`, `scan_id`, `checksum`, `modified_at` -> `{id, version, status: "available"}` |
| `POST scans/{id}/complete/` | `{success, error}` |

Each inventory entry includes `relative_path`, `parent_path`, `name`,
`is_directory`, `size_bytes`, UTC `modified_at`, SHA-256 `checksum` (empty for
directories/catalogue), and `error`. The connector batches at most 100 entries;
the API accepts at most 200. Multipart uploads use a neutral `content.bin`
filename; the backend must preserve the original filename from the inventory
record for content type detection, download names, and extraction.

## Connector tests

```powershell
py -m unittest discover -s scripts/tests -p test_file_replica_sync.py
```

Tests use temporary local folders and mocked HTTP requests. They cover discovery,
selected recursion, exclusions, unsafe paths, reparse points, unstable/oversized
files, upload snapshot retries, missing/permission-failed roots, failed completion,
redirect rejection, and a dry run with no API credentials. Actual symlink creation
is skipped on Windows hosts that do not grant that privilege.
