# Shared project access

The existing `module_team` visibility strategy shares Project Organizer records
only when the viewer and project owner both have an effective `read` grant for
the same configured collaboration module. The central
`apps.rbac.action_policy.module_action_allowed` policy is authoritative: module
navigation visibility, a cached menu entry, or the globally visible Process
Datasheet module alone does not grant shared record access. Explicit read denials
and role revocations are checked on the request.

The existing owner/admin access remains in place. Team sharing adds project and
activity read access; project PATCH/DELETE and direct activity POST remain
owner/admin operations. A denied shared record is omitted from the project list
and returns HTTP 403 for direct project/activity access.

HMB uses the same shared-project check in addition to its endpoint guards. A
failure to resolve that check denies shared access instead of falling back to
menu visibility. Existing HMB action guards and owner/admin paths are unchanged.
No module grants, roles, sharing configuration defaults or schema are added.

## Focused verification

- `apps.project_organizer.tests` exercises effective grants, explicit denial,
  globally visible modules without grants, nonmatching/shared alternative
  modules, role revocation, inactive profiles, owner strategy, permission lookup
  failure, shared read versus writes, and existing owner/admin writes.
- `apps.process_datasheet.tests.test_hmb_template_access` adds shared-template
  grant, denial and lookup-failure cases to the existing record-access checks.
- Project tests use `config.settings_release_test` with isolated SQLite.
  HMB access/storage tests require the Process Datasheet app registry and an
  isolated PostgreSQL database because the app contains PostgreSQL array fields.
  Model-sync tests do not certify applied migrations.
