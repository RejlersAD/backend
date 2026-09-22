# Editing activities in the Gantt table

The Duration, Start and Finish cells support direct editing. Enter or leaving the cell saves; Escape cancels. Failed requests keep the entered value available for correction. The Logic column opens an editor for incoming and outgoing FS, SS, FF and SF links, including signed working-day lag.

## Date and duration semantics

- A date edit selects one exact anchor (`must_start` or `must_finish`). Duration is retained. Editing the other endpoint replaces the previous anchor; CPM determines the opposite endpoint when the calendar, duration and network permit calculation.
- Original document dates, durations, references and excerpts remain evidence, separate from planner corrections. Editing a duration does not verify the original source calendar.
- A missing duration remains missing, rather than becoming a zero-duration activity. Milestones retain zero duration.
- Changes invalidate calculated dates, float and critical-path flags. Pending rows show the entered planner anchor; calculated dates and float are available after a successful calculation.

## Saved schedules and concurrency

`POST /api/v1/planning-intelligence/projects/{project_id}/simple-plan/edit-activity/` accepts an optimistic `revision`, a `task_id`, and one or more of `duration_days`, `timing_edit`, or `dependency_details`. The Logic editor can instead submit an atomic `updates` array of activity patches. The response is the refreshed shared planning read model.

For an independent working draft, the server applies the sparse patch to its saved task set. For a selected saved schedule, the first edit creates a planner revision containing the full WBS, activities and relationships. It preserves the original version and the independent working draft. Later edits reuse the planner revision. Source metadata, unchanged relationship evidence, resource assignments and risks survive cloning.

All edits require project write access. History, approved baselines and submitted schedules remain read-only. Stale revisions, unknown activities, self-links, duplicate relationship types and dependency cycles are rejected atomically. Multiple distinct relationship types between one activity pair are supported.

Planner revisions record their original input fingerprint and edited input fingerprint, and produce audit events. Uncontrolled input changes or changed source inputs require reconciliation. The deterministic CPM engine retains its input-readiness checks. Document-driven versions retain source review gates; legacy manual versions retain the normal assurance, submission and approval workflow. An edit never approves or publishes a baseline.

## Validation

Focused backend tests cover sparse selected-version edits, source preservation, calendar/date handling, typed links, read-only access, revision conflicts, missing durations and calculation invalidation. A representative 1,048-activity / 222-WBS / 784-link test verifies that editing the displayed schedule preserves an independent 220-row draft and reuses the planner revision on later edits. Browser tests cover inline interaction, persistence, validation recovery, logic editing and accessibility.
