# Receivables source facts

Automatic inbound reading of a configured SharePoint workbook is documented in
[finance-sharepoint.md](finance-sharepoint.md). It uses this same source importer
and requires separate server configuration; local imports do not establish a
live Microsoft connection.

The receivables source tables retain Finance's external workbook rows separately
from operational customer invoices. Importing a snapshot never updates
`CustomerInvoice`, derives payment statuses, fills missing due dates, recalculates
balances, or refreshes exchange rates. Old versions remain available after a new
version is activated; each request pins its source version.

The source key includes the workbook SHA-256, sheet and verified row bounds.
Invoice numbers are not row keys: duplicate workbook invoice numbers remain
separate facts. Historical operational invoice IDs are unverified provenance;
current publication does not create an operational invoice relationship.

## Reviewed canonical references

The shared-record review workspace can annotate an active source row with an
existing RADAI project and Sales client. Finance migration `0016` stores these
references in `ReceivablesSourceIdentity`, beside the immutable source row. It
does not change the workbook, source values, invoice amounts or payment status.
Each new workbook publication needs its own review; neither an invoice number
nor worksheet position proves identity across source versions.

Operational `CustomerInvoice` gets separate optional canonical references in
invoice-tracker migration `0006`. Original company/account/project text is
preserved. The customer source is `company`; `account` and external `project_id`
retain their distinct meanings. These protected fields are changed only through
the shared-record link command, which requires source update authority, target
visibility, a reviewed freshness token, reason and retry UUID. The shared command
commits its audit in the same transaction. No automatic name matching or master
record creation is performed.

Register responses expose `canonical_links` with minimal authorized target
labels and a state. Changed source identity, unavailable target access and
partially reviewed recorded references return `needs_review`. Stale links do
not appear as current IDs. Existing physical duplicate invoice IDs must first
use the outgoing invoice register's dedicated duplicate review; the shared
queue excludes them. No source annotation creates an operational detail URL.

These additive migrations leave existing rows unlinked. Reverse guards prevent
dropping reviewed identity annotations. Roll back application code while retaining
the additive schema and evidence if operational recovery is needed. An unused
schema can be reversed after stopping the newer application. The shared command
audit is separately retained by the core migration.

Headers are checked before import. The default source is `External Invoice `
(including its trailing space), header row 5, invoice rows 6 through 4409.
The Graph reader detects the final invoice row rather than using that manual
command default. Two layouts are supported: the original A:AE layout and the
layout with `Business Unit` inserted at H, shifting original H:AE to I:AF.
Every header required by a reader is checked at its mapped position before
values are read. The two `COMPANY` columns at E/F keep their distinct meanings;
arbitrary insertions, renamed required headers and column reordering are rejected.
`Business Unit` is a layout marker, not a new imported dimension or allocation rule.

`Payment Status` is preserved and normalized by exact label: `New` stays `new`, and
`Paid (partial)` becomes `partial`. Unknown statuses remain outside the eligible
unpaid statuses. Dates come from their recorded cells, including `Due Date`.

`Invoice Amount`, `Inv Amt. (AED)`, `Balance to be received` and `Actual Payment
Received` retain cached numeric values with eight decimal places. Blank,
text and Excel error cells remain unknown. The recorded AED invoice amount
is never regenerated from the original invoice amount. Original invoice, remaining balance and receipt
currencies are each classified independently from that cell's explicit currency
format and `Inv. CUR` (AE in the original layout, AF after the H insertion).
Conflicts and absent currencies stay explicit. Neither blank `Inv. CUR`
nor an unspecified currency defaults to AED.

Source rows and the stored workbook summary use the same verified mapping and
retain their existing amount, currency, status and row-count checks. The summary's
`source.currency_column` records AE or AF accurately; the JSON schema and SQL
models are unchanged. Existing AE summaries remain readable. An older application
that only accepts AE cannot read an AF summary, so rollback requires a compatible
reader or reactivation of a previously retained AE workbook after pausing sync.

From the backend directory, review a workbook without database reads or writes:

```powershell
python manage.py import_receivables_source "C:\path\Updated!Invoice Tracking _MASTER FILE- Latest (2).xlsx" --dry-run
```

After reviewing its row bounds, provenance and reconciliation, apply migrations
and run the same command without `--dry-run` to atomically publish that source.
An identical import reuses its version. Importing a previously retained workbook
reactivates that version. A failed import leaves the previous active version in
place. The database also prevents two versions from being active simultaneously.

## Deployment and verification

Migration `finance.0012_receivables_source_snapshot` creates the additive
`finance_receivablessourcesnapshot` and `finance_receivablessourcerow` tables.
It does not import private workbook data or modify operational invoices. Deploy
the backend and apply migrations before deploying the matching frontend:

```sh
python manage.py migrate --noinput
python manage.py migrate --check
python manage.py import_receivables_source /secure/path/finance-workbook.xlsx --dry-run
python manage.py import_receivables_source /secure/path/finance-workbook.xlsx
```

Provision the reviewed workbook separately in each target environment. An empty
source table continues to use the operational register; deploying application
code alone does not activate the reconciled source. Keep the workbook and import
reports out of Git. The importer requires cached spreadsheet formula values and
does not run Excel calculations. Review explicit sheet and row bounds if the
workbook layout changes.

With an active source, the AED dashboard sums recorded `Inv Amt. (AED)` across original
invoice currencies. Unpaid includes New, Pending and Overdue invoice amounts;
Partial uses the recorded `Balance to be received` only when its currency is known to be
AED. A missing balance remains unknown. Total overdue includes only recorded
Overdue statuses. The 30+, 60+ and 90+ cards use `Due Date` and strict age
thresholds greater than 30, 60 and 90 days among eligible unpaid invoices.

Verify the Finance and Executive APIs return `source.mode = "workbook"`, the
expected snapshot identity, and the same overdue count and AED sum in both the
dashboard and filtered customer-invoice register. The verified source below has
18 overdue rows totaling AED 1,846,363.06. Original-currency views use recorded `Invoice Amount`
and preserve separate currencies. Source invoice identifiers are display-only;
they do not link to potentially mismatched operational invoice details.

To switch workbook versions, import the reviewed replacement. Previous versions
remain available, and reimporting a previous workbook reactivates that version.
Avoid reversing the schema migration merely to switch data versions.

The supplied workbook hash
`308a453a0bf71490174f6699760cfb670186e1731adf8f3843eff55b12840e43`
contains 4,404 source rows and 18 recorded Overdue rows. Their M values total
AED **1,846,363.06**, matching Finance's supplied 18 amounts. The operational
register is unsuitable for this reconciliation: its older importer discarded
currency formats, recalculated M and balances, derived statuses and collapsed
duplicate invoice numbers.

Tests run against an isolated database:

```powershell
python manage.py test apps.finance.tests_receivables_source --settings=config.settings_executive_test --noinput
```
