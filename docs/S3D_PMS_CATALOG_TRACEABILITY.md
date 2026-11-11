# PMS to approved PCMC catalog traceability

Authority: the user's 11 November 2026 feature brief; existing I-06 reuse and
S3D-REL-01 release boundaries apply. No production deployment or engineering
approval is implied.

Migration `0018_component_pms_source` adds a literal PMS code, source locator JSON,
and read status to components. Apply migrations before running the updated web
and extraction workers. Missing legacy source evidence is unknown; it is not
backfilled from generated identifiers or reference documents.

Local runtime reconciliation: migration `0019_legacy_source_references_default`
preserves a legacy plural `source_references` JSONB column where it exists and
gives omitted inserts an empty-array database default. A database can report no
pending migrations while retaining this extra NOT NULL column from another schema
revision. Fresh schemas without that historical column are unchanged. Existing
values and the NOT NULL constraint are retained. A transactional PostgreSQL insert
probe verified this failure path; its inserted row was rolled back.

Native LNG grids recognize wrapped COMMODITY CODE headers. Values come only
from that cell, retaining raw cell text and page/table/row/bounding-box evidence.
Part symbols, ERP numbers and REF DOC identifiers remain separate source evidence.
The API exposes `pms_commodity_code`, `source_reference`, and `source_read_status`.

The existing reference upload supports the PCMC control-data worksheet with
CONTRACTORCOMMODITYCODE and descriptions. Existing project ownership, file hashes,
reference pinning, freeze and release authority checks remain in force. The local
approved workbook is verification input; it does not overwrite a frozen project
revision or grant release authority. Industry codes are not substituted for blank
contractor codes. Approved catalog identifiers are never padded or reformatted.
The 18–20 character/no-whitespace rule applies to new generated project codes.
Literal PMS and approved catalog identifiers are retained as supplied.
Confirmed contractor codes are not copied into blank industry-code fields.
Missing geometry/industry identifiers still block the existing CAT release gates.

Matching checks exact populated PMS codes first, otherwise proposes candidates
from structured attributes and explicit engineering tokens in descriptions.
Existing codelist aliases and reviewed selections are reused. Description similarity
ranks candidates after engineering comparisons. Unknown attributes are displayed
as unknown. Known conflicts prevent confirmation, including remapping. Range
coverage and gaps are recorded; a single candidate cannot be confirmed across an
unproven size range. Large candidate sets show their total and the first 30 ranked
records. Duplicate source decision keys stop matching rather than conflating rows.

The review response adds catalog revision and SHA-256. Candidate evidence includes
catalog row, matched/unknown attributes, differences, coverage and confirmability.
Existing confirmation records the reviewer and timestamp. Unchanged matching
reruns preserve the confirmed selection; changed evidence invalidates it.

Following the user's supplied review workbook and explicit generation request,
blank PMS cells now receive project-generated CommodityCode values in preview and
draft exports. The old RAD/component/spec/nominal-size pattern is compacted to
alphanumeric text; ordinary values such as RADCAP10A0600N0050 contain 18 characters.
Long/short patterns and distinct engineering variants sharing a base use a stable
20-character digest form. Source IDs and timestamps do not seed these identifiers.
The literal PMS value and locator remain separate source evidence. Generated codes
are not catalog confirmations. Confirmed PCMC Code is not a visible field or an
extra worksheet column: the approved PCF template's columns remain unchanged.
Frozen SPEC exports still use the reviewed mapping under existing release gates.
Required mappings are
checked by the existing validation/freeze gates. Pilot and release approval remain
required. Previously requested PCF blank fields, FluidCode=1, PipeBranch angle/code
fields and Tap-001 formatting are applied in the shared preview/export path.

PipeBranch restoration uses the 148 explicit size-pair/primary-ShortCode records
from the user-approved Sample-Template PCF File-1AA1B.xlsx. The bundled JSON records
the source filename and SHA-256; no Windows-local path is required at runtime.
Each extracted specification receives that matrix with its own SpecName. Header
and branch sizes, units, and primary codes remain the approved matrix values.
Per the user's explicit override, both AngleLow and AngleHigh are 90.5deg (the
source AngleLow was 89.5deg), and SecondaryShortCode/TertiaryShortCode remain blank.
These rows are template reference data, not extracted PMS branch instructions or
new class-specific engineering approvals; existing release/pilot gates remain.
PermissibleTaps restores one row per specification with Tap-001, retaining the
existing IsPreferredTap=1 setting shown in the user's screenshot. This request
does not expand the sheet to the template's additional Tap-002 through Tap-037.

Verification on physical page 779 of PMS_FI4VsV1.pdf, spec 10A06.00, extracted 14
source rows with blank commodity cells. ITN14200 and ITN82034 remained REF DOC
values. The approved Sample Template -2-CatalogDataExtractor_PCMCD.xlsx contains
72,598 records. Its missing size/engineering fields leave range coverage unknown;
these real source rows require engineering review rather than automatic assignment.

Software checks cover exact lookup, attribute conflicts, multiple candidates,
coverage gaps, unknown coverage, canonical selections, and confirmed blank-source
mapping persistence across API reload/rerun. These checks do not certify Smart 3D
import acceptance or approve any candidate on the real project.
