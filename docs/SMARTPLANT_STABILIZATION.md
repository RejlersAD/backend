# SmartPlant stabilization and intake retries — 13 October 2026

Implements the first bounded slice of the user-authorized three-pillar sequence.
The cross-repository plan is `../../docs/features/smartplant-three-pillar-analysis-plan.md`.

## Project creation retries

`POST /api/v1/spec-customization/projects/` accepts an optional
`creation_request_id` UUID. Existing callers without it keep previous behavior.
New automatic-project UI submissions reuse the UUID for the same selected File.

- First request: create project and durable ProjectCreationRequest; HTTP 201.
- Same actor, UUID and sanitized payload: return the original project; HTTP 200.
- Same actor/UUID with changed payload, or original project deleted: HTTP 409.
- Invalid UUID: HTTP 400. Another actor's identical UUID is independent.

The request record stores the actor, UUID, payload hash and project link. Owner
row locking and an atomic transaction serialize creation; a database unique
constraint additionally protects actor/request identity. It does not provide
full upload/job idempotency, cross-browser file identity or canonical project
merging. Those remain later intake work.

Migration `0011_projectcreationrequest` is additive and must precede deploying
the backend request branch. It was applied to verified local PostgreSQL
`postgres_local/radai_dev`; production was not migrated. Existing request-less
clients remain compatible. No old records are rewritten or backfilled.

## Matching

Parser and matcher now import models from the correct parent package. Model
registration includes project and matching models for reliable migration state.
Workbook upload, parsing, sheet counting and catalog reads use the Django storage
API rather than FileField.path. Tests use storage that explicitly rejects local
paths; live S3 credentials/storage were not exercised.

Existing match response fields remain. Additive fields: `status`,
`candidate_count`, and bounded `candidate_commodity_codes` for ambiguity.
Distinct multiple catalog rows return `matched=false`, `status=ambiguous` and
do not persist a successful result. Missing required material columns return
`invalid_reference`; blank commodity codes are not successful matches. A unique
candidate retains legacy rule-based scoring; score 1.0 is not a calibrated AI
correctness probability. SPEC eligibility, units-aware matching and reference
release/version pinning are not enabled by this slice.

Upload and activation run transactionally and lock the owning project before
changing active sets. Sequential behavior is tested; PostgreSQL concurrent-race
testing and a dedicated immutable reference-revision model remain later work.

## Processing completeness and export

Job detail/list serializers add `extraction_completeness` with state, failed chunk
page ranges, attempted chunk counts and processing-report availability. Workers
persist their failed chunk ranges in config_snapshot.extraction_outcome; partial
results remain reviewable. Job `completed` means processing finished, not that
engineering correctness or release approval is established. Old jobs without a
report are `unknown`; legacy completed-job draft export remains compatible.

SPEC/CAT export returns HTTP 409 for unfinished jobs or known partial processing
(failed/missing chunks). Preview labels partial results and disables those two
bulk-load buttons. XLSX/JSON review output remains available under its existing
behavior. These checks are not an engineering release workflow or a target
SmartPlant import certificate.

## Verification and remaining gates

Isolated SQLite suites cover parser/storage behavior, ambiguity, workbook upload,
activation, owner denial, finished/partial export, failed worker chunks and
project creation replay/conflicts/deletion/actor scope. No live AI calls or
external business notifications are issued by fixtures. Migration state is
checked with the release app registry and migrations restored in the dry-run
command. Browser checks preserve the two-screen flow and unchanged shared shell.

Executed 39 backend regressions (isolated SQLite), seven browser checks,
targeted frontend lint and production/PWA build successfully. Spec Customization
migration dry-run with the complete release-test app registry reports no changes.
Migration 0011 applied successfully on verified local PostgreSQL.
The local web service was gracefully reloaded and the local Celery worker was
restarted after inspecting its active-task count (zero); production services
were not changed.
SQLite tests do not certify PostgreSQL concurrent locking; an actual S3 round-trip and target
SmartPlant import remain untested. Frontend lint retains existing extractor prop
validation warnings, and build retains existing bundle/dependency-age warnings.

Before implementing approved reference eligibility/release, obtain the approved
reference pack, target SmartPlant version/profile and engineering release
authority. Do not infer these from bundled templates, first catalog matches,
class confidence or cell-level approval timestamps.

## ADNOC sample extraction diagnosis — 13 October 2026

The supplied P12531-30-99-12-0601-2.pdf has 587 pages and readable native
text. Its SHA-256 matches the user's existing local queued upload. Worker logs
showed that the job lookup returned not found and ended without results; both
services currently address the same local PostgreSQL database. The exact reason
for the original visibility failure is unproven (ATOMIC_REQUESTS is disabled).
As a bounded reliability correction, asynchronous dispatch now waits for commit,
missing-job lookups retry, and broker/storage/processing failures become visible
terminal states. Broker failure no longer falls back to a long inline extraction.

Recovery exposed Gemini model 404 and OpenAI credential 401 responses. The
hard-coded gemini-2.0-flash is retired according to Google's official
deprecation schedule. The default is now gemini-3.6-flash, configurable via
SPEC_GEMINI_MODEL; SPEC_OPENAI_MODEL overrides the retained gpt-4o default.
See https://ai.google.dev/gemini-api/docs/deprecations/.
The latest local registry has Gemini enabled with no selected credential.
Model changes do not register or repair provider secrets.

Missing/rejected AI credentials or models now stop structured extraction with
an actionable error rather than repeatedly retrying every chunk or reporting
header-only OCR output as a successful component extraction. No credential,
permission, sidebar, header, or engineering-release authority was changed.
The original recovery was stopped after four chunks when it demonstrated the
provider configuration failure. A subsequent bounded recovery checks the new
terminal failure path: the real job now reports failed with the actionable
missing/rejected credentials or model message, rather than remaining queued.
Forty-five isolated backend regression tests pass;
the sample's full component extraction and SmartPlant import remain unverified
until working provider configuration is available.

## Appendix-A pipe class summary detection

The extraction worker now checks for `APPENDIX-A: PIPE CLASS SUMMARY` in the
vicinity of PDF page 29 before falling back to locating that heading elsewhere.
It parses the summary's multiline `Pipe Class / Basic Mate. / ASME Class` header
and class-code rows across subsequent pages. If no table rows match, a
six-digit/hyphen/letter regex finds codes on those appendix pages. These
source-page-backed, low-confidence class-only records remain reviewable after
the existing quality pipeline; they do not supply component data, approve
engineering correctness, or replace the configured Vision extraction path.
Existing chunk processing still runs to collect component details, and
component-rich results take precedence when merging the same class code.

For zero persisted classes, worker warnings give the job ID and exact PDF page
numbers visited. Raw text excerpts (up to 2048 characters per visited page)
are emitted only when DEBUG logging is explicitly enabled for the extraction
service: source PDF text may be sensitive and is not logged by default.
Missing/rejected structured-AI configuration still produces the established
actionable failure; a detected summary does not make a component extraction
successful. Validation uses synthetic appendix PDFs, not the supplied ADNOC
document, whose full end-to-end extraction remains unverified.

## Save and history for S3D uploads

`auto_project=1` on multipart uploads, or boolean `auto_project=true` on
presigned completion, asks the server to assign a project based on the
uploaded file's SHA-256 after validation. A per-user database row lock
serializes folder resolution and document creation; only projects with
S3D-upload provenance created by that user are reused. An identical file
under a new name reuses its folder, while changed bytes/new owners get
different folders. Every extraction gets a fresh document/job; manually
selected project uploads retain their prior contract and deduplication
policy. Project updated_at advances on another run.

Migration 0012 adds nullable job `saved_at` and `saved_by`. Explicit POST
`paper-spec/jobs/<id>/save/` requires owner/admin project access, a completed
run and at least one extracted class; replay is idempotent. It does not
replace previous jobs or approve the resulting workbooks. Project job
history exposes the actor/time and identifies the last-saved run as the
current saved result. Install migration 0012 before activating the new UI
or save route. Verified with isolated model-sync tests; not applied to a
production or local PostgreSQL database as part of this change.
