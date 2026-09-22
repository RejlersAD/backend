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

## Calendar and export fidelity

Calendars can now record exact daily working intervals and exception shifts. An empty interval map means working times were not specified. Existing calendars are not populated with assumed shifts.

JSON and Excel preserve the canonical snapshot and build lineage. Microsoft Project XML exports use explicit calendar intervals and verify the supported schema subset and parsed values; the provenance ZIP includes the full RADAI snapshot and verification results. This verification is not a claim of native Microsoft Project or Primavera certification. Unsupported field combinations are reported rather than silently dropped. Native `.mpp` is not generated, and unsupported XER semantics remain blocked.

## API

- `GET/POST projects/{id}/planning-builds/`: candidates, previous previews, prepare immutable preview.
- `GET projects/{id}/planning-builds/{uuid}/`: exact preview and findings.
- `POST projects/{id}/planning-builds/{uuid}/apply/`: requires preview fingerprint, Master selection revision and reason; applies and activates atomically.
- `GET/POST/PATCH projects/{id}/risk-register/`: read/create/manage scoped risks. Updates require item revision and reason. Source statement and provenance cannot be edited.

New schema is introduced by migrations `0040_planning_builds` and `0041_planning_registers_calendar_intervals`. No existing project is rebuilt by migration.
