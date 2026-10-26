# Local directory schema recovery

Scope and authority: workspace `../../docs/features/rbac-local-schema-recovery.md`.

Migration `0067_role_assignment_windows_and_delegation` supplies schema already
referenced by the running RBAC model/query implementation:

- Nullable `UserRole.effective_from` and `expires_at`, with their composite index.
- The `rbac_delegations` table, its foreign keys/indexes and unique
  `(delegator, delegatee, role)` constraint.

It contains no data migrations. All existing assignment dates remain NULL, so
the existing assignments retain immediate/no-expiry behavior. No delegation,
role, permission or module grant is created. This does not authorize the broader
pending RBAC policy changes or migrate unrelated field/row-permission definitions.

The verified local database is PostgreSQL `radai_dev` on `postgres_local`.
Only this forward migration should be in its recovery plan; stop if prerequisites
would apply unrelated migrations. Application is transactional. A short lock
timeout makes a blocked ALTER fail rather than wait indefinitely.

The disposable verification database contains a schema-only copy plus migration
history and synthetic test identities. It exercises forward/reverse/reapply,
preserved assignment identity/primary flag, NULL date bounds, empty delegations
and uniqueness enforcement. Never use reverse migration as a live recovery
without reviewing subsequently created delegation/window data: reversing deletes
that new structure. The local activation compares assignment/grant fingerprints
before and after. HTTP checks use the real administrator through the local Vite
proxy; ephemeral authorization stays in process and is never logged.

Local activation passed on 26 October 2026. Assignment/grant fingerprints were
unchanged (703/18,667/5,001 rows respectively). All six directory/profile read
endpoints returned HTTP 200 through the Vite proxy; anonymous directory access
returned 401. The full requesting administrator profile includes its actual
superuser flag, super_admin role and modules. See the workspace brief for exact
endpoint counts, artifact paths and verification limits.

## Deployed missing reference key - 26 October 2026

The subsequent container log fails at 0067 deferred FK creation with PostgreSQL
`no unique constraint matching given keys for referenced table "rbac_roles"`.
The startup database-access warning is separate. The earlier healthy local audit
verified migration history/columns but did not reproduce this missing-key state;
it was explicitly not production verification.

0067 now calls the existing 0053 reference-key repair before creating delegation
foreign keys. It checks Role, UserProfile and the configured auth user, reuses
eligible immediate/unconditional unique keys, and locks/validates before adding
a missing PK (or UNIQUE if another column already has the PK). Duplicate/null
IDs stop with a clear error; nothing deletes, merges or reassigns identities.
0069 repeats this idempotent verification for already-applied 0067/0068 installs.
Neither migration changes model state, role grants or assignment values. Reverse
does not remove repaired reference keys, matching the existing repair convention.

All 32 real PostgreSQL key-migration tests pass, including eight new delegation
tests with missing-role reproduction, all 0067/0068 deferred FKs, healthy/alternate/
partial/deferrable/composite indexes, duplicates/nulls on all three targets,
idempotent follow-up, assignment/grant preservation and reverse DDL. Fixtures use
transactional synthetic schemas; all test data/DDL rolls back. Logs remain in the
workspace artifacts/rbac-delegation-key-*. No production credentials or data were
read by this verification.

The healthy local installation applied 0069 successfully: 657 applied migrations,
none pending, no model drift, and identical eligible indexes, roles, assignments,
permission/module grants and delegations before/after. This is local evidence only.

Deploy the fixed backend revision through the normal release process; startup
will retry the previously rolled-back 0067. Do not fake it or disable foreign keys.
If identity validation stops the migration, reconcile the reported duplicate/null
identities explicitly; this repair intentionally will not infer those decisions.
Production migration success remains unverified until its deployment completes.
