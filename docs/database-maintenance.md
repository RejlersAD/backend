# Admin Console database cleaning

**Currently disabled.** The Maintenance tab hides database cleaning, and both
backend endpoints reject requests while `DATABASE_MAINTENANCE_ENABLED` is false
(the default). The implementation remains available for later use. Re-enabling
requires explicitly setting that backend flag to true, restoring the
`DatabaseCleaning` component in `AdminDashboard`, and reloading backend workers.

Open **Admin Console → Maintenance → Database cleaning** to search the default
application database's tables. PostgreSQL tables include their schema names;
the inventory includes tables without a registered Django model. PostgreSQL row
counts are catalog estimates and can be unavailable or stale until statistics
are updated. SQLite row counts are exact.

Administrators can inspect the inventory. Active super administrators can:

- **Delete data:** delete every row in the selected table, keeping its structure
  and sequence. Files in external storage are unaffected.
- **Delete table:** remove the table, indexes, and its data. Application features
  that depend on the table will require restoration. This does not reset Django
  migration history; rerunning `migrate` will not recreate an already-applied table.

Both actions require the exact table name to be typed in the confirmation dialog.
Have a current backup available before using either action.

## Boundaries

- Authentication, permission assignments, audit history, and migration tables
  remain visible and protected.
- Incoming foreign keys block both operations, including self references and
  references from other schemas. No cascading deletion is performed. Removing
  rows from a dependent table does not remove its foreign-key dependency.
- Partitions, inherited/foreign tables, extensions, row security, and custom
  triggers/rules require database-level maintenance. Dependent PostgreSQL views
  and applicable DDL event triggers block table deletion. SQLite table deletion
  is blocked while views are present because SQLite lacks a dependency catalog.
- Actions run transactionally with an audit event. An audit failure rolls back
  the operation. PostgreSQL rechecks dependencies under a table lock, requires
  read-committed isolation, waits up to 5 seconds for locks, and limits each
  maintenance statement to 30 seconds. Larger operations may require a DBA.
- Mutations support PostgreSQL and SQLite. Other database engines are read-only.

## API and validation

### Local server reload

The local Compose backend runs Gunicorn without automatic code reload. After
adding or changing backend endpoints, reload its workers so the running server
uses the updated routes:

```text
docker kill --signal=HUP radai_backend_local
```

The application route guard returns `401` for requests without authentication.
While disabled, an authenticated administrator receives `404` with the message
"Database cleaning is temporarily disabled." A generic route-not-found `404`
while enabled means the running backend needs the update or worker reload;
refreshing the frontend alone will not load it.

### Endpoints and tests

`GET /api/v1/rbac/admin/database/tables/` returns the table inventory and per-table
capabilities. `POST /api/v1/rbac/admin/database/tables/action/` accepts
`{"table":"public.example","action":"clear","confirmation":"public.example"}`;
use `"drop"` for table deletion. The server revalidates permission, confirmation,
table identity, and dependencies on every request.

No application schema migration is required. Isolated checks:

```text
python manage.py test apps.rbac.tests.tests_database_maintenance apps.rbac.tests.tests_console_telemetry --settings=config.settings_database_maintenance_test --noinput
```

From `frontend`, run the standalone mocked browser checks:
`node scripts/check-database-cleaning.mjs` and `node scripts/check-admin-console.mjs`.

`apps.rbac.tests.tests_database_maintenance_postgresql` provides the PostgreSQL
regressions. Run it with settings targeting a disposable local PostgreSQL database
and a test account able to create databases and event triggers. It skips on SQLite.
