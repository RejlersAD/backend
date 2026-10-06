"""Bridge between the designiq line-list flow and the trained 'Line List'
legend model (ai/legend_models, key ``line_list`` → MLflow model
``legend_line_list``).

The Line List model is a COMBINED segmenter covering every known line-list
format (config.COMBINED_MODELS in ai/legend_models/config.py):
    Phase 1 (Line Numbering):  SIZE - FLUID - CLASS - NUMBER - INSULATION
    Phase 2 (Line Number):     SIZE - UNIT - FLUID - SERIAL(A####) - CLASS(7) - COATING (- STREAM)?

Functions exposed to the line-list page pipeline:
  - segment tagging/validation of every extracted line (predict_tag)
  - fluid-code → description enrichment from the legend's own code table
  - area derivation from the serial segment's first character

Everything is soft-coded below and every call is fail-safe: when the model
(or its dependencies) is unavailable the flow falls back to the regex-only
result unchanged.

The `ai/` package lives at the REPO ROOT, outside the Django app tree, so it
is added to sys.path lazily on first use (same pattern as
pid_checker_v2/services/line_tagger_bridge.py).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Soft-coded knobs ────────────────────────────────────────────────────────
LINELIST_MODEL_ENABLED = os.environ.get('LINELIST_MODEL_ENABLED', 'true').lower() == 'true'
LINELIST_MODEL_KEY = os.environ.get('LINELIST_MODEL_KEY', 'line_list')
# format_type values that trigger Line List model enrichment
LINELIST_MODEL_FORMATS = ('linelist', 'general')

_loaded = None        # None = not attempted; False = unavailable; model = ready


def _load_model():
    """Load the champion Line List model once; False if unavailable."""
    global _loaded
    if _loaded is not None:
        return _loaded
    if not LINELIST_MODEL_ENABLED:
        _loaded = False
        return _loaded
    try:
        # The ai/ package lives at the repo root.  Candidate roots (first hit wins):
        #   local dev  : <workspace>/   — docker-compose mounts ./ai at /ai, so the
        #                container filesystem root ('/') exposes /ai/legend_models
        #   production : /app           — Railway's `COPY . .` ships ai/legend_models
        #                (tracked in this repo) at /app/ai/legend_models
        _here = Path(__file__).resolve()
        _candidates = [_here.parents[3], _here.parents[2]]
        for root in _candidates:
            if (Path(str(root)) / 'ai' / 'legend_models' / 'infer.py').exists():
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))
                break
        from ai.legend_models.infer import LegendOrchestrator  # noqa: PLC0415
        _loaded = LegendOrchestrator().load(LINELIST_MODEL_KEY)
        logger.info('[linelist_model] loaded champion model for key %r', LINELIST_MODEL_KEY)
    except Exception as e:  # noqa: BLE001 — MLflow/sklearn may be absent
        logger.info('[linelist_model] unavailable (no trained model / deps): %s', e)
        _loaded = False
    return _loaded


def _model_info(model) -> tuple[set, dict, dict]:
    """Normalise single-scheme and combined numbering models.

    Returns (fluid_field_keys, merged_fluid_codes, required_fields_by_scheme).
    Combined models (CombinedNumberingModel) use canonical field labels
    ('fluid_code', 'serial', …) and route each tag to a scheme; single-scheme
    models use their sheet-native field keys.
    """
    fluid_keys: set = set()
    codes: dict = {}
    required: dict = {}
    if getattr(model, 'schemes', None):                      # combined model
        for entry in model.schemes:
            fmap = entry.get('field_map', {})
            req = []
            for seg in entry['scheme'].get('segments', []):
                canon = fmap.get(seg['field_key'], seg['field_key'])
                if not seg.get('optional'):
                    req.append(canon)
                if seg.get('codes') and canon == 'fluid_code':
                    fluid_keys.add('fluid_code')
                    codes.update(seg['codes'])
            required[entry['key']] = req
    else:                                                  # single-scheme model
        for seg in getattr(model, 'scheme', {}).get('segments', []):
            if seg.get('codes'):
                fluid_keys.add(seg['field_key'])
                codes.update(seg['codes'])
        required[''] = [s['field_key']
                        for s in getattr(model, 'scheme', {}).get('segments', [])
                        if not s.get('optional')]
    return fluid_keys, codes, required


def linelist_enricher_for(format_type: str):
    """Return a per-line enrichment callable, or None when not applicable.

    enricher(line_dict) -> dict with any of:
      fluid_description : legend description for the fluid/designation code
      area              : serial segment's first character (per legend rule)
      model_valid       : True when every required segment of the routed
                          scheme was recognised
      model_fields      : {field_key: token} ML segmentation of the tag
      model_scheme      : which line-list scheme the model routed the tag to
    """
    if format_type not in LINELIST_MODEL_FORMATS:
        return None
    model = _load_model()
    if not model:
        return None

    fluid_keys, fluid_codes, required_by_scheme = _model_info(model)

    def enrich(line: dict) -> dict:
        tag = line.get('line_number') or line.get('original_detection') or ''
        if not tag:
            return {}
        try:
            pred = model.predict_tag(str(tag))
        except Exception as e:  # noqa: BLE001 — never break extraction
            logger.debug('[linelist_model] predict_tag failed for %r: %s', tag, e)
            return {}
        parsed = {str(k): str(v) for k, v in pred.get('parsed', {}).items()}
        scheme_key = str(pred.get('scheme', '') or '')
        required = required_by_scheme.get(scheme_key) or next(
            iter(required_by_scheme.values()), [])
        out: dict = {'model_fields': parsed,
                     'model_valid': all(k in parsed for k in required),
                     'model_scheme': scheme_key}
        fluid = next((parsed[k] for k in fluid_keys if k in parsed), '').upper()
        if fluid and fluid in fluid_codes:
            out['fluid_description'] = fluid_codes[fluid]
        serial = parsed.get('serial') or parsed.get(
            'serial_number_first_character_for_area', '')
        if serial:
            out['area'] = serial[0]
        return out

    return enrich
