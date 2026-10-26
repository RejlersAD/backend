# S3D local extraction and acceptance

Authority: S3D-LOCAL-01, the user's five-task continuation on 26 October 2026;
workspace brief `../../docs/features/s3d-local-extraction.md`.

## Implemented boundary

Existing upload, preview, matching and controlled-release APIs remain in place.
No migration, approval grant, production rollout or paid extraction is included.

PDF text is read first. Supported tables have an explicit class heading and
recognised, positioned or pipe/tab-separated component/description, size and
engineering-attribute columns. The BUHASA nominal-size/item-code matrix is
identified by PIPING COMPONENTS, Nominal pipe size, a unique class heading and
explicit Metric/Imperial units. Populated cells produce nominal-size candidates.
Metric nominal sizes retain DN, imperial retain NPS; no conversion occurs. Item
codes and cell codes remain separate source evidence, never commodity codes.

Generic tables require an explicit class on each page. The bounded LNG
continuation exception is specified below. Unsupported layouts, ambiguous rows and
unlabelled continuation pages are not guessed. Joining COMPONENT MATERIAL
DESCRIPTION pages to item-code matrices remains unsupported and is reported.
Missing material, schedule, ends and class metadata stay blank. Local rows bypass
legacy inferred engineering defaults, fuzzy deduplication and size expansion.
Source page/row and matrix codes survive in component notes; exact table/cell
polygons are not claimed.

### DGS appendix recovery (26 October 2026)

The user's failed `0500867-SP-00-LA-0076_C2_PMS.pdf` upload also requires
positioned DGS-1300-010 appendix tables. These now support an explicit five-digit
class on the same appendix heading, two-line Size From/To headers, multiline
descriptions, and blank short codes. Column positions come from that page's
headings. Short/stock codes, rating and notes remain source evidence; material
text remains in the literal description rather than an inferred material field.
Multiple size lines and unsupported component rows are flagged for review.
DGS source page/row identities prevent overlap duplication while preserving
separate source rows. Image pages with little native text explicitly require OCR.
Partial outcomes retain the retry/export warning even when components are saved.

The source's SHA-256 is
`1994a0580aae0ce82c8f914b4c2054569f78081281b5a06ae3fb2979a10dd924`.
A local-only recovery of its existing document saved 526 component candidates
across six classes, plus retained header-only classes (31 classes total).
Completeness remains partial: 232 ambiguous rows and 15 image pages require
review/OCR. No paid provider, engineering acceptance or release is implied.
See the workspace brief for test and local activation evidence.

### ADNOC LNG native grids

Authority: the user's explicit LNG extension request; workspace brief
`../../docs/features/s3d-lng-parser.md`. The uploaded 2,335-page PMS.pdf uses
A-100-A4Z-0-25020 grids with PART NAME/SYMBOL, SIZE FROM/TO, W.T., DESCRIPTION
and either ME NO. or COMMODITY CODE. Native table-cell rectangles resolve shared
values only within explicit merged cells. Separate blank cells remain blank.
Curly inch marks and spaced/mixed fractions validate without converting the
literal source notation. Explicit BELOW/ABOVE constraints retain an open bound;
a single stated bound is kept without inventing its counterpart and flagged.
PIPING and FITTINGS are recognised source categories. Other literal categories
retain component_type=other with a classification-review issue.

A continuation must have recognised LNG headers, the same document identity and
logical page total, and consecutive logical and physical pages. A non-table page
or ambiguous heading breaks context. Chunk-start lookup verifies the same chain
back to an explicit class heading, bounded by SPEC_LNG_CONTEXT_LOOKBACK_PAGES
(default 64, range 1-200). It never carries a component name/description from a
neighbouring page into an unlabelled source cell. Those rows remain review issues.
Malformed or mixed explicit headings also block continuation rather than carrying
the preceding class. Class codes respect the existing 32-character boundary.
Note-qualified headings retain their literal note and require review.

Source page/table/row, class-heading page and merged parent-row references survive
in component notes. Native rectangle evidence is also available in the transient
source-reference object; geometry is not added to the database schema. Explicit
ME numbers and commodity codes remain distinct source evidence in notes. They are
not generated or interpreted as matching/release approval. LNG source row identity
deduplicates chunk overlaps while retaining separate source rows.

Native grid extraction stays inside the existing hard-deadline child process.
Other local/OCR/provider fallback and source-only quality handling remain. Missing
grid headers, ambiguous class context, rejected rows, references such as AS ABOVE,
unknown classifications and incomplete bounds retain partial coverage and block
the existing release/export gates. This extension adds no schema or paid retry.

RapidOCR, PaddleOCR and Tesseract preserve word positions and use the same table
reader. Header-only OCR cannot mask later component extraction. RapidOCR requires
packaged ONNX assets. PaddleOCR requires preinstalled detection/recognition model
directories in SPEC_PADDLE_DETECTION_MODEL_DIR and SPEC_PADDLE_RECOGNITION_MODEL_DIR;
automatic model downloads are not requested. Tesseract requires its binding,
executable and language data. Missing installations are skipped. Configured cloud
providers may run when local extraction cannot produce components; explicit BYOK
retains provider routing.
Partial local results can also reach fallback, but their candidates and reported
coverage gaps are retained alongside provider output. A provider response alone
does not resolve a rejected local row or confer coverage approval.

GET /api/v1/spec-customization/config/ adds `local_extraction` installation checks
and `processing_limits`. These are server checks, not worker health or engineering
accuracy certification. Web and worker installations must agree. The banner uses
these checks, falls back to unverified availability and explains cloud fallback.

## Limits and retries

- Effective chunk concurrency is one: shared provider budgets remain sequential.
- SPEC_CHUNK_TIMEOUT_S defaults to 300; SPEC_JOB_TIMEOUT_S defaults to 3600.
- Local native/OCR stages run in child processes with the remaining chunk/job
  deadline; expiry kills and waits for the child. The deadline spans retry delays.
  Image rendering respects max_image_size.
- OpenAI/Anthropic use remaining request time and zero SDK retries. Gemini uses
  remaining HTTP timeout and one attempt. Paid pages are reserved before calls,
  including failed calls and escalation. Local OCR remains available at zero
  remaining paid-page budget.
- SPEC_RETRY_MAX defaults to three local retries (0–5), with exponential delays
  using SPEC_RETRY_BACKOFF_BASE (default two). No chunk replay follows any paid
  attempt. Missing installations and unsupported/empty tables are not transient.
- Overall elapsed-time checks and Celery soft/hard limits remain. Native parent
  PDF rendering and HTTP transport timeout semantics are not a separate universal
  wall-clock process kill; local subprocess timeouts are hard bounds.
- Job snapshots retain attempts, engine errors and local table issues. Rejected
  local rows mark partial coverage, retaining review and blocking existing
  bulk-load export/release gates. Zero components means failed. Keys are cleared
  on every task exit, including cancellation/early failure.

## Representative source and independent labels

User-selected source: P12531-30-99-12-0601-2.pdf, found in Downloads under
ADNOC_ONSHORE___SAMPLE_DATA. It has 587 pages and SHA-256
`5a78671cc8885d318ef4ba7dbcc1d7139a98596f942b029fc15e9c28baa06d60`.
Neighbouring reference workbooks exist; presence does not establish approval.

```powershell
python scripts/benchmark_s3d_local.py --source '<source.pdf>' --output '<report.json>'
```

This read-only probe records literal candidates, source locations, counts and
issues without DB/provider access. Add `--labels '<reviewed-labels.json>'` for an
independent comparison. Labels must identify the exact source hash, contain
label_status=engineering_reviewed, reviewer, reviewed_at and nonempty components.
Each component supplies class_code, component_type, description, size_from,
size_to, material_standard, schedule_or_rating and end_connection, including
explicit empty values. Labels must cover the whole evaluated file or an explicitly
scoped, separately hashed excerpt. Comparison records matches/missing/unexpected;
it never fabricates expected results or publishes accuracy percentages.

Workspace artifacts include the probe JSON, candidate CSV and empty unreviewed
labels template. Candidate output is deliberately separate from expected labels.
Two local native-text probes of the selected source returned 10,539 candidates
across 38 class codes. The final run took 167.37 seconds and reported 133 issues
(46 material joins, 81 unsupported/empty matrix rows, four ambiguous classes,
two missing unit systems). There are no supplied independent engineering labels.

Historical implementation verification: the initial scoped backend run passed 141 tests with six skips;
24 focused checks passed after the last partial-fallback preservation changes.
Two isolated component browser checks, scoped lint and whitespace checks passed.
At that stage the full application build did not complete. Test settings used SQLite and a
workspace-only Django 5.2.18 overlay to bypass an existing Django 5.0/model-API
version mismatch; this is not verification of the installed deployment runtime.
The workspace feature brief lists exact commands, test-hasher boundary and logs.

The subsequent Development alignment fixes the Django 5.0 compatibility issue
and validates the actual pinned runtime and frontend build. See
[the current release verification](RELEASE_20261026.md) for the final results
and separate PostgreSQL migration audit.

## Disposable-target handoff

Actual execution needs target access, agreed SP3D version/import profile,
approved reference revision and engineering reviewer/release authority.

1. Resolve coverage, fields, matching and validation with engineering. Configure
   recorded decisions through existing controlled-release settings, then freeze
   through the guarded API. Source selection does not approve reference data.
2. Download SPEC/CAT through the released endpoints, save the frozen release
   response and check local hashes with
   `python scripts/check_s3d_pilot_package.py --release release.json --spec SPEC.xlsx --cat CAT.xlsx`.
   This does not replace live release freshness/authority checks.
3. Import those exact bytes into the agreed disposable target/profile. Retain
   its actual import/rejection reports and version/profile/release identities.
4. Record actual outcome and rejection counts via existing `import-attempts/`.
   Nonplanned evidence requires a target report (maximum 5 MB). Reconciliation
   references the original attempt; preserve original evidence.
5. Engineering reconciles rejected/missing/changed rows against source/reference.
   Changed inputs require a new validated freeze and target rerun. No connector,
   synthetic report or local unit test substitutes for actual import acceptance.
