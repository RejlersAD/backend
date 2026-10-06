# Sales opportunity document types and versions

Implemented locally for the 1 October 2026 request. Apply Sales migrations
`0018_document_versions` and `0019_document_classification` before loading the
new web/worker code. Verification and local activation status are recorded in
`../../docs/features/sales-document-classification-versions.md`.

The inline custom-tag extension requires `0020_document_custom_tag` before its
web/worker code. It adds an empty-by-default, document-wide text field without
changing existing document types, classification runs or file versions.

The intelligence-metadata extension requires `0021_document_intelligence_metadata`
and its database confidence-range guard in `0022_document_intelligence_confidence_range`.
Each completed run stores folder-safe tags, an integer 0-100 confidence,
recommended folder, bounded reasoning and search keywords. The existing
classification fields remain compatible; projections add `intelligence` with
`documentType`, `folder`, `tags`, `confidence`, `recommendedFolder`, `reasoning`
and `searchKeywords`. Global tags use current Opportunity/client facts. Missing
business-unit data is represented as empty and is never inferred.

## Ownership and compatibility

The six workspace categories and their custom folder tags are unchanged.
`OpportunityDocument` groups immutable private `OpportunityWorkspaceUpload`
revisions. A document UUID equals its original upload UUID. Existing
`radai-<upload UUID>` IDs continue to resolve the exact original bytes; they
never redirect a proposal-review binding to a newer revision.

Legacy uploads remain implicit version-one documents until an explicit mutation
materializes their group. GET and migrations perform no backfill, classification
or provider calls. New successful uploads atomically publish the head, record
audit and create their durable classification run after original-byte verification.
Folder listings/counts show current document heads. Revisions retain their own
filenames; the document's canonical name remains its original name.

Migration reversal is allowed only while the added evidence is absent. Once
document groups/revisions or classification/review/request records exist, reversal
refuses to erase them. Preserve the schema and compatible readers during recovery.

## API

All routes extend `/api/v1/sales/deals/{deal}/workspace/` and repeat current
record/category/file permissions. Metadata and commands use private/no-store
responses; protected bytes retain existing export and integrity checks.

- Workspace GET adds `type_catalog`: `{value,label,color}` options. Private file
  projections add `document_id`, `document_name`, `version`, `is_current`,
  `head_file_id`, `head_token`, `revision_note`, `can_upload_version` and
  `classification`. History rows expose exact immutable `file_id` values for
  existing metadata/preview/download routes.
- `POST folders/{key}/files/{file}/versions/upload/`: multipart `file`, UUID
  `upload_request_id`, current `expected_token`, optional `revision_note` (1000
  characters). Requires read/create/update. Explicit target and freshness prevent
  silent same-name replacement. Returns 201 for creation or 200 for exact retry.
- `GET folders/{key}/files/{file}/classification/`: current classification and
  type catalog. GET/HEAD require read access and never mutate legacy files.
- `POST .../classification/`: JSON `document_type` and/or `custom_tag`, integer
  `expected_revision`, UUID `request_id`, optional `reason` (1000 characters). Requires current
  read/update and the exact current document head. Saves a document-wide human
  correction and audit atomically; stale requests return 409.
  At least one editable field is required. Omitted fields are preserved: existing
  type-only clients cannot erase custom tags, and tag-only edits cannot confirm
  or replace document types. Custom tags are trimmed, NFC-normalized single-line
  text of at most 80 characters; controls and unpaired surrogates are rejected.
  Empty text clears only the custom tag. Type and tag changes share the human
  metadata revision and request identity. Their before/after values are audited.
- `POST .../classification/retry/`: JSON `expected_revision`, UUID `request_id`,
  optional `reason`. Also requires export because processing reads file contents.
  Queues durable work; exact retries have one effect. Pending work is not duplicated.

Classification fields distinguish `origin` (confirmed/rule/ai/unclassified), job
`status`, human metadata `revision`, `suggested_type`, evidence, safe diagnostics,
provider/model, exact source upload/hash, and edit/retry capabilities. Evidence
excerpts require current export permission. A confirmed document type is shared
across its revisions and identified as `confirmed_scope: document`; source-version
suggestions remain separate. Classification never submits, approves, awards or
rebinds a proposal. Existing SharePoint native history remains external/read-only
for management; new classification/version-write capabilities are for RADAI files.

Intelligence tags are limited to the supplied vocabulary for the file's current
Correspondence, Tender, Proposal, Internal, Submitted or Award folder. Exact
rule matches score 95, validated AI suggestions 80 and folder-only possibilities
60. Lower-confidence unsupported results require manual review. Content evidence
may recommend a different folder when it is stronger than folder context, but
classification never moves files automatically.

`classification.custom_tag` is independent metadata, always a string (empty when
unset). It does not change `label`, `document_type`, `origin` or badge color.
The tag survives new file versions and later automatic results. Tag text is not
sent to the AI provider. The inline AI action uses the existing retry endpoint:
rules first, with configured AI for ambiguous readable content; it neither forces
a provider call nor clears human type corrections or custom tags. A tag-only save
does not queue classification. Historical projections show the current shared
document tag and remain read-only.

Migration 0020 can be reversed only while every custom tag is empty. Populated
reversal refuses to discard labels; retain the schema and compatible code for
recovery. Empty-tag reversal preserves the pre-existing type, revision, audit,
request, job and file evidence. No historical labels are inferred or backfilled.

## Durable work and recovery

PostgreSQL folder-upload lookup locks only matching upload rows. Its optional
document/head join is read for current-version selection but is not included in
`FOR UPDATE`; this avoids locking the nullable side of that join while retaining
serialized same-name upload/version behavior.

Folder-upload retries resolve the existing request ledger before selecting a
current same-name document. An initial upload remains an initial-upload replay;
a folder-created revision retains its reserved predecessor and head token.
Casefold-equivalent names create new immutable versions rather than replacing
earlier bytes. Version numbers increase from the highest reserved version,
including deleted revisions, so deleting a head and uploading again never
reuses an existing identity or violates the document/version uniqueness guard.
PostgreSQL deletion locks the selected upload row, not its optional joined
document/head, before separately locking the document; this keeps the delete
and reupload path usable when that join is nullable.
Completed retries return the exact saved upload with HTTP 200, even after a later
head, without creating another revision, object or completion audit. Uncertain
attempts reconcile their original intent. Actor, folder, provider, filename and
content identity checks still apply; explicit version commands keep their
caller-supplied target/token/note validation.

`OpportunityDocumentClassificationRun` stores exact source identity, requested
actor, attempts, due time and lease. Celery Beat dispatches due SQL rows independently
of SharePoint readiness. Lost broker messages remain recoverable; stale worker
leases cannot overwrite a newer run. Automatic failed attempts are bounded to
three; authorized explicit retry starts another attempt cycle. The management
command `python manage.py process_document_classifications` can drain due work.

Rules use filenames and leading extracted text. Ambiguity can call the configured
Sales provider through the existing encrypted credential resolver and bounded
transport, with document-specific system instructions/schema. An AI suggestion
must cite an exact supplied excerpt; incoming/outgoing cannot be inferred solely
from sender domains. Provider readiness is configuration evidence, not proof of
live authentication. Missing/disabled/failed AI stays visible; files remain usable.

Workers recheck actor/record/export authority, source hash/head, storage mapping,
lease and provider configuration before publishing. User-confirmed types remain
authoritative while a run finishes and across subsequent revisions. Audit/job
effects commit together. Content/provider payloads and credentials are not logged.

Uncertain revision storage outcomes retain a pending reservation: retry the same
actor/request/file to reconcile it. Competing new requests return 409 while old
head/history stay readable. There is no destructive automated cleanup or invented
retention policy; unresolved outcomes need administrator reconciliation.

## Extraction limits

Classification uses bounded text: 32 MiB source, 16 MiB declared ZIP aggregate,
4 MiB selected XML, 4096 archive entries, 100000 XML nodes and 20000 characters.
PDF extraction reads up to 20 pages and declines encrypted/repaired or >500-page
documents. DOCX/XLSX/PPTX extraction reads passive XML text; calculations, macros,
images and external resources do not run. OCR is not added. Unsupported/scanned
or oversized sources retain filename classification/manual correction/download.

MSG uses pinned `olefile==0.47` for read-only top-level properties, bounded to
32 MiB source, 4096 entries, 1 MiB per selected property and 20000 output characters.
It handles Unicode, supported recorded legacy code pages, plain body and passive
HTML text. It does not extract attachments or decode RTF. Missing body/encoding
is explicit. See the [olefile API](https://olefile.readthedocs.io/en/latest/Howto.html)
and [Microsoft MSG property streams](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxmsg/08185828-e9e9-4ef2-bcd2-f6e69c00891b).
These are processing bounds, not upload/download restrictions.

## Inline custom-tag verification

The 0020 extension passed 27 classification regressions in the isolated SQLite
release harness, including six new custom-tag cases: independent type/tag edits,
normalization and validation, clear behavior, API saves, stale/retry identity,
late AI/new-version preservation, access denial and atomic audit failure. Both
new PostgreSQL cases passed with observed competing locks: a tag/type conflict
retains the first edit, and an identical tag retry records one audit.

`scripts/check_document_custom_tag_migration.py` independently verified actual
0020 DDL on synthetic PostgreSQL, old-field preservation across 15 models,
blank-tag reverse/reapply, populated reverse refusal, cleared-tag reversal and
length/null constraints. This verifies the additive migration, not the complete
historical migration chain. Sales migration drift, scoped fatal lint and
whitespace checks passed. Logs are under `backend/artifacts/` as
`document-custom-tag-sqlite.log`, `document-custom-tag-postgresql.log` and
`document-custom-tag-migration-drift.log`; local application activation is tracked
in the shared feature brief. No live client document was sent to an AI provider.
