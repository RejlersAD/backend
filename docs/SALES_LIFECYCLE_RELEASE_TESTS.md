# Sales lifecycle backend release verification — 1 October 2026

Initial integration baseline: development
`a160bdb7c9d28253d79ce151145a12c0b91cd500`, after the release implementation and
`origin/main` `cc03c276` were combined. Final source baseline is
`1d189261bc3aa5407741899458e88ed0c9291aa8`, including the fixes below and the
separately verified schema repairs. The subsequent `eed77847` commit changes
only Dockerfile runtime dependencies; Python source is unchanged and its fresh
image checks are reported by the image verifier. Documentation-only updates may follow.
The release coordinator owns remote alignment, commits, pushes and the final
main-base PR. This document records backend functional and source checks only;
dependency installation, image builds and actual migration probes have separate
release evidence. No production deployment or main merge is implied.

## Scope and environment

The inspected release includes canonical client/project/employee references;
bid-to-proposal preparation; Opportunity private uploads, compression, document
versions, folder tags and classification/custom tags; proposal review/register;
Sales mailbox search; bid decision access/AI; and proposal eligibility/AI drafting.
Adjacent Planning, Finance, invoice, Organizer and RBAC checks cover the shared
contracts. This is a comprehensive affected-domain run, not a claim that every
application in the repository was tested.

Consulted the workspace/repository AGENTS instructions, workspace README,
PROJECT_BRIEF, REPOSITORY_GAP_REPORT, DECISIONS, SECURITY, CONTRIBUTING and the
shared-record, bid-preparation, document-control and proposal-start feature
briefs. The approved requirements preserve current authority, immutable evidence,
retry identity, atomic audit effects and explicit human review of AI output.

Functional checks use the existing Windows virtual environment, Python 3.13.14,
synthetic records, temporary local storage and mocked provider/Graph delivery.
No live customer documents or external AI calls are required. The canonical
Python 3.11 deployment image/dependency checks are reported separately. Final
PostgreSQL checks run with the existing provisioned Python 3.11.16 Linux runtime
in `radai_backend_local`, from an isolated native `/tmp` source copy containing
the final source fixes. The running web process, its settings and application
database are not changed. This runtime is distinct from the freshly rebuilt
image; the image verifier separately checks its dependency resolution.

SQLite uses `config.settings_release_test` (current-model synchronization,
isolated database/cache/email/media). Guarded RBAC uses
`config.settings_permissions_test`. PostgreSQL uses the explicit disposable-only
`config.settings_procurement_postgresql_test`, PostgreSQL 15 on loopback port
15453, and database `test_radai_pr_concurrency`. The final Linux test process
connects directly to the named task container on its internal port 5432; only
this process's in-memory host/port settings differ from the published-port
harness. Its normal statement/lock
timeouts remain 20/15 seconds. Model-sync tests do not validate migration history.

Logs are local artifacts under `artifacts/sales-lifecycle-release-20261001/`.
They are not shipped as application source and contain synthetic test data.

## Results

| Check | Result | Artifact |
| --- | --- | --- |
| All Sales functional modules (49 modules) | Passed: 993 run, 6 PostgreSQL-only skips; 595.960 seconds | `sales-functional-final.log` |
| Shared identity, Planning, Project Control, Organizer, Finance/invoice | Passed: 360 run, 7 PostgreSQL-only skips; 212.633 seconds | `identity-planning-finance-final.log` |
| RBAC business approval, module/actions, overrides and service access | 58 passed, 8.931 seconds | `rbac-security.log` |
| Python 3.11/PostgreSQL affected functional, contention, native invoice and AI suites | 389 run: 382 passed, 7 host-allowlist skips; 656.717 seconds | `canonical-postgresql-functional.log` |
| Mandatory Purchase Recommendation CI runner controls | 10 passed, 0.043 seconds | `canonical-procurement-runner-controls.log` |
| Fresh-image Purchase Recommendation strict PostgreSQL suites plus 7 native invoice cases | 33 passed, no skips; 39.299 seconds | `canonical-procurement-postgresql.log` |
| Final changed Python compilation | Passed, 118 files | `final-python-compile.log` |
| Final changed Python fatal-code lint (`E9,F63,F7,F82`) | Passed, 118 files | `final-python-fatal-lint.log` |
| Final release-diff whitespace check | Passed | `final-changed-whitespace.log` |
| Shared-record/Project Control isolation regression | 17 passed, 4.428 seconds | `project-control-isolation.log` |
| Bid access/preparation/readiness audit-isolation regression | 60 passed, 73.701 seconds | `sales-audit-isolation.log` |
| Three release fix files: compilation, fatal lint and whitespace | Passed | `release-fixes-{compile,fatal-lint,whitespace}.log` |

The initial cross-module run exposed import-order coupling in
`apps/project_control/tests/test_phase0_access.py`: another test module had
wrapped shared URL callbacks with module/action guards, while these older
fixtures deliberately exercised project object access without module grants.
The two object-access tests now construct their own router from the original
`EstimateViewSet`; their original outsider/read/approval-denial assertions are
retained. Production routes and permission rules are unchanged. Guarded HTTP
authorization remains covered by the separate RBAC and feature suites.

The initial Sales run found one additional test-isolation defect: the bid-decision
audit-failure test temporarily replaced `workflow._audit`, and a domain module
first imported during that request retained the mock after cleanup. Fifteen later
preparation tests consequently saw the injected failure. The fixture now fails
`OpportunityAuditEvent.objects.create` instead, exercising real transactional
audit rollback without replacing a function that another module may import.

The canonical image's broader compilation found a pre-existing invalid string
literal in `apps/designiq/pid_ocr_extractor.py:221`. A delimiter-only correction
restores valid Python without changing the intended ASCII quote replacement.
A synthetic line parser probe accepts both quote forms without constructing or
initializing OCR engines (`pid-parser-syntax.log`).

The original Windows PostgreSQL run was deliberately interrupted to avoid slow
host-to-container SQL round trips. It is not counted as a passing gate. Only the
verified test worker was stopped; zero remaining test-database sessions were
confirmed before dropping that synthetic database and starting the complete
Linux run. The disposable PostgreSQL container alone was attached to the local
Docker network. No running application container was committed or redeployed.

The 389-test run contains 389 distinct logged test method identities. Its seven
invoice skips were caused by the existing native-invoice fixture's explicit local
host allowlist, which rejects a Docker service hostname. No guard was relaxed.
Those cases passed in the final image sharing the disposable PostgreSQL
container's network namespace, using loopback `127.0.0.1:5432`, alongside the
mandatory Purchase Recommendation suite. Its strict runner rejects any skip.
The six Sales row-lock tests skipped by SQLite all executed in the 389-test run.
All seven invoice cases executed in the final 33-test strict run, alongside the
26 required Purchase Recommendation tests. Both PostgreSQL processes exited 0.
The two logs contain 415 distinct test method identities (389 + 33, with seven
overlapping invoice methods); each executed successfully in at least one run.
Other suite counts overlap across runtimes and should not be summed as unique
coverage. No functional or source gate listed here remains failed or pending.
Final SQL verification reports zero `test_radai_pr_concurrency` databases and
zero sessions (`postgresql-cleanup.log`). The isolated `/tmp` test source copy
was removed after both processes exited. The release coordinator may now remove
the labeled disposable PostgreSQL container; the normal application is untouched.

## Exact functional commands

Run from the backend repository in PowerShell. The explicit environment avoids
discovering the normal application database or object storage:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:AIFLOW_ENVIRONMENT = 'testing'
$env:ENVIRONMENT = 'testing'
$env:USE_S3 = 'false'
$env:SPEC_SKIP_CORS_ON_READY = '1'
$env:S3_AUTO_APPLY_CORS = '0'
```

All Sales functional modules were selected deterministically; PostgreSQL-only
modules and migration probes run separately:

```powershell
$salesTestModules = @(Get-ChildItem -LiteralPath apps/sales/tests -Filter 'test*.py' |
    Where-Object { $_.Name -notmatch 'migration|concurrency|postgresql' } |
    Sort-Object Name | ForEach-Object { 'apps.sales.tests.' + $_.BaseName })
..\.venv\Scripts\python.exe manage.py test @salesTestModules --settings=config.settings_release_test --noinput --verbosity=2
```

The exact expanded module list is also saved in `sales-functional-modules.txt`
beside the logs. The adjacent-domain invocation is:

```powershell
..\.venv\Scripts\python.exe manage.py test `
  apps.core.tests.test_shared_records `
  apps.core.tests.tests_project_portfolio `
  apps.core.tests.tests_project_milestones `
  apps.planning_intelligence.tests.test_sales_preparation `
  apps.planning_intelligence.tests.test_shared_record_identity `
  apps.planning_intelligence.tests.test_technical_proposals `
  apps.planning_intelligence.tests.test_business_approval_gates `
  apps.planning_intelligence.tests.test_resource_planning `
  apps.planning_intelligence.tests.test_project_archival `
  apps.planning_intelligence.tests.test_project_setup `
  apps.planning_intelligence.tests.test_schedule_approval `
  apps.planning_intelligence.tests.test_identity_policy `
  apps.planning_intelligence.tests.test_operational_action_guard `
  apps.project_control.tests.test_phase0_access `
  apps.project_control.tests.test_actuals_and_snapshots `
  apps.project_control.tests.test_cumulative_actuals `
  apps.project_organizer.tests `
  apps.finance.tests_shared_record_links `
  apps.finance.tests_customer_invoice_register `
  apps.finance.tests_customer_invoice_source_register `
  apps.finance.tests_receivables_source `
  apps.finance.tests_receivables_source_dashboard `
  apps.invoice_tracker.tests_collections `
  apps.invoice_tracker.tests_duplicate_invoices `
  apps.invoice_tracker.tests_bulk_duplicate_invoices `
  apps.invoice_tracker.tests_identity_conflicts `
  apps.invoice_tracker.tests_workbook_upload `
  --settings=config.settings_release_test --noinput --verbosity=2

..\.venv\Scripts\python.exe manage.py test `
  apps.rbac.tests.tests_business_approval `
  apps.rbac.tests.tests_module_actions `
  apps.rbac.tests.tests_action_enforcement `
  apps.rbac.tests.tests_user_permission_overrides `
  apps.rbac.tests.tests_service_access `
  --settings=config.settings_permissions_test --noinput --verbosity=1

..\.venv\Scripts\python.exe manage.py test `
  apps.core.tests.test_shared_records apps.project_control.tests.test_phase0_access `
  --settings=config.settings_release_test --noinput --verbosity=2

..\.venv\Scripts\python.exe manage.py test `
  apps.sales.tests.test_bid_decision_access apps.sales.tests.test_bid_preparation `
  apps.sales.tests.test_proposal_readiness `
  --settings=config.settings_release_test --noinput --verbosity=2
```

The original (interrupted) PostgreSQL invocation used only the task-owned
disposable server. The password below is a synthetic local test credential,
not an application secret:

```powershell
$env:RADAI_CONCURRENCY_PG_PORT = '15453'
$env:RADAI_CONCURRENCY_PG_PASSWORD = 'synthetic-release-tests-only'
..\.venv\Scripts\python.exe manage.py test `
  apps.core.tests.test_shared_records `
  apps.core.tests.test_shared_records_postgresql `
  apps.planning_intelligence.tests.test_sales_preparation `
  apps.planning_intelligence.tests.test_shared_record_identity `
  apps.finance.tests_shared_record_links `
  apps.sales.tests.test_bid_preparation `
  apps.sales.tests.test_bid_preparation_postgresql `
  apps.sales.tests.test_attachment_streaming `
  apps.sales.tests.test_private_attachments `
  apps.sales.tests.test_opportunity_workspace `
  apps.sales.tests.test_workspace_documents `
  apps.sales.tests.test_document_versions `
  apps.sales.tests.test_document_classification `
  apps.sales.tests.test_document_classification_msg `
  apps.sales.tests.test_document_classification_postgresql `
  apps.sales.tests.test_folder_tags `
  apps.sales.tests.test_folder_tags_postgresql `
  apps.sales.tests.test_proposal_review `
  apps.sales.tests.test_proposal_readiness `
  apps.sales.tests.test_proposal_readiness_postgresql `
  --settings=config.settings_procurement_postgresql_test --noinput --verbosity=2
```

The final Linux invocation selects those same 20 labels plus:

```text
apps.invoice_tracker.tests_duplicate_invoices.DuplicateInvoicePostgreSQLTests
apps.invoice_tracker.tests_bulk_duplicate_invoices.BulkDuplicateInvoicePostgreSQLTests
apps.sales.tests.test_proposal_draft_ai
apps.sales.tests.test_bid_justification
apps.sales.tests.test_email_ai_provider
```

`canonical-postgresql-modules.txt` records that exact 25-label list beside the
logs. `run_postgresql_functional.py` is a task-only launcher in the isolated
source directory. Its complete behavior is:

```python
import importlib
import os
import sys

if os.environ.get('RADAI_RELEASE_PG_NAMESPACE') != 'radai-sales-lifecycle-release-pg-20261001':
    raise SystemExit('This launcher requires the named disposable test network.')
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_procurement_postgresql_test'
settings = importlib.import_module(os.environ['DJANGO_SETTINGS_MODULE'])
database = settings.DATABASES['default']
assert database['HOST'] == '127.0.0.1'
assert database['NAME'] == database['USER'] == 'radai_pr_concurrency'
assert database['TEST']['NAME'] == 'test_radai_pr_concurrency'
assert str(database['PORT']) == '15453'
host = os.environ.get('RADAI_RELEASE_PG_NETWORK_HOST', '127.0.0.1')
if host not in ('127.0.0.1', 'radai-sales-lifecycle-release-pg-20261001'):
    raise SystemExit('Only the task-owned PostgreSQL service is permitted.')
database['HOST'] = host
database['PORT'] = '5432'
from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py', 'test', *sys.argv[1:],
    '--settings=config.settings_procurement_postgresql_test', '--noinput', '--verbosity=2'])
```

The source copy was made from the image verifier's complete native snapshot of
the synced source, including the current schema repairs/OCR fix, with the two
final test-isolation fixes overlaid. Required Python/document/PostgreSQL imports
and an actual connection to the named disposable PostgreSQL 15.18 service were
verified before replacing the interrupted run. The execution command was:

```powershell
$releasePgModules = @(Get-Content -LiteralPath artifacts/sales-lifecycle-release-20261001/canonical-postgresql-modules.txt)
docker exec -e PYTHONIOENCODING=utf-8 -e PYTHONUNBUFFERED=1 -e PYTHONDONTWRITEBYTECODE=1 `
  -e RADAI_CONCURRENCY_PG_PORT=15453 -e RADAI_CONCURRENCY_PG_PASSWORD=synthetic-release-tests-only `
  -e RADAI_RELEASE_PG_NAMESPACE=radai-sales-lifecycle-release-pg-20261001 `
  -e RADAI_RELEASE_PG_NETWORK_HOST=radai-sales-lifecycle-release-pg-20261001 `
  -w /tmp/radai-sales-release-tests-20261001 radai_backend_local `
  python run_postgresql_functional.py @releasePgModules
```

The mandatory hosted workflow `.github/workflows/purchase-recommendation-concurrency.yml`
adds the database-free runner controls and strict PostgreSQL runner. After the
389-test process exited and database absence was confirmed, the strict suite and
native invoice cases ran in fresh image `radai-backend-release-20261001-final`
(image prefix `0472dce25381`, source `eed77847`) sharing only the disposable
database's network namespace. The commands are:

```powershell
docker exec -e PYTHONIOENCODING=utf-8 -e PYTHONUNBUFFERED=1 -e PYTHONDONTWRITEBYTECODE=1 `
  -e DJANGO_SETTINGS_MODULE=config.settings_procurement_postgresql_test `
  -e RADAI_CONCURRENCY_PG_PORT=15453 -e RADAI_CONCURRENCY_PG_PASSWORD=synthetic-release-tests-only `
  -w /tmp/radai-sales-release-tests-20261001 radai_backend_local `
  python -m unittest config.test_procurement_ci_runner -v

Get-Content -Raw -LiteralPath artifacts/sales-lifecycle-release-20261001/run_postgresql_functional.py | `
docker run --rm -i --name radai-sales-release-ci-tests-20261001 `
  --label radai.task=sales-lifecycle-release-functional-20261001 `
  --network container:radai-sales-lifecycle-release-pg-20261001 `
  -e PYTHONIOENCODING=utf-8 -e PYTHONUNBUFFERED=1 -e PYTHONDONTWRITEBYTECODE=1 `
  -e RADAI_CONCURRENCY_PG_PORT=15453 -e RADAI_CONCURRENCY_PG_PASSWORD=synthetic-release-tests-only `
  -e RADAI_RELEASE_PG_NAMESPACE=radai-sales-lifecycle-release-pg-20261001 `
  -w /app --entrypoint python radai-backend-release-20261001-final - `
  apps.procurement.tests.test_pr_save_submit_api `
  apps.procurement.tests.test_requisition_concurrency_postgresql `
  apps.invoice_tracker.tests_duplicate_invoices.DuplicateInvoicePostgreSQLTests `
  apps.invoice_tracker.tests_bulk_duplicate_invoices.BulkDuplicateInvoicePostgreSQLTests `
  --testrunner=config.procurement_ci_runner.PurchaseRecommendationCIRunner
```

## Exact source checks

The initial manifest contained 111 Python files. The final source manifest
`final-changed-python-files.txt` contains all 118 changed Python files against
`origin/main`, including the release fixes and additional schema-repair sources
committed in `1d189261`. The three functional fix files were also checked separately.

```powershell
$releasePythonFiles = @(git diff --name-only origin/main...HEAD -- '*.py')
..\.venv\Scripts\python.exe -m compileall -q @releasePythonFiles
..\.venv\Scripts\python.exe -m flake8 @releasePythonFiles --select=E9,F63,F7,F82
git diff --check origin/main...HEAD
```

This is fatal-code lint, not a claim of repository-wide style compliance.
Frontend/browser/build evidence, real migration forward/reverse checks,
dependency compatibility and final GitHub mergeability are separate release gates.
