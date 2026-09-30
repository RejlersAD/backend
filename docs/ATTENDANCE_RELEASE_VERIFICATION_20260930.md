# Attendance frontend release: backend compatibility and migration verification

Verified locally on 30 September 2026. This backend release changes documentation
only. The Attendance frontend redesign uses existing backend services and needs
no new endpoint, model, migration, dependency or permission change.

## Source alignment

Fetched `origin` before preparation. Both `origin/development` and `origin/main`
were `60d79b467ce8f7c272becc715d9d089d5f302937`, the merged VF registration/export
release. The clean active worktree at `c55db7c4` fast-forwarded to that revision
without file changes or conflicts. Preparation uses
`release/attendance-development-20260930`; original worktrees remain untouched.

This note is the only new backend release content. Development push, main-base
PR checks, and any later merge/deployment are separate observable steps. The
verification below does not claim that a production migration or deployment ran.

## Existing contracts retained

- Timesheet daily/monthly responses describe returned attendance records and
  their available source fields. They do not establish scheduled workforce,
  completed-import certification or an organization-wide absence denominator.
  Missing/future/unavailable data must not become a reported zero.
- `GET /api/v1/payroll/attendance-overrides/?year=YYYY&month=M` uses the configured
  DRF page-number envelope (`count`, `next`, `previous`, `results`; page size 500).
  The frontend must collect all pages before presenting correction totals.
  Active correction rows expose original/corrected hours, reason and existing
  actor/timestamp fields; create/update authority remains server-controlled.
- `GET /api/v1/payroll/leave-requests/pending-for-me/` returns a nonpaginated
  `{count, results}` object after current-user visibility/review checks. This is
  the actor's actionable leave queue, not a general attendance/overtime approval
  queue or a count scoped by the frontend's selected attendance date/department.
- Approved leave calendars, branch-code mappings, annual leave imports, public
  holidays, and existing attendance uploads/exports keep their established
  meanings and authorization. Recorded overtime does not establish approved pay.

Inspected implementation: `apps/payroll/views.py`, `models.py`, `serializers.py`,
`apps/timesheet/models.py`, `services.py`, `mirror_services.py`, `urls.py`, and
`config/settings.py`. Cross-repository grounding is maintained in workspace
`docs/features/attendance-reference-restyle.md`, `docs/API_CONTRACTS.md`,
`docs/DECISIONS.md` and the repository gap report. Draft HR policy remains draft.

## Local PostgreSQL migration evidence

Checked the actual local application database only: `postgres_local/radai_dev`,
served by `radai_backend_local` using this active worktree. The audit asserts that
database boundary before inspecting migration/schema state. It reads migration
history and table metadata, without creating synthetic business records.

| Check | Observed result |
| --- | --- |
| Full installed migration graph/history | Consistent history; 564 applied historical records; zero pending operations |
| `manage.py migrate --check --noinput` | Passed |
| `manage.py migrate --plan` | No planned migration operations |
| Relevant graph leaves | Core `0013`, Payroll `0023`, Sales `0011`, Timesheet `0009`, all applied |
| Full `makemigrations --check --dry-run --skip-checks` | No changes detected; exit 0 |
| Focused Payroll/Timesheet/Sales/Core model drift | No changes detected |
| Required current model columns | All present in the seven models below |

Column inspection covered `AttendanceOverride` (12), `EmployeeLeaveRecord` (16),
`LeaveRequest` (26), `PublicHoliday` (12), `DailyAttendanceSummary` (24),
`TimesheetMirrorHeartbeat` (3), and Sales `Deal` (64). It does not certify all
historical constraints/data or a fresh replay of every unrelated migration.

There was nothing to migrate, so no migration was applied, faked or reversed.
The 564 historical records differ from the 535 current on-disk migration files;
the verified current graph has no pending nodes and passes consistency checks.
No history cleanup is part of this release. Production migration state was not
inspected and must not be inferred from this local database.

Local evidence files, intentionally excluded from commits:
`artifacts/attendance-release-local-migrations.log` and
`artifacts/attendance-release-all-model-drift.log`. The guarded audit helper is
`artifacts/attendance_release_local_migrations.py`; it also captures successful
`migrate --check` and `--plan` calls with system checks skipped after the explicit
database/graph inspection. Standard management commands were checked separately.

## Existing backend regressions

All **37 tests passed** from `apps.timesheet.tests` and
`apps.payroll.tests.tests_leave_workforce_sync` under Python 3.11 in the local
backend container. The run uses `config.settings_leave_test` and an isolated
in-memory SQLite database with model synchronization, local cache/email, and
synthetic fixtures. It is functional evidence, not migration or PostgreSQL lock
verification.

Coverage includes source selection, manual parsing, employee identity matching,
daily/monthly hours, biometric/manual hybrid precedence, idempotent ingest,
heartbeat freshness, UAE attendance dates, unknown live presence, the exclusion
of recorded overtime from payroll hours, and canonical leave-ledger preservation.

The isolated runner adds the existing Timesheet URL namespace to the reduced
leave routes. It follows `config/settings_database_maintenance_test.py` in
excluding `signals.E001` for three unrelated AI telemetry senders whose apps are
intentionally absent from that test registry. The initial harness run stopped
at those system checks before any tests; the corrected isolated run passed.
Application settings/source were not changed to accommodate the harness.

Command executed:

```text
docker exec -e PYTHONPATH=/app radai_backend_local python artifacts/attendance_release_tests.py
```

Local evidence: `artifacts/attendance-release-tests.log`; the initial harness
diagnostic is retained as `artifacts/attendance-release-tests-initial-harness.log`.
Whitespace verification (`git diff --check`) passed. Frontend behavior, browser
checks and production build are recorded in the matching frontend release.

No backend business rules, API write behavior, schema or production configuration
changed during this preparation.
