# Receivables source facts

The receivables source tables retain Finance's external workbook rows separately
from operational customer invoices. Importing a snapshot never updates
`CustomerInvoice`, derives payment statuses, fills missing due dates, recalculates
balances, or refreshes exchange rates. Old versions remain available after a new
version is activated; each request pins its source version.

The source key includes the workbook SHA-256, sheet and verified row bounds.
Invoice numbers are not row keys: duplicate workbook invoice numbers remain
separate facts. An operational invoice link is recorded only when the invoice
number occurs once in the workbook and matches an existing register record.

Headers are checked before import. The default source is `External Invoice `
(including its trailing space), header row 5, invoice rows 6 through 4409.
Column R is preserved and normalized by exact label: `New` stays `new`, and
`Paid (partial)` becomes `partial`. Unknown statuses remain outside the eligible
unpaid statuses. Dates come from their recorded cells, including Due Date N.

L, M, Y and AA retain cached numeric values with eight decimal places. Blank,
text and Excel error cells remain unknown. M is the recorded AED invoice amount;
it is never regenerated from L. Original invoice, remaining balance and receipt
currencies are each classified independently from that cell's explicit currency
format and AE. Conflicts and absent currencies stay explicit. Neither blank AE
nor an unspecified currency defaults to AED.

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

With an active source, the AED dashboard sums recorded column M across original
invoice currencies. Unpaid includes New, Pending and Overdue invoice amounts;
Partial uses the recorded column Y balance only when its currency is known to be
AED. A missing balance remains unknown. Total overdue includes only recorded
Overdue statuses. The 30+, 60+ and 90+ cards use Due Date N and strict age
thresholds greater than 30, 60 and 90 days among eligible unpaid invoices.

Verify the Finance and Executive APIs return `source.mode = "workbook"`, the
expected snapshot identity, and the same overdue count and AED sum in both the
dashboard and filtered customer-invoice register. The verified source below has
18 overdue rows totaling AED 1,846,363.06. Original-currency views use recorded L
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
