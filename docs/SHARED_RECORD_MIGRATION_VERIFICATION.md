# Shared-record additive migration verification

Verified on 1 October 2026 against the explicitly provisioned disposable
PostgreSQL container `radai-shared-records-test-20261001`, bound to
`127.0.0.1:15447`. The verification created its own database,
`shared_record_migration_verify`; it did not use the application database or the
separate concurrent-test database.

## Executed checks

`scripts/check_shared_record_migrations.py` completed with exit code 0. The script
first synchronized current models in an empty database, constructed the preceding
model state, and executed the actual reverse operations for the following six
migrations while the new identity fields and tables were empty. It then inserted
synthetic historical facts and executed the actual forward operations:

- `core.0014_shared_record_identity`
- `invoice_tracker.0006_canonical_invoice_references`
- `finance.0016_receivables_source_identity`
- `planning_intelligence.0049_resource_employee_identity`
- `project_control.0011_hour_employee_identity`
- `project_organizer.0003_enterprise_project_identity`

The checks established:

- Every original field across ten source record types retained the same hash,
  including original labels, dates, approval fields and decimal financial values.
- The seven new source fields initially contained only null references or the
  documented empty identity-basis object. The migrations performed no backfill.
- Eight new PostgreSQL foreign keys and the actor/request uniqueness constraint
  existed. Organizer's enterprise link deliberately retains its documented
  `db_constraint=False` compatibility boundary.
- Actual writes with a nonexistent employee reference or duplicate request key
  failed with a PostgreSQL integrity error.
- After creating synthetic reviewed links, all six real reverse migrations
  refused to remove the new schema. The transaction preserved the source facts,
  links and command evidence.

Final SQL inspection confirmed one retained command, one Finance source-row link,
one linked enterprise project, one named planning resource, one linked hour entry
and one linked Organizer workspace, with all six new schemas intact. After the
final 148-test PostgreSQL run passed, the explicitly labeled disposable container
and its temporary data were removed. Logs and this reproduction script remain;
application containers and databases were not removed.

## Reproduction and boundary

Provision the disposable PostgreSQL server according to the existing
`PROCUREMENT_CONCURRENCY_POSTGRESQL.md` isolation guidance. Set
`RADAI_CONCURRENCY_PG_PORT` and `RADAI_CONCURRENCY_PG_PASSWORD` to that server's
explicit synthetic configuration. Run from `backend/`:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:RADAI_SHARED_MIGRATION_DATABASE = 'shared_record_migration_verify_repeat'
..\.venv\Scripts\python.exe scripts/check_shared_record_migrations.py
```

The script only accepts a dedicated `shared_record_migration_verify` database
name, optionally followed by an underscore and lowercase alphanumeric suffix,
on loopback and a nondefault PostgreSQL port. It refuses populated databases and
never drops a database. Use a fresh name for another execution.

This is execution evidence for the six additive migrations against the preceding
schema derived from current models. It is not a replay or certification of the
repository's complete historical migration chain, an applied-migration ledger,
a production-data reconciliation, or a deployment. An earlier full SQLite
migration attempt encountered preexisting PostgreSQL-specific SQL in `core.0004`;
this focused PostgreSQL verification does not change that historical migration.

For recovery after reviewed links exist, retain the additive schema and evidence
while rolling back compatible application code. The reverse guards deliberately
prevent silent deletion of canonical references or their review history.
