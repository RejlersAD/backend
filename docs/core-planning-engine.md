# RADAI core planning engine

The core engine lives inside Project Control. Open a project, then **Plan & Baseline → Schedule → Master Schedule**. Existing projects, approval workflows and published baselines remain the basis for planning and reporting.

## Work breakdown and schedule

Create or edit a manual activity and provide its **Phase** and **Deliverable** to organize the WBS as Project → Phase → Deliverable → Activity. Existing activities without these labels retain their existing grouping. The hierarchy is retained when the draft becomes a relational schedule version.

Activity planning supports start and finish dates, duration, predecessor relationships and date constraints. Each predecessor can use FS, SS, FF or SF with a signed lag in working days. Relationship types are retained on save, reload and schedule materialization. Constraint validation and the existing CPM engine expose infeasible dates; dependencies are not silently converted to FS.

The current deterministic engine calculates dates at whole-working-day precision. Manual decimal duration/lag inputs are retained; the editor and schedule warnings disclose that calculations round them upward to whole days. Document-driven schedules retain their stricter supported-input gates and evidence review requirements. This increment does not add hourly/mixed-calendar calculations or automatic resource leveling.

## Resources and productivity

The **Resources** area contains the project resource catalog and activity allocations, alongside employee assignments.

- Create labor, equipment or material resources with an explicit resource unit and daily capacity.
- Optionally record a positive productivity rate and its output unit together. For example, `2 drawings / hour` describes output per resource unit.
- Allocate a resource to a saved schedule activity using planned resource units. Optionally provide planned output when the resource has a productivity rate.
- Required resource units = planned output / productivity, rounded up to 0.01 resource unit. This comparison does not overwrite entered allocations or activity durations.
- Resource changes require recalculation/review of affected schedules. Baseline views use frozen resource inputs. Resources already used by immutable schedules must be replaced with a new resource code when revising their definition.
- Project access and commercial access remain distinct. Unit costs and allocation budgets are hidden from readers without the required commercial access.

The read endpoint is `GET /api/v1/planning-intelligence/resources/plan/?project=<id>&version=<id>`. Catalog and allocation writes reuse the existing resource and assignment APIs.

New fields are `ScheduleResource.productivity_rate`, `ScheduleResource.productivity_unit`, and `ActivityAssignment.planned_output_quantity`. Omitted productivity and output remain unknown.

## Progress and project controls

After baseline publication, use **Operational controls** to approve an earning policy, enter weekly observations, review source actuals and publish a reviewed report. **Review & publish** shows planned, actual and remaining progress for both the project and each activity.

Actual progress follows the approved earning method. Project progress uses explicit approved weights. Remaining progress is `100 − actual progress`; missing observations or weights do not become an assumed percentage. Remaining progress is separate from the remaining duration used for date forecasts.

| Measure | Calculation and basis |
| --- | --- |
| BAC | Sum of approved activity budgets. |
| PV | Approved budgets phased through the reporting data date. |
| EV | Sum of approved activity budget × measured earning fraction. |
| AC | Reconciled posted actual costs in the approved reporting currency, with confirmed coverage. |
| SPI | EV / PV; unavailable when the required inputs or a positive denominator are missing. |
| CPI | EV / AC; unavailable when the required inputs or a positive denominator are missing. |
| EAC | BAC × AC / EV, using unrounded inputs and positive AC/EV. This is a statistical cost-efficiency projection. |
| ETC | EAC − AC, using unrounded inputs. |

The UI exposes the existing cost calculations and adds explicit remaining progress and an activity comparison table. It does not substitute statistical EAC for an independently approved commercial forecast. Monetary measures retain commercial access controls.

New report previews record rule version `operational-controls/1.1`. The calculation version participates in review freshness: a submitted report calculated under older rules requires a new review. Published reports retain their original values and rule version; historical reports without remaining-progress fields show those values as unavailable.

## Risks and mitigation

The **Risk register** supports source statements, ownership, priority, status, response and resolution. Risk management now also records:

- Explicit probability from 0–100%, conditional cost impact/currency, and assessed schedule-impact days.
- The assessment basis, preserving the distinction between an estimate and measured project performance.
- A mitigation plan, due date, and status: not planned, planned, in progress or completed.
- Overdue mitigation counts and expected cost grouped by currency. Expected cost = probability / 100 × conditional cost impact; incomplete assessments remain unknown.

Individual schedule impacts are not added together or presented as a calculated project completion forecast. Use the existing Delay & recovery workflow to assess an impact against schedule logic.

Cost impacts require commercial access. Shared baseline risk snapshots and audit payloads omit structured monetary assessments; authorized users can review them in the live risk register. Source statements and provenance remain immutable, and management edits retain revision checks.

## Uploaded schedule fidelity

Supported ruled PDF schedules now retain their printed column positions during extraction. Original duration, Start, Finish and Total Float remain independent source values; blank milestone endpoints remain blank. The parser preserves printed summary indentation and page/row/cell references without inventing native WBS identifiers, calendars or predecessor relationships. Incomplete or stale geometry falls back to the existing text extraction.

Source imports display the uploaded project and summary values, including zero and negative float, separately from calculated CPM results. The source project row is identified by its signed evidence even when its title differs from the registered project name. Import freshness is checked against the signed source inputs, independently of calculation readiness. Existing MDR drafts and assignments remain available after an imported version becomes the active Master Schedule.

The scheduling AI logic-and-sequence action and its proposal/connection dialogs have been removed. Standard schedule building, manual dependency editing and calculation remain available under their existing permissions.

## Deterministic logic from an imported schedule

**Build logic & sequence** creates a separate calculated planning draft from an imported schedule. It retains the original PDF version, activity identities, WBS, durations and printed evidence. This operation uses deterministic rules and does not call an AI service.

Complete sibling groups with the five named stages are linked in this order: IFR, Company Review, IFA, Company Approval, Final Issue (IFT/IFM). The added relationships are finish-to-start with zero lag and are identified as planning rules, not relationships recovered from the PDF. Activities outside a complete group remain in scope and are explicitly listed for further network review.

The planner supplies a working calendar. A default calendar remains a scenario assumption until confirmed; no holidays are inferred. Printed starts become disclosed start-no-earlier-than planning release constraints. For a zero-duration milestone with only a printed finish, that finish supplies its release date. These are new planning assumptions, not recovered native constraints. The original project finish remains the target used for the backward pass, so late chains show negative float.

The calculated draft shows working-day CPM dates and float, colored workflow stages and dependency arrows. The original source dates and float remain available for comparison. A calculated result does not establish that the engineering network is complete or that the PDF's native calendar has been reconstructed. Calendar exceptions, open ends, cross-deliverable dependencies and source-date differences still require review before baseline approval.

## Schema and verification

Migrations `0044_resource_productivity` and `0045_risk_impact_mitigation` add optional planning fields and risk-value constraints. Existing records retain unknown productivity/impact values; no schedule, progress or business approval is synthesized.

Focused regressions cover typed dependencies, hierarchy persistence, constraints, resource CRUD and allocations, productivity limits, project isolation, immutable snapshots, risk assessments/mitigation, cost restrictions, weighted progress and report freshness. PostgreSQL migration verification must supplement the fast SQLite test harness, including forward/reverse/reapply behavior and retained legacy rows. Browser checks cover the new manual planning, resource, risk and operational-report controls.

Verification completed on 22 September 2026:

- WBS/scheduling integration: 94 backend tests passed, including 11 new engine cases.
- Resources: 8 backend tests and 30 existing scheduling regressions passed.
- Risks: 18 backend tests passed; the 8 risk cases also passed after shared-audit redaction hardening.
- Operational controls: 54 tests completed successfully, with one existing PostgreSQL-only test skipped by the SQLite harness.
- Browser checks: 14 operational-control cases passed (one navigation timeout passed on retry), 2 resource cases passed including accessibility, and the manual scheduling workflow and dedicated risk UI checks passed.
- Targeted frontend lint and the final production build passed.
- A disposable PostgreSQL schema clone passed forward/reverse/reapply checks, retained legacy rows, rejected invalid risk values, and showed no planning model drift. The clone was removed afterward.
- Migrations 0044 and 0045 were applied to the local `radai_dev` database and the new columns verified. The backend was reloaded and its health endpoint returned HTTP 200.

The subsequent deterministic source-logic update passed 33 source-import/logic backend cases, 40 existing master-schedule/boundary/date cases, and 12 browser cases, plus the production build. The final comparison-field addition also passed its six focused backend cases. On local project 59000102, version 34 retains 1,048 activities and 222 summary nodes, adds 784 stage links, and calculates 10 critical activities. An independent calendar/DAG verifier checked 9,432 calculated fields with no mismatches and confirmed source version 31 remained unchanged. The live Gantt displayed all 784 arrows and calculated float. This scenario retains the 6 January to 4 September 2026 window; its assumed Monday-Friday calendar gives 174 working days, compared with the source's printed 165 days. The 68 activities outside the named stage groups remain for dependency review.

These results cover the local implementation and selected automated workflows. A production rollout and release PR were not part of this change.
