# Delay and recovery analysis

The Master Schedule now has a **Delay & recovery** area. It uses an approved frozen baseline and an explicitly selected published operational report. Opening the area does not create records or recalculate project history.

## Planner workflow

1. Choose the baseline and register an event. Identify exact baseline activities, a source reference and any known event dates. Existing governance or risk records may be referenced from the same schedule version. Dates are optional; missing dates are not estimated.
2. Create an impact case using a published report. Each change records its event, evidence, reason, original value and proposed value. Supported changes are remaining duration, a hold/restart date for remaining work, and an explicit typed relationship addition, replacement or removal.
3. Calculate the combined impact. Review affected activity dates, milestone dates, float and downstream paths against the same published reference. A reachable activity is not necessarily delayed: available float can absorb an effect.
4. Add recovery alternatives. Each alternative starts from the combined impacted schedule and is evaluated independently. Resource productivity and acceleration rates are not guessed.
5. Record a recommendation and supporting contractual references. Submit for independent internal review. A creator, editor or submitter cannot approve their own case. Reviewed cases remain immutable; a revision preserves the original case and calculation history.
6. Download the JSON or Excel review package. It identifies the baseline, published report, events, changes, calculation fingerprint, results, limitations and internal review status. A stale calculation is explicitly marked.

No action in this area writes to baseline dates, actual dates, the contractual finish, resource assignments or financial ledgers. There is no automatic extension award or automatic recovery implementation.

## Calculation boundary

This release evaluates **forward remaining-work sensitivity** from a published data date. It is not retrospective forensic delay attribution. Events already ending on or before the selected data date are flagged because their effects may already be present in reported remaining work. Event elapsed days are never automatically added to the schedule.

The reference forecast must be reproducible from the frozen baseline and published observations using the supported operational forecast version. If it is not, comparative results are unavailable. Published history is not silently recalculated using a different engine. Later corrections to the selected reporting period are disclosed; the selected historical reference is retained until the planner deliberately creates another case.

The engine supports the existing whole-working-day calendar model and FS, SS, FF and SF relationships with signed whole-day lags. It preserves actual starts and finishes, warns about out-of-sequence execution, and rejects cyclic or contradictory scenario edits. Unsupported calendars, missing remaining-work data and missing timing evidence remain unavailable. Contract overrun and additional impact relative to the reference are reported separately.

Paths include reachable activities, activities whose timing changed, typed driving edges and bounded witness paths. Witnesses are capped at 30, path nodes at 10,000 and edges at 20,000, with omitted counts disclosed. The engine does not enumerate every possible path in a large network.

Time-extension days are a planner-entered proposal. A positive request requires a clause reference, notice reference, causation assessment, concurrency assessment, mitigation assessment and basis. An unsupported request or a request exceeding the modelled incremental effect is flagged. Internal approval records review of a technical recommendation; contractual entitlement remains undetermined.

## Integrity and access

- All records are scoped to the project, approved baseline and exact activity identifiers.
- Optimistic revision and expected-before checks prevent stale edits.
- Source fingerprints include events, exact referenced sources, the published report, changes, alternatives and recommendation. Changed evidence invalidates pending calculation/review.
- Referenced document versions retain their recorded content hashes. This case area does not re-download original storage bytes; it explicitly labels that check as not performed and asks reviewers to inspect the cited evidence. Fact-only references also include their document version. This is not a guarantee of current original-file availability.
- Calculation runs are immutable. PostgreSQL triggers also prevent bulk mutation of runs and reviewed cases, deletion of delay history, and rebinding records to another project, baseline or published report.
- The API and review exports expose schedule geometry, not cost ledgers, earning-policy budgets or hourly rates.
- Spreadsheet text is escaped against formula execution.

API: `GET/POST /api/v1/planning-intelligence/projects/{id}/delay-analysis/` and `GET /api/v1/planning-intelligence/projects/{id}/delay-analysis/cases/{case_id}/export/?format=json|xlsx`.

## Project removal

The enterprise project's existing DELETE endpoint now archives the project and its active planning workspaces atomically. Protected planning bases, profiles, baselines, tasks, reports and financial records are retained. Only the owner, current project manager or authorized active administrator can archive it. Archived projects disappear from active lists and their planning workspaces reject access and changes. A repeated request returns a clean not-found response.

Migration `0043_delay_analysis` adds the three delay tables and their integrity triggers. Project archival uses existing soft-delete fields and needs no additional schema migration.
