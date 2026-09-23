# Scope-based schedule generation

The existing programmatic draft action now selects executable source deliverables
and specific work instructions, then expands each into at least five logical work
tasks. It no longer allocates project dates evenly to every contract statement.
Reference-only schedule files (`output_schedule_sample`) cannot supply new scope.
Excluded contract clauses remain recorded in `scope_selection` for review.

No frontend components or styles change. Existing task rows, WBS grouping,
assignment controls, schedule previews and approval actions consume the new data.
Existing saved schedules are not rewritten. No database schema migration is needed.

## Execution model

WBS remains the deliverable hierarchy. An activity package groups a workflow under
its WBS deliverable; only its leaf tasks and project boundary milestones enter CPM.
Each task contains an action and its actual scope title, a working-day duration,
predecessor and derived successor links, discipline code, responsible role,
package deliverable, task output, acceptance criteria and physical progress weight.
Package weights sum to 100; task outputs identify completion evidence.

The naming library includes architectural, structural, MEP, process, piping,
electrical, instrumentation, survey, utilities, FEED, procurement, construction,
pre-commissioning, testing, commissioning and project-controls workflows. Scope
classification distinguishes preparing a construction drawing or commissioning
procedure from executing construction or commissioning. Ambiguous or placeholder
scope is rejected for clarification, never padded with invented activities.

Industry comes from the owning project's explicit `custom_fields.industry`, then
`custom_fields.project_type`, then its `project_type`. Supported industry values
are building, industrial, infrastructure and oil_gas (including display-label
aliases). The existing engineering type means unspecified engineering industry.
Explicit complexity (`simple`, `standard`, `complex`) comes from project custom
fields, with a standard allowance when unspecified. An individual input package
can supply its own complexity.

## Duration and logic basis

Each library step has a documented allowance range. Complexity selects its lower
bound, rounded midpoint or upper bound. These are planning allowances, not values
prescribed by PMI, GAO or Oracle and not verified contract/source durations.
Duration basis and library version survive saving, materialization and reopening.
Prior planner durations, assignment and progress on matching workflow tasks are
preserved during regeneration. Existing started parent work requires review before
conversion to a task sequence.

Generated internal relationships are FS. Explicit package relationships map to
the appropriate first/last task endpoints, including supplied relationship type
and lag. A later execution phase can inherit a release gate from an earlier phase
only inside a shared explicit deliverable WBS; unrelated packages remain parallel.
One zero-day start and one zero-day completion milestone close the network.
No artificial row-order chain or target-date compression is introduced.

The existing working-calendar CPM engine calculates dates and float. Target-date
overruns remain visible. Generated networks are checked for duplicate IDs and work,
invalid/missing durations, dangling/self/circular relationships, incomplete task
chains and open/orphan branches. Validation also runs before materialization and
as a submission blocker. Successors are derived from persisted predecessors.

The optional API `workflow_mode: enterprise` uses the same generator through the
existing signed preview/apply endpoints. The existing Build schedule action keeps
the enterprise mode when rebuilding an enterprise draft. Source-only and configured
five-stage modes retain their semantics for source-review drafts. Generated estimates do not
silently become imported source evidence or approved baselines.

## Standards basis and limits

The distinction between WBS deliverables and scheduled work follows the
[PMI WBS guidance](https://www.pmi.org/learning/library/work-breakdown-structure-practice-standard-4591).
Sequenced activities, credible duration estimates, connected logic and a valid
critical path follow the principles in the
[GAO Schedule Assessment Guide](https://www.gao.gov/products/gao-16-89g).
Task relationships, duration and percent-complete information align with the
[Primavera P6 activity model](https://docs.oracle.com/cd/F74771_01/English/User_Guides/p6_pro_user/activities.htm).

This change implements these scheduling principles; it is not a standards
certification. Quantities, crew productivity, procurement lead times, technical
interfaces and resource availability still require project-specific planning
review. The supplied example PDF was inspected as a reference only: no project
names, contractual dates or alleged dependencies were copied into the library.

## Verification

Pure library/network tests cover supported industries, complexity, deterministic
identity, valid parallel branches, explicit release gates and malformed networks.
API tests cover source selection, reference exclusion, stale-input/revision guards,
holiday calendars, target overrun and persistence. Relational tests verify WBS
separation, metadata and relationships after reopening, and critical-path/float
calculations across parallel branches. The release SQLite test settings use model
sync; they do not certify PostgreSQL migrations or production data.
