# Accepted planning inputs, calculations, baselines and exports

`services/planning_boundaries.py` is the shared calculation and approval policy.
Document-driven versions (including revisions of them) must match the persisted
evidence graph's accepted property values. A metadata flag, confidence score or
an extracted row alone cannot authorize calculation. Existing manually authored
versions retain their explicit scheduling workflow; they are not retrospectively
represented as document-verified plans.

## Calculation

The implemented deterministic CPM engine supports a single validated working-day
calendar, whole-day durations and lags, and FS, SS, FF and SF relationships.
Missing calendars and pending durations block calculation. Document-driven
fractional durations, mixed calendars, partial working-day exceptions and dynamic
level-of-effort activities are reported as unsupported; they are not rounded or
normalized silently. Invalid endpoints and dependency cycles block calculation.
The evidence graph requires an accepted predecessor list; an explicitly accepted
empty list distinguishes independence from missing relationship information.

Each successful calculation records `schedule.calculated_inputs` with the exact
activity inputs, relationships, calendar and exceptions, source versions, accepted
evidence graph and rule versions. Its canonical SHA-256 manifest also guards
approval against source or calendar changes after calculation. Calculation job
fingerprints include source freshness, graph revision and calendar exceptions.

This engine does not claim hourly scheduling, automatic resource leveling or
native Primavera scheduling equivalence. Explicit project finish dates remain
the backward-pass target; infeasible networks retain honest negative float and
timing warnings rather than silently extending the contractual project finish.

Accepted source planned dates are preserved separately from explicit constraints.
The engine never turns a source start/finish into an implicit constraint. After
calculation, a difference between those accepted dates and CPM output creates an
issue containing both dates and the source fact reference. The draft calculation
remains available, but approval and validated export readiness are blocked until
the discrepancy is reviewed. Structured JSON/Excel drafts retain the issues so
the team can review them without losing access to partial output.

## Approval and baseline

Existing authority, current-version, review and schedule-assurance controls are
retained. All approval routes also require accepted inputs and a calculation
manifest matching those inputs. An approved/baselined version cannot be
recalculated; create a revision. Baseline snapshots retain the frozen input
manifest, source graph, calendars and exceptions alongside output activities and
relationships. Approved baseline model saves reject changes to its approved
content. PostgreSQL triggers additionally protect approved baseline snapshots,
original document versions, assertion values and append-only decisions against
bulk-update/delete bypasses. Baseline exports read the saved snapshot and frozen
project/schedule identities, never today's mutable names or calendar.

The Master Schedule canvas also projects approved baselines directly from these
frozen snapshots. Later source, profile or calendar edits cannot clear published
dates, float, WBS summaries or historical source/rule badges. Older snapshots
without a retained calendar keep their leaf dates and declare unavailable summary
duration rather than borrowing today's calendar. The live risk-management
register is returned separately from the baseline's original risk snapshot.

## Export adapters

`GET schedule-versions/{id}/export-capabilities/` returns the supported formats,
versions, limitations and traceability behavior. The same access checks apply as
the schedule export endpoint.

| Adapter | Status | Behavior |
| --- | --- | --- |
| RADAI JSON 2.0 | Implemented | Structured draft or frozen approved baseline; embedded evidence, calendars, relationships and readiness. |
| Excel / Office Open XML | Implemented | Tabular schedule plus calendar, adapter, validation and complete JSON traceability sheets. Long values are retained in numbered chunks; document text is never an Excel formula. |
| Activities CSV | Implemented, partial | Activity table only. It does not preserve a complete baseline, calendars or relationships. Use JSON/Excel for traceability. |
| Primavera P6 XER | Legacy, unvalidated | Existing subset retained only for legacy integrations. Rejected for evidence-driven plans/baselines and unsupported calendar/constraint semantics. No P6 round-trip compatibility claim. |
| Primavera XML | Unavailable | No implemented/validated adapter; requests return an explicit error. |
| Microsoft Project XML (`mspdi`) | Implemented subset | Real XML, checked against the RADAI supported-subset XSD and independently parsed to compare supported input fields. Microsoft Project application import/recalculation has not been tested. |
| Microsoft Project XML bundle (`mspdi_zip`) | Implemented subset | `schedule.xml`, full `radai-provenance.json`, and `verification.json` with SHA-256 binding. Recommended for baseline handover. |

JSON/Excel drafts are explicitly labelled `structured_draft`; unknown durations
remain null and blocked document calculations do not export a false critical
path. These are useful review outputs, not a claim of an approved or calculated
schedule. Shared validation rejects invalid identifiers, date ranges and dangling
relationships before serialization. Approved exports are labelled
`approved_baseline`. Old baselines without a frozen source/calendar manifest
declare that limitation rather than reconstructing unrecorded history.

### Microsoft Project interchange boundary

Use `GET schedule-versions/{id}/export/?export_format=mspdi_zip` for a ZIP or
`export_format=mspdi` for XML. `ms_project_xml` remains a compatibility alias;
export records use the canonical `mspdi` value. No file is named `.mpp`.

Native export requires a current calculated/approved plan or a frozen approved
baseline, explicit referenced calendars, and explicit working-time intervals.
The existing date-only CPM finish is inclusive. XML starts at the first working
interval and finishes at the last interval on the respective date. The complete
working time between those endpoints must exactly equal the recorded duration;
fractional/intraday timing is not invented. Start milestones use the working-day
start; finish milestones use its finish. Durations and work use ISO durations;
lag and slack are converted exactly to tenths of a minute. Negative float and
negative lag are preserved, not clamped.

The subset carries WBS summary tasks/outline hierarchy, activity identities,
tasks and milestones, FS/SS/FF/SF links with explicit signed lag, supported dated
constraints, referenced calendar weekdays/shifts/date exceptions, resource
identities and labor/equipment assignment work. The XML uses fixed-duration,
non-effort-driven tasks; this is an interchange representation, not a new source
assertion about resource leveling. Original task types, database IDs, resource
capacity, cost rates, assignment quantities/costs, calendar timezones, unused
project calendars, risk registers, accepted planning-build manifests and full
evidence/approval provenance remain in the companion JSON. Full source values
are also retained by the JSON and Excel adapters, including values without a
supported native mapping. The existing accepted-input manifest/hash is unchanged.

Unsupported semantics fail with structured `mspdi_unsupported_semantics` issues:
missing shifts, inconsistent hours/date/duration, nonworking endpoints, excess
numeric precision, calendars with different day units/timezones, level of effort,
material assignments, dangling references, duplicate typed links and cycles.
These errors do not block structured JSON/Excel review exports. No assumed
08:00–17:00 calendar, default lag, guessed duration, or truncated source name is
introduced. Native resource costing and resource leveling are not certified.

Verification has separate meanings:

1. The stdlib writer produces well-formed XML; libxml2 parses it independently.
2. libxml2 validates `services/schemas/mspdi-supported-subset-1.xsd`, an independently
   authored **RADAI subset contract**, not Microsoft's complete vendor schema.
3. The independent parser compares the native supported fields with the original
   snapshot, including dates/working-time endpoints, hierarchy, calendars,
   exceptions, signed lag/float and assignment work.
4. `verification.json` identifies these checks and explicitly records
   `vendor_application_roundtrip: not_tested`. Desktop Microsoft Project/P6
   import, recalculation and save/reimport testing remain outstanding.

The format is based on Microsoft's [Project XML reference](https://learn.microsoft.com/en-us/office-project/xml-data-interchange/project-xml-data-interchange-schema-reference),
[task/link type definitions](https://learn.microsoft.com/en-us/office-project/xml-data-interchange/type-element-multiple-parents),
[predecessor lag units](https://learn.microsoft.com/en-us/office-project/xml-data-interchange/predecessorlink-element),
[calendar structure](https://learn.microsoft.com/en-us/office-project/xml-data-interchange/calendar-elements-and-xml-structure),
and [slack units](https://learn.microsoft.com/en-us/office-project/xml-data-interchange/freeslack-element).
The namespace follows Microsoft's [XML file example](https://learn.microsoft.com/en-us/office-project/xml-data-interchange/saveversion-element).

Apply the evidence architecture migrations before starting application workers:

- `0034_evidence_graph` stores versioned sources, assertions, relationships and decisions.
- `0035_evidence_immutability` installs PostgreSQL immutability triggers. Other database engines retain model/API protection but do not receive these database triggers.
- `0036_schedule_evidence_projection` stores the originating graph, revision and immutable input snapshot on projected schedule versions; dropping activity metadata cannot remove this provenance policy.
- Release 2 migration `0041` adds explicit calendar working-time intervals and the native export record formats alongside planning-risk support. Empty shift fields mean unspecified, not standard office hours.

`test_planning_boundaries`, `test_evidence_graph` and the existing scheduling and
approval suites verify these contracts.
`test_ms_project_export` additionally verifies supported XML field preservation,
independent schema/parser rejection, baseline bundle checksums and agreement
between JSON, Excel traceability and the native bundle sidecar.
