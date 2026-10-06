"""Training orchestrator — one ML model per legend sub-sheet, MLflow-tracked.

Usage (repo root):
    python -m ai.legend_models.train                      # all sheets
    python -m ai.legend_models.train --sheets "VALVE" "Equipment Numbering"
    python -m ai.legend_models.train --dry-run            # extract + detect only
    python -m ai.legend_models.train --no-register        # track, don't register

Pipeline per sheet:
    extract grid+images → detect format → fit format-specific model →
    save export/ → log params/metrics/artifacts to legend/<sheet_key> →
    register legend_<sheet_key> → set @champion

Then a single legend_orchestrator run records the fleet manifest, and the
format router itself is trained + registered as legend_format_router.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import config, formats, mlflow_tracking as mlt
from .models import MODEL_REGISTRY
from .workbook import SheetGrid, extract_workbook, save_extracted

logger = logging.getLogger('legend_models.train')


def train_sheet(grid: SheetGrid, decision: formats.FormatDecision,
                register: bool = True) -> dict:
    """Train, persist and MLflow-track one sheet's model."""
    params = config.MODEL_PARAMS.get(decision.format_type, {})
    model_cls = MODEL_REGISTRY[decision.format_type]
    export_dir = config.WORK_DIR / grid.key / config.EXPORT_DIRNAME

    started = time.time()
    with mlt.start_run(sheet_key=grid.key,
                       run_name=f'{grid.key}_{decision.format_type}',
                       tags={'sheet': grid.title, 'format': decision.format_type,
                             'router': decision.source}) as run:
        mlt.log_params({'sheet_title': grid.title, 'format': decision.format_type,
                        'router_source': decision.source,
                        'n_rows': len(grid.rows), 'n_images': len(grid.images),
                        **{f'hp_{k}': v for k, v in params.items()}})
        mlt.log_metrics({f'signal_{k}': v for k, v in decision.signals.items()})

        model = model_cls()
        metrics = model.fit(grid, params)
        model.save(export_dir)

        metrics['train_seconds'] = round(time.time() - started, 2)
        mlt.log_metrics(metrics)
        mlt.log_export_artifacts(export_dir)

        run_id = mlt.register_model_from_run(sheet_key=grid.key) if register and run else None
        champion = mlt.set_champion_alias(run_id, sheet_key=grid.key) if run_id else False

    logger.info('%-28s %-16s %s', grid.title, decision.format_type, metrics)
    return {'sheet': grid.title, 'sheet_key': grid.key, 'format': decision.format_type,
            'router': decision.source, 'metrics': metrics,
            'run_id': run_id, 'champion': champion,
            'export_dir': str(export_dir)}


def train_combined(key: str, spec: dict, register: bool = True,
                   _wb_cache: dict | None = None) -> dict:
    """Train one COMBINED model over several numbering schemes (soft-coded in
    config.COMBINED_MODELS) — e.g. the 'Line List' model covering Phase 1 +
    Phase 2 line-number formats with a single segmenter."""
    from .models.numbering import CombinedNumberingModel, parse_numbering_scheme

    cache = _wb_cache if _wb_cache is not None else {}
    schemes = []
    for src in spec['sources']:
        wb_path = Path(src['workbook'])
        if not wb_path.is_absolute():
            wb_path = config.LEGENDS_DIR / wb_path
        if str(wb_path) not in cache:
            cache[str(wb_path)] = extract_workbook(str(wb_path))
        grid = next((g for g in cache[str(wb_path)] if g.title == src['sheet']), None)
        if grid is None:
            raise ValueError(f"sheet {src['sheet']!r} not found in {wb_path.name}")
        schemes.append({'key': config.sheet_key(grid.title), 'title': grid.title,
                        'scheme': parse_numbering_scheme(grid),
                        'field_map': spec.get('field_map', {}).get(grid.title, {})})

    params = dict(config.MODEL_PARAMS.get(config.FORMAT_NUMBERING, {}))
    params.update(spec.get('params', {}))
    export_dir = config.WORK_DIR / key / config.EXPORT_DIRNAME

    started = time.time()
    with mlt.start_run(sheet_key=key, run_name=f'{key}_combined',
                       tags={'kind': 'combined', 'model_key': key}) as run:
        model = CombinedNumberingModel()
        metrics = model.fit_schemes(schemes, params)
        model.save(export_dir)
        metrics['train_seconds'] = round(time.time() - started, 2)
        mlt.log_params({'combined_key': key, 'n_schemes': len(schemes),
                        'sheets': [s['title'] for s in schemes],
                        **{f'hp_{k}': v for k, v in params.items()}})
        mlt.log_metrics({k: v for k, v in metrics.items() if isinstance(v, (int, float))})
        mlt.log_export_artifacts(export_dir)
        run_id = mlt.register_model_from_run(sheet_key=key) if register and run else None
        champion = mlt.set_champion_alias(run_id, sheet_key=key) if run_id else False

    logger.info('COMBINED %-22s %s', key, metrics)
    return {'model_key': key, 'format': config.FORMAT_COMBINED, 'metrics': metrics,
            'schemes': [s['key'] for s in schemes],
            'run_id': run_id, 'champion': champion, 'export_dir': str(export_dir)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Train one ML model per legend sub-sheet')
    parser.add_argument('--workbook', default=config.DEFAULT_WORKBOOK,
                        help='Path to the legend .xlsx (soft-coded default in config.py)')
    parser.add_argument('--sheets', nargs='*', default=None,
                        help='Optional subset of sheet titles to train')
    parser.add_argument('--dry-run', action='store_true',
                        help='Extract + detect formats only; no training')
    parser.add_argument('--no-register', action='store_true',
                        help='Track runs in MLflow but skip model registration')
    parser.add_argument('--save-extracted', action='store_true',
                        help='Persist extracted grids/images under the work dir')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(levelname)s %(name)s: %(message)s')

    grids = extract_workbook(args.workbook)
    if args.sheets:
        wanted = {s.strip().lower() for s in args.sheets}
        grids = [g for g in grids if g.title.lower() in wanted or g.key in wanted]
        if not grids:
            logger.error('No matching sheets for %s', args.sheets)
            return 2
    if args.save_extracted:
        save_extracted(grids, config.WORK_DIR / '_extracted')

    # ── format routing for every sheet ──────────────────────────────────────
    decisions = [formats.detect_format(g) for g in grids]
    for g, d in zip(grids, decisions):
        logger.info('FORMAT  %-28s → %-16s (%s)  scores=%s',
                    g.title, d.format_type, d.source,
                    {k: round(v, 2) for k, v in d.scores.items()})
    if args.dry_run:
        print(json.dumps([{'sheet': g.title, 'key': g.key, 'format': d.format_type,
                           'router': d.source, 'rows': len(g.rows), 'images': len(g.images)}
                          for g, d in zip(grids, decisions)], indent=2))
        return 0

    # ── train one model per sheet ───────────────────────────────────────────
    results, failures = [], []
    for grid, decision in zip(grids, decisions):
        try:
            results.append(train_sheet(grid, decision, register=not args.no_register))
        except Exception as e:  # noqa: BLE001 — one bad sheet must not stop the fleet
            logger.error('FAILED %s: %s', grid.title, e)
            failures.append({'sheet': grid.title, 'error': str(e)})

    # ── learned format router (optional layer over the rule scorer) ─────────
    router_result = {'trained': 0, 'reason': 'skipped'}
    try:
        router, router_result = formats.train_router(decisions)
        if router is not None:
            import joblib

            router_dir = config.WORK_DIR / '_router' / config.EXPORT_DIRNAME
            router_dir.mkdir(parents=True, exist_ok=True)
            joblib.dump(router, router_dir / 'router.joblib')
            (router_dir / 'signals.json').write_text(
                json.dumps(formats.SIGNAL_ORDER, indent=2), encoding='utf-8')
            with mlt.start_run(sheet_key=None, run_name='format_router',
                               tags={'kind': 'router'}):
                mlt.log_metrics({k: v for k, v in router_result.items()
                                 if isinstance(v, (int, float))})
                mlt.log_export_artifacts(router_dir)
                rid = mlt.register_model_from_run(model_name=config.ROUTER_MODEL_NAME) \
                    if not args.no_register else None
                if rid:
                    mlt.set_champion_alias(rid, model_name=config.ROUTER_MODEL_NAME)
    except Exception as e:  # noqa: BLE001 — router is auxiliary
        logger.warning('Router training failed (non-fatal): %s', e)

    # ── combined cross-scheme models (soft-coded config.COMBINED_MODELS) ────
    # e.g. the "Line List" model: one segmenter for Phase 1 + Phase 2
    # line-number formats.  Sources may live in OTHER workbooks — they are
    # extracted on demand and cached.
    combined_results = []
    _wb_cache: dict = {}
    for _ckey, _spec in config.COMBINED_MODELS.items():
        try:
            combined_results.append(
                train_combined(_ckey, _spec, register=not args.no_register,
                               _wb_cache=_wb_cache))
        except Exception as e:  # noqa: BLE001 — combined model must not stop the fleet
            logger.error('COMBINED FAILED %s: %s', _ckey, e)
            failures.append({'sheet': _ckey, 'error': str(e)})

    # ── orchestrator manifest run ───────────────────────────────────────────
    with mlt.start_run(sheet_key=None, run_name='fleet_manifest',
                       tags={'kind': 'orchestrator'}):
        mlt.log_params({'workbook': args.workbook, 'n_sheets': len(results)})
        mlt.log_metrics({'sheets_trained': len(results), 'sheets_failed': len(failures),
                         'combined_trained': len(combined_results)})
        mlt.log_manifest({'workbook': str(args.workbook),
                          'router': router_result,
                          'models': results, 'combined': combined_results,
                          'failures': failures})

    summary = {'trained': len(results), 'failed': failures,
               'combined': {r['model_key']: r['metrics'] for r in combined_results},
               'by_format': {f: sum(1 for r in results if r['format'] == f)
                             for f in config.FORMAT_TYPES}}
    print(json.dumps(summary, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
