# S3D project reference-key recovery — 14 October 2026

The reported deployment fails in `spec_customization.0011_projectcreationrequest`
because `spec_customization_specproject.project_id` has no eligible PostgreSQL
reference key. Migration 0003 already declares it as the primary key; the physical
schema differs from recorded model/migration state. The error does not establish
how that mismatch occurred.

Scope follows shared decision I-06 (preserve established records), the S3D feature
brief and its existing project-creation API contract. No API/model fields change.
The implementation reuses the existing EPC PostgreSQL reference-key recovery helper.

0011 now checks project and user reference keys before creating its deferred foreign
keys. Existing eligible keys are retained. Otherwise it locks the affected table,
rejects null/duplicate IDs, and restores a primary key (or a unique constraint if
another primary key exists). No rows or identifiers are rewritten. Repair locks
last until the atomic migration completes and can temporarily block table access.
0013 repeats verification for databases that already applied 0011/0012. Existing
migration dependencies are unchanged; a new predecessor would invalidate applied
histories. Reversal retains repaired keys, consistent with EPC recovery.

Verification: `scripts/check_spec_project_reference_keys.py` exercises actual
0011/0013 operations in an isolated PostgreSQL 15 container. Eight cases cover
missing project/user keys, healthy keys, another primary key, partial and deferrable
unique keys, duplicate IDs and null IDs. Original rows remain unchanged; repeated
verification succeeds and project foreign keys reject nonexistent IDs. This probes
the affected DDL, not the full historical migration chain or live deployment.

Deploy the updated backend and retry its existing Railway pre-deploy command:

```sh
python manage.py migrate --noinput --skip-checks
python manage.py migrate --check --skip-checks
python manage.py showmigrations spec_customization --skip-checks
```

0011, 0012 and 0013 must all be applied in the failing deployment environment.
Do not fake 0011 or disable foreign keys. If null/duplicate identities are reported,
reconcile those records and links explicitly before retrying.

Earlier checks on the configured Railway database found 0011/0012 applied and save
columns present. They did not verify the separate database in the supplied failure.
This fix has not been applied to that failing database as part of local validation.
