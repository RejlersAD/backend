# S3D default SPEC profile - 27 October 2026

Authority: S3D-EXPORT-01, the user's explicit selection of the supplied
1AA1B.xlsx structure as the default for future SPEC exports. Shared scope:
../../docs/features/s3d-spec-template-comparison.md.

The default workbook preserves the reference's 50 sheet names/order, Head row
headers/column order, Start/End markers, and formatting. Example engineering rows
and origin-machine metadata are removed. A blank styled data exemplar supports
new rows without importing reference values. No reference commodity code or
engineering approval becomes a class default from this template selection.

smartplant_spec_profile.json records the schema and aliases to established API
field keys. Canvas rows and saved overrides keep their canonical field names;
Excel columns retain the user's original header case. Workbook writing matches
headers without case sensitivity. Preview builders absent from the selected
schema do not create extra sheets; their names are reported as
excluded_legacy_sections, with original source data retained in review exports.

The legacy 25-sheet file remains available at smartplant_spec_legacy_template.xlsx.
SPEC_TEMPLATE_PATH can explicitly select an alternate file. CAT structure is
unchanged. The preparation script accepts a reference/legacy/output path and
does not modify the user's reference workbook.

The raw XLSX endpoint remains a class-per-sheet review export. Its UI label and
download filename now identify it as review data. Use Draft SPEC for the standard
template. Existing partial-coverage, frozen-release, matching and permission
guards remain; template selection does not authorize export of partial work as
an approved/released SPEC. Template changes invalidate existing input fingerprints
under the established release rules; no historical data or approvals are rewritten.

Validation: all 199 isolated S3D regressions completed successfully (six existing
skips), including blank-template preparation, input preservation, case-insensitive
writing, canonical preview/override keys, exact 50-sheet output and release guards.
The legacy MaterialsData allowable-stress regression now selects the retained
legacy profile explicitly. Frontend scoped lint and production/PWA build passed.

The source comparison report in the user's Downloads contains 11 review sheets.
AA1B has 153 raw source rows against 519 expected PipingCommodityFilter entries.
379 expected entries have a family/first-size candidate and 140 do not. Secondary
sizes remain unverified for 258 expected entries. Material Std and End Conn.
columns are blank in all 153 source rows; descriptions may retain literal facts.
Five source rows repeat PART NAME headers. These findings are not an accuracy
percentage or engineering approval. No reference code was assigned to extracted
rows and neither user-supplied workbook was modified.
