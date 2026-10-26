# S3D and RBAC Development alignment - 26 October 2026

Authority: user request to prepare all pending backend/frontend work, integrate
remote Development, verify migrations, push origin/development and create PRs.
Preserve I-06 reuse, source provenance, permissions and controlled-release gates.
This is local release preparation, not a production database/deployment claim.

## Included behavior

- DGS and LNG table extensions retain explicit source components, merged-cell
  ownership, class continuity and ambiguous/unsupported review evidence. Partial
  extraction remains partial and continues to block SPEC/CAT draft/release exports.
- Upload timeout recovery looks up only the caller's jobs (`mine=1`); one unique
  recent filename/size match resumes the accepted run without replaying a POST.
  Ambiguous/missing matches require checking History and retain the selected file.
- Workbook previews reuse/close template resources and ordered component reads;
  gzip negotiates the same JSON through the existing production route guards.
  The frontend shares only pending preview reads per login/job/workbook and
  invalidates them before edits; the counts-only duplicate preview is removed.
- Raw combined XLSX/JSON exports use optional `export_format` to avoid DRF's
  reserved renderer `format` parameter. Legacy JSON and response shapes remain.
  The live completed PMS source exported 214 classes into both formats. SPEC/CAT
  correctly returned 409 for its partial coverage; no evidence was overridden.
- Profile fallback preserves actual authenticated Django identity flags. Header
  formatting consumes the existing RBAC profile and primary role instead of
  incorrectly showing Team Member for a superuser.
- Includes the existing pending RBAC work: effective role windows/delegations,
  role-based access/catalogue corrections, cache invalidation signals, optional
  field/row permission models/helpers/base views and configuration diagnostics.
  It removes department-based Sales access fallback in favor of role grants.
  This access behavior needs review; no deployment grant reseeding is performed.

Remote integration retained 12 backend commits for I/O-list extraction and Valve
MTO corrections, and 10 frontend commits for the I/O-list workspace. Both merged
without conflicts after local checkpoint commits. No force push or main merge.

## Database evidence

The verified target is local `radai_dev` on `postgres_local`, Django 5.0/Python
3.11. A private pg_dump backup is retained in the local database container.
RBAC 0067 (nullable assignment windows and delegations) was already applied.
The audit discovered missing migrations for the pending field/row models;
0068_field_and_row_permissions supplies their empty tables, indexes and FKs.
Forward/reverse/reapply passed in an isolated PostgreSQL schema without changing
public assignments/grants. The local apply then successfully applied:

- rbac.0068_field_and_row_permissions
- instrument_io_workflow.0068_iolistextraction_iolistservicedecision_and_more
- instrument_io_workflow.0069_iolistextraction_legend_rules_and_more

Final physical audit: 656 applied migrations, zero pending/conflicts and no
missing mapped tables/columns across RBAC, S3D and I/O-list (including automatic
join tables). There remain 703 assignments, 18,667 role-permission grants,
5,001 role-module grants, no bounded assignments and no delegations.

The full migration graph is consistent, has no conflicts/pending migrations and
full-registry model autodetection reports no changes. Assignment, role-permission,
role-module and delegation fingerprints are unchanged. New permission tables are
empty. Actual physical-schema and application checks are recorded separately
from SQLite functional tests. No Celery restart or additional extraction occurs.

## Verification and limits

Pinned Python 3.11/Django 5.0 container: 211 S3D/RBAC functional tests pass with
six configured skips on isolated SQLite. The first mixed run used a URL registry
that omitted the action suite's routes/apps; it is not passing evidence. The
authorization settings now include the existing project-organizer FK dependency.
All 21 authorization tests pass with their guarded URL registry after adding
that missing test-only dependency. Those tests are separate from the 211 above.

Node 24 container: all 553 Node tests pass; three isolated browser cases verify
processing/completed/missing upload recovery without a second POST. Production
Vite/PWA build passes (124 precache entries); existing bundle-size/Browserslist
warnings remain. Changed frontend source lint has zero errors and four warnings.
The real SPEC preview compressed 439,624,698 decoded bytes to 7,226,629 wire bytes
and completed in 75.95 seconds. Whole-preview browser memory remains a limitation.

Workspace logs/backups/raw source data remain outside the repositories. Root
feature briefs are workspace-only; this note supplies reviewable release scope.
Engineering-labelled accuracy, disposable-target SP3D import and production
migrations/deployment remain separate acceptance work.
