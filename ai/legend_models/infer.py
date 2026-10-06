"""Inference orchestrator — route any legend sheet to its champion model.

Loads champion artifacts from the MLflow registry
(`models:/legend_<sheet_key>@champion`) with a local-export fallback so
inference also works before registration or while MLflow is down.

Usage (repo root):
    python -m ai.legend_models.infer tag equipment_numbering "B-101-A1"
    python -m ai.legend_models.infer tag existing_pids "MRI-O91-011"
    python -m ai.legend_models.infer symbol valve path/to/glyph.png
    python -m ai.legend_models.infer combo instrument_letters FIC
    python -m ai.legend_models.infer lookup miscellaneous "gate valve"
    python -m ai.legend_models.infer fleet                     # manifest table

Or programmatically:
    from ai.legend_models.infer import LegendOrchestrator
    orch = LegendOrchestrator()
    orch.predict_tag('equipment_numbering', 'B-101-A1')
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import config
from .models import MODEL_REGISTRY, BaseLegendModel

logger = logging.getLogger('legend_models.infer')


class LegendOrchestrator:
    """Fleet-wide inference: one entry point, every sheet's champion model."""

    def __init__(self, tracking_uri: str | None = None) -> None:
        self.tracking_uri = tracking_uri or os.environ.get(
            'MLFLOW_TRACKING_URI', 'http://localhost:5000')
        self._cache: dict[str, BaseLegendModel] = {}

    # ── model loading ───────────────────────────────────────────────────────
    def _export_dir(self, sheet_key: str) -> Path:
        """Resolve the champion export dir: MLflow registry → local fallback."""
        try:
            import mlflow  # noqa: PLC0415
            mlflow.set_tracking_uri(self.tracking_uri)
            return Path(mlflow.artifacts.download_artifacts(
                f'models:/{config.model_name(sheet_key)}@{config.CHAMPION_ALIAS}/{config.EXPORT_DIRNAME}'))
        except Exception as e:  # noqa: BLE001
            local = config.WORK_DIR / sheet_key / config.EXPORT_DIRNAME
            if local.exists():
                logger.info('MLflow unavailable (%s) — using local export for %s', e, sheet_key)
                return local
            raise RuntimeError(
                f'No champion model for sheet {sheet_key!r} in MLflow and no local export. '
                f'Train it first: python -m ai.legend_models.train --sheets "{sheet_key}"') from e

    def load(self, sheet_key: str) -> BaseLegendModel:
        if sheet_key in self._cache:
            return self._cache[sheet_key]
        export = self._export_dir(sheet_key)
        meta = json.loads((export / 'meta.json').read_text(encoding='utf-8'))
        model_cls = MODEL_REGISTRY[meta['format_type']]
        model = model_cls.load(export)
        self._cache[sheet_key] = model
        return model

    # ── format-specific predictors ──────────────────────────────────────────
    def predict_tag(self, sheet_key: str, tag: str) -> dict:
        return self.load(sheet_key).predict_tag(tag)

    def legend_definition(self, sheet_key: str) -> dict:
        return self.load(sheet_key).legend_definition()

    def predict_symbol(self, sheet_key: str, image_path: str | Path) -> dict:
        return self.load(sheet_key).predict_symbol(image_path)

    def predict_combo(self, sheet_key: str, letters: str) -> dict:
        return self.load(sheet_key).predict_combo(letters)

    def lookup(self, sheet_key: str, description: str) -> list[dict]:
        return self.load(sheet_key).lookup(description)

    def fleet(self) -> list[dict]:
        """Manifest of locally exported sheet models."""
        rows = []
        if not config.WORK_DIR.exists():
            return rows
        for child in sorted(config.WORK_DIR.iterdir()):
            meta_path = child / config.EXPORT_DIRNAME / 'meta.json'
            if child.is_dir() and meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
                rows.append({'sheet_key': child.name, **meta})
        return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Legend fleet inference')
    sub = parser.add_subparsers(dest='command', required=True)
    for name, extra in (('tag', 'tag'), ('symbol', 'image'), ('combo', 'letters'),
                        ('lookup', 'description')):
        p = sub.add_parser(name)
        p.add_argument('sheet_key')
        p.add_argument(extra)
    sub.add_parser('fleet')
    args = parser.parse_args(argv)

    orch = LegendOrchestrator()
    if args.command == 'fleet':
        out = orch.fleet()
    elif args.command == 'tag':
        out = orch.predict_tag(args.sheet_key, args.tag)
    elif args.command == 'symbol':
        out = orch.predict_symbol(args.sheet_key, args.image)
    elif args.command == 'combo':
        out = orch.predict_combo(args.sheet_key, args.letters)
    else:
        out = orch.lookup(args.sheet_key, args.description)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
