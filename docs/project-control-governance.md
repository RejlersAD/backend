# Project Control governance

## Control Accounts

A Control Account is the accountable control point for one enterprise-project
WBS node. It does not store a second budget value. Its control budget is the sum
of approved `BudgetAllocation` records on that WBS node.

Required attributes are a unique project code, WBS node, Control Account
Manager, earned-value method, and baseline start and finish dates.

State flow:

```text
Draft -> Submitted -> Active -> Closed
```

- Only Draft records may be edited.
- Activation requires an approved WBS budget.
- Project Control or Finance approval is required for activation and closure.
- Except for a superuser, the submitter cannot approve the same account.
- Active and Closed records are immutable through the API.

## Reporting Periods

A Reporting Period is the governed data-entry window for one project. Periods
cannot overlap, and only one period per project can be Open or Reopened.

State flow:

```text
Open/Reopened -> Submitted -> Locked
Locked --approved reason--> Reopened
```

- Open and Reopened allow controlled data entry.
- Submitted freezes entry while Project Control reviews the period.
- Locked is immutable.
- Project Control or Finance permission is required to lock or reopen.
- Except for a superuser, the submitter cannot lock the same period.
- Reopening requires a reason and is refused while another entry window exists.
- Every create, edit and transition writes an append-only `ReportingPeriodAudit` event.

## Approved hours and finance actuals

Project labour is entered as an `ApprovedHourEntry` against an Active Control
Account and an Open/Reopened Reporting Period. Biometric attendance is evidence
of presence only; it is not posted as project cost until a project, Control
Account, rate, work date and unique source reference are assigned.

State flow:

```text
Draft -> Submitted -> Approved -> Reconciled ledger actual
Approved --period reopened + reason--> Reversed
```

The submitter cannot approve their own entry unless they are a superuser.
Verified invoice allocations enter the same ledger from Finance. The invoice
date determines the accounting period, while an approved WBS allocation maps
the cost to its Active Control Account.

Each reconciliation is append-only and retry-safe. It records approved-hour
IDs, finance-ledger IDs, labour/finance/ledger totals, exceptions and a SHA-256
checksum. Missing Control Accounts, currency differences and total mismatches
are blocking exceptions. Submitting a period runs this gate automatically.

## Immutable integrated reporting snapshots

Locking a submitted period is refused unless its latest reconciliation is
exception-free. The lock transaction seals a versioned
`IntegratedReportingSnapshot` containing BAC, PV, EV, AC, commitments, approved
hours, labour and finance actuals, CV, SV, CPI, SPI, EAC, ETC and VAC.

The source manifest identifies the reconciliation, Control Accounts and latest
approved schedule-control snapshot. Where no schedule-control snapshot exists,
the manifest explicitly labels enterprise project progress as the fallback.
The calculation inputs, formula names and SHA-256 checksum are retained. The
snapshot API is read-only and model updates/deletes are rejected. Reopening and
relocking a period creates the next snapshot version instead of altering the
previous management report.

## Portfolio exception dashboard

The portfolio endpoint evaluates only projects accessible to the requesting
user. It bulk-loads governance and reporting evidence, ranks projects by their
highest exception, and never recalculates CPI/SPI from mutable dashboard fields.

Exception categories cover governance, reporting, data quality, cost and
schedule. They include missing or pending Control Accounts, missing/overdue
periods, periods awaiting lock, reconciliation failures, unmapped actuals,
CPI/SPI threshold breaches, forecast overruns, stale snapshots and overdue
project finish dates. Each exception identifies its accountable owner and the
project work area where it can be resolved.

Thresholds are configured through `PORTFOLIO_EXCEPTION_THRESHOLDS` and the
corresponding environment variables in `project_control/config.py`.
