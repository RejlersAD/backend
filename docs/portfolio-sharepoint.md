# Portfolio workbook uploads and synchronization

Upload a downloaded POC Excel workbook from Procurement's Project Links page
(`/procurement/projects/reconciliation`). RADAI
validates its POC and delivery forecast sheets and publishes an immutable
PostgreSQL snapshot. Uploads require no Microsoft application credentials.
Dashboard requests read PostgreSQL only. Automatic SharePoint synchronization
is optional and stays disabled until Microsoft access is configured.

```mermaid
flowchart LR
    Excel[Download SharePoint Excel] --> Upload[Upload in Project Links]
    Upload --> Preview[Preview validation results]
    Preview --> Confirm[Confirm import]
    Confirm --> Transform[Validate and transform]
    Transform --> DB[PostgreSQL snapshot]
    DB --> API[RADAI dashboard APIs]
```

## Upload from the dashboard

1. Download the current workbook from SharePoint as `.xlsx`. If its formula
   values need updating, recalculate and save it in Excel before downloading.
2. Open **Procurement → Project Links → Upload Excel workbook** at
   `/procurement/projects/reconciliation`.
3. Select the file (maximum 25 MiB) and preview its reporting date, source row
   count and validation warnings.
4. Select **Confirm import** to publish it. The upload status refreshes with
   the file and last upload time. Workbook management stays on Project Links;
   Executive Project Portfolio does not show a separate workbook section.

The upload action requires a RADAI staff administrator or superuser with
Executive Dashboard **View**, Project Control **View** and Project Control
**Edit** access. Explicit denials still apply. SharePoint permissions are
separate. Readers can still view the previously published workbook.
The upload publishes the shared portfolio source for all authorized readers;
it is not a private attachment.

Previewing a workbook does not change the portfolio source. Confirmation must
use the same file within 15 minutes. If another import changes the active
snapshot, preview the file again. Invalid files, older reporting dates and
imports during an active synchronization job leave the current snapshot intact.
Successful uploads record the uploader and snapshot metadata in the existing
audit log. Workbook contents and preview tokens are not written to that log.

Keep `PORTFOLIO_SYNC_ENABLED=false` while using manual uploads. No tenant ID,
client ID, client secret, drive ID or item ID is needed for this workflow.

## Command-line import

Use the existing backend environment and database. Review the dry-run output
before publishing a source version:

```powershell
python manage.py migrate portfolio
python manage.py import_portfolio_workbook "C:\path\POC workbook.xlsx" --dry-run
python manage.py import_portfolio_workbook "C:\path\POC workbook.xlsx"
```

The default source key is `poc`, which the dashboard reads. Importing identical
bytes with the same parser version reuses the original snapshot. An older
reporting date cannot replace a newer active report. A local import is labelled
as a manual workbook import; it does not establish a live Microsoft connection.
Operational project records, project-control approvals and users are not changed.

## Connect an existing Microsoft application

Configure the existing Entra application in the backend/worker secret store or
the ignored local `.env`. Never place credentials or private sharing links in
tracked files. Backend and worker must receive the same configuration.

```dotenv
PORTFOLIO_SHAREPOINT_TENANT_ID=
PORTFOLIO_SHAREPOINT_CLIENT_ID=
PORTFOLIO_SHAREPOINT_CLIENT_SECRET=
PORTFOLIO_SHAREPOINT_DRIVE_ID=
PORTFOLIO_SHAREPOINT_ITEM_ID=
PORTFOLIO_SYNC_ENABLED=false
PORTFOLIO_SYNC_INTERVAL_SECONDS=3600
PORTFOLIO_REPORT_STALE_DAYS=7
```

The application requires permission to read this file. Prefer a Selected
application scope plus an explicit read grant for the intended site or file.
An administrator must configure both consent and the resource grant; consent
alone does not grant access. See [Microsoft Selected permissions](https://learn.microsoft.com/en-us/graph/permissions-selected-overview).

A supplied browser sharing link is not an application credential. The scheduled
job uses stable drive/item identifiers. IT can provide those identifiers using
an already authorized account. An optional resolver is available if the existing
application can access the sharing-link endpoint:

```dotenv
PORTFOLIO_SHAREPOINT_URL=<private HTTPS sharing link>
```

```powershell
python manage.py resolve_portfolio_sharepoint_link
```

Copy its returned IDs into the two ID settings above. The resolver does not
redeem or change sharing permissions. Microsoft documents broader permissions
for the [sharing-link endpoint](https://learn.microsoft.com/en-us/graph/api/shares-get?view=graph-rest-1.0)
than the file reader needs; a 403 here is not a reason to broaden the scheduled
reader's access. Supply the stable IDs from IT instead. The downloaded workbook
uses [driveItem content](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0),
not Excel workbook sessions, so the job supports application authentication.

Validate remote access, then perform the first remote publication:

```powershell
python manage.py sync_portfolio_sharepoint --dry-run
python manage.py sync_portfolio_sharepoint
```

Set `PORTFOLIO_SYNC_ENABLED=true` and restart the configured worker and scheduler
after a successful initial sync. The existing Celery setup discovers the task
and merges its beat schedule. Run one beat scheduler for the environment:

```text
celery -A config worker --loglevel=info
celery -A config beat --loglevel=info
```

## API and visibility

- `GET /api/v1/dashboard/executive/` includes `portfolio_performance.workbook`
  with up to 200 authorized source rows in its API response, and
  `portfolio_performance.revenue_dashboard` for the main Project Portfolio.
- `GET /api/v1/dashboard/executive/portfolio-workbook/revenue/` accepts the
  same row filters and pagination. Its six revenue metrics, breakdowns, risk
  register, invoicing, PM performance and capacity sections drive the main
  portfolio view. Aggregate values cover all authorized matching rows,
  independently of project-register pagination.
- `GET /api/v1/dashboard/executive/portfolio-workbook/` accepts `search`, `pm`,
  `business_unit`, `client`, `limit` (1–200) and `offset`. Totals cover the complete
  authorized filtered scope, independently of pagination.
- `POST /api/v1/dashboard/executive/portfolio-workbook/preview/` accepts a
  multipart `file` and returns validated metadata plus a signed `preview_token`.
- `POST /api/v1/dashboard/executive/portfolio-workbook/import/` accepts the same
  multipart `file` and `preview_token` and atomically publishes the snapshot.
  Both POST routes enforce upload permissions on the server. The report's
  `can_upload` field controls whether Project Links offers the upload action.
- All report routes require Executive Dashboard read access; workbook facts also
  require Project Control read access. Explicit source denials are respected.
- Ordinary readers see rows matched to accessible project codes. Exact subproject
  matches take precedence over parent matches, including deleted/hidden codes.
  Superusers can additionally inspect unmatched source identities for reconciliation.
- The revenue dashboard grants full uploaded-source scope only to actors with
  the complete upload permission set above. This includes workbook identities
  absent from the operational project register. Other readers keep project-row
  visibility restrictions. Global PM KPI and resource-planning sections are
  withheld when row filters or restricted project visibility apply.
- Responses distinguish the workbook reporting date, import date, remote sync
  status, stale reporting dates, missing values and absent sources. Responses are
  private and are not cached in shared caches.

The frontend Nginx proxy allows 26 MiB requests to the workbook API, including
multipart overhead. Any additional ingress proxy must allow that request size;
the application separately enforces the 25 MiB workbook limit.

## Workbook interpretation

The importer validates the expected header profile and uses dated header groups
to locate current and historical values. It handles the second resource
deputation header section separately: booked hours, recognized revenue and sold
rates are not POC or EDDR percentages. Forecasts join on both project and
subproject codes, never worksheet row offsets.

Values retain their worksheet/row/cell provenance. Money uses decimal storage;
percent fields use percentage points (25 means 25%). The workbook's AED amounts
are stored independently of the original contract currency. Formulas are never
executed: saved calculation results are read. Invalid numbers, Excel errors and
missing formula caches become null with validation warnings; they never become
zero. Structural/identity errors prevent publication.

The original reconciliation endpoint retains its `Without PT = Yes` totals and
the separate `With PT = Yes` backlog total. The executive revenue endpoint uses
the source Report sheet's broader row scope: current revenue and forecasts
include resource-deputation rows, while POC risk uses the primary POC section.
Backlog uses the saved `Backlog Without PT` amount, whose formula already applies
its inclusion rule. An additional filter would incorrectly omit revenue.

The main cards map to current-period revenue, current forecast, PM forecast,
current forecast minus PM forecast, backlog and positive POC overclaim exposure.
They retain the workbook reporting cutoff and reporting month. All money is AED.
Missing values remain null with completeness metadata. For the full unfiltered
source only, a dated saved Report total may supply a partial card value when it
reconciles to the known row subtotal within rounding tolerance. This does not
make missing individual facts complete. Discrepancies are exposed rather than
silently overriding the row totals.

Project, invoice and forecast joins use project and subproject identity. Monthly
series with unmatched forecast rows show explicitly labelled known subtotals.
POC and EDDR remain separate from approved schedule-confidence measures.

The supplied 18 September 2026 workbook contains 199 POC source rows and 82
distinct parent labels. Three delivery-forecast identities do not exactly match
the POC identities; the reconciliation records them for review. Helper row
errors do not prevent otherwise valid source rows from being read. The invoice
sheet supplies saved invoice and revenue-comparison facts, not receivables or
cash collection. Its period labels can conflict with formula-linked revenue
dates; those conflicts are retained as warnings.
Invoice comparison totals require a single known baseline date. When dates
differ or are unknown, the dashboard retains dated rows and labelled known
subtotals but withholds the combined comparison total. Invoice and balance
totals use only rows explicitly included by the invoice sheet.

Published PM KPI scores retain their source heading and mixed-period warning;
they are not relabelled as current-month KPI results. The workbook's capped CPI
score is distinct from a calculated earned-value CPI. Monthly capacity uses
man-hours. The separate staffing plan includes Engineering FTE and other staff
categories, so its saved undated total is labelled as mixed planning units, not
a measured headcount. Unlabelled monetary planning cells are not presented as
resource costs. Missing delivery-efficiency measurements are not inferred.

Parser upgrades create a new immutable snapshot from the same workbook bytes.
Re-import the active upload to populate new facts. A scheduled SharePoint job
also downloads an unchanged ETag when its active parser version is outdated.

## Failure behavior

The reader checks file identity/version before and after a bounded download,
uses ETags for unchanged files and does not forward Graph credentials to download
redirects. A PostgreSQL source lease prevents overlapping jobs. Publication and
remote version metadata commit together. Failed validation, expired leases or
network failures retain the last completed snapshot. Graph HTTP 429 and 5xx
responses receive at most three retries. Errors stored for operators omit
credentials and download URLs.

## Verification

```powershell
python manage.py test apps.portfolio apps.dashboard.tests_executive apps.core.tests.tests_project_portfolio --settings=config.settings_portfolio_test --noinput
```

These functional tests use an isolated SQLite database. Validate the new
`portfolio.0001_initial` migration and repeated sample-file imports separately
against PostgreSQL before deployment. Frontend checks are:

```text
node scripts/check-portfolio-workbook.mjs
node scripts/check-project-portfolio.mjs
node scripts/check-portfolio-revenue.mjs
npm run build
```
