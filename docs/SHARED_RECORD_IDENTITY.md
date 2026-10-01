# Shared client, project and employee records

Lifecycle roadmap order 1, implemented 1 October 2026. Canonical owners remain
`sales.Client`, `core.Project` and `hr_core.EmployeeMaster`. Existing text labels
are retained as source evidence. Linking does not create approvals, staffing,
costs, access grants or externally synchronized records.

## Entry points and source ownership

Frontend: Projects > Shared records (`/projects?view=shared-records`). New project
creation also offers a Client selector; new Project Control hour entry offers a
project-scoped Employee selector. Sales conversion sets its known canonical
client automatically. Existing records require explicit review.

| Source key | Reference written | Source authority |
| --- | --- | --- |
| `project_client` | `Project.client` | Project Control and project write authority; preserves Sales lineage |
| `planning_project` | Existing `PlanningProject.enterprise_project` | Planning source write and target project write; one workspace per enterprise project |
| `organizer_project` | `project_organizer.Project.enterprise_project` | Existing Organizer modifier and target project write |
| `schedule_resource` | `ScheduleResource.employee` | Planning source write, project-scoped employee and editable schedule resource |
| `approved_hours` | `ApprovedHourEntry.employee` | Project Control write and project write; recorded approval and cost evidence retained |
| `customer_invoice` | `CustomerInvoice.canonical_project/canonical_client` | Finance outgoing read/update and visible targets |
| `receivables_source` | `ReceivablesSourceIdentity` beside immutable source row | Finance outgoing read/update, active publication and visible targets |

Existing permanent client/project/employee links cannot be reassigned by generic
PATCH or this reconciliation command. Finance may review stale/partial mappings
against their current source basis. Generic role resources remain valid without
an employee and are shown as not applicable until explicitly reviewed as a
person. Frozen schedule evidence requires the existing revision process.

## API

All paths below are relative to `/api/v1/projects/`:

- `GET shared-records/?source_type=project_client&status=unlinked&search=&page=1&page_size=25`
  returns `{sources:[{key,label}], count, page, page_size, results:[record]}`.
  Sources are separately paginated. Status is `all`, `unlinked` (including needs
  review) or `linked`. Page size is at most 50; search at most 160 characters.
- `GET shared-records/{source_type}/{source_id}/` returns the current record and
  up to ten retained review reasons/timestamps.
- `GET shared-records/{source_type}/{source_id}/candidates/?kind=client|project|employee&search=`
  searches accessible canonical targets. No automatic match is performed.
- `GET shared-record-targets/?kind=client|project|employee&search=&project_id=`
  provides the same minimal selectors for creation. Employee lookup requires an
  accessible enterprise project. Candidate responses contain at most 20 results
  as `{id,code,label}` plus `has_more`.
- `POST shared-records/{source_type}/{source_id}/link/` accepts:

```json
{
  "request_id": "client-generated UUID",
  "expected_token": "exact signed token from the reviewed record",
  "reason": "Reviewed source evidence",
  "targets": {"client_id": "explicit canonical record ID"}
}
```

`targets` accepts only the source's supported `client_id`, `project_id` and/or
`employee_id`; reason is nonblank and at most 1,000 characters. Success is
`{record, replayed}`. Unknown fields, unsupported targets and inconsistent
project/client organizations are rejected. A record includes `source_type`,
string `id`, `reference`, `label`, `source_values`, minimal `links`,
`target_kinds`, `state`, `can_link`, `expected_token`, and optional `warning`.
State is `linked`, `unlinked`, `needs_review` or `not_applicable`.

The signed token binds actor, source, ID and the fingerprint of current source
and connection evidence and expires after one hour. It is not a permission
grant. Stale source, expired token, changed saved source or conflicting UUID
reuse returns 409. A same-content retry rechecks current source/target access
and the saved after-state before returning `replayed: true`.

## Scope, transactions and preservation

All routes require Project Control read; writes also require its update action
and current source write authority. Nonstaff targets require known same-org
ownership through existing scope queries. Employee selection reuses the existing
project employee eligibility policy; it exposes no private HR fields. Canonical
projections, including raw employee IDs on HTTP serializers, disappear when the
actor loses access. Original labels remain visible only through original source
authority. Responses use `Cache-Control: private, no-store`.

The command serializes actor request-key retries, locks parent projects before
dependent sources where applicable, reloads and rechecks the source, and commits
the reference plus `SharedRecordLinkCommand` together. The audit stores actor,
request key, before/after fingerprints and reason. Repeated delivery creates no
second link effect or audit. Generic metadata saves retain protected links;
mutable schedule calculations become draft when resource identity changes.

Finance preserves imported numbers, labels, amounts, payments and approvals.
Its source identity basis is exact and includes invoice/category/company/account
and project references. Changed basis requires review; projections cannot confirm
an old match. A new workbook publication needs fresh row review because no stable
cross-publication identity contract exists. The active snapshot is fixed for
each queue query and rechecked under lock when linking. Ambiguous physical/null
operational invoice IDs remain in the existing duplicate-reconciliation flow.

## Migrations, verification and recovery

Six additive migrations create nullable links and initially empty audit/mapping
tables: core 0014, invoice_tracker 0006, finance 0016, planning_intelligence 0049,
project_control 0011 and project_organizer 0003. No historical match is backfilled.
Organizer keeps its existing cross-domain `db_constraint=False` compatibility
pattern; the other new references use database constraints and protected deletion.

See [actual PostgreSQL migration evidence](SHARED_RECORD_MIGRATION_VERIFICATION.md)
for preservation checks and scope. Reverse migrations refuse to remove reviewed
references/evidence. Once used, recover by retaining the additive schema while
rolling back compatible application code. Do not bypass guards to delete history.

Functional and guarded-HTTP regressions live in `apps/core/tests/test_shared_records.py`,
`apps/finance/tests_shared_record_links.py`, and
`apps/planning_intelligence/tests/test_shared_record_identity.py`. The PostgreSQL
concurrency suite is `apps/core/tests/test_shared_records_postgresql.py`. It
observes actual lock waits between competing writers and same-command retries.
Use the disposable server/settings described in
`PROCUREMENT_CONCURRENCY_POSTGRESQL.md`; never point these tests at app data.
`config.settings_shared_record_migrations` supports migration drift checks for
the affected app registry. Final executed results and local rollout are in the
workspace `docs/features/shared-record-identity.md`.

This foundation does not reconcile every existing record, merge master-data
duplicates, write SharePoint, implement roadmap orders 2-7 or certify production
deployment. Unknown ownership remains unresolved rather than inferred.
