# Document-driven planning

RADAI's document planning path extracts source facts before assembling a draft.
Example files are regression fixtures, not project templates or production rules.

## Evidence flow

Project Control's Schedule view opens the retained Master Schedule with its
internal Performance tab. The Document Intelligence mode exposes the eleven-step
document workflow. As explicitly requested on 25 September (I-29e), completing
analysis or choosing Open schedule workspace now invokes the separate
`planning_package` generation mode and opens its exact editable relational
version. That mode expands eligible source deliverables with configured workflow
stages and disclosed estimated durations/calendar/technical sequencing. It does
not turn those proposals into literal source evidence or baseline approval.
Unchanged-input retries reuse the durable job and generation. The package owns
its calendar/date context without rewriting earlier schedule inputs. Metadata
corrections do not require paid reanalysis of unchanged files; deleted or changed
documents do require a current saved analysis.

The default `document_driven` mode and explicit source-review routes retain the
following literal-evidence flow. Their missing timings/links are not filled by
the proposal generator. Source-confirmation, assurance and baseline commands
remain separate; generation does not execute them.

1. Parse the uploaded file and record page/sheet coverage, skipped units, parser
   failures, truncation and unsupported content. Preserve source text and locators.
2. Recover activity tables by their explicit column labels. Supported adapters
   include delimited tables, Markdown tables, register rows and printed schedule
   text. Column order, filenames and project vocabulary do not determine logic.
3. Preserve exact titles, native IDs, printed values, units and per-field evidence.
   AI claims require a project-owned source, matching quote and matching value;
   a valid quote still requires human semantic review.
4. Assemble an evidence draft. Unspecified values remain null and are displayed
   as **Not Specified**. Preserve separately any register rows that have no explicit
   association with schedule activities. Similar names do not establish a link.
5. Review facts and missing information. Only uniquely resolved predecessor IDs
   with explicit relationship type and lag form source relationships. Missing
   relationship fields are not converted into finish-to-start, zero-lag links.
6. Calculate only after the calendar and network are verified. Printed source
   dates/durations and calculated dates/float are different kinds of information.

Captioned PDF deliverable tables with a serial/description header also support
numbered whitespace rows. Literal source groups and repeated serial numbers
retain their own locators; group labels do not establish canonical disciplines.
Clear titles can supply evidence-only activity proposals. Interleaved or wrapped
titles with uncertain boundaries remain register inventory, with a blocking
review issue, and cannot become activities through bulk preview confirmation or
the simple/WBS path. Adjacent notes and software/reference appendices are excluded.
Missing duration, dates, calendar and relationships remain unspecified.

Explicitly captioned applicability matrices also retain hierarchical serials
when the adjacent header identifies discipline, deliverable description,
package/applicability and remarks columns. A complete single-line title with
literal applicability marks may supply a proposal. Unmarked, conditional and
interleaved rows remain register inventory requiring source review. Flattened
text cannot locate the marks within package columns, so no package assignment
is inferred. Source remarks, marks and applicability status remain attached to
the evidence. Bulk fact/preview confirmation does not bypass these guards.
The `6.2-applicability-registers` engine invalidates older extraction caches;
existing saved analyses are preserved and require an explicit fresh analysis.

Client fields require a colon or a spaced hyphen delimiter. Hyphenated phrases
such as `COMPANY-approved` are not client names. Actual conflicting labeled
client values and workspace values continue to produce reviewable conflicts.

## Source dates in the schedule

Structured schedule tables also accept module-name/combined module-ID-and-name
headings and explicit target completion/finish date headings. A combined title
remains verbatim; it does not invent a separate activity ID. Target finish dates
remain source evidence, not inferred constraints or calculated finish dates.
Module summaries without explicit schedule fields do not become activities.

An MDR/EDDR upload category is a parsing hint. When neither register tasks nor
register inventory are recovered, explicit source activity tables may supply the
draft. Actual register scope and exclusions retain their existing precedence.

The planning read model exposes matched source start and finish dates separately
from planned and calculated dates. Each endpoint keeps its extraction status and
source references. Exact source dates remain visible in the activity table and
Gantt even when a duration, calendar or dependency network is unverified. They are
labelled **Source dates** and do not supply calculated float or critical status.

**Not Specified** means no usable source value is matched to that field;
**Not calculated** identifies pending schedule calculation; **Review source**
identifies ambiguous, conflicting or invalid date evidence. These labels do not
claim that a missing value is absent from every uploaded document.

Ordinary bars require both endpoints; a single source date never supplies the
other endpoint. A deliverable summary uses its own source evidence, without
deriving dates or duration from child source dates. These display fields are
read-only and do not rewrite project dates, activities or stored planning state.

## Current safeguards

- The document generation pipeline does not invoke catalogue activity templates,
  fixed stage durations, inferred work-package sequencing or default manpower.
- Build Schedule defaults to preserving source activities. A previously selected
  five-stage workflow remains user-configured rather than document-detected.
- Deliverable names do not automatically create phases, scenarios, dependencies,
  recurring instances or approval gates.
- A single project container is administrative grouping, not a claimed source WBS.
- Incomplete evidence produces a reviewable generation without materializing a
  calculated schedule version or publishing a baseline.
- Project dates, existing assignments and published baselines are not rewritten
  merely because this extraction policy changes.
- Parser coverage and AI chunk coverage are reported separately from semantic
  understanding. Unknown legacy coverage is never shown as fully verified.

The retained Master Schedule can display a saved generation's evidence workspace
without a relational version. It reads exact scoped generation detail, including
all activities and source gaps, and offers existing source review, immutable draft
editing and work-breakdown routes. This read does not materialize or approve it.
The materialize command's HTTP 200 `needs_evidence_review` response carries null
version/calculation IDs and `materialization_issues`; clients must not report an
upgrade from the HTTP status alone. A real upgrade selects the returned scoped
schedule version. An evidence selection cannot borrow an older version's export
or calculated header values.

A succeeded analysis job can contain failed or partial AI processing. The
document workspace displays that distinction, retains extracted facts, and
offers the existing AI settings and explicit retry actions. New failed chunks
record only safe, allowlisted provider codes/status and static recovery guidance;
historical failures without diagnostics remain unexplained in their saved data.
An authentication error requires correcting the key in the existing secure
settings dialog. Source fact counts are not activity counts.

Actual AI-settings changes advance the project revision used by analysis cache
keys; unchanged saves do not. Preview/generation fingerprints also include the
extractor version, preventing reuse of a completed preview from an older parser.
These changes preserve prior runs and do not silently reanalyze live projects.

Document analysis reports source extraction, AI chunk processing, persistence
and planning-basis progress through its existing job. Provider progress contains
only counts and status, never source text, response text or credentials. Stage
percentages are not evidence coverage. Anthropic document analysis streams its
response with the existing 45-second network inactivity timeout and no hidden
SDK retries. A terminal stream event is required; an interrupted response is a
failed chunk, while deterministic source evidence remains available. Streaming
does not impose a total-duration guarantee or prove semantic completeness.

For document extraction only, the verified `claude-opus-5` and `claude-sonnet-5`
requests explicitly disable thinking at `high` effort. These models otherwise
enable thinking by default, and it shares the bounded output-token budget with
the answer. The configured output cap, model and credentials do not change.
The policy is an exact model allowlist, not a rule for future models. Connection
tests, narrative requests and other providers keep their existing request form.
See Anthropic's [thinking configuration](https://platform.claude.com/docs/en/build-with-claude/thinking)
and [output cost controls](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost).

An empty response ending with `max_tokens` or `model_context_window_exceeded`
reports `output_limit`, not a credential error. Its safe metadata contains only
the recognized stop reason and integer input/output/maximum token counts. The
same cause is retained when a nonempty capped response contains truncated JSON.
Parseable capped JSON remains partial and is never checkpointed as complete.
No raw response, thinking content or credential enters diagnostics. A successful
short connection test establishes current access, not full-document completion.
The `analyze-v6-bounded-extraction` job fingerprint prevents an older completed
streaming failure from masking a fresh explicit analysis; prior runs remain.

Verification: 48 isolated provider tests passed, including 11 new request-policy,
real-SDK/mock-transport, limit, partial-output and cache-key checks. The 11 new
checks also passed against worker SDK 1.0.0. No paid provider call was used.

Explicit analysis retry resumes unfinished chunks through the existing scoped
continuation command. Completed checkpoints are preserved; changed source or
settings identity requires fresh analysis. The analysis job fingerprint includes
the updated transport identity without rewriting historical runs. The workspace
labels previous saved findings during analysis, applies a restored job's terminal
result once for the current project, and offers a GET-only status reconnection
after a transient monitoring failure. Reading job status never starts AI work.

## Bounded adaptive source analysis

Large inputs can produce more quoted facts than one provider response permits.
The document pass now starts with smaller literal source ranges and bisects only
responses that explicitly reach their output/context limit. Every parent and
child request consumes the same finite pass budget. Authentication, timeout,
connection and malformed-response failures without an explicit limit do not
trigger a split or automatic retry. Providers remain sequential.

| Configuration | Default | Meaning |
| --- | --- | --- |
| `PLANNING_CLAUDE_MAX_INPUT_CHARS` | 6,000 | Maximum initial source range |
| `PLANNING_CLAUDE_INTELLIGENCE_MAX_TOKENS` | 6,000 | Existing output cap per request |
| `PLANNING_AI_MAX_CHUNKS` | 64 | Total provider calls per explicit pass, including limited parents |
| `PLANNING_AI_MIN_CHUNK_CHARS` | 750 | Minimum child source range |
| `PLANNING_AI_MAX_SPLIT_DEPTH` | 3 | Maximum adaptive bisections along a range |

An explicit configured call limit, including 16, remains authoritative. The
larger default call budget allows a long document to use smaller requests; it
does increase the maximum total output allowance per pass. Splits can still
consume that budget before the full source is processed. Unfinished ranges are
reported as skipped with explicit continuation available, never as complete.
Terminal limited ranges remain partial/failed and can be retried explicitly;
they are not automatically resent within the same pass.

Split boundaries prefer nearby source newlines, preserve every source character
exactly once and retain document-relative offsets. Checkpoints save deterministic
split keys, completed leaf responses and valid partial parent/leaf responses.
Continuation reconstructs the same source-bound tree, reuses completed leaves,
preserves partial facts after a later failure, and never resends a split parent.
An identical unreviewed assertion can gain a quoted discipline from a completed
child response when the partial assertion had none. Different nonempty
disciplines or quote boundaries remain separate review claims.
Malformed trees or changed source/model/schema/strategy/output constraints cannot
rebind an old response to a new range. Coverage counts only the final leaf
partition; prior split attempts remain represented by call/split counts. Stage
progress uses finished source characters so changing leaf counts cannot move it
backwards. This progress still does not certify semantic completeness.

The prompt requests compact evidence JSON, avoids repeating generic requirement
lines already retained by the deterministic parser, and preserves additional
structured and multiline assertions. Unique exact quotes no longer require a
model-generated character count: the unchanged validator locates them itself.
Repeated quotes still need an exact local offset or a longer unique quotation.
Incorrect offsets, unsupported values and absent quotes remain rejected.

`analyze-v7-adaptive-chunks` and the configured chunk-policy values identify new
jobs; `adaptive-source-chunks/1.0` identifies compatible checkpoints. Existing
runs are immutable. The separate engine/source guards still govern continuation.
On the affected document's dense 6,000-character register slice, an authorized
real-provider verification returned a complete response with 38 literal-validated
deliverables; 17 unsupported claims remained rejected. That verifies this slice,
not completion or approval of the entire document. Full-run verification is a
separate explicit operation.

## Deliberate limitations

This implementation does not claim complete understanding of arbitrary files.
Images, diagrams, unsupported native formats, ambiguous tables and unprocessed
chunks remain review gaps. The generic evidence draft currently requires calendar
and network verification before calculation; it does not reconstruct a native CPM
calendar from printed dates. Large files are processed with bounded resource
budgets, and any cap is reported as partial coverage instead of silent completion.

No synthetic stages, guessed dates or invented dependencies may be used to fill
those gaps. Additional format adapters must keep the same evidence contract and
include tests with unrelated project types, ambiguous values and malformed input.

## Verification

Generation-preview jobs are dispatched only after the enclosing request
transaction commits. The wizard reports the persisted job state and can detach
its polling without cancelling server work. An explicit `retry_queued_job_id`
submitted with unchanged preview inputs reuses a scoped, never-started queued
job with a fresh delivery token; old deliveries cannot claim the replacement
attempt. Running/completed previews are returned without replay, and job reads
do not start work. Post-commit callbacks are not a durable transactional outbox.

Relevant suites cover structured extraction, document plans, coverage, provenance,
duration reconciliation, planning generation, workflow preservation, source access,
idempotency and publication guards. UI checks cover evidence visibility, unknown
values and explicit user-selected workflows.

## Preview confirmation and repeated analysis

The simple schedule Document Intelligence Preview uses the existing
`POST /api/v1/planning-intelligence/intelligence-runs/{id}/confirm-preview/`
command with `{preview: {...}}`. It displays the selections and findings before
the explicit **Confirm & save preview** action. The returned
`preview_confirmation.is_current` controls the saved indicator after save,
reopen and reload. Merely viewing the preview does not confirm findings or
approve a schedule. Individual fact decisions retain their separate review API.

The command retains its current project permissions, source-currentness and
conflict checks, atomic review/audit persistence, and rejected-fact preservation.
Errors keep the findings visible. Saving unchanged project inputs in the UI does
not send a redundant project update that would invalidate the source fingerprint.

Repeated analysis may retain recorded decisions only for uniquely matching
evidence under the same engine and exact source fingerprint/extraction manifest.
Actor/time are retained and a new audit links the old and new findings. Changed
assertions and new conflicts require review. Whole-preview confirmation has the
additional identical extraction, coverage and review-state requirements described
in `planning-extraction-boundaries.md`. No schema migration is introduced.

## Open an exact completed analysis

`GET /api/v1/planning-intelligence/intelligence-runs/{id}/schedule-workspace/`
returns a read-only schedule-evidence projection for the requested completed run.
It requires authenticated Planning read access and access to the run's project.
The response contains `analysis_run_id`, `project`, `state: "analysis_evidence"`,
`analysis_completed_at`, and the existing document payload: `intelligence`,
`activities`, `wbs`, `logic_matrix`, `validation`, `milestones`, `eddr`, `manhours`
and `narrative`. It contains no generation ID/version and creates no generation,
job, calendar, schedule, calculation, confirmation or audit mutation. No provider
call occurs on this GET. Current fact review states remain unchanged; applying
preview edits and confirming findings remain separate commands.

The current parsed file set, saved source fingerprint and extraction text hashes
must match the selected run. Engine compatibility applies to resuming extraction,
not to reading unchanged historical evidence. These checks run before and after
projection; concurrent review changes also require a retry. HTTP 409 returns
`intelligence_workspace_run_unavailable`, `intelligence_workspace_sources_changed`
or `intelligence_workspace_review_changed` with an actionable error. Historical
runs without a verifiable manifest require a new analysis. An exact older run
with unchanged sources is allowed; a newer run or saved generation is never
substituted. Failed/partial AI coverage within a succeeded analysis remains
visible while its deterministic source evidence can still be reviewed.

The projection uses the current document parser against those unchanged sources;
it does not claim that a historical AI run was reprocessed. The run's engine,
saved summary, findings and review decisions are not rewritten by the GET.
Two additional regressions cover read-only historical-engine success and changed
historical-source rejection; the combined workspace/resume/source-identity suite
passed 33 checks. Full recovery verification is recorded in the workspace
`artifacts/document-analysis-limit-recovery-results.md`.

Fourteen endpoint regressions and 43 existing document-generation, analysis-resume
and preview-confirmation checks passed in the isolated SQLite/model-sync harness.
Checks include a guarded read-only role, 403/404/authentication denial, no SQL
writes on repeated GET, exact-run identity, preserved missing values/AI coverage,
and concurrent source/review changes. No schema change or live provider/job
operation was performed; these checks do not certify PostgreSQL concurrency.
