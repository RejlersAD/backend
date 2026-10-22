# HR-IMP-24 — Employee master import repair

Authorized 22 October 2026: diagnose the supplied employee register, update
existing employees without duplicates and create unmatched employees. User will
select an existing organization and role for new employees; preserve existing
organization, roles, credentials and account status by default.

Observed: 819 CSV rows comprise 423 nonempty employees and 396 empty rows.
35 nonempty rows lack email. The source organization and Employee role are not
configured locally. The old upload UI omits update_fields, so 328 matching rows
were skipped. Blank rows and unmapped new records explain the failure classes.

Add a reviewed import mode to the existing permission-protected bulk endpoint,
leaving legacy callers compatible. Accept CSV/XLSX, ignore blank records, retain
original row numbers, match all supplied stable identities, reject conflicting
or ambiguous matches, require valid email only when creating new accounts, and
never derive email addresses. Preview and execute share validation. Each row is
atomic; failed rows cannot leave partial updates. Synchronize canonical HR data
through EmployeeService and report created/updated/unchanged/skipped/failed.
New accounts receive the explicitly selected organization/role. No welcome mail
is sent by the master register import. Existing access and audit timestamps are
not taken from the register. Test in an isolated database before live import.

UI: shared import dialog for Employees and Users Management with preview,
explicit new-user mappings, safe editable field selection and visible results.
Sidebar/shared shell and unrelated work remain unchanged. No schema migration.

## Diagnosis and delivery

The original 491 failures reconcile exactly: 396 blank records, 35 employees
without email, and 60 unmatched new records with an unconfigured organization.
The source Employee role was also unconfigured. The old UI omitted update_fields
and skipped 328 matching accounts. Comparing all identifiers exposed nine
conflicting existing rows, which remain excluded rather than guessed.

User selected upsert, Rejlers Abu Dhabi (REJ_UAE) and Default (default) for new
employees. Existing organizations and roles remain intact. A shared reviewed
dialog opens directly from Employees → Import and Users Management → Import
users, replacing the CSV-only modal and correcting the template's XLSX filename.

Twenty isolated import tests passed: permissions, matching, legacy identifiers,
conflicts, duplicates, orphan accounts, rollback, repeat imports, preview parity,
canonical sync and preserved login bookkeeping. Browser import and existing
directory checks passed. Changed-source lint has no errors; both full page
dependency graphs compile with esbuild. Protected Sidebar/shared-shell hashes
match the task baseline. No migration or schema change.

The broader profile-sync suite was attempted with config.settings_rbac_test:
six cases failed on signal/deferred-field expectations; these are not reported
as passing. The later aligned release passed 93 domain regressions using
config.settings_candidate_forms_test and verified all local migrations. The
paired frontend also passed its production-mode Vite/PWA build; see
HR_RELEASE_20261022.md for the release verification boundary.

## Local import committed, 22 October 2026

After a full-file rollback trial, committed 319 updates and 60 new employees;
excluded 44 rows and ignored 396 blank records. In-transaction assertions checked
every reviewed field, existing account settings and roles, exactly 60 new
accounts, and each new employee's organization, role and canonical link.
Welcome emails were neither sent nor queued. Original source file unchanged.

Evidence in artifacts/employee-import/: import-result.json, browser screenshots
and check results, focused backend log and Employee_Register_Corrections.csv.
The correction file includes original row numbers and reasons for the 35 missing
emails and nine identity conflicts. before-import.json is a minimal local
recovery snapshot of identity/contact fields and canonical links, excluding
credentials.
