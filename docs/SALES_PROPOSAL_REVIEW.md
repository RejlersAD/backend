# Sales Proposal Preview and Commenting

Implemented and tested locally, 1 October 2026. This is internal review feedback,
separate from configured commercial approval and external submission. The source
is canonical `sales.Quote` plus an explicitly bound immutable private RADAI PDF
from the same opportunity's Proposal folder. Legacy `pdf_file_path` and arbitrary
URLs are never preview sources. No proposal status or approval changes are made
by these review commands; no approver/team policy is introduced.

## API contract

All paths below start `/api/v1/sales/quotes/{quote_id}/review/`.

`GET` accepts optional `document_id` and `comments_cursor` and returns:

```text
quote: {id, quote_number, version, status, updated_at, deal_id, deal_code,
        deal_name, client_name, prepared_by: {id,name}|null}
documents: [{id, revision, file_id, name, size, page_count, created_at,
             created_by: {id,name}|null, is_current, feedback_version}]
documents_count: integer
selected_document: same document shape | null
comments: [{id, parent_id, body, kind, page_number, anchor, context, is_resolved,
            resolved_at, resolved_by: {id,name}|null, created_at,
            author: {id,name}|null}]
next_comments_cursor: signed string | null
counts: {all, open, resolved}  # root threads, not replies
submissions: [{id, outcome, note, actor: {id,name}|null, created_at}]
submissions_count: integer
capabilities: {can_preview, can_download, can_bind, can_comment,
               can_resolve, can_submit, deny_reason}
```

Documents/submissions contain the newest 100 records and disclose their total
counts. A selected historical document outside that window is included as a
bounded 101st document. Any authorized historical document remains selectable by
its UUID. Comments are chronological flat one-level threads, paginated 100 rows, with `parent_id` linking
replies. The signed cursor is bound to quote, document and feedback version;
concurrent feedback changes require a fresh listing. The initial empty response
has empty arrays, zero counts and null selected document. No reads create rows.

| Method and suffix | Request/result |
| --- | --- |
| POST `documents/` | `{file_id: "radai-UUID", request_id: UUID, expected_document_id: UUID|null, expected_quote_updated_at: ISO timestamp}` |
| GET `documents/{document_id}/content/` | Authenticated PDF attachment bytes for the existing PDF.js Blob renderer |
| GET `documents/{document_id}/download/` | Authenticated original PDF attachment bytes |
| POST `documents/{document_id}/comments/` | `{body, kind: "comment"|"required_change", page_number: integer|null, anchor: object|null, context: string, parent_id: UUID|null, request_id: UUID, expected_version: integer}` |
| POST `documents/{document_id}/comments/{comment_id}/resolve/` | `{is_resolved: boolean, request_id: UUID, expected_version: integer}` |
| POST `documents/{document_id}/submit/` | `{outcome: "reviewed"|"request_changes", note: string, request_id: UUID, expected_version: integer}` |

Write responses contain `document_id`, `feedback_version`, `request_id`,
`replayed`, and `comment_id` or `submission_id` when applicable. Refresh GET after
success. Keep request UUID/input on uncertain retry; stale 409 responses require
explicit refresh/review before resubmitting. Expected version comes from the
selected document's `feedback_version`. An empty first binding explicitly uses
`expected_document_id: null`. Revision numbers describe review-document bindings,
not inferred families of sibling Quote rows.

Comments have a one-based `page_number`, optional human `context` (200 characters)
and optional `anchor: {rects: [{x,y,width,height}], quote}`. Up to 50 normalized
rectangles stay inside the displayed page; quote text is at most 2,000 characters,
body/note at most 4,000. Page-only comments may use an empty rects array. Replies
belong to the same document and root thread; replies to replies are refused.
The optional kind defaults to comment; required_change is reviewer-selected
feedback classification and creates no approval gate. Replies inherit root kind.

## Scope and durability

Every endpoint requires current Sales proposal read, opportunity read and Deal
visibility. Preview/download retain the existing opportunity export requirement;
read alone never exposes file bytes. Binding requires proposal create/update and
the private file's read/export guards. Comments require proposal create; resolve
and submit require proposal update. Capabilities expose these conditions.
Historical document revisions are readable but fresh feedback writes target only
the current revision; `can_comment`, `can_resolve` and `can_submit` are false with
a reason for historical selection. Binding is blocked once approval/submission
evidence exists or the proposal has a protected lifecycle status.

Each write serializes on Quote then its document. Actor/UUID/payload-bound replay
and feedback-version comparison prevent duplicate or stale effects. State,
durable command result and opportunity audit commit together. The bound upload
UUID/hash/size/name and actual parsed PDF page count are immutable. Reads verify
private content identity and current scope; no public storage URL is exposed.
Binding accepts an actual unencrypted, unrepaired PDF containing 1 to 500 pages,
validated by the existing PyMuPDF dependency. It has no separate 10 MiB review
ceiling: the upload service owns any configured file-size limit. Validation
copies already verified original bytes to a private temporary file in bounded
chunks and parses the disk path, restoring the caller's stream position and
removing temporary files on success or failure. A temporary disk failure returns
503 `pdf_validation_unavailable` without creating a review revision. It does not
convert Office documents or trust a filename/MIME label as proof of valid PDF
content. Compressed private storage is transparent to review: bound name, size,
SHA-256, preview and download all refer to the exact original PDF bytes.
Feedback evidence prevents destructive quote deletion or identity reassignment.

Sales migration `0014_proposal_review` adds document bindings, comments and
durable review command records. Apply it before activating the updated page/API;
reversal refuses while review evidence exists. No live file copying, SharePoint
writes, production deployment or external messages are part of this feature.

## Verification

The larger-upload follow-up passed 23 scoped SQLite cases (20 review API cases
and three disk-validation cases), without skips. It includes a real PDF larger
than 10 MiB that is losslessly compressed in private storage, bound as a review
revision and returned byte-for-byte through preview and download. Invalid and
repaired PDFs, temporary disk failure, bounded input reads, stream position and
temporary-file cleanup are covered. Evidence:
`artifacts/sales-proposal-large-pdf-tests.log`. This functional run does not
certify PostgreSQL concurrency or migrations; combined PostgreSQL verification
is recorded with the opportunity upload follow-up.

The earlier combined disposable PostgreSQL run passed all 105 tests with no skips
in 432.693 seconds: 20 proposal-review tests, 84 existing private/workspace/
document-explorer tests and the existing configured proposal-approval regression.
Evidence: `artifacts/sales-proposal-review-postgresql.log`. Tests use actual
temporary private PDFs and synthetic users; no live SharePoint or application
attachment was created. This includes PostgreSQL competing feedback writes,
scoped content and export denials, source integrity, validated page anchors,
required-change classification, replies/resolution, revision isolation,
idempotent/stale requests, late approval/revocation, audit rollback, chronological
pagination, mixed-read detection and protected proposal identity/deletion.

```powershell
$env:PYTHONUTF8 = '1'
$env:RADAI_CONCURRENCY_PG_PORT = '15451'
$env:RADAI_CONCURRENCY_PG_PASSWORD = 'synthetic-tests-only'
..\.venv\Scripts\python.exe manage.py test apps.sales.tests.test_proposal_review apps.sales.tests.test_private_attachments apps.sales.tests.test_opportunity_workspace apps.sales.tests.test_workspace_documents apps.sales.tests.testintake.ProposalApprovalTests --settings=config.settings_procurement_postgresql_test --noinput --verbosity 1
```

Use only an explicitly provisioned disposable PostgreSQL instance with these
test settings. Their model-sync harness does not validate the migration graph.

The initial isolated SQLite run passed 15 review cases with one PostgreSQL-only
concurrency case skipped (`artifacts/sales-proposal-review-tests.log`). Subsequent
tests add source identity, binding replay/stale checks, mixed projection detection
and a selected revision outside the recent-history window. New source/tests pass
F-code/fatal Python checks; existing changed source passes the repository's fatal
Python lint rules. These functional checks do not certify a migration graph.

The independent real PostgreSQL `0013` to `0014` DDL probe passed: existing Deal,
Quote and private upload rows preserved; empty reversal/reapplication; reversal
refused after document/comment/command evidence; actual unique constraints
verified (`artifacts/sales-proposal-review-migration.log`). This used synthetic
rows in a disposable database, not an application or production database.

The verified additive migration was then applied to the named local `radai_dev`
database and checked successfully: `0014_proposal_review` applied, no pending
migrations across all apps, consistent migration history, no Sales model drift,
review schema/unique constraint present and existing Quote/private attachment
counts unchanged. The local check inserted no review data. Evidence:
`artifacts/sales-proposal-review-local-migration.log`. Production was not migrated.
