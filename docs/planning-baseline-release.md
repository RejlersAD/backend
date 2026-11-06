# Planning and baseline

Release 2 applies approved planning policy to accepted scope in the existing Master Schedule.

## Planner workflow

1. Review extracted evidence and confirm project dates and scope completeness.
2. Select an approved project planning profile. The profile fixes workflow stages, explicitly approved durations and typed relationships, WBS conventions, resource roles and calendar policy.
3. Open **Plan generation**. Select accepted deliverables for workflow expansion and accepted source activities to preserve. Bind any approved cross-package rule endpoints to exact source identities. Confirm independent roots explicitly.
4. Preview activities, WBS, logic, required roles, source risks, provenance and unresolved inputs. Applying a preview creates a new draft and selects it as the Master Schedule atomically. Existing plans and baselines are retained.
5. Calculate, validate, submit to the assigned reviewer and publish using the existing approval controls. L1–L3 are views of the same version, not independent schedules.

Each build is immutable and binds an evidence revision, exact source fingerprint, approved profile snapshot, explicit applicability and field lineage. A later source or profile change requires review and a new build. Metadata supplied by a client cannot grant an approved-rule badge.

## Preserved boundaries

- Accepted source activities retain their durations, constraints, milestone types and typed dependencies. Aggregate package durations or dates are not silently distributed among stages.
- Cross-package relationships require exact accepted links or explicitly bound approved rules. Names, filenames and row order do not imply dependencies.
- Required resource roles are distinct from employee assignments. Missing quantities, hours, capacities and risk priorities remain unspecified.
- Generated risk statements retain their original value and lineage. Register decisions track owner, priority, response, status and resolution with optimistic revision checks and audit events. Baselines freeze the register state at publication; subsequent management decisions do not change the baseline or CPM inputs.
- The current CPM adapter supports one explicit working calendar, whole working-day durations/lags and FS/SS/FF/SF links. Mixed calendars, partial working days and dynamic level of effort remain unsupported and are identified before applying/calculating. It does not round unsupported source values.
- A contractual overrun remains visible as a warning/negative float. RADAI does not extend the registered project finish or silently compress approved durations. A conflicting accepted source date requires review before approval.

## Confirmed draft recovery - 5 November 2026

Recoverable planning-quality findings (timing variance, resource overallocation,
parallel-work assumptions and supported Start-to-Finish links) are advisory
warnings. They remain in assurance and approval history without blocking saving,
calculation or authorized approval. Cycles, invalid inputs, changed source
evidence, missing permissions and immutable versions remain protected.

The planner's confirmed Approve/Baseline actions prepare missing calculation and
assurance steps, then use the existing authorized approval commands. A baseline
is still created only from an approved version. Failed actions retain their
reason and offer a focused correction or reload action; failures are not hidden
or reported as successful.

`PATCH schedule-versions/{id}/recover-draft/` accepts `operation` and
`expected_updated_at`. Supported operations:

- `extend_planning_target`: store the current CPM forecast as this editable
  version's planning target, invalidate calculation/assurance and recalculate
  through the existing calculation job. The registered contractual finish and
  source documents are unchanged. The extension is the actual calendar-date
  variance, not the number of negative-float activities. Float uses the confirmed
  planning target; contractual overrun remains visible separately.
- `renumber_activity_ids`: allocate unique numeric IDs in increments of ten from
  100, preserving deleted IDs, relational relationships and original/source
  generation identifiers. Invalidate calculation/assurance, record the mapping
  in an audit event, and recalculate. Generated-logic reset resolves preserved
  source-generation IDs after renumbering.

New `planning-package-proposal/3` proposals use numeric IDs by default, retaining
original generated identities as provenance and remapping every relationship
and EDDR stage reference. WBS hierarchy codes remain separate from activity IDs.
The recovery command is limited to the current editable version, requires the
existing project write permission and rejects stale revision tokens.

Version-scoped adjustments use the existing input snapshot and are included in
calculation fingerprints and frozen baseline input manifests. Unadjusted
versions retain their previous calculation fingerprint; historical baselines
and source identities are not rewritten. No schema migration is introduced.

## Calendar and export fidelity

Calendars can now record exact daily working intervals and exception shifts. An empty interval map means working times were not specified. Existing calendars are not populated with assumed shifts.

JSON and Excel preserve the canonical snapshot and build lineage. Microsoft Project XML exports use explicit calendar intervals and verify the supported schema subset and parsed values; the provenance ZIP includes the full RADAI snapshot and verification results. This verification is not a claim of native Microsoft Project or Primavera certification. Unsupported field combinations are reported rather than silently dropped. Native `.mpp` is not generated, and unsupported XER semantics remain blocked.

## API

- `GET/POST projects/{id}/planning-builds/`: candidates, previous previews, prepare immutable preview.
- `GET projects/{id}/planning-builds/{uuid}/`: exact preview and findings.
- `POST projects/{id}/planning-builds/{uuid}/apply/`: requires preview fingerprint, Master selection revision and reason; applies and activates atomically.
- `GET/POST/PATCH projects/{id}/risk-register/`: read/create/manage scoped risks. Updates require item revision and reason. Source statement and provenance cannot be edited.

New schema is introduced by migrations `0040_planning_builds` and `0041_planning_registers_calendar_intervals`. No existing project is rebuilt by migration.
