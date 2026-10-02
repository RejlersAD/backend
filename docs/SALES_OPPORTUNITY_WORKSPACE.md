# Sales opportunity document workspace

## Larger files and lossless storage - 1 October 2026

The upload/download paths no longer impose a fixed Sales size limit. Optional
`SALES_WORKSPACE_MAX_UPLOAD_BYTES` and SharePoint-only
`SALES_WORKSPACE_MAX_DOWNLOAD_BYTES` accept a positive deployment limit; zero
(the default) means no application cap and is projected as JSON `null`.
Infrastructure timeouts, disk space and provider limits still apply. Private
downloads use the saved original/stored sizes and hashes, never a later upload
limit. All file extensions are accepted as attachments; folders can hold mixed
types. The multipart command still accepts one file and one request UUID, so a
multiple-selection interface can retain independent success/failure/retry state.

RADAI readiness adds `automatic_compression: "lossless_if_smaller"`. Each new
private file exposes `storage_encoding` (`identity` or `gzip`) and `stored_size`.
Existing `name`, `mime_type`, `size` and SHA-256 identity describe the **original**
file. Fixed-size reads prepare disk-backed original/gzip streams; deterministic
gzip is selected only when it saves bytes. Compressed formats may stay unchanged.
No document conversion, image resampling, text rewriting or archive extraction
occurs. Downloads return the original filename and exact original bytes, after
verifying both the stored representation and decoded size/hash in temporary
files. The saved original size also bounds decoding. All temporary handles close
on failure; existing scope, export, no-store and attachment disposition apply.

Sales migration `0017_attachment_storage_encoding` adds `storage_encoding`,
nullable `stored_size` and `stored_sha256` to the existing ledger. Legacy identity
rows with null/blank storage metadata remain raw files checked by original
size/hash. There is no object rewrite, backfill or workspace provisioning. New
representation evidence commits before object writes; uncertain retries retain
that representation and never replace corrupt/partial objects. Completion/audit
remain atomic, and current authority/storage identity are rechecked.

Reversal refuses once representation evidence exists. **Retaining the schema alone
is not sufficient for application rollback:** an encoding-aware reader must stay
in use after gzip writes. Pre-encoding application versions cannot serve these
objects. A downgrade requires a separately planned and verified decompression
migration preserving original identity; none is performed automatically here.

SharePoint keeps native original files. Its upload-session transport sends
sequential 5 MiB fragments, rechecking current opportunity access, configuration
and folder scope before each fragment. Returned ranges and final identity/size
must match. Any attempted-fragment failure remains uncertain and cannot blindly
start a new remote upload. Signed upload URLs remain server-only and never
receive a Graph bearer. This change does not activate SharePoint or synchronize
the two stores.

Application processing uses disk spools and bounded buffers; the existing S3
adapter may first spool an object using its own configured memory threshold
(currently 100 MiB). Disk capacity and provider/network timeouts remain relevant.
Railway's documented ingress requires each request body to finish within five
minutes, with an overall fifteen-minute active request ceiling. A larger worker
timeout does not remove those edge limits. Browser downloads currently use an
authenticated Blob. This is not a resumable browser-to-server transfer or an
unlimited-file-size guarantee. See [Graph upload sessions](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0)
and [Railway network limits](https://docs.railway.com/networking/public-networking/specs-and-limits).

Verification completed in isolated fixtures:

- From `backend`, `..\.venv\Scripts\python.exe manage.py test apps.sales.tests.test_attachment_streaming apps.sales.tests.test_private_attachments apps.sales.tests.test_opportunity_workspace apps.sales.tests.test_workspace_documents apps.sales.tests.test_proposal_review apps.sales.tests.test_folder_tags apps.sales.tests.test_folder_tags_postgresql --settings=config.settings_procurement_postgresql_test --noinput`
  passed all 133 cases, no skips (346.079 seconds), using disposable loopback
  PostgreSQL on port15449. Log: workspace
  `artifacts/sales-large-attachments-postgresql.log`.
- The final source delta adds safe handling of initial temporary-file allocation
  failure and rejects boolean Graph size metadata. The final focused command,
  `..\.venv\Scripts\python.exe manage.py test apps.sales.tests.test_attachment_streaming --settings=config.settings_release_test --noinput`,
  passed all 14 cases (11.426 seconds). This includes those final changes after
  the combined PostgreSQL process loaded its sources; the counts are not additive.
  Log: workspace `artifacts/sales-attachment-streaming-tests.log`.
- `..\.venv\Scripts\python.exe manage.py makemigrations sales --check --dry-run --settings=config.settings_shared_record_migrations`
  reports no model drift. Changed-source F-code and whitespace checks passed.
  Log: workspace `artifacts/sales-large-attachments-migration-drift.log`.
- The actual isolated PostgreSQL0017 migration probe preserved original fields
  across 11 source/review tables, applied/reversed/reapplied legacy-compatible
  schema, checked database constraints and refused reversal for gzip, new raw
  and partial representation evidence. Log: backend
  `artifacts/opportunity-upload-compression-migration.log`. It reconstructs the
  preceding schema and is not a full historical-chain migration certificate.

These checks use synthetic records and local temporary objects. They do not
certify live SharePoint delivery, production quotas, ingress or storage durability.

## Private RADAI attachments - 1 October 2026

The user explicitly requests uploads without personal SharePoint access. Sales
migration `0013_private_opportunity_attachments` extends the existing upload ledger
with provider, private object identity, destination fingerprint, MIME metadata and
normalized filename. Existing rows remain SharePoint records. Deploy/apply this
migration before the updated workspace API/frontend. Reversal refuses when any
RADAI attachment evidence exists; no file is silently detached from its identity.

RADAI uploads require current Sales read/create/update and opportunity visibility.
Browsing/details/version metadata require read; authenticated download also needs
export. Personal SharePoint access and Graph configuration are not required.
The six categories are logical attachment destinations and do not create a second
opportunity/project master. One successful upload is immutable stored version `1`;
this does not create document review, approval or replacement workflows.

The workspace response adds `radai_storage` with `status`, `message`, `can_upload`,
six `{key,item_count}` entries and `max_upload_bytes`. Existing top-level state is
still the separately verified SharePoint connection. Choose RADAI explicitly:

- `GET .../workspace/folders/{key}/files/?storage=radai` lists only local attachments
  using a signed, provider/destination/opportunity/category-bound 15-minute cursor.
- `POST .../workspace/folders/{key}/upload/` includes `storage=radai` alongside the
  existing multipart file and UUID, with the optional deployment limit above. Omission of
  `storage`, or `storage=sharepoint`, preserves the original remote upload behavior.
- RADAI file IDs are `radai-{upload-uuid}`; the existing file details, versions and
  download routes resolve them only inside the authorized opportunity/category.
  Metadata includes `storage_provider=radai`, no public `web_url`, actor provenance
  and actual byte size. Download returns an authenticated attachment, never a
  storage URL or inline untrusted content.

Private filesystem storage defaults to `BASE_DIR/private/sales-attachments`.
`SALES_ATTACHMENT_ROOT` can point to an administrator-managed persistent private
volume; it must not overlap `MEDIA_ROOT`. Deployments must persist this directory
or use the application's configured private object-storage backend. The existing
private-object boundary requires private ACL and authenticated signed access;
unknown/public storage fails closed. No production volume durability is certified
by local tests. Object names are server-owned UUID paths, never user filenames.

Request UUID, actor, provider, category, filename, SHA-256 and storage destination
are bound before bytes are written. Final state and completion audit commit
atomically after content verification and fresh scope/destination checks. The
existing narrowly exempted upload route commits its recovery intent before I/O.
Case-normalized filename conflicts never replace another attachment. An uncertain
RADAI retry can verify its own opaque object/hash and finalize without another
write. A process crash leaving `uploading` requires administrator reconciliation;
the API returns 409 rather than starting a competing write. Corrupt/partial objects
also require reconciliation and are never automatically overwritten.

Local uploads never silently fall back from uncertain SharePoint operations, copy
existing SharePoint files, or synchronize the stores. Existing SharePoint browsing
and file identities remain available through the explicit SharePoint selection.
Ordinary opportunity/client deletion retains every local upload recovery record.
Stored bytes are checked for size/hash and scope is rechecked before download.

Verification: the final combined suite passed all 84 cases on disposable
PostgreSQL, including 19 private-attachment cases, 42 existing workspace cases
and 23 document-explorer cases; no skips. It uses real temporary private files
and synthetic scoped users, with no live SharePoint writes. Evidence:
`artifacts/sales-private-attachments-postgresql.log`. Coverage includes concurrent
filename reservation, committed intent before I/O, completed-UUID replay, storage
failure and audit rollback recovery, signed pagination, foreign identifiers,
private-media separation, download integrity, late permission loss and ledger
reconciliation. New-source F-code checks, existing changed-source fatal lint and
Git whitespace checks passed. The PostgreSQL rerun corrected test-only duplicate
`FileResponse.close()` calls that bypassed Django's test-client connection guard;
production response handling was unchanged.

## Release and rollout checks - 1 October 2026

The release source starts from `f44ada5920102f63c7ac731e96f23c5744059f6e`,
the fetched `origin/development` and `origin/main` heads. The final release PR
targets `main` from `development`; opening it does not merge or deploy it.

The isolated PostgreSQL migration probe applied the real Sales `0012` to `0013`
transition, preserved existing opportunity/workspace/SharePoint upload rows,
confirmed the SharePoint provider default, reversed and reapplied before local
evidence existed, and refused reversal once a RADAI upload record existed.
Local `radai_dev` has both migrations applied and the full-registry Sales model
drift check reports no changes. The local read-only runtime check confirmed the
private filesystem sits outside public media and an authorized opportunity owner
can upload to all six categories. These checks created no business attachments
and do not certify production migration or storage durability.

Deploy the backend and apply both `0012_opportunity_workspace` and
`0013_private_opportunity_attachments` before activating the matching frontend.
The existing Railway pre-deploy command is
`python manage.py migrate --noinput --skip-checks`; a failed pre-deploy leaves the
previous release running. Provision persistent private `SALES_ATTACHMENT_ROOT`
storage or verify the configured private object-storage backend before production
uploads. The pinned Django 5.0 settings implementation maps the existing
`DEFAULT_FILE_STORAGE` setting into its `STORAGES` default alias; no unrelated
storage configuration change is included. Live object-store access and private
volume persistence remain deployment checks. Keep SharePoint disabled until its
separate Sales authority and destination have been verified.

The release regression command is:

```powershell
$env:PYTHONUTF8 = '1'
$env:RADAI_CONCURRENCY_PG_PORT = '15449'
$env:RADAI_CONCURRENCY_PG_PASSWORD = 'synthetic-tests-only'
..\.venv\Scripts\python.exe manage.py test apps.sales.tests.test_private_attachments apps.sales.tests.test_opportunity_workspace apps.sales.tests.test_workspace_documents --settings=config.settings_procurement_postgresql_test --noinput --verbosity 1
```

Use only the explicitly provisioned disposable PostgreSQL instance. The functional
harness synchronizes models; it does not replace the separate migration probe.
Private attachments and every external service use temporary storage/synthetic
fixtures during these tests. Migration/runtime evidence is recorded in
`artifacts/sales-attachment-migration.log`,
`artifacts/sales-attachment-local-migration.log`,
`artifacts/sales-attachment-model-check.log` and
`artifacts/sales-attachment-runtime-check.log`; these local logs are not release
artifacts committed to Git.

Implemented locally on 1 October 2026. SharePoint activation and external folder
creation have not been verified. The separate network-drive destination remains
unknown; the existing read-only file replica connector is unchanged.

The following integration sections describe the existing SharePoint provider;
the additive RADAI storage contract and deployment requirements are above.

## SharePoint scope and lifecycle

The existing Sales Deal owns one `OpportunityWorkspace`. New manual and reviewed
email registrations insert it in their existing atomic transaction. The saved VF
code names the folder; numbering remains Q-102101 onward and historical numbers
are preserved. Six fixed categories are Correspondence, Tender, Proposal,
Internal, Submitted and Award. Creating/uploading storage does not submit a
proposal, approve an award, send correspondence or change commercial evidence.

Disabled configuration records `not_configured`; configured creation records
`pending`. Existing records are not backfilled or remotely created by migration.
An authorized setup POST queues them explicitly. Celery Beat scans durable SQL
rows every minute when enabled. Workers claim a 120-second renewable lease with
a fencing token, recheck requester action/record access and destination identity
before each mutation, and persist each returned folder ID. Broker outages retain
the due row. A worker restart reclaims an expired claim; an unresolved mutation
intent stops for reconciliation. Provider rejections retain completed mappings;
Retry resumes missing folders. No external tree is renamed or overwritten.

Workspace GET never writes. Ready GET verifies the configured container, VF
folder and all six named/mapped children, and reads current SharePoint childCount.
Unknown counts remain null. Moved, missing or denied items remove the ready badge,
links and upload capability from the returned projection. Counts describe items
(including any subfolders), not approved documents. Files beneath a nested folder
are opened in SharePoint; recursive in-app browsing is not implemented.

## Configuration and access

Set these server variables in API, worker and Beat environments:

| Variable | Meaning |
| --- | --- |
| `SALES_WORKSPACE_ENABLED` | Explicitly opt in; defaults false |
| `SALES_WORKSPACE_TENANT_ID` | Sales writer Entra tenant UUID |
| `SALES_WORKSPACE_CLIENT_ID` | Sales writer application UUID |
| `SALES_WORKSPACE_CLIENT_SECRET` | Server-held application secret |
| `SALES_WORKSPACE_HOSTNAME` | Exact tenant hostname ending `.sharepoint.com` |
| `SALES_WORKSPACE_DRIVE_ID` | Verified Graph document-library ID |
| `SALES_WORKSPACE_ROOT_ITEM_ID` | Existing, administrator-provisioned Opportunities container ID |
| `SALES_WORKSPACE_ROOT_PATH` | Complete decoded path to that container, ending `/Opportunities` |
| `SALES_WORKSPACE_MAX_UPLOAD_BYTES` | Optional positive upload limit; default zero means no Sales application cap |
| `SALES_WORKSPACE_MAX_DOWNLOAD_BYTES` | Optional positive SharePoint download limit; default zero means no Sales application cap |

No Finance or mailbox credential fallback exists. Credentials and destination
identifiers are never accepted from API callers. The supplied destination is
`rejlerssverige.sharepoint.com`, expected decoded container path
`/sites/TeamADSalesBidding/Delade dokument/General/Myynti/Opportunities`.
The site URL is an input, not proof that the container exists or write access is
granted. An administrator must first create/verify that common container, resolve
its library/item IDs, and grant the Sales application the authorized write scope.
The application verifies its name, exact web path and drive before creating any
opportunity folders. New folders inherit that SharePoint container's permissions;
this feature does not synchronize RADAI roles into SharePoint ACLs.

Activation must confirm the container's existing membership is appropriate for
Sales documents. RADAI checks current opportunity read/object scope for every API.
Setup also needs update; upload needs create and update. Jobs retain the requesting
actor and require that actor's current read plus original create/update permission.
SharePoint itself governs direct Open SharePoint links. No guessed permissions,
retention periods or alternate shared-drive root are introduced.

## API

All routes use `/api/v1/sales/deals/{id}/` and existing scoped Deal lookup:

- `GET workspace/`: opportunity_id, deal_code, status, message, error_code,
  web_url, can_manage, can_upload, updated_at, max_upload_bytes and six folders
  (`key`, `name`, `purpose`, `item_count`, `web_url`). Status values are
  not_configured, not_created, pending, creating, ready and failed.
- `POST workspace/setup/`: empty JSON body; returns the same projection with 202.
  Duplicate pending/ready requests retain the same mapping; active claims are not
  interrupted. Setup does no Graph write in the request transaction.
- `GET workspace/folders/{key}/files/`: returns folder_key, files, item_count,
  next_cursor. The signed cursor expires after 15 minutes and is bound to the
  workspace/folder; no caller-supplied URL is followed. Files expose id, name,
  size, modified_at, web_url, is_folder. Optional source metadata is mime_type,
  version, publication_level, created_at, created_by and modified_by. Identity
  fields contain source display names, not inferred opportunity owners/reviewers.
  Each page is bounded at 100 items.
- `GET workspace/folders/{key}/files/{file_id}/`: current direct-file projection
  plus folder_key, is_folder=false, can_download and max_download_bytes.
- `GET workspace/folders/{key}/files/{file_id}/versions/`: file_id, versions and
  next_cursor. Each version includes id, modified_at, modified_by, size,
  publication_level and is_current. The signed 15-minute cursor binds workspace,
  category and file. Current is determined only by the source publication version
  ID; if unavailable, is_current remains null. Versions retain provider order.
- `GET workspace/folders/{key}/files/{file_id}/download/`: authenticated binary
  attachment requiring current opportunity read and export grants. The server
  honors the optional configured SharePoint download limit; Open SharePoint remains available.
  The complete download is checked before any bytes are returned. Details,
  versions and download responses use private/no-store cache controls.
- `POST workspace/folders/{key}/upload/`: multipart `file` plus UUID
  `upload_request_id`; returns the created file projection with 201 or 200 for an
  identical completed retry. Nonempty files only, subject to the optional configured upload limit.
  Reusing a UUID for different bytes/name/folder/actor returns 409. Upload sessions use
  Graph conflictBehavior=fail; same-name files are never replaced or auto-renamed.

Errors use `detail`/`code` with 400 validation, 403 authority, 404 scope/missing Deal,
409 conflict/state, 424 storage unavailable. Provider bodies, tokens and upload-session
URLs are not included. A narrow fixed-identity route-guard exception lets only
workspace_upload commit its SQL attempt before network I/O; its permission checks
still run. Other Sales writes retain the existing outer transaction behavior.

## SharePoint document explorer metadata and downloads

The 1 October document-explorer extension is additive and needs no new migration.
File details/history/download verify configured container, opportunity VF folder,
canonical category, direct file parent and drive, and the requested file ID.
Moved files, other opportunity files and remote shortcuts are refused. These GET
routes do not create workspaces, document rows, approval state or audit events.

The `version` string is Graph publication.versionId. `publication_level` is only
published or checkout when present. These source values do not establish a Sales
proposal's approval/review status; absent metadata stays null. History depends on
the SharePoint library's existing version settings and may be empty or incomplete.
No restoration, deletion, review assignment, new-folder creation or prior-version
download is introduced in this slice. Existing subfolders open in SharePoint.

Download requests use a server-built Graph content endpoint. A provider-returned
302 target must be HTTPS on the exact configured tenant or a hostname beneath
files.1drv.com, with no credentials, custom port, fragment or control characters.
Only that first vetted redirect is followed, without the Graph Authorization
header. Subsequent redirects are refused; signed URLs never enter API responses,
database records or application logs. Per-request connect/read timeouts, the
known source size and optional configured download cap bound transport. The spool uses memory
for its first 1 MiB and temporary storage thereafter, and is closed on failure.
Declared and received sizes must match. Before exposing the complete attachment,
the service reloads the actor, rechecks permissions/configuration and re-verifies
the full scope chain and file ID/name/size/eTag/modification time. A move, changed
revision, denied read or failed transport yields a normal safe API error instead
of a partially successful download.

Graph contracts consulted for this extension:
[driveItem metadata](https://learn.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0),
[publication facet](https://learn.microsoft.com/en-us/graph/api/resources/publicationfacet?view=graph-rest-1.0),
[version history](https://learn.microsoft.com/en-us/graph/api/driveitem-list-versions?view=graph-rest-1.0),
[content download](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0).

The normal Deal DELETE and parent Client cascade paths reject 409 after provisioning is queued/attempted or
external mappings/intents/upload attempts exist. This prevents losing storage
recovery evidence while leaving remote files behind. A truly unconfigured row
without external evidence retains the original deletion behavior. No automatic
remote deletion or retention policy is supplied.

## Recovery and migration

Apply Sales 0012 before API/worker activation. It adds two tables and a unique
workspace/upload-request constraint; no historical registration or remote folder
is altered. Reversal before any workspace row exists is supported. After use,
reversal refuses to erase external mappings/retry evidence; keep the schema for a
forward fix or restore a verified backup. Disabling the integration stops dispatch
and all read/write transport without deleting mappings.

External creation and database commit cannot be atomic. A persisted intent records
the expected parent, name and folder key before creation. If Graph succeeds but
the response/database checkpoint is lost, `recovery_required` prevents automatic
adoption of a potentially unrelated tree. There is deliberately no API that links
an arbitrary existing path. An operator must inspect the recorded intent, Graph
parent/drive/name and external audit evidence, then reconcile that exact mapping
under a locked administrative database procedure with a recorded audit event.
Only proven absence permits clearing an intent for retry. Do not clear it merely
because the client timed out; do not delete or rename a conflicting folder.
This initial phase has no self-service reconciliation UI or management command.

Upload attempts commit actor, request UUID, category, filename, byte count and
SHA-256 before Graph. Completed replay returns its saved result. Definitive access,
throttle or configuration rejection may retry the same identity. An uncertain or
still-running upload returns 409 on retry until an operator verifies the remote
file identity/content and reconciles the attempt. A different UUID is not a safe
way to resolve an uncertain result. Provider conflict never authorizes overwrite.

Graph references: [folder creation](https://learn.microsoft.com/en-us/graph/api/driveitem-post-children?view=graph-rest-1.0),
[upload-session conflict and upload URL contract](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0).

## Custom opportunity folder tags - 1 October 2026

The user selected custom editable tags for the folder overview. Each canonical
Deal/category pair can hold one optional single-line tag, at most 64 characters.
Tags are shared by the logical category across RADAI and SharePoint. Empty text
clears the tag; tags do not rename folders, classify files, or grant approval.

Sales migration `0016_opportunity_folder_tags` adds a unique OpportunityFolderTag
row with text, revision, latest command fingerprint and actor/time. It neither
backfills opportunities nor creates workspaces, upload records or provider work.
The SQL category constraint accepts the existing six categories only. Reversal
refuses after any tag row exists, including a cleared row; retain this additive
schema when reverting application code so revision/retry evidence survives.

Existing `GET deals/{id}/workspace/` returns each folder's `tag` (empty when unset)
and `tag_token`, plus top-level `can_edit_tags`. GET does not create tag records.
`PATCH deals/{id}/workspace/folders/{folder_key}/tag/` accepts exactly
`{tag:string, expected_token:string}` and returns
`{folder_key, tag, expected_token, replayed}`. Use the returned token for the next
edit. Storage/provider query parameters are not accepted by this category-level
command. The response and workspace projection are private/no-store.

Read/update Sales opportunity grants and current Deal visibility authorize edits;
upload/create/export grants and SharePoint readiness are not required. Commands
recheck the active actor and access under actor/Deal/tag locks and commit the
required opportunity audit atomically. Tokens bind actor, opportunity, category,
tag revision and value, and expire after one hour. Resending the same latest
token/normalized text replays one saved effect. A later edit, competing value,
wrong actor/category/opportunity or invalid/expired token returns409. Unknown
categories, nontext/multiline tags, oversized tags and unknown payload fields
return400. Access loss denies retries. The UI must preserve entered text after
failure and refresh explicitly after a conflict.

An unchanged value is a no-op. A changed value increments its category revision;
editing another category does not invalidate that token. Tag writes never invoke
storage adapters or alter SharePoint provisioning/lease/recovery state, workflow
stages, approvals or item counts. Guarded API cases are in `test_folder_tags.py`;
`test_folder_tags_postgresql.py` observes concurrent writers and identical retries.
Executed verification is recorded in the workspace feature brief.

## Verification

The subsequent document-explorer run passed 62 tests with three existing
PostgreSQL-only tests skipped (65 discovered), combining
`apps.sales.tests.test_workspace_documents` and `test_opportunity_workspace`
under `config.settings_release_test` (`artifacts/sales-document-explorer-tests.log`).
This includes real module/action guard allow/deny for downloads, current read and
export grants, cross-record/folder/drive denial, version pagination binding and
expiry, missing metadata, no read mutations, safe redirect transport, size bounds,
partial transport failure and revocation/move/revision change before release of
the private buffer. Scoped fatal/F-code lint passed. No migration, PostgreSQL lock
behavior or production SharePoint activation changed in this extension.

Synthetic tests cover transactional registration, read-only missing/ready views,
real item counts, retry/partial creation, unrelated collisions, provider failures,
configuration changes, lease fencing, broker failure, record/action denial,
bounded uploads, stable retry identity and storage-only workflow effects. The
guard TransactionTestCase proves the attempt is committed before network access
and retained on errors. PostgreSQL-only tests exercise simultaneous claims/setup.
An observed PostgreSQL lock interleave also checks a worker audit can commit while
a delete waits on its workspace, preserving recovery evidence without a deadlock.

The isolated SQLite regression run completed 108 tests: 106 passed and two
PostgreSQL-only cases skipped, across workspace, VF registration/export and mailbox opportunity entrypoints
(`artifacts/sales-workspace-backend-tests.log`). The later focused run passed
40 workspace tests with two PostgreSQL-only skips, including parent-client
deletion and existing long filenames (`artifacts/sales-workspace-backend-final.log`).
The additional PostgreSQL lock test was introduced after those runs. Scoped
fatal and F-code Python lint plus whitespace checks passed. See the workspace
feature brief for PostgreSQL and migration results, local activation and remaining
deployment boundaries; these tests did not write to live SharePoint.
