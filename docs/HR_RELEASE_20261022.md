# HR release verification — 22 October 2026

The local HR work was committed and merged with current origin/development
without conflicts. Remote Critical Line List models, routes and migrations are
retained. This release includes recruitment registers, private candidate forms,
careers synchronization, CV/pre-onboarding improvements, leave/HR overrides,
probation dates and the reviewed employee register import. Scope is recorded in
the HR feature documents in this directory. No production data is seeded.

## Validation

93 isolated regressions passed using config.settings_candidate_forms_test:
candidate forms, careers/public links, CV assistance, direct pre-onboarding,
talent acquisition, HR leave enhancements, employee import and business approval.
External delivery is mocked/isolated. These SQLite tests do not claim PostgreSQL
concurrency or deployment verification.

## Local PostgreSQL migrations

The complete migration plan was inspected after alignment. Existing local
hr_core migrations through 0022_pre_onboarding_verified_email and payroll
migrations through 0025_leave_hr_override were already applied. The incoming
critical_line_list.0001_initial and 0002_cllproject_location_type were applied
successfully after a PostgreSQL custom-format backup was created and its
pg_restore catalog validated.

Final manage.py migrate --check --skip-checks exited zero. Full-app
makemigrations --check --dry-run --skip-checks reported no changes. A separate
MigrationExecutor check found zero pending migrations; database introspection
checked 115 managed tables across hr_core, payroll, payroll_engine, rbac and
critical_line_list with no missing model columns or tables. The local backend
workers were gracefully reloaded after migration.

Evidence and the private database backup remain outside this repository under
workspace artifacts/release-hr-20261022/. No source register, correction report,
credentials, database dump or other employee data is included in this release.
This verifies the local database; a remote deployment must run its own normal
migration process. No production database migration or main-branch merge is
claimed.
