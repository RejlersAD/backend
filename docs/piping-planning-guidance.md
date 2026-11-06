# Piping planning guidance

## Authority and scope

The user supplied Planning Department guidance and explicitly confirmed these
assumptions on 6 November 2026. No external department document or decision ID
was supplied. This feature implements those instructions as explicit planning
input for a selected editable schedule revision. It does not certify source
document facts, create missing deliverables or approve a baseline.

Consulted the shared README, PROJECT_BRIEF, gap report, DECISIONS I-29/I-29e,
PRD PC-01, architecture/data/API context, workflows/security, CONTRIBUTING and
the planning-package restoration and unified-master-schedule briefs. Existing
project scope, immutable history, audit and explicit approval boundaries apply.

## Confirmed rules

- Default every specified relationship to FS+0 working days. Preserve only the
  specified P&IDs IFR -> Plot Plan IFR FF+3 and Piping Design Basis IFR -> 30%
  Model Review Procedure IFR FF+4 exceptions.
- Apply the provided Process Basis, Piping Basis, PMS, valve specification,
  datasheet, material requisition, vendor, TBE, purchase-order and model-review
  chains. The versioned catalogue is `services/piping_guidance.py`.
- Correct the vendor chain to MR for Valves IFA -> Valve Vendor Offers 15d ->
  Receipt of Valve Vendor Offer 5d -> TBE for Valves IFC. Remove the superseded
  MR-to-receipt and vendor-offers-to-TBE shortcuts.
- For equivalent IFR/IFA/IFC stages present on both ends of a same-revision
  document dependency, link matching revisions with FS+0. Explicit cross-revision
  gates, such as closeout IFA -> MTO IFF, remain explicit exceptions. Lags are
  never copied from an IFR exception onto other revisions.
- IFC, IFI and IFF remain distinct. A generic FINAL_ISSUE is not a substitute.
- Preserve positive durations of 15/5/10/10 working days for vendor offers,
  offer receipt, PO placement and vendor input, and 5/10/5 working days for
  30/60/90% modelling closeout completion. These rows become tasks, retaining
  their original names and original duration/type metadata. Zero-duration
  milestone arithmetic cannot consume those periods.
- Piping activities, selected by the actual Piping discipline assignment, must
  finish within the first floor(90% * N) complete working days of the project.
  N counts working dates inclusively from Project Start through Contractual
  Project Finish on the active CPM project calendar, including holidays and
  working-day exceptions. The deadline is the last date in that window; any
  fractional working day is reserved as contingency. An extended draft planning
  target does not change this contractual comparison.

## Workflow and contract

In Master Schedule -> Logic, choose **Review piping guidance**. The read-only
preview shows exact matched links, removed conflicting links, duration/type
corrections, absent scope and blocking missing/ambiguous stages or cycles.
Missing scope is disclosed. A required relationship with just one endpoint
present blocks apply, as do missing stages for present deliverables. Rules whose
two deliverable families are both absent are not applied to unrelated scope.
The user reviews the preview before choosing **Apply reviewed piping guidance**.
Save pending activity edits first. Approved/baselined versions require a new
revision; application does not automatically create or approve one.

`GET /api/v1/planning-intelligence/schedule-versions/{id}/piping-guidance/`
returns the plan, `preview_token`, rule version, current calendar deadline and
`can_apply`. `POST` to the same route accepts `{preview_token}`. The existing
module/object write permissions apply. Writes lock the version, recheck current
revision, state and preview identity, apply atomically and record an audit event.
Stale previews and blocked/immutable revisions return 409; denied project reads
remain 404. Approval authority is not granted by this command.

Application stores a version-specific policy under the existing JSON planning
adjustments, invalidates calculation and assurance, and asks the planner to
recalculate. No database schema change is needed. Revision cloning carries the
policy and exact relationship metadata forward without retaining old activity
primary-key bindings. Reset generated logic reapplies the opted-in policy
inside the same transaction; a cycle/conflict rolls back the reset.

Assurance detects altered rule links, protected durations and the 90% deadline.
These are critical findings and prevent assurance/schedule approval. Names,
discipline and activity type join calculation fingerprints only for opted-in
versions; unchanged legacy schedules retain their prior fingerprint shape.
Existing manual/source links outside the defined family pairs are retained.
Conflicting generic generated category/wave entry gates are shown as removals;
retained links that create cycles require explicit planner correction.

## Verification

Targeted backend tests cover the catalogue, equivalent revisions, explicit lags,
nonzero milestone corrections, calendar exceptions and rounding, stale and
unauthorized requests, immutable versions, repeated application, missing stages,
cycles, reset persistence, assurance and audit rollback. Browser checks exercise
preview/apply, read-only state, missing revisions, stale responses and dirty edits.
Verification on 6 November 2026 passed 18 piping-guidance tests on the local
PostgreSQL test database and 29 existing planner-recovery/approval regressions.
Eight browser checks passed across the guidance panel, baseline preparation,
active-job locking and table/timeline alignment; the five guidance-panel cases
also passed after the final UI error-routing update. Changed-file ESLint, Python
compilation and the frontend production/PWA build passed. Existing HMB CSS,
mixed-import and large-bundle build warnings were outside this change.

A transaction explicitly set READ ONLY inspected enterprise project 77's current
approved version 39: 87 activities, 85 assigned to `not_specified`, two with no
discipline, and no exact guidance-family matches. That schedule was not modified.
The user must identify the intended piping schedule or supply/review its missing
scope before applying the policy; no project-specific IDs are used by the rules.
