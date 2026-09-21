# Unified planning foundation — Release 1

The existing Schedule page remains the Master Schedule. The sidebar, project
navigation and Activities & Gantt layout are preserved.

## Planner workflow

1. Open a project and its Schedule. Existing working drafts remain available.
2. Open **Schedule → ⋯ → Planning profile** to create a draft from an explicitly selected
   workflow, review the stage durations/relationships and optional engineering
   rules, and configure calendar, WBS, progress and resource policies.
3. Propose the profile. An eligible project authority reviews and approves its
   exact version, then selects it for the project. Selection records the
   approved snapshot; it does not overwrite activity values.
4. Analyse uploaded documents. **Evidence** shows extracted categories, source
   fragments, missing inputs and decisions. Partial extraction is visible;
   **Continue extraction** processes another bounded group of chunks.
5. Refresh and review evidence explicitly after an analysis. Accept quoted
   facts or supply planner inputs with a reason. Unspecified values stay empty.
6. Create the schedule from accepted inputs. It opens in the same Master
   Schedule and remains selected after reload.
7. Calculate, validate, submit to an eligible reviewer, and publish the approved
   baseline from that canvas. Reopening creates a new version and preserves the
   published snapshot. Version history is read-only until explicitly selected.

## Data boundaries

- `PlanningProject.master_schedule_version` identifies the current saved
  relational schedule. A null selection retains the original working draft.
  Opening history is a read operation. No migration selects or rebuilds a
  business project automatically.
- Canonical commands use an opaque revision token covering version inputs,
  evidence, calendar, calculation and review state. Version selection has a
  separate revision. Evidence materialization and activation are atomic.
- Existing activity assignments and employee history are preserved. Selecting
  accepted evidence does not transfer assignments across unrelated identities.
- Current accepted schedules use the existing CPM, assurance and approval
  services. Baseline authority and immutable snapshots are retained.
- Approved planning profiles are versioned and immutable, including a database
  guard on PostgreSQL. Revision, proposal, approval and selection have separate
  permissions and audit events.
- Document evidence, planner input, proposals, calculated values and unknown
  provenance are labelled separately. A field cannot claim a verified source by
  supplying a same-project fact ID for a different entity/property/value.
  A selected profile is not proof that a rule was applied to an activity.

## Extraction boundaries

Quoted extraction now covers deliverables, milestones, constraints, review
periods, packages, disciplines, responsibilities, resource requirements,
dependency statements and risks. Reviewable source assertions remain separate
from executable schedule links. Source quotes, file versions and physical
locators are retained.

Large-document analysis records chunk checkpoints and partial coverage. Resume
creates a new auditable run, reuses completed chunks and checks source hashes
before queueing and again in the worker. It does not silently approve findings
or replace a schedule. Processing coverage does not claim complete semantic
understanding. Unsupported or ambiguous facts remain review items.

## Release boundary

This release establishes profile governance and a unified evidence-to-schedule
workflow. Applying approved engineering rules to generate complete WBS,
activity sets, resource allocations or missing durations is the next release.
No new rule application, schedule inference, native MPP export or automatic
baseline approval is claimed here.

## Migration and verification

Migrations 0037–0039 add approved profiles, broader fact categories and the
explicit Master Schedule selection. They add schema without rewriting project
schedules. Regression coverage includes selection isolation/concurrency,
immutable baseline revision, approval gates, provenance integrity, resumable
extraction and the existing simple planning workflow.
