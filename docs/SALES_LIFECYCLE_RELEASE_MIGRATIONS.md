# Backend dependency and migration verification — 1 October 2026

Release verification follows development source `eed77847c9e44266b9055b2dc146db9bb3d42058`,
including main `cc03c276`, the reviewed validation repairs in `1d189261`, and
the Docker runtime-library fix. Root owns Git publication. This report covers backend
dependencies, compilation, system checks and migrations; functional test evidence
is recorded separately in `SALES_LIFECYCLE_RELEASE_TESTS.md`.

## Targets and safeguards

The running local database was explicitly verified as PostgreSQL
`postgres_local/radai_dev` before inspection or mutation. Startup catalogue sync
and storage CORS setup were disabled for verification commands. A private custom
format `pg_dump` was completed and its archive catalogue validated with
`pg_restore --list` before applying migrations. The dump and all detailed logs
are ignored artifacts, not release source.

Synthetic DDL checks use the task-labeled PostgreSQL 15 container
`radai-sales-lifecycle-release-pg-20261001` on loopback port 15453. Every probe uses
its own specially named verification database and refuses a populated target.
The functional test agent exclusively owns `test_radai_pr_concurrency` on that
server. No production database, storage object or external provider is used.

## Full local migration graph

Initial inspection found consistent applied history and no graph conflicts.
All Sales 0014–0020 and the six shared-record migrations were already applied.
Fifteen migrations from the merged engineering source were pending:

- PID Verification `0014_pidvtagindex`.
- PID Checker V2's alternate 0015 branch through `0024_merge_20261001_1442`.
- Valve MTO 0001–0004.

The existing PID legend-scope 0015 branch was already applied. The 0024 merge
depends on both branches. Before applying the incoming remove/re-add sequence,
the verifier confirmed that its three `owner_project_id` columns did not exist
in the local baseline; existing project-scope values could not be silently
discarded by that sequence. Application uses actual `migrate` operations, never
`--fake` or manual recorder updates.

All fifteen actual migrations applied successfully. The stronger schema audit
then detected three absent managed tables even though the recorder had no
pending migrations. Finance's current migration state already declares its
workflow and notification tables, but those physical tables were missing. The
P&ID V2 tag-index model had no checked-in 0006 migration.

Two reviewed forward migrations resolve those findings:

- `finance/0017_restore_missing_payroll_workflow_tables.py` creates only missing
  declared workflow/log tables; it adds no workflow or payroll data.
- `pid_verification_v2/0006_pidvtagindex.py` adds the actual model state and
  creates its missing table.

Both preserve compatible existing tables. `config/migration_schema.py` rejects
partial schemas, incompatible types/nullability, missing keys/indexes, missing
automatic ID generators and non-equivalent positive-number checks. Both repairs
are intentionally forward-only: they cannot infer whether an existing table was
created by this release, so rollback must retain its data and use a forward fix.
They create no approvals, notifications, tag rows or business backfill.

The two repairs applied locally after their synthetic PostgreSQL probe passed.
The final process exited **0**: the entire graph is consistent and has **zero
pending migrations**, all managed-model tables/columns exist, full-registry
`makemigrations --check --dry-run --noinput --skip-checks` reports **No changes
detected**, and `check` reports **no issues (0 silenced)**. Seventeen real
migrations were applied during this release verification; none were faked.

Local execution used Python 3.11.16 in `radai_backend_local`, with an exact
native-disk source copy to avoid slow Windows bind-mount imports. The task-owned
`local_migration_verify.py` asserts the target and backup confirmation before
calling `migrate --noinput --skip-checks`, then uses the full application
`MigrationExecutor` and PostgreSQL catalogue for the checks above. Logs retain
the initial missing-table failure and final passing run:
`local-migrate-and-check.log`, `full-model-drift-initial.log`, and
`local-migrate-and-check-final.log`.

## Additive PostgreSQL DDL probes

The maintained scripts execute actual PostgreSQL schema operations and verify
source preservation, foreign keys/uniqueness and the relevant reverse guards:

```powershell
$env:RADAI_CONCURRENCY_PG_PORT='15453'
$env:RADAI_CONCURRENCY_PG_PASSWORD='<explicit synthetic test password>'
$env:PYTHONIOENCODING='utf-8'
$env:RBAC_AUTO_SYNC_MODULES='0'
..\.venv\Scripts\python.exe scripts/check_shared_record_migrations.py
..\.venv\Scripts\python.exe scripts/check_proposal_review_migration.py
..\.venv\Scripts\python.exe scripts/check_bid_preparation_migration.py
..\.venv\Scripts\python.exe scripts/check_opportunity_folder_tag_migration.py
..\.venv\Scripts\python.exe scripts/check_attachment_storage_encoding_migration.py
..\.venv\Scripts\python.exe scripts/check_document_control_migrations.py
..\.venv\Scripts\python.exe scripts/check_document_custom_tag_migration.py
..\.venv\Scripts\python.exe scripts/check_release_schema_repairs.py
```

The shared-record check covers core 0014, invoice_tracker 0006, finance 0016,
planning_intelligence 0049, project_control 0011 and project_organizer 0003.
The Sales checks cover 0014–0020. The proposal-review probe builds its historical
0013 dependency state directly. Other probes reconstruct the predecessor from
synchronized current models. These are additive-operation checks, not a fresh
replay of the entire repository's historical migration chain.

All seven Sales/shared-record probes exited **0**. The final repair probe also
exited **0**, verifying actual creation, populated-table preservation, foreign
keys/uniqueness/indexes, incompatible column/type/nullability rejection, absent
automatic ID generation, wrong positive-number checks and reversal refusal.
It uses minimal synthetic referenced primary-key tables and directly executes
the two repair migrations. Their state and actual local full graph are checked
separately above. The probe runner uses host Python 3.13.14/Django 5.0; the local
application checks use canonical Python 3.11.16/Django 5.0. Logs are named after
each script, plus `schema-repair-postgresql-final.log`.

## Python and container checks

The canonical running container reports Python 3.11.16 and passes
`python -m pip check`. The fresh image also uses Python 3.11.16. Its clean build
context copies Git-tracked files from the synchronized checkout and refreshes
every changed path through the frozen `eed77847` commit. The repository
Dockerfile and `.dockerignore` exclude local configuration, logs, data and
database archives. The final source layer includes the repairs and OCR
correction; dependencies were installed fresh for this release and reused in
the final source-layer build.

```powershell
docker build --progress plain --tag radai-backend-release-20261001-final `
  --label org.opencontainers.image.revision=eed77847c9e44266b9055b2dc146db9bb3d42058 `
  --file artifacts/sales-lifecycle-release-20261001/build-context/Dockerfile `
  artifacts/sales-lifecycle-release-20261001/build-context
```

An initial `python -m compileall -q apps config` found an invalid quote literal
in `apps/designiq/pid_ocr_extractor.py`. The functional test agent corrected the
literal without changing parsing intent; the final canonical compilation passes.
The initial failed log is retained alongside the passing rerun.

Fresh dependency imports exposed a missing `libGL.so.1` needed by the OpenCV
contrib wheel resolved by PaddleOCR. The Dockerfile now installs `libgl1` and
checks dependencies plus `cv2`/`olefile` imports during the build. No package
version or business behavior was changed to resolve this native-library error.

The final build exited **0**. Its image ID is
`sha256:0472dce2538189c8a8883a4db909617434b7e1c37591b7586d1ddca5c38c28d6`,
tagged `radai-backend-release-20261001-final` and labeled with the full release
commit above. `docker-build-final-cached.log` records the passing build. An
earlier Git-archive build attempt invalidated the dependency cache and was
deliberately canceled; `docker-build-final.log` is not passing build evidence.

An isolated, network-disabled container from the final image passed all of:

- Source equality against the `eed77847` Git archive for both repairs, their
  helper, the OCR fix, Sales readiness/AI/serializer/routes, and requirements
  (CRLF normalized to LF).
- `python -m pip check` and `python -m compileall -q apps config`.
- Imports for Django, psycopg2, olefile, pandas, openpyxl, PyPDF2, PyMuPDF,
  OpenCV, Torch/Torchvision, OpenAI/Anthropic/Google clients, Paddle,
  EasyOCR and PaddleOCR; real OpenCV/Torch/Paddle array operations.
- `python manage.py check --settings=config.settings` with **zero issues**.
- `python manage.py collectstatic --noinput --verbosity=0 --settings=config.settings`.

These commands use synthetic environment values and isolated SQLite, with
startup RBAC/catalogue and storage CORS mutation disabled. They load no customer
data, OCR model weights or external AI provider. The runtime process exited
**0**, recorded in `final-image-checks.log`. Notable resolved versions are
pandas 3.0.6, openpyxl 3.1.5, OpenCV 4.10.0, Torch 2.5.1+cpu, Paddle 3.3.0 and
PaddleOCR 3.3.3. The separate native dependency-layer probe also passed
(`native-dependency-check.log`).

Focused tests in that final image passed **76 tests in 26.311 seconds**, exit
**0**, with no skips. They cover proposal readiness and AI drafting plus
Finance source imports, customer invoice mapping and workbook uploads against
the freshly resolved dataframe/Excel dependencies. The image deliberately
excludes tests, so only exact-commit test modules and isolated test settings were
copied from the `eed77847` archive into the throwaway container; application
modules and dependencies remained those of the built image. The command was:

```text
python manage.py test apps.sales.tests.test_proposal_readiness
  apps.sales.tests.test_proposal_draft_ai
  apps.finance.tests_receivables_source
  apps.finance.tests_customer_invoice_register
  apps.invoice_tracker.tests_workbook_upload
  --settings=config.settings_release_test --noinput --verbosity=2
```

The command above is one invocation (wrapped for readability). Evidence is in
`final-image-focused-tests.log`; the synthetic SQLite test database was
destroyed normally. This smoke suite complements the separate PostgreSQL and
broader functional checks; it does not replace them.

## Evidence and deployment limits

Detailed local evidence is under
`artifacts/sales-lifecycle-release-20261001/`; it is intentionally outside Git.
Production migration state and a production restore have not been verified.
Publication is blocked while any required gate remains failed or incomplete.

Deploy the forward schema before starting code that needs the new fields.
Reviewed identity, proposal, version and classification evidence has guarded
reverse migrations. After gzip uploads exist, retaining schema alone does not
make an older application safe: keep an encoding-aware reader or perform an
explicitly reviewed and verified decompression migration before downgrading.
No production deployment or main-branch merge is part of these checks.
