# EPC migration recovery for restored reference tables

## Failure

PostgreSQL can reject `project_control.0007_epc_foundation` with:

```text
there is no unique constraint matching given keys for referenced table "procurement_requisitions"
```

It can also apply 0007 successfully, then reject `project_control.0008_epc_execution` with:

```text
there is no unique constraint matching given keys for referenced table "core_projectmilestone"
```

The new EPC relationships reference the requisition UUID and the milestone ID,
respectively. A restored database can contain the rows and applied migration
records while missing the original primary-key or unique index. The milestone
ID is already a primary key in `core.0001_initial`; the error means the physical
table no longer has a suitable reference key. A successful migration on another
database does not verify the restored database's physical constraints.

Railway runs migrations before starting the new backend. If that command fails,
the previous deployment can still answer health checks while new endpoints,
including File Server Replica, return 404.

## Repair included in the release

- The preflight in EPC migration 0007 checks requisition and order ID keys.
  Migration 0008 checks every existing target of its new foreign keys: users,
  projects, milestones, schedule activities and control/progress records,
  integrated baselines, project documents, WBS nodes, and purchase orders.
  It also retains the requisition check for previously applied 0007 histories.
- A PostgreSQL key that is already suitable for a foreign key is retained.
  Otherwise, the migration locks the table, validates its IDs, and adds the
  missing primary-key or unique constraint.
- Existing IDs and business records are preserved. Null or duplicate IDs stop
  the migration with a specific error; the repair does not choose replacement
  identities or remove rows.
- Migration 0009 verifies procurement keys for installations that already
  completed 0007 and 0008. Migration 0010 verifies the execution targets for
  installations already through 0009. Existing migration dependencies are
  unchanged, so applied histories remain valid.
- The repair runs before 0008 creates tables and deferred foreign keys, since
  a later migration alone cannot unblock a failure inside 0008. Table locks
  remain held until the atomic migration completes. Repeated checks reuse
  eligible existing keys.

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

EPC migrations 0007 through 0010 and File Replica 0001 should be marked applied.
Then verify the backend health endpoint and load File Server Replica while
signed in as an administrator. The sources endpoint requires authentication;
an unauthenticated 401/403 is different from a missing route's 404.

If the preflight reports null or duplicate identities, reconcile those records
and their existing links before retrying. Do not fake the EPC migration or drop
its foreign keys to bypass the failure.

Connection configuration and office-connector synchronization are separate
from deploying the module. A restored API does not itself establish a new
connection to the office file share.
