# PMS to approved PCMC catalog traceability

Authority: the user's 11 November 2026 feature brief; existing I-06 reuse and
S3D-REL-01 release boundaries apply. No production deployment or engineering
approval is implied.

Migration `0018_component_pms_source` adds a literal PMS code, source locator JSON,
and read status to components. Apply migrations before running the updated web
and extraction workers. Missing legacy source evidence is unknown; it is not
backfilled from generated identifiers or reference documents.

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

PCF preview retains the PMS code and exposes a distinct ConfirmedPCMCCode. Draft
source downloads retain the literal source code; frozen SPEC exports use only the
confirmed mapping in the template CommodityCode column. Required mappings are
checked by the existing validation/freeze gates. Pilot and release approval remain
required. Previously requested PCF blank fields, FluidCode=1, PipeBranch angle/code
fields and Tap-001 formatting are applied in the shared preview/export path.

Verification on physical page 779 of PMS_FI4VsV1.pdf, spec 10A06.00, extracted 14
source rows with blank commodity cells. ITN14200 and ITN82034 remained REF DOC
values. The approved Sample Template -2-CatalogDataExtractor_PCMCD.xlsx contains
72,598 records. Its missing size/engineering fields leave range coverage unknown;
these real source rows require engineering review rather than automatic assignment.

Software checks cover exact lookup, attribute conflicts, multiple candidates,
coverage gaps, unknown coverage, canonical selections, and confirmed blank-source
mapping persistence across API reload/rerun. These checks do not certify Smart 3D
import acceptance or approve any candidate on the real project.
