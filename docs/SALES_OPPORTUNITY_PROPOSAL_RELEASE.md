# Opportunity to saved proposal draft — 19 October 2026

Scope: SAL-FLOW-01 in the shared workspace decision register. Preserve existing
Sales access, department owner rules, CEO business authority and client gates.

Changes:

- Map the existing Deal CEO action to HTTP approval permission. The domain
  still requires the CEO business position and valid lifecycle state.
- Lock/reload qualification submissions so stale or concurrent calls cannot
  create duplicate review notifications or audit transitions.
- Repair the isolated app registry and stale fixtures, including Sales department
  owners, real notification fields, completed upstream gates and explicit denies
  that remain effective over inherited Sales department permissions.
- Add real guarded HTTP journeys for Bid and Conditional Bid through saved draft,
  plus PostgreSQL concurrent qualification coverage. No application schema change.

Reproducible PostgreSQL validation uses `config.settings_sales_postgresql_test`
and an explicitly provisioned disposable `radai_sales_verify_20261019` service
or loopback connection. Set RADAI_SALES_TEST_PG_HOST, RADAI_SALES_TEST_PG_PORT and
RADAI_SALES_TEST_PG_PASSWORD. Credentials belong only to the disposable service.
The database names and user are fixed to the verification environment; never
point these settings at application databases.

Run `python manage.py test apps.sales.tests.test_opportunity_proposal_flow
apps.sales.tests.test_vf_registration_api apps.sales.tests.test_vf_registration_core
apps.sales.tests.test_workflow apps.sales.tests.test_bid_preparation
apps.sales.tests.test_proposal_readiness apps.sales.tests.test_proposal_register
apps.sales.tests.test_proposal_draft_ai apps.sales.tests.test_proposal_review
apps.sales.tests.test_proposal_readiness_postgresql
apps.sales.tests.test_vf_registration_concurrency
--settings=config.settings_sales_postgresql_test --noinput`.

Functional tests synchronize current models. Fresh application migrations must
also be applied to a separate disposable database using normal settings before
claiming migration compatibility. Production frontend build/browser evidence is
recorded separately in the frontend workspace artifacts.

## Verified checks

- Isolated backend regression suite on Python 3.11: 146 tests, 144 passed and
  two PostgreSQL-only cases skipped. These cases are exercised separately below.
  Frontend artifact: `artifacts/sales-flow-backend-regression-final.log`.
- PostgreSQL journeys/locking: all five checks passed. Both Bid and Conditional
  Bid complete registration, qualification, recipient read, CEO authorization,
  preparation selection and explicit saved draft. Non-CEO decisions are denied;
  another recipient cannot acknowledge the owner's notification. Incomplete gates
  do not save drafts. Duplicate registration
  and proposal requests do not duplicate business records. Concurrent qualification
  produces one review event/notification. A client block committed during proposal
  creation is rechecked under lock. Artifact: `sales-flow-postgresql-journey.log`.
- Frontend helper tests: all 43 passed. All 14 proposal browser cases and four
  selected registration browser cases passed, including desktop/mobile Axe checks.
- All six additional PostgreSQL tests passed: concurrent/repeated registrations,
  distinct actor numbering, changed-payload conflicts, rollback recovery and
  competing review updates. Artifact: `sales-flow-postgresql-concurrency-final.log`.
- Scoped frontend ESLint and repository whitespace checks passed. Both Vite/PWA
  builds passed. The explicit production-profile bundle selects production and
  contains the configured production backend API URL. Artifact:
  `sales-flow-production-env-build.log`.
- `makemigrations sales --check --dry-run --skip-checks` passed with no Sales
  changes detected. Artifact: `sales-flow-migration-drift.log`.

Builds retain existing Browserslist age and large-bundle warnings. These checks
do not exercise a live AI provider or external document/mail delivery; manual
proposal preparation does not depend on those optional integrations.

- Fresh full-application migrations passed: 600 applied, including Sales 0001
  through 0032. The three seeded letter templates exist. Artifact:
  `sales-flow-migrations.log`.
- Both Bid and Conditional Bid journeys also passed through `config.urls` using
  the normal full application registry and the freshly migrated PostgreSQL
  schema. Each saved draft was read back and its lifecycle/audit identities
  checked. The enclosing transactions were rolled back; SQL confirmed zero
  synthetic users, opportunities and quotes remained. Artifact:
  `sales-flow-migrated-full-routes.log`.

To reproduce the migrated-schema check, run normal `manage.py migrate` against
the guarded disposable database, then run:

```sh
python manage.py shell -c "import apps.sales.tests.verify_migrated_flow"
```

This helper refuses application database names and hosts. The eleven dedicated
PostgreSQL checks above use model synchronization;
this additional check validates the real migration-backed schema and complete
application routing.

Local activation: restarted only `radai_backend_local`. After cold startup,
`http://localhost:8000/api/v1/health/` and the frontend proxy at
`http://localhost:5173/api/v1/health/` both returned 200. The disposable
verification container was removed. Existing local Sales business records and
production services were not changed.

## Deployment conditions

Deploy the backend approval registration and qualification lock together with
the frontend label correction. This repair adds no migrations, endpoints or
environment variables to application configuration. Apply existing repository
migrations normally during release; never use model-sync test settings in deployment.

The target environment must have active Sales department assignees, a CEO's
canonical active employee/business position and approval access, and permitted
Active/Prospect clients. Explicit permission denies still win. Keep the configured
bid approval route or the previously authorized fallback policy; this repair
does not authorize new production grants or change commercial approval policy.

Frontend release builds must set VITE_AIFLOW_ENVIRONMENT=production and use the
production backend configuration. After rollout, check authenticated readiness
and complete a controlled acceptance journey with the actual production roles.
Local tests do not certify those account assignments or remote deployment state.
No production deployment is performed by this verification.
