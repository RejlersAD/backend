# Opportunity Analysis contract

Authorized scope: workspace `docs/features/sales-opportunity-analysis.md`,
decision SAL-ANALYSIS-01 (15 October 2026). Analysis is advisory and requester
scoped; it never writes a Deal decision, deadline, approval or task assignment.

## HTTP

- `GET /api/v1/sales/deals/{id}/analysis/`: current requester report or
  `status: not_started`. No provider call or business write occurs on GET.
- `POST .../analysis/` with `{retry?: boolean}`: idempotently enables/queues
  analysis of the saved opportunity's current sources. No caller-supplied
  file IDs, instructions, facts or provider settings are accepted.
- `GET .../analysis/sources/?offset=0`: 25 sources, exact excerpts and processing
  diagnostics, with `next_offset`. Each source exposes at most 100 findings;
  `claims_total` states its full finding count. `analysis_error` is an allowlisted
  source-level AI/provider error code; it contains no source text or credentials.
  File/chunk processing itself has no 20-file/first-page cap.

All operations require active identity, current opportunity read/export
permission and record visibility, reusing Sales workspace guards. Reports are
isolated by `(opportunity, requested_by)`. Linked email sources additionally
follow email-intake read and current mailbox visibility. Responses are private,
no-store. Missing/changed access cannot reveal another requester's cached work.

Response fields: opportunity_id, report id, status, stale, updated_at,
ai_ready/provider/model, counts, result, error_code, sources, next_offset and a
coverage_note. Status is not_started, queued, running, ready, partial, failed or
blocked. A changed source/provider/storage configuration hides stale findings
and coverage until synchronization; the UI requests refresh automatically.
`counts.chunks_failed` distinguishes failed AI sections from fully analyzed
sources. A report with no verified findings and failed chunks is a failed
analysis, not evidence that an opportunity has no requirements or risks.

`result.sections` contains brief, client_need, submission, risk, action,
deadline and next_step arrays. Each item includes text, exact source excerpt,
optional validated ISO calendar date, source_id, name and location. The server
keeps up to 12 summary items per category, counts all distinct findings, and
streams chunk claims without loading source text into the executive digest.
The Brief UI shows three items per question for quick reading. Upcoming dates
sort before past/unknown dates using the Dubai calendar. Multiple dates remain
a review signal, not proof of conflicting versions of the same milestone.

Sales follow-up dated 29 October 2026 adds capability_fit, strategic_fit,
resources, commercial, schedule and clarification source claim kinds. The
background pipeline synthesizes `result.manager_review` from the saved
opportunity and bounded verified claims. It reports capability fit, strategic
fit, resources, execution risk, commercial attractiveness and schedule, plus a
cited Go / Conditional Go / No-Go / insufficient-evidence recommendation and
client clarifications. Evidence IDs must resolve to extracted findings or saved
Opportunity fields. Internal Rejlers Abu Dhabi capability, strategy, resource
availability and commercial guardrails remain explicitly unknown unless an
approved source is supplied. The recommendation is advisory; analysis does not
write a Deal decision or prepare a letter. The result uses the existing JSON
field, without a migration. The contract version invalidates old claim caches
for reprocessing; synthesis failures leave the report partial and retryable.
Saved attachment IDs for generated outgoing letters are excluded from the
source inventory, so a prior regret or EOI cannot support its own review.
The manager output schema uses provider-supported array keywords; exact item
counts and evidence-ID limits are enforced again by server-side validation.
Overlong citation lists are bounded, citations on unknown dimensions are
discarded, and client clarifications without valid citations are omitted.
The Sales decision risk list is `result.manager_review.business_risks`, with
`category` (attractiveness, profitability, winnability or commercial_exposure),
`risk`, `decision_impact` and resolved `evidence`. It contains only cited risks
that may change a pursue, price or qualification decision. Portal access notices
and generic delivery tasks are excluded. The legacy `result.sections.risk`
remains raw extracted evidence and is not the curated Risk view. Changing the
review version refreshes synthesis from verified source claims without forcing
source extraction again.

The executive brief is an extractive digest of AI findings across sources,
not an autonomous legal/commercial interpretation. Document relationships are
shared reference identifiers and exact content hashes. Semantic precedence,
amendment supersession and action completion still need human confirmation.

## Processing and source coverage

Sales migration 0030 adds OpportunityAnalysis, OpportunityAnalysisSource and
OpportunityAnalysisChunk. Sources are current ready RADAI upload heads,
tracked SharePoint uploads and accessible linked email previews. Original
upload IDs, source identities and excerpt/page/part provenance are preserved.
Untracked SharePoint changes require connector synchronization; no embedded
URL is followed. Files remain in their original folders.

Read/export authorization and immutable content integrity are checked through
existing download adapters. Passive extraction supports PDF text, MIME EML,
MSG, DOCX, PPTX, XLSX and UTF-8 text/CSV/HTML. Source limit: 32 MiB, two million
text characters per document, 12,000 characters per provider chunk. Existing
XML archive/expanded-size/node budgets remain enforced. XLSX visits all sheets
within the archive budget and reads saved numeric/date values without executing
formulas or links, with explicit cached-values/row/column coverage diagnostics.
DOCX body-only, bounded MSG, linked email preview and nested EML attachments
are marked partial. Scanned pages, unsupported encodings/formats, unreadable
storage and corrupt documents appear as coverage gaps; they are never counted
as fully read. ZIP contents and OCR are not introduced by this feature.

The trusted Sales AI transport supplies bounded chunks as untrusted data,
never commands. Output must match the server schema; every finding requires
an exact contiguous excerpt from that chunk and date evidence. When a provider
quotes spaces across source line breaks, the server preserves the matching
original substring, never the provider's altered quote. Valid claims remain
available when a separate claim fails validation, but the source/report is
marked partial with `invalid_evidence`. An unverified claim is never published.
This validates provenance, not every semantic inference. Secrets/private source
text do not enter task arguments or application error logs.

SQL intents, 300-second leases, bounded retries/backoff and worker-loss recovery
drive one extraction/provider unit per task. Celery continues work immediately;
Beat dispatches due rows every 15 seconds if delivery is interrupted. Sources
and successful chunks are reused when unchanged. Source write/delete/head
signals invalidate enabled reports after commit; the first ready upload also
queues the authorized uploader's own report. An unchanged ready upload save
does not invalidate a report. A saved ready report is served on GET and an
idempotent POST does not update it; source or relevant opportunity changes,
explicit retry or provider/contract changes schedule new work. Optional invalidation failure
does not turn a successful existing document save into a false failure; GET's
snapshot check still rejects stale output. Provider configuration changes
invalidate cached claims. Analysis validation v2 invalidates old snapshots and
retries failed chunks once on the next refresh; subsequent manual retry
requeues failed and partially verified chunks, preserving successful work.
Safe provider failure codes (for example `provider_rate_limit` or
`provider_authentication`) remain available per source rather than being
collapsed into a generic evidence error. Existing reports with the legacy
combined code cannot retrospectively recover its cause.

Analysis validation v3 consolidates PDF/DOCX heads with the same
case-insensitive filename stem and identical nonempty extracted words after
case/punctuation normalization. Exact words matter: a single changed date,
amount or obligation prevents suppression. Unreadable sources and extraction
coverage gaps beyond DOCX's known body-only limitation are not suppressed.
The first extracted source remains the cited
representative; the later duplicate has no source/chunk record or AI call.
Suppressed upload identities and representative identities live on the
requester report (Sales migration 0031), so later refreshes do not recreate
unchanged duplicates. Changing either identity or removing the representative
reintroduces the upload for comparison. Existing completed cross-format
sources are consolidated on the first v3 refresh. `counts.total` and paged
sources represent unique analysis sources, including any accessible email
intakes, not the raw upload-list count. Physical uploads are not deleted.
The one-time v2-to-v3 contract transition makes old reports stale; a subsequent
authorized request queues reconciliation. Normal opens of an already current
saved report do not reprocess it.

Reports and cached chunks live in the private application database. Removing
their report/user/opportunity cascades derived data; replacing/removing a source
removes obsolete source chunks on reconciliation. No historical document bytes
or saved business decisions are modified. No automatic external correspondence.

## Verification and operation

Use `config.settings_release_test` for the synthetic domain/HTTP suite and the
dedicated Playwright configuration for the actual Opportunity panel. SQLite
tests do not certify PostgreSQL race behavior. Migration 0030 is additive and
must be applied before activating the API/workers. A rollback drops derived
reports/chunks; preserve them first if needed, while original documents remain
intact. No production rollout or migration is implied by local verification.

The two-minute target is a reading experience after background processing.
No two-minute ingestion SLA or 500-file provider throughput benchmark is claimed.

## Development release verification — 16 October 2026

Aligned with the fetched `origin/development` without conflicts. The isolated
Docker release-test run passed all 88 tests across opportunity analysis, letter
extraction, saved-letter regeneration, letter output/attachments and MSG parsing.
The Windows virtual environment lacks WeasyPrint; PDF-dependent verification
therefore uses the existing Python 3.11 Docker runtime with real rendering.

The local PostgreSQL database already had Sales 0030 and 0031 applied. After a
custom-format database backup, Sales 0032 and seven other pending migrations
applied successfully: electrical_comparison 0005–0008, pid_checker_v2 0026,
pid_verification 0015 and spec_customization 0013. Direct schema inspection
confirmed the analysis suppression and letter fingerprint columns. The full
application `makemigrations --check --dry-run --skip-checks` found no changes.
This evidence concerns local PostgreSQL; production migration and deployment
remain unverified. Apply the migrations before deploying the matching API/workers.
