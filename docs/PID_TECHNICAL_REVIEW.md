# P&ID technical review — Task 3

Authority: the user's Task 3 request of 12 November 2026. This extends the
Task 2 canonical project, engineering work item, extraction, and exact live
`CRSRevision` link under the shared I-07/D-03 access gap. It does not issue or
approve CRS documents, accept EPC work items, change progress, or alter a
baseline.

Migration `pid_checker_v2.0028` adds review state, assigned reviewer, submitter,
decision actor/times, validation snapshot, source fingerprint, and review round
to `PidEngineeringEvidenceLink`. `PidEvidenceReviewEvent` records each
validation, submission, comment, and decision; the existing `rbac.AuditLog`
receives a matching event in the same transaction. Existing links start in
`draft`; unlinked legacy extractions remain unlinked.

All paths below are under `/api/v1/pid-checker-v2/engineering-evidence/{extraction_id}/`:

| Endpoint | Action | State effect |
| --- | --- | --- |
| `GET review/` | `pid_analysis.read`, `crs_documents.read`, project access | Read review, validation, comments, source identity, current fingerprint status |
| `POST validate/` with `line_list_id` | `pid_analysis.update`, `crs_documents.read`, project write, extraction creator | `draft`/`returned` to `validated` on passing deterministic checks; otherwise `draft` |
| `POST submit/` | Same writer authority | `validated` to `under_review`; assign the EPC work item's reviewer |
| `POST comments/` with `note` | `pid_analysis.approve`, current assigned reviewer | Record an auditable comment |
| `POST decision/` with `decision` (`approve`, `return`, `reject`) and `note` | Same reviewer authority | `under_review` to `approved`, `returned`, or `rejected` |

The reviewer must retain project access, `pid_analysis.approve`,
`crs_documents.read`, the current EPC
assignment, and a Process Engineering HOD or engineering manager business
position. The reviewer cannot be the extraction creator, submitter, or EPC work
owner. Review submission fails if no independent eligible reviewer is assigned.
Returned evidence can be corrected, validated, and resubmitted as a new round;
rejection is terminal. Identical decision retries return the existing result
without duplicate events, subject to current reviewer authority.

Validation reuses the default line-list legend validator and line-list cross
check with AI disabled. A passing snapshot requires a readable CRS source file,
nonempty extracted tags and master rows, zero legend errors/warnings, and exact
cross-check coverage with no missing, extra, or mismatched line tags. Validation
records exact CRS revision/document IDs, source file SHA-256, and check results.
`line_list_id` accepts the upload API's public Line List UUID; legacy positive
integer primary keys remain accepted. Both resolve an active upload owned by
the requesting engineer. Task 5.2 adds the master Line List provenance gate:
the upload must carry an explicit `PidLineListProvenance` association to the
link's canonical project and to an approved, current CRS master revision (see
`PID_LINE_LIST_PROVENANCE.md`). Cross-project, unassociated, unapproved, or
stale masters are rejected before validation runs.
The fingerprint also includes the project/work identity, reviewer and work
state, extraction fields and tags, line-list rows, CRS revision/document/
chain metadata, and the master Line List provenance facts (canonical project,
master revision/document/chain identity, statuses, and timestamps).
Submission and approval recompute it. A changed source or
revision returns 409; an authorized reviewer can return it for correction.

All state transitions lock the link and source database rows and write the
review event plus `AuditLog` atomically. A linked extraction cannot be deleted
through the extraction API. Since Task 5.2, project-linked technical approval
requires the master Line List's explicit canonical-project and CRS-revision
association in addition to creator ownership and active status; legacy
uploads without an association remain usable for standalone checks but are
rejected here until explicitly linked. External changes
to file storage after a decision can make `evidence_current` false; they do not
silently revoke a recorded decision.

Local verification uses focused Task 1–3 tests on disposable SQLite. Migration
0028 is applied in the disposable migration harness after historical state is
faked because the historical `core.0004` migration contains PostgreSQL SQL.
PostgreSQL concurrent decision behavior and production migration are not run.
