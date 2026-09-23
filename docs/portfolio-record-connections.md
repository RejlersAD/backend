# Portfolio workbook and recorded project connections

The uploaded POC workbook is a dated reporting source. Its project codes are
reconciled with existing RADAI records when the portfolio is read. Refreshing or
uploading a workbook refreshes those associations without replacing invoice
amounts, approvals, project status, schedules, or departmental records.

## Project identity and counts

- Registered projects are distinct accessible `core.Project` records.
- Workbook projects are distinct source parent project codes; workbook rows
  identify their project/subproject pairs. Neither count is added to the
  registered total.
- An exact subproject match takes precedence. A known ambiguous, deleted or
  inaccessible subproject blocks fallback to its parent.
- Identity collisions are checked against the entire uploaded snapshot, so a
  filter cannot make an ambiguous source code appear unique. Hidden identities
  only suppress unsafe matches; their details are not returned.
- An exact parent match is labelled as a parent association. It does not create
  a new subproject or allocate parent financial values across subprojects.
- Unmatched and ambiguous identities appear for review. Creating or correcting
  an operational project uses the existing project register and its permissions;
  the workbook importer does not create projects automatically.

The portfolio connections use existing canonical project references for Project
Control, Procurement and converted Sales records. Finance and QHSE retain their
own project-number registers, so those associations require exact project codes.
Department counts and links follow their read permissions and existing record
visibility rules. A link to a department register is distinguished from a link
to a particular record or project.

## Invoice control

The POC workbook's `INVOICING. STATUS` sheet contains project-level totals, not
invoice numbers. Portfolio Invoice Control therefore shows two distinct sources:

1. Current recorded Outgoing Invoices from `invoice_tracker.CustomerInvoice`,
   connected by project number and identified by invoice number plus project
   number. A parent invoice is counted once. Duplicate composite identities or
   database IDs require review before a reliable invoice link/total is reported.
2. Workbook reconciliation, retaining its original reporting date, missing-value
   coverage and comparison dates. Known subtotals remain identified as subtotals;
   mixed revenue comparison dates do not become a complete comparison total.

Recorded invoice receipts and balances are current register figures, not a
historical balance reconstructed at the workbook cutoff. Original currencies
remain separate. Stored AED amounts retain their recorded basis; no exchange-rate
refresh or comparison of unverified tax bases occurs during a dashboard read.
Financial totals exclude conflicting identities, cancelled invoices and credit
notes. Those records remain visible for review, with the exclusions stated.

`GET /api/v1/dashboard/executive/portfolio-workbook/outgoing-invoices/` requires
Executive Dashboard, Project Control and Outgoing Invoices read access. It uses
the same authorized workbook filters as the revenue report; `limit` and `offset`
paginate invoices after complete-scope aggregation. `snapshot_id` binds the
request to the displayed workbook and returns HTTP 409 if a newer upload became
active. Responses are private and not cached. The existing outgoing register's
`project_exact` filter preserves the project context of a portfolio drill-down;
`queue=all` avoids silently restricting it to overdue invoices.
