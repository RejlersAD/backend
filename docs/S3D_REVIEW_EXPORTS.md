# S3D review workbook downloads - 27 October 2026

Authority S3D-EXPORT-02: the user reports Draft SPEC.xlsx still does not download
and explicitly requests implementation of Export workbooks (download). Inspection
confirmed the existing completeness gate rejects partial jobs before draft=1 is
handled. S3D-EXPORT-01 changed layout but intentionally retained that gate.

Add an explicit draft=1&review=1 attachment mode for completed jobs containing
component rows. This mode may include partial/unverified coverage; it creates no
release, grants no engineering approval and does not resolve any extraction gap.
Non-review draft and frozen/released exports retain existing completeness,
matching and authority requirements. Running/empty jobs and review requests
combined with release_id are rejected. Ownership checks remain unchanged.

Review SPEC retains the exact 50-sheet template and uses Revision History for a
visible not-for-import notice, source identity, coverage and failed page ranges.
Review CAT retains its catalog sheets and adds a Review Notice tab. Filenames and
response headers identify review state. Audit records use review_export.

The menu separates template review workbooks from class-per-sheet raw data and
JSON. XLSX responses are checked for ZIP content before download, and Blob JSON
errors are decoded into visible server explanations. A review download cannot
be represented as an approved/released engineering workbook.

Verify actual generated attachments, exact SPEC names/headers, coverage notices,
raw/JSON downloads, wrong-owner denial, noncompleted/empty rejection and unchanged
frozen-export guards. Do not copy reference codes or fill extraction gaps.

Validation: 205 backend regressions passed (six existing skips), plus the six
review endpoint tests rerun after adding audit assertions. Browser check
check-s3d-downloads.mjs clicked all four real menu actions with synthetic HTTP
responses, parsed every downloaded XLSX and verified Blob error explanations.
Node Blob tests and scoped ESLint passed. Actual saved local PMS job
17adaef1-118f-421a-9668-84e70f55debd generated SPEC (50 sheets, 8,837,996 bytes,
98.92 seconds) and CAT (29 sheets including notice, 8,247,433 bytes, 52.14 seconds).
Both explicitly report partial coverage; files were copied into Downloads.
No extraction data or reference engineering values were changed.
