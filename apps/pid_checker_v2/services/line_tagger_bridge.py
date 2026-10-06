"""Bridge between the backend and the root `ai/` line_tagger package.

The web container can call `validate_line_tag()` / `validate_rows()` to get
ML-tagged fields for low-confidence extractions. Until a trained model exists
in ai/line_tagger/export/, every call returns None / empty and the caller
falls back to the regex result — so this is a safe no-op out of the box.

The `ai/` package lives at the REPO ROOT, outside the Django app tree. We add
it to sys.path lazily (and only when first needed) so importing this module
never requires torch/transformers unless a trained model is present.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Soft-coded: enable/disable the ML fallback entirely (kill switch).
LINE_TAGGER_ENABLED = os.environ.get('LINE_TAGGER_ENABLED', 'true').lower() == 'true'

_loaded = None        # None = not attempted; False = unavailable; module = ready


def _resolve_ai_root() -> Path | None:
    """Locate the line_tagger package — in the container the repo `ai/` folder
    isn't mounted, so we fall back to a copy bundled under this app. Locally the
    repo-root `ai/` wins so training and the app share one source of truth."""
    # 1) Container/bundled copy next to the Django app (works in Docker).
    bundled = Path(__file__).resolve().parent / 'line_tagger'
    if (bundled / 'config.py').exists():
        return bundled.parent  # parent dir containing the `line_tagger` package
    # 2) Repo-root `ai/` folder (local dev / training).
    here = Path(__file__).resolve()
    root = here.parents[4]
    ai_dir = root / 'ai'
    return ai_dir if ai_dir.is_dir() else None


def _load():
    """Import the line_tagger inference module once; False if unavailable."""
    global _loaded
    if _loaded is not None:
        return _loaded
    if not LINE_TAGGER_ENABLED:
        _loaded = False
        return _loaded
    ai_dir = _resolve_ai_root()
    if ai_dir is None:
        _loaded = False
        return _loaded
    try:
        root = str(ai_dir.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        # Import the package by whatever name resolves — the repo root exposes it
        # as `ai.line_tagger`; the bundled container copy as `line_tagger`.
        try:
            from ai.line_tagger import infer  # noqa: PLC0415
        except ImportError:
            from line_tagger import infer  # noqa: PLC0415
        _loaded = infer
    except Exception as e:  # noqa: BLE001 — torch/transformers may be absent
        logger.info('[line_tagger] unavailable (no trained model / deps): %s', e)
        _loaded = False
    return _loaded


def validate_line_tag(tag_text: str) -> dict | None:
    """Return ML-tagged fields for a raw line tag string, or None.

    Fields use the backend column keys (size, fluid_code, piping_spec,
    sequence_no, from_line, to_line) plus a 'confidence' score. Returns None
    when no model is available — callers must treat None as "use regex result".
    """
    infer = _load()
    if infer is None:
        return None
    try:
        result = infer.tag_line_text(tag_text)
        return result or None
    except Exception as e:  # noqa: BLE001 — never break extraction
        logger.warning('[line_tagger] validate_line_tag failed: %s', e)
        return None


def validate_rows(rows: list[dict], *, text_key: str = 'original_detection') -> list[dict]:
    """Annotate a list of extracted rows with ML suggestions where available.

    Non-destructive: adds an 'ml_suggestion' sub-dict to rows the model has a
    confident answer for; leaves everything else untouched. Returns the same
    list (mutated in place) so callers can opt in without restructuring.
    """
    infer = _load()
    if infer is None or not rows:
        return rows
    for row in rows:
        text = str(row.get(text_key) or '').strip()
        if not text:
            continue
        suggestion = validate_line_tag(text)
        if suggestion:
            row['ml_suggestion'] = suggestion
    return rows
