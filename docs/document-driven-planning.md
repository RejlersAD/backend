# Document-driven planning

RADAI's document planning path extracts source facts before assembling a draft.
Example files are regression fixtures, not project templates or production rules.

## Evidence flow

1. Parse the uploaded file and record page/sheet coverage, skipped units, parser
   failures, truncation and unsupported content. Preserve source text and locators.
2. Recover activity tables by their explicit column labels. Supported adapters
   include delimited tables, Markdown tables, register rows and printed schedule
   text. Column order, filenames and project vocabulary do not determine logic.
3. Preserve exact titles, native IDs, printed values, units and per-field evidence.
   AI claims require a project-owned source, matching quote and matching value;
   a valid quote still requires human semantic review.
4. Assemble an evidence draft. Unspecified values remain null and are displayed
   as **Not Specified**. Preserve separately any register rows that have no explicit
   association with schedule activities. Similar names do not establish a link.
5. Review facts and missing information. Only uniquely resolved predecessor IDs
   with explicit relationship type and lag form source relationships. Missing
   relationship fields are not converted into finish-to-start, zero-lag links.
6. Calculate only after the calendar and network are verified. Printed source
   dates/durations and calculated dates/float are different kinds of information.

## Source dates in the schedule

The planning read model exposes matched source start and finish dates separately
from planned and calculated dates. Each endpoint keeps its extraction status and
source references. Exact source dates remain visible in the activity table and
Gantt even when a duration, calendar or dependency network is unverified. They are
labelled **Source dates** and do not supply calculated float or critical status.

**Not Specified** means no usable source value is matched to that field;
**Not calculated** identifies pending schedule calculation; **Review source**
identifies ambiguous, conflicting or invalid date evidence. These labels do not
claim that a missing value is absent from every uploaded document.

Ordinary bars require both endpoints; a single source date never supplies the
other endpoint. A deliverable summary uses its own source evidence, without
deriving dates or duration from child source dates. These display fields are
read-only and do not rewrite project dates, activities or stored planning state.

## Current safeguards

- The document generation pipeline does not invoke catalogue activity templates,
  fixed stage durations, inferred work-package sequencing or default manpower.
- Build Schedule defaults to preserving source activities. A previously selected
  five-stage workflow remains user-configured rather than document-detected.
- Deliverable names do not automatically create phases, scenarios, dependencies,
  recurring instances or approval gates.
- A single project container is administrative grouping, not a claimed source WBS.
- Incomplete evidence produces a reviewable generation without materializing a
  calculated schedule version or publishing a baseline.
- Project dates, existing assignments and published baselines are not rewritten
  merely because this extraction policy changes.
- Parser coverage and AI chunk coverage are reported separately from semantic
  understanding. Unknown legacy coverage is never shown as fully verified.

## Deliberate limitations

This implementation does not claim complete understanding of arbitrary files.
Images, diagrams, unsupported native formats, ambiguous tables and unprocessed
chunks remain review gaps. The generic evidence draft currently requires calendar
and network verification before calculation; it does not reconstruct a native CPM
calendar from printed dates. Large files are processed with bounded resource
budgets, and any cap is reported as partial coverage instead of silent completion.

No synthetic stages, guessed dates or invented dependencies may be used to fill
those gaps. Additional format adapters must keep the same evidence contract and
include tests with unrelated project types, ambiguous values and malformed input.

## Verification

Relevant suites cover structured extraction, document plans, coverage, provenance,
duration reconciliation, planning generation, workflow preservation, source access,
idempotency and publication guards. UI checks cover evidence visibility, unknown
values and explicit user-selected workflows.
