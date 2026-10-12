# AI credential consumer coverage

Implementation inventory checked on 30 September 2026. The administrator registry and API contract are documented in [ADMIN_AI_API_KEYS.md](ADMIN_AI_API_KEYS.md). This inventory describes credential integration, not a certification of every engineering extraction result or provider model.

## Shared operation boundary

`apps/core/ai_consumer_clients.py` provides explicit synchronous SDK proxies. Constructing a proxy, or saving a nested endpoint, performs no registry query and opens no network connection. Each SDK operation resolves the currently selected credential and matching managed metadata from one registry snapshot. Rotation and disabling therefore apply to reused clients and worker processes without rebuilding a client singleton. An in-flight provider request completes with the credential with which it started.

Managed credentials use official provider hosts. OpenAI ambient organization/project values and Anthropic ambient bearer authentication are cleared; custom consumer HTTP clients and headers cannot redirect a managed credential. Gemini uses the public Gemini endpoint, not an ambient Vertex project. Module-specific models, timeout/retry arguments and generation options retain their existing contracts. Legacy options are used only when that provider has no central configuration.

Ordinary responses close their SDK client immediately after receipt. Streams retain their client through iteration or context-manager exit. The wrapper sanitizes provider failures and suppresses SDK/HTTP diagnostic logs only within the active provider operation. It does not monkeypatch provider packages, mutate environment variables, or globally mute unrelated request logs.

`provider_available` is an operation-time availability check for existing validators and dispatch gates. It must not be used to copy the central key into request data, model fields, task arguments or job metadata. Actual worker services resolve credentials when they run. Existing legacy request/project key payloads are retained only for unmanaged compatibility; centrally managed frontends omit those inputs.

## Integration inventory

The table covers 89 application consumer files, plus the shared observed-client factory. Sales and Planning retain their dedicated provider adapters and are documented separately in the registry feature brief.

| Consumer family | Existing supported providers | Representative integration points |
| --- | --- | --- |
| CRS comment cleaning | OpenAI | `crs_documents/helpers/comment_cleaner.py`: `CommentCleaner`, including the reused cleaner singleton |
| Dashboard assistance | OpenAI | `dashboard/tasks.py` |
| DesignIQ extraction | OpenAI | `designiq/pid_ocr_extractor_v2.py`, `designiq/tasks.py` |
| Electrical checklist handwriting | OpenAI | `electrical_checklist/handwriting_extractor.py`: `HandwritingExtractor` |
| Electrical datasheets | OpenAI | `electrical_datasheet/ai_quality_checker.py`, equipment generators and `views.py` |
| Finance classification | OpenAI | `finance/services/ai_classifier.py` |
| HR assistant | OpenAI | `hr_core/assistant.py` |
| Instrument tools | OpenAI | `instrument_tools/ai_smart_parser.py`, `ai_header_mapper.py`, `ai_explainer.py` |
| Instrument IO workflow | OpenAI, Anthropic | `instrument_io_workflow/services/pid_vision_extractor.py`, orchestrator, tasks and connection validator |
| Non-TEFF metadata | OpenAI, Gemini | `non_teff_metadata/services/vision_extractor.py`, `ai_recommendations.py` |
| PFD analysis | OpenAI | `pfd/services/pfd_analysis_service.py` |
| PFD converter | OpenAI, Anthropic | 15 converter/generation/learning modules, including nine module-level lazy clients |
| PID analysis | OpenAI, Gemini | `pid_analysis/services.py`, `multi_model_service.py`, equipment/instrument/RAG services |
| PID checker V2 | OpenAI, Anthropic | Vision, equipment/instrument/symbol extraction, cross-check services and request validators |
| PID verification V1 and V2 | OpenAI, Anthropic, Gemini | Extraction/legend/naming/comparison services, orchestrators, workers and mode validation |
| Process datasheets | OpenAI, Gemini | `process_datasheet/ai_provider.py`, PID/HMB extraction, pump extraction and base AI agent |
| Procurement | OpenAI | `procurement/services/po_ai_extractor.py`, `pr_pdf_handwriting.py` |
| PaperSpec customization | OpenAI, Anthropic, Gemini | `spec_customization/services/extraction_service.py`, project-BYOK resolution and diagnostics |
| Wrench integration | OpenAI | `wrench_integration/service.py` |
| Usage telemetry factory | OpenAI | `rbac/ai_telemetry.py`: `observed_openai`, including imports aliased as `OpenAI` |

The existing consumer alias `claude` resolves Anthropic credentials. A key for one provider does not make another provider's SDK or specialized extraction mode usable. Existing explicit provider/model choices remain in force. Existing automatic multi-provider workflows keep their configured modes and order; this change does not introduce arbitrary cross-provider model substitution.

PID verification's older direct-HTTP extraction engines resolve keys through properties at call time, use fixed official HTTPS endpoints and disable redirects. Their keys are not captured in worker dispatch payloads. Modern Gemini consumers use the already installed `google-genai` package; legacy global `google.generativeai.configure` state was removed from these application paths.

## Valve MTO local extraction

The Valve MTO page defaults to `extraction_mode=local`; `vision` remains an
explicit alternative and the compatibility default for API clients that omit
the parameter. Local mode never resolves an AI credential or calls a provider.
It requires Tesseract and the existing `opencv-python-headless` dependency.

In AI Vision mode, Valve MTO offers OpenAI and Claude (Anthropic) using the
shared server-provider status display and central administrator registry.
Central mode sends `vision_provider` and `vision_credential_mode=central`,
never an API key, and disables submission when the selected managed provider
is unavailable or its status cannot be checked. Central disabling/registry
failures never fall back to another credential or provider.

Valve MTO also offers an explicitly selected personal BYOK mode
(`vision_credential_mode=byok`, `vision_api_key`). This mode uses exactly the
supplied key independently of central configuration, including a disabled
central provider, with no automatic central/environment fallback. The key
is held only in page/worker memory, not browser storage or job snapshots,
and clears on reload or provider/credential-source changes. BYOK requests
use the existing sensitive-request logging boundary. A synthetic Test API
key action checks that same personal key without sending drawings/legends;
it may incur a small provider charge. Both provider SDKs retain sanitized
errors, private diagnostic logs, official hosts, ambient-auth isolation and
client/stream cleanup through `personal_provider_client`.

Both AI modes include active private Valve MTO legend text and individual
valve/regulator symbol pictures. Pictures are selected by scope,
deduplicated by caption/image content and resized to at most 256 pixels per
side before JPEG encoding. Non-valve equipment and composite assembly
pictures are excluded, as in local detection. Drawing tiles and captioned
legend reference pictures have explicitly different roles: only drawing
valves are counted, and uncertain types/specifications stay unassigned.
An unreadable active picture fails extraction explicitly instead of silently
dropping the legend. No picture-count cap silently discards symbol families.
Local extraction remains available independently of either credential mode.
Legacy API key payloads in central mode remain unmanaged compatibility only.
Existing module model settings remain unchanged. AI Vision sends drawing
images and legend context to the selected external provider only on submission;
provider readiness does not certify extraction accuracy. Uploads in BYOK
mode still require explicit submission, module access and engineering review.

### Native detail extraction and review

AI Vision's default thorough mode clips the original PDF before rendering.
Adaptive regions target `VALVE_MTO_DETAIL_DPI` (240 by default), bounded by
`VALVE_MTO_REGION_EDGE` (1568 pixels). Overlap supplies pipe/tag context;
each region has a non-overlapping ownership core. One AI pass is made per
region, so dense A0 drawings can require substantially more calls than the
former eight low-resolution calls. Quick mode retains the whole-page preview.
Region-specific Tesseract at all four cardinal rotations and PSM 6/11
supplies text hints, not image overlays or guessed labels.
Faint neutral CAD strokes are darkened before JPEG encoding; coloured
scope/revision linework and the original source PDF remain unchanged.
Returned normalized valve-body rectangles map to displayed PDF points,
respecting rotation; context-only overlap sightings
are excluded. Missing locations remain low-confidence count-review candidates.
Different physical locations are not merged just because type/size match.

Both engines read exact, private active project lookups from `piping`,
`line_list`, `scope_symbols` and `limit_line`. Field keys `spec`,
`piping_class`, `pms_class` or `material_class` supply material descriptions;
`rating` and `facing` supply the corresponding exact-code mappings.
A structured lookup value can supply all three, for example
`{"AC3N": {"pms_class": "CARBON STEEL", "rating": "150", "facing": "RF"}}`,
**only when supported by the actual project PMS**. An `area` lookup must
map an exact line or P&ID identifier to `ISLAND`/`FIELD`; boundary symbol
names alone do not establish installation area. Conflicting active mappings
fail explicitly; conflicts with existing row values are retained and flagged.
OCR class codes are never corrected by guessing similar letters/numbers.
`AREA` is the complete `DRG. TITLE` / drawing title from the sheet's bottom
title block (left or right), not ISLAND/FIELD. Both extraction engines read
native title-cell text first and use rotated title-block OCR for raster sheets.
The verified page title overrides region responses, including when the title
block lies outside that region. Titles are editable free text and persist up
to 512 characters. Multi-line titles are joined in reading order without the
adjacent project, drawing number, location or revision fields.
Installation allocation is separate, using the existing `tab` field; exact
project `area` lookups populate allocation, never overwrite the drawing title.
UI tab filters and exports follow allocation, not words in a title.
Unknown installation allocations have zero allocated quantities, and each physical candidate has
an observed count of one. Observed counts are not approved procurement counts.
When nominal size is blank, an explicitly quoted leading line-number size
fills it: `4"-PL-DC3N-8198-P` gives `4"`. Integer, fractional and mixed-fraction
inch sizes are supported. Explicit valve sizes are preserved, and unquoted
sequence prefixes (for example `604-LFG-...`) are not treated as sizes.
This applies to extraction, loaded records, imports, saves and exports.

The page's dedicated **Extraction Review** workspace shows actual record
coverage for area, material class, rating/facing, connected line, tag and size.
A compact register notice links there instead of printing every warning.
Grouped findings expose next actions; method evidence, non-valve exclusions
and complete original audit messages remain available in expandable panels.
Populated values are not labelled verified automatically. Existing saved
drafts are not silently regenerated or overwritten by this change.

Import `.xlsx`/`.xlsm` through Valve MTO's own Manage Legends dialog. Its
`/api/v1/valve-mto/legends/workbook-preview/` and `workbook-import/` endpoints
reuse workbook parsing while storing cells, merged ranges and PNG data URLs
in the requesting user's isolated `ValveMTOLegend.definition`. They do not
write P&ID legends, shared symbol records or global archives. Unsupported
sections are initially mapped to piping references and can be remapped in
preview. Composite ESD/actuator assemblies and non-valve equipment pictures
(including tanks) are preserved for viewing but are not individual-valve
detector templates. Only captions identifying valves or regulators are used.

Local extraction combines spatial, overlapping-tile Tesseract (300 DPI,
PSM 6/11 and four rotations in thorough mode) with conservative reference
image matching. Opposing filled triangular lobes can establish a bow-tie
valve candidate even when reference fill/raster style differs, but the
type remains `UNIDENTIFIED` rather than guessing a project-specific meaning.
Coordinates, image evidence and association cautions appear in each row.
Embedded text and neutral, axis-aligned CAD pipe segments are transformed
using the PDF page rotation into the same displayed PDF-point frame as OCR
and rendered symbols. Vector segments supplement raster pipe tracing.
OCR line labels normalize typographic dashes and inch quotes without
guessing missing characters. Nearby annotations must also align with the
valve's pipe axis and connect to the traced network;
ambiguity leaves the line unassigned. Material descriptions are only
resolved through an explicit imported specification-code lookup.

Each local candidate has `qty_observed=1`, LOW confidence and a review
remark. Unknown area allocations, ratings, facing and material remain
unassigned. Observed quantities must be non-negative whole numbers or null;
invalid bulk saves return HTTP 400 before replacing existing rows.
Observed quantities persist and export separately from purchase
quantities; the server export includes an UNASSIGNED REVIEW sheet. Local
output is not an exhaustive/approved MTO, and light-outline, actuated,
unusual or poorly scanned symbols can be missed. Engineer verification of
both matches and missed valves is required before procurement.

## Inspection and verification limits

- The application scan found no remaining unwrapped OpenAI/Anthropic/GenAI SDK construction or global provider configuration in the scoped application consumers. Sales and Planning use their separately reviewed adapters.
- No asynchronous SDK client or GenAI `.aio` consumer exists under `apps`. The shared proxy supports the synchronous interfaces currently used; a future asynchronous consumer requires explicit async lifetime support.
- Existing consumers call SDK operations through nested resources. No consumer was found reading SDK credential/configuration attributes such as `.api_key`, `.base_url` or `.is_closed` from these proxies. The proxy is an operation interface, not a general reflection replacement for an SDK client.
- The pre-existing `designiq/pid_ocr_extractor.py` contains a syntax error and is not repaired by this credential change. Inspected routes and tasks import `pid_ocr_extractor_v2.py`, which is integrated. Standalone examples, maintenance scripts, cloud-storage/OCR infrastructure credentials and obsolete unused code are outside this provider registry integration.
- `apps.core.tests.tests_ai_consumer_clients` covers rotation, disabling, failure without legacy fallback, official-host and ambient-auth isolation, nested endpoints, stream lifetime, private logs, provider rejection, late configuration of a singleton, the telemetry factory, and missing-user-key extraction paths. Real installed OpenAI/Anthropic SDKs use mock HTTP transports; tests send no business data or real provider requests. The Anthropic mock uses the installed SDK's HTTP transport (`httpx` or `httpx2`).
- The consumer suite is verification of credential routing and integration boundaries. Live provider billing, model availability, engineering accuracy, browser workflows and production deployment require their own evidence.
