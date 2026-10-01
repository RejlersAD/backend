# Finance SharePoint workbook connection

RADAI can read a configured SharePoint invoice workbook into its existing
receivables reporting snapshots. SharePoint remains the master. The Finance
page's Accounts Receivable, Workbook totals and embedded Customer invoices
table, plus the corresponding Executive readers, use the published source
through their existing permissions. The separate operational outgoing-invoices
workspace retains its existing records. The reader does not update SharePoint
or operational customer invoices.

## Microsoft configuration

An administrator must configure an Entra application with read access to the
intended workbook. Prefer a Selected permission plus an explicit resource read
grant; consent alone does not grant access. See Microsoft's
[Selected permissions guidance](https://learn.microsoft.com/en-us/graph/permissions-selected-overview).
Store these settings in the backend and worker secret configuration:

```dotenv
FINANCE_SHAREPOINT_TENANT_ID=
FINANCE_SHAREPOINT_CLIENT_ID=
FINANCE_SHAREPOINT_CLIENT_SECRET=
FINANCE_SHAREPOINT_DRIVE_ID=
FINANCE_SHAREPOINT_ITEM_ID=
FINANCE_SHAREPOINT_SYNC_ENABLED=false
FINANCE_SHAREPOINT_SYNC_INTERVAL_SECONDS=1800
```

The application secret never belongs in frontend configuration, Git, chat or
logs. A browser workbook link is not an application credential. A SharePoint
plugin connected to ChatGPT does not configure RADAI's backend or worker.

IT can supply the Graph drive/item identifiers directly. Alternatively, set
`FINANCE_SHAREPOINT_URL` privately and run:

```sh
python manage.py resolve_finance_sharepoint_link
```

Copy the returned IDs to the corresponding configuration settings. The resolver
uses the existing application's access and never redeems or changes sharing
permissions. The [sharing-link endpoint](https://learn.microsoft.com/en-us/graph/api/shares-get?view=graph-rest-1.0)
has broader documented permissions than the selected-resource reader; a denied
resolution is not a reason to broaden the scheduled application's access.
Get stable identifiers from IT instead, or resolve the known site by hostname
and path, list its drives, and retrieve the exact drive item within that site.
Verify its site, filename and unique document ID against the supplied link
before saving the Graph-returned drive/item IDs. Do not assume the browser
link's document GUID is the Graph drive-item ID.

## Validate and activate

Apply the additive Finance synchronization-state migration before running any
Finance workbook publication with this version of the backend. Stop old workers
during deployment so old and new import code do not run concurrently.

```sh
python manage.py migrate --noinput
python manage.py migrate --check
python manage.py sync_finance_sharepoint --dry-run
python manage.py sync_finance_sharepoint
```

The dry run downloads and validates the complete workbook without publishing
invoice rows or changing the successful remote checkpoint. Commands return only
status, source ID and row counts. The reader supports the existing `External
Invoice` sheet and row-5 headers, detects the final invoice row and rejects gaps
or unexpected headers. It reads saved formula results; Excel calculations are
not executed. The maximum downloaded XLSX size is 25 MiB.

After the first successful publication, enable
`FINANCE_SHAREPOINT_SYNC_ENABLED=true` and restart the backend, worker and single
Celery beat scheduler with matching settings. When Docker Compose mounts or
`env_file` values change, recreate the containers; a plain restart does not
reload those values. Preserve the existing beat schedule file when switching
source mounts. The configurable interval defaults
to one hour and has a one-minute minimum. Existing deployment commands remain:

```sh
celery -A config worker --loglevel=info
celery -A config beat --loglevel=info
```

No UI setup screen or new public API is introduced. Operators can inspect the
singleton `ReceivablesSyncState` through the backend: last attempt, last successful
check, sanitized last error and last synchronized snapshot. The Finance pages
continue showing the existing published source provenance; they do not yet show
a separate remote-connection badge.

## Publication and recovery

HTTP finishes before publication. The shared reader checks source identity,
size and ETag before and after download and never forwards a Graph credential
to a preauthenticated download URL. Authentication and permission failures stop;
the scheduled task retries HTTP 429 and 5xx failures at most three times.

A database lease prevents overlapping remote jobs. All Finance source imports
use the same database lock and publication generation. A manual replacement or
expired lease invalidates older work, including a source that changes away and
back. Remote-version markers and the source snapshot commit atomically.
Unchanged remote content skips download only when its checkpoint still matches
the active source. An explicit dry run always validates downloaded content.

Failures preserve the previous source. Snapshot versions remain available;
disable scheduling and use the existing reviewed local import to restore a
previous workbook if needed. When scheduling is reenabled, SharePoint resumes
being the source. Do not reverse the migration while running this version's
importer. Schema rollback, if necessary, requires stopping workers and reverting
the application code first; it removes sync metadata, not retained source rows.

This connection covers the configured Excel workbook only. It does not import
linked invoice PDFs, arbitrary libraries, incoming invoices or payment records.
See [receivables-source.md](receivables-source.md) for preserved field semantics.

## Checks

```sh
python manage.py test apps.finance.tests_receivables_sharepoint apps.finance.tests_sharepoint_operations apps.finance.tests_receivables_source apps.finance.tests_receivables_source_dashboard apps.finance.tests_customer_invoice_source_register apps.portfolio.tests.test_sync --settings=config.settings_portfolio_test --noinput
```

These tests use an isolated database and simulated Graph responses; they do not
establish live access or finance-data reconciliation. Validate the additive
migration and concurrent publisher behavior separately against PostgreSQL.

`apps.finance.tests_receivables_sharepoint_postgres` adds three real concurrent
publisher/lease checks. Run it with intentionally isolated PostgreSQL test
settings; it skips on SQLite. Do not point tests at a running application DB.

On 30 September 2026, 120 functional regressions and all three PostgreSQL 16
concurrency tests passed. The additive migration also passed forward/backward/
forward application with synthetic source preservation and no Finance model
drift. Initial live access was separate: the existing local Microsoft application
authenticated, but Graph rejected the selected workbook request with HTTP 401.
Finance configuration and successful live validation were still required then.

Subsequent IndexApp setup verified its client-secret authentication (HTTP 200)
and `Sites.Selected` token role. Both the selected workbook sharing-link request
and the known Finance site's metadata request returned HTTP 403 `accessDenied`.
At that point selected-site access, ID resolution and live validation were
blocked and the schedule remained disabled.

On 1 October 2026, IndexApp authentication and the known Finance-site metadata
request both succeeded (HTTP 200). The exact workbook's identity was verified,
Graph drive/item IDs were saved in ignored local configuration, and the complete
workbook passed a database-free dry run: 4,412 source rows, worksheet rows 6-4417.
The user selected local RADAI for first activation. Runtime activation evidence
is recorded in the workspace feature brief; production activation is separate.

The local `radai_dev` database then received Finance migration `0015` and the
first real synchronization: snapshot 2 with 4,412 rows. Snapshot 1 (4,404 rows)
remains retained and inactive; the operational customer-invoice count was
unchanged. The successful checkpoint committed with the new snapshot and the
lease was released. Hourly synchronization was enabled, and the local backend,
worker and single beat service were recreated from the Finance worktree with
the previous scheduler state and other service configuration preserved.

All three services passed their health checks. A real queued worker task returned
`unchanged` for snapshot 2, advanced the successful checkpoint and created no
duplicate snapshot. The persisted beat entry was independently verified at
3,600 seconds; the first timed hourly dispatch had not elapsed. Backend health
and the frontend Finance route returned HTTP 200; anonymous Finance source
requests returned HTTP 401. Authenticated dashboard rendering was not checked
with a user session. See the workspace's
`artifacts/finance-sharepoint-local-activation-20261001.log` for operational evidence.

Open `http://localhost:5173/finance` to view the published workbook. Local
scheduled checks require the Docker worker, scheduler, database and Redis to
remain running. The UI still shows source publication time separately from the
internal last successful remote check; no connection-status UI was added.

## Production rollout requested on 1 October 2026

The requested production destination is `https://www.radai.ae/finance`, using
the same authoritative workbook and a 30-minute interval. Local backend, worker
and scheduler have already been reconfigured with an explicit interval of
1,800 seconds. Production activation remains unverified.

Deploy this backend change against the latest production branch, preserving
unrelated newer changes. Railway's existing pre-deploy command applies the full
migration graph; verify Finance `0015` before publishing a workbook. Keep the
five Finance identity/resource settings in the production server's secret
configuration, set the interval to `1800`, and retain scheduling disabled until
production dry-run validation and initial publication succeed.

For an existing Celery worker and single approved beat scheduler, supply the
matching Finance settings to all participating processes, verify the effective
broker and non-eager task execution, then enable Finance scheduling. The default
web runtime can start an optional worker but does not start beat. Inspect the
actual production services before adding a scheduler: the application's shared
beat configuration also includes other departments' jobs. A dedicated Finance
cron job is an alternative when no existing scheduler is available; configure
that service separately from the web service and pause the cron itself to stop
it, because the manual sync command intentionally works with the schedule flag
disabled.

Verify the deployed revision, migration state, real production workbook
validation/publication, preserved prior snapshot, repeat sync without duplication
and an actual scheduled run. Public HTTP health alone does not establish Finance
database or worker readiness. Do not report the live connection active until
these production checks have been observed.
