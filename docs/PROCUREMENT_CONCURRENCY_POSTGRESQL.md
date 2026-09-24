# Purchase recommendation PostgreSQL concurrency verification

Date: 24 September 2026. Source baseline: `1c92b06`.

Scope: the existing recommendation edit/submit freshness feature only. Use
synthetic users, recommendations and approval assignments. No application
database, production data, external delivery or deployment is involved.

## Isolated environment

The test database runs in a dedicated disposable `postgres:15-alpine` container
(observed server version: PostgreSQL 15.18), with its port published only to
`127.0.0.1:15439` and its data directory on tmpfs. The application's existing
PostgreSQL container and port 5432 are not used. The password below is an
explicitly disposable synthetic test value, not an application credential.

`config.settings_procurement_postgresql_test` fixes the host, role, base database
and test database names instead of inheriting them from application settings.
It requires an explicit non-5432 port/password and limits statement/lock waits.
The underlying release harness uses local cache/email/temp media and synchronizes
current models; **it does not apply or validate the migration graph**. Outbound
notification tasks are patched in the fixtures while real local notification
records and on-commit callbacks are exercised.

Host test interpreter: Python 3.13.14 in the workspace `.venv`. This verifies the
current installed stack against this PostgreSQL version, not every deployment
runtime, isolation level or database version.

## Commands

From the workspace root (PowerShell), provision only this new container:

```powershell
docker run --detach --name radai-pr-concurrency-20260924 --label radai.purpose=pr-concurrency-verification --publish 127.0.0.1:15439:5432 --tmpfs /var/lib/postgresql/data:rw --env POSTGRES_DB=radai_pr_concurrency --env POSTGRES_USER=radai_pr_concurrency --env POSTGRES_PASSWORD=synthetic-tests-only postgres:15-alpine
docker exec radai-pr-concurrency-20260924 pg_isready -U radai_pr_concurrency -d radai_pr_concurrency
docker inspect radai-pr-concurrency-20260924 --format '{{.Config.Image}} {{json .HostConfig.PortBindings}} {{json .HostConfig.Tmpfs}}'
docker exec radai-pr-concurrency-20260924 psql -U radai_pr_concurrency -d radai_pr_concurrency -Atc 'SELECT version();'
```

Wait for `pg_isready` to report accepting connections. The first immediate
readiness probe during this run saw server startup in progress; the next probe
succeeded. The container was created from an already available local image.

From `backend/`, set only the test connection values and run the focused checks:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:RADAI_CONCURRENCY_PG_PORT = '15439'
$env:RADAI_CONCURRENCY_PG_PASSWORD = 'synthetic-tests-only'
..\.venv\Scripts\python.exe manage.py test apps.procurement.tests.test_pr_save_submit_api --settings=config.settings_procurement_postgresql_test --noinput --verbosity 2
..\.venv\Scripts\python.exe manage.py test apps.procurement.tests.test_requisition_concurrency_postgresql --settings=config.settings_procurement_postgresql_test --noinput --verbosity 2
..\.venv\Scripts\python.exe manage.py test apps.procurement.tests.test_pr_save_submit_api apps.procurement.tests.test_requisition_concurrency_postgresql --settings=config.settings_procurement_postgresql_test --noinput --verbosity 2
```

The commands create and destroy `test_radai_pr_concurrency`; do not use `--keepdb`
or point this configuration at an application server. Logs for this session are
under the workspace `.codex-temp/` directory. The first command passed all 16
existing tests. The initial nine-case race run exposed test-harness defects,
described below. After fixing those, the combined final command passed all 25
tests with no skips (exit 0, 41.039 seconds).

Cleanup commands executed from the workspace root:

```powershell
docker exec radai-pr-concurrency-20260924 psql -U radai_pr_concurrency -d radai_pr_concurrency -Atc "SELECT datname FROM pg_database WHERE datname LIKE '%pr_concurrency%';"
docker rm --force radai-pr-concurrency-20260924
docker ps -a --filter name=radai-pr-concurrency-20260924 --format '{{.Names}}'
git -C backend diff --check
git -C frontend status --short
```

Only the base `radai_pr_concurrency` database remained after Django destroyed its
test database. Container removal succeeded; the final container query returned
no rows. Git whitespace validation passed and the frontend remained unchanged.
An additional optional label-format inspection failed because PowerShell/native
argument quoting removed the template string's quotes; the earlier successful
image/port/tmpfs inspection and the literal container name identified the
disposable target. This did not affect database tests or cleanup.

## Transaction contract

The production route guard, `apps/rbac/route_guard.py:ModuleActionGuardMixin`,
wraps mutating requests in `transaction.atomic` and marks DRF error responses for
rollback. The test router uses that same guard, existing permission checks and
real domain commands.

- Edit: `PurchaseRequisitionSerializer.update` enters an atomic block, obtains
  the current row through `select_for_update`, compares `expected_updated_at`,
  then applies the update and any related changes.
- Submit: `PurchaseRequisitionViewSet.submit` enters an atomic block, locks the
  row, checks issuer authority, compares the token, then optionally saves the
  route and invokes `RequisitionWorkflowService.submit`. Nested atomic blocks
  retain the outer transaction and lock.
- Submission notifications are registered with `transaction.on_commit`; a
  rejected transaction must not persist changes or schedule its delivery.

The concurrency suite uses `TransactionTestCase` and independent thread-local
connections. It holds the first real row lock, starts the contender, observes
the waiter with PostgreSQL's blocking-session functions, then releases the
winner. Instrumentation delegates to real SQL and the real comparator. Distinct
backend PIDs and matching transaction IDs at lock, comparison and update provide
runtime evidence; a pair of sequential requests is not sufficient evidence.

## Results

**25 tests passed: 16 existing save/submit regressions plus nine new PostgreSQL
tests.** Six of the new tests force real contention between independent
connections. The other three check complete stale-request snapshots and current
permissions. The observed isolation level was READ COMMITTED for both workers
in every race.

| Scenario | Outcome | Observed winner/waiter backend PIDs | Transaction IDs |
| --- | --- | --- | --- |
| Two edits with the same saved token | 200 / 409; only winning content persists | 147 / 148 | 4780 / 4782 |
| Edit wins against submission | 200 / 409; stays draft, attempted route replacement is absent, no notices | 136 / 137 | 4047 / 4049 |
| Submission wins against edit | 200 / 409; stays submitted, stale edit absent, one assignment notice | 155 / 156 | 5332 / 5335 |
| Two submissions with the same token | 200 / 409; one transition and one assignment notice | 150 / 151 | 4962 / 4965 |
| Legacy edits omitting token | 200 / 200; serialized, later edit wins | 140 / 141 | 4229 / 4231 |
| Legacy submissions omitting token | 200 / 200; second returns active record, one transition/notice | 143 / 144 | 4411 / 4414 |

For each row, `pg_stat_activity` reported `wait_event_type = Lock` and
`pg_blocking_pids(waiter)` included the winning PID. The contender's in-flight
SQL wrapper identified an actual `SELECT ... FOR UPDATE`. The successful
operation's lock, token comparison (or intentional omission), and every PR
UPDATE all recorded one transaction ID with autocommit disabled and an active
atomic block. Conflicting contenders recorded lock/comparison and **no UPDATE**.
Submit's additional service lock remained in the same transaction.

The stale-request test compares **every PR database column**, all local
notification rows and immutable delivery-call snapshots before/after stale PATCH
and submit payloads containing content, price, route and approval-remark changes.
Both return 409 without partial changes. The existing submit-validation test
also verifies rollback of a route modification when subsequent validation fails.

Existing access behavior passed: an authorized module editor can edit another
issuer's draft but cannot submit it (403, including with a stale token). An
unprivileged user cannot edit or submit merely by possessing a valid token (403).

No application concurrency defect was found; permissions and legacy behavior
were not changed. Two defects in the new test harness were corrected before
the final run: PostgreSQL truncates `pg_stat_activity.query`, so the exact live
SQL classification is correlated with observed blocker/waiter PIDs instead of
searching truncated text; mock calls are converted to immutable snapshots before
copying, avoiding `_Call.__deepcopy__` side effects. The dedicated settings also
reject zero-padded representations of port 5432.

Local evidence files:

- `.codex-temp/pr-concurrency-postgresql-baseline.log`: 16 existing tests passed.
- `.codex-temp/pr-concurrency-postgresql-races.log`: initial test-harness failures.
- `.codex-temp/pr-concurrency-postgresql-final.log`: final 25-test pass and six
  `POSTGRESQL_CONCURRENCY_EVIDENCE` records with PIDs, transaction IDs and events.
- `.codex-temp/pr-concurrency-postgresql-evidence.json`: extracted final race records.

Reusable files added: `config/settings_procurement_postgresql_test.py`,
`apps/procurement/tests/test_requisition_concurrency_postgresql.py`, and this
report. `.gitignore` permits tracking the report. The existing approval-controls
document links this focused verification. No production code, schema or frontend
file changed.

## Compatibility and limits

The token is optional for existing clients. Without it, PostgreSQL still
serializes changes, but the server cannot determine which saved version the
caller saw: two edits can both succeed and the later edit can overwrite the
earlier one. An edit before a token-less submit can be included in that
submission without the submitter having seen it. Existing correction rules also
allow edits after submission; this feature does not freeze a submitted snapshot.

Repeated token-less submissions use the existing active-state no-op behavior
(both callers can receive 200), rather than a new idempotency-key ledger. Omitting
the token does not waive permissions, issuer checks or workflow rules. Supplied
blank/null/malformed tokens remain invalid.

The timestamp only detects writes that advance `updated_at`; it is not a global
revision counter across every import, file or background operation. These tests
do not certify unrelated source-approval, import, storage or migration behavior.
Local notification persistence and mocked task scheduling do not establish real
external delivery or durable outbox recovery.

## Pull-request CI check

Workflow: `.github/workflows/purchase-recommendation-concurrency.yml`.
The exact job/check name to require later is
**`Purchase Recommendation Concurrency (PostgreSQL)`**. It runs on pull requests
without branch/path exclusions, merge queues, and manual dispatch. Adding this
workflow does **not** enable branch protection or make the check mandatory.

The publication branch starts from `main` commit `4465330` (release PR #192).
That release already contains the verified concurrency implementation and the
16 save/submit API tests; this change adds its PostgreSQL verification and CI gate.

The job follows the existing backend Python 3.11/action conventions and starts
its own `postgres:15-alpine` service with synthetic credentials, health checks,
port 15439, and disposable tmpfs storage. It uses the verified
`config.settings_procurement_postgresql_test` settings above. The existing
Railway workflow, application settings, dependencies and runtime code are unchanged.

`requirements-procurement-ci.txt` selects only dependencies needed for the test
harness and its shared URL imports. `-c requirements.txt` retains the application's
version constraints; this is not a replacement production dependency list. The
existing open version ranges remain open rather than introducing a separate lock.

The opt-in `PurchaseRecommendationCIRunner` rejects a non-PostgreSQL connection,
an unavailable PostgreSQL server, either missing test module, an empty run, or
**any skipped test**. Ordinary failures/errors still fail. Bash `pipefail`
preserves the process exit while `tee` records logs. Ten database-free controls
test these gates separately from the real concurrency tests.

The workflow retains only dependency, runner-control, test and PostgreSQL service
logs for seven days, including failure logs. It does not reference repository
secrets or upload environment files/database dumps. If service initialization
fails before steps begin, inspect GitHub's service startup logs; artifact steps
cannot run in that situation.

Local validation on 24 September 2026 used a clean Linux Python **3.11.16**
container and PostgreSQL **15.18**, with a sanitized source copy containing no
application `.env` or production credentials. The disposable PostgreSQL container
shared the Python container's network namespace and listened on localhost:15439;
GitHub uses its service-to-runner port mapping instead. Both exercise the same
verified settings and these commands:

```bash
export RADAI_CONCURRENCY_PG_PORT=15439
export RADAI_CONCURRENCY_PG_PASSWORD=synthetic-tests-only
python -m pip install --disable-pip-version-check -r requirements-procurement-ci.txt
python -m pip check
python manage.py check --settings=config.settings_procurement_postgresql_test
python -m unittest config.test_procurement_ci_runner -v
python manage.py test \
  apps.procurement.tests.test_pr_save_submit_api \
  apps.procurement.tests.test_requisition_concurrency_postgresql \
  --settings=config.settings_procurement_postgresql_test \
  --testrunner=config.procurement_ci_runner.PurchaseRecommendationCIRunner \
  --noinput --verbosity 2
```

The clean dependency install, `pip check`, Django check and all **10 runner
controls passed**. The exact two-module CI command passed **25 tests, zero skips**
in 24.102 seconds, including all six PostgreSQL contention scenarios. The log
contains blocker/waiter backend PIDs and same-transaction lock/compare/mutation
evidence. No application changes were needed.

Two process-level failure probes also passed: the same command with
`RADAI_CONCURRENCY_PG_PORT=15440` returned exit **1** on connection refusal;
a temporary wrapper marked one PostgreSQL test skipped and invoked the same
management command, which returned exit **1** after reporting the unexpected
skip. Both ran through `set -euo pipefail` and `tee`, proving log capture did not
hide the failure. The wrapper changed no repository test file.

Local evidence is retained under the workspace `.codex-temp/` directory:
`pr-concurrency-ci-linux.log`, `pr-concurrency-ci-unavailable.log`,
`pr-concurrency-ci-injected-skip.log`, and `pr-concurrency-ci-postgresql.log`.

After validation, only the base synthetic database remained; Django had removed
its test database. Both disposable CI containers were removed. Automatic approval
review rejected recursive deletion of the sanitized source copy with "blocked by
policy", so it remains at
`C:\Users\firaolak\AppData\Local\Temp\radai-pr-ci-source-002733b157434b3289a13ae4a12e66a3`.

The workflow also passed `actionlint` **1.7.12**, downloaded from its official
release and checked against the published SHA-256 checksum:

```text
actionlint.exe -shellcheck= .github/workflows/purchase-recommendation-concurrency.yml
git diff --check
```

ShellCheck was unavailable; Bash executed the actual test/tee pipelines locally.
This record describes **local validation**, not a hosted GitHub Actions run.
Checkout, cache, service orchestration and artifact uploading were not exercised
by these local commands. Subsequent hosted results belong to the pull request's
checks and linked workflow run. No repository protection settings or deployment
workflows are changed by this feature.
