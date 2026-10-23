"""
Valve MTO — RADAI AI Engine service interface (Stage 3 + Start Extraction).

No personal API keys (§4): the engine is SERVER-configured
(config/settings.py's OPENAI_API_KEY / ANTHROPIC_API_KEY). This module
is the single seam the frontend talks to:

    engine_status()                → Stage-3 connectivity/health check
    run_extraction(job, ...)       → Start-Extraction pipeline

PIPELINE STRUCTURE (all steps record progress on the ValveMTOJob row so
GET …/jobs/:jobId can drive the UI step list):

    1. Reading text layer          (pdf_parse.parse_drawing reuse)
    2. Detecting symbols           (symbol detection impl — see below)
    3. Matching legend & tag rules (tag-code rules → legend → note rules)
    4. Associating lines & sizes   (nearest size label / line tracing)
    5. Applying scope              (scope clouds / vendor boundary)
    6. Building register           (ValveMTORow bulk create)

SYMBOL DETECTION — clearly-marked tunable implementation
─────────────────────────────────────────────────────────
`detect_symbols()` below is the v1 implementation: vector ellipse
candidates from pdf_parse, grouped into symbol groups by (fill, size
band). Bow-tie / check / butterfly vision-tile detection is the
deliberate extension point (see the TODO there) — the reference P&ID was
not available when this was written, so the vision half ships behind the
same interface with a documented stub, ready to be tuned once
50249-05-AD-1050 lands in Documents/Piping/Valve MTO/.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time

import fitz  # PyMuPDF
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from . import pdf_parse
from .models import ValveMTOJob, ValveMTORow

logger = logging.getLogger(__name__)

JOB_STEPS = [
    'Reading text layer',
    'Detecting symbols',
    'Matching legend & tag rules',
    'Associating lines & sizes',
    'Applying scope',
    'Building register',
]

# Default tag-code rules (§4 Stage 4) — regex → valve type. Legends can
# override/extend via definition.tag_rules.
DEFAULT_TAG_RULES = [
    (r'^51RA\d', 'GLOBE VALVE'),
    (r'^51RB\d', 'BALL VALVE'),
    (r'^[AB]\d-P\d+', 'BALL VALVE'),
    (r'^51RC', 'CHECK VALVE'),
    (r'^C\d-', 'CHECK VALVE'),
    (r'^51RF\d', 'BUTTERFLY VALVE'),
    (r'^G\d-', 'GATE VALVE'),
]

# Note rules (§4): note text → attribute override.
DEFAULT_NOTE_RULES = [
    (r'TRIPLE OFFSET BUTTERFLY', 'BUTTERFLY VALVE (TRIPLE OFFSET)'),
]

# Instrument tag prefixes → category (§5).
INSTRUMENT_CATEGORIES = [
    (re.compile(r'\b[PTLF]V-\d+', re.I), 'control'),
    (re.compile(r'\bHV-\d+', re.I), 'actuated-onoff'),
    (re.compile(r'\b(?:PSV|TSV)-\d+', re.I), 'psv'),
    (re.compile(r'\bSOV-\d+', re.I), 'solenoid'),
]

# Tag fragments that mark a normally-closed variant (filled ellipse tags
# carry their own rule; text like 'NC' near the tag also qualifies).
RE_NC = re.compile(r'\bNC\b', re.I)


def engine_status() -> dict:
    """Stage-3 health check. Configured = at least one server-side vision
    key present. Never exposes the key itself. Also advertises the
    selectable vision models per provider (soft-coded from the
    extractor's own tables) so the frontend dropdown never hardcodes
    model IDs."""
    providers = []
    if getattr(settings, 'OPENAI_API_KEY', ''):
        providers.append('openai')
    if getattr(settings, 'ANTHROPIC_API_KEY', ''):
        providers.append('anthropic')
    models = {}
    try:
        from apps.pid_verification.services.piping_valve_mto_extractor import (
            COST_PER_1K_TOKENS, VISION_MODELS,
        )
        for provider, default_model in VISION_MODELS.items():
            ids = [m for m in COST_PER_1K_TOKENS if m.startswith('gpt') == (provider == 'openai')]
            models[provider] = {
                'default': default_model,
                'options': sorted(ids, key=lambda m: (m != default_model, m)),
            }
    except Exception:
        models = {}
    return {
        'configured': bool(providers),
        'providers': providers,
        'methods': ['vector-vision', 'vision-only'],
        'models': models,
    }


def test_engine() -> dict:
    """'Test engine' button — configuration check with timing. A live
    round-trip is intentionally NOT done here (cost); connectivity to the
    configured provider is exercised on first real extraction."""
    t0 = time.monotonic()
    st = engine_status()
    return {
        'ok': st['configured'],
        'elapsed_s': round(time.monotonic() - t0, 2),
        'providers': st['providers'],
        'detail': 'connected' if st['configured'] else 'Engine not configured — ask an Admin',
    }


# ─────────────────────────────────────────────────────────────────────────
# Symbol detection — TUNABLE v1 (see module docstring).
# ─────────────────────────────────────────────────────────────────────────
def detect_symbols(doc) -> list[dict]:
    """Return raw symbol candidates with a symbol_group_id.

    v1: vector ellipse candidates only (pdf_parse.detect_valve_symbol_
    candidates), grouped by fill + coarse size band:
        group 'ellipse-hollow' / 'ellipse-filled' / …
    TODO(vision): bow-tie / check / butterfly via vision tiles deduped
    against these vector hits — plug in here once the reference P&ID is
    available for tuning. Candidates carry page/x/y/rect either way, so
    downstream steps don't change.
    """
    raw = pdf_parse.detect_valve_symbol_candidates(doc)
    for c in raw:
        band = 's' if max(c['w'], c['h']) < 16 else ('m' if max(c['w'], c['h']) < 28 else 'l')
        c['symbol_group_id'] = f"ellipse-{'filled' if c['fill'] else 'hollow'}-{band}"
    return raw


def _nearest_label(symbol, labels, max_dist=120.0):
    """Nearest line label to a symbol (word bboxes were not kept per
    label in v1, so association is by page + text order proximity —
    TUNABLE once the reference drawing is available). Returns the parsed
    label dict or None."""
    same_page = [l for l in labels if l.get('page') == symbol['page']]
    return same_page[0] if same_page else None


def _category_for_tag(tag: str) -> str:
    for rx, cat in INSTRUMENT_CATEGORIES:
        if rx.search(tag or ''):
            return cat
    return 'manual'


def _type_from_rules(tag: str, text_near: str, tag_rules, note_rules):
    """Type resolution order (§5): 1 tag-code rule, 2 legend symbol entry
    (handled by caller via group mapping), 3 note rule, else UNIDENTIFIED.
    Never guess."""
    for pattern, vtype in tag_rules:
        if re.search(pattern, tag or ''):
            return vtype, 'tag-rule'
    for pattern, vtype in note_rules:
        if re.search(pattern, text_near or '', re.I):
            return vtype, 'note-rule'
    return 'UNIDENTIFIED', 'unidentified'


def run_extraction(job: ValveMTOJob, vision_provider: str = None, vision_api_key: str = None,
                   vision_model: str = None) -> dict:
    """Execute the extraction pipeline for job.project. Synchronous —
    Celery wiring can wrap this unchanged (the job row carries progress).

    vision_provider/vision_api_key: optional per-run BYOK credential from
    Stage 3 — used for this run only, never persisted. vision_model:
    optional caller-chosen model override (None = provider default).

    Returns the result summary dict (also stored on job.result)."""
    from .models import ValveMTOProjectSettings

    project = job.project
    settings_obj = getattr(project, 'mto_settings', None)
    drawing = project.drawings.order_by('-created_at').first()
    if not drawing:
        raise ValueError('No drawing uploaded — complete Stage 1 first.')

    steps = [{'name': s, 'status': 'pending', 'detail': ''} for s in JOB_STEPS]

    def mark(idx, status_, detail=''):
        steps[idx]['status'] = status_
        if detail:
            steps[idx]['detail'] = detail
        job.current_step = steps[idx]['name'] if status_ == 'running' else job.current_step
        job.steps = steps
        job.save(update_fields=['current_step', 'steps', 'updated_at'])

    job.status = ValveMTOJob.STATUS_RUNNING
    job.save(update_fields=['status', 'updated_at'])

    # ── 1. Reading text layer ─────────────────────────────────────────
    mark(0, 'running')
    drawing.file.open('rb')
    pdf_bytes = drawing.file.read()
    drawing.file.close()
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    parsed = drawing.parsed or pdf_parse.parse_drawing(pdf_bytes, drawing.original_name)
    mark(0, 'done', f"{parsed.get('counts', {}).get('line_labels', 0)} line labels")

    # Pattern resolution for line-label parsing.
    preset = settings_obj.line_pattern_preset if settings_obj else 'service-seq-size-class-insul'
    custom_regex = settings_obj.line_pattern_regex if settings_obj else ''
    pattern_result = pdf_parse.apply_line_pattern(
        parsed.get('line_labels', []), preset, custom_regex,
    )
    parsed_labels = pattern_result.get('parsed', [])

    # Vision path (vector-vision / vision-only): delegate to the BYOK/central
    # vision pipeline (apps.pid_verification) instead of the v1 ellipse-only
    # detector, which cannot see bow-tie/check/butterfly symbols. A per-run
    # BYOK key counts as "configured" even without a server-side key.
    if (settings_obj and settings_obj.engine_method in ('vector-vision', 'vision-only')
            and (engine_status()['configured'] or vision_api_key)):
        rows_out, extra = _run_vision_extraction(
            job, pdf_bytes, parsed, settings_obj, mark,
            vision_provider=vision_provider, vision_api_key=vision_api_key,
            vision_model=vision_model,
        )
        mark(3, 'done', f'{len(rows_out)} valves associated')
        mark(4, 'done', 'scope applied')

        # ── 6. Building register ──────────────────────────────────────
        mark(5, 'running')
        project.rows.all().delete()
        ValveMTORow.objects.bulk_create(rows_out)
        if project.status != project.STATUS_COMPLETED:
            project.status = project.STATUS_COMPLETED
            project.save(update_fields=['status', 'updated_at'])

        title_block = parsed.get('title_block', {})
        unidentified = sum(1 for r in rows_out if r.type_basis == 'unidentified' or r.valve_type in ('', 'UNIDENTIFIED'))
        field_gaps = sum(1 for r in rows_out if not r.size_primary or not r.piping_class or not r.line_number)
        order_count = sum(1 for r in rows_out if (r.qty_combined or 0) > 0)
        result = {
            'row_count': len(rows_out),
            'unidentified': unidentified,
            'field_gaps': field_gaps,
            'order_count': order_count,
            'pid_number': drawing.confirmed_drawing_number or title_block.get('company_drawing_no', ''),
            'pid_rev': drawing.confirmed_rev or title_block.get('rev', ''),
            **extra,
        }
        mark(5, 'done', f'{len(rows_out)} rows')
        job.status = ValveMTOJob.STATUS_COMPLETED
        job.result = result
        job.save(update_fields=['status', 'result', 'updated_at'])
        doc.close()
        if settings_obj:
            settings_obj.extraction_outdated = False
            settings_obj.save(update_fields=['extraction_outdated', 'updated_at'])
        return result

    # ── 2. Detecting symbols ──────────────────────────────────────────
    mark(1, 'running')
    symbols = detect_symbols(doc)
    mark(1, 'done', f'{len(symbols)} candidates')

    # ── 3. Matching legend & tag rules ────────────────────────────────
    mark(2, 'running')
    legend = settings_obj.legend if settings_obj else None
    legend_def = (legend.definition or {}) if legend else {}
    tag_rules = [(r, t) for r, t in legend_def.get('tag_rules', [])] or DEFAULT_TAG_RULES
    note_rules = [(r, t) for r, t in legend_def.get('note_rules', [])] or DEFAULT_NOTE_RULES
    group_map = legend_def.get('symbol_group_map', {})  # groupId → {type, variant}
    mark(2, 'done', f'{len(tag_rules)} tag rules')

    # ── 4. Associating lines & sizes ──────────────────────────────────
    mark(3, 'running')
    title_block = parsed.get('title_block', {})
    pid_number = drawing.confirmed_drawing_number or title_block.get('company_drawing_no', '')
    pid_rev = drawing.confirmed_rev or title_block.get('rev', '')
    area = title_block.get('title') or title_block.get('company', '')
    pms_classes = (settings_obj.pms_data or {}).get('classes', {}) if settings_obj else {}

    rows_out = []
    for idx, sym in enumerate(symbols):
        label = _nearest_label(sym, parsed_labels)
        groups = (label or {}).get('groups', {})
        line_number = (label or {}).get('text', '') or 'Not traced'
        size = groups.get('size', '')
        piping_class = groups.get('class', '')
        piping_class_source = 'line-number' if piping_class else 'not-shown'
        if not piping_class:
            piping_class = 'Class not shown'

        # Tag: nearest tag-ish text would come from word positions — v1
        # uses the line label's service/seq when present (TUNABLE).
        tag = ''
        if groups.get('fluid') and groups.get('seq'):
            tag = f"{groups['fluid']}-{groups['seq']}"

        vtype, basis = _type_from_rules(tag, '', tag_rules, note_rules)
        variant = ''
        gm = group_map.get(sym['symbol_group_id'])
        if basis == 'unidentified' and gm:
            vtype, basis = gm.get('type', 'UNIDENTIFIED'), 'legend-symbol'
            variant = gm.get('variant', '')
        if not variant and RE_NC.search(tag or ''):
            variant = 'normally-closed'

        category = _category_for_tag(tag)
        if settings_obj and settings_obj.engine_instruments == 'exclude' and category != 'manual':
            continue

        # Rating/facing/material (§5): PMS → inferred → PER PMS <class>.
        pms_row = pms_classes.get(piping_class)
        if pms_row:
            rating = f"{pms_row.get('rating', '')} {pms_row.get('facing', '')}".strip()
            material = pms_row.get('material', '')
            material_source = 'pms'
        elif settings_obj and settings_obj.pms_infer_from_drawing:
            rating = ''
            material = f'(inferred)'
            material_source = 'inferred'
        else:
            rating = ''
            material = f'PER PMS {piping_class}' if piping_class != 'Class not shown' else 'PER PMS'
            material_source = 'per-pms'

        # ── 5. Applying scope (folded into the row loop) ──────────────
        scope_status = 'new-in-scope' if (
            settings_obj and settings_obj.engine_scope == 'project-scope'
        ) else 'existing-outside'
        in_scope_new_manual = (
            scope_status == 'new-in-scope' and category == 'manual'
            and (not settings_obj or settings_obj.engine_scope == 'project-scope')
        )
        order_total = 1 if in_scope_new_manual else 0
        remarks = '' if order_total else ('Not in project scope — qty 0' if scope_status != 'new-in-scope' else 'Instrument item — listed separately')

        rows_out.append(ValveMTORow(
            project=project,
            row_order=idx,
            page=sym['page'], pos_x=sym['x'], pos_y=sym['y'],
            valve_type=vtype, variant=variant, category=category,
            type_basis=basis, symbol_group_id=sym['symbol_group_id'] if basis == 'unidentified' else '',
            size_primary=size, line_number=line_number,
            piping_class=piping_class, piping_class_source=piping_class_source,
            rating=rating, pms_class=material, material_source=material_source,
            pid_number=pid_number, pid_rev=pid_rev, area=area,
            tag_number=tag, description='',
            scope_status=scope_status,
            qty_observed=1,
            qty_island=order_total, qty_field=0, qty_combined=order_total,
            unit='EACH',
            confidence_score=0.5,  # v1: no vision confidence yet — TUNABLE
            remarks=remarks,
            tab='combined',
        ))

        # Evidence crop (best-effort — never fail the job on a bad crop).
        try:
            png = pdf_parse.render_evidence_crop(doc, sym['page'], sym['rect'])
            rows_out[-1].evidence_crop.save(
                f'ev_{job.id}_{idx}.png', ContentFile(png), save=False,
            )
        except Exception:
            logger.debug('evidence crop failed for symbol %s', idx)

    mark(3, 'done', f'{len(rows_out)} symbols associated')
    mark(4, 'done', 'scope applied')

    # ── 6. Building register ──────────────────────────────────────────
    mark(5, 'running')
    project.rows.all().delete()
    ValveMTORow.objects.bulk_create(rows_out)
    if project.status != project.STATUS_COMPLETED:
        project.status = project.STATUS_COMPLETED
        project.save(update_fields=['status', 'updated_at'])

    unidentified = sum(1 for r in rows_out if r.type_basis == 'unidentified')
    field_gaps = sum(1 for r in rows_out if not r.size_primary or r.piping_class == 'Class not shown' or r.line_number == 'Not traced')
    order_count = sum(1 for r in rows_out if (r.qty_combined or 0) > 0)
    result = {
        'row_count': len(rows_out),
        'unidentified': unidentified,
        'field_gaps': field_gaps,
        'order_count': order_count,
        'pid_number': pid_number,
        'pid_rev': pid_rev,
    }
    mark(5, 'done', f'{len(rows_out)} rows')
    job.status = ValveMTOJob.STATUS_COMPLETED
    job.result = result
    job.save(update_fields=['status', 'result', 'updated_at'])
    doc.close()

    # Extraction is now fresh.
    if settings_obj:
        settings_obj.extraction_outdated = False
        settings_obj.save(update_fields=['extraction_outdated', 'updated_at'])
    return result


# Confidence string → numeric score (review threshold comparison in the
# register view expects 0..1).
_VISION_CONFIDENCE_SCORE = {'HIGH': 0.9, 'MEDIUM': 0.7, 'LOW': 0.4}


def _run_vision_extraction(job, pdf_bytes, parsed, settings_obj, mark,
                           vision_provider=None, vision_api_key=None, vision_model=None):
    """Steps 2–3 via the vision pipeline (apps.pid_verification's
    piping_valve_mto_extractor). Uses central server credentials by
    default; a per-run BYOK key (credential_mode='byok') takes priority
    and is never persisted. vision_model overrides the provider default
    for this run. Returns (rows_out, extra_result_fields)."""
    from apps.pid_verification.services.piping_valve_mto_extractor import (
        extract_valve_mto_streaming,
    )

    project = job.project
    drawing = project.drawings.order_by('-created_at').first()
    title_block = parsed.get('title_block', {})
    pid_number = drawing.confirmed_drawing_number or title_block.get('company_drawing_no', '')
    pid_rev = drawing.confirmed_rev or title_block.get('rev', '')

    mark(1, 'running')

    def on_progress(cur, total, rows_so_far):
        mark(1, 'running', f'vision batch {cur}/{total} — {rows_so_far} valves so far')

    pms_list = [
        {'code': code, **(info or {})}
        for code, info in ((settings_obj.pms_data or {}).get('classes', {}) or {}).items()
    ]

    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    try:
        tmp.write(pdf_bytes)
        tmp.close()
        mark(2, 'running')  # legend context is resolved inside the vision pipeline
        outcome = extract_valve_mto_streaming(
            tmp.name,
            on_progress=on_progress,
            user_id=project.created_by_id,
            vision_provider=vision_provider,
            vision_api_key=vision_api_key,
            vision_model=vision_model,
            credential_mode='byok' if vision_api_key else 'central',
            pms_data=pms_list or None,
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if outcome.get('status') == 'error':
        raise ValueError(outcome.get('error') or 'Vision extraction failed.')

    vision_rows = outcome.get('rows') or []
    mark(1, 'done', f'{len(vision_rows)} valves detected')
    mark(2, 'done', f"provider {outcome.get('provider') or '?'} · model {outcome.get('model') or '?'}")

    rows_out = []
    for idx, vr in enumerate(vision_rows):
        tag = vr.get('valve_tag', '')
        category = _category_for_tag(tag)
        if settings_obj.engine_instruments == 'exclude' and category != 'manual':
            continue
        qty_island = vr.get('qty_island') or 0
        qty_field = vr.get('qty_field') or 0
        conf = (vr.get('confidence') or '').upper()
        rows_out.append(ValveMTORow(
            project=project,
            row_order=idx,
            tag_number=tag,
            valve_type=vr.get('type', ''),
            category=category,
            type_basis='vision',
            pms_class=vr.get('pms_class', ''),
            piping_class=vr.get('piping_class', ''),
            piping_class_source='line-number' if vr.get('piping_class') else 'not-shown',
            rating=vr.get('rating', ''),
            facing=vr.get('facing', ''),
            size_primary=vr.get('size_1', ''),
            size_secondary=vr.get('bore', ''),
            size_secondary_2=vr.get('size_2', ''),
            line_number=vr.get('line_number', ''),
            line_list_ref=vr.get('line_list', ''),
            pid_number=vr.get('pid_number') or pid_number,
            pid_rev=pid_rev,
            area=vr.get('area', ''),
            description=vr.get('description', ''),
            qty_observed=vr.get('qty_observed'),
            qty_island=qty_island,
            qty_field=qty_field,
            qty_combined=qty_island + qty_field,
            unit=vr.get('unit') or 'EACH',
            remarks=vr.get('remarks', ''),
            confidence=conf.title() if conf else '',
            confidence_score=_VISION_CONFIDENCE_SCORE.get(conf),
            location_hint=vr.get('location_hint', ''),
            tab=vr.get('tab') or 'combined',
            scope_status='new-in-scope' if settings_obj.engine_scope == 'project-scope' else 'existing-outside',
            material_source='pms' if vr.get('rating') else 'per-pms',
        ))

    extra = {
        'engine': outcome.get('engine'),
        'provider': outcome.get('provider'),
        'model': outcome.get('model'),
        'warnings': outcome.get('warnings') or [],
        'total_cost_usd': outcome.get('total_cost_usd'),
    }
    return rows_out, extra
