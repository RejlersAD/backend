# Broad quoted planning assertions

The `6.0-broad-quoted-assertions` document engine extracts reviewable assertions;
it does not authorize schedules. Its quoted AI schema covers the existing project
scalars and deliverables plus milestones, constraints, review periods, packages,
disciplines, responsibilities, resource requirements, dependency statements, risks,
requirements and exclusions. Deterministic register extraction remains the source
of MDR scope. Conservative labeled clauses also work without an AI provider.

Every structured field must occur in the exact quote. Dates and units are retained
as written. Missing optional fields remain absent. Source IDs, character start/end,
the extracted-text SHA-256, and available page/sheet/line locators are retained.
Repeated identical quotes require their precise occurrence offset. Proposal-class
responses and unsupported or unquoted values are rejected, counted and never
persisted as document facts. Semantic interpretation still requires review.

The singular `dependency` and `constraint` assertion types are distinct from the
executable `dependencies` and `constraints` planning properties. A dependency
statement retains its original endpoint wording. Acceptance does not resolve it
by title, assume FS/zero lag, assign resources, or add a network relationship.

The declared document category guides extraction priorities; it is not proof of a
requirement. Uploaded text is provider input data, never provider instructions.
No document-specific names, layouts or sample dates are used as planning defaults.

## Coverage and continuation

Each provider pass is bounded by `PLANNING_AI_MAX_CHUNKS` (default 16). Coverage
records every chunk's source/offset, processed, skipped, failed or partial state,
and remaining count. Provider output truncation never counts as completed review.
Text extraction coverage and AI processing coverage remain separate; neither
certifies semantic completeness.

`run_document_intelligence(project, resume_run=previous_run)` creates a **new**
analysis run. It preserves the earlier run and facts, reuses completed chunks only,
and retries outstanding chunks. Checkpoints persist after processed chunks and
are fingerprinted by engine/schema, source ID, text hash, category and offsets.
Changed source content or source sets invalidate resumption. Source freshness is
checked again before committing extracted facts. No provider work occurs inside
the fact-persistence transaction.

`extraction_summary` exposes category/method counts, text and AI coverage status,
`chunks_remaining`, `resume_available` and `semantic_coverage_verified: false`.
Run status `succeeded` means that a bounded pass finished; a partial extraction
summary must remain visible and must not be displayed as complete understanding.
Raw provider checkpoints are kept in the run summary, outside the compiled UI
intelligence response.

## Persisted input review

`POST intelligence-runs/{id}/confirm-preview/` stores the submitted `preview`
selection separately from raw extraction, with the actual reviewer, time and
atomic audit event. The run response exposes `preview_confirmation.is_current`.
Confirmed/rejected finding decisions also survive reopening the run. Project or
source changes, later reviews and unresolved conflicts invalidate full-preview
confirmation; confirming a preview does not approve a schedule or baseline.

Re-analysis creates a new run and preserves the previous evidence. When the
latest preceding run has the same engine, project/source fingerprint and exact
extracted-source manifest, uniquely matching assertions retain their saved
confirmed/rejected decisions and original actor/time. Matching includes source,
type, key, value, method, excerpt and locator. Changed assertions remain
unreviewed. Conflict decisions carry forward only when the complete competing
assertion set matches; new conflicts remain unresolved.

The full preview is retained only if its prior review fingerprint is still valid
and the complete findings, review states, confidence, raw extraction and coverage
are unchanged. Newly discovered findings require another preview confirmation.
The new run records the preceding run and old/new fact/conflict IDs in its
`review_retention` summary and atomic `intelligence.reviews_retained` audit event.
No schema change or historical backfill is required. These rules apply to new
analyses after deployment; already superseded reviews are not silently repaired.

## Assertion type migration

Migration `0038_planning_assertion_types` adds the new assertion choices after
`0037_planning_profiles`; it changes no project or schedule data. Tests live in
`test_planning_fact_extraction` and the existing generic coverage/intelligence suites.
