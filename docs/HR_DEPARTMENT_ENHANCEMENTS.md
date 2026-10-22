# HR-ENH-01 — HR department enhancements

User-authorized scope and evidence are recorded in the shared workspace at
`../docs/features/hr-department-enhancements.md` (relative to backend root).

## Additive schema

- `hr_core.0015_employee_nationality`: citizenship distinct from residence;
  legacy employees remain unclassified until HR verifies nationality.
- `hr_core.0016_employee_probation_start`: optional custom probation start.
  Existing probation end dates remain intact.
- `payroll.0024_hr_department_leave`: request-specific manager, additional
  leave categories and idempotent CL/NSL/WFH/FU seeds. Existing codes are retained.
- `payroll.0025_leave_hr_override`: immutable original requested dates/days and
  explicit HR exception fields. Existing request facts are copied once.

## API and authority

`GET /api/v1/payroll/leave-requests/manager-options/` returns
`{manual_selection_required, results:[{id,name,department}]}`. Only Sales/Finance
employees with no assigned manager receive options. Submission accepts optional
`selected_manager` (canonical employee ID), validates active identity and approval
access, and does not alter the employee's reporting line. Approval commands
remain protected by positive payroll approve grants and current stage ownership.

Final `POST .../leave-requests/{id}/approve/` accepts optional
`approved_start_date`, `approved_end_date`, `allow_negative_balance`, and
`override_reason` alongside `note`. Changed dates or a balance exception require
a reason. The negative-balance exception applies only to annual leave; it cannot
be supplied by employees at submission or by an intermediate approver. Missing
annual ledgers and overlapping requests still fail. Original request facts,
actor/reason and final dates are retained; the existing delta ledger deducts once.

Responses add `requested_start_date`, `requested_end_date`, `requested_days`,
`hr_balance_override`, `hr_override_reason`, `approval_steps`, and
`department_head_notification_status`. Workflow stage codes identify intermediate
FU stages without changing the existing request status vocabulary.

Onboarding accepts existing business-unit/business-area strings from the new
listed options, plus nationality and optional probation dates. Missing probation
dates default to joining/six calendar months; invalid date ranges fail before
identity creation. EDC employees retain their profile and cannot enter new payroll
outputs. Existing runs containing EDC records are blocked before processing.

## FU setup

After migrations, configure exact active accounts without granting broader roles:

```powershell
python manage.py configure_flex_leave --jamal jamal.ayoub@rejlers.ae --rafat rafat.sm.saqer@rejlers.ae --mohammed moghawanmeh@rejlers.ae
```

This creates a new workflow version. In-flight requests retain their version.
Each named reviewer still needs a positive effective payroll approve grant;
configuration does not confer it. The local inspection found this grant missing
for Jamal and Rafat. FU submission fails until all named reviewers are eligible.

Department-head notification recipients can be overridden in active
`leave_request_v1.configuration.department_heads` using department-to-exact-email
entries, or `LEAVE_DEPARTMENT_HEADS` JSON. Otherwise a unique current holder of
the existing head position is selected from canonical employee records. Missing,
ambiguous and failed delivery states are visible; no chart sample name is used.

## Verification

```powershell
python manage.py test apps.payroll.tests.tests_hr_department_enhancements --settings=config.settings_leave_test --noinput
```

The guarded self-contained suite covers success/denial, FU order, balance
exceptions/idempotency, dates, notifications, payroll exclusion and real additive
migration operations on SQLite. This does not prove PostgreSQL concurrency or
production acceptance. See the shared feature brief for local activation,
frontend checks and the PWA bundle-size build limitation.

Local activation: all four additive migrations applied after a validated local
PostgreSQL backup, FU workflow v1 configured, and backend restarted. The local
page and proxied API health responded with 200. Jamal/Rafat still need an
explicit positive approval grant; no role changes or production deployment
were performed. No real employee/leave/payroll records were created as tests.

Employee creation UI follow-up (HR-ENH-01a): Division is labelled Department,
Department/UAE title use searchable organization-catalog dropdowns, and the
Finland title is removed from creation, review, restored drafts and payloads.
The existing `division` API key and stored historical employee fields remain
compatible; this follow-up needs no backend or database migration.
