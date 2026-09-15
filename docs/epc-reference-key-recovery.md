# EPC migration recovery for restored procurement tables

## Failure

PostgreSQL can reject `project_control.0007_epc_foundation` with:

```text
there is no unique constraint matching given keys for referenced table "procurement_requisitions"
```

The new EPC relationship references the requisition UUID. A restored database
can contain the rows and applied migration records while missing the original
primary-key or unique index. A successful migration on another database does
not verify the restored database's physical constraints.

Railway runs migrations before starting the new backend. If that command fails,
the previous deployment can still answer health checks while new endpoints,
including File Server Replica, return 404.

## Repair included in the release

- The preflight in EPC migrations 0007 and 0008 checks the requisition and order
  ID keys before creating their new foreign keys.
- A PostgreSQL key that is already suitable for a foreign key is retained.
  Otherwise, the migration locks the table, validates its IDs, and adds the
  missing primary-key or unique constraint.
- Existing IDs and business records are preserved. Null or duplicate IDs stop
  the migration with a specific error; the repair does not choose replacement
  identities or remove rows.
- Migration 0009 performs the same verification for installations that already
  completed 0007 and 0008. Existing migration dependencies are unchanged.

## Deployment and verification

Deploy the backend containing this repair. The existing Railway pre-deploy
command applies it automatically:

```sh
python manage.py migrate --noinput --skip-checks
```

Verify against the intended deployed database, using that environment's shell:

```sh
python manage.py migrate --check --skip-checks
python manage.py showmigrations project_control file_replica --skip-checks
```

EPC migrations 0007, 0008, and 0009 and File Replica 0001 should be marked applied.
Then verify the backend health endpoint and load File Server Replica while
signed in as an administrator. The sources endpoint requires authentication;
an unauthenticated 401/403 is different from a missing route's 404.

If the preflight reports null or duplicate identities, reconcile those records
and their existing links before retrying. Do not fake the EPC migration or drop
its foreign keys to bypass the failure.

Connection configuration and office-connector synchronization are separate
from deploying the module. A restored API does not itself establish a new
connection to the office file share.
