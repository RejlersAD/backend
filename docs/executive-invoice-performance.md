# Executive invoice performance

The Executive overview uses Finance invoice values for **Invoiced revenue**.
These values are not recognised accounting revenue. Monthly and calendar-YTD
amounts retain the recorded invoice tax basis and original currency. No VAT
removal or exchange-rate conversion is assumed.

The complete external invoice population includes paid invoices and excludes
cancelled, credit-note and internal invoices. Missing or future invoice dates
are excluded and counted. Missing amounts remain unknown. Recorded receipt
subtotals are shown separately from complete totals; a blank receipt is not
assumed to be zero in this performance view. Outstanding is calculated for
each invoice before summing, so overpayments do not offset other invoices.

Receipts and outstanding are current values grouped by invoice issue month.
They are not monthly cash flows or historical receivables balances.

## Refresh the workbook source

The application can use a privately provisioned daily snapshot from the same
workbook as the existing invoice overview. The source SHA-256 and invoice-row
count must match the workbook summary. The artifact contains no invoice
numbers, customer names or project identities. Daily aggregates can still
describe a single invoice and are sensitive Finance data: do not commit them
to this public repository or attach them to a public pull request.

Generate both artifacts from the same reviewed Finance file. Run these from
the backend directory, using the project's Python environment. Supply the
actual last invoice row; the generators reject invoice rows beyond that
boundary, and totals/footer rows must not be included.

```powershell
python manage.py generate_invoice_workbook_summary "C:\Finance\Invoice Tracking.xlsx" --last-row 4409 --output apps/finance/data/invoice_workbook_summary.json
python manage.py generate_invoice_performance_summary "C:\Finance\Invoice Tracking.xlsx" --last-row 4409 --output "C:\Finance\PrivateArtifacts\invoice_performance_summary.json"
```

Both commands accept `--sheet`, `--header-row` and `--snapshot-at` for
reproducibility. They do not import or modify invoice database records. Review
the artifacts against the same source version. The current format reads invoice
date B, invoice amount L, payment status R, receipts AA and currency AE from
the External Invoice sheet. Currency codes must agree with any explicit cell
format currency markers. Conflicting receipt currencies remain unknown.

Provision the daily artifact separately from application code:

1. Copy the reviewed daily JSON into a private persistent deployment volume,
   for example `/data/finance/invoice_performance_summary.json`, readable by
   the application process and outside static/media web roots.
2. Set `EXECUTIVE_INVOICE_PERFORMANCE_SNAPSHOT_PATH` to that absolute path in
   the backend service environment. On Windows, for a local service:
   `$env:EXECUTIVE_INVOICE_PERFORMANCE_SNAPSHOT_PATH = 'C:\Finance\PrivateArtifacts\invoice_performance_summary.json'`.
3. Deploy code, apply the Finance migration through the normal deployment
   process, and reload the application workers. Confirm the authenticated
   response identifies `invoice_performance.source.kind` as `finance_workbook`.

Without an environment override, the ignored local path
`apps/finance/data/invoice_performance_summary.json` is supported for existing
installations. No actual daily snapshot ships in Git. An unreadable or
mismatched configured artifact falls back to the authorised database register,
not an older local snapshot. Database records may have different row coverage
or imported values from the workbook; an empty register produces unavailable
invoice-period measures. Provision the private artifact when the dashboard
must reproduce the reviewed workbook cohorts.

When deploying service changes, apply the Finance migrations and reload the
running application workers. The local Gunicorn command does not automatically
reload Python changes. After confirming Gunicorn is the container's main
process, `docker kill --signal=HUP radai_backend_local` gracefully reloads its
workers. Verify the authenticated response through the frontend proxy at
`/api/v1/dashboard/executive/receivables/?currency=AED&months=12` contains
`invoice_performance.monthly`. A successful fresh Django-shell calculation
alone does not verify the code loaded by the HTTP workers.

The snapshot timestamp records extraction, not when Finance last updated an
invoice. The dashboard also exposes the first and last eligible invoice dates.
Customer-filtered views use the complete authorised database register; they
do not substitute the whole-workbook total. If a verified snapshot is absent,
the response identifies the database register as its source.

## Approved Finance plans and costs

The Executive Financial Performance tab consumes the same `invoice_performance`
response as the Overview. Its monthly/YTD controls select invoice issue periods;
the workbook amount and receipt cards remain all-period totals for the selected
original currency. Missing numeric cells are marked as known subtotals (`*`).

Client balances, ageing and invoice collection priorities come from the current
authorised receivables register. They are labelled separately from invoice-period
collections: the collection register treats blank receipts as zero, while the
invoice performance series keeps missing receipts unknown and withholds incomplete
collection rates. Current ageing is not a historical cash or working-capital series.
Business-unit attribution and project profitability require additional Finance
mapping and matching costs; invoice project numbers alone do not establish either.

Authorised Finance administrators can maintain **Executive Finance periods**
in Django admin. Each row represents one calendar month and original currency
for the entire workspace. Enter:

- `month`: the first day of the calendar month, and `currency`.
- `budget_invoiced` and/or `forecast_invoiced`: approved invoice-value amounts
  on the same recorded tax and currency basis as the invoice chart.
- `recognised_revenue` and `operating_costs`: both matching accounting amounts,
  with `actual_through` in that month. These are optional and separate from
  invoice values.
- A Finance `source_reference`, approving user and approval timestamp, then
  set the status to `approved`.

Draft, future-dated approvals and invalid or incomplete approvals are not
displayed. Reading plans and operating costs additionally requires Finance
overview read permission. Workspace plans and costs are withheld when a
single customer is selected.

Operating margin is `(recognised revenue - operating costs) / recognised
revenue`; it requires positive approved recognised revenue and matching costs.
YTD margin uses the summed matched accounting amounts, never an average of
monthly percentages. It is shown only when every month from January through
the selected cutoff has complete approved coverage.

## Estimated outlook

Approved future invoicing forecasts take priority. Missing months in a partial
approved forecast stay missing. The application never fills them with estimates.

Without approved forecast values, the workspace may show an **Estimated
invoicing outlook**: the average invoiced value from the three completed
calendar months before the cutoff, repeated for twelve future months. Zero
months are included. The source must cover all three months, contain complete
invoice amounts in the baseline and have no undated eligible invoices. A zero
baseline does not establish forward demand. The estimate is not an approved
Finance forecast or a statement of secured revenue.

## Release checks

Run functional tests against the isolated in-memory database, never the live
application database. An isolated checkout without a local `.env` can set
`DATABASE_URL=sqlite:///:memory:` before these commands; the executive test
settings also replace database, cache, password hashing and email delivery.

```powershell
python manage.py check --settings=config.settings_executive_test
python manage.py test apps.finance.tests_invoice_performance apps.finance.tests_receivables_dashboard apps.finance.tests_workbook_summary apps.finance.tests_customer_invoice_register apps.dashboard.tests_executive_finance apps.dashboard.tests_executive --settings=config.settings_executive_test --noinput
```

For migration drift, use the production app registry with an explicit private
SQLite configuration. Reset the complete database configuration because the
production settings add PostgreSQL connection options even for a SQLite URL:

```powershell
$env:DATABASE_URL = 'sqlite:///:memory:'
@'
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
from django.conf import settings
settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
import django
django.setup()
from django.core.management import call_command
call_command('makemigrations', 'finance', check=True, dry_run=True, skip_checks=True)
'@ | python -
```

The invoice-performance tests exercise the new migration forwards and backwards
in a separate private in-memory SQLite connection and compare its model state
with Django's current registry, including the month/currency uniqueness
constraint. Functional suites use model synchronization; they do not certify
that production migrations have been applied. The private-path tests use only
synthetic aggregates and check source matching and stale-artifact rejection.
