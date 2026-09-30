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

## Inspection and verification limits

- The application scan found no remaining unwrapped OpenAI/Anthropic/GenAI SDK construction or global provider configuration in the scoped application consumers. Sales and Planning use their separately reviewed adapters.
- No asynchronous SDK client or GenAI `.aio` consumer exists under `apps`. The shared proxy supports the synchronous interfaces currently used; a future asynchronous consumer requires explicit async lifetime support.
- Existing consumers call SDK operations through nested resources. No consumer was found reading SDK credential/configuration attributes such as `.api_key`, `.base_url` or `.is_closed` from these proxies. The proxy is an operation interface, not a general reflection replacement for an SDK client.
- The pre-existing `designiq/pid_ocr_extractor.py` contains a syntax error and is not repaired by this credential change. Inspected routes and tasks import `pid_ocr_extractor_v2.py`, which is integrated. Standalone examples, maintenance scripts, cloud-storage/OCR infrastructure credentials and obsolete unused code are outside this provider registry integration.
- `apps.core.tests.tests_ai_consumer_clients` covers rotation, disabling, failure without legacy fallback, official-host and ambient-auth isolation, nested endpoints, stream lifetime, private logs, provider rejection, late configuration of a singleton, the telemetry factory, and missing-user-key extraction paths. Real installed OpenAI/Anthropic SDKs use mock HTTP transports; tests send no business data or real provider requests. The Anthropic mock uses the installed SDK's HTTP transport (`httpx` or `httpx2`).
- The consumer suite is verification of credential routing and integration boundaries. Live provider billing, model availability, engineering accuracy, browser workflows and production deployment require their own evidence.
