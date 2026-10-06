"""Format router — recognise each sub-sheet's archetype from its content.

Two layers, both soft-coded:
  1. Rule scorer: weighted signals from config.FORMAT_SIGNALS (always on).
  2. Learned router (optional): a sklearn classifier trained on the signal
     vectors of previously routed sheets, registered in MLflow as
     `legend_format_router`. Only used when a champion exists in the
     registry and its confidence beats the rule margin; the rule scorer
     remains the fallback so routing never depends on MLflow being up.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import config
from .workbook import SheetGrid

logger = logging.getLogger('legend_models.formats')


@dataclass
class FormatDecision:
    sheet_key: str
    format_type: str
    scores: dict[str, float]
    signals: dict[str, float]
    source: str  # 'hint' | 'rules' | 'learned'


def compute_signals(grid: SheetGrid) -> dict[str, float]:
    """Compute the soft-coded signal vector for one sheet."""
    th = config.SIGNAL_THRESHOLDS
    rows = [r for r in grid.rows if any(r)]
    n_rows = max(len(rows), 1)
    placeholder_re = re.compile(th['placeholder_re'])
    section_re = re.compile(th['section_re'])
    example_re = re.compile(th['example_re'], re.I)

    example_row = 0.0
    placeholder_rows = 0.0
    sectioned = 0.0
    error_cells = 0.0
    code_desc_rows = 0.0
    uppercase_codes = 0.0

    for r in rows:
        first = r[0] if r else ''
        if example_re.search(first):
            example_row = 1.0
        if placeholder_re.match(first.strip()):
            placeholder_rows += 1
        if section_re.match(first.strip()):
            sectioned += 1
        if any(c.strip() == '#VALUE!' for c in r):
            error_cells += 1
        # two-column short-code → long-description shape
        non_empty = [c for c in r if c]
        if len(non_empty) == 2:
            code, desc = non_empty
            if 1 <= len(code) <= 6 and len(desc) >= 4 and code.strip() == code.strip().upper():
                code_desc_rows += 1
                if re.match(r'^[A-Z0-9&/\- ]+$', code):
                    uppercase_codes += 1

    # wide grid = genuinely matrix-like: most rows are dense (>=4 filled cells)
    wide = 0.0
    if grid.n_cols >= th['wide_grid_min_cols']:
        dense = sum(1 for r in rows if sum(1 for c in r if c) >= 4)
        if dense / n_rows > 0.5:
            wide = 1.0
    # header matrix: wide grid whose first column holds short row labels
    header_matrix = 0.0
    if wide and rows:
        first_col = [r[0] for r in rows[2:] if r and r[0]]
        if first_col and sum(1 for c in first_col if len(c) <= 4) / len(first_col) > 0.6:
            header_matrix = 1.0

    return {
        'example_row': example_row,
        'placeholder_rows': placeholder_rows / n_rows,
        'sectioned_tables': sectioned / n_rows,
        'anchored_images': min(len(grid.images) / 5.0, 1.0),
        'error_image_cells': error_cells / n_rows,
        'wide_grid': wide,
        'header_matrix': header_matrix,
        'two_col_code_desc': code_desc_rows / n_rows,
        'uppercase_codes': uppercase_codes / n_rows,
    }


def score_formats(signals: dict[str, float]) -> dict[str, float]:
    """Weighted sum of signals per format type (weights from config)."""
    scores = {f: 0.0 for f in config.FORMAT_TYPES}
    for signal, value in signals.items():
        for fmt, weight in config.FORMAT_SIGNALS.get(signal, {}).items():
            scores[fmt] = scores.get(fmt, 0.0) + weight * value
    return scores


def _argmax_with_priority(scores: dict[str, float]) -> str:
    best, best_score = config.FORMAT_PRIORITY[-1], float('-inf')
    for fmt in config.FORMAT_PRIORITY:  # earlier entry wins ties
        if scores.get(fmt, 0.0) > best_score:
            best, best_score = fmt, scores[fmt]
    return best


def detect_format(grid: SheetGrid) -> FormatDecision:
    """Route one sheet to its format type: hint > learned router > rules."""
    hint = config.sheet_format_hint(grid.title)
    signals = compute_signals(grid)
    scores = score_formats(signals)
    if hint:
        return FormatDecision(grid.key, hint, scores, signals, source='hint')

    learned = _learned_router_predict(signals)
    if learned is not None:
        fmt, conf = learned
        rule_fmt = _argmax_with_priority(scores)
        rule_margin = scores.get(rule_fmt, 0.0) - sorted(scores.values())[-2] if len(scores) > 1 else 0.0
        if conf >= 0.6 or rule_margin < 1.0:
            return FormatDecision(grid.key, fmt, scores, signals, source='learned')

    return FormatDecision(grid.key, _argmax_with_priority(scores), scores, signals, source='rules')


# ── Learned router (optional layer) ─────────────────────────────────────────

SIGNAL_ORDER = sorted(config.FORMAT_SIGNALS.keys())


def signal_vector(signals: dict[str, float]) -> list[float]:
    return [signals.get(name, 0.0) for name in SIGNAL_ORDER]


def train_router(decisions: list[FormatDecision], params: dict | None = None):
    """Train the learned router from a batch of rule-scored decisions.

    Returns (model, metrics). Skipped by train.py when fewer than
    `min_samples` sheets exist (soft-coded).
    """
    from sklearn.linear_model import LogisticRegression

    params = {'min_samples': 6, 'max_iter': 500, **(params or {})}
    X = [signal_vector(d.signals) for d in decisions]
    y = [d.format_type for d in decisions]
    if len(X) < params['min_samples'] or len(set(y)) < 2:
        return None, {'trained': 0, 'reason': 'insufficient samples'}
    clf = LogisticRegression(max_iter=params['max_iter'])
    clf.fit(X, y)
    acc = float(clf.score(X, y))
    return clf, {'trained': 1, 'train_accuracy': acc, 'n_samples': len(X), 'n_classes': len(set(y))}


def _learned_router_predict(signals: dict[str, float]) -> tuple[str, float] | None:
    """Load the champion router from MLflow (if any) and predict."""
    try:
        import mlflow  # noqa: PLC0415
        from mlflow.tracking import MlflowClient  # noqa: PLC0415
        mlflow.set_tracking_uri(__import__('os').environ.get('MLFLOW_TRACKING_URI', 'http://localhost:5000'))
        client = MlflowClient()
        version = client.get_model_version_by_alias(config.ROUTER_MODEL_NAME, config.CHAMPION_ALIAS)
        local = mlflow.artifacts.download_artifacts(
            f'models:/{config.ROUTER_MODEL_NAME}@{config.CHAMPION_ALIAS}/export')
        import joblib  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415
        clf = joblib.load(Path(local) / 'router.joblib')
        proba = clf.predict_proba([signal_vector(signals)])[0]
        idx = int(proba.argmax())
        return str(clf.classes_[idx]), float(proba[idx])
    except Exception:  # noqa: BLE001 — learned router is strictly optional
        return None
