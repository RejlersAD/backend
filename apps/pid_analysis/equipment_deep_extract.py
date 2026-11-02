"""
Phase 2 — BYOK AI Deep Extraction for the Equipment List (ADDITIVE module).

This module is intentionally self-contained: it does not modify the
deterministic OCR/regex pipeline in equipment_analysis_views.py.  It is a
second, optional pass that the user triggers manually from the Equipment List
workspace when the deterministic pass missed records.

Flow
----
1. Frontend re-POSTs the source P&ID (multipart `file`) together with the
   user's model selection (`model`) and the phase-1 `upload_id` for merging.
2. Pages are rendered to images and sent page-by-page to the selected vision
   model with a strict-JSON extraction prompt (all equipment data boxes,
   including unit-prefixed tags inside two-column TAG NUMBER boxes and data
   surrounded by red revision clouds).
3. The provider credential is resolved per call through the managed
   credential registry (BYOK) via lazy_provider_client — keys are never
   accepted from, or returned to, the browser.
4. Results are merged into the cached phase-1 result entry (same upload_id)
   so the standard /results endpoint returns the combined register.

Soft-coded: everything lives under the "deep_extract" section of
equipment_type_config.json (enabled flag, model allowlist, page/token
limits, prompt template, merge behaviour).
"""

import base64
import io
import json
import logging
import re

from django.core.cache import cache
from rest_framework import status as drf_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.ai_consumer_clients import (
    AIProviderOperationError,
    lazy_provider_client,
)
from apps.core.ai_credentials import get_provider_configuration
logger = logging.getLogger(__name__)


# Register schema fields the AI may return (mirrors _pid_item_to_register_schema
# keys; sl_no is assigned during merge).  Soft-coded prompt picks this up.
DEEP_EXTRACT_FIELDS = [
    'tag', 'description', 'design_flowrate', 'oper_pressure', 'oper_temperature',
    'design_pressure_min', 'design_pressure_max', 'design_temp_min', 'design_temp_max',
    'moc', 'insulation', 'dimension_length', 'dimension_diameter', 'motor_rating',
    'pid_no', 'quality_required', 'phase', 'remarks',
]


def _deep_cfg(config: dict) -> dict:
    return (config or {}).get('deep_extract', {}) or {}


def _allowed_models(deep_cfg: dict) -> list:
    return [m for m in deep_cfg.get('models', [])
            if isinstance(m, dict) and m.get('id') and m.get('provider')]


def _provider_ready(provider: str) -> bool:
    try:
        meta = get_provider_configuration(provider)
        if meta['managed']:
            return bool(meta['ready'])
    except Exception:  # noqa: BLE001 — readiness is advisory only
        pass
    # Unmanaged provider: a legacy env/settings key may still exist.
    try:
        from apps.core.ai_consumer_clients import provider_api_key
        return bool(provider_api_key(provider))
    except Exception:  # noqa: BLE001
        return False


def _render_page_images(file_bytes: bytes, deep_cfg: dict) -> list:
    """Render PDF pages to base64 PNG strings, capped for vision payloads."""
    import pymupdf

    max_pages = int(deep_cfg.get('max_pages', 6))
    dpi = int(deep_cfg.get('dpi', 150))
    max_edge = int(deep_cfg.get('max_image_edge_px', 2400))
    zoom = dpi / 72.0
    images = []
    with pymupdf.open(stream=file_bytes, filetype='pdf') as doc:
        for page in doc:
            if len(images) >= max_pages:
                break
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            long_edge = max(pix.width, pix.height)
            if long_edge > max_edge:
                shrink = max_edge / long_edge
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom * shrink, zoom * shrink))
            images.append(base64.b64encode(pix.tobytes('png')).decode('ascii'))
    return images


def _build_prompt(deep_cfg: dict, drawing_ref: str, page_no: int) -> str:
    template = deep_cfg.get('prompt_template') or (
        'You are an Oil & Gas P&ID equipment extraction assistant.\n'
        'Examine this P&ID drawing page image and extract EVERY piece of primary '
        'equipment that has an equipment data box (a rectangle, often two-column, '
        'with cells like "TAG NUMBER" / "SERVICE DESCRIPTION").\n'
        'Tags may be unit-prefixed, e.g. "497-PU-3197" or "427-V-3064" — always '
        'report the FULL tag including the leading unit number.\n'
        'Data may be surrounded by RED revision clouds — that data is still valid '
        'and MUST be extracted.\n'
        'Do NOT extract: instrument/valve tags (PT, FT, LCP, PCV, PSV ...), line '
        'numbers, or drawing/document numbers (e.g. 501-D-11010, AD-497-D-15109).\n'
        'Drawing: {drawing_ref}, page {page_no}.\n'
        'Return ONLY a JSON array. Each element is an object with these keys '
        '(use "" when not visible on the drawing):\n{fields}\n'
        '\n'
        'Field mapping rules for this drawing format:\n'
        '\n'
        'COMBINED PRESSURE // TEMP labels:\n'
        'When a data box row has label like:\n'
        "'DISCHARGE PRESSURE // TEMP',\n"
        "'OPERATING PRESSURE // TEMP',\n"
        "'DISCHARGE PRESSURE / TEMP':\n"
        "→ Split the value on '//' or '/'\n"
        '→ First part (pressure) → oper_pressure\n'
        '→ Second part (temperature) → oper_temperature\n'
        '\n'
        "DISCHARGE PRESSURE // TEMP: 145 psig // 27.4°F\n"
        "→ oper_pressure = '145 psig(10 barg)'\n"
        "→ oper_temperature = '27.4°F(53°C)'\n"
        'The temperature value NEVER goes into oper_pressure!\n'
        "Keep the FULL temperature value including its parenthesised "
        "annotation — '27.4°F(53°C)', never truncate to just '27.4°F'.\n"
        '\n'
        "When label is 'DESIGN PRESSURE // TEMP':\n"
        '→ First part (pressure) → design_pressure_max\n'
        '→ Second part (temperature) → design_temp_max\n'
        '\n'
        'DESIGN PRESSURE // TEMP label:\n'
        '→ design_pressure_max = pressure part only\n'
        '→ design_temp_max = temperature part only\n'
        'Never put temperature value in pressure field!\n'
        '\n'
        'DESIGN PRESSURE // TEMP examples:\n'
        'Value: FV/180psig (FV/12.41barg)//29°F/185°F(-4°C/85°C)\n'
        "→ design_pressure_max: 'FV/180psig (FV/12.41barg)'\n"
        "→ design_temp_max: '29°F/185°F(-4°C/85°C)'\n"
        'Split ONLY on the // between pressure and temp.\n'
        'Do NOT split on the / inside FV/180psig!\n'
        '\n'
        'design_temp_min = minimum temperature value\n'
        'design_temp_max = maximum temperature value\n'
        'When DESIGN PRESSURE // TEMP label:\n'
        'temperature part goes to design_temp_max only.\n'
        'design_temp_min comes from a separate\n'
        'MIN temperature row if present.\n'
        '\n'
        'COMBINED DIAMETER & LENGTH labels:\n'
        "When label is 'INTERNAL DIAMETER & T/T LENGTH'\n"
        "or 'INTERNAL DIAMETER & T/T':\n"
        "Value format: '1800 mm (ID)x4000 mm (T/T)'\n"
        "→ First part before 'x' → dimension_diameter\n"
        '→ Second part after \'x\' → dimension_length\n'
        '\n'
        'OTHER FIELD MAPPINGS:\n'
        "'DISCHARGE PRESSURE' alone → oper_pressure\n"
        "'OPERATING PRESS' alone → oper_pressure\n"
        "'OPERATING TEMP (MAX/MIN)' → oper_temperature\n"
        "  (value like \"105/86 °F\" goes as-is into oper_temperature)\n"
        "  (keep combined value, do not split)\n"
        "'RATED FLOW @ T&P' or 'RATED CAPACITY' → design_flowrate\n"
        "'NOMINAL CAPACITY' → design_flowrate\n"
        "'GROSS CAPACITY' → design_flowrate\n"
        "'HEIGHT (T/T)' → dimension_length\n"
        "'DIAMETER (ID)' → dimension_diameter\n"
        "'MOTOR POWER' → motor_rating\n"
        "'INSULATION TYPE / THICKNESS' → insulation\n"
        "'TRIM' → remarks (append to existing remarks)\n"
        "'MATERIAL : SHELL INTERNALS' → moc\n"
        "'MATERIAL OF SHELL/INTERNAL' → moc\n"
        "'MATERIAL : SHELL / INTERNALS' → moc\n"
        '\n'
        'MAX/MIN dual-value labels:\n'
        'When label has (MAX/MIN) qualifier\n'
        'with two values separated by /:\n'
        '\n'
        "'DESIGN PRESS (MAX/MIN): 195 psig / FV'\n"
        "→ design_pressure_max: '195 psig'\n"
        "→ design_pressure_min: 'FV'\n"
        '\n'
        "'DESIGN TEMP (MAX/MIN): 185°F / -13.2°F'\n"
        "→ design_temp_max: '185°F'\n"
        "→ design_temp_min: '-13.2°F'\n"
        '\n'
        "'OPERATING TEMP (MAX/MIN): 105/86°F'\n"
        "→ oper_temperature: '105/86°F' (keep as-is)\n"
        '\n'
        'IMPORTANT: For (MAX/MIN) labels:\n'
        'First value before / → MAX field\n'
        'Second value after / → MIN field\n'
        '\n'
        'DESIGN TEMP extraction rules:\n'
        '\n'
        'When DESIGN PRESSURE // TEMP label:\n'
        "Temperature part: '29°F/185°F(-4°C/85°C)'\n"
        "→ design_temp_min: '-4°C' (lowest temperature)\n"
        "→ design_temp_max: '85°C' (highest temperature)\n"
        '\n'
        'When DESIGN TEMP (MAX/MIN) standalone label:\n'
        "Value: '185°F/-13.2°F'\n"
        "→ design_temp_max: '185°F' (first value)\n"
        "→ design_temp_min: '-13.2°F' (second value)\n"
        '\n'
        'IMPORTANT:\n'
        'design_temp_min = LOWEST/COLDEST temperature\n'
        'design_temp_max = HIGHEST/WARMEST temperature\n'
        '\n'
        'EXAMPLE:\n'
        "Temperature part '29°F/185°F(-4°C/85°C)':\n"
        'Minimum = -4°C (29°F)\n'
        'Maximum = 85°C (185°F)\n'
        "→ design_temp_min: '-4°C'\n"
        "→ design_temp_max: '85°C'\n"
        '\n'
        'EXAMPLE — V-803-TF drawing:\n'
        'OPERATING PRESS: 150 psig\n'
        "→ oper_pressure: '150 psig'\n"
        '\n'
        'OPERATING TEMP (MAX/MIN): 105/86 °F\n'
        "→ oper_temperature: '105/86 °F'\n"
        '\n'
        'DESIGN PRESS (MAX/MIN): 195 psig / FV\n'
        "→ design_pressure_max: '195 psig'\n"
        "→ design_pressure_min: 'FV'\n"
        '\n'
        'DESIGN TEMP (MAX/MIN): 185 °F / -13.2 °F\n'
        "→ design_temp_max: '185 °F'\n"
        "→ design_temp_min: '-13.2 °F'\n"
        '\n'
        'NOMINAL CAPACITY: 327 M3\n'
        "→ design_flowrate: '327 M3'\n"
        '\n'
        'MATERIAL OF SHELL/INTERNAL: CS + LINING\n'
        "→ moc: 'CS + LINING'\n"
        '\n'
        'EXAMPLES from real drawings:\n'
        'Label: DISCHARGE PRESSURE // TEMP\n'
        'Value: 145 psig(10 barg)// 27.4°F(53°C)\n'
        "→ oper_pressure: '145 psig(10 barg)'\n"
        "→ oper_temperature: '27.4°F(53°C)'\n"
        '\n'
        'Label: OPERATING PRESSURE // TEMP\n'
        'Value: 143.5 psig(10 barg)// 127.4°F(53°C)\n'
        "→ oper_pressure: '143.5 psig(10 barg)'\n"
        "→ oper_temperature: '127.4°F(53°C)'\n"
        '\n'
        'Label: DESIGN PRESSURE // TEMP\n'
        'Value: FV/180psig (FV/12.41barg)//29°F/185°F(-4°C/85°C)\n'
        "→ design_pressure_max: 'FV/180psig (FV/12.41barg)'\n"
        "→ design_temp_max: '29°F/185°F(-4°C/85°C)'\n"
        '\n'
        'Label: INTERNAL DIAMETER & T/T LENGTH\n'
        'Value: 1800 mm (ID)x4000 mm (T/T)\n'
        "→ dimension_diameter: '1800 mm (ID)'\n"
        "→ dimension_length: '4000 mm (T/T)'\n"
        '\n'
        'EXAMPLE — V-806-TF drawing:\n'
        'GROSS CAPACITY: 4.24 m3\n'
        "→ design_flowrate: '4.24 m3'\n"
        '\n'
        'HEIGHT (T/T): 3350 mm\n'
        "→ dimension_length: '3350 mm'\n"
        '\n'
        'DIAMETER (ID): 1200 mm\n'
        "→ dimension_diameter: '1200 mm'\n"
        '\n'
        'OPERATING TEMP (MAX/MIN): 120°F/110°F\n'
        "→ oper_temperature: '120°F/110°F'\n"
        '(keep both values as-is, do not split)\n'
        '\n'
        'DESIGN TEMP (MAX/MIN): 185°F/-29.8°F\n'
        "→ design_temp_max: '185°F'\n"
        "→ design_temp_min: '-29.8°F'\n"
        '(first value = MAX, second value = MIN)\n'
        '\n'
        'Always extract the COMPLETE value\n'
        'including units and annotations like\n'
        '(10 barg), (ID), (T/T), (53°C).\n'
        'Do not truncate values.\n'
        '\n'
        'No markdown fences, no commentary.'
    )
    return template.format(
        drawing_ref=drawing_ref or 'unknown',
        page_no=page_no,
        fields=', '.join(DEEP_EXTRACT_FIELDS),
    )


def _client(provider: str, factory, api_key: str = '', **options):
    """Build a lazy provider client. When the user pasted a BYOK key it is
    used for this request only (never stored/logged); otherwise the managed
    registry / environment credential is resolved per call."""
    if api_key:
        return lazy_provider_client(provider, factory, api_key=api_key, **options)
    return lazy_provider_client(provider, factory, **options)


def _call_openai(model: str, prompt: str, image_b64: str, deep_cfg: dict, api_key: str = '') -> str:
    from apps.rbac.ai_telemetry import observed_openai as OpenAI
    client = _client('openai', OpenAI, api_key, timeout=180.0, max_retries=1)
    response = client.chat.completions.create(
        model=model,
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url',
                 'image_url': {'url': f'data:image/png;base64,{image_b64}'}},
            ],
        }],
        max_tokens=int(deep_cfg.get('max_tokens', 4000)),
        temperature=float(deep_cfg.get('temperature', 0)),
        timeout=180.0,
    )
    return (response.choices[0].message.content or '').strip()


def _call_gemini(model: str, prompt: str, image_b64: str, deep_cfg: dict, api_key: str = '') -> str:
    from google import genai as google_genai
    from google.genai import types as genai_types
    client = _client('gemini', google_genai.Client, api_key)
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt,
            genai_types.Part.from_bytes(
                data=base64.b64decode(image_b64), mime_type='image/png',
            ),
        ],
        config=genai_types.GenerateContentConfig(
            max_output_tokens=int(deep_cfg.get('max_tokens', 4000)),
            temperature=float(deep_cfg.get('temperature', 0)),
        ),
    )
    return (response.text or '').strip()


def _call_anthropic(model: str, prompt: str, image_b64: str, deep_cfg: dict, api_key: str = '') -> str:
    from anthropic import Anthropic
    client = _client('anthropic', Anthropic, api_key, timeout=180.0, max_retries=1)
    response = client.messages.create(
        model=model,
        max_tokens=int(deep_cfg.get('max_tokens', 4000)),
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image',
                 'source': {'type': 'base64', 'media_type': 'image/png',
                            'data': image_b64}},
                {'type': 'text', 'text': prompt},
            ],
        }],
    )
    return ''.join(
        block.text for block in response.content if getattr(block, 'type', '') == 'text'
    ).strip()


_PROVIDER_CALLERS = {
    'openai': _call_openai,
    'gemini': _call_gemini,
    'anthropic': _call_anthropic,
}


def _parse_items(raw: str) -> list:
    cleaned = re.sub(r'^```[a-z]*\s*', '', (raw or '').strip(), flags=re.I)
    cleaned = re.sub(r'\s*```$', '', cleaned.strip())
    # Tolerate leading/trailing prose around the JSON array
    match = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if not match:
        return []
    parsed = json.loads(match.group(0))
    return [p for p in parsed if isinstance(p, dict)] if isinstance(parsed, list) else []


def _normalise_item(raw: dict, drawing_ref: str) -> dict:
    item = {field: str(raw.get(field) or '').strip() for field in DEEP_EXTRACT_FIELDS}
    item['tag'] = re.sub(r'\s+', '', item['tag']).upper()
    if not item['pid_no']:
        item['pid_no'] = drawing_ref
    return item


def _norm_tag_key(tag: str) -> str:
    """Unit-insensitive identity: 497-PU-3198 and PU-3198 collapse."""
    return re.sub(r'^\d{2,4}-(?=[A-Z]{1,3}-\d)', '', (tag or '').upper())


def _fetch_archived_pid_bytes(upload_id: str, project_label: str, filename: str = ''):
    """Retrieve the phase-1 source P&ID from the S3 archive.

    The analyze endpoint archives every uploaded P&ID as kind 'pid_document'
    with metadata upload-id=<upload_id>.  This lets Boost-with-AI re-read the
    exact same drawing even when the browser session no longer holds the
    file (page reload, saved register opened later).  Never raises.
    """
    try:
        from apps.designiq.s3_utils import (
            ARCHIVE_DEFAULT_PROJECT,
            ARCHIVE_KIND_PREFIXES,
            ARCHIVE_ROOT,
            _slug,
            s3_storage,
        )
        if not upload_id or not s3_storage.is_enabled():
            return None
        client = s3_storage.s3_client
        prefixes = [f"{ARCHIVE_ROOT}/{_slug(project_label or ARCHIVE_DEFAULT_PROJECT)}/"
                    f"{ARCHIVE_KIND_PREFIXES['pid_document']}/"]
        if not project_label:
            pass
        else:
            prefixes.append(
                f"{ARCHIVE_ROOT}/{ARCHIVE_DEFAULT_PROJECT}/{ARCHIVE_KIND_PREFIXES['pid_document']}/"
            )
        want_suffix = _slug(filename).lower() if filename else ''
        paginator = client.get_paginator('list_objects_v2')
        for prefix in prefixes:
            for page in paginator.paginate(Bucket=s3_storage.bucket_name, Prefix=prefix):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if want_suffix and not key.lower().endswith(want_suffix):
                        continue
                    head = client.head_object(Bucket=s3_storage.bucket_name, Key=key)
                    if str(head.get('Metadata', {}).get('upload-id', '')) == str(upload_id):
                        data = s3_storage.get_document(key)
                        if data:
                            logger.info('[DeepExtract] Recovered source P&ID from archive: %s', key)
                            return data
    except Exception as exc:  # noqa: BLE001 — archive recovery is best-effort
        logger.warning('[DeepExtract] Archive recovery failed: %s', exc)
    return None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def deep_extract_models(request):
    """GET /api/v1/pid/equipment/deep-extract/models/
    Returns the soft-coded model allowlist annotated with BYOK readiness, so
    the frontend can grey out providers whose key is not configured.
    """
    from apps.pid_analysis.equipment_analysis_views import _load_config
    deep_cfg = _deep_cfg(_load_config())
    models = [
        {**m, 'ready': _provider_ready(m['provider'])}
        for m in _allowed_models(deep_cfg)
    ]
    return Response({
        'enabled': bool(deep_cfg.get('enabled', True)),
        'default_model': deep_cfg.get('default_model', ''),
        'models': models,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def deep_extract_equipment(request):
    """POST /api/v1/pid/equipment/deep-extract/
    multipart: file (PDF), model (allowlisted id), upload_id (optional merge
    target), drawing_ref (optional).
    """
    from apps.pid_analysis.equipment_analysis_views import (
        EQ_RESULT_CACHE_KEY_FMT,
        EQ_RESULT_CACHE_TTL_S,
        _eq_get_result_entry,
        _load_config,
    )

    config = _load_config()
    deep_cfg = _deep_cfg(config)
    if not bool(deep_cfg.get('enabled', True)):
        return Response({'error': 'AI deep extraction is disabled.',
                         'success': False}, status=drf_status.HTTP_403_FORBIDDEN)

    pid_file = request.FILES.get('file') or (
        list(request.FILES.values())[0] if request.FILES else None)
    upload_id = (request.POST.get('upload_id') or '').strip()
    project_label = (request.POST.get('project_code')
                     or request.POST.get('project_name') or '').strip()
    source_filename = (request.POST.get('source_filename') or '').strip()
    if pid_file:
        file_bytes = pid_file.read()
        drawing_ref = (request.POST.get('drawing_ref')
                       or pid_file.name.rsplit('.', 1)[0]).strip()
    else:
        # No file in the request — recover the phase-1 source from the S3
        # archive via the upload_id (recorded as object metadata at upload).
        if not upload_id:
            return Response({'error': 'No file provided and no upload_id to recover it from.',
                             'success': False}, status=drf_status.HTTP_400_BAD_REQUEST)
        file_bytes = _fetch_archived_pid_bytes(upload_id, project_label, source_filename)
        if not file_bytes:
            return Response(
                {'error': 'The archived source P&ID could not be located for this extraction. '
                          'Please re-upload the same P&ID and extract it again.',
                 'success': False}, status=drf_status.HTTP_400_BAD_REQUEST)
        drawing_ref = (request.POST.get('drawing_ref')
                       or source_filename.rsplit('.', 1)[0]).strip()

    model_id = (request.POST.get('model') or deep_cfg.get('default_model') or '').strip()
    model_entry = next((m for m in _allowed_models(deep_cfg) if m['id'] == model_id), None)
    if not model_entry:
        return Response(
            {'error': f'Model "{model_id}" is not in the deep_extract allowlist.',
             'success': False},
            status=drf_status.HTTP_400_BAD_REQUEST)
    provider = model_entry['provider']
    caller = _PROVIDER_CALLERS.get(provider)
    if caller is None:
        return Response({'error': f'Provider "{provider}" is not supported.',
                         'success': False}, status=drf_status.HTTP_400_BAD_REQUEST)

    # Optional per-request BYOK key — used for this call only, never persisted.
    byok_key = (request.POST.get('byok_key') or '').strip()

    try:
        images = _render_page_images(file_bytes, deep_cfg)
    except Exception as exc:  # noqa: BLE001
        logger.error('[DeepExtract] page render failed: %s', exc)
        return Response({'error': 'Could not render the PDF pages.',
                         'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)
    if not images:
        return Response({'error': 'No pages could be rendered from the file.',
                         'success': False},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    # ── Per-page vision extraction ────────────────────────────────────────
    # Soft-coded resilience: if the selected model is unavailable (404 — the
    # provider account doesn't have it), automatically walk the rest of the
    # allowlisted models for the SAME provider instead of failing the run.
    model_chain = [model_entry] + [
        m for m in _allowed_models(deep_cfg)
        if m['provider'] == provider and m['id'] != model_id
    ]
    extracted: list = []
    page_errors: list = []
    model_used = model_id
    fatal = False
    for page_no, image_b64 in enumerate(images, start=1):
        prompt = _build_prompt(deep_cfg, drawing_ref, page_no)
        for candidate in model_chain:
            try:
                raw = caller(candidate['id'], prompt, image_b64, deep_cfg, api_key=byok_key)
                for parsed in _parse_items(raw):
                    item = _normalise_item(parsed, drawing_ref)
                    if item['tag']:
                        item['source_page'] = page_no
                        extracted.append(item)
                model_used = candidate['id']
                break  # page succeeded
            except AIProviderOperationError as exc:
                if getattr(exc, 'code', None) == 'model_unavailable':
                    logger.warning('[DeepExtract] model %s unavailable, trying next', candidate['id'])
                    continue  # next model in the same-provider fallback chain
                page_errors.append(f'page {page_no}: {exc}')
                fatal = True   # credential/quota problems repeat — stop the run
                break
            except Exception as exc:  # noqa: BLE001 — one bad page must not fail the run
                logger.warning('[DeepExtract] page %d failed: %s', page_no, exc)
                page_errors.append(f'page {page_no}: {type(exc).__name__}')
                break
        else:
            page_errors.append(f'page {page_no}: no available model for provider {provider}')
        if fatal:
            break

    if not extracted and page_errors:
        from apps.core.ai_credentials import AICredentialUnavailable
        credential_failure = any('AICredentialUnavailable' in e for e in page_errors)
        if credential_failure and not byok_key:
            msg = (f'No API key configured for {provider}. Paste your BYOK key '
                   f'in the AI Deep Extract panel, or ask an admin to save one '
                   f'in AI provider settings.')
        else:
            msg = f'AI deep extraction failed — {page_errors[0]}'
        return Response({'error': msg, 'success': False},
                        status=drf_status.HTTP_502_BAD_GATEWAY)

    # ── Merge into the phase-1 result set ─────────────────────────────────
    # Base of truth: the rows the user CURRENTLY sees (posted as
    # current_items).  The cached upload entry is only a fallback — it may
    # have expired or belong to an older upload, and merging against nothing
    # would WIPE the visible register.  Never return fewer rows than the
    # client already has.
    # Merge mode: 'fill' (default — AI only fills empty fields, phase-1 values
    # are trusted) or 'correct' (AI may overwrite wrong-looking phase-1 values
    # — user-selected in the Boost panel when the deterministic pass produced
    # inaccurate data).  Soft-coded default via merge_fill_empty_only.
    fill_empty_only = bool(deep_cfg.get('merge_fill_empty_only', True))
    if (request.POST.get('merge_mode') or '').strip().lower() == 'correct':
        fill_empty_only = False
    existing: list = []
    entry = None
    raw_current = (request.POST.get('current_items') or '').strip()
    if raw_current:
        try:
            parsed_current = json.loads(raw_current)
            if isinstance(parsed_current, list):
                existing = [p for p in parsed_current if isinstance(p, dict)]
        except (ValueError, TypeError):
            existing = []
    if not existing and upload_id:
        entry = _eq_get_result_entry(upload_id)
        if entry:
            existing = entry.get('equipment', []) or []
    cache_key = (
        EQ_RESULT_CACHE_KEY_FMT.format(upload_id=upload_id) if upload_id else None
    )

    by_key = {}
    for item in existing:
        by_key.setdefault(_norm_tag_key(item.get('tag', '')), item)

    added, enriched = 0, 0
    for item in extracted:
        key = _norm_tag_key(item['tag'])
        if not key:
            continue
        target = by_key.get(key)
        if target is None:
            item['extraction_source'] = 'ai_deep'
            by_key[key] = item
            added += 1
        else:
            changed = False
            for field in DEEP_EXTRACT_FIELDS:
                value = item.get(field)
                if not value:
                    continue
                if field == 'tag' and re.match(r'^\d{2,4}-', item['tag']) \
                        and not re.match(r'^\d{2,4}-', str(target.get('tag', ''))):
                    target['tag'] = item['tag']  # adopt unit-prefixed identity
                    changed = True
                elif not target.get(field) or not fill_empty_only:
                    if target.get(field) != value:
                        target[field] = value
                        changed = True
            if changed:
                enriched += 1

    merged = list(by_key.values())
    for idx, item in enumerate(merged, start=1):
        item['sl_no'] = str(idx)

    if cache_key:
        # Keep the shared cache in sync (create the entry when it expired).
        if entry is None:
            entry = {'status': 'completed', 'drawing_ref': drawing_ref}
        entry['equipment'] = merged
        entry['total'] = len(merged)
        cache.set(cache_key, entry, EQ_RESULT_CACHE_TTL_S)

    return Response({
        'success': True,
        'model': model_used,
        'model_requested': model_id,
        'provider': provider,
        'pages_processed': len(images) - len(page_errors),
        'upload_id': upload_id,
        'equipment': merged,
        'total': len(merged),
        'added': added,
        'enriched': enriched,
        'page_errors': page_errors,
    })
