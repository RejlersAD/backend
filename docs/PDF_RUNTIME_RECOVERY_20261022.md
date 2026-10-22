# PDF-RUNTIME-01 — production route-loading failure

Authority: the user supplied the production console failure after the HR release
and restored Railway access for investigation on 22 October 2026. This repair
follows D-01/I-06: preserve working architecture and established behavior.

Context consulted: workspace README, PROJECT_BRIEF, REPOSITORY_GAP_REPORT,
DECISIONS, ARCHITECTURE, DATA_MODEL, API_CONTRACTS, SECURITY, CONTRIBUTING,
HR-TA-01, HR-REC-02 and HR-REC-03; backend AGENTS, Railway/Docker startup,
runtime logs and current dependency declarations.

## Observed failure

Railway deployment 6689ca04-b90f-438b-8221-795e4086e1d2, commit f9671415,
loads config.urls -> pid_analysis.urls -> services -> fitz -> pymupdf.layout.
ONNX session initialization fails with pthread_create error 11, Resource
temporarily unavailable. Retrying URL resolution repeats the failing import.
The user observed recruitment HTTP 500, profile HTTP 503 and widespread API
timeouts. Independent production probes connected/TLS successfully but received
no backend response within 12–25 seconds; the frontend returned HTTP 200.

The former pymupdf>=1.24.0 declaration allowed an unreviewed major upgrade.
PyMuPDF 2.0 integrates Layout and initializes it on import. The deployed traceback
matches that new initialization path. Local PyMuPDF 1.28.2 has the established
API without that automatic Layout import.

## Repair and boundaries

Pin PyMuPDF to the locally tested 1.28.2. Keep all PDF consumers and their
existing extraction behavior. Add scripts/check_pdf_runtime.py to verify a fresh
fitz import does not initialize ONNX, and that PDF text extraction and page
rendering work. No application route, permission, model, API, frontend or
Sidebar changes. No global inference-session monkeypatch or timeout increase.
The Docker image build runs the same smoke check before it can be deployed.

Local verification: the fresh import took 0.278 seconds, PDF text round trip and
rendering passed, and no ONNX initialization occurred. pip check reported no
broken requirements. All 32 focused candidate/register/Careers/form regressions
passed using config.settings_candidate_forms_test. Live recovery is a separate
post-deployment check, not implied by these local tests.

PyMuPDF upstream documentation:
https://pymupdf.io/blog/pymupdf-2-0
https://pymupdf.readthedocs.io/en/latest/installation.html

## Production migration evidence

The deployed pre-deploy logs show HR migrations 0015 through 0022 and payroll
0024/0025 completed with OK. The incident traceback concerns URL import and
thread creation, not missing HR columns. This fix adds no migrations. A separate
pending-plan check and authenticated production HR smoke check remain necessary
before claiming complete production recovery.

Private logs are retained outside Git under workspace
artifacts/production-hr-recovery-20261022/. They are not copied into the repository.
