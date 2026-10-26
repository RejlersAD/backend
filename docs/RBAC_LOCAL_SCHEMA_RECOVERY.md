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
