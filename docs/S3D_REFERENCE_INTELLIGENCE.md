# S3D reference intelligence - Phase 2, 20 October 2026

Authority: I-06 and the user-supplied implementation plan, recorded in
`../../docs/features/s3d-reference-intelligence-release.md`. Existing upload,
preview, matching routes and legacy successful matching results are retained.
The original reference workflow left ingestion unchanged. The subsequent
[empty-extraction recovery](S3D_EXTRACTION_RECOVERY.md) repairs the Claude SDK
request and zero-result completion handling. Phase 3/4 continuation adds controlled
release and pilot-evidence workflows; see [the current release contract](S3D_CONTROLLED_RELEASE.md).
Business approval and actual target-import acceptance remain explicit gates.

## Additive contracts

All paths are under `/api/v1/spec-customization/` and require authentication.
Existing project ownership/admin and source-job access checks remain in force.
Identifiers are UUIDs; the integer IDs in the plan are illustrative.

| Route | Contract |
| --- | --- |
| `POST matching/upload/` | Existing multipart files plus optional `standard_edition`, `units`, `effective_date`, `review_status` (`draft`/`review`). Validates before writing files or changing activation; invalid structure returns 422 with `schema_errors`. Uploaded sets receive SHA-256 hashes and `schema_valid`. |
| `GET matching/sets/`, `GET matching/sets/{id}/` | Existing responses add hashes, aggregate `reference_hash`, schema/governance fields and freeze actor/time. |
| `POST matching-set-freeze/` | `{workbook_set_id}`. Checks parsing, structure and actual file hashes; locks a revision without conferring engineering approval. Replays return the original lock. |
| `POST matching-run/` | `{job_id, workbook_set_id?}`. Uses an explicit revision, existing job pin, or active project set. Requires completed extraction, same project, parsed/validated reference and matching actual file hashes. Atomically pins the job and upserts decisions. Changing an existing pin returns 409. |
| `GET matching-decisions/` | Optional `job`, `workbook_set`, `state` filters. Returns `{items, count}`; both reference and source access are required. |
| `POST matching-decision-review/` | `{decision_id, review_decision, final_commodity_code?, review_notes?}`. Actions: approved, rejected, remapped. Ambiguous approval requires explicit candidate selection. Remapping requires a code present in the pinned CAT workbook. Ineligible/invalid/unmatched states cannot be approved directly. |

Job serialization adds the nullable `reference_workbook_set` UUID. SPEC/CAT
downloads add `X-Reference-Set-ID` and `X-Reference-Hash` when pinned; bulk-load
worksheet columns are unchanged. Reviewed decisions now flow into supported SPEC/CAT rows through component
bindings in the shared preview/export builder. Headers alone remain traceability
evidence; the Phase 3 integration test checks actual generated workbook content.

Frozen or pinned sets reject API reparse/deletion with 409. New uploads create
new revisions rather than replace old ones. Ordinary model writes and admin
editing also protect locked reference contents/rules. Activation remains separate
from content immutability. Storage administrators/raw SQL remain privileged
operations; actual file hashes are checked before each matching run.

## Supported reference evidence

- Match uses the existing configured A/D mapping columns. Duplicate component
  mappings, missing mapped CAT sheets/columns, empty catalogs and missing codes
  are rejected before activation.
- Reference readers accept ordinary first-row headers and SmartPlant
  `Head`/`Start`/`End` worksheets, using storage streams rather than `.path`.
- SPEC must contain populated `PipeNominalDiameters.Npd`, or populated
  `ServiceLimits.NominalPipingDiameterFrom/To`, plus populated
  `MaterialsData.MaterialsGrade`. When `SpecName` exists, batch matching scopes
  diameter eligibility to the extracted class code. Header-only templates do
  not establish eligibility.
- SPEC eligibility precedes any CAT read. An excluded component is ineligible;
  missing/unsupported eligibility evidence is invalid_reference.
- CAT evaluates size, rating, material, end preparation and subtype. When no
  CommodityType column exists, subtype must be supported by the mapped catalog
  component name. Multiple viable rows remain ambiguous, including rows with
  the same code but differing other values. No first-row selection occurs.
- Scores count satisfied discriminators out of five. They are ranking aids,
  not calibrated probabilities.
- DN and NPS, and PN and class ratings, are not interchangeable. There is no
  inferred metric/imperial conversion, standards edition, pressure-to-rating
  conversion, material equivalence or end-connection alias policy. Numeric
  fractions are normalized without configured precision rounding.
- Extracted size intervals remain unmatched until engineering expands them
  into nominal sizes. The service does not choose a range endpoint as a part.
- Component names use an exact existing mapping of subtype/type or type;
  there is no fuzzy resolution. Criteria beyond the schema's bounded lengths
  stop the batch with 409 rather than truncate engineering identifiers.

The requested uniqueness key omits subtype/end connection. Conflicting source
rows under that key produce invalid_reference instead of silently combining
different criteria. Unchanged reruns preserve reviews. Changed evidence clears
the previous review. Existing successful ComponentMatchingResult records remain.

## Verification and gates

Use the documented isolated backend environment with `config.settings_release_test`.
Frontend browser command: `S3D_TEST_TIMEOUT_MS=240000 node
node_modules/@playwright/test/cli.js test --config=playwright.s3d.config.js`.
The dedicated configuration owns strict port 5187 and does not reuse a server.

`scripts/check_s3d_reference_intelligence.py --apply` asserts PostgreSQL,
local host and exact database `radai_dev`, permits only pending migration 0014,
then checks synthetic matching/pinning, ambiguity, listing, explicit review,
rerun uniqueness and freeze protection inside a rolled-back transaction.
Its normal mode verifies an already migrated database. In-memory storage keeps
the probe independent of S3/AI. No synthetic business rows are retained.

The workspace feature brief records final run counts and artifacts. SQLite is
application-behavior evidence; the PostgreSQL probe is not a concurrent race
test. Migration verification does not authorize deployment.

Phase 2 engineering acceptance remains open for an approved real reference pack,
its owner/approval authority, agreed units/standards and a representative piping
pilot. The reviewed-code preview/export bridge, persisted validation, provenance,
reproducible frozen files, release/export guards and import-report tracking are
now implemented and synthetically verified in the Phase 3/4 continuation. Real
engineering acceptance and an agreed disposable SP3D version/profile remain
required before claiming a successful target pilot. No accuracy percentages or import
readiness claims follow from these synthetic tests.
