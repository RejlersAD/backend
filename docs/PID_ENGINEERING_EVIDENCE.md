# P&ID engineering evidence linkage — Task 2

Authority: user Task 2 request, 12 November 2026, and the user's selection of
the live `apps.crs.CRSRevision` store. This is bounded evidence linkage under
the shared I-07/D-03 access gap; it does not create engineering approval.

The live CRS API uses `apps.crs.CRSDocument`, `CRSRevisionChain`, and
`CRSRevision`. The similarly named `apps.crs_documents.CRSDocumentVersion`
belongs to an inactive app with no migrations. `PlanDeliverable` is a planning
generation entry, not the authoritative execution work item. The link uses
`project_control.EPCWorkItem` and its existing canonical `core.Project` FK.

Migration `pid_checker_v2.0027_engineering_evidence_links` adds:

- `CRSChainProjectBinding`: one explicit canonical project per CRS revision
  chain, created only by the chain creator or a revision source uploader with
  project write and CRS read grants. Legacy free-text project names are not
  inferred or backfilled.
- `PidEngineeringEvidenceLink`: one link per extraction to canonical project,
  engineering-phase EPC work item, and exact live CRS revision. The revision's
  `document_id` is the source document. Existing extraction rows stay intact.

`GET /api/v1/pid-checker-v2/engineering-evidence/?project_id=<core.Project.id>`
lists links in the authorized project. `POST` to the same path accepts
`extraction_id`, `project_id`, `work_item_id`, and `revision_id`; it returns
201 for a new link, 200 for an identical retry, and 409 for a conflicting
association. `GET/PATCH /api/v1/pid-checker-v2/engineering-evidence/<extraction_id>/`
retrieves or updates the work item/revision; project reassignment is rejected.
The response contains the exact CRS document, chain, revision number and label,
work item, project, actor IDs, and timestamps.

The existing route guard supplies `pid_analysis.read/create/update`; the
service also checks `crs_documents.read`, current project membership/write
role, extraction ownership, matching EPC project/engineering phase, and the
chain binding. Row locks, one-to-one constraints, and one transaction cover
binding, link, and `rbac.AuditLog` creation. Identical retries do not duplicate
links or audit rows. No CRS status, work status, project progress, or baseline
is changed. Legacy extractions without a canonical link remain readable through
their existing API and are not assigned a project automatically.

Verification uses disposable SQLite model-sync tests and an isolated migration
application harness. The full historical migration chain cannot execute on
SQLite because `core.0004` contains PostgreSQL SQL; the harness fakes historical
state after model sync, then applies `0027` normally. Production migration and
PostgreSQL concurrency were not exercised.
