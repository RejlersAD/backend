# Permanent project deletion

The Project Control **Delete project** action now requires explicit permanent
deletion instead of silently archiving its enterprise/planning records. This
change does not purge existing archives or execute any live deletion on rollout.

## HTTP contract

`DELETE /api/v1/projects/{id}/` requires a JSON body:

```json
{
  "permanent": true,
  "expected_updated_at": "<exact last-returned project updated_at>"
}
```

An older client showing archive wording has no permanent flag and receives 400.
Existing route/object visibility and active owner, assigned project manager or
authorized administrator checks still apply. A changed project returns 409.
No error falls back to archiving.

The legacy planning-workspace endpoint returns 409 for a linked enterprise
project and directs the user to Project Control. Standalone legacy workspaces
retain their existing archive command and are outside this change.

The transaction removes the eligible enterprise project and its ORM cascade,
including archived project-owned children and the linked planning workspace. It
records a surviving global `AuditLog` deletion entry and exact storage manifest
atomically. Shared users, clients, vendors, templates and independent business
records are not project-owned deletion targets.

Legal holds, active processing/delivery, linked procurement records and existing
protected/immutable business or planning history block deletion. This includes
PostgreSQL-protected evidence, baseline/build history and model-level immutable
records. The user decision about erasing protected planning history is still
pending; this implementation does not bypass those protections.

## Stored files and recovery

After the database commit, cleanup uses the recorded FileField keys, validates
the storage namespace and checks surviving references, including archived rows.
Shared bytes stay in place. Separate append-only file-cleanup audit events record
completed/failed work. There is no claim of atomic database-and-storage deletion
or automatic durable worker delivery.

204 means database removal and current-manifest cleanup completed. 202 with
`cleanup_pending: true`, `code: project_file_cleanup_pending` and `deletion_id`
means the project record is removed but file cleanup remains. The frontend
returns to portfolio and states that some files await deletion.

An operator can retry that already-authorized manifest with:

```powershell
python manage.py retry_project_file_cleanup --audit-id <deletion_id>
```

The command must not be used to start a new project deletion. It refuses an
uncommitted deletion or an audit whose project still exists. Retries recheck
storage and references and tolerate an already-absent object. Backup/provider
retention is outside this application deletion contract.

No schema change is required. PostgreSQL concurrency and migration application
must be distinguished from isolated SQLite functional tests. Verification is
recorded in the workspace feature brief.
