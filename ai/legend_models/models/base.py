"""Base contract for per-sheet legend models.

Every model family implements:
    fit(grid, params)  -> metrics dict
    save(dir)          -> write self-contained artifacts (joblib + meta.json)
    load(dir)          -> classmethod, restore from save()
    predict(...)       -> format-specific inference

Artifacts are plain files so MLflow can log/register them generically and
infer.py can download `models:/legend_<sheet>@champion/export` untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..workbook import SheetGrid


class BaseLegendModel:
    format_type: str = 'base'
    model_version: int = 1

    def fit(self, grid: SheetGrid, params: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def save(self, out_dir: str | Path) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def load(cls, in_dir: str | Path):  # pragma: no cover - interface
        raise NotImplementedError

    # ── shared helpers ──────────────────────────────────────────────────────
    def _write_meta(self, out_dir: str | Path, extra: dict | None = None) -> None:
        meta = {
            'format_type': self.format_type,
            'model_version': self.model_version,
            'model_class': type(self).__name__,
            **(extra or {}),
        }
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / 'meta.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')

    @staticmethod
    def _read_meta(in_dir: str | Path) -> dict:
        p = Path(in_dir) / 'meta.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
